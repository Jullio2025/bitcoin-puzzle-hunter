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

    def test_rejects_implausible_margin(self):
        # caso real Mexico x Korea: "Fora +2.5 @22.00" é odd podre -> soma 15%
        # = margem absurda. Não pode ser tratado como surebet.
        resp = _resp(
            ("A", [("Asian Handicap", [("Home -2.5", 9.50)])]),
            ("B", [("Asian Handicap", [("Away +2.5", 22.00)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        self.assertEqual(surebet.scan_fixture(by, ["handicap"], near_max=2.0), [])

    def test_rejects_single_book_arb(self):
        # caso real Galway: as duas pernas na MESMA casa -> impossível ser arb
        resp = _resp(
            ("Betfair", [("Asian Handicap", [("Home -2.5", 13.00),
                                             ("Away +2.5", 9.50)])]),
        )
        by = surebet.parse_odds_by_book(resp)
        self.assertEqual(surebet.scan_fixture(by, ["handicap"], near_max=2.0), [])

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
                                markets=["goals"], near_max=1.0)
        self.assertEqual(len(scan.bets), 1)
        sb = scan.bets[0]
        self.assertEqual(sb.fixture, "Brasil x Haiti")
        self.assertEqual(sb.fid, 1)
        self.assertEqual(sb.hl, "h.png")
        self.assertTrue(sb.is_arb)


if __name__ == "__main__":
    unittest.main()
