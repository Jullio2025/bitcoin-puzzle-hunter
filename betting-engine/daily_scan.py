"""
Garimpo diário automático: roda o Scanner com os SEUS critérios salvos,
liquida o tracker e manda o resumo no Telegram.

Uso:
  python daily_scan.py            # roda agora (também via timer do sistema)

Pré-requisitos:
  1. Salvar critérios padrão: no painel web, aba Scanner, marque
     "Salvar estes critérios como padrão" ao garimpar.
  2. TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env (veja notify.py).
"""
from __future__ import annotations

import json
from datetime import date

import config
import settle
from api_client import ApiFootballClient
from notify import send_telegram, telegram_configured
from scanner import Criterion, scan_day
from tracker import Tracker

PRESET_FILE = config.DATA_DIR / "scan_preset.json"
MAX_HITS_IN_MESSAGE = 10


def load_preset() -> dict | None:
    if not PRESET_FILE.exists():
        return None
    return json.loads(PRESET_FILE.read_text())


def save_preset(criteria: list[Criterion], bookmaker: int, last_n: int,
                match_all: bool, deep_limit: int) -> None:
    PRESET_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESET_FILE.write_text(json.dumps({
        "criteria": [c.__dict__ for c in criteria],
        "bookmaker": bookmaker, "last_n": last_n,
        "match_all": match_all, "deep_limit": deep_limit,
    }, ensure_ascii=False, indent=2))


def format_message(today: str, result, criteria: list[Criterion],
                   settle_summary: dict, report: dict) -> str:
    lines = [f"⚡ ChapaFut — garimpo de {today}", ""]
    lines += [f"Critérios: {c.label}" for c in criteria]
    lines.append("")

    hits = [(g, crit, ma) for g in result.groups for crit, ma in g.hits]
    if not hits:
        lines.append("Nenhum jogo bateu nos seus critérios hoje.")
    else:
        lines.append(f"{len(hits)} oportunidade(s) "
                     f"em {len(result.groups)} jogo(s):")
        for g, _crit, ma in hits[:MAX_HITS_IN_MESSAGE]:
            mt = ma.metrics
            lines.append(
                f"• {g.ctx.fixture['fixture']['date'][11:16]} {g.ctx.label}\n"
                f"  {ma.label} — odd {mt.odd:.2f} | p {mt.p_model:.0%} | "
                f"EV {mt.ev:+.2f}")
        if len(hits) > MAX_HITS_IN_MESSAGE:
            lines.append(f"... e mais {len(hits) - MAX_HITS_IN_MESSAGE} "
                         "(veja no painel).")
    if result.skipped_by_limit:
        lines.append(f"({result.skipped_by_limit} candidato(s) fora do "
                     "limite de profundidade)")

    lines += ["", f"Tracker: {settle_summary['ganhou']} ganhas, "
                  f"{settle_summary['perdeu']} perdidas, "
                  f"{settle_summary['devolvida']} devolvidas "
                  f"(liquidação automática)."]
    if report["hit_rate"] is not None:
        lines.append(f"Hit rate real: {report['hit_rate']:.0%} | "
                     f"ROI: {report['roi']:+.1%}" if report["roi"] is not None
                     else f"Hit rate real: {report['hit_rate']:.0%}")
    if report["warning"]:
        lines.append("⚠️ " + report["warning"])
    lines += ["", "Os números na mesa — a decisão é sua. (18+)"]
    return "\n".join(lines)


def main() -> None:
    today = date.today().isoformat()
    preset = load_preset()
    if not preset:
        msg = ("⚡ ChapaFut: nenhum critério padrão salvo ainda. Abra o "
               "Scanner no painel e marque 'Salvar estes critérios como "
               "padrão' ao garimpar.")
        print(msg)
        send_telegram(msg)
        return

    client = ApiFootballClient()
    criteria = [Criterion(**c) for c in preset["criteria"]]
    result = scan_day(client, today, criteria,
                      bookmaker=preset.get("bookmaker"),
                      last_n=preset.get("last_n"),
                      match_all=preset.get("match_all", False),
                      deep_limit=preset.get("deep_limit", 20))

    tracker = Tracker()
    settle_summary = settle.auto_settle(tracker, client)
    message = format_message(today, result, criteria, settle_summary,
                             tracker.report())
    print(message)
    if telegram_configured():
        sent = send_telegram(message)
        print(f"\n[telegram: {'enviado' if sent else 'FALHOU'}]")
    else:
        print("\n[telegram não configurado — defina TELEGRAM_BOT_TOKEN e "
              "TELEGRAM_CHAT_ID no .env]")


if __name__ == "__main__":
    main()
