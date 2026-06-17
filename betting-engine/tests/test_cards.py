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


if __name__ == "__main__":
    unittest.main()
