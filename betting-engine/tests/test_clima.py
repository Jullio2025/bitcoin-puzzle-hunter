"""Testes da regra de clima e do parser da Open-Meteo — sem rede."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import weather  # noqa: E402
from rules.clima import (CARDS_RAIN, GOALS_RAIN, GOALS_WIND,  # noqa: E402
                         Clima)
from test_pipeline_offline import fake_ctx  # noqa: E402


def wx(**kw):
    base = {"city": "Goiânia", "temp_c": 25.0, "rain_mm": 0.0,
            "rain_prob": 10, "wind_kmh": 8.0, "summary": "25°C, calmo"}
    base.update(kw)
    return base


class TestClimaRule(unittest.TestCase):
    def _apply(self, weather_dict):
        ctx = fake_ctx()
        ctx.weather = weather_dict
        return Clima().apply(ctx)

    def test_no_forecast_is_neutral_and_says_why(self):
        res = self._apply(None)
        self.assertEqual(res.multipliers, {})
        self.assertIn("indisponível", res.explanation)

    def test_normal_conditions_neutral_but_informative(self):
        res = self._apply(wx())
        self.assertEqual(res.multipliers, {})
        self.assertIn("Goiânia", res.explanation)
        self.assertIn("neutro", res.explanation)

    def test_heavy_rain_lowers_goals_raises_cards(self):
        res = self._apply(wx(rain_mm=6.0, rain_prob=90))
        self.assertEqual(res.multipliers["goals_home"], GOALS_RAIN)
        self.assertEqual(res.multipliers["cards"], CARDS_RAIN)
        self.assertIn("chuva forte", res.explanation)

    def test_high_prob_with_some_rain_counts_as_heavy(self):
        res = self._apply(wx(rain_mm=1.5, rain_prob=80))
        self.assertIn("cards", res.multipliers)

    def test_strong_wind_combines_with_rain(self):
        res = self._apply(wx(rain_mm=6.0, rain_prob=90, wind_kmh=40.0))
        self.assertAlmostEqual(res.multipliers["goals_home"],
                               GOALS_RAIN * GOALS_WIND)
        self.assertIn("vento forte", res.explanation)

    def test_light_drizzle_is_neutral(self):
        res = self._apply(wx(rain_mm=0.4, rain_prob=40))
        self.assertEqual(res.multipliers, {})


class TestWeatherParsing(unittest.TestCase):
    def test_pick_hour(self):
        hourly = {"time": ["2026-06-11T19:00", "2026-06-11T20:00"]}
        target = datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(weather._pick_hour(hourly, target), 1)
        target = datetime(2026, 6, 11, 22, 0, tzinfo=timezone.utc)
        self.assertIsNone(weather._pick_hour(hourly, target))

    def test_forecast_for_assembles_summary(self):
        kickoff = (datetime.now(timezone.utc)
                   .replace(minute=0, second=0, microsecond=0))
        hour_key = kickoff.strftime("%Y-%m-%dT%H:00")

        def fake_get(url, params, ttl, cache_key):
            if "geocoding" in url:
                return {"results": [{"latitude": -16.68, "longitude": -49.25}]}
            return {"hourly": {"time": [hour_key],
                               "temperature_2m": [22.5],
                               "precipitation": [5.2],
                               "precipitation_probability": [85],
                               "wind_speed_10m": [12.0]}}

        with patch.object(weather, "_get_json", fake_get):
            w = weather.forecast_for("Goiânia", kickoff.isoformat())
        self.assertEqual(w["rain_mm"], 5.2)
        self.assertIn("22°C", w["summary"])
        self.assertIn("5.2 mm", w["summary"])

    def test_no_city_or_far_future_returns_none(self):
        self.assertIsNone(weather.forecast_for(None, "2026-06-11T20:00:00+00:00"))
        self.assertIsNone(weather.forecast_for("Goiânia",
                                               "2030-01-01T20:00:00+00:00"))

    def test_network_failure_returns_none(self):
        def boom(*a, **k):
            raise RuntimeError("offline")
        kickoff = datetime.now(timezone.utc).isoformat()
        with patch.object(weather, "_get_json", boom):
            self.assertIsNone(weather.forecast_for("Goiânia", kickoff))


if __name__ == "__main__":
    unittest.main()
