"""Regra: mando de campo (casa/fora)."""
from rules import RuleResult, register


@register
class MandoDeCampo:
    name = "mando_de_campo"
    description = ("Compara o desempenho do mandante em casa com o do "
                   "visitante fora, nos últimos N jogos.")

    def apply(self, ctx) -> RuleResult:
        # Os lambdas base JÁ usam médias separadas por mando (casa/fora),
        # então o efeito principal do mando já está embutido. Esta regra
        # só informa o diferencial de pontos para o usuário enxergar.
        diff = ctx.home.points - ctx.away.points
        max_pts = max(ctx.home.games, ctx.away.games, 1) * 3
        return RuleResult(
            rule=self.name,
            score=diff / max_pts,
            explanation=(
                f"{ctx.home.team_name} em casa: {ctx.home.points} pts "
                f"({ctx.home.form or 'sem jogos'}) | "
                f"{ctx.away.team_name} fora: {ctx.away.points} pts "
                f"({ctx.away.form or 'sem jogos'}). "
                "Efeito do mando já está nos lambdas base (médias separadas "
                "por casa/fora)."),
        )
