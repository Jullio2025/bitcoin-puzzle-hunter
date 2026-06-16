"""Armazenamento dos bilhetes compartilháveis (links curtos /b/<code>)."""
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
           lose_prob: float, partial: bool = False) -> str:
    """Guarda o bilhete e devolve o código curto do link público."""
    shared = _load()
    code = secrets.token_urlsafe(4).replace("_", "a").replace("-", "b")[:6]
    while code in shared:
        code = secrets.token_urlsafe(4).replace("_", "a").replace("-", "b")[:6]
    shared[code] = {
        "created": date.today().isoformat(),
        "legs": [{"fixture": l["fixture"], "market": l["market"],
                  "odd": (float(l["odd"]) if l.get("odd") else None),
                  "p": float(l["p"])} for l in legs],
        "combined_odd": combined_odd,
        "combined_prob": combined_prob,
        "lose_prob": lose_prob,
        "partial": partial,
    }
    _save(shared)
    return code
