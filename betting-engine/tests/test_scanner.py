"""Testes do scanner (lógica invertida: critérios -> jogos), sem rede."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner  # noqa: E402
from odds_parser import parse_odds  # noqa: E402
from test_pipeline_offline import FAKE_FIXTURE, FAKE_ODDS_RESPONSE, fake_ctx  # noqa: E402

ODDS_MAP = parse_odds(FAKE_ODDS_RESPONSE, bookmaker_id=8)
# ODDS_MAP: goals over/under 2.5 (1.90/1.95), cards over/under 4.5
# (1.80/2.00), 1x2 home 2.10 / draw 3.30 / away 3.60


class TestCriterionMatching(unittest.TestCase):
    def test_match_by_odd_range(self):
        c = scanner.Criterion("goals", "over", None, odd_min=1.5, odd_max=2.0)
        self.assertEqual(scanner._odds_keys_matching(ODDS_MAP, c),
                         [("goals", "over", 2.5)])

    def test_odd_out_of_range(self):
        c = scanner.Criterion("goals", "over", None, odd_min=2.5, odd_max=3.0)
        self.assertEqual(scanner._odds_keys_matching(ODDS_MAP, c), [])

    def test_line_tolerance_half(self):
        # usuário pede linha 2 -> aceita a 2.5 disponível (tolerância 0.5)
        c = scanner.Criterion("goals", "over", 2.0, odd_min=1.0, odd_max=3.0)
        self.assertEqual(scanner._odds_keys_matching(ODDS_MAP, c),
                         [("goals", "over", 2.5)])
        c = scanner.Criterion("goals", "over", 4.0, odd_min=1.0, odd_max=3.0)
        self.assertEqual(scanner._odds_keys_matching(ODDS_MAP, c), [])

    def test_1x2_any_side(self):
        c = scanner.Criterion("1x2", "any", None, odd_min=2.0, odd_max=3.5)
        keys = scanner._odds_keys_matching(ODDS_MAP, c)
        self.assertEqual(sorted(k[1] for k in keys), ["draw", "home"])


class FakeScanClient:
    calls_made = 0

    def leagues_with_stats_coverage(self):
        return [{"league_id": 71, "name": "Série A", "country": "Brazil",
                 "season": 2026}]

    def fixtures_by_date(self, date):
        return [FAKE_FIXTURE]

    def odds_by_date(self, date, bookmaker_id=None, max_pages=30):
        item = dict(FAKE_ODDS_RESPONSE[0])
        item["fixture"] = {"id": FAKE_FIXTURE["fixture"]["id"]}
        return [item]


class TestScanDay(unittest.TestCase):
    def _scan(self, criteria, **kw):
        with patch.object(scanner, "build_match_context",
                          lambda client, fx, last_n=None: fake_ctx()):
            return scanner.scan_day(FakeScanClient(), "2026-06-11",
                                    criteria, bookmaker=8, **kw)

    def test_finds_game_meeting_criterion(self):
        # under 4.5 cartões: lambda alto (~5.2) -> p baixa; over passa
        c = scanner.Criterion("cards", "over", None, odd_min=1.5,
                              odd_max=2.0, p_min=0.5)
        res = self._scan([c])
        self.assertEqual(res.fixtures_today, 1)
        self.assertEqual(res.odds_candidates, 1)
        self.assertEqual(len(res.groups), 1)
        crit, ma = res.groups[0].hits[0]
        self.assertEqual(ma.market, "cards")
        self.assertGreaterEqual(ma.metrics.p_model, 0.5)

    def test_p_min_filters_after_model(self):
        # odd bate, mas exigir p >= 99% derruba no modelo
        c = scanner.Criterion("cards", "over", None, odd_min=1.5,
                              odd_max=2.0, p_min=0.99)
        res = self._scan([c])
        self.assertEqual(res.odds_candidates, 1)  # passou no pré-filtro
        self.assertEqual(len(res.groups), 0)      # caiu na probabilidade

    def test_match_all_requires_every_criterion(self):
        ok = scanner.Criterion("cards", "over", None, 1.5, 2.0, 0.5)
        impossible = scanner.Criterion("corners", "over", None, 1.1, 9.0, 0.0)
        # não há odds de escanteios no jogo falso -> match_all reprova
        res = self._scan([ok, impossible], match_all=True)
        self.assertEqual(len(res.groups), 0)
        # sem match_all, o jogo entra pelo critério de cartões
        res = self._scan([ok, impossible], match_all=False)
        self.assertEqual(len(res.groups), 1)

    def test_deep_limit_reports_skipped(self):
        c = scanner.Criterion("cards", "over", None, 1.5, 2.0, 0.0)
        res = self._scan([c], deep_limit=0)
        self.assertEqual(res.skipped_by_limit, 1)
        self.assertEqual(res.analyzed, 0)

    def test_criterion_label_is_readable(self):
        c = scanner.Criterion("corners", "over", 6.0, 1.3, 2.0, 0.6)
        self.assertIn("Escanteios", c.label)
        self.assertIn("Mais de 6", c.label)
        self.assertIn("1.3", c.label)


class TestPlainExplanation(unittest.TestCase):
    def _ma(self, market, side, line, p, odd):
        import scoring
        from recommender import MarketAnalysis
        return MarketAnalysis(market=market, side=side, line=line,
                              lambda_used=None,
                              metrics=scoring.market_metrics(p, odd),
                              passes_filters=True)

    def test_over_half_line_counts_correctly(self):
        # "Mais de 1.5 gols" = 2 ou mais
        text = scanner.plain_explanation(self._ma("goals", "over", 1.5,
                                                  0.78, 1.52))
        self.assertIn("2 ou mais gol", text)
        self.assertIn("78%", text)
        self.assertIn("1.52", text)
        self.assertIn("a MAIS", text)  # edge positivo (0.78 > 1/1.52)

    def test_under_half_line(self):
        # "Menos de 10.5 escanteios" = no máximo 10
        text = scanner.plain_explanation(self._ma("corners", "under", 10.5,
                                                  0.65, 1.73))
        self.assertIn("no máximo 10 escanteio", text)

    def test_integer_line_mentions_push(self):
        text = scanner.plain_explanation(self._ma("cards", "over", 5.0,
                                                  0.5, 1.9))
        self.assertIn("6 ou mais", text)
        self.assertIn("devolvida", text)

    def test_negative_edge_warns_paying_too_much(self):
        text = scanner.plain_explanation(self._ma("goals", "over", 2.5,
                                                  0.40, 1.90))
        self.assertIn("a MENOS", text)
        self.assertIn("perde", text)

    def test_1x2(self):
        text = scanner.plain_explanation(self._ma("1x2", "home", None,
                                                  0.55, 2.10))
        self.assertIn("vitória do time da casa", text)

    def test_scan_attaches_plain_text(self):
        c = scanner.Criterion("cards", "over", None, 1.5, 2.0, 0.0)
        with patch.object(scanner, "build_match_context",
                          lambda client, fx, last_n=None: fake_ctx()):
            res = scanner.scan_day(FakeScanClient(), "2026-06-11", [c],
                                   bookmaker=8)
        _, ma = res.groups[0].hits[0]
        self.assertIn("Para ganhar", ma.plain)


if __name__ == "__main__":
    unittest.main()
