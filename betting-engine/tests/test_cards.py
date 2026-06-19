"""Seção de Cartões: conferência automática + armazenamento por usuário."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
config.DATA_DIR = Path(tempfile.mkdtemp())

import settle  # noqa: E402
import share_store  # noqa: E402
share_store.SHARED_FILE = config.DATA_DIR / "cards.json"


class FinishedClient:
    """Jogo 1 terminou 2x0."""
    def fixture_by_id(self, fid):
        return {"fixture": {"id": fid, "status": {"short": "FT"}},
                "goals": {"home": 2, "away": 0}}
    def fixture_statistics(self, fid):
        return []


class PendingClient:
    def fixture_by_id(self, fid):
        return {"fixture": {"id": fid, "status": {"short": "NS"}},
                "goals": {"home": None, "away": None}}
    def fixture_statistics(self, fid):
        return []


class TestDecideNewMarkets(unittest.TestCase):
    def test_btts(self):
        self.assertEqual(settle.decide("btts", "yes", None, 2, 1, None), "won")
        self.assertEqual(settle.decide("btts", "yes", None, 2, 0, None), "lost")
        self.assertEqual(settle.decide("btts", "no", None, 2, 0, None), "won")

    def test_double_chance(self):
        self.assertEqual(settle.decide("double_chance", "1X", None, 2, 0, None),
                         "won")
        self.assertEqual(settle.decide("double_chance", "1X", None, 0, 2, None),
                         "lost")
        self.assertEqual(settle.decide("double_chance", "X2", None, 1, 1, None),
                         "won")


class TestSettleCard(unittest.TestCase):
    def _card(self, *legs):
        return {"legs": list(legs)}

    def test_one_lost_makes_card_lost(self):
        card = self._card(
            {"fixture": "A x B", "market": "Gols +1.5", "fid": 1,
             "mkt": "goals", "side": "over", "line": 1.5},
            {"fixture": "A x B", "market": "Ambas marcam", "fid": 1,
             "mkt": "btts", "side": "yes", "line": None})
        status, detail = settle.settle_card(FinishedClient(), card)
        self.assertEqual(status, "perdeu")
        self.assertEqual(detail[0]["outcome"], "won")
        self.assertEqual(detail[1]["outcome"], "lost")

    def test_all_won(self):
        card = self._card(
            {"fixture": "A x B", "market": "Gols +1.5", "fid": 1,
             "mkt": "goals", "side": "over", "line": 1.5},
            {"fixture": "A x B", "market": "Casa vence", "fid": 1,
             "mkt": "1x2", "side": "home", "line": None})
        status, _ = settle.settle_card(FinishedClient(), card)
        self.assertEqual(status, "ganhou")

    def test_pending_when_game_not_finished(self):
        card = self._card({"fixture": "A x B", "market": "Gols +1.5", "fid": 1,
                           "mkt": "goals", "side": "over", "line": 1.5})
        status, _ = settle.settle_card(PendingClient(), card)
        self.assertEqual(status, "pendente")

    def test_unknown_stat_marks_conferir(self):
        card = self._card({"fixture": "A x B", "market": "Escanteios +9.5",
                           "fid": 1, "mkt": "corners", "side": "over",
                           "line": 9.5})
        status, _ = settle.settle_card(FinishedClient(), card)
        self.assertEqual(status, "conferir")  # sem estatística de escanteios


class TestStore(unittest.TestCase):
    def test_owner_and_structured_legs(self):
        code = share_store.create(
            [{"fixture": "A x B", "market": "Gols +1.5", "odd": 1.5, "p": 0.8,
              "fid": 1, "mkt": "goals", "side": "over", "line": 1.5}],
            1.5, 0.8, 0.2, owner="ze", source="bot")
        mine = share_store.list_for_user("ze")
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["owner"], "ze")
        self.assertEqual(mine[0]["legs"][0]["mkt"], "goals")
        self.assertEqual(mine[0]["status"], "pendente")

    def test_update_status(self):
        code = share_store.create(
            [{"fixture": "A x B", "market": "x", "odd": 1.5, "p": 0.8,
              "fid": 1, "mkt": "goals", "side": "over", "line": 1.5}],
            1.5, 0.8, 0.2, owner="ze2")
        share_store.update(code, status="ganhou", notified=True)
        self.assertEqual(share_store.get(code)["status"], "ganhou")
        self.assertNotIn("ze2-other", [c["owner"] for c in
                                       share_store.list_for_user("nobody")])


class TestStakePL(unittest.TestCase):
    def _card(self, status, stake, odd=2.0):
        return {"combined_odd": odd, "status": status, "stake": stake}

    def test_no_stake_returns_none(self):
        pl = share_store.card_pl(self._card("ganhou", None))
        self.assertIsNone(pl["pl"])
        self.assertFalse(pl["settled"])

    def test_win_profit(self):
        pl = share_store.card_pl(self._card("ganhou", 10.0, odd=2.5))
        self.assertAlmostEqual(pl["returned"], 25.0)
        self.assertAlmostEqual(pl["pl"], 15.0)
        self.assertTrue(pl["settled"])

    def test_loss(self):
        pl = share_store.card_pl(self._card("perdeu", 10.0))
        self.assertAlmostEqual(pl["pl"], -10.0)
        self.assertAlmostEqual(pl["returned"], 0.0)

    def test_refund_is_zero(self):
        pl = share_store.card_pl(self._card("devolvida", 10.0))
        self.assertAlmostEqual(pl["pl"], 0.0)
        self.assertAlmostEqual(pl["returned"], 10.0)

    def test_pending_potential_only(self):
        pl = share_store.card_pl(self._card("pendente", 10.0, odd=2.0))
        self.assertIsNone(pl["pl"])
        self.assertFalse(pl["settled"])
        self.assertAlmostEqual(pl["potential"], 10.0)

    def test_summary_aggregates_pl(self):
        for st, stake in [("ganhou", 10.0), ("perdeu", 10.0)]:
            code = share_store.create(
                [{"fixture": "A x B", "market": "x", "odd": 2.0, "p": 0.5,
                  "fid": 1, "mkt": "goals", "side": "over", "line": 1.5}],
                2.0, 0.5, 0.5, owner="banca")
            share_store.update(code, status=st, stake=stake)
        s = share_store.user_summary("banca")
        # ganhou: +10 (10*(2-1)); perdeu: -10 -> lucro 0, apostado 20
        self.assertAlmostEqual(s["staked"], 20.0)
        self.assertAlmostEqual(s["returned"], 20.0)
        self.assertAlmostEqual(s["profit"], 0.0)
        self.assertEqual(s["n_with_stake"], 2)


class TestImpossibleCriterion(unittest.TestCase):
    def test_p100_percent_is_impossible(self):
        import scanner
        self.assertTrue(scanner.Criterion("goals", "over", 1.5, p_min=100).impossible)
        self.assertTrue(scanner.Criterion("goals", "over", 1.5, p_min=1.0).impossible)

    def test_normal_p_is_possible(self):
        import scanner
        self.assertFalse(scanner.Criterion("goals", "over", 1.5, p_min=80).impossible)
        self.assertFalse(scanner.Criterion("goals", "over", 1.5, p_min=0.7).impossible)


if __name__ == "__main__":
    unittest.main()
