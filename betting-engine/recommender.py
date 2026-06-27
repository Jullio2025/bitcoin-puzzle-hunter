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
                 "1x2": "Resultado (1X2)",
                 "team_goals_home": "Gols do mandante",
                 "team_goals_away": "Gols do visitante",
                 "handicap": "Handicap", "btts": "Ambas marcam",
                 "double_chance": "Dupla chance",
                 "odd_even": "Par/Ímpar (gols)",
                 "clean_sheet_home": "Mandante não sofre gol",
                 "clean_sheet_away": "Visitante não sofre gol",
                 "draw_no_bet": "Empate anula"}
SIDE_LABELS = {"over": "Mais de", "under": "Menos de",
               "home": "Vitória mandante", "draw": "Empate",
               "away": "Vitória visitante",
               "yes": "Sim", "no": "Não",
               "even": "Par", "odd": "Ímpar",
               "1X": "Casa ou empate", "12": "Casa ou visitante",
               "X2": "Empate ou visitante"}

# mercados derivados do Poisson de gols (line=None): lados por mercado
DERIVED_SIDES = {"btts": ("yes", "no"),
                 "double_chance": ("1X", "12", "X2"),
                 "odd_even": ("even", "odd"),
                 "clean_sheet_home": ("yes", "no"),
                 "clean_sheet_away": ("yes", "no")}


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
        if self.market == "handicap":
            team = "Casa" if self.side == "home" else "Fora"
            return f"{base}: {team} {self.line:+g}"
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
    charts: dict = field(default_factory=dict)


def markets_without_base(ctx: MatchContext) -> set[str]:
    """Mercados sem estatística da API (médias cruas zeradas dos dois times).

    Detecta pela amostra crua — NÃO pelo lambda — porque o encolhimento
    puxa zeros para o baseline e mascararia a ausência de dados."""
    h, a = ctx.home, ctx.away
    out: set[str] = set()
    if (h.corners_for + h.corners_against
            + a.corners_for + a.corners_against) <= 0:
        out.add("corners")
    if (h.cards + a.cards) <= 0:
        out.add("cards")
    return out


def count_charts(lambdas: dict[str, float], omit: set[str] = frozenset()) -> dict:
    """Dados dos gráficos da interface: distribuição de Poisson por mercado
    (P de cada contagem exata) e probabilidades do 1X2.

    `omit` lista mercados sem base de dados (não desenha gráfico mentiroso)."""
    charts: dict = {}
    specs = {"goals": lambdas["goals_home"] + lambdas["goals_away"],
             "corners": lambdas["corners"], "cards": lambdas["cards"]}
    for market, lam in specs.items():
        if lam <= 0.05 or market in omit:
            continue  # sem estatísticas: gráfico mentiroso é pior que nenhum
        k_max = max(6, min(18, int(lam * 2 + 3)))
        points = [{"k": k, "p": scoring.poisson_pmf(k, lam)}
                  for k in range(k_max + 1)]
        p_max = max(pt["p"] for pt in points) or 1.0
        for pt in points:
            pt["h"] = round(100 * pt["p"] / p_max, 1)  # altura da barra (%)
        charts[market] = {"lam": lam, "points": points}
    ph, pd, pa = scoring.match_outcome_probs(
        lambdas["goals_home"], lambdas["goals_away"],
        max_goals=config.MODEL["max_goals_grid"])
    charts["p1x2"] = {"home": ph, "draw": pd, "away": pa}
    return charts


# ------------------------------------------------------------ lambdas base
def _shrink(value: float, games: int, baseline: float) -> float:
    """Regressão à média: puxa `value` (de `games` jogos) para `baseline`.

    shrunk = (n*observado + k*baseline) / (n + k). Com poucos jogos, o
    resultado fica mais perto do baseline (menos superconfiança); com
    muitos jogos, fica perto do observado. k=0 desliga.
    """
    k = config.MODEL.get("shrink_k", 0)
    if k <= 0 or games <= 0:
        return value
    return (games * value + k * baseline) / (games + k)


def base_lambdas(ctx: MatchContext) -> tuple[dict[str, float], dict[str, str]]:
    """Lambdas base a partir das médias por mando, com explicação textual.

    Cada taxa de time passa por regressão à média (encolhimento) conforme
    o tamanho da amostra, para o modelo não ficar superconfiante com poucos
    jogos."""
    h, a = ctx.home, ctx.away
    M = config.MODEL

    hgf = _shrink(h.goals_for, h.games, M["baseline_goals_for"])
    hga = _shrink(h.goals_against, h.games, M["baseline_goals_against"])
    agf = _shrink(a.goals_for, a.games, M["baseline_goals_for"])
    aga = _shrink(a.goals_against, a.games, M["baseline_goals_against"])
    hcf = _shrink(h.corners_for, h.games, M["baseline_corners_for"])
    hca = _shrink(h.corners_against, h.games, M["baseline_corners_against"])
    acf = _shrink(a.corners_for, a.games, M["baseline_corners_for"])
    aca = _shrink(a.corners_against, a.games, M["baseline_corners_against"])
    hcards = _shrink(h.cards, h.games, M["baseline_cards_team"])
    acards = _shrink(a.cards, a.games, M["baseline_cards_team"])

    goals_home = (hgf + aga) / 2
    goals_away = (agf + hga) / 2
    corners = ((hcf + aca) / 2 + (acf + hca) / 2)
    cards = hcards + acards

    lambdas = {"goals_home": goals_home, "goals_away": goals_away,
               "corners": corners, "cards": cards}
    nota = ("  (valores já ponderados pelo tamanho da amostra — quanto menos "
            "jogos, mais perto da média típica, p/ evitar exagero.)")
    expl = {
        "goals_home": (
            f"⚽ Gols esperados do {h.team_name} (mandante): em casa marcou "
            f"~{h.goals_for:.2f}/jogo e o {a.team_name} sofreu "
            f"~{a.goals_against:.2f}/jogo fora → {goals_home:.2f} esperados."
            + nota),
        "goals_away": (
            f"⚽ Gols esperados do {a.team_name} (visitante): fora marcou "
            f"~{a.goals_for:.2f}/jogo e o {h.team_name} sofreu "
            f"~{h.goals_against:.2f}/jogo em casa → {goals_away:.2f} esperados."
            + nota),
        "corners": (
            f"🚩 Escanteios esperados: {h.team_name} força ~{h.corners_for:.2f} "
            f"em casa, {a.team_name} força ~{a.corners_for:.2f} fora → "
            f"{corners:.2f} no jogo." + nota),
        "cards": (
            f"🟨 Cartões esperados: {h.team_name} recebe ~{h.cards:.2f}/jogo em "
            f"casa e {a.team_name} ~{a.cards:.2f}/jogo fora → {cards:.2f} no "
            f"jogo." + nota),
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
        failed.append(f"odd {metrics.odd:.2f} abaixo da sua mín. "
                      f"{params.odd_min:.2f}")
    if metrics.odd > params.odd_max:
        failed.append(f"odd {metrics.odd:.2f} acima da sua máx. "
                      f"{params.odd_max:.2f}")
    if metrics.p_model < params.p_min:
        failed.append(f"chance {metrics.p_model:.0%} abaixo da sua mín. "
                      f"{params.p_min:.0%}")
    if metrics.ev is not None and metrics.ev < params.ev_min:
        failed.append("não compensa pelo seu mínimo")
    return not failed, failed


# ---------------------------------------------------------------- análise
def analyze_fixture(ctx: MatchContext, odds_map: OddsMap,
                    params: UserParams) -> FixtureAnalysis:
    lambdas, expl = base_lambdas(ctx)
    lambdas, rule_results = apply_rules(ctx, lambdas, params.rules_enabled)
    no_base = markets_without_base(ctx)

    markets: list[MarketAnalysis] = []

    for market in params.markets:
        if market == "1x2":
            markets.extend(_analyze_1x2(lambdas, odds_map, params))
            continue
        if market == "handicap":
            markets.extend(_analyze_handicap(lambdas, odds_map, params))
            continue
        if market == "draw_no_bet":
            markets.extend(_analyze_dnb(lambdas, odds_map, params))
            continue
        if market in DERIVED_SIDES:
            markets.extend(_analyze_derived(market, lambdas, odds_map, params))
            continue
        if market == "team_goals_home":
            lam = lambdas["goals_home"]
        elif market == "team_goals_away":
            lam = lambdas["goals_away"]
        else:
            lam = lambdas["goals_home"] + lambdas["goals_away"] \
                if market == "goals" else lambdas.get(market)
        if lam is None:
            continue
        # mercado sem estatística da API (detecção pela amostra crua)
        no_data = market in no_base or lam <= 0.05
        lines = available_lines(odds_map, market)
        if not lines:
            # sem odds: mostra a linha "clássica" só com p_model
            lines = {"goals": [2.5], "corners": [9.5], "cards": [4.5],
                     "team_goals_home": [1.5], "team_goals_away": [1.5]}[market]
        for line in lines:
            for side, p in (("over", scoring.prob_over(lam, line)),
                            ("under", scoring.prob_under(lam, line))):
                odd = odds_map.get((market, side, line))
                metrics = scoring.market_metrics(p, odd)
                if scoring.prob_push(lam, line) > 0:
                    metrics.notes.append(
                        f"Linha inteira: P(push/devolução) = "
                        f"{scoring.prob_push(lam, line):.1%}.")
                if no_data:
                    metrics.notes.append(
                        "SEM BASE: a API não trouxe estatísticas de "
                        f"{MARKET_LABELS.get(market, market).lower()} dos "
                        "últimos jogos destes times — desconsidere estes "
                        "números.")
                    ok, failed = False, ["sem dados do mercado"]
                else:
                    ok, failed = _check_filters(metrics, params)
                markets.append(MarketAnalysis(
                    market=market, side=side, line=line, lambda_used=lam,
                    metrics=metrics, passes_filters=ok, failed_filters=failed))

    return FixtureAnalysis(ctx=ctx, lambdas=lambdas,
                           lambda_explanations=expl,
                           rule_results=rule_results, markets=markets,
                           charts=count_charts(lambdas, omit=no_base))


def _analyze_handicap(lambdas: dict[str, float], odds_map: OddsMap,
                      params: UserParams) -> list[MarketAnalysis]:
    """Handicap asiático: só as linhas que a casa publicou (sem inventar)."""
    out = []
    for (mkt, side, line), odd in sorted(odds_map.items(),
                                         key=lambda kv: str(kv[0])):
        if mkt != "handicap":
            continue
        p_win, p_push = scoring.handicap_prob(
            lambdas["goals_home"], lambdas["goals_away"], side, line,
            max_goals=config.MODEL["max_goals_grid"])
        metrics = scoring.market_metrics(p_win, odd)
        if p_push > 0:
            metrics.notes.append(
                f"Linha inteira: P(push/devolução) = {p_push:.1%}.")
        ok, failed = _check_filters(metrics, params)
        out.append(MarketAnalysis(market="handicap", side=side, line=line,
                                  lambda_used=None, metrics=metrics,
                                  passes_filters=ok, failed_filters=failed))
    return out


def _derived_prob(market: str, side: str, lambdas: dict[str, float]) -> float:
    lh, la = lambdas["goals_home"], lambdas["goals_away"]
    mg = config.MODEL["max_goals_grid"]
    if market == "btts":
        p_yes = scoring.both_teams_score_prob(lh, la)
        return p_yes if side == "yes" else 1.0 - p_yes
    if market == "double_chance":
        return scoring.double_chance_prob(lh, la, side, max_goals=mg)
    if market == "odd_even":
        par, impar = scoring.odd_even_prob(lh, la, mg)
        return par if side == "even" else impar
    if market in ("clean_sheet_home", "clean_sheet_away"):
        team = "home" if market.endswith("home") else "away"
        p_yes = scoring.clean_sheet_prob(lh, la, team, mg)
        return p_yes if side == "yes" else 1.0 - p_yes
    return 0.0


def _analyze_derived(market: str, lambdas: dict[str, float], odds_map: OddsMap,
                     params: UserParams) -> list[MarketAnalysis]:
    """Mercados derivados do Poisson de gols (ambas marcam, dupla chance,
    par/ímpar, não sofre gol) — sem custo extra de API."""
    sides = DERIVED_SIDES[market]
    no_data = lambdas["goals_home"] + lambdas["goals_away"] <= 0.05
    out = []
    for side in sides:
        p = _derived_prob(market, side, lambdas)
        odd = odds_map.get((market, side, None))
        metrics = scoring.market_metrics(p, odd)
        if no_data:
            metrics.notes.append(
                "SEM BASE: a API não trouxe gols dos últimos jogos destes "
                "times — desconsidere estes números.")
            ok, failed = False, ["sem dados do mercado"]
        else:
            ok, failed = _check_filters(metrics, params)
        out.append(MarketAnalysis(market=market, side=side, line=None,
                                  lambda_used=None, metrics=metrics,
                                  passes_filters=ok, failed_filters=failed))
    return out


def _analyze_dnb(lambdas: dict[str, float], odds_map: OddsMap,
                 params: UserParams) -> list[MarketAnalysis]:
    """Empate anula (DNB): vitória do time, com devolução no empate."""
    out = []
    for side in ("home", "away"):
        p_win, p_push = scoring.draw_no_bet_prob(
            lambdas["goals_home"], lambdas["goals_away"], side,
            max_goals=config.MODEL["max_goals_grid"])
        odd = odds_map.get(("draw_no_bet", side, None))
        metrics = scoring.market_metrics(p_win, odd)
        if p_push > 0:
            metrics.notes.append(
                f"Empate anula: no empate a aposta é devolvida "
                f"(P = {p_push:.1%}).")
        ok, failed = _check_filters(metrics, params)
        out.append(MarketAnalysis(market="draw_no_bet", side=side, line=None,
                                  lambda_used=None, metrics=metrics,
                                  passes_filters=ok, failed_filters=failed))
    return out


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
