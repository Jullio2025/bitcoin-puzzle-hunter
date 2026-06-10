"""Regra: forma nas últimas N partidas de cada time (no mando do jogo)."""
from rules import RuleResult, register

# Sensibilidade do ajuste: cada ponto acima/abaixo da média (1.5 pt/jogo)
# muda o lambda de gols do time em 2%. Documentado para ser auditável.
SENSITIVITY = 0.02


@register
class Forma:
    name = "forma"
    description = "Ajusta o lambda de gols pela forma recente (pontos nos últimos N)."

    def apply(self, ctx) -> RuleResult:
        mult = {}
        notes = []
        for side, stats in (("goals_home", ctx.home), ("goals_away", ctx.away)):
            if stats.games == 0:
                notes.append(f"{stats.team_name}: sem jogos no recorte — neutro.")
                continue
            avg_expected = 1.5 * stats.games  # média neutra: 1.5 pt/jogo
            delta_pts = stats.points - avg_expected
            m = 1.0 + SENSITIVITY * delta_pts
            mult[side] = m
            notes.append(
                f"{stats.team_name} ({stats.venue}): {stats.points} pts em "
                f"{stats.games} jogos ({stats.form}) => multiplicador "
                f"{m:.3f} (1 + {SENSITIVITY} x {delta_pts:+.1f} pts vs média).")
        return RuleResult(rule=self.name, multipliers=mult,
                          explanation=" | ".join(notes))
