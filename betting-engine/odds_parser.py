"""
Parser do retorno de /odds da API-Football (requisito 2).

Transforma a estrutura bookmakers > bets > values num dicionário plano:
    {(mercado, lado, linha): odd}
ex.: {("cards", "over", 4.5): 1.85, ("1x2", "home", None): 2.10}

Bookmaker e mapeamento de mercados são configuráveis em config.py.
"""
from __future__ import annotations

import re

import config

# value da API: "Over 2.5", "Under 9.5", "Home", "Draw", "Away", "Home -1.5"
_OU_RE = re.compile(r"^(over|under)\s+([\d.]+)$", re.IGNORECASE)
_AH_RE = re.compile(r"^(home|away)\s+([+-]?[\d.]+)$", re.IGNORECASE)
_1X2_MAP = {"home": "home", "draw": "draw", "away": "away",
            "1": "home", "x": "draw", "2": "away"}

OddsMap = dict[tuple[str, str, float | None], float]


def _bet_name_to_market(bet_name: str,
                        market_names: dict[str, list[str]]) -> str | None:
    for market, names in market_names.items():
        if bet_name.strip().lower() in (n.lower() for n in names):
            return market
    return None


def parse_odds(odds_response: list,
               bookmaker_id: int | None = None,
               market_names: dict[str, list[str]] | None = None) -> OddsMap:
    """Converte o `response` de /odds no dict {(mercado, lado, linha): odd}.

    - `bookmaker_id`: se informado, considera só esse bookmaker;
      caso contrário usa o primeiro disponível.
    - `market_names`: mapeamento mercado interno -> nomes de bet na API
      (default: config.MARKET_BET_NAMES).
    """
    market_names = market_names or config.MARKET_BET_NAMES
    out: OddsMap = {}

    for item in odds_response:
        for bm in item.get("bookmakers", []):
            if bookmaker_id is not None and bm.get("id") != bookmaker_id:
                continue
            for bet in bm.get("bets", []):
                market = _bet_name_to_market(bet.get("name", ""), market_names)
                if market is None:
                    continue
                for value in bet.get("values", []):
                    label = str(value.get("value", "")).strip()
                    try:
                        odd = float(value.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    key = _parse_value_label(market, label)
                    if key is not None and key not in out:
                        out[key] = odd
            if bookmaker_id is None:
                # sem bookmaker fixado: usa só o primeiro que apareceu
                bookmaker_id = bm.get("id")
    return out


def _parse_value_label(market: str,
                       label: str) -> tuple[str, str, float | None] | None:
    if market == "1x2":
        side = _1X2_MAP.get(label.lower())
        return (market, side, None) if side else None
    if market == "handicap":
        m = _AH_RE.match(label)
        if not m:
            return None
        line = float(m.group(2))
        # só linhas inteiras e meias; linhas de quarto (-0.75) têm regra de
        # devolução parcial que o modelo não representa
        if (line * 2) % 1 != 0:
            return None
        return (market, m.group(1).lower(), line)
    m = _OU_RE.match(label)
    if not m:
        return None
    return (market, m.group(1).lower(), float(m.group(2)))


def available_lines(odds_map: OddsMap, market: str) -> list[float]:
    """Linhas (ex.: 3.5, 4.5...) disponíveis para um mercado over/under."""
    return sorted({line for (mkt, _side, line) in odds_map
                   if mkt == market and line is not None})
