"""
Estratégias pré-montadas: atalhos de 1 clique que viram critérios do
Scanner. Tiram o usuário da tela de 54 campos — ele escolhe uma INTENÇÃO
("quero segurança", "quero valor") e o motor garimpa.

Cada estratégia é só um preset de Criterion + parâmetros de varredura;
nenhuma lógica de modelo nova. Usa só dados que já calculamos.
"""
from __future__ import annotations

from scanner import Criterion

# odd alta como "sem teto" prático
HI = 100.0


def _c(market, side, line, odd_min, odd_max, p_min, ev_min):
    return Criterion(market=market, side=side, line=line, odd_min=odd_min,
                     odd_max=odd_max, p_min=p_min, ev_min=ev_min)


# Cada item: chave -> {icon, nome, desc, criteria, match_all}
STRATEGIES: dict[str, dict] = {
    "seguras": {
        "icon": "🛡️",
        "nome": "Seguras do dia",
        "desc": "Mercados que o modelo considera quase certos (dupla chance "
                "e gols mínimos). Odds baixas, alta probabilidade.",
        "criteria": [
            _c("double_chance", "any", None, 1.01, 1.40, 0.80, 0.0),
            _c("goals", "over", 1.5, 1.01, 1.50, 0.80, 0.0),
            _c("goals", "over", 0.5, 1.01, 1.30, 0.90, -100),
        ],
    },
    "valor": {
        "icon": "💎",
        "nome": "Caça-valor (EV+)",
        "desc": "Onde o modelo vê MAIS chance do que o preço cobra. "
                "Qualquer mercado com vantagem matemática (EV alto).",
        "criteria": [
            _c(m, s, None, 1.30, 4.0, 0.50, 0.10)
            for m, s in (("goals", "over"), ("corners", "over"),
                         ("cards", "over"), ("btts", "yes"),
                         ("1x2", "any"), ("double_chance", "any"))
        ],
    },
    "gols": {
        "icon": "⚽",
        "nome": "Festival de gols",
        "desc": "Jogos abertos: gols acima da linha + ambas marcam.",
        "criteria": [
            _c("goals", "over", 1.5, 1.10, 2.2, 0.65, 0.0),
            _c("goals", "over", 2.5, 1.40, 3.0, 0.55, 0.05),
            _c("btts", "yes", None, 1.30, 2.2, 0.55, 0.0),
        ],
    },
    "escanteios": {
        "icon": "🚩",
        "nome": "Escanteios",
        "desc": "Times que pressionam: escanteios acima da linha.",
        "criteria": [
            _c("corners", "over", None, 1.20, 2.5, 0.60, 0.0),
        ],
    },
    "cartoes": {
        "icon": "🟨",
        "nome": "Cartões",
        "desc": "Jogos quentes / árbitro rígido: cartões acima da linha.",
        "criteria": [
            _c("cards", "over", None, 1.20, 2.5, 0.60, 0.0),
        ],
    },
    "multipla_baixa": {
        "icon": "🎯",
        "nome": "Múltipla de odds baixas",
        "desc": "Pernas 'quase certas' pra montar acumulado seguro "
                "(odd 1.01–1.25, probabilidade ≥ 85%).",
        "criteria": [
            _c(m, s, ln, 1.01, 1.25, 0.85, -100)
            for m, s, ln in (("goals", "over", 0.5), ("goals", "over", 1.5),
                             ("double_chance", "any", None),
                             ("team_goals_home", "over", 0.5))
        ],
    },
    "favoritos": {
        "icon": "🏆",
        "nome": "Favoritos",
        "desc": "Mandantes fortes: vitória da casa ou handicap.",
        "criteria": [
            _c("1x2", "home", None, 1.20, 2.0, 0.55, 0.0),
            _c("handicap", "home", -1.5, 1.50, 3.0, 0.45, 0.0),
        ],
    },
}


def get(key: str) -> dict | None:
    return STRATEGIES.get(key)
