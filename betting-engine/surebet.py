"""
Detector de surebet (arbitragem) entre casas de apostas.

NÃO usa o modelo Poisson — é matemática pura de preço: para um conjunto de
resultados mutuamente exclusivos e exaustivos (ex.: Mais de 2.5 / Menos de
2.5, ou Casa/Empate/Fora), pega a MELHOR odd de cada resultado entre todas
as casas e soma as probabilidades implícitas (1/odd).

    soma < 100%  ->  surebet: lucro travado, dê no que der
    soma 100%–~103%  ->  "quase": vira surebet com um pequeno movimento de odd

Transparência total: a conta inteira fica à mostra, e cada perna diz em qual
casa está a melhor odd. A EXECUÇÃO é sempre manual, na conta do usuário —
o ChapaFut só mostra os números.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config
from odds_parser import _bet_name_to_market, _parse_value_label
from recommender import MARKET_LABELS, SIDE_LABELS

# Mercados que formam uma partição limpa (sem sobreposição) para arbitragem.
# Dupla chance fica de fora de propósito: os resultados se sobrepõem.
OVER_UNDER_MARKETS = ("goals", "corners", "cards",
                      "team_goals_home", "team_goals_away")
DEFAULT_MARKETS = ("goals", "1x2", "btts")
ALL_MARKETS = ("goals", "1x2", "btts", "corners", "cards", "handicap")

# soma das implícitas até este teto entra como "quase-surebet" (não garantido)
NEAR_MAX_DEFAULT = 1.03


def _is_half_line(line: float | None) -> bool:
    """True só para linhas .5 (2.5, -1.5...) — sem risco de empate/devolução."""
    if line is None:
        return False
    return round(line * 2) % 2 == 1


def outcome_label(market: str, side: str, line: float | None) -> str:
    """Texto curto do resultado, do jeito que a casa mostra."""
    if market == "1x2":
        return SIDE_LABELS.get(side, side)
    if market == "btts":
        return f"Ambas marcam: {'Sim' if side == 'yes' else 'Não'}"
    if market == "handicap":
        team = "Casa" if side == "home" else "Fora"
        return f"{team} {line:+g}"
    return f"{SIDE_LABELS.get(side, side)} {line:g}"


def parse_odds_by_book(odds_response: list,
                       market_names: dict | None = None,
                       books: set[str] | None = None) -> dict:
    """{(mercado, lado, linha): {casa: melhor_odd}} mantendo TODAS as casas.

    Ao contrário de odds_parser.parse_odds (que colapsa numa casa só), aqui
    guardamos a odd de cada casa para poder comparar entre elas.
    `books`: se informado, considera só essas casas (pelo nome)."""
    market_names = market_names or config.MARKET_BET_NAMES
    out: dict = {}
    for item in odds_response:
        for bm in item.get("bookmakers", []):
            book = bm.get("name") or str(bm.get("id"))
            if books is not None and book not in books:
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
                    if odd <= 1.0:
                        continue
                    key = _parse_value_label(market, label)
                    if key is None:
                        continue
                    slot = out.setdefault(key, {})
                    if book not in slot or odd > slot[book]:
                        slot[book] = odd  # melhor odd dessa casa p/ o resultado
    return out


@dataclass
class SureLeg:
    market: str
    side: str
    line: float | None
    label: str           # "Mais de 2.5", "Vitória mandante"...
    book: str            # casa com a melhor odd
    odd: float
    implied: float       # 1/odd
    stake_pct: float     # fração do total a apostar nesta perna


@dataclass
class SureBet:
    fid: int
    market: str
    market_label: str
    line: float | None
    legs: list[SureLeg]
    sum_implied: float
    margin: float        # 1/soma - 1 (positivo = lucro garantido por unidade)
    is_arb: bool         # soma < 100%
    n_books: int         # quantas casas distintas a operação usa
    # preenchidos depois com dados do jogo (para exibir)
    fixture: str = "?"
    league: str = ""
    country: str = ""
    when: str = ""
    hl: str = ""
    al: str = ""

    def split(self, total: float) -> list[tuple[SureLeg, float, float]]:
        """Para um valor total, retorna [(perna, valor, retorno_se_ganhar)]."""
        rows = []
        for leg in self.legs:
            stake = total * leg.stake_pct
            rows.append((leg, stake, stake * leg.odd))
        return rows


def _evaluate_group(bybook: dict, market: str, line: float | None,
                    outcomes: list[tuple[str, str, float | None]],
                    min_margin: float, near_max: float) -> SureBet | None:
    """Avalia um conjunto de resultados; devolve SureBet se for arb/quase."""
    best: list[tuple[str, str, float | None, str, float]] = []
    for key in outcomes:
        slot = bybook.get(key)
        if not slot:
            return None  # falta uma perna -> não dá pra travar
        book, odd = max(slot.items(), key=lambda kv: kv[1])
        best.append((key[0], key[1], key[2], book, odd))

    sum_implied = sum(1.0 / odd for *_x, odd in best)
    margin = (1.0 / sum_implied) - 1.0
    is_arb = sum_implied < 1.0
    # filtro: surebets acima da margem mínima, ou "quase" até o teto
    if not (margin >= min_margin or sum_implied <= near_max):
        return None

    legs = [SureLeg(market=mk, side=sd, line=ln,
                    label=outcome_label(mk, sd, ln), book=bk, odd=od,
                    implied=1.0 / od, stake_pct=(1.0 / od) / sum_implied)
            for (mk, sd, ln, bk, od) in best]
    return SureBet(
        fid=0, market=market, market_label=MARKET_LABELS.get(market, market),
        line=line, legs=legs, sum_implied=sum_implied, margin=margin,
        is_arb=is_arb, n_books=len({l.book for l in legs}))


def scan_fixture(bybook: dict, markets: list[str],
                 min_margin: float = 0.0,
                 near_max: float = NEAR_MAX_DEFAULT) -> list[SureBet]:
    """Todas as arbitragens/quase de UM jogo, a partir do mapa por-casa."""
    found: list[SureBet] = []

    for market in markets:
        if market == "1x2":
            grp = [("1x2", "home", None), ("1x2", "draw", None),
                   ("1x2", "away", None)]
            sb = _evaluate_group(bybook, "1x2", None, grp, min_margin, near_max)
            if sb:
                found.append(sb)
        elif market == "btts":
            grp = [("btts", "yes", None), ("btts", "no", None)]
            sb = _evaluate_group(bybook, "btts", None, grp, min_margin, near_max)
            if sb:
                found.append(sb)
        elif market in OVER_UNDER_MARKETS:
            lines = {ln for (mk, _s, ln) in bybook
                     if mk == market and _is_half_line(ln)}
            for ln in sorted(lines):
                grp = [(market, "over", ln), (market, "under", ln)]
                sb = _evaluate_group(bybook, market, ln, grp,
                                     min_margin, near_max)
                if sb:
                    found.append(sb)
        elif market == "handicap":
            home_lines = {ln for (mk, sd, ln) in bybook
                          if mk == "handicap" and sd == "home"
                          and _is_half_line(ln)}
            for ln in sorted(home_lines):
                grp = [("handicap", "home", ln), ("handicap", "away", -ln)]
                sb = _evaluate_group(bybook, "handicap", ln, grp,
                                     min_margin, near_max)
                if sb:
                    found.append(sb)

    found.sort(key=lambda s: -s.margin)
    return found


@dataclass
class SureScan:
    bets: list[SureBet]
    fixtures_scanned: int
    books_seen: int


def scan_day(client, date: str, markets: list[str] | None = None,
             min_margin: float = 0.0, near_max: float = NEAR_MAX_DEFAULT,
             books: set[str] | None = None,
             max_fixtures: int | None = None) -> SureScan:
    """Varre um dia inteiro: odds de todas as casas + nomes dos jogos.

    Barato: 1 chamada de jogos do dia (cache) + odds paginadas por data
    (sem chamadas por time, pois surebet não usa o modelo)."""
    markets = list(markets or DEFAULT_MARKETS)

    fx_map = {}
    for fx in client.fixtures_by_date(date):
        fx_map[fx["fixture"]["id"]] = fx

    odds_response = client.odds_by_date_all(date)
    all_books: set[str] = set()
    bets: list[SureBet] = []
    scanned = 0

    for item in odds_response:
        fid = item.get("fixture", {}).get("id")
        bybook = parse_odds_by_book([item], books=books)
        for bm in item.get("bookmakers", []):
            all_books.add(bm.get("name") or str(bm.get("id")))
        if not bybook:
            continue
        scanned += 1
        found = scan_fixture(bybook, markets, min_margin, near_max)
        if not found:
            continue
        fx = fx_map.get(fid, {})
        teams = fx.get("teams", {})
        league = fx.get("league", {})
        home = teams.get("home", {})
        away = teams.get("away", {})
        label = (f"{home.get('name', '?')} x {away.get('name', '?')}"
                 if teams else f"Jogo #{fid}")
        for sb in found:
            sb.fid = fid
            sb.fixture = label
            sb.league = league.get("name", "")
            sb.country = league.get("country", "")
            sb.when = (fx.get("fixture", {}).get("date", "") or "")[:16]
            sb.hl = home.get("logo", "") or ""
            sb.al = away.get("logo", "") or ""
        bets.extend(found)
        if max_fixtures and scanned >= max_fixtures:
            break

    bets.sort(key=lambda s: -s.margin)
    return SureScan(bets=bets, fixtures_scanned=scanned,
                    books_seen=len(all_books))
