"""Regra: importância do jogo (final, decisão, clássico)."""
from rules import RuleResult, register

# Ajustes documentados para jogo decisivo: histórico de finais mostra
# mais cartões (tensão) e menos gols (cautela). Valores conservadores.
CARDS_MULT = 1.10
GOALS_MULT = 0.95


@register
class Importancia:
    name = "importancia"
    description = ("Se o usuário marcar o jogo como decisivo (flag 'final'), "
                   "ajusta cartões para cima e gols para baixo.")

    def apply(self, ctx) -> RuleResult:
        round_name = ctx.fixture.get("league", {}).get("round", "")
        flagged = bool(ctx.user_flags.get("final"))
        looks_final = "final" in round_name.lower()
        if not (flagged or looks_final):
            return RuleResult(
                rule=self.name,
                explanation=(f"Neutro: jogo não marcado como decisivo "
                             f"(rodada: '{round_name or '?'}')."))
        origin = "marcado pelo usuário" if flagged else f"rodada '{round_name}'"
        return RuleResult(
            rule=self.name,
            multipliers={"cards": CARDS_MULT,
                         "goals_home": GOALS_MULT, "goals_away": GOALS_MULT},
            explanation=(f"Jogo decisivo ({origin}): cartões x{CARDS_MULT}, "
                         f"gols x{GOALS_MULT}."))
