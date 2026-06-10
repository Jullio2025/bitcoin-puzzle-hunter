"""
Extração de features por time e por árbitro, com cache em disco.

Diretriz do usuário: a busca é SEMPRE separada por time, e separada
por mando — últimos N jogos EM CASA e últimos N jogos FORA de cada time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

import config
from api_client import ApiFootballClient

# nomes dos tipos no /fixtures/statistics da API-Football
STAT_CORNERS = "Corner Kicks"
STAT_YELLOW = "Yellow Cards"
STAT_RED = "Red Cards"
STAT_SHOTS_ON_GOAL = "Shots on Goal"


@dataclass
class SideStats:
    """Médias de um time em UM mando (casa OU fora), últimos N jogos."""
    team_id: int
    team_name: str
    venue: str                    # "casa" | "fora"
    games: int = 0
    goals_for: float = 0.0        # média de gols marcados
    goals_against: float = 0.0    # média de gols sofridos
    corners_for: float = 0.0      # média de escanteios a favor
    corners_against: float = 0.0  # média de escanteios cedidos
    cards: float = 0.0            # média de cartões RECEBIDOS pelo time
    cards_opponent: float = 0.0   # média de cartões do adversário no jogo
    shots_on_goal: float = 0.0    # média de chutes a gol
    form: str = ""                # ex.: "VVEDV" (mais recente primeiro)
    points: int = 0               # pontos nos últimos N (V=3, E=1)
    missing_stats: int = 0        # jogos sem estatísticas disponíveis
    fixtures_used: list[int] = field(default_factory=list)


def _stat_value(stats_block: dict, stat_type: str) -> float | None:
    for item in stats_block.get("statistics", []):
        if item.get("type") == stat_type:
            v = item.get("value")
            if v is None:
                return None
            if isinstance(v, str):
                v = v.replace("%", "")
            try:
                return float(v)
            except ValueError:
                return None
    return None


def _cards_total(stats_block: dict) -> float | None:
    yellow = _stat_value(stats_block, STAT_YELLOW)
    red = _stat_value(stats_block, STAT_RED)
    if yellow is None and red is None:
        return None
    return (yellow or 0.0) + (red or 0.0) * config.MODEL["red_card_weight"]


def team_side_stats(client: ApiFootballClient, team_id: int, team_name: str,
                    venue: str, last_n: int) -> SideStats:
    """Médias do time no mando indicado nos últimos `last_n` jogos.

    Busca um lote de jogos encerrados do time, filtra pelo mando e busca
    as estatísticas de cada jogo (cacheadas — jogo encerrado não muda).
    """
    assert venue in ("casa", "fora")
    fixtures = client.team_last_fixtures(team_id, last=max(40, last_n * 6))

    selected = []
    for fx in fixtures:
        home = fx["teams"]["home"]["id"] == team_id
        if (venue == "casa") == home:
            selected.append((fx, home))
        if len(selected) >= last_n:
            break

    s = SideStats(team_id=team_id, team_name=team_name, venue=venue,
                  games=len(selected))
    gf, ga, cf, ca, my_cards, opp_cards, shots = [], [], [], [], [], [], []
    form_chars = []

    for fx, home in selected:
        goals = fx.get("goals", {})
        mine = goals.get("home" if home else "away") or 0
        theirs = goals.get("away" if home else "home") or 0
        gf.append(mine)
        ga.append(theirs)
        form_chars.append("V" if mine > theirs else ("E" if mine == theirs else "D"))
        s.fixtures_used.append(fx["fixture"]["id"])

        blocks = client.fixture_statistics(fx["fixture"]["id"])
        my_block = next((b for b in blocks
                         if b.get("team", {}).get("id") == team_id), None)
        opp_block = next((b for b in blocks
                          if b.get("team", {}).get("id") != team_id), None)
        if not my_block or not opp_block:
            s.missing_stats += 1
            continue
        for src, dest in ((_stat_value(my_block, STAT_CORNERS), cf),
                          (_stat_value(opp_block, STAT_CORNERS), ca),
                          (_cards_total(my_block), my_cards),
                          (_cards_total(opp_block), opp_cards),
                          (_stat_value(my_block, STAT_SHOTS_ON_GOAL), shots)):
            if src is not None:
                dest.append(src)

    s.goals_for = mean(gf) if gf else 0.0
    s.goals_against = mean(ga) if ga else 0.0
    s.corners_for = mean(cf) if cf else 0.0
    s.corners_against = mean(ca) if ca else 0.0
    s.cards = mean(my_cards) if my_cards else 0.0
    s.cards_opponent = mean(opp_cards) if opp_cards else 0.0
    s.shots_on_goal = mean(shots) if shots else 0.0
    s.form = "".join(form_chars)
    s.points = sum(3 if c == "V" else (1 if c == "E" else 0)
                   for c in form_chars)
    return s


@dataclass
class RefereeStats:
    name: str
    games: int = 0
    cards_avg: float | None = None  # média de cartões TOTAIS por jogo
    note: str = ""


def referee_stats(client: ApiFootballClient, referee_name: str | None,
                  league_id: int, season: int,
                  max_games: int | None = None) -> RefereeStats:
    """Média de cartões por jogo do árbitro.

    A API não tem agregado por árbitro (requisito 3): varre os jogos da
    liga/temporada, filtra pelo nome do árbitro e soma os cartões de cada
    jogo via /fixtures/statistics — tudo cacheado em disco.
    """
    max_games = max_games or config.USER_DEFAULTS["referee_games"]
    if not referee_name:
        return RefereeStats(name="?", note="Árbitro ainda não definido.")

    # nome na API costuma vir como "F. Sobrenome, País" — compara o miolo
    needle = referee_name.split(",")[0].strip().lower()
    fixtures = client.league_fixtures(league_id, season)
    matches = [fx for fx in fixtures
               if fx["fixture"].get("referee")
               and needle in fx["fixture"]["referee"].lower()
               and fx["fixture"]["status"]["short"] in ("FT", "AET", "PEN")]
    matches.sort(key=lambda fx: fx["fixture"]["date"], reverse=True)
    matches = matches[:max_games]

    totals = []
    for fx in matches:
        blocks = client.fixture_statistics(fx["fixture"]["id"])
        per_team = [_cards_total(b) for b in blocks]
        per_team = [v for v in per_team if v is not None]
        if per_team:
            totals.append(sum(per_team))

    r = RefereeStats(name=referee_name, games=len(totals))
    if totals:
        r.cards_avg = mean(totals)
        r.note = (f"Média de {r.cards_avg:.2f} cartões/jogo em {r.games} "
                  f"jogos apitados nesta liga/temporada.")
    else:
        r.note = ("Sem histórico do árbitro nesta liga/temporada — "
                  "regra do árbitro fica neutra.")
    return r


@dataclass
class MatchContext:
    """Tudo que as regras e o recommender precisam sobre um jogo."""
    fixture: dict
    home: SideStats        # time da casa, médias EM CASA
    away: SideStats        # visitante, médias FORA
    referee: RefereeStats
    league_id: int
    season: int
    user_flags: dict = field(default_factory=dict)  # ex.: {"final": True}
    lineups: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return (f"{self.fixture['teams']['home']['name']} x "
                f"{self.fixture['teams']['away']['name']}")


def build_match_context(client: ApiFootballClient, fixture: dict,
                        last_n: int | None = None,
                        user_flags: dict | None = None) -> MatchContext:
    last_n = last_n or config.USER_DEFAULTS["last_n"]
    home_team = fixture["teams"]["home"]
    away_team = fixture["teams"]["away"]
    league_id = fixture["league"]["id"]
    season = fixture["league"]["season"]

    home = team_side_stats(client, home_team["id"], home_team["name"],
                           "casa", last_n)
    away = team_side_stats(client, away_team["id"], away_team["name"],
                           "fora", last_n)
    ref = referee_stats(client, fixture["fixture"].get("referee"),
                        league_id, season)
    try:
        lineups = client.fixture_lineups(fixture["fixture"]["id"])
    except Exception:
        lineups = []
    return MatchContext(fixture=fixture, home=home, away=away, referee=ref,
                        league_id=league_id, season=season,
                        user_flags=user_flags or {}, lineups=lineups)
