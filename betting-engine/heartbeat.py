"""
Batimento cardíaco do ChapaFut: 1 mensagem por dia no Telegram do admin
dizendo que o sistema está vivo e o que precisa de atenção.

Se a mensagem não chegar no horário de sempre, algo caiu (serviço, timer,
bot) — você descobre ANTES do cliente reclamar. Roda de carona no job
horário: dispara na primeira execução do dia a partir de SEND_AFTER_HOUR
(horário de Brasília), uma vez só (marcador em disco).
"""
from __future__ import annotations

from datetime import datetime

import config

MARKER = config.DATA_DIR / "heartbeat_sent"
SEND_AFTER_HOUR = 8  # a partir das 8h de Brasília (mensagem de madrugada não ajuda)


def build_message() -> str:
    """Resumo do dia — só leitura local, nenhuma chamada à API de futebol."""
    import share_store
    import users

    total = 0
    pendentes_acesso = 0
    for un in users.all_usernames():
        if users.is_admin(un):
            continue
        total += 1
        if users.access_status(un) == "pendente":
            pendentes_acesso += 1
    ativos = users.count_active()

    cards = list(share_store._load().values())
    cards_abertos = sum(1 for c in cards
                        if c.get("status") in (None, "pendente"))

    backup_hoje = (config.DATA_DIR / "backups"
                   / f"chapafut-{config.br_today()}.tar.gz").exists()

    lines = [f"💓 ChapaFut no ar — {config.br_today()}",
             "",
             f"👥 Usuários: {ativos} ativo(s) de {total} cadastrado(s)"]
    if pendentes_acesso:
        lines.append(f"⏳ {pendentes_acesso} aguardando liberação "
                     "(confira o PIX e libere em /admin)")
    lines.append(f"🎫 Cartões em aberto: {cards_abertos}")
    lines.append("💾 Backup de hoje: " + ("ok ✅" if backup_hoje
                                          else "ainda não gerado"))
    lines += ["", "Se esta mensagem parar de chegar, algo caiu no servidor."]
    return "\n".join(lines)


def send_daily_heartbeat(force: bool = False) -> bool:
    """Manda o batimento 1x/dia (após SEND_AFTER_HOUR, hora de Brasília).
    Retorna True se enviou agora."""
    import notify
    from backup import _admin_chat_id

    today = config.br_today()
    if not force:
        if datetime.now(config.BR_TZ).hour < SEND_AFTER_HOUR:
            return False
        if MARKER.exists() and MARKER.read_text().strip() == today:
            return False  # já mandou hoje

    chat = _admin_chat_id()
    if not chat or not notify.bot_configured():
        return False
    if not notify.send_telegram(build_message(), chat_id=chat):
        return False  # falhou o envio: tenta de novo na próxima hora
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(today)
    return True
