"""
Usuários do ChapaFut: cadastro, login e dados por usuário.

- Senhas guardadas com hash pbkdf2 (nunca em texto puro).
- Sessão via cookie assinado com HMAC (segredo gerado uma vez em disco).
- Cada usuário tem sua pasta (tracker e presets individuais).
- Stdlib apenas — nenhuma dependência nova.

A camada de DADOS de jogos (cache da API) é compartilhada por todos:
um único pré-aquecimento diário abastece o cache e todos leem dele, então
o custo de API não cresce com o número de usuários.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

import config

USERS_FILE = config.DATA_DIR / "users.json"
USERS_DIR = config.DATA_DIR / "u"
SECRET_FILE = config.DATA_DIR / ".session_secret"

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,29}$")
_PBKDF2_ROUNDS = 200_000


# ------------------------------------------------------------- segredo
def _secret() -> bytes:
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32))
    return SECRET_FILE.read_text().strip().encode()


# ------------------------------------------------------------- storage
def _load() -> dict:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text())
    return {}


def _save(data: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def all_usernames() -> list[str]:
    return sorted(_load())


def get_user(username: str) -> dict | None:
    return _load().get((username or "").lower())


def user_dir(username: str) -> Path:
    d = USERS_DIR / username.lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------- senha
def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(),
                               bytes.fromhex(salt), _PBKDF2_ROUNDS).hex()


def create_user(username: str, password: str) -> tuple[bool, str]:
    """Cria usuário. Retorna (ok, mensagem)."""
    username = (username or "").strip().lower()
    if not USERNAME_RE.match(username):
        return False, ("Usuário inválido: use 3–30 letras/números "
                       "(sem espaço, minúsculas).")
    if len(password or "") < 6:
        return False, "Senha muito curta (mínimo 6 caracteres)."
    data = _load()
    if username in data:
        return False, "Esse usuário já existe."
    salt = secrets.token_hex(16)
    data[username] = {"salt": salt, "hash": _hash(password, salt),
                      "created": time.time(), "telegram_chat_id": "",
                      "send_hour": 9}
    _save(data)
    return True, "Conta criada."


def verify_user(username: str, password: str) -> bool:
    u = get_user(username)
    if not u:
        return False
    expected = _hash(password, u["salt"])
    return hmac.compare_digest(expected, u["hash"])


def set_telegram(username: str, chat_id: str) -> None:
    data = _load()
    if username.lower() in data:
        data[username.lower()]["telegram_chat_id"] = (chat_id or "").strip()
        _save(data)


def set_send_hour(username: str, hour: int) -> None:
    """Hora (0–23, horário do servidor) em que o usuário quer o garimpo."""
    data = _load()
    if username.lower() in data:
        data[username.lower()]["send_hour"] = max(0, min(23, int(hour)))
        _save(data)


def set_password(username: str, new_password: str) -> tuple[bool, str]:
    if len(new_password or "") < 6:
        return False, "Senha muito curta (mínimo 6 caracteres)."
    data = _load()
    u = data.get(username.lower())
    if not u:
        return False, "Usuário não encontrado."
    salt = secrets.token_hex(16)
    u["salt"] = salt
    u["hash"] = _hash(new_password, salt)
    _save(data)
    return True, "Senha alterada."


# ------------------------------------------------------------- sessão
def make_token(username: str) -> str:
    sig = hmac.new(_secret(), username.lower().encode(),
                   hashlib.sha256).hexdigest()
    return f"{username.lower()}.{sig}"


def read_token(token: str | None) -> str | None:
    """Valida o cookie e devolve o usuário, ou None."""
    if not token or "." not in token:
        return None
    username, _, sig = token.rpartition(".")
    expected = hmac.new(_secret(), username.encode(),
                        hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected, sig) and username in _load():
        return username
    return None


def ensure_admin_from_env() -> None:
    """Migração: se não há usuários e existe PANEL_PASSWORD no .env, cria a
    conta admin com essas credenciais (para não trancar quem já usava)."""
    import os
    if _load():
        return
    user = os.getenv("PANEL_USER", "admin").lower()
    pw = os.getenv("PANEL_PASSWORD", "")
    if not pw:
        return
    if not USERNAME_RE.match(user):
        user = "admin"
    ok, _ = create_user(user, pw if len(pw) >= 6 else pw + "______")
    # herda dados legados (tracker/preset únicos) para o admin
    if ok:
        d = user_dir(user)
        for legacy, dest in ((config.DATA_DIR / "bets.json", d / "bets.json"),
                             (config.DATA_DIR / "scan_preset.json",
                              d / "scan_preset.json")):
            if legacy.exists() and not dest.exists():
                dest.write_text(legacy.read_text())
