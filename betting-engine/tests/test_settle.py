"""Liquidação automática e mensagem do garimpo diário — sem rede."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import settle  # noqa: E402
from settle import decide  # noqa: E402
from tracker import Tracker  # noqa: E402


class TestDecide(unittest.TestCase):
    def test_goals_over_under(self):
        self.assertEqual(decide("goals", "over", 2.5, 2, 1, None), "won")
        self.assertEqual(decide("goals", "under", 2.5, 2, 1, None), "lost")
        self.assertEqual(decide("goals", "over", 3.0, 2, 1, None), "push")

    def test_team_totals(self):
        self.assertEqual(decide("team_goals_home", "under", 2.5, 1, 4, None),
                         "won")
        self.assertEqual(decide("team_goals_away", "over", 1.5, 0, 2, None),
                         "won")

    def test_1x2(self):
        self.assertEqual(decide("1x2", "home", None, 2, 0, None), "won")
        self.assertEqual(decide("1x2", "draw", None, 1, 1, None), "won")
        self.assertEqual(decide("1x2", "away", None, 1, 1, None), "lost")

    def test_handicap(self):
        # visitante +3: perdeu por 2 -> cobre; por 3 -> push; por 4 -> perde
        self.assertEqual(decide("handicap", "away", 3.0, 2, 0, None), "won")
        self.assertEqual(decide("handicap", "away", 3.0, 3, 0, None), "push")
        self.assertEqual(decide("handicap", "away", 3.0, 4, 0, None), "lost")

    def test_corners_need_stats(self):
        self.assertEqual(decide("corners", "over", 9.5, 1, 1, None),
                         "unknown")
        self.assertEqual(decide("corners", "over", 9.5, 1, 1, 11.0), "won")
        self.assertEqual(decide("cards", "under", 4.5, 1, 1, 3.0), "won")


class FakeSettleClient:
    """Jogo 1 terminou 2x0; jogo 2 ainda não começou."""

    def fixture_by_id(self, fid, ttl=None):
        if fid == 1:
            return {"fixture": {"id": 1, "status": {"short": "FT"}},
                    "goals": {"home": 2, "away": 0}}
        return {"fixture": {"id": 2, "status": {"short": "NS"}},
                "goals": {"home": None, "away": None}}

    def fixture_statistics(self, fid):
        return [{"team": {"id": 10}, "statistics": [
                    {"type": "Corner Kicks", "value": 7},
                    {"type": "Yellow Cards", "value": 2},
                    {"type": "Red Cards", "value": None}]},
                {"team": {"id": 20}, "statistics": [
                    {"type": "Corner Kicks", "value": 4},
                    {"type": "Yellow Cards", "value": 3},
                    {"type": "Red Cards", "value": 1}]}]


class TestAutoSettle(unittest.TestCase):
    def setUp(self):
        self.t = Tracker(Path(tempfile.mkdtemp()) / "bets.json")

    def test_settles_finished_keeps_pending_and_manual(self):
        self.t.add("A x B", "Gols: Mais de 1.5", 1.5, 1, 0.8,
                   legs=[{"fid": 1, "market": "goals", "side": "over",
                          "line": 1.5, "label": "x"}])
        self.t.add("A x B", "Escanteios: Mais de 10.5", 1.8, 1, 0.6,
                   legs=[{"fid": 1, "market": "corners", "side": "over",
                          "line": 10.5, "label": "x"}])
        self.t.add("C x D", "Gols: Mais de 2.5", 1.9, 1, 0.5,
                   legs=[{"fid": 2, "market": "goals", "side": "over",
                          "line": 2.5, "label": "x"}])
        self.t.add("Manual", "Qualquer", 2.0, 1, 0.5)  # sem legs

        s = settle.auto_settle(self.t, FakeSettleClient())
        self.assertEqual(s["ganhou"], 2)   # gols 2x0 e 11 escanteios
        self.assertEqual(s["pendentes"], 1)
        self.assertEqual(s["manuais"], 1)
        self.assertEqual(self.t.bets[0].result, "ganhou")
        self.assertEqual(self.t.bets[2].result, "pendente")

    def test_multiple_all_legs_must_win(self):
        self.t.add("A x B", "dupla", 2.7, 1, 0.5, legs=[
            {"fid": 1, "market": "goals", "side": "over", "line": 1.5,
             "label": "x"},
            {"fid": 1, "market": "1x2", "side": "away", "line": None,
             "label": "y"}])
        s = settle.auto_settle(self.t, FakeSettleClient())
        self.assertEqual(s["perdeu"], 1)   # 2x0: away perdeu -> bilhete caiu

    def test_multiple_with_push_goes_to_manual_warning(self):
        self.t.add("A x B", "dupla", 2.0, 1, 0.5, legs=[
            {"fid": 1, "market": "goals", "side": "over", "line": 2.0,
             "label": "push"},
            {"fid": 1, "market": "1x2", "side": "home", "line": None,
             "label": "win"}])
        s = settle.auto_settle(self.t, FakeSettleClient())
        self.assertEqual(self.t.bets[0].result, "pendente")
        self.assertTrue(any("push" in a for a in s["avisos"]))


class TestDailyMessage(unittest.TestCase):
    def test_format_message_lists_hits_and_tracker(self):
        import daily_scan
        from scanner import Criterion, ScanGroup, ScanResult
        from recommender import MarketAnalysis
        import scoring
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_pipeline_offline import fake_ctx

        c = Criterion("goals", "over", 1.5, 1.2, 1.6, 0.7)
        ma = MarketAnalysis("goals", "over", 1.5, 2.7,
                            scoring.market_metrics(0.78, 1.52), True)
        group = ScanGroup(ctx=fake_ctx(), lambdas={}, lambda_explanations={},
                          rule_results=[], hits=[(c, ma)])
        result = ScanResult(groups=[group], fixtures_today=5,
                            odds_candidates=2, analyzed=2, skipped_by_limit=0)
        msg = daily_scan.format_message(
            "2026-06-12", result, [c],
            {"ganhou": 1, "perdeu": 0, "devolvida": 0, "pendentes": 2,
             "manuais": 0, "avisos": []},
            {"hit_rate": 0.6, "roi": 0.05, "warning": "AMOSTRA PEQUENA: x"})
        self.assertIn("Time Casa x Time Fora", msg)
        self.assertIn("odd 1.52", msg)
        self.assertIn("vale a pena", msg)  # EV>0 vira veredito simples
        self.assertIn("1 ganhas", msg)
        self.assertIn("⚠️", msg)
        self.assertIn("a decisão é sua", msg)


if __name__ == "__main__":
    unittest.main()
