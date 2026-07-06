"""Alertas de surebet no Telegram: opt-in, threshold e dedupe (sem rede)."""
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
_TMP = Path(tempfile.mkdtemp())

import users  # noqa: E402
users.USERS_FILE = _TMP / "users.json"
users.USERS_DIR = _TMP / "u"
users.SECRET_FILE = _TMP / ".secret"

import surebet  # noqa: E402
import daily_scan  # noqa: E402
daily_scan.SUREBET_SENT_FILE = _TMP / "surebet_sent.json"


def _fake_bet(fid, margin, fixture="A x B"):
    leg = surebet.SureLeg("goals", "over", 1.5, "Mais de 1.5", "Bet365",
                          2.0, 0.5, 0.5)
    sb = surebet.SureBet(fid=fid, market="goals", market_label="Gols",
                         line=1.5, legs=[leg], sum_implied=1 / (1 + margin),
                         margin=margin, is_arb=True, n_books=2)
    sb.fixture = fixture
    return sb


class TestSurebetAlerts(unittest.TestCase):
    def setUp(self):
        # zera estado entre testes
        if daily_scan.SUREBET_SENT_FILE.exists():
            daily_scan.SUREBET_SENT_FILE.unlink()
        if users.USERS_FILE.exists():
            users.USERS_FILE.unlink()
        self.sent = []
        daily_scan.bot_configured = lambda: True
        daily_scan.send_telegram = lambda text, chat_id=None: \
            self.sent.append((chat_id, text))

    def _client(self):
        return object()  # scan_day é monkeypatchado, não usa o client

    def test_no_subscribers_no_scan(self):
        users.create_user("ninguem", "pw1234")
        called = {"n": 0}
        surebet.scan_day = lambda *a, **k: called.__setitem__("n", called["n"] + 1)
        n = daily_scan.surebet_alerts_run(self._client(), "2026-06-20")
        self.assertEqual(n, 0)
        self.assertEqual(called["n"], 0)  # nem escaneia se ninguém quer

    def test_sends_above_threshold_and_dedupes(self):
        users.create_user("zeca", "pw1234")
        users.set_access("zeca", True)  # conta liberada (paga)
        users.set_telegram("zeca", "999")
        users.set_surebet_alerts("zeca", True, min_margin=2.0)  # 2%
        surebet.scan_day = lambda *a, **k: surebet.SureScan(
            bets=[_fake_bet(1, 0.05), _fake_bet(2, 0.01)],  # 5% passa, 1% não
            suspects=[], fixtures_scanned=2, books_seen=3)

        n1 = daily_scan.surebet_alerts_run(self._client(), "2026-06-20")
        self.assertEqual(n1, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("999", self.sent[0][0])
        # só a de 5% entrou (a de 1% ficou abaixo do threshold de 2%)
        self.assertIn("+5.00%", self.sent[0][1])
        self.assertNotIn("+1.00%", self.sent[0][1])

        # rodar de novo no mesmo dia com a MESMA margem NÃO reenvia (dedupe)
        n2 = daily_scan.surebet_alerts_run(self._client(), "2026-06-20")
        self.assertEqual(n2, 0)
        self.assertEqual(len(self.sent), 1)

    def test_realerts_when_margin_improves(self):
        users.create_user("rita", "pw1234")
        users.set_access("rita", True)  # conta liberada (paga)
        users.set_telegram("rita", "222")
        users.set_surebet_alerts("rita", True, min_margin=0.5)
        # 1ª rodada: margem 3% -> avisa
        surebet.scan_day = lambda *a, **k: surebet.SureScan(
            bets=[_fake_bet(1, 0.03)], suspects=[], fixtures_scanned=1, books_seen=2)
        self.assertEqual(
            daily_scan.surebet_alerts_run(self._client(), "2026-06-20"), 1)
        # subiu pouco (3.5%, +0.5 p.p.) -> NÃO reavisa
        surebet.scan_day = lambda *a, **k: surebet.SureScan(
            bets=[_fake_bet(1, 0.035)], suspects=[], fixtures_scanned=1, books_seen=2)
        self.assertEqual(
            daily_scan.surebet_alerts_run(self._client(), "2026-06-20"), 0)
        # subiu bastante (6%, +3 p.p. da última avisada) -> reavisa
        surebet.scan_day = lambda *a, **k: surebet.SureScan(
            bets=[_fake_bet(1, 0.06)], suspects=[], fixtures_scanned=1, books_seen=2)
        self.assertEqual(
            daily_scan.surebet_alerts_run(self._client(), "2026-06-20"), 1)
        self.assertIn("SUBIU", self.sent[-1][1])

    def test_disabled_user_gets_nothing(self):
        users.create_user("maria", "pw1234")
        users.set_telegram("maria", "111")
        users.set_surebet_alerts("maria", False, min_margin=0.5)
        surebet.scan_day = lambda *a, **k: surebet.SureScan(
            bets=[_fake_bet(1, 0.05)], suspects=[], fixtures_scanned=1,
            books_seen=2)
        n = daily_scan.surebet_alerts_run(self._client(), "2026-06-20")
        self.assertEqual(n, 0)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
