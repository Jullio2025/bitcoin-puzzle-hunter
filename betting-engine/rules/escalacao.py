"""Regra: escalação (titulares confirmados / desfalques)."""
from rules import RuleResult, register


@register
class Escalacao:
    name = "escalacao"
    description = ("Informa se as escalações já foram confirmadas na API "
                   "(saem ~1h antes do jogo).")

    def apply(self, ctx) -> RuleResult:
        if not ctx.lineups:
            return RuleResult(
                rule=self.name,
                explanation=("Escalações ainda não disponíveis (a API "
                             "publica ~1h antes). Neutro — reanalise perto "
                             "do jogo para confirmar titulares."))
        teams = []
        for lu in ctx.lineups:
            n = len(lu.get("startXI", []) or [])
            teams.append(f"{lu.get('team', {}).get('name')}: {n} titulares "
                         f"confirmados ({lu.get('formation') or '?'})")
        return RuleResult(
            rule=self.name,
            explanation=("Escalações confirmadas — " + " | ".join(teams) +
                         ". Avalie desfalques manualmente; a regra não "
                         "altera lambdas automaticamente."))
