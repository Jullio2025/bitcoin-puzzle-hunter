"""
Tracker de apostas: registra o que o USUÁRIO decidiu apostar e mede
hit rate e ROI reais (requisito 4).

Armazena em data/bets.json. Abaixo de TRACKER_MIN_SAMPLE apostas
liquidadas, o relatório avisa que a variância domina e que não dá para
concluir nada sobre edge.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import config


@dataclass
class Bet:
    id: int
    fixture: str           # "Time A x Time B (2026-06-11)"
    market: str            # "Cartões: Mais de 4.5"
    odd: float
    stake: float
    p_model: float
    result: str = "pendente"   # "pendente" | "ganhou" | "perdeu" | "devolvida"
    created_at: float = field(default_factory=time.time)


class Tracker:
    def __init__(self, path: Path | None = None):
        self.path = path or config.TRACKER_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.bets: list[Bet] = self._load()

    def _load(self) -> list[Bet]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text())
        return [Bet(**b) for b in raw]

    def _save(self) -> None:
        self.path.write_text(
            json.dumps([asdict(b) for b in self.bets],
                       ensure_ascii=False, indent=2))

    def add(self, fixture: str, market: str, odd: float, stake: float,
            p_model: float) -> Bet:
        bet = Bet(id=(max((b.id for b in self.bets), default=0) + 1),
                  fixture=fixture, market=market, odd=odd, stake=stake,
                  p_model=p_model)
        self.bets.append(bet)
        self._save()
        return bet

    def settle(self, bet_id: int, result: str) -> Bet:
        if result not in ("ganhou", "perdeu", "devolvida"):
            raise ValueError("resultado deve ser: ganhou, perdeu ou devolvida")
        bet = next((b for b in self.bets if b.id == bet_id), None)
        if bet is None:
            raise KeyError(f"aposta #{bet_id} não encontrada")
        bet.result = result
        self._save()
        return bet

    def report(self) -> dict:
        settled = [b for b in self.bets
                   if b.result in ("ganhou", "perdeu")]
        wins = [b for b in settled if b.result == "ganhou"]
        staked = sum(b.stake for b in settled)
        returned = sum(b.stake * b.odd for b in wins)
        profit = returned - staked

        rep = {
            "total_registradas": len(self.bets),
            "pendentes": sum(1 for b in self.bets if b.result == "pendente"),
            "liquidadas": len(settled),
            "acertos": len(wins),
            "hit_rate": (len(wins) / len(settled)) if settled else None,
            "total_apostado": staked,
            "lucro": profit,
            "roi": (profit / staked) if staked > 0 else None,
            # comparação honesta: hit rate previsto pelo modelo vs real
            "p_model_medio": (sum(b.p_model for b in settled) / len(settled))
                             if settled else None,
            "warning": None,
        }
        if len(settled) < config.TRACKER_MIN_SAMPLE:
            rep["warning"] = (
                f"AMOSTRA PEQUENA: {len(settled)} apostas liquidadas "
                f"(< {config.TRACKER_MIN_SAMPLE}). Com poucas apostas a "
                "variância domina o resultado — hit rate e ROI ainda NÃO "
                "permitem concluir se existe edge real.")
        return rep
