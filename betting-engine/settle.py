"""
Liquidação automática: confere na API o resultado dos jogos encerrados
e marca cada aposta como ganhou/perdeu/devolvida sozinho.

Funciona para apostas registradas pelo painel (que guardam fixture_id e
mercado de forma estruturada). Apostas digitadas à mão continuam sendo
liquidadas manualmente.
"""
from __future__ import annotations

import config
from api_client import ApiFootballClient
from features import STAT_CORNERS, _cards_total, _stat_value
from tracker import Tracker

FINISHED = ("FT", "AET", "PEN")


def decide(market: str, side: str, line: float | None,
           gh: int, ga: int, count_total: float | None) -> str:
    """Resultado de UMA perna dado o placar/contagem final.

    Retorna 'won' | 'lost' | 'push' | 'unknown' (sem dado de contagem).
    Lógica espelhada do modelo — auditável de ponta a ponta.
    """
    if market == "1x2":
        winner = "home" if gh > ga else ("away" if ga > gh else "draw")
        return "won" if side == winner else "lost"
    if market == "handicap":
        adj = (gh + line - ga) if side == "home" else (ga + line - gh)
        return "won" if adj > 1e-9 else ("push" if abs(adj) <= 1e-9
                                         else "lost")
    if market == "btts":
        scored = gh > 0 and ga > 0
        won = scored if side == "yes" else not scored
        return "won" if won else "lost"
    if market == "double_chance":
        winner = "home" if gh > ga else ("away" if ga > gh else "draw")
        ok = {"1X": winner in ("home", "draw"),
              "12": winner in ("home", "away"),
              "X2": winner in ("away", "draw")}.get(side, False)
        return "won" if ok else "lost"
    if market == "goals":
        total = gh + ga
    elif market == "team_goals_home":
        total = gh
    elif market == "team_goals_away":
        total = ga
    elif market in ("corners", "cards"):
        if count_total is None:
            return "unknown"
        total = count_total
    else:
        return "unknown"
    if total == line:
        return "push"
    if side == "over":
        return "won" if total > line else "lost"
    return "won" if total < line else "lost"


def _count_total(client: ApiFootballClient, fixture_id: int,
                 market: str) -> float | None:
    """Total final de escanteios/cartões do jogo, via estatísticas."""
    blocks = client.fixture_statistics(fixture_id)
    values = []
    for b in blocks:
        v = (_stat_value(b, STAT_CORNERS) if market == "corners"
             else _cards_total(b))
        if v is not None:
            values.append(v)
    return sum(values) if values else None


def leg_outcome(client: ApiFootballClient, leg: dict,
                ttl: int | None = None) -> str:
    """'won'|'lost'|'push'|'pending'|'unknown' para uma perna estruturada.

    ttl: TTL de cache do jogo (em s). Use um valor curto na tela para captar
    rápido o fim do jogo; deixe None nos jobs (cache padrão de 1h)."""
    fx = client.fixture_by_id(leg["fid"], ttl=ttl)
    if not fx or fx["fixture"]["status"]["short"] not in FINISHED:
        return "pending"
    gh = fx["goals"]["home"] or 0
    ga = fx["goals"]["away"] or 0
    count = (_count_total(client, leg["fid"], leg["market"])
             if leg["market"] in ("corners", "cards") else None)
    return decide(leg["market"], leg["side"], leg.get("line"),
                  gh, ga, count)


_OUTCOME_PT = {"won": "✅", "lost": "❌", "push": "↩️",
               "unknown": "❔", "pending": "⏳"}


def settle_card(client: ApiFootballClient, card: dict,
                ttl: int | None = None) -> tuple[str, list]:
    """Confere um cartão. Retorna (status, detalhe_por_perna).

    status: 'pendente' (algum jogo não acabou) | 'ganhou' | 'perdeu' |
    'devolvida' (todas push) | 'conferir' (alguma perna sem estatística).
    Cada perna do cartão tem fid, mkt, side, line, market(label).
    ttl: TTL de cache do jogo (curto na tela, padrão nos jobs)."""
    outcomes, detail = [], []
    for leg in card.get("legs", []):
        if not leg.get("fid") or not leg.get("mkt"):
            outcomes.append("unknown")
            detail.append({"label": leg.get("market", "?"), "icon": "❔"})
            continue
        o = leg_outcome(client, {"fid": leg["fid"], "market": leg["mkt"],
                                 "side": leg["side"], "line": leg.get("line")},
                        ttl=ttl)
        outcomes.append(o)
        detail.append({"label": leg.get("market", leg["mkt"]),
                       "icon": _OUTCOME_PT.get(o, "❔"), "outcome": o})

    if not outcomes:
        return "pendente", detail
    if "pending" in outcomes:
        return "pendente", detail
    if "lost" in outcomes:
        return "perdeu", detail
    if "unknown" in outcomes:
        return "conferir", detail
    if all(o == "push" for o in outcomes):
        return "devolvida", detail
    return "ganhou", detail


def auto_settle(tracker: Tracker,
                client: ApiFootballClient | None = None) -> dict:
    """Percorre as apostas pendentes e liquida o que já tem resultado."""
    client = client or ApiFootballClient()
    summary = {"ganhou": 0, "perdeu": 0, "devolvida": 0,
               "pendentes": 0, "manuais": 0, "avisos": []}

    for bet in tracker.bets:
        if bet.result != "pendente":
            continue
        if not bet.legs:
            summary["manuais"] += 1
            continue

        try:
            outcomes = [leg_outcome(client, leg) for leg in bet.legs]
        except Exception as e:  # falha de rede/dado não derruba o lote
            summary["avisos"].append(f"#{bet.id}: erro ao consultar "
                                     f"({type(e).__name__}) — fica pendente.")
            continue
        if "pending" in outcomes:
            summary["pendentes"] += 1
            continue
        if "unknown" in outcomes:
            summary["avisos"].append(
                f"#{bet.id}: sem estatísticas finais — liquide manualmente.")
            continue
        if "lost" in outcomes:
            result = "perdeu"
        elif all(o == "push" for o in outcomes):
            result = "devolvida"
        elif "push" in outcomes:  # múltipla com push parcial: regra da casa
            summary["avisos"].append(
                f"#{bet.id}: múltipla com perna devolvida (push) — a casa "
                "recalcula a odd; liquide manualmente.")
            continue
        else:
            result = "ganhou"
        tracker.settle(bet.id, result)
        summary[result] += 1
    return summary
