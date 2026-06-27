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
from datetime import datetime, timezone

import config
from odds_parser import _bet_name_to_market, _parse_value_label
from recommender import MARKET_LABELS, SIDE_LABELS

# Mercados que formam uma partição limpa (sem sobreposição) para arbitragem.
# Dupla chance fica de fora de propósito: os resultados se sobrepõem.
OVER_UNDER_MARKETS = ("goals", "corners", "cards",
                      "team_goals_home", "team_goals_away")
DEFAULT_MARKETS = ("goals", "1x2", "btts")
ALL_MARKETS = ("goals", "team_goals_home", "team_goals_away", "1x2", "btts",
               "odd_even", "clean_sheet_home", "clean_sheet_away",
               "draw_no_bet", "corners", "cards", "handicap")

# mercados de 2 vias com linha None (partição limpa: os dois lados cobrem tudo).
# draw_no_bet entra aqui porque no empate AMBAS as pernas são devolvidas
# (push) — então cobrir os dois lados é seguro (no pior caso, empata).
TWO_WAY_MARKETS = {
    "btts": ("yes", "no"),
    "odd_even": ("even", "odd"),
    "clean_sheet_home": ("yes", "no"),
    "clean_sheet_away": ("yes", "no"),
    "draw_no_bet": ("home", "away"),
}

# soma das implícitas até este teto entra como "quase-surebet" (não garantido)
NEAR_MAX_DEFAULT = 1.03

# Margem máxima crível para uma arbitragem REAL. Acima disso é erro de dado
# (odd velha/ilíquida/mal rotulada): no pré-jogo arbitragem real fica em
# ~0–5%; uma "surebet" de 50%, 500% etc. nunca é verdadeira.
MAX_PLAUSIBLE_MARGIN = 0.15


def _is_half_line(line: float | None) -> bool:
    """True só para linhas .5 (2.5, -1.5...) — sem risco de empate/devolução."""
    if line is None:
        return False
    return round(line * 2) % 2 == 1


def outcome_label(market: str, side: str, line: float | None) -> str:
    """Só o LADO da aposta (o mercado e a linha já aparecem no topo do card,
    então não repetimos aqui — deixa a coluna curta)."""
    if market == "btts":
        return "Sim" if side == "yes" else "Não"
    if market == "handicap":     # a linha distingue os dois lados, mantém
        team = "Casa" if side == "home" else "Fora"
        return f"{team} {line:+g}"
    return SIDE_LABELS.get(side, side)   # 1X2: Vitória.../Empate · O/U: Mais de/Menos de


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
    depth: int = 0       # casas que cotam a perna MAIS RASA (liquidez/limite)
    suspect: bool = False        # parece arb mas cheira a erro de dado
    suspect_reason: str = ""     # por que é suspeita (mostrado ao usuário)
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

    @property
    def liquidity(self) -> str:
        """Quão líquido/confiável é o mercado, pela profundidade de casas.

        Mercado raso = poucas casas cotam = limite de aposta provavelmente
        baixo e maior chance de a odd estar velha."""
        if self.depth <= 2:
            return "raso"      # ⚠️ limite provavelmente baixo
        if self.depth <= 4:
            return "médio"
        return "líquido"


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
    n_books = len({bk for *_x, bk, _od in best})
    # profundidade = casas que cotam a perna MAIS RASA (gargalo de liquidez)
    depth = min(len(bybook.get(key, {})) for key in outcomes)

    # Classifica em: arb limpa | quase | SUSPEITA (nada é descartado em
    # silêncio — o que cheira a erro vai pra uma lista separada e marcada).
    suspect, reason = False, ""
    if is_arb:
        # Uma casa sozinha nunca arbitra contra si mesma (sempre tem
        # overround); e arbitragem real no pré-jogo fica em ~0–5%. Fora
        # disso é odd velha/ilíquida/mal rotulada — quase sempre a casa
        # anula depois pela regra de "erro palpável".
        if n_books < 2:
            suspect = True
            reason = ("aposta cairia numa casa só — uma casa nunca arbitra "
                      "contra si mesma; isso é dado quebrado/odd mal rotulada")
        elif margin > MAX_PLAUSIBLE_MARGIN:
            suspect = True
            reason = ("margem alta demais p/ ser real — quase sempre odd "
                      "velha/ilíquida ou erro que a casa anula depois; "
                      "confira AO VIVO na casa antes de qualquer coisa")
        elif margin < min_margin:
            return None  # arb limpa abaixo da margem mínima pedida
    else:
        # quase-surebet: só interessa entre casas diferentes e dentro do teto
        if n_books < 2 or sum_implied > near_max:
            return None

    legs = [SureLeg(market=mk, side=sd, line=ln,
                    label=outcome_label(mk, sd, ln), book=bk, odd=od,
                    implied=1.0 / od, stake_pct=(1.0 / od) / sum_implied)
            for (mk, sd, ln, bk, od) in best]
    return SureBet(
        fid=0, market=market, market_label=MARKET_LABELS.get(market, market),
        line=line, legs=legs, sum_implied=sum_implied, margin=margin,
        is_arb=is_arb, n_books=n_books, depth=depth,
        suspect=suspect, suspect_reason=reason)


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
        elif market in TWO_WAY_MARKETS:
            grp = [(market, s, None) for s in TWO_WAY_MARKETS[market]]
            sb = _evaluate_group(bybook, market, None, grp, min_margin, near_max)
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
    bets: list[SureBet]        # arbitragens/quase confiáveis
    suspects: list[SureBet]    # parecem arb mas cheiram a erro (mostradas à parte)
    fixtures_scanned: int
    books_seen: int


def _is_upcoming(fx: dict, now) -> bool:
    """True só se o jogo ainda NÃO começou (kickoff no futuro).

    Surebet é pré-jogo: jogo que já rolou/está rolando não serve (não dá pra
    apostar, e a odd mostrada é velha)."""
    raw = (fx.get("fixture", {}) or {}).get("date") if fx else None
    if not raw:
        return False
    try:
        kickoff = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if kickoff.tzinfo is None:           # data sem fuso: assume UTC
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    return kickoff > now


def scan_day(client, date: str, markets: list[str] | None = None,
             min_margin: float = 0.0, near_max: float = NEAR_MAX_DEFAULT,
             books: set[str] | None = None,
             max_fixtures: int | None = None,
             only_upcoming: bool = True, min_depth: int = 1) -> SureScan:
    """Varre um dia inteiro: odds de todas as casas + nomes dos jogos.

    Barato: 1 chamada de jogos do dia (cache) + odds paginadas por data
    (sem chamadas por time, pois surebet não usa o modelo).
    only_upcoming: ignora jogos que já começaram (padrão — surebet é pré-jogo).
    min_depth: exige que a linha apareça em >= N casas (2+ = confirmável;
    filtra as 'fantasmas' que só uma casa lista)."""
    markets = list(markets or DEFAULT_MARKETS)
    now = datetime.now(timezone.utc)

    fx_map = {}
    for fx in client.fixtures_by_date(date):
        fx_map[fx["fixture"]["id"]] = fx

    odds_response = client.odds_by_date_all(date)
    all_books: set[str] = set()
    bets: list[SureBet] = []
    suspects: list[SureBet] = []
    scanned = 0

    for item in odds_response:
        fid = item.get("fixture", {}).get("id")
        for bm in item.get("bookmakers", []):
            all_books.add(bm.get("name") or str(bm.get("id")))
        fx = fx_map.get(fid, {})
        if only_upcoming and not _is_upcoming(fx, now):
            continue  # jogo já começou/acabou -> fora (surebet é pré-jogo)
        bybook = parse_odds_by_book([item], books=books)
        if not bybook:
            continue
        scanned += 1
        found = scan_fixture(bybook, markets, min_margin, near_max)
        if not found:
            continue
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
        bets.extend(s for s in found
                    if not s.suspect and s.depth >= min_depth)
        suspects.extend(s for s in found if s.suspect)
        if max_fixtures and scanned >= max_fixtures:
            break

    bets.sort(key=lambda s: -s.margin)
    suspects.sort(key=lambda s: -s.margin)
    return SureScan(bets=bets, suspects=suspects, fixtures_scanned=scanned,
                    books_seen=len(all_books))
