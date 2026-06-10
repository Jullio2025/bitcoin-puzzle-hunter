"""Regra: tendência de escanteios."""
from rules import RuleResult, register


@register
class Escanteios:
    name = "escanteios"
    description = ("Expõe os componentes do lambda de escanteios "
                   "(a favor e cedidos, por mando).")

    def apply(self, ctx) -> RuleResult:
        return RuleResult(
            rule=self.name,
            explanation=(
                f"{ctx.home.team_name} em casa: {ctx.home.corners_for:.2f} a "
                f"favor / {ctx.home.corners_against:.2f} cedidos por jogo. "
                f"{ctx.away.team_name} fora: {ctx.away.corners_for:.2f} a "
                f"favor / {ctx.away.corners_against:.2f} cedidos. "
                "Lambda base = média cruzada (ataque de um x defesa do outro)."),
        )
