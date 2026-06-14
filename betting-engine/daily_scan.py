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
import os
import socket
from datetime import date

import config
import scoring
import settle
import share_store
import users
from api_client import ApiFootballClient
from notify import bot_configured, send_telegram
from scanner import Criterion, scan_day
from tracker import Tracker

MAX_HITS_IN_MESSAGE = 10
TICKET_MAX_LEGS = 10  # bilhete automático: até N pernas (1 por jogo)


def _preset_path(username: str):
    return users.user_dir(username) / "scan_preset.json"


def load_preset(username: str) -> dict | None:
    path = _preset_path(username)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_preset(username: str, criteria: list[Criterion], bookmaker: int,
                last_n: int, match_all: bool, deep_limit: int) -> None:
    _preset_path(username).write_text(json.dumps({
        "criteria": [c.__dict__ for c in criteria],
        "bookmaker": bookmaker, "last_n": last_n,
        "match_all": match_all, "deep_limit": deep_limit,
    }, ensure_ascii=False, indent=2))


def panel_url() -> str:
    """URL pública do painel: PANEL_URL do .env, senão IP detectado:8000."""
    url = os.getenv("PANEL_URL", "").rstrip("/")
    if url:
        return url
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:8000"
    except OSError:
        return "http://SEU_IP:8000"


def build_auto_ticket(result, max_legs: int = TICKET_MAX_LEGS) -> str | None:
    """Monta o bilhete do garimpo e devolve o código do link público.

    Montagem MECÂNICA a partir dos critérios do usuário: 1 perna por jogo
    (a de maior EV — os hits já vêm ordenados) até max_legs, ranqueadas
    por EV. Nenhuma escolha editorial; a decisão continua sendo do usuário.
    """
    legs = []
    for g in result.groups:
        if not g.hits:
            continue
        _crit, ma = g.hits[0]  # melhor EV do jogo
        legs.append({"fixture": g.ctx.label, "market": ma.label,
                     "odd": ma.metrics.odd, "p": ma.metrics.p_model,
                     "ev": ma.metrics.ev or 0})
    if not legs:
        return None
    legs.sort(key=lambda l: -l["ev"])
    legs = legs[:max_legs]
    ticket = scoring.combine_legs([(l["odd"], l["p"]) for l in legs])
    return share_store.create(legs, ticket.combined_odd,
                              ticket.combined_prob, ticket.lose_prob)


def format_message(today: str, result, criteria: list[Criterion],
                   settle_summary: dict, report: dict,
                   ticket_code: str | None = None) -> str:
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

    if ticket_code:
        t = share_store.get(ticket_code)
        lines += ["",
                  f"🎫 Bilhete montado com o garimpo "
                  f"({len(t['legs'])} perna(s), 1 por jogo, maior EV):",
                  f"   Odd combinada {t['combined_odd']:.2f} | "
                  f"prob. {t['combined_prob']:.0%} | "
                  f"chance de PERDER {t['lose_prob']:.0%}",
                  f"   Ver/compartilhar: {panel_url()}/b/{ticket_code}"]

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


def run_for_user(client: ApiFootballClient, username: str,
                 today: str) -> str:
    """Roda o garimpo + liquidação de UM usuário e devolve a mensagem."""
    u = users.get_user(username) or {}
    chat_id = u.get("telegram_chat_id", "")
    tracker = Tracker(users.user_dir(username) / "bets.json")
    preset = load_preset(username)

    if not preset:
        # ainda liquida o tracker do usuário, mesmo sem preset
        settle.auto_settle(tracker, client)
        msg = (f"⚡ ChapaFut ({username}): você ainda não salvou critérios "
               "padrão. Abra o Scanner e marque 'Salvar como padrão'.")
    else:
        criteria = [Criterion(**c) for c in preset["criteria"]]
        result = scan_day(client, today, criteria,
                          bookmaker=preset.get("bookmaker"),
                          last_n=preset.get("last_n"),
                          match_all=preset.get("match_all", False),
                          deep_limit=preset.get("deep_limit", 20))
        settle_summary = settle.auto_settle(tracker, client)
        ticket_code = build_auto_ticket(
            result, max_legs=preset.get("ticket_max_legs", TICKET_MAX_LEGS))
        msg = format_message(today, result, criteria, settle_summary,
                             tracker.report(), ticket_code=ticket_code)
    print(f"\n===== {username} =====\n{msg}")
    if chat_id and bot_configured():
        send_telegram(msg, chat_id=chat_id)
    return msg


def main(force: bool = False) -> None:
    """Roda o garimpo. Por padrão, só dos usuários cuja hora escolhida
    bate com a hora atual do servidor (o timer chama de hora em hora).
    force=True processa todos (para teste manual)."""
    import sys
    from datetime import datetime
    force = force or any(a in ("force", "all", "--force") for a in sys.argv[1:])
    today = date.today().isoformat()
    now_hour = datetime.now().hour
    client = ApiFootballClient()
    usernames = users.all_usernames()
    if not usernames:
        print("Nenhum usuário cadastrado.")
        return
    if not bot_configured():
        print("[aviso] TELEGRAM_BOT_TOKEN ausente no .env — rodando sem "
              "enviar mensagens.")

    due = [u for u in usernames
           if force or (users.get_user(u) or {}).get("send_hour", 9) == now_hour]
    if not due:
        print(f"[{now_hour:02d}h] nenhum usuário agendado para esta hora.")
        return
    for username in due:
        try:
            run_for_user(client, username, today)
        except Exception as e:  # um usuário com erro não derruba os demais
            print(f"[erro em {username}] {type(e).__name__}: {e}")
    print(f"\n[concluído: {len(due)} usuário(s) nesta hora | "
          f"{client.calls_made} chamadas à API no total]")


if __name__ == "__main__":
    main()
