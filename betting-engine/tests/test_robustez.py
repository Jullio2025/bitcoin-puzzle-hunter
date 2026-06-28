"""Robustez: backup diário, resumo global e retry da API (sem rede)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
config.DATA_DIR = Path(tempfile.mkdtemp())

import share_store  # noqa: E402
share_store.SHARED_FILE = config.DATA_DIR / "cards.json"
import backup  # noqa: E402
backup.BACKUP_DIR = config.DATA_DIR / "backups"
import api_client  # noqa: E402


class TestBackup(unittest.TestCase):
    def test_creates_and_is_idempotent(self):
        (config.DATA_DIR / "users.json").write_text('{"x": 1}')
        p = backup.run_daily_backup(send_to_admin=False)
        self.assertIsNotNone(p)
        self.assertTrue(p.exists())
        # 2ª vez no mesmo dia não regera
        self.assertIsNone(backup.run_daily_backup(send_to_admin=False))


class TestGlobalSummary(unittest.TestCase):
    def test_aggregates_all_owners(self):
        for owner in ("a1user", "a2user"):
            code = share_store.create(
                [{"fixture": "A x B", "market": "x", "odd": 2.0, "p": 0.7,
                  "fid": 1, "mkt": "goals", "side": "over", "line": 1.5}],
                2.0, 0.7, 0.3, owner=owner)
            share_store.update(code, status="ganhou",
                               detail=[{"icon": "✅", "outcome": "won"}])
        s = share_store.global_summary()
        self.assertEqual(s["total"], 2)              # de donos diferentes
        self.assertEqual(s["counts"]["ganhou"], 2)
        self.assertEqual(s["legs_settled"], 2)


class _FakeResp:
    def __init__(self, status):
        self.status_code = status
        self.ok = 200 <= status < 300
    def json(self):
        return {"response": [1, 2], "errors": []}
    def raise_for_status(self):
        pass


class _FlakySession:
    def __init__(self, seq):
        self.seq, self.i, self.headers = seq, 0, {}
    def get(self, url, params=None, timeout=None):
        item = self.seq[self.i]
        self.i += 1
        if isinstance(item, Exception):
            raise item
        return _FakeResp(item)


class TestApiRetry(unittest.TestCase):
    def setUp(self):
        api_client.time.sleep = lambda *_a, **_k: None  # não espera de verdade

    def _client(self, seq):
        cli = api_client.ApiFootballClient(api_key="x")
        cli.pause = 0
        cli.session = _FlakySession(seq)
        return cli

    def test_retries_5xx_then_succeeds(self):
        cli = self._client([500, 500, 200])
        self.assertEqual(cli._get("leagues", ttl=0), [1, 2])
        self.assertEqual(cli.calls_made, 3)

    def test_retries_network_error(self):
        import requests
        cli = self._client([requests.RequestException("rede caiu"), 200])
        self.assertEqual(cli._get("leagues", ttl=0), [1, 2])

    def test_gives_up_after_attempts(self):
        cli = self._client([500, 500, 500])
        with self.assertRaises(api_client.ApiError):
            cli._get("leagues", ttl=0)

    def test_4xx_does_not_retry(self):
        cli = self._client([400, 200])  # 400 não deve repetir
        with self.assertRaises(api_client.ApiError):
            cli._get("leagues", ttl=0)
        self.assertEqual(cli.calls_made, 1)


if __name__ == "__main__":
    unittest.main()
