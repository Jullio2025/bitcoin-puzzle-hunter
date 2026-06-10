"""Regra: cartões do time (dados e provocados nos jogos dele)."""
from rules import RuleResult, register


@register
class CartoesTime:
    name = "cartoes_time"
    description = ("Mostra a média de cartões recebidos por cada time e "
                   "pelos adversários nos jogos deles (já é a base do lambda).")

    def apply(self, ctx) -> RuleResult:
        # O lambda base de cartões já soma as médias recebidas dos dois
        # times; esta regra expõe os componentes para auditoria.
        return RuleResult(
            rule=self.name,
            explanation=(
                f"{ctx.home.team_name} em casa: recebe {ctx.home.cards:.2f} "
                f"cartões/jogo (adversários recebem "
                f"{ctx.home.cards_opponent:.2f}). "
                f"{ctx.away.team_name} fora: recebe {ctx.away.cards:.2f} "
                f"(adversários {ctx.away.cards_opponent:.2f}). "
                "Lambda base de cartões = soma das médias recebidas."),
        )
