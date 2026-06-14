"""
Ouvinte do @ChapaFut_bot: responde a quem manda mensagem (ex.: /start)
com o chat_id da pessoa + instruções, pra ela colar na conta do ChapaFut.

Roda em loop (long polling). Não precisa de webhook nem porta aberta.
Use o token compartilhado TELEGRAM_BOT_TOKEN do .env.

  rodar:   python bot_listener.py
  serviço: bash install-bot.sh   (systemd: chapafut-bot)
"""
from __future__ import annotations

import os
import time

import requests

import config  # carrega o .env

PANEL_HINT = os.getenv("PANEL_URL", "").rstrip("/") or "o site do ChapaFut"

WELCOME = (
    "⚡ Bem-vindo ao ChapaFut!\n\n"
    "Seu chat_id é: {chat_id}\n\n"
    "Para receber seu garimpo diário:\n"
    "1) Crie sua conta em {panel}\n"
    "2) Na aba Tracker, cole este chat_id em "
    "'Receber meu garimpo no Telegram'\n"
    "3) No Scanner, monte seus critérios e marque 'Salvar como padrão'\n\n"
    "Pronto! Os números na mesa — a decisão é sua. (18+)"
)


def build_reply(update: dict) -> tuple[int, str] | None:
    """De um update do Telegram, devolve (chat_id, texto_resposta) ou None.

    Sempre responde com o chat_id (qualquer mensagem serve), porque é
    isso que a pessoa precisa copiar. Função pura, fácil de testar."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    return chat_id, WELCOME.format(chat_id=chat_id, panel=PANEL_HINT)


def _send(token: str, chat_id: int, text: str) -> None:
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                  json={"chat_id": chat_id, "text": text}, timeout=15)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN ausente no .env — nada a ouvir.")
    base = f"https://api.telegram.org/bot{token}"
    offset = None
    print("ChapaFut bot ouvindo /start...")
    while True:
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"{base}/getUpdates", params=params, timeout=60)
            for update in r.json().get("result", []):
                offset = update["update_id"] + 1
                reply = build_reply(update)
                if reply:
                    _send(token, *reply)
        except Exception as e:  # rede instável não pode derrubar o ouvinte
            print(f"[bot_listener] {type(e).__name__}: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
