"""Acesso pago: liberação manual, validade e teto de ativos (sem rede)."""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
_TMP = Path(tempfile.mkdtemp())

import users  # noqa: E402
users.USERS_FILE = _TMP / "users.json"
users.USERS_DIR = _TMP / "u"
users.SECRET_FILE = _TMP / ".secret"


class TestAccess(unittest.TestCase):
    def setUp(self):
        if users.USERS_FILE.exists():
            users.USERS_FILE.unlink()

    def test_new_user_is_pending(self):
        users.create_user("novato", "pw1234")
        self.assertEqual(users.access_status("novato"), "pendente")
        self.assertFalse(users.is_active("novato"))

    def test_release_activates(self):
        users.create_user("pagante", "pw1234")
        amanha = (date.today() + timedelta(days=30)).isoformat()
        users.set_access("pagante", True, amanha)
        self.assertEqual(users.access_status("pagante"), "ok")
        self.assertTrue(users.is_active("pagante"))

    def test_expired_blocks(self):
        users.create_user("vencido", "pw1234")
        ontem = (date.today() - timedelta(days=1)).isoformat()
        users.set_access("vencido", True, ontem)
        self.assertEqual(users.access_status("vencido"), "expirado")
        self.assertFalse(users.is_active("vencido"))

    def test_legacy_user_grandfathered(self):
        # conta antiga (sem o campo 'ativo') continua com acesso
        data = users._load()
        data["antigo"] = {"salt": "x", "hash": "y"}
        users._save(data)
        self.assertEqual(users.access_status("antigo"), "ok")

    def test_count_active_ignores_admin_and_pending(self):
        users.create_user("ana", "pw1234"); users.set_access("ana", True)
        users.create_user("bia", "pw1234"); users.set_access("bia", True)
        users.create_user("ped", "pw1234")  # pendente
        self.assertEqual(users.count_active(), 2)

    def test_admin_always_active(self):
        import os
        os.environ["ADMIN_USER"] = "chefe"
        users.create_user("chefe", "pw1234")  # nasce pendente...
        self.assertTrue(users.is_admin("chefe"))
        self.assertTrue(users.is_active("chefe"))  # ...mas admin sempre entra
        del os.environ["ADMIN_USER"]


if __name__ == "__main__":
    unittest.main()
