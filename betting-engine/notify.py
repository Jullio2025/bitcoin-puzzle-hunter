"""
Notificações via Telegram (bot oficial, grátis).

O BOT é compartilhado (um token só, no .env):
  TELEGRAM_BOT_TOKEN  -> crie um bot com o @BotFather e cole o token

Cada usuário cadastra o PRÓPRIO chat_id no painel (aba Tracker) e recebe
o garimpo dele. O id se descobre falando com o @userinfobot.
"""
from __future__ import annotations

import os

import requests


def bot_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN"))


def telegram_configured() -> bool:
    """Compat.: bot + um chat_id global no .env (modo usuário único)."""
    return bool(os.getenv("TELEGRAM_BOT_TOKEN")
                and os.getenv("TELEGRAM_CHAT_ID"))


def send_telegram(text: str, chat_id: str | None = None) -> bool:
    """Envia a mensagem ao chat_id (ou ao TELEGRAM_CHAT_ID do .env).
    True se chegou. Mensagens longas são fatiadas."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
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


def send_document(path, chat_id: str | None = None, caption: str = "") -> bool:
    """Envia um arquivo (ex.: backup) ao chat_id. True se chegou."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        with open(path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": caption[:1000]},
                files={"document": f}, timeout=60)
        return resp.ok
    except (OSError, requests.RequestException):
        return False
