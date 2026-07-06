"""Regra: tendência de cartões do árbitro."""
import config
from rules import RuleResult, register


@register
class ArbitroCartoes:
    name = "arbitro_cartoes"
    description = ("Ajusta o lambda de cartões pela média do árbitro vs "
                   "baseline da liga.")

    def apply(self, ctx) -> RuleResult:
        ref = ctx.referee
        if ref.cards_avg is None:
            return RuleResult(rule=self.name,
                              explanation=f"Neutro: {ref.note}")
        baseline = config.MODEL["referee_baseline_cards"]
        weight = config.MODEL["referee_weight"]
        ratio = ref.cards_avg / baseline
        # mistura ponderada: weight=0 ignora árbitro, weight=1 usa só ele
        mult = (1 - weight) + weight * ratio
        return RuleResult(
            rule=self.name,
            multipliers={"cards": mult},
            score=ratio - 1.0,
            explanation=(
                f"Árbitro {ref.name}: {ref.cards_avg:.2f} cartões/jogo em "
                f"{ref.games} jogos vs baseline {baseline}. Multiplicador = "
                f"(1-{weight}) + {weight} x {ratio:.3f} = {mult:.3f}."),
        )
