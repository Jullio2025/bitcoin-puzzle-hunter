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


def clear_preset(username: str) -> None:
    path = _preset_path(username)
    if path.exists():
        path.unlink()


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


def build_auto_ticket(result, max_legs: int = TICKET_MAX_LEGS,
                      owner: str | None = None) -> str | None:
    """Monta o bilhete do garimpo e devolve o código do link público.

    Montagem MECÂNICA a partir dos critérios do usuário: 1 perna por jogo
    (a de maior EV — os hits já vêm ordenados) até max_legs, ranqueadas
    por EV. Guarda as pernas ESTRUTURADAS p/ conferência automática.
    """
    legs = []
    for g in result.groups:
        if not g.hits:
            continue
        _crit, ma = g.hits[0]  # melhor EV do jogo
        if not ma.metrics.odd:
            continue
        legs.append({"fixture": g.ctx.label, "market": ma.label,
                     "odd": ma.metrics.odd, "p": ma.metrics.p_model,
                     "ev": ma.metrics.ev or 0,
                     "fid": g.ctx.fixture["fixture"]["id"], "mkt": ma.market,
                     "side": ma.side, "line": ma.line,
                     "hl": g.ctx.fixture["teams"]["home"].get("logo"),
                     "al": g.ctx.fixture["teams"]["away"].get("logo")})
    if not legs:
        return None
    legs.sort(key=lambda l: -l["ev"])
    legs = legs[:max_legs]
    ticket = scoring.combine_legs([(l["odd"], l["p"]) for l in legs])
    return share_store.create(legs, ticket.combined_odd,
                              ticket.combined_prob, ticket.lose_prob,
                              owner=owner, source="bot")


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
            result, max_legs=preset.get("ticket_max_legs", TICKET_MAX_LEGS),
            owner=username)
        msg = format_message(today, result, criteria, settle_summary,
                             tracker.report(), ticket_code=ticket_code)
    print(f"\n===== {username} =====\n{msg}")
    if chat_id and bot_configured():
        send_telegram(msg, chat_id=chat_id)
    return msg


_CARD_STATUS_PT = {"ganhou": "✅ GANHOU", "perdeu": "❌ PERDEU",
                   "devolvida": "↩️ DEVOLVIDO",
                   "conferir": "❔ CONFERIR (sem estatística de alguma perna)"}


def refresh_card_details(client: ApiFootballClient, username: str,
                         ttl: int | None = None) -> None:
    """Atualiza SÓ o detalhe por perna (✅/❌/⏳) dos cartões pendentes.

    Serve para a tela mostrar os resultados parciais assim que cada jogo
    termina, sem esperar o cartão inteiro fechar. NÃO altera o status nem
    envia Telegram — isso continua a cargo de check_cards_for_user (job de
    hora em hora). Pernas já decididas (jogo encerrado) não são reconsultadas.
    """
    import share_store
    for card in share_store.list_for_user(username):
        if card.get("status") not in (None, "pendente"):
            continue
        legs = card.get("legs", [])
        prev = card.get("detail") or []
        detail, changed = [], False
        for i, leg in enumerate(legs):
            old = prev[i] if i < len(prev) else None
            if old and old.get("outcome") in ("won", "lost", "push"):
                detail.append(old)  # jogo encerrado não muda — reaproveita
                continue
            if not leg.get("fid") or not leg.get("mkt"):
                detail.append({"label": leg.get("market", "?"), "icon": "❔"})
                continue
            try:
                o = settle.leg_outcome(
                    client, {"fid": leg["fid"], "market": leg["mkt"],
                             "side": leg["side"], "line": leg.get("line")},
                    ttl=ttl)
            except Exception:
                o = "pending"
            detail.append({"label": leg.get("market", leg["mkt"]),
                           "icon": settle._OUTCOME_PT.get(o, "❔"),
                           "outcome": o})
            if not old or old.get("outcome") != o:
                changed = True
        if changed:
            share_store.update(card["code"], detail=detail)


def check_cards_for_user(client: ApiFootballClient, username: str) -> int:
    """Confere os cartões pendentes do usuário cujos jogos já terminaram e
    manda o resultado no Telegram assim que fecham. Retorna nº notificados."""
    import share_store
    u = users.get_user(username) or {}
    chat_id = u.get("telegram_chat_id", "")
    notified = 0
    for card in share_store.list_for_user(username):
        if card.get("notified") or card.get("status") not in (None, "pendente"):
            continue
        status, detail = settle.settle_card(client, card)
        # Salva detalhe parcial mesmo quando pendente: pernas já encerradas
        # ficam visíveis (✅/❌/⏳) sem esperar o cartão inteiro fechar.
        if detail and detail != card.get("detail"):
            share_store.update(card["code"], detail=detail)
        if status == "pendente":
            continue  # ainda tem jogo rolando
        share_store.update(card["code"], status=status, detail=detail,
                           notified=True)
        if chat_id and bot_configured():
            wins = sum(1 for d in detail if d.get("outcome") == "won")
            lines = [f"🎫 Seu cartão de {card['created']} — "
                     f"{_CARD_STATUS_PT.get(status, status)}",
                     f"(acertou {wins} de {len(detail)})", ""]
            for d, leg in zip(detail, card["legs"]):
                lines.append(f"{d['icon']} {leg['fixture']} — {leg['market']}")
            pl = share_store.card_pl({**card, "status": status})
            if pl["settled"] and pl["pl"] is not None:
                if pl["pl"] > 0:
                    lines += ["", f"💰 Aposta R$ {pl['stake']:.2f} → "
                              f"retorno R$ {pl['returned']:.2f} "
                              f"(lucro R$ {pl['pl']:.2f})"]
                elif pl["pl"] < 0:
                    lines += ["", f"💸 Aposta R$ {pl['stake']:.2f} → "
                              f"prejuízo R$ {-pl['pl']:.2f}"]
                else:
                    lines += ["", f"↩️ Aposta R$ {pl['stake']:.2f} devolvida "
                              f"(R$ 0,00)"]
            lines += ["", "Os números na mesa — a decisão é sua. (18+)"]
            send_telegram("\n".join(lines), chat_id=chat_id)
            notified += 1
    return notified


# ---------------------------------------------------------- alertas de surebet
SUREBET_SENT_FILE = config.DATA_DIR / "surebet_sent.json"


def _load_sent() -> dict:
    if SUREBET_SENT_FILE.exists():
        return json.loads(SUREBET_SENT_FILE.read_text())
    return {}


def _save_sent(data: dict) -> None:
    SUREBET_SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SUREBET_SENT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def _surebet_sig(sb, today: str) -> str:
    """Assinatura de uma oportunidade (1 alerta por jogo+mercado+linha/dia)."""
    return f"{today}|{sb.fid}|{sb.market}|{sb.line}"


def _format_surebets(bets: list, today: str) -> str:
    lines = [f"⚖️ Surebets de hoje ({len(bets)} nova(s)):", ""]
    for sb in bets:
        line_txt = f" {sb.line:g}" if sb.line is not None else ""
        lines.append(f"🟢 {sb.fixture} — {sb.market_label}{line_txt}")
        lines.append(f"   lucro travado +{sb.margin * 100:.2f}% "
                     f"(soma {sb.sum_implied * 100:.1f}%)")
        for leg in sb.legs:
            lines.append(f"   • {leg.label}: {leg.odd:.2f} na {leg.book}")
        lines.append("")
    lines.append(f"Divisão do valor e detalhes: {panel_url()}/surebet")
    lines.append("⚠️ Confirme a odd na casa antes de apostar — elas mudam "
                 "rápido. Cada um aposta na própria conta. 18+")
    return "\n".join(lines)


def surebet_alerts_run(client: ApiFootballClient, today: str) -> int:
    """Escaneia surebets do dia e avisa no Telegram quem ativou. Roda de hora
    em hora (sensível ao tempo). Dedupe: 1 aviso por oportunidade por dia.
    Retorna nº de usuários notificados."""
    import surebet
    subs = [(u, users.get_user(u) or {}) for u in users.all_usernames()]
    subs = [(u, info) for (u, info) in subs
            if info.get("surebet_alerts") and info.get("telegram_chat_id")]
    if not subs or not bot_configured():
        return 0

    # escaneia UMA vez na menor margem pedida entre os inscritos (só os limpos)
    floor = min(info.get("surebet_min_margin", 0.5) for _u, info in subs) / 100.0
    scan = surebet.scan_day(client, today, markets=list(surebet.ALL_MARKETS),
                            min_margin=floor, near_max=1.0)
    if not scan.bets:
        return 0

    sent = _load_sent()
    sent = {u: {s: d for s, d in sigs.items() if d == today}  # poda dias velhos
            for u, sigs in sent.items()}
    notified = 0
    for username, info in subs:
        chat_id = info["telegram_chat_id"]
        thr = info.get("surebet_min_margin", 0.5) / 100.0
        mine = sent.setdefault(username, {})
        new = [sb for sb in scan.bets
               if sb.margin >= thr and _surebet_sig(sb, today) not in mine]
        if not new:
            continue
        send_telegram(_format_surebets(new[:8], today), chat_id=chat_id)
        for sb in new:
            mine[_surebet_sig(sb, today)] = today
        notified += 1
    _save_sent(sent)
    return notified


def main(force: bool = False) -> None:
    """Roda o garimpo. Por padrão, só dos usuários cuja hora escolhida
    bate com a hora atual do servidor (o timer chama de hora em hora).
    force=True processa todos (para teste manual)."""
    import sys
    from datetime import datetime
    force = force or any(a in ("force", "all", "--force") for a in sys.argv[1:])
    today = date.today().isoformat()
    now_hour = datetime.now(config.BR_TZ).hour
    client = ApiFootballClient()
    usernames = users.all_usernames()
    if not usernames:
        print("Nenhum usuário cadastrado.")
        return
    if not bot_configured():
        print("[aviso] TELEGRAM_BOT_TOKEN ausente no .env — rodando sem "
              "enviar mensagens.")

    # 1) conferência de cartões: TODOS os usuários, toda hora — resultado
    #    chega assim que os jogos do cartão terminam.
    cards_notified = 0
    for username in usernames:
        try:
            cards_notified += check_cards_for_user(client, username)
        except Exception as e:
            print(f"[erro conferindo cartões de {username}] "
                  f"{type(e).__name__}: {e}")

    # 2) alertas de surebet: de hora em hora p/ quem ativou (sensível ao
    #    tempo — independe do send_hour). Só roda se houver inscrito.
    sure_notified = 0
    try:
        sure_notified = surebet_alerts_run(client, today)
    except Exception as e:
        print(f"[erro nos alertas de surebet] {type(e).__name__}: {e}")

    # 3) garimpo do dia: só usuários cuja hora escolhida bate com agora.
    due = [u for u in usernames
           if force or (users.get_user(u) or {}).get("send_hour", 9) == now_hour]
    for username in due:
        try:
            run_for_user(client, username, today)
        except Exception as e:  # um usuário com erro não derruba os demais
            print(f"[erro em {username}] {type(e).__name__}: {e}")
    print(f"\n[concluído: garimpo p/ {len(due)} usuário(s) nesta hora | "
          f"{cards_notified} resultado(s) de cartão enviado(s) | "
          f"{sure_notified} alerta(s) de surebet enviado(s) | "
          f"{client.calls_made} chamadas à API no total]")


if __name__ == "__main__":
    main()
