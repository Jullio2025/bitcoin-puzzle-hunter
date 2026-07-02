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
    "Para receber os alertas (garimpo, surebets e resultados):\n"
    "1) Crie sua conta em {panel}\n"
    "2) Depois da liberação do acesso, abra ⚙️ Conta e cole este "
    "chat_id no campo do Telegram\n"
    "3) No Scanner, monte o que você quer garimpar e marque "
    "'Salvar como padrão'\n\n"
    "Pronto! Os números na mesa — a decisão é sua. (18+)"
)


GROUP_ID = ("⚡ ChapaFut\n\nO chat_id deste grupo é: {chat_id}\n\n"
            "Cole-o em ⚙️ Conta pra os alertas caírem aqui. (18+)")


def build_reply(update: dict) -> tuple[int, str] | None:
    """De um update do Telegram, devolve (chat_id, texto_resposta) ou None.

    - Conversa PRIVADA: responde qualquer mensagem com as boas-vindas
      (a pessoa precisa do próprio chat_id).
    - GRUPO: responde SÓ a /start ou /id com o id do grupo — responder
      toda mensagem viraria spam. Função pura, fácil de testar."""
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    if chat.get("type") == "private":
        return chat_id, WELCOME.format(chat_id=chat_id, panel=PANEL_HINT)
    cmd = (msg.get("text") or "").strip().lower().split("@")[0]
    if cmd in ("/start", "/id"):
        return chat_id, GROUP_ID.format(chat_id=chat_id)
    return None


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
