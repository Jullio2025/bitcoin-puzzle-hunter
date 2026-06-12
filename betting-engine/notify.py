"""
Notificações via Telegram (bot oficial, grátis).

Configuração no .env:
  TELEGRAM_BOT_TOKEN  -> crie um bot com o @BotFather e cole o token
  TELEGRAM_CHAT_ID    -> mande /start pro seu bot e pegue seu id com
                         o @userinfobot (ou via getUpdates)
"""
from __future__ import annotations

import os

import requests


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN")
                and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(text: str) -> bool:
    """Envia a mensagem; True se chegou. Mensagens longas são fatiadas."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    ok = True
    for i in range(0, len(text), 3800):  # limite do Telegram: 4096
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[i:i + 3800]},
            timeout=15)
        ok = ok and resp.ok
    return ok
