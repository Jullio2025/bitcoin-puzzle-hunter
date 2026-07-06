"""Mercados de odds baixas: gols por time e handicap asiático — sem rede."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scoring  # noqa: E402
from odds_parser import parse_odds  # noqa: E402
from recommender import MarketAnalysis, UserParams, analyze_fixture  # noqa: E402
from scanner import Criterion, _odds_keys_matching, plain_explanation  # noqa: E402
from test_pipeline_offline import fake_ctx  # noqa: E402

ODDS_RESP = [{"bookmakers": [{"id": 8, "bets": [
    {"name": "Asian Handicap", "values": [
        {"value": "Home -1.5", "odd": "2.05"},
        {"value": "Away +1.5", "odd": "1.80"},
        {"value": "Away +0.75", "odd": "1.50"},   # linha de quarto: ignorada
    ]},
    {"name": "Total - Home", "values": [
        {"value": "Over 1.5", "odd": "2.10"},
        {"value": "Under 2.5", "odd": "1.12"},
    ]},
]}]}]


class TestHandicapProb(unittest.TestCase):
    def test_big_positive_line_is_near_certain(self):
        p, push = scoring.handicap_prob(1.3, 1.1, "away", 3.5)
        self.assertGreater(p, 0.97)
        self.assertEqual(push, 0)

    def test_integer_line_has_push(self):
        _, push = scoring.handicap_prob(1.3, 1.1, "away", 3.0)
        self.assertGreater(push, 0)

    def test_opposite_half_lines_complement(self):
        ph, _ = scoring.handicap_prob(1.8, 1.0, "home", -1.5)
        pa, _ = scoring.handicap_prob(1.8, 1.0, "away", 1.5)
        self.assertAlmostEqual(ph + pa, 1.0)

    def test_invalid_side(self):
        with self.assertRaises(ValueError):
            scoring.handicap_prob(1.0, 1.0, "draw", 1.5)


class TestNewMarketsParsing(unittest.TestCase):
    def setUp(self):
        self.omap = parse_odds(ODDS_RESP, bookmaker_id=8)

    def test_asian_handicap_keys(self):
        self.assertEqual(self.omap[("handicap", "home", -1.5)], 2.05)
        self.assertEqual(self.omap[("handicap", "away", 1.5)], 1.80)

    def test_quarter_lines_ignored(self):
        self.assertNotIn(("handicap", "away", 0.75), self.omap)

    def test_team_totals(self):
        self.assertEqual(self.omap[("team_goals_home", "under", 2.5)], 1.12)


class TestNewMarketsAnalysis(unittest.TestCase):
    def test_analysis_labels(self):
        params = UserParams(markets=["team_goals_home", "handicap"])
        fa = analyze_fixture(fake_ctx(), parse_odds(ODDS_RESP, 8), params)
        labels = [m.label for m in fa.markets]
        self.assertTrue(any("Gols do mandante: Menos de 2.5" in l
                            for l in labels), labels)
        self.assertTrue(any("Handicap: Fora +1.5" in l for l in labels),
                        labels)

    def test_scanner_criterion_matches_handicap(self):
        c = Criterion("handicap", "away", 3.0, 1.01, 1.20, 0.9)
        self.assertTrue(_odds_keys_matching({("handicap", "away", 3.0): 1.09},
                                            c))
        self.assertFalse(_odds_keys_matching({("handicap", "home", 3.0): 1.09},
                                             c))

    def test_plain_text_positive_line(self):
        m = MarketAnalysis("handicap", "away", 3.5, None,
                           scoring.market_metrics(0.97, 1.05), True)
        t = plain_explanation(m)
        self.assertIn("não pode perder por 4 ou mais", t)

    def test_plain_text_negative_integer_line_mentions_push(self):
        m = MarketAnalysis("handicap", "home", -1.0, None,
                           scoring.market_metrics(0.4, 2.4), True)
        t = plain_explanation(m)
        self.assertIn("vencer por mais de 1", t)
        self.assertIn("devolve", t)

    def test_plain_text_team_total(self):
        m = MarketAnalysis("team_goals_away", "under", 2.5, None,
                           scoring.market_metrics(0.93, 1.04), True)
        t = plain_explanation(m)
        self.assertIn("no máximo 2 gol(s) do visitante", t)


if __name__ == "__main__":
    unittest.main()
