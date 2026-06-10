"""Regra: clima (placeholder transparente)."""
from rules import RuleResult, register


@register
class Clima:
    name = "clima"
    description = "Clima no estádio (requer fonte externa de dados)."

    def apply(self, ctx) -> RuleResult:
        # A API-Football não fornece previsão do tempo. A regra existe
        # para ser plugada a uma fonte externa (ex.: Open-Meteo) sem
        # mexer no núcleo; até lá, declara-se neutra explicitamente.
        return RuleResult(
            rule=self.name,
            explanation=("Neutro: sem fonte de clima configurada. "
                         "Plugue uma API de tempo neste módulo para ativar."))
