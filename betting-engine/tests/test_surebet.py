"""Detector de surebet: matemática de arbitragem entre casas (sem rede)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import surebet  # noqa: E402


def _resp(*bookmakers):
    """Monta um item de /odds com as casas dadas: (nome, [(bet, [(val,odd)])])."""
    return [{"fixture": {"id": 1}, "league": {"name": "L", "country": "BR"},
             "bookmakers": [
                 {"id": i, "name": name,
                  "bets": [{"name": bet,
                            "values": [{"value": v, "odd": str(o)}
                                       for v, o in vals]}
                           for bet, vals in bets]}
                 for i, (name, bets) in enumerate(bookmakers)]}]


class TestParseByBook(unittest.TestCase):
    def test_keeps_each_book(self):
        resp = _resp(
            ("A", [("Goals Over/Under", [("Over 1.5", 2.00), ("Under 1.5", 1.80)])]),
            ("B", [("Goals Over/Under", [("Over 1.5", 1.85), ("Under 1.5", 2.10)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        self.assertEqual(by[("goals", "over", 1.5)], {"A": 2.00, "B": 1.85})
        self.assertEqual(by[("goals", "under", 1.5)], {"A": 1.80, "B": 2.10})

    def test_books_filter(self):
        resp = _resp(
            ("A", [("Goals Over/Under", [("Over 1.5", 2.00)])]),
            ("B", [("Goals Over/Under", [("Over 1.5", 1.85)])]),
        )
        by = surebet.parse_odds_by_book(resp, books={"A"})
        self.assertEqual(by[("goals", "over", 1.5)], {"A": 2.00})


class TestArbDetection(unittest.TestCase):
    def test_real_arb_over_under(self):
        # Over 1.5 @2.00 (A) + Under 1.5 @2.10 (B): soma 0.976 -> +2.46%
        resp = _resp(
            ("A", [("Goals Over/Under", [("Over 1.5", 2.00), ("Under 1.5", 1.80)])]),
            ("B", [("Goals Over/Under", [("Over 1.5", 1.85), ("Under 1.5", 2.10)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        bets = surebet.scan_fixture(by, ["goals"], min_margin=0.0, near_max=1.0)
        self.assertEqual(len(bets), 1)
        sb = bets[0]
        self.assertTrue(sb.is_arb)
        self.assertAlmostEqual(sb.sum_implied, 0.5 + 1 / 2.10, places=4)
        self.assertAlmostEqual(sb.margin, 1 / (0.5 + 1 / 2.10) - 1, places=4)
        # pernas pegam a melhor casa de cada lado
        over = next(l for l in sb.legs if l.side == "over")
        under = next(l for l in sb.legs if l.side == "under")
        self.assertEqual((over.book, over.odd), ("A", 2.00))
        self.assertEqual((under.book, under.odd), ("B", 2.10))

    def test_stake_split_locks_equal_return(self):
        resp = _resp(
            ("A", [("Goals Over/Under", [("Over 1.5", 2.00)])]),
            ("B", [("Goals Over/Under", [("Under 1.5", 2.10)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        sb = surebet.scan_fixture(by, ["goals"], near_max=1.0)[0]
        returns = [ret for _leg, _stake, ret in sb.split(100.0)]
        # retorno igual em qualquer resultado (lucro travado)
        self.assertAlmostEqual(returns[0], returns[1], places=2)
        self.assertGreater(returns[0], 100.0)  # lucro positivo

    def test_no_arb_when_margined(self):
        # casa com margem: Over 1.85 / Under 1.85 -> soma 1.08, sem arb
        resp = _resp(
            ("A", [("Goals Over/Under", [("Over 1.5", 1.85), ("Under 1.5", 1.85)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        bets = surebet.scan_fixture(by, ["goals"], min_margin=0.0, near_max=1.0)
        self.assertEqual(bets, [])

    def test_near_miss_included_when_allowed(self):
        # soma ~1.02 entre DUAS casas -> entra como "quase" se near_max >= 1.02
        resp = _resp(
            ("A", [("Goals Over/Under", [("Over 1.5", 1.96), ("Under 1.5", 1.90)])]),
            ("B", [("Goals Over/Under", [("Over 1.5", 1.92), ("Under 1.5", 1.96)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        none = surebet.scan_fixture(by, ["goals"], near_max=1.0)
        self.assertEqual(none, [])
        near = surebet.scan_fixture(by, ["goals"], near_max=1.05)
        self.assertEqual(len(near), 1)
        self.assertFalse(near[0].is_arb)

    def test_flags_implausible_margin_as_suspect(self):
        # caso real Mexico x Korea: "Fora +2.5 @22.00" é odd podre -> soma 15%
        # = margem absurda. NÃO some: vira "suspeita" marcada.
        resp = _resp(
            ("A", [("Asian Handicap", [("Home -2.5", 9.50)])]),
            ("B", [("Asian Handicap", [("Away +2.5", 22.00)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        bets = surebet.scan_fixture(by, ["handicap"], near_max=2.0)
        self.assertEqual(len(bets), 1)
        self.assertTrue(bets[0].suspect)
        self.assertIn("margem", bets[0].suspect_reason.lower())

    def test_flags_single_book_arb_as_suspect(self):
        # caso real Galway: as duas pernas na MESMA casa -> suspeita, não arb
        resp = _resp(
            ("Betfair", [("Asian Handicap", [("Home -2.5", 13.00),
                                             ("Away +2.5", 9.50)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        bets = surebet.scan_fixture(by, ["handicap"], near_max=2.0)
        self.assertEqual(len(bets), 1)
        self.assertTrue(bets[0].suspect)
        self.assertIn("casa", bets[0].suspect_reason.lower())

    def test_incomplete_group_skipped(self):
        # só tem Over, falta Under -> não dá pra travar
        resp = _resp(("A", [("Goals Over/Under", [("Over 1.5", 2.00)])]))
        by = surebet.parse_odds_by_book(resp)
        self.assertEqual(surebet.scan_fixture(by, ["goals"], near_max=1.5), [])

    def test_integer_line_ignored(self):
        # linha inteira (2.0) tem risco de empate/devolução -> ignorada
        resp = _resp(
            ("A", [("Goals Over/Under", [("Over 2", 2.00), ("Under 2", 2.10)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        self.assertEqual(surebet.scan_fixture(by, ["goals"], near_max=1.5), [])

    def test_derived_markets_in_all(self):
        for m in ("odd_even", "clean_sheet_home", "clean_sheet_away"):
            self.assertIn(m, surebet.ALL_MARKETS)

    def test_odd_even_arb(self):
        # Ímpar @2.05 (A) + Par @2.05 (B) -> arb ~ +2.5%
        resp = _resp(
            ("A", [("Odd/Even", [("Odd", 2.05), ("Even", 1.85)])]),
            ("B", [("Odd/Even", [("Odd", 1.90), ("Even", 2.05)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        bets = surebet.scan_fixture(by, ["odd_even"], near_max=1.0)
        self.assertEqual(len(bets), 1)
        self.assertTrue(bets[0].is_arb)
        self.assertEqual(bets[0].market, "odd_even")
        self.assertEqual({l.side for l in bets[0].legs}, {"even", "odd"})

    def test_clean_sheet_arb(self):
        resp = _resp(
            ("A", [("Clean Sheet - Home", [("Yes", 2.10), ("No", 1.80)])]),
            ("B", [("Clean Sheet - Home", [("Yes", 1.90), ("No", 2.10)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        bets = surebet.scan_fixture(by, ["clean_sheet_home"], near_max=1.0)
        self.assertEqual(len(bets), 1)
        self.assertTrue(bets[0].is_arb)

    def test_draw_no_bet_arb(self):
        # Home DNB @2.10 (A) + Away DNB @2.10 (B) -> arb (empate devolve)
        resp = _resp(
            ("A", [("Draw No Bet", [("Home", 2.10), ("Away", 1.80)])]),
            ("B", [("Draw No Bet", [("Home", 1.85), ("Away", 2.10)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        bets = surebet.scan_fixture(by, ["draw_no_bet"], near_max=1.0)
        self.assertEqual(len(bets), 1)
        self.assertTrue(bets[0].is_arb)
        self.assertEqual({l.side for l in bets[0].legs}, {"home", "away"})

    def test_team_goals_in_all_markets(self):
        self.assertIn("team_goals_home", surebet.ALL_MARKETS)
        self.assertIn("team_goals_away", surebet.ALL_MARKETS)

    def test_team_goals_arb(self):
        # gols do mandante: Mais de 1.5 @2.05 (A) + Menos de 1.5 @2.00 (B)
        resp = _resp(
            ("A", [("Total - Home", [("Over 1.5", 2.05), ("Under 1.5", 1.85)])]),
            ("B", [("Total - Home", [("Over 1.5", 1.90), ("Under 1.5", 2.00)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        bets = surebet.scan_fixture(by, ["team_goals_home"], near_max=1.0)
        self.assertEqual(len(bets), 1)
        self.assertTrue(bets[0].is_arb)
        self.assertEqual(bets[0].market, "team_goals_home")
        self.assertEqual(bets[0].market_label, "Gols do mandante")

    def test_depth_marks_thin_market(self):
        # só 2 casas cotam over/under -> perna mais rasa tem profundidade 2
        resp = _resp(
            ("A", [("Goals Over/Under", [("Over 1.5", 2.00), ("Under 1.5", 1.80)])]),
            ("B", [("Goals Over/Under", [("Over 1.5", 1.85), ("Under 1.5", 2.10)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        sb = surebet.scan_fixture(by, ["goals"], near_max=1.0)[0]
        self.assertEqual(sb.depth, 2)
        self.assertEqual(sb.liquidity, "raso")

    def test_depth_liquid_market(self):
        # 5 casas cotam os dois lados -> mercado líquido
        books = [(c, [("Goals Over/Under",
                       [("Over 1.5", o), ("Under 1.5", u)])])
                 for c, o, u in [("A", 2.00, 1.80), ("B", 1.85, 2.10),
                                 ("C", 1.90, 1.95), ("D", 1.88, 1.98),
                                 ("E", 1.92, 1.93)]]
        by = surebet.parse_odds_by_book(_resp(*books))
        sb = surebet.scan_fixture(by, ["goals"], near_max=2.0)[0]
        self.assertEqual(sb.depth, 5)
        self.assertEqual(sb.liquidity, "líquido")

    def test_1x2_three_way_arb(self):
        # melhor odd de cada via vem de casas diferentes -> arb plausível ~8%
        resp = _resp(
            ("A", [("Match Winner", [("Home", 3.20), ("Draw", 3.30), ("Away", 3.00)])]),
            ("B", [("Match Winner", [("Home", 3.05), ("Draw", 3.45), ("Away", 3.10)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        bets = surebet.scan_fixture(by, ["1x2"], near_max=1.10)
        self.assertEqual(len(bets), 1)
        self.assertEqual(len(bets[0].legs), 3)
        self.assertTrue(bets[0].is_arb)
        self.assertGreaterEqual(bets[0].n_books, 2)
        self.assertLessEqual(bets[0].margin, surebet.MAX_PLAUSIBLE_MARGIN)


class TestScanDay(unittest.TestCase):
    class FakeClient:
        def fixtures_by_date(self, date):
            return [{"fixture": {"id": 1, "date": "2026-06-19T20:00:00+00:00"},
                     "league": {"name": "Amistosos", "country": "World"},
                     "teams": {"home": {"name": "Brasil", "logo": "h.png"},
                               "away": {"name": "Haiti", "logo": "a.png"}}}]
        def odds_by_date_all(self, date, max_pages=30):
            return _resp(
                ("A", [("Goals Over/Under", [("Over 1.5", 2.00), ("Under 1.5", 1.80)])]),
                ("B", [("Goals Over/Under", [("Over 1.5", 1.85), ("Under 1.5", 2.10)])]),
            )

    def test_attaches_fixture_info(self):
        scan = surebet.scan_day(self.FakeClient(), "2026-06-19",
                                markets=["goals"], near_max=1.0,
                                only_upcoming=False)
        self.assertEqual(len(scan.bets), 1)
        self.assertEqual(scan.suspects, [])
        sb = scan.bets[0]
        self.assertEqual(sb.fixture, "Brasil x Haiti")
        self.assertEqual(sb.fid, 1)
        self.assertEqual(sb.hl, "h.png")
        self.assertTrue(sb.is_arb)

    class DepthClient:
        def fixtures_by_date(self, date):
            return [{"fixture": {"id": 1, "date": "2099-01-01T00:00:00+00:00"},
                     "league": {"name": "L", "country": "BR"},
                     "teams": {"home": {"name": "A"}, "away": {"name": "B"}}}]
        def odds_by_date_all(self, date, max_pages=30):
            # Over só na A, Under só na B -> cada lado em 1 casa (depth 1)
            return [{"fixture": {"id": 1}, "league": {"name": "L", "country": "BR"},
                     "bookmakers": [
                {"id": 0, "name": "A", "bets": [{"name": "Goals Over/Under",
                 "values": [{"value": "Over 1.5", "odd": "2.10"}]}]},
                {"id": 1, "name": "B", "bets": [{"name": "Goals Over/Under",
                 "values": [{"value": "Under 1.5", "odd": "2.10"}]}]}]}]

    def test_min_depth_filters_single_book_lines(self):
        cli = self.DepthClient()
        s1 = surebet.scan_day(cli, "2099-01-01", markets=["goals"],
                              near_max=1.0, min_depth=1)
        self.assertEqual(len(s1.bets), 1)
        self.assertEqual(s1.bets[0].depth, 1)        # linha só em 1 casa
        s2 = surebet.scan_day(cli, "2099-01-01", markets=["goals"],
                              near_max=1.0, min_depth=2)
        self.assertEqual(s2.bets, [])                # "só confirmável" tira ela

    class FutureAndPastClient:
        def fixtures_by_date(self, date):
            return [
                {"fixture": {"id": 1, "date": "2020-01-01T00:00:00+00:00"},
                 "league": {"name": "L", "country": "BR"},
                 "teams": {"home": {"name": "Velho"}, "away": {"name": "Jogo"}}},
                {"fixture": {"id": 2, "date": "2099-01-01T00:00:00+00:00"},
                 "league": {"name": "L", "country": "BR"},
                 "teams": {"home": {"name": "Futuro"}, "away": {"name": "Jogo"}}},
            ]
        def odds_by_date_all(self, date, max_pages=30):
            def book(name, over, under):
                return {"id": hash(name) % 99, "name": name, "bets": [
                    {"name": "Goals Over/Under",
                     "values": [{"value": "Over 1.5", "odd": str(over)},
                                {"value": "Under 1.5", "odd": str(under)}]}]}
            # arb real (2 casas: melhor over em A, melhor under em B)
            books = [book("A", 2.00, 1.80), book("B", 1.85, 2.10)]
            return [
                {"fixture": {"id": 1}, "league": {"name": "L", "country": "BR"},
                 "bookmakers": books},
                {"fixture": {"id": 2}, "league": {"name": "L", "country": "BR"},
                 "bookmakers": books},
            ]

    def test_skips_already_started_games(self):
        # padrão only_upcoming=True: o jogo de 2020 sai, só o de 2099 fica
        scan = surebet.scan_day(self.FutureAndPastClient(), "2026-06-20",
                                markets=["goals"], near_max=1.0)
        labels = [b.fixture for b in scan.bets]
        self.assertEqual(labels, ["Futuro x Jogo"])
        self.assertEqual(scan.fixtures_scanned, 1)

    class MixedClient(FakeClient):
        def odds_by_date_all(self, date, max_pages=30):
            return _resp(
                # arb limpa (gols, 2 casas, margem pequena)
                ("A", [("Goals Over/Under", [("Over 1.5", 2.00), ("Under 1.5", 1.80)]),
                       ("Asian Handicap", [("Home -2.5", 13.00), ("Away +2.5", 9.50)])]),
                ("B", [("Goals Over/Under", [("Over 1.5", 1.85), ("Under 1.5", 2.10)])]),
            )

    def test_separates_clean_from_suspect(self):
        scan = surebet.scan_day(self.MixedClient(), "2026-06-19",
                                markets=["goals", "handicap"], near_max=1.0,
                                only_upcoming=False)
        self.assertEqual(len(scan.bets), 1)       # gols: arb limpa
        self.assertFalse(scan.bets[0].suspect)
        self.assertEqual(len(scan.suspects), 1)   # handicap numa casa só
        self.assertTrue(scan.suspects[0].suspect)
        self.assertEqual(scan.suspects[0].fixture, "Brasil x Haiti")


if __name__ == "__main__":
    unittest.main()
