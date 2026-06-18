"""
Cliente da API-Football (api-sports.io) com cache em disco e rate limit.

Arquitetura de provedor de dados: tudo que o motor consome passa pelos
métodos públicos de ApiFootballClient. Para plugar outra fonte no futuro
(ex.: SofaScore), basta criar outra classe com os mesmos métodos
(mesma "interface") e injetá-la no lugar — o resto do motor não muda.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import requests

import config


class ApiError(RuntimeError):
    """Erro retornado pela API (chave inválida, rate limit, parâmetro etc.)."""


class DiskCache:
    """Cache simples em disco: 1 arquivo JSON por chave, com TTL."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.md5(key.encode()).hexdigest()
        return self.dir / f"{digest}.json"

    def get(self, key: str, ttl: float) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if time.time() - payload.get("saved_at", 0) > ttl:
            return None
        return payload.get("data")

    def set(self, key: str, data: Any) -> None:
        payload = {"saved_at": time.time(), "key": key, "data": data}
        self._path(key).write_text(json.dumps(payload, ensure_ascii=False))


class ApiFootballClient:
    def __init__(self, api_key: str | None = None,
                 pause: float | None = None,
                 cache_dir: Path | None = None):
        self.api_key = api_key or config.APISPORTS_KEY
        if not self.api_key:
            raise ApiError(
                "APISPORTS_KEY não configurada. Copie .env.example para .env "
                "e preencha sua chave."
            )
        self.pause = config.REQUEST_PAUSE if pause is None else pause
        self.cache = DiskCache(cache_dir or config.CACHE_DIR)
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": self.api_key})
        self._last_call = 0.0
        self.calls_made = 0

    # ------------------------------------------------------------------ http
    def _get(self, path: str, params: dict | None = None,
             ttl: float = 0, cache_suffix: str = "") -> list:
        """GET com cache; retorna o campo `response` da API.

        `cache_suffix` separa o cache sem mudar a chamada à API — usado p/
        dados "ao vivo" não colidirem com o cache de longa duração (ex.:
        estatísticas finais usadas na liquidação)."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = path + "?" + json.dumps(params, sort_keys=True) + cache_suffix
        if ttl:
            cached = self.cache.get(key, ttl)
            if cached is not None:
                return cached

        # respeita o rate limit: pausa mínima entre chamadas reais
        wait = self.pause - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)

        resp = self.session.get(f"{config.API_BASE_URL}/{path}",
                                params=params, timeout=30)
        self._last_call = time.time()
        self.calls_made += 1

        if resp.status_code == 429:
            raise ApiError("Rate limit atingido (HTTP 429). "
                           "Aumente REQUEST_PAUSE no .env.")
        resp.raise_for_status()
        body = resp.json()
        errors = body.get("errors")
        if errors and (not isinstance(errors, list) or len(errors) > 0):
            raise ApiError(f"Erro da API em /{path}: {errors}")

        data = body.get("response", [])
        if ttl:
            self.cache.set(key, data)
        return data

    # ------------------------------------------------------------- endpoints
    def leagues(self) -> list:
        return self._get("leagues", ttl=config.CACHE_TTL["leagues"])

    def leagues_with_stats_coverage(self) -> list[dict]:
        """Ligas/temporadas com coverage.fixtures.statistics_fixtures = true.

        Sem essa cobertura, cartões/escanteios vêm vazios — requisito 1.
        Retorna [{league_id, name, country, season}] da temporada atual coberta.
        """
        out = []
        for item in self.leagues():
            league = item.get("league", {})
            country = item.get("country", {})
            for season in item.get("seasons", []):
                cov = season.get("coverage", {}).get("fixtures", {})
                if season.get("current") and cov.get("statistics_fixtures"):
                    out.append({
                        "league_id": league.get("id"),
                        "name": league.get("name"),
                        "country": country.get("name"),
                        "season": season.get("year"),
                    })
        return out

    def fixtures_by_date(self, date: str, league_id: int | None = None,
                         season: int | None = None) -> list:
        """Jogos de uma data (YYYY-MM-DD), opcionalmente filtrados por liga."""
        return self._get(
            "fixtures",
            {"date": date, "league": league_id, "season": season},
            ttl=config.CACHE_TTL["fixtures_day"],
        )

    def fixture_by_id(self, fixture_id: int) -> dict | None:
        rows = self._get("fixtures", {"id": fixture_id},
                         ttl=config.CACHE_TTL["fixtures_day"])
        return rows[0] if rows else None

    def live_fixtures(self) -> list:
        """TODOS os jogos em andamento no mundo, numa única chamada.

        Cache de 60s (config) — a tela ao vivo atualiza a cada minuto lendo
        do cache, então o custo é ~1 chamada/min independente de quantos
        usuários estejam olhando."""
        return self._get("fixtures", {"live": "all"},
                         ttl=config.CACHE_TTL["live"])

    def fixture_events(self, fixture_id: int) -> list:
        """Timeline do jogo (gols, cartões, substituições). Cache de 60s.

        Buscado SOB DEMANDA (quando o usuário expande um jogo ao vivo),
        então o custo é proporcional ao que é realmente aberto."""
        return self._get("fixtures/events", {"fixture": fixture_id},
                         ttl=config.CACHE_TTL["live"])

    def fixture_statistics_live(self, fixture_id: int) -> list:
        """Estatísticas AO VIVO do jogo (chutes, posse, etc.), cache 60s.

        Cache SEPARADO (cache_suffix) do fixture_statistics de longa duração
        usado na liquidação — para não corromper a conferência de resultado."""
        return self._get("fixtures/statistics", {"fixture": fixture_id},
                         ttl=config.CACHE_TTL["live"], cache_suffix="|live")

    def team_last_fixtures(self, team_id: int, last: int = 40) -> list:
        """Últimos jogos encerrados de um time (casa e fora misturados).

        A API não filtra por mando; busca-se um lote maior e o features.py
        separa casa/fora — busca sempre separada por time, como pedido.
        """
        return self._get(
            "fixtures",
            {"team": team_id, "last": last, "status": "FT-AET-PEN"},
            ttl=config.CACHE_TTL["team_fixtures"],
        )

    def league_fixtures(self, league_id: int, season: int) -> list:
        """Todos os jogos de uma liga/temporada (usado p/ varrer árbitro)."""
        return self._get(
            "fixtures", {"league": league_id, "season": season},
            ttl=config.CACHE_TTL["league_fixtures"],
        )

    def fixture_statistics(self, fixture_id: int) -> list:
        """Estatísticas (cartões, escanteios, chutes) de um jogo encerrado."""
        return self._get(
            "fixtures/statistics", {"fixture": fixture_id},
            ttl=config.CACHE_TTL["fixture_stats"],
        )

    def fixture_lineups(self, fixture_id: int) -> list:
        return self._get("fixtures/lineups", {"fixture": fixture_id},
                         ttl=config.CACHE_TTL["lineups"])

    def bookmakers(self) -> list:
        """Casas de apostas disponíveis na API (id + nome)."""
        return self._get("odds/bookmakers", ttl=config.CACHE_TTL["leagues"])

    def odds(self, fixture_id: int, bookmaker_id: int | None = None) -> list:
        return self._get(
            "odds",
            {"fixture": fixture_id,
             "bookmaker": bookmaker_id or config.DEFAULT_BOOKMAKER_ID},
            ttl=config.CACHE_TTL["odds"],
        )

    def odds_by_date(self, date: str, bookmaker_id: int | None = None,
                     max_pages: int = 30) -> list:
        """Odds de TODOS os jogos de uma data (paginado) — base do scanner.

        Muito mais barato que 1 chamada por jogo: cada página traz dezenas
        de jogos. Para quando vier página vazia ou atingir max_pages.
        """
        out: list = []
        for page in range(1, max_pages + 1):
            rows = self._get(
                "odds",
                {"date": date,
                 "bookmaker": bookmaker_id or config.DEFAULT_BOOKMAKER_ID,
                 "page": page},
                ttl=config.CACHE_TTL["odds"],
            )
            if not rows:
                break
            out.extend(rows)
        return out
