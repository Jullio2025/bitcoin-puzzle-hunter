"""
Armazém de cartões (bilhetes). Cada cartão tem link público curto
(/b/<code>), dono (usuário), pernas estruturadas (p/ conferência
automática), status e flag de notificação no Telegram.
"""
from __future__ import annotations

import json
import secrets
from datetime import date

import config

SHARED_FILE = config.DATA_DIR / "shared_tickets.json"


def _load() -> dict:
    if SHARED_FILE.exists():
        return json.loads(SHARED_FILE.read_text())
    return {}


def _save(data: dict) -> None:
    SHARED_FILE.parent.mkdir(parents=True, exist_ok=True)
    SHARED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def get(code: str) -> dict | None:
    return _load().get(code)


def create(legs: list[dict], combined_odd: float | None, combined_prob: float,
           lose_prob: float, partial: bool = False,
           owner: str | None = None, source: str = "manual") -> str:
    """Guarda o cartão e devolve o código curto do link público.

    Mantém as pernas ESTRUTURADAS (fid, mkt, side, line) além do texto,
    para a conferência automática de resultado funcionar."""
    shared = _load()
    code = secrets.token_urlsafe(4).replace("_", "a").replace("-", "b")[:6]
    while code in shared:
        code = secrets.token_urlsafe(4).replace("_", "a").replace("-", "b")[:6]
    shared[code] = {
        "code": code,
        "created": date.today().isoformat(),
        "owner": owner,
        "source": source,
        "legs": [{"fixture": l["fixture"], "market": l["market"],
                  "odd": (float(l["odd"]) if l.get("odd") else None),
                  "p": float(l["p"]),
                  "fid": l.get("fid"), "mkt": l.get("mkt"),
                  "side": l.get("side"), "line": l.get("line")}
                 for l in legs],
        "combined_odd": combined_odd,
        "combined_prob": combined_prob,
        "lose_prob": lose_prob,
        "partial": partial,
        "status": "pendente",
        "notified": False,
        "detail": [],
    }
    _save(shared)
    return code


def list_for_user(username: str) -> list[dict]:
    """Cartões do usuário, mais recentes primeiro."""
    cards = [c for c in _load().values() if c.get("owner") == username]
    cards.sort(key=lambda c: (c.get("created", ""), c.get("code", "")),
               reverse=True)
    return cards


def update(code: str, **fields) -> None:
    data = _load()
    if code in data:
        data[code].update(fields)
        _save(data)
