"""
Backup diário dos dados do ChapaFut (usuários, cartões, tracker, calibração).

Tudo que importa vive em DATA_DIR e NÃO vai pro git. Sem backup, um VPS que
morre/é reciclado leva a base inteira junto. Aqui:
  1. compacta DATA_DIR num .tar.gz por dia (em DATA_DIR/backups);
  2. mantém só os últimos N;
  3. (off-site) manda o arquivo no Telegram do admin, se configurado —
     assim a cópia não fica só no mesmo VPS.
"""
from __future__ import annotations

import tarfile
from datetime import date
from pathlib import Path

import config

BACKUP_DIR = config.DATA_DIR / "backups"
KEEP = 14  # quantos backups diários manter


def _admin_chat_id() -> str:
    """chat_id do admin (pra mandar o backup off-site), se houver."""
    import os
    import users
    admin = os.getenv("ADMIN_USER", os.getenv("PANEL_USER", "admin"))
    u = users.get_user(admin) or {}
    return u.get("telegram_chat_id", "")


def run_daily_backup(send_to_admin: bool = True) -> Path | None:
    """Gera o backup do dia (idempotente: 1 por dia). Devolve o caminho,
    ou None se já existia/sem dados."""
    if not config.DATA_DIR.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"chapafut-{date.today().isoformat()}.tar.gz"
    if dest.exists():
        return None  # já fez hoje

    tmp = dest.with_suffix(".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        for item in config.DATA_DIR.iterdir():
            if item.resolve() == BACKUP_DIR.resolve():
                continue  # não faz backup do próprio backup
            tar.add(item, arcname=item.name)
    tmp.replace(dest)

    # rotação: mantém só os mais novos
    backups = sorted(BACKUP_DIR.glob("chapafut-*.tar.gz"))
    for old in backups[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            pass

    # cópia off-site no Telegram do admin (protege contra perda do VPS)
    if send_to_admin:
        try:
            import notify
            chat = _admin_chat_id()
            if chat and notify.bot_configured():
                notify.send_document(
                    dest, chat_id=chat,
                    caption=f"💾 Backup ChapaFut {date.today().isoformat()} "
                            "(guarde — é a sua base de usuários/cartões).")
        except Exception:
            pass  # backup local já está salvo; off-site é bônus
    return dest
