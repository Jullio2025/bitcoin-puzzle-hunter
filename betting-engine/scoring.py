"""
Matemática do motor — 100% transparente e auditável. Sem caixa-preta.

Todas as funções são puras (sem API, sem estado) para serem testáveis
e para o usuário conseguir reproduzir cada número na mão.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ----------------------------------------------------------------- Poisson
def poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) para X ~ Poisson(lam)."""
    if lam < 0:
        raise ValueError("lambda não pode ser negativo")
    if k < 0:
        return 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) para X ~ Poisson(lam)."""
    if k < 0:
        return 0.0
    return sum(poisson_pmf(i, lam) for i in range(k + 1))


def prob_over(lam: float, line: float) -> float:
    """P(total > linha). Para linha 4.5 => P(X >= 5).

    Em linha inteira (ex.: 4.0), o empate exato (push) NÃO conta como
    over nem under — use prob_push para ver essa massa.
    """
    return 1.0 - poisson_cdf(math.floor(line), lam)


def prob_under(lam: float, line: float) -> float:
    """P(total < linha). Para linha 4.5 => P(X <= 4)."""
    if float(line).is_integer():
        return poisson_cdf(int(line) - 1, lam)
    return poisson_cdf(math.floor(line), lam)


def prob_push(lam: float, line: float) -> float:
    """P(total == linha) quando a linha é inteira (devolução); 0 se .5."""
    if float(line).is_integer():
        return poisson_pmf(int(line), lam)
    return 0.0


# ---------------------------------------------------- métricas por mercado
def implied_prob(odd: float) -> float:
    """Probabilidade implícita na odd = 1/odd (inclui a margem da casa)."""
    if odd <= 1.0:
        raise ValueError("odd deve ser > 1.0")
    return 1.0 / odd


def edge(p_model: float, odd: float) -> float:
    """Edge = p_model - 1/odd. Positivo = o modelo vê valor."""
    return p_model - implied_prob(odd)


def ev_per_unit(p_model: float, odd: float) -> float:
    """EV por unidade apostada = p*(odd-1) - (1-p)."""
    return p_model * (odd - 1.0) - (1.0 - p_model)


def breakeven_rate(odd: float) -> float:
    """Taxa de acerto necessária para empatar no longo prazo = 1/odd."""
    return implied_prob(odd)


def wins_to_recover(odd: float) -> float:
    """Quantas vitórias recuperam 1 derrota = 1/(odd-1).

    Mata a ilusão de segurança das odds baixas: a 1.20, uma derrota
    consome o lucro de 5 vitórias.
    """
    if odd <= 1.0:
        raise ValueError("odd deve ser > 1.0")
    return 1.0 / (odd - 1.0)


# ------------------------------------------------------------------- 1X2
def match_outcome_probs(lam_home: float, lam_away: float,
                        max_goals: int = 10) -> tuple[float, float, float]:
    """(P_casa, P_empate, P_fora) com Poisson independente por time.

    Soma a grade de placares 0..max_goals e normaliza o resíduo da cauda.
    """
    p_home = p_draw = p_away = 0.0
    for h in range(max_goals + 1):
        ph = poisson_pmf(h, lam_home)
        for a in range(max_goals + 1):
            p = ph * poisson_pmf(a, lam_away)
            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p
    total = p_home + p_draw + p_away
    return p_home / total, p_draw / total, p_away / total


def handicap_prob(lam_home: float, lam_away: float, side: str,
                  line: float, max_goals: int = 10) -> tuple[float, float]:
    """(P_ganhar, P_push) do handicap asiático para `side` com `line`.

    Ex.: side='away', line=+3 -> visitante cobre se gols_fora + 3 >
    gols_casa (push se empatar exatamente com a linha inteira).
    Mesma grade de placares do 1X2 — totalmente auditável.
    """
    if side not in ("home", "away"):
        raise ValueError("side deve ser 'home' ou 'away'")
    p_win = p_push = total = 0.0
    for h in range(max_goals + 1):
        ph = poisson_pmf(h, lam_home)
        for a in range(max_goals + 1):
            p = ph * poisson_pmf(a, lam_away)
            total += p
            adj = (h + line - a) if side == "home" else (a + line - h)
            if adj > 1e-9:
                p_win += p
            elif abs(adj) <= 1e-9:
                p_push += p
    return p_win / total, p_push / total


# -------------------------------------------------------------- múltiplas
@dataclass
class CombinedTicket:
    combined_odd: float
    combined_prob: float
    lose_prob: float
    warning: str = ("Probabilidade combinada assume INDEPENDÊNCIA entre as "
                    "pernas — é uma aproximação. Pernas do mesmo jogo são "
                    "correlacionadas e o número real pode diferir.")


def combine_legs(legs: list[tuple[float, float]]) -> CombinedTicket:
    """Combina pernas [(odd, p_model), ...] de uma múltipla.

    Odd combinada = produto das odds.
    Probabilidade combinada = produto dos p_model (aprox. de independência).
    Chance de perder o bilhete = 1 - probabilidade combinada.
    """
    if not legs:
        raise ValueError("múltipla precisa de pelo menos 1 perna")
    odd = 1.0
    prob = 1.0
    for o, p in legs:
        if o <= 1.0:
            raise ValueError("odd de perna deve ser > 1.0")
        if not 0.0 <= p <= 1.0:
            raise ValueError("p_model deve estar entre 0 e 1")
        odd *= o
        prob *= p
    return CombinedTicket(combined_odd=odd, combined_prob=prob,
                          lose_prob=1.0 - prob)


# ------------------------------------------------- pacote de transparência
@dataclass
class MarketMetrics:
    """Todos os números que o usuário precisa ver para decidir sozinho."""
    p_model: float
    odd: float | None = None
    implied: float | None = None
    edge: float | None = None
    ev: float | None = None
    breakeven: float | None = None
    wins_to_recover: float | None = None
    notes: list[str] = field(default_factory=list)


def market_metrics(p_model: float, odd: float | None) -> MarketMetrics:
    """Calcula o pacote completo de métricas; odd ausente => só p_model."""
    m = MarketMetrics(p_model=p_model, odd=odd)
    if odd is None:
        m.notes.append("Sem odd disponível neste bookmaker para este mercado.")
        return m
    if odd <= 1.0:
        # casas às vezes publicam 1.00 em mercado suspenso/decidido
        m.notes.append(f"Odd {odd:.2f} ≤ 1.00 (mercado suspenso?) — "
                       "métricas não calculáveis.")
        m.odd = None
        return m
    m.implied = implied_prob(odd)
    m.edge = edge(p_model, odd)
    m.ev = ev_per_unit(p_model, odd)
    m.breakeven = breakeven_rate(odd)
    m.wins_to_recover = wins_to_recover(odd)
    return m
