"""Pipeline completo com dados falsos — roda sem rede e sem chave de API."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from features import MatchContext, RefereeStats, SideStats  # noqa: E402
from odds_parser import available_lines, parse_odds  # noqa: E402
from recommender import (MultipleLeg, UserParams, analyze_fixture,  # noqa: E402
                         build_multiple)

FAKE_FIXTURE = {
    "fixture": {"id": 1, "date": "2026-06-11T20:00:00+00:00",
                "referee": "J. Silva", "status": {"short": "NS"}},
    "league": {"id": 71, "season": 2026, "name": "Série A",
               "round": "Regular Season - 10"},
    "teams": {"home": {"id": 10, "name": "Time Casa"},
              "away": {"id": 20, "name": "Time Fora"}},
}

FAKE_ODDS_RESPONSE = [{
    "bookmakers": [{
        "id": 8, "name": "Bet365",
        "bets": [
            {"name": "Goals Over/Under", "values": [
                {"value": "Over 2.5", "odd": "1.90"},
                {"value": "Under 2.5", "odd": "1.95"}]},
            {"name": "Cards Over/Under", "values": [
                {"value": "Over 4.5", "odd": "1.80"},
                {"value": "Under 4.5", "odd": "2.00"}]},
            {"name": "Match Winner", "values": [
                {"value": "Home", "odd": "2.10"},
                {"value": "Draw", "odd": "3.30"},
                {"value": "Away", "odd": "3.60"}]},
        ],
    }],
}]


def fake_ctx() -> MatchContext:
    home = SideStats(team_id=10, team_name="Time Casa", venue="casa",
                     games=5, goals_for=1.8, goals_against=0.8,
                     corners_for=6.0, corners_against=4.0,
                     cards=2.2, cards_opponent=2.5, shots_on_goal=5.5,
                     form="VVEDV", points=10)
    away = SideStats(team_id=20, team_name="Time Fora", venue="fora",
                     games=5, goals_for=1.0, goals_against=1.5,
                     corners_for=4.5, corners_against=5.5,
                     cards=2.6, cards_opponent=2.0, shots_on_goal=3.8,
                     form="DEDVD", points=4)
    ref = RefereeStats(name="J. Silva", games=8, cards_avg=5.5,
                       note="média 5.5 em 8 jogos")
    return MatchContext(fixture=FAKE_FIXTURE, home=home, away=away,
                        referee=ref, league_id=71, season=2026)


class TestOddsParser(unittest.TestCase):
    def test_parse_basic(self):
        odds = parse_odds(FAKE_ODDS_RESPONSE, bookmaker_id=8)
        self.assertEqual(odds[("goals", "over", 2.5)], 1.90)
        self.assertEqual(odds[("cards", "under", 4.5)], 2.00)
        self.assertEqual(odds[("1x2", "draw", None)], 3.30)

    def test_available_lines(self):
        odds = parse_odds(FAKE_ODDS_RESPONSE)
        self.assertEqual(available_lines(odds, "goals"), [2.5])
        self.assertEqual(available_lines(odds, "corners"), [])

    def test_wrong_bookmaker_filtered(self):
        self.assertEqual(parse_odds(FAKE_ODDS_RESPONSE, bookmaker_id=99), {})


class TestAnalyzeFixture(unittest.TestCase):
    def setUp(self):
        self.odds = parse_odds(FAKE_ODDS_RESPONSE, bookmaker_id=8)
        self.params = UserParams()
        self.fa = analyze_fixture(fake_ctx(), self.odds, self.params)

    def test_all_requested_markets_shown(self):
        markets = {m.market for m in self.fa.markets}
        self.assertEqual(markets, {"goals", "corners", "cards", "1x2"})

    def test_nothing_silently_filtered(self):
        # mercados fora dos filtros aparecem com a razão, não desaparecem
        out_of_filter = [m for m in self.fa.markets if not m.passes_filters]
        self.assertTrue(all(m.failed_filters for m in out_of_filter))

    def test_corners_without_odds_still_listed(self):
        corners = [m for m in self.fa.markets if m.market == "corners"]
        self.assertTrue(corners)
        self.assertTrue(all(m.metrics.odd is None for m in corners))

    def test_1x2_probs_sum_to_one(self):
        probs = [m.metrics.p_model for m in self.fa.markets
                 if m.market == "1x2"]
        self.assertAlmostEqual(sum(probs), 1.0, places=6)

    def test_referee_rule_raises_cards_lambda(self):
        # árbitro 5.5 vs baseline 4.8 deve SUBIR os cartões em relação à
        # base (já encolhida). Comparamos com a base antes das regras.
        import recommender
        base, _ = recommender.base_lambdas(fake_ctx())
        self.assertGreater(self.fa.lambdas["cards"], base["cards"])

    def test_every_rule_explains_itself(self):
        for res in self.fa.rule_results:
            self.assertTrue(res.explanation, f"regra {res.rule} sem explicação")

    def test_metrics_consistency(self):
        for m in self.fa.markets:
            mt = m.metrics
            if mt.odd is None:
                continue
            self.assertAlmostEqual(mt.implied, 1 / mt.odd)
            self.assertAlmostEqual(mt.edge, mt.p_model - 1 / mt.odd)


class TestMultiple(unittest.TestCase):
    def test_ticket_shows_three_numbers(self):
        legs = [MultipleLeg("A x B", "Gols: Mais de 2.5", 1.9, 0.55),
                MultipleLeg("C x D", "Cartões: Mais de 4.5", 1.8, 0.60)]
        t = build_multiple(legs)
        self.assertAlmostEqual(t.combined_odd, 1.9 * 1.8)
        self.assertAlmostEqual(t.combined_prob, 0.55 * 0.60)
        self.assertAlmostEqual(t.lose_prob, 1 - 0.33)
        self.assertTrue(t.warning)


class TestRulesRegistry(unittest.TestCase):
    def test_all_expected_rules_registered(self):
        import rules
        names = rules.all_rule_names()
        for expected in ("mando_de_campo", "escalacao", "clima",
                         "arbitro_cartoes", "cartoes_time", "estilo_de_jogo",
                         "forma", "importancia", "escanteios"):
            self.assertIn(expected, names)

    def test_subset_selection(self):
        import rules
        rs = rules.get_rules(["forma", "arbitro_cartoes"])
        self.assertEqual(len(rs), 2)
        with self.assertRaises(KeyError):
            rules.get_rules(["nao_existe"])


if __name__ == "__main__":
    unittest.main()
