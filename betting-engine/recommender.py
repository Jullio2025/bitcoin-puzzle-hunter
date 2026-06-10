"""
Núcleo de análise: junta features + regras + odds e calcula, para CADA
mercado pedido, o pacote completo de transparência.

Princípios:
- NADA é filtrado silenciosamente: todos os mercados pedidos aparecem,
  com uma marcação dizendo se passam ou não nos filtros DO USUÁRIO.
- Cada lambda vem com a explicação de como foi calculado e com o
  detalhamento do ajuste de cada regra.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config
import scoring
from features import MatchContext
from odds_parser import OddsMap, available_lines
from rules import RuleResult, get_rules

MARKET_LABELS = {"goals": "Gols", "corners": "Escanteios", "cards": "Cartões",
                 "1x2": "Resultado (1X2)"}
SIDE_LABELS = {"over": "Mais de", "under": "Menos de",
               "home": "Vitória mandante", "draw": "Empate",
               "away": "Vitória visitante"}


@dataclass
class UserParams:
    """Parâmetros do USUÁRIO. Aceitam QUALQUER valor; defaults são sugestão."""
    odd_min: float = config.USER_DEFAULTS["odd_min"]
    odd_max: float = config.USER_DEFAULTS["odd_max"]
    p_min: float = config.USER_DEFAULTS["p_min"]
    ev_min: float = config.USER_DEFAULTS["ev_min"]
    mode: str = config.USER_DEFAULTS["mode"]  # "simples" | "multipla"
    markets: list[str] = field(
        default_factory=lambda: ["goals", "corners", "cards", "1x2"])
    rules_enabled: list[str] | None = None    # None = todas


@dataclass
class MarketAnalysis:
    market: str
    side: str
    line: float | None
    lambda_used: float | None      # None para 1X2 (usa dois lambdas)
    metrics: scoring.MarketMetrics
    passes_filters: bool
    failed_filters: list[str] = field(default_factory=list)
    plain: str = ""                # explicação em português claro (scanner)

    @property
    def label(self) -> str:
        base = MARKET_LABELS.get(self.market, self.market)
        side = SIDE_LABELS.get(self.side, self.side)
        if self.line is not None:
            return f"{base}: {side} {self.line}"
        return f"{base}: {side}"


@dataclass
class FixtureAnalysis:
    ctx: MatchContext
    lambdas: dict[str, float]
    lambda_explanations: dict[str, str]
    rule_results: list[RuleResult]
    markets: list[MarketAnalysis]


# ------------------------------------------------------------ lambdas base
def base_lambdas(ctx: MatchContext) -> tuple[dict[str, float], dict[str, str]]:
    """Lambdas base a partir das médias por mando, com explicação textual."""
    h, a = ctx.home, ctx.away
    goals_home = (h.goals_for + a.goals_against) / 2
    goals_away = (a.goals_for + h.goals_against) / 2
    corners = ((h.corners_for + a.corners_against) / 2
               + (a.corners_for + h.corners_against) / 2)
    cards = h.cards + a.cards

    lambdas = {"goals_home": goals_home, "goals_away": goals_away,
               "corners": corners, "cards": cards}
    expl = {
        "goals_home": (f"média({h.team_name} marca em casa {h.goals_for:.2f}, "
                       f"{a.team_name} sofre fora {a.goals_against:.2f}) "
                       f"= {goals_home:.2f}"),
        "goals_away": (f"média({a.team_name} marca fora {a.goals_for:.2f}, "
                       f"{h.team_name} sofre em casa {h.goals_against:.2f}) "
                       f"= {goals_away:.2f}"),
        "corners": (f"média cruzada casa ({h.corners_for:.2f} a favor x "
                    f"{a.corners_against:.2f} cedidos) + fora "
                    f"({a.corners_for:.2f} x {h.corners_against:.2f}) "
                    f"= {corners:.2f}"),
        "cards": (f"{h.team_name} recebe {h.cards:.2f}/jogo em casa + "
                  f"{a.team_name} recebe {a.cards:.2f}/jogo fora "
                  f"= {cards:.2f}"),
    }
    return lambdas, expl


def apply_rules(ctx: MatchContext, lambdas: dict[str, float],
                rules_enabled: list[str] | None
                ) -> tuple[dict[str, float], list[RuleResult]]:
    """Aplica os multiplicadores de cada regra registrada aos lambdas."""
    adjusted = dict(lambdas)
    results = []
    for rule in get_rules(rules_enabled):
        res = rule.apply(ctx)
        results.append(res)
        for key, mult in res.multipliers.items():
            if key in adjusted:
                adjusted[key] *= mult
    return adjusted, results


# --------------------------------------------------------------- filtros
def _check_filters(metrics: scoring.MarketMetrics,
                   params: UserParams) -> tuple[bool, list[str]]:
    """Marca (sem esconder!) se o mercado passa nos filtros do usuário."""
    failed = []
    if metrics.odd is None:
        failed.append("sem odd")
        return False, failed
    if metrics.odd < params.odd_min:
        failed.append(f"odd {metrics.odd:.2f} < mín {params.odd_min:.2f}")
    if metrics.odd > params.odd_max:
        failed.append(f"odd {metrics.odd:.2f} > máx {params.odd_max:.2f}")
    if metrics.p_model < params.p_min:
        failed.append(f"p_model {metrics.p_model:.1%} < mín {params.p_min:.1%}")
    if metrics.ev is not None and metrics.ev < params.ev_min:
        failed.append(f"EV {metrics.ev:+.3f} < mín {params.ev_min:+.3f}")
    return not failed, failed


# ---------------------------------------------------------------- análise
def analyze_fixture(ctx: MatchContext, odds_map: OddsMap,
                    params: UserParams) -> FixtureAnalysis:
    lambdas, expl = base_lambdas(ctx)
    lambdas, rule_results = apply_rules(ctx, lambdas, params.rules_enabled)

    markets: list[MarketAnalysis] = []

    for market in params.markets:
        if market == "1x2":
            markets.extend(_analyze_1x2(lambdas, odds_map, params))
            continue
        lam = lambdas["goals_home"] + lambdas["goals_away"] \
            if market == "goals" else lambdas.get(market)
        if lam is None:
            continue
        lines = available_lines(odds_map, market)
        if not lines:
            # sem odds: mostra a linha "clássica" só com p_model
            lines = {"goals": [2.5], "corners": [9.5], "cards": [4.5]}[market]
        for line in lines:
            for side, p in (("over", scoring.prob_over(lam, line)),
                            ("under", scoring.prob_under(lam, line))):
                odd = odds_map.get((market, side, line))
                metrics = scoring.market_metrics(p, odd)
                if scoring.prob_push(lam, line) > 0:
                    metrics.notes.append(
                        f"Linha inteira: P(push/devolução) = "
                        f"{scoring.prob_push(lam, line):.1%}.")
                ok, failed = _check_filters(metrics, params)
                markets.append(MarketAnalysis(
                    market=market, side=side, line=line, lambda_used=lam,
                    metrics=metrics, passes_filters=ok, failed_filters=failed))

    return FixtureAnalysis(ctx=ctx, lambdas=lambdas,
                           lambda_explanations=expl,
                           rule_results=rule_results, markets=markets)


def _analyze_1x2(lambdas: dict[str, float], odds_map: OddsMap,
                 params: UserParams) -> list[MarketAnalysis]:
    ph, pd, pa = scoring.match_outcome_probs(
        lambdas["goals_home"], lambdas["goals_away"],
        max_goals=config.MODEL["max_goals_grid"])
    out = []
    for side, p in (("home", ph), ("draw", pd), ("away", pa)):
        odd = odds_map.get(("1x2", side, None))
        metrics = scoring.market_metrics(p, odd)
        ok, failed = _check_filters(metrics, params)
        out.append(MarketAnalysis(market="1x2", side=side, line=None,
                                  lambda_used=None, metrics=metrics,
                                  passes_filters=ok, failed_filters=failed))
    return out


# --------------------------------------------------------------- múltipla
@dataclass
class MultipleLeg:
    fixture_label: str
    market_label: str
    odd: float
    p_model: float


def build_multiple(legs: list[MultipleLeg]) -> scoring.CombinedTicket:
    """Monta a múltipla. A exibição DEVE mostrar sempre os 3 números juntos
    (odd combinada, probabilidade combinada e chance de perder) — nunca a
    odd combinada sozinha."""
    return scoring.combine_legs([(leg.odd, leg.p_model) for leg in legs])
