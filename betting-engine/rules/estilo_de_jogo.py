"""Regra: estilo de jogo (proxy: volume de chutes a gol)."""
from rules import RuleResult, register

# Chutes a gol esperados por time num jogo "médio" (referência documentada)
BASELINE_SHOTS = 4.5
SENSITIVITY = 0.5  # 50% do desvio relativo de chutes vira ajuste de gols


@register
class EstiloDeJogo:
    name = "estilo_de_jogo"
    description = ("Usa a média de chutes a gol como proxy de agressividade "
                   "ofensiva e ajusta levemente o lambda de gols.")

    def apply(self, ctx) -> RuleResult:
        mult = {}
        notes = []
        for side, stats in (("goals_home", ctx.home), ("goals_away", ctx.away)):
            if stats.shots_on_goal <= 0:
                notes.append(f"{stats.team_name}: sem dado de chutes — neutro.")
                continue
            ratio = stats.shots_on_goal / BASELINE_SHOTS
            m = 1.0 + SENSITIVITY * (ratio - 1.0)
            mult[side] = m
            notes.append(
                f"{stats.team_name}: {stats.shots_on_goal:.2f} chutes a "
                f"gol/jogo vs baseline {BASELINE_SHOTS} => multiplicador "
                f"{m:.3f}.")
        return RuleResult(rule=self.name, multipliers=mult,
                          explanation=" | ".join(notes))
