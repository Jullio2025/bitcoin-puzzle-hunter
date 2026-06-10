"""
Registrador central de regras plugáveis.

Cada parâmetro de análise é um módulo independente nesta pasta.
Para criar uma regra nova: copie um módulo existente, decore a classe
com @register e pronto — o núcleo do motor não precisa mudar.

Contrato de uma regra:
- atributos `name` (id) e `description`;
- método `apply(ctx: MatchContext) -> RuleResult`;
- RuleResult.multipliers ajusta os lambdas base por mercado
  ("goals_home", "goals_away", "cards", "corners"); 1.0 ou ausente = neutro;
- RuleResult.explanation SEMPRE diz de onde saiu o número (transparência),
  inclusive quando a regra fica neutra por falta de dados.
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field

_REGISTRY: dict[str, type] = {}
_LOADED = False


@dataclass
class RuleResult:
    rule: str
    multipliers: dict[str, float] = field(default_factory=dict)
    score: float = 0.0          # sinal informativo (-1 a +1, livre)
    explanation: str = ""


def register(cls):
    """Decorator: registra a classe de regra pelo seu atributo `name`."""
    _REGISTRY[cls.name] = cls
    return cls


def _load_all():
    global _LOADED
    if _LOADED:
        return
    pkg = importlib.import_module(__name__)
    for mod in pkgutil.iter_modules(pkg.__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")
    _LOADED = True


def all_rule_names() -> list[str]:
    _load_all()
    return sorted(_REGISTRY)


def get_rules(enabled: list[str] | None = None) -> list:
    """Instancia as regras. `enabled=None` => todas as registradas."""
    _load_all()
    names = enabled if enabled is not None else sorted(_REGISTRY)
    unknown = [n for n in names if n not in _REGISTRY]
    if unknown:
        raise KeyError(f"Regras desconhecidas: {unknown}. "
                       f"Disponíveis: {sorted(_REGISTRY)}")
    return [_REGISTRY[n]() for n in names]
