"""Batimento diário: conteúdo, idempotência e janela de horário (sem rede)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import users  # noqa: E402
import share_store  # noqa: E402
import heartbeat  # noqa: E402
import notify  # noqa: E402


def _fresh_dirs():
    """Cada teste em caixa própria: outros módulos de teste também apontam
    users/share_store na importação, e o último vence — então re-apontamos
    aqui a cada teste pra ninguém poluir ninguém."""
    tmp = Path(tempfile.mkdtemp())
    config.DATA_DIR = tmp
    users.USERS_FILE = tmp / "u.json"
    users.USERS_DIR = tmp / "u"
    users.SECRET_FILE = tmp / ".s"
    share_store.SHARED_FILE = tmp / "cards.json"
    heartbeat.MARKER = tmp / "heartbeat_sent"
    return tmp


class TestBuildMessage(unittest.TestCase):
    def setUp(self):
        self.tmp = _fresh_dirs()

    def test_counts_users_cards_and_backup(self):
        users.create_user("pagante", "pw1234")
        users.set_access("pagante", True)
        users.create_user("devendo", "pw1234")   # pendente
        share_store.create(
            [{"fixture": "A x B", "market": "x", "odd": 1.5, "p": 0.8,
              "fid": 1, "mkt": "goals", "side": "over", "line": 1.5}],
            1.5, 0.8, 0.2, owner="pagante")
        msg = heartbeat.build_message()
        self.assertIn("1 ativo(s) de 2", msg)
        self.assertIn("1 aguardando liberação", msg)
        self.assertIn("Cartões em aberto: 1", msg)
        self.assertIn("ainda não gerado", msg)   # sem backup hoje

    def test_backup_ok_when_file_exists(self):
        bdir = self.tmp / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / f"chapafut-{config.br_today()}.tar.gz").write_bytes(b"x")
        self.assertIn("ok ✅", heartbeat.build_message())


class TestSendOncePerDay(unittest.TestCase):
    def setUp(self):
        _fresh_dirs()
        self.sent = []
        self._bot_configured = notify.bot_configured
        self._send = notify.send_telegram
        notify.bot_configured = lambda: True
        notify.send_telegram = lambda text, chat_id=None: \
            self.sent.append((chat_id, text)) or True
        import backup
        self._chat = backup._admin_chat_id
        backup._admin_chat_id = lambda: "777"

    def tearDown(self):
        import backup
        notify.bot_configured = self._bot_configured
        notify.send_telegram = self._send
        backup._admin_chat_id = self._chat

    def test_force_sends_and_marks(self):
        self.assertTrue(heartbeat.send_daily_heartbeat(force=True))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "777")
        self.assertEqual(heartbeat.MARKER.read_text().strip(),
                         config.br_today())

    def test_does_not_repeat_same_day(self):
        heartbeat.MARKER.write_text(config.br_today())
        # sem force: já mandou hoje -> não manda de novo (independe da hora)
        self.assertFalse(heartbeat.send_daily_heartbeat())
        self.assertEqual(self.sent, [])

    def test_failed_send_does_not_mark(self):
        notify.send_telegram = lambda text, chat_id=None: False
        self.assertFalse(heartbeat.send_daily_heartbeat(force=True))
        self.assertFalse(heartbeat.MARKER.exists())  # tenta de novo na próxima


if __name__ == "__main__":
    unittest.main()
