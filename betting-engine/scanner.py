"""
Scanner: a lógica invertida — o usuário define os CRITÉRIOS primeiro
(mercado, lado, linha, faixa de odd, probabilidade mínima) e o motor
varre os jogos do dia atrás dos que entregam essas peças.

Estratégia econômica de chamadas:
1. /odds por DATA (paginado, barato) -> pré-filtra pela faixa de odd;
2. só os candidatos passam pelo modelo completo (times + árbitro),
   onde a probabilidade mínima é verificada.

Transparência mantida: o filtro aqui é o próprio pedido do usuário, e
cada acerto sai com o pacote completo de métricas.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config
import scoring
from api_client import ApiFootballClient
from features import MatchContext, build_match_context
from odds_parser import OddsMap, parse_odds
from recommender import (MARKET_LABELS, SIDE_LABELS, MarketAnalysis,
                         apply_rules, base_lambdas)


@dataclass
class Criterion:
    """Uma 'peça' que o usuário quer para o bilhete."""
    market: str                 # goals | corners | cards | 1x2
    side: str                   # over/under ou home/draw/away/any
    line: float | None          # None = qualquer linha disponível
    odd_min: float = 1.01
    odd_max: float = 100.0
    p_min: float = 0.0

    @property
    def label(self) -> str:
        base = MARKET_LABELS.get(self.market, self.market)
        side = ("qualquer resultado" if self.side == "any"
                else SIDE_LABELS.get(self.side, self.side))
        line = f" {self.line:g}" if self.line is not None else ""
        return (f"{base}: {side}{line} | odd {self.odd_min:g}–"
                f"{self.odd_max:g} | p ≥ {self.p_min:.0%}")


@dataclass
class ScanGroup:
    """Um jogo aprovado, com os mercados que bateram nos critérios."""
    ctx: MatchContext
    lambdas: dict
    lambda_explanations: dict
    rule_results: list
    hits: list[tuple[Criterion, MarketAnalysis]] = field(default_factory=list)


@dataclass
class ScanResult:
    groups: list[ScanGroup]
    fixtures_today: int          # jogos do dia (ainda não iniciados, cobertos)
    odds_candidates: int         # jogos cuja odd bateu em >= 1 critério
    analyzed: int                # analisados a fundo (modelo completo)
    skipped_by_limit: int        # candidatos além do limite de profundidade


def _odds_keys_matching(odds_map: OddsMap, c: Criterion) -> list:
    """Chaves do mapa de odds que satisfazem o critério (pré-filtro barato)."""
    keys = []
    for (mkt, side, line), odd in odds_map.items():
        if mkt != c.market:
            continue
        if c.market == "1x2":
            if c.side != "any" and side != c.side:
                continue
        else:
            if side != c.side:
                continue
            # linha pedida casa com a disponível (6 aceita 6.0 e 6.5)
            if c.line is not None and (line is None
                                       or abs(line - c.line) > 0.5):
                continue
        if not (c.odd_min <= odd <= c.odd_max):
            continue
        keys.append((mkt, side, line))
    return keys


def _prob_for_key(key: tuple, lambdas: dict) -> float:
    market, side, line = key
    if market == "1x2":
        ph, pd, pa = scoring.match_outcome_probs(
            lambdas["goals_home"], lambdas["goals_away"],
            max_goals=config.MODEL["max_goals_grid"])
        return {"home": ph, "draw": pd, "away": pa}[side]
    lam = (lambdas["goals_home"] + lambdas["goals_away"]
           if market == "goals" else lambdas[market])
    return (scoring.prob_over(lam, line) if side == "over"
            else scoring.prob_under(lam, line))


def _lambda_for_key(key: tuple, lambdas: dict) -> float | None:
    market = key[0]
    if market == "1x2":
        return None
    if market == "goals":
        return lambdas["goals_home"] + lambdas["goals_away"]
    return lambdas[market]


def scan_day(client: ApiFootballClient, date: str,
             criteria: list[Criterion], bookmaker: int | None = None,
             last_n: int | None = None, match_all: bool = False,
             deep_limit: int = 20) -> ScanResult:
    """Varre os jogos do dia atrás dos critérios pedidos.

    match_all=True: só aprova jogos em que TODOS os critérios batem
    (útil para múltipla dentro do mesmo jogo — atenção à correlação!).
    match_all=False: aprova com qualquer critério (peças vindas de jogos
    diferentes, o caso típico de múltipla).
    """
    bookmaker = bookmaker or config.DEFAULT_BOOKMAKER_ID
    covered = {lg["league_id"] for lg in client.leagues_with_stats_coverage()}
    fixtures = {
        fx["fixture"]["id"]: fx
        for fx in client.fixtures_by_date(date)
        if fx["fixture"]["status"]["short"] in ("NS", "TBD")
        and fx["league"]["id"] in covered
    }

    # fase 1: pré-filtro barato pelas odds do dia inteiro
    candidates = []
    for item in client.odds_by_date(date, bookmaker):
        fid = item.get("fixture", {}).get("id")
        fx = fixtures.get(fid)
        if not fx:
            continue
        odds_map = parse_odds([item], bookmaker_id=bookmaker)
        matched = {i: keys for i, c in enumerate(criteria)
                   if (keys := _odds_keys_matching(odds_map, c))}
        if not matched:
            continue
        if match_all and len(matched) < len(criteria):
            continue
        candidates.append((fx, odds_map, matched))
    candidates.sort(key=lambda t: t[0]["fixture"]["date"])

    # fase 2: modelo completo só nos candidatos (limite de profundidade)
    groups = []
    for fx, odds_map, matched in candidates[:deep_limit]:
        ctx = build_match_context(client, fx, last_n=last_n)
        lambdas, expl = base_lambdas(ctx)
        lambdas, rule_results = apply_rules(ctx, lambdas, None)

        hits_by_crit: dict[int, list] = {}
        for i, keys in matched.items():
            c = criteria[i]
            for key in keys:
                p = _prob_for_key(key, lambdas)
                if p < c.p_min:
                    continue
                metrics = scoring.market_metrics(p, odds_map[key])
                ma = MarketAnalysis(
                    market=key[0], side=key[1], line=key[2],
                    lambda_used=_lambda_for_key(key, lambdas),
                    metrics=metrics, passes_filters=True)
                hits_by_crit.setdefault(i, []).append(ma)

        if not hits_by_crit:
            continue
        if match_all and len(hits_by_crit) < len(criteria):
            continue
        groups.append(ScanGroup(
            ctx=ctx, lambdas=lambdas, lambda_explanations=expl,
            rule_results=rule_results,
            hits=[(criteria[i], ma) for i in sorted(hits_by_crit)
                  for ma in hits_by_crit[i]]))

    return ScanResult(
        groups=groups,
        fixtures_today=len(fixtures),
        odds_candidates=len(candidates),
        analyzed=min(len(candidates), deep_limit),
        skipped_by_limit=max(0, len(candidates) - deep_limit))
