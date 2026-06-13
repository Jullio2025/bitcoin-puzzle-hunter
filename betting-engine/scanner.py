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

import math
from dataclasses import dataclass, field

import config
import scoring
from api_client import ApiFootballClient
from features import MatchContext, build_match_context
from odds_parser import OddsMap, parse_odds
from recommender import (MARKET_LABELS, SIDE_LABELS, MarketAnalysis,
                         apply_rules, base_lambdas, count_charts)


@dataclass
class Criterion:
    """Uma 'peça' que o usuário quer para o bilhete."""
    market: str                 # goals | corners | cards | 1x2 | handicap...
    side: str                   # over/under ou home/draw/away/any
    line: float | None          # None = qualquer linha disponível
    odd_min: float = 1.01
    odd_max: float = 100.0
    p_min: float = 0.0
    ev_min: float = -100.0      # -100 = sem filtro de EV

    @property
    def label(self) -> str:
        base = MARKET_LABELS.get(self.market, self.market)
        side = ("qualquer resultado" if self.side == "any"
                else SIDE_LABELS.get(self.side, self.side))
        line = (f" {self.line:g}" if self.line is not None
                else " (qualquer linha)")
        if self.market == "1x2":
            line = ""
        text = (f"{base}: {side}{line} | odd {self.odd_min:g}–"
                f"{self.odd_max:g} | p ≥ {self.p_min:.0%}")
        if self.ev_min > -100:
            text += f" | EV ≥ {self.ev_min:+.2f}"
        return text


@dataclass
class ScanGroup:
    """Um jogo aprovado, com os mercados que bateram nos critérios."""
    ctx: MatchContext
    lambdas: dict
    lambda_explanations: dict
    rule_results: list
    hits: list[tuple[Criterion, MarketAnalysis]] = field(default_factory=list)
    charts: dict = field(default_factory=dict)


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
        if c.market in ("1x2", "handicap", "btts", "double_chance"):
            if c.side != "any" and side != c.side:
                continue
            if (c.market == "handicap" and c.line is not None
                    and (line is None or abs(line - c.line) > 0.5)):
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
    if market == "handicap":
        p_win, _push = scoring.handicap_prob(
            lambdas["goals_home"], lambdas["goals_away"], side, line,
            max_goals=config.MODEL["max_goals_grid"])
        return p_win
    if market in ("btts", "double_chance"):
        from recommender import _derived_prob
        return _derived_prob(market, side, lambdas)
    if market == "team_goals_home":
        lam = lambdas["goals_home"]
    elif market == "team_goals_away":
        lam = lambdas["goals_away"]
    else:
        lam = (lambdas["goals_home"] + lambdas["goals_away"]
               if market == "goals" else lambdas[market])
    return (scoring.prob_over(lam, line) if side == "over"
            else scoring.prob_under(lam, line))


_UNITS = {"goals": "gol(s)", "corners": "escanteio(s)",
          "cards": "cartão(ões)",
          "team_goals_home": "gol(s) do time da casa",
          "team_goals_away": "gol(s) do visitante"}
_1X2_EVENTS = {"home": "vitória do time da casa", "draw": "empate",
               "away": "vitória do visitante"}


def _handicap_event(side: str, line: float) -> str:
    team = "o time da casa" if side == "home" else "o visitante"
    is_int = float(line).is_integer()
    if line > 0:
        if is_int:
            return (f"{team}, com {line:+g} de vantagem, não pode perder por "
                    f"mais de {int(line)} gols (derrota por exatamente "
                    f"{int(line)} devolve a aposta)")
        return (f"{team}, com {line:+g} de vantagem, não pode perder por "
                f"{math.floor(line) + 1} ou mais gols de diferença")
    if line < 0:
        if is_int:
            return (f"{team}, dando {line:+g}, precisa vencer por mais de "
                    f"{int(-line)} gols (vitória por exatamente "
                    f"{int(-line)} devolve a aposta)")
        return (f"{team}, dando {line:+g}, precisa vencer por "
                f"{math.floor(-line) + 1} ou mais gols")
    return f"{team} não pode perder (empate devolve a aposta)"


def plain_explanation(ma) -> str:
    """Traduz o resultado para português claro — o que precisa acontecer,
    o que o modelo acha, o que a casa cobra e o que isso significa."""
    mt = ma.metrics
    # 1) o que precisa acontecer no jogo
    if ma.market == "1x2":
        event = _1X2_EVENTS.get(ma.side, ma.side)
        text = (f"Para ganhar, precisa dar {event}. "
                f"O ChapaFut calcula {mt.p_model:.0%} de chance disso.")
    elif ma.market == "handicap":
        text = (f"Para ganhar, {_handicap_event(ma.side, ma.line)}. "
                f"O ChapaFut calcula {mt.p_model:.0%} de chance disso.")
    elif ma.market == "btts":
        ev = ("os DOIS times marcarem" if ma.side == "yes"
              else "pelo menos um time NÃO marcar")
        text = (f"Para ganhar, precisa {ev}. "
                f"O ChapaFut calcula {mt.p_model:.0%} de chance disso.")
    elif ma.market == "double_chance":
        ev = {"1X": "o mandante vencer OU empatar",
              "12": "o mandante OU o visitante vencer (qualquer um, menos "
                    "empate)",
              "X2": "o visitante vencer OU empatar"}.get(ma.side, ma.side)
        text = (f"Para ganhar, basta {ev}. "
                f"O ChapaFut calcula {mt.p_model:.0%} de chance disso.")
    else:
        unit = _UNITS.get(ma.market, "")
        line = ma.line
        is_int = float(line).is_integer()
        if ma.side == "over":
            need = int(line) + 1 if is_int else math.floor(line) + 1
            event = f"saírem {need} ou mais {unit} no jogo"
        else:
            cap = int(line) - 1 if is_int else math.floor(line)
            event = f"saírem no máximo {cap} {unit} no jogo"
        push = (f" (se der exatamente {int(line)}, a aposta é devolvida)"
                if is_int else "")
        text = (f"Para ganhar, precisam {event}{push}. "
                f"O ChapaFut calcula {mt.p_model:.0%} de chance disso.")
    if mt.odd is None or mt.implied is None:
        return text
    # 2) o que a casa cobra e a comparação
    text += (f" A casa paga odd {mt.odd:.2f} — preço que equivale a "
             f"{mt.implied:.0%} de chance.")
    edge_pts = mt.edge * 100
    if mt.edge >= 0:
        text += (f" O modelo vê {edge_pts:.0f} ponto(s) a MAIS de chance do "
                 f"que o preço cobra: valor a seu favor (se o modelo "
                 f"estiver certo).")
    else:
        text += (f" O modelo vê {abs(edge_pts):.0f} ponto(s) a MENOS do que "
                 f"o preço cobra: você estaria pagando caro.")
    # 3) consequência prática
    verb = "rende" if mt.ev >= 0 else "perde"
    text += (f" No longo prazo, cada 1 apostado {verb} "
             f"{abs(mt.ev):.2f} em média, e 1 derrota nessa odd consome o "
             f"lucro de {mt.wins_to_recover:.1f} vitória(s) iguais.")
    return text


def _lambda_for_key(key: tuple, lambdas: dict) -> float | None:
    market = key[0]
    if market in ("1x2", "handicap", "btts", "double_chance"):
        return None
    if market == "goals":
        return lambdas["goals_home"] + lambdas["goals_away"]
    if market == "team_goals_home":
        return lambdas["goals_home"]
    if market == "team_goals_away":
        return lambdas["goals_away"]
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
                lam = _lambda_for_key(key, lambdas)
                if lam is not None and lam <= 0.05:
                    continue  # API sem estatísticas deste mercado: sem base
                p = _prob_for_key(key, lambdas)
                if p < c.p_min:
                    continue
                metrics = scoring.market_metrics(p, odds_map[key])
                if metrics.ev is not None and metrics.ev < c.ev_min:
                    continue
                ma = MarketAnalysis(
                    market=key[0], side=key[1], line=key[2],
                    lambda_used=_lambda_for_key(key, lambdas),
                    metrics=metrics, passes_filters=True)
                ma.plain = plain_explanation(ma)
                hits_by_crit.setdefault(i, []).append(ma)

        if not hits_by_crit:
            continue
        if match_all and len(hits_by_crit) < len(criteria):
            continue
        hits = [(criteria[i], ma) for i in sorted(hits_by_crit)
                for ma in hits_by_crit[i]]
        # o que compensa mais primeiro (EV decrescente)
        hits.sort(key=lambda h: -(h[1].metrics.ev
                                  if h[1].metrics.ev is not None else -999))
        groups.append(ScanGroup(
            ctx=ctx, lambdas=lambdas, lambda_explanations=expl,
            rule_results=rule_results, charts=count_charts(lambdas),
            hits=hits))

    return ScanResult(
        groups=groups,
        fixtures_today=len(fixtures),
        odds_candidates=len(candidates),
        analyzed=min(len(candidates), deep_limit),
        skipped_by_limit=max(0, len(candidates) - deep_limit))
