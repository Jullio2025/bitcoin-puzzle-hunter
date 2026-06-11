"""Regra: clima no estádio (Open-Meteo, grátis e sem chave).

Limiares e ajustes DOCUMENTADOS (conservadores de propósito):
- chuva forte (>= RAIN_MM mm/h no horário, ou prob >= RAIN_PROB% com
  >= 1 mm): gramado pesado -> gols x0.95, cartões x1.05
- vento forte (>= WIND_KMH km/h): bola aérea imprevisível -> gols x0.96
Fora disso, a regra só informa as condições e fica neutra.
"""
from rules import RuleResult, register

RAIN_MM = 4.0
RAIN_PROB = 70
WIND_KMH = 35.0
GOALS_RAIN = 0.95
CARDS_RAIN = 1.05
GOALS_WIND = 0.96


@register
class Clima:
    name = "clima"
    description = "Previsão no horário do jogo (Open-Meteo) com ajustes leves."

    def apply(self, ctx) -> RuleResult:
        w = ctx.weather
        if not w:
            return RuleResult(
                rule=self.name,
                explanation=("Neutro: previsão indisponível (sem cidade do "
                             "estádio na API ou jogo fora da janela de 16 "
                             "dias da Open-Meteo)."))

        mults: dict[str, float] = {}
        reasons = []
        heavy_rain = (w["rain_mm"] >= RAIN_MM
                      or (w["rain_prob"] >= RAIN_PROB and w["rain_mm"] >= 1))
        if heavy_rain:
            mults["goals_home"] = GOALS_RAIN
            mults["goals_away"] = GOALS_RAIN
            mults["cards"] = CARDS_RAIN
            reasons.append(f"chuva forte: gols x{GOALS_RAIN}, "
                           f"cartões x{CARDS_RAIN}")
        if w["wind_kmh"] >= WIND_KMH:
            for k in ("goals_home", "goals_away"):
                mults[k] = mults.get(k, 1.0) * GOALS_WIND
            reasons.append(f"vento forte: gols x{GOALS_WIND}")

        base = f"Previsão para o horário em {w['city']}: {w['summary']}."
        if not reasons:
            return RuleResult(rule=self.name,
                              explanation=base + " Condições normais — neutro.")
        return RuleResult(rule=self.name, multipliers=mults,
                          score=-0.2 if heavy_rain else -0.1,
                          explanation=base + " Ajustes: " + "; ".join(reasons)
                          + ".")
