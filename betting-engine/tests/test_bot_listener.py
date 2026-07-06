"""Ouvinte do bot: responde privado sempre, grupo só a /start|/id."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot_listener  # noqa: E402


def _update(chat_type, text="oi", chat_id=42):
    return {"message": {"chat": {"id": chat_id, "type": chat_type},
                        "text": text}}


class TestBuildReply(unittest.TestCase):
    def test_private_any_message_gets_welcome(self):
        reply = bot_listener.build_reply(_update("private", "qualquer coisa"))
        self.assertIsNotNone(reply)
        chat_id, text = reply
        self.assertEqual(chat_id, 42)
        self.assertIn("42", text)
        self.assertIn("⚙️ Conta", text)        # instrução atualizada

    def test_group_random_message_is_ignored(self):
        # em grupo, responder toda mensagem viraria spam
        self.assertIsNone(bot_listener.build_reply(
            _update("supergroup", "bom dia galera")))

    def test_group_start_returns_group_id(self):
        reply = bot_listener.build_reply(
            _update("supergroup", "/start@ChapaFut_bot", chat_id=-100123))
        self.assertIsNotNone(reply)
        chat_id, text = reply
        self.assertEqual(chat_id, -100123)
        self.assertIn("-100123", text)

    def test_group_id_command_works(self):
        self.assertIsNotNone(bot_listener.build_reply(
            _update("group", "/id", chat_id=-55)))

    def test_no_message_returns_none(self):
        self.assertIsNone(bot_listener.build_reply({"channel_post": {}}))


if __name__ == "__main__":
    unittest.main()
