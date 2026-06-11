"""
Clima via Open-Meteo (open-meteo.com) — grátis, mundial, sem chave.

Fluxo: cidade do estádio (vem da API-Football) -> geocoding -> previsão
hora a hora -> condições no horário do pontapé inicial. Tudo cacheado em
disco; qualquer falha vira None (a regra de clima fica neutra e avisa).
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

import config
from api_client import DiskCache

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEO_TTL = 30 * 24 * 3600       # cidade não muda de lugar
FORECAST_TTL = 3 * 3600
MAX_FORECAST_DAYS = 16          # janela da Open-Meteo

_cache = DiskCache(config.CACHE_DIR)


def _get_json(url: str, params: dict, ttl: float, cache_key: str):
    cached = _cache.get(cache_key, ttl)
    if cached is not None:
        return cached
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _cache.set(cache_key, data)
    return data


def geocode(city: str) -> tuple[float, float] | None:
    """Cidade -> (lat, lon) via geocoding gratuito da Open-Meteo."""
    data = _get_json(GEO_URL, {"name": city, "count": 1, "language": "pt"},
                     GEO_TTL, f"weather:geo:{city.lower()}")
    results = data.get("results") or []
    if not results:
        return None
    return results[0]["latitude"], results[0]["longitude"]


def _pick_hour(hourly: dict, target_utc: datetime) -> int | None:
    """Índice da hora da previsão que casa com o horário do jogo (UTC)."""
    want = target_utc.strftime("%Y-%m-%dT%H:00")
    times = hourly.get("time") or []
    return times.index(want) if want in times else None


def forecast_for(city: str | None, kickoff_iso: str) -> dict | None:
    """Condições previstas no horário do jogo, ou None se indisponível."""
    if not city:
        return None
    try:
        kickoff = datetime.fromisoformat(kickoff_iso).astimezone(timezone.utc)
        days_ahead = (kickoff - datetime.now(timezone.utc)).days
        if days_ahead < -1 or days_ahead > MAX_FORECAST_DAYS:
            return None
        coords = geocode(city)
        if not coords:
            return None
        lat, lon = coords
        day = kickoff.strftime("%Y-%m-%d")
        data = _get_json(FORECAST_URL, {
            "latitude": lat, "longitude": lon, "timezone": "UTC",
            "start_date": day, "end_date": day,
            "hourly": ("temperature_2m,precipitation,"
                       "precipitation_probability,wind_speed_10m"),
        }, FORECAST_TTL, f"weather:fc:{lat:.2f},{lon:.2f}:{day}")
        hourly = data.get("hourly") or {}
        i = _pick_hour(hourly, kickoff)
        if i is None:
            return None
        w = {
            "city": city,
            "temp_c": hourly["temperature_2m"][i],
            "rain_mm": hourly["precipitation"][i] or 0.0,
            "rain_prob": (hourly.get("precipitation_probability") or
                          [None])[i] or 0,
            "wind_kmh": hourly["wind_speed_10m"][i] or 0.0,
        }
        w["summary"] = (f"{w['temp_c']:.0f}°C, vento {w['wind_kmh']:.0f} "
                        f"km/h, chuva {w['rain_mm']:.1f} mm "
                        f"({w['rain_prob']:.0f}%)")
        return w
    except Exception:
        return None  # clima nunca pode derrubar uma análise
