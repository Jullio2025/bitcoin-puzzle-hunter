"""
Armazém de cartões (bilhetes). Cada cartão tem link público curto
(/b/<code>), dono (usuário), pernas estruturadas (p/ conferência
automática), status e flag de notificação no Telegram.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import date

import config

SHARED_FILE = config.DATA_DIR / "shared_tickets.json"


def _load() -> dict:
    if SHARED_FILE.exists():
        try:
            return json.loads(SHARED_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}  # arquivo corrompido: não derruba o sistema
    return {}


def _save(data: dict) -> None:
    SHARED_FILE.parent.mkdir(parents=True, exist_ok=True)
    # escrita atômica: grava num temporário e troca, pra nunca deixar o
    # arquivo pela metade se o processo morrer no meio.
    tmp = SHARED_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, SHARED_FILE)


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
                  "side": l.get("side"), "line": l.get("line"),
                  "hl": l.get("hl"), "al": l.get("al")}
                 for l in legs],
        "combined_odd": combined_odd,
        "combined_prob": combined_prob,
        "lose_prob": lose_prob,
        "partial": partial,
        "status": "pendente",
        "notified": False,
        "detail": [],
        "stake": None,
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


def same_fixture_warning(legs: list) -> list:
    """Jogos com mais de UMA perna no mesmo bilhete (pernas correlacionadas).

    Retorna [(rótulo_do_jogo, n_pernas)] — vazio se cada jogo aparece 1x.
    A probabilidade combinada assume INDEPENDÊNCIA entre as pernas; quando
    há pernas do mesmo jogo (ex.: "Mais de 1.5" + "Ambas marcam"), elas são
    correlacionadas e o número combinado fica IMPRECISO (em geral subestima)."""
    counts: dict = {}
    labels: dict = {}
    for leg in legs:
        fid = leg.get("fid")
        if not fid:
            continue
        counts[fid] = counts.get(fid, 0) + 1
        labels[fid] = leg.get("fixture", "jogo")
    return [(labels[fid], n) for fid, n in counts.items() if n > 1]


def card_pl(card: dict) -> dict:
    """Lucro/prejuízo de um cartão a partir do valor apostado (stake).

    - ganhou:    retorno = stake × odd combinada;  lucro = stake × (odd-1)
    - perdeu:    retorno = 0;                        lucro = -stake
    - devolvida: retorno = stake (reembolso);        lucro = 0
    - pendente/conferir: ainda em aberto -> só o lucro POTENCIAL se ganhar.

    Retorna stake/returned/pl = None quando não há valor apostado, ou quando
    o cartão é parcial sem odd combinada (não dá pra calcular)."""
    stake = card.get("stake")
    odd = card.get("combined_odd")
    status = card.get("status", "pendente")
    out = {"stake": stake, "returned": None, "pl": None,
           "potential": None, "settled": False}
    if not stake:
        return out
    if odd:
        out["potential"] = stake * (odd - 1.0)
    if status == "ganhou":
        if odd:
            out["returned"] = stake * odd
            out["pl"] = stake * (odd - 1.0)
            out["settled"] = True
    elif status == "perdeu":
        out["returned"] = 0.0
        out["pl"] = -stake
        out["settled"] = True
    elif status == "devolvida":
        out["returned"] = stake
        out["pl"] = 0.0
        out["settled"] = True
    return out


def user_summary(username: str) -> dict:
    """Resumo do histórico + calibração do modelo (por perna) do usuário.

    Calibração: agrupa as pernas JÁ RESOLVIDAS por faixa de probabilidade
    e compara a chance que o modelo previu com o acerto real. Se as duas
    baterem, o modelo está calibrado. Abaixo de ~100 pernas, a variância
    ainda domina (não dá pra concluir)."""
    cards = list_for_user(username)
    counts = {"pendente": 0, "ganhou": 0, "perdeu": 0,
              "devolvida": 0, "conferir": 0}
    # faixas de calibração: <50, 50-60, ..., 90-100
    edges = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9),
             (0.9, 1.01)]
    buckets = [{"lo": lo, "hi": hi, "n": 0, "won": 0, "psum": 0.0}
               for lo, hi in edges]
    legs_settled = 0
    staked = 0.0          # total apostado em cartões já resolvidos
    returned = 0.0        # total que voltou (ganhos + devoluções)
    n_with_stake = 0      # cartões resolvidos que tinham valor apostado
    pending_stake = 0.0   # apostado ainda em jogo (pendente/conferir)

    for card in cards:
        counts[card.get("status", "pendente")] = \
            counts.get(card.get("status", "pendente"), 0) + 1
        pl = card_pl(card)
        if pl["stake"] and pl["settled"]:
            staked += pl["stake"]
            returned += pl["returned"]
            n_with_stake += 1
        elif pl["stake"] and card.get("status") in ("pendente", "conferir"):
            pending_stake += pl["stake"]
        for leg, det in zip(card.get("legs", []), card.get("detail", [])):
            outcome = det.get("outcome")
            if outcome not in ("won", "lost"):
                continue  # push/unknown/pending não entram na calibração
            p = leg.get("p")
            if p is None:
                continue
            legs_settled += 1
            for b in buckets:
                if b["lo"] <= p < b["hi"]:
                    b["n"] += 1
                    b["psum"] += p
                    if outcome == "won":
                        b["won"] += 1
                    break

    for b in buckets:
        b["previsto"] = (b["psum"] / b["n"]) if b["n"] else None
        b["real"] = (b["won"] / b["n"]) if b["n"] else None

    decided = counts["ganhou"] + counts["perdeu"]
    profit = returned - staked
    return {
        "counts": counts,
        "total": len(cards),
        "card_hit_rate": (counts["ganhou"] / decided) if decided else None,
        "buckets": [b for b in buckets if b["n"] > 0],
        "legs_settled": legs_settled,
        "enough": legs_settled >= 100,
        "staked": staked,
        "returned": returned,
        "profit": profit,
        "roi": (profit / staked) if staked else None,
        "n_with_stake": n_with_stake,
        "pending_stake": pending_stake,
    }
