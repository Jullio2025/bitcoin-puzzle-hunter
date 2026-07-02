"""
Painel web do motor — mesma calculadora transparente, agora no navegador.

- Protegido por senha (HTTP Basic): defina PANEL_PASSWORD no .env.
- Nenhuma lógica de cálculo vive aqui: o painel só coleta os parâmetros
  do usuário e exibe o que recommender/scoring calculam.

Rodar no VPS:  ./run-web.sh   (e acesse http://IP_DO_VPS:8000)
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import date

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
import rules
import scanner
import users
from api_client import ApiError, ApiFootballClient
from features import build_match_context
from odds_parser import parse_odds
from recommender import UserParams, analyze_fixture
from tracker import Tracker

BASE = config.BASE_DIR
app = FastAPI(title="ChapaFut — calculadora transparente de apostas")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
SESSION_COOKIE = "chapafut_session"

# migra deploy antigo (PANEL_PASSWORD) para conta admin, se preciso
users.ensure_admin_from_env()


class NotAuthenticated(Exception):
    """Disparada quando falta sessão válida; o handler redireciona p/ login."""


@app.exception_handler(NotAuthenticated)
async def _to_login(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def current_user(request: Request) -> str:
    """Dependência: devolve o usuário logado ou redireciona p/ login."""
    username = users.read_token(request.cookies.get(SESSION_COOKIE))
    if not username:
        raise NotAuthenticated()
    return username


def user_tracker(username: str) -> Tracker:
    return Tracker(users.user_dir(username) / "bets.json")


def _live_map() -> dict:
    """{fixture_id: {gh, ga, min, status}} dos jogos ao vivo (cache 60s)."""
    out: dict = {}
    try:
        for fx in ApiFootballClient().live_fixtures():
            f = fx["fixture"]
            out[f["id"]] = {
                "gh": fx["goals"].get("home"), "ga": fx["goals"].get("away"),
                "min": f.get("status", {}).get("elapsed"),
                "status": f.get("status", {}).get("short")}
    except Exception:
        pass
    return out


def _annotate_live(cards: list, live: dict) -> None:
    """Marca em cada perna se está VENCENDO/PERDENDO agora (parcial ao vivo).

    Usa o placar ao vivo + a mesma lógica de conferência. Só vale para
    mercados de gols/resultado (escanteios/cartões não têm contagem no
    feed ao vivo). Também conta quantas pernas estão vencendo no cartão."""
    import settle
    _map = {"won": "winning", "lost": "losing", "push": "push"}
    for c in cards:
        if c.get("status") not in (None, "pendente"):
            continue  # cartão já fechado não precisa de parcial
        winning = total_live = 0
        for leg in c.get("legs", []):
            lv = live.get(leg.get("fid"))
            leg["live"] = lv
            leg["live_status"] = None
            if not lv or not leg.get("mkt"):
                continue
            o = settle.decide(leg["mkt"], leg["side"], leg.get("line"),
                              lv["gh"] or 0, lv["ga"] or 0, None)
            st = _map.get(o)
            leg["live_status"] = st
            if st in ("winning", "losing", "push"):
                total_live += 1
                if st == "winning":
                    winning += 1
        c["live_winning"] = winning
        c["live_total"] = total_live


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> HTMLResponse:
    """Nada de 'Internal Server Error' seco: página amigável, sem vazar o
    detalhe técnico do erro (que vai só pro log do servidor)."""
    logging.exception("Erro não tratado em %s", request.url.path)
    html = (f"<html><body style='font-family:sans-serif;background:#0a0e1a;"
            f"color:#eef3fb;padding:40px'><h2>⚡ ChapaFut — algo quebrou</h2>"
            f"<p>Tivemos um erro ao processar <code>{request.url.path}</code>."
            f" Tente de novo em instantes.</p>"
            f"<p class='hint'>Se persistir, o dono do sistema vê o detalhe em "
            f"<code>journalctl -u chapafut -n 50</code>.</p>"
            f"<a href='/' style='color:#3dff8b'>← Voltar</a></body></html>")
    return HTMLResponse(html, status_code=500)


def render(request: Request, template: str, **ctx) -> HTMLResponse:
    ctx.setdefault("username",
                   users.read_token(request.cookies.get(SESSION_COOKIE)))
    ctx.update({"request": request, "defaults": config.USER_DEFAULTS,
                "bookmaker_default": config.DEFAULT_BOOKMAKER_ID,
                "is_admin": users.is_admin(ctx.get("username"))})
    resp = templates.TemplateResponse(request, template, ctx)
    # página sempre revalidada: evita o navegador mostrar versão velha do
    # site (o CSS já tem ?v=, mas a página em si podia ficar em cache).
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# Páginas liberadas mesmo para conta pendente/expirada (o resto é bloqueado).
_OPEN_PATHS = {"/login", "/register", "/logout", "/pendente", "/resultados"}


@app.middleware("http")
async def gate_inactive_users(request: Request, call_next):
    """Conta logada mas não liberada (pendente/expirada) só vê /pendente e
    afins — o conteúdo pago fica bloqueado até a liberação manual."""
    path = request.url.path
    if (path.startswith("/static") or path.startswith("/b/")
            or path in _OPEN_PATHS or path.startswith("/admin")):
        return await call_next(request)
    user = users.read_token(request.cookies.get(SESSION_COOKIE))
    if user and not users.is_active(user):
        return RedirectResponse("/pendente", status_code=303)
    return await call_next(request)


# ---------------------------------------------------------- contas/login
# Anti-martelação de senha: N erros seguidos -> espera. Em memória (1
# processo), zera no restart — suficiente pra barrar força bruta básica.
_LOGIN_FAILS: dict[str, list[float]] = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_WINDOW = 10 * 60  # 10 minutos


def _login_blocked(key: str) -> bool:
    import time as _t
    now = _t.time()
    fails = [t for t in _LOGIN_FAILS.get(key, []) if now - t < _LOGIN_WINDOW]
    _LOGIN_FAILS[key] = fails
    return len(fails) >= _LOGIN_MAX_FAILS


def _login_failed(key: str) -> None:
    import time as _t
    _LOGIN_FAILS.setdefault(key, []).append(_t.time())


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", username=None)


@app.post("/login")
def login(request: Request, username: str = Form(...),
          password: str = Form(...)):
    key = (username or "").strip().lower()
    if _login_blocked(key):
        return render(request, "login.html", username=None,
                      error="Muitas tentativas erradas. Espere 10 minutos "
                            "e tente de novo.")
    if not users.verify_user(username, password):
        _login_failed(key)
        return render(request, "login.html", username=None,
                      error="Usuário ou senha inválidos.")
    _LOGIN_FAILS.pop(key, None)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, users.make_token(username),
                    httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return resp


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return render(request, "register.html", username=None)


@app.post("/register")
def register(request: Request, username: str = Form(...),
             password: str = Form(...)):
    ok, msg = users.create_user(username, password)
    if not ok:
        return render(request, "register.html", username=None, error=msg)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, users.make_token(username),
                    httponly=True, samesite="lax", max_age=30 * 24 * 3600)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/pendente", response_class=HTMLResponse)
def pendente(request: Request, user: str = Depends(current_user)):
    """Tela de conta aguardando liberação (com instruções de pagamento)."""
    status = users.access_status(user)
    if status == "ok":  # já liberado: manda pro app
        return RedirectResponse("/", status_code=303)
    return render(request, "pendente.html", conta_user=user, status=status,
                  pix_key=config.PIX_KEY, pix_contato=config.PIX_CONTATO)


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, user: str = Depends(current_user),
               msg: str = ""):
    if not users.is_admin(user):
        return RedirectResponse("/", status_code=303)
    rows = []
    for un in users.all_usernames():
        if users.is_admin(un):
            continue
        u = users.get_user(un) or {}
        rows.append({"username": un, "status": users.access_status(un),
                     "expira": u.get("expira"),
                     "telegram": u.get("telegram_chat_id", "")})
    rows.sort(key=lambda r: (r["status"] != "pendente", r["username"]))
    return render(request, "admin.html", rows=rows, msg=msg,
                  active=users.count_active(), cap=config.MAX_ACTIVE_USERS)


@app.post("/admin/access")
def admin_access(request: Request, user: str = Depends(current_user),
                 alvo: str = Form(...), acao: str = Form(...),
                 dias: int = Form(30)):
    if not users.is_admin(user):
        return RedirectResponse("/", status_code=303)
    from datetime import timedelta
    if acao == "liberar":
        # respeita o teto, a menos que o alvo já esteja ativo
        if (users.access_status(alvo) != "ok"
                and users.count_active() >= config.MAX_ACTIVE_USERS):
            return RedirectResponse(
                f"/admin?msg=Limite+de+{config.MAX_ACTIVE_USERS}+ativos+atingido",
                status_code=303)
        expira = (config.br_today_date()
                  + timedelta(days=max(1, int(dias)))).isoformat()
        users.set_access(alvo, True, expira)
        msg = f"{alvo}+liberado+at%C3%A9+{expira}"
    else:
        users.set_access(alvo, False, None)
        msg = f"{alvo}+bloqueado"
    return RedirectResponse(f"/admin?msg={msg}", status_code=303)


@app.get("/conta", response_class=HTMLResponse)
def conta(request: Request, user: str = Depends(current_user), msg: str = ""):
    import notify
    import daily_scan
    from datetime import datetime
    u = users.get_user(user) or {}
    preset = daily_scan.load_preset(user)
    criteria_labels = []
    if preset:
        criteria_labels = [scanner.Criterion(**c).label
                           for c in preset["criteria"]]
    return render(request, "conta.html", conta_user=user,
                  telegram_chat_id=u.get("telegram_chat_id", ""),
                  send_hour=u.get("send_hour", 9),
                  surebet_alerts=u.get("surebet_alerts", False),
                  surebet_min_margin=u.get("surebet_min_margin", 0.5),
                  server_time=datetime.now(config.BR_TZ).strftime("%H:%M"),
                  bot_ready=notify.bot_configured(),
                  criteria_labels=criteria_labels, msg=msg)


@app.post("/conta/telegram")
def conta_telegram(user: str = Depends(current_user),
                   chat_id: str = Form(""), send_hour: int = Form(9)):
    users.set_telegram(user, chat_id)
    users.set_send_hour(user, send_hour)
    return RedirectResponse("/conta?msg=Telegram+e+hor%C3%A1rio+salvos",
                            status_code=303)


@app.post("/conta/surebet")
def conta_surebet(user: str = Depends(current_user),
                  surebet_alerts: str = Form(""),
                  surebet_min_margin: float = Form(0.5)):
    users.set_surebet_alerts(user, surebet_alerts == "on", surebet_min_margin)
    return RedirectResponse("/conta?msg=Alertas+de+surebet+salvos",
                            status_code=303)


@app.post("/conta/preset/limpar")
def conta_preset_clear(user: str = Depends(current_user)):
    import daily_scan
    daily_scan.clear_preset(user)
    return RedirectResponse("/conta?msg=Crit%C3%A9rios+do+garimpo+limpos",
                            status_code=303)


@app.post("/conta/senha")
def conta_senha(request: Request, user: str = Depends(current_user),
                senha_atual: str = Form(...), senha_nova: str = Form(...)):
    if not users.verify_user(user, senha_atual):
        return RedirectResponse("/conta?msg=Senha+atual+incorreta",
                                status_code=303)
    ok, _m = users.set_password(user, senha_nova)
    msg = "Senha alterada" if ok else "Senha+nova+muito+curta"
    return RedirectResponse(f"/conta?msg={msg.replace(' ', '+')}",
                            status_code=303)


def _bookmakers() -> list:
    """Casas disponíveis para o seletor; lista vazia vira campo numérico."""
    try:
        return sorted(ApiFootballClient().bookmakers(),
                      key=lambda b: (b.get("name") or "").lower())
    except Exception:
        return []


# ------------------------------------------------------------------ páginas
# Campeonatos fixados no topo da lista (id na API-Football)
FAVORITE_LEAGUE_IDS = [71, 72, 73, 13, 11, 2, 3, 39, 140, 135, 78, 61]


def _leagues_for_select() -> tuple[list, list, str | None]:
    """(populares, todas A-Z por país, erro) para o seletor de campeonato."""
    try:
        leagues = ApiFootballClient().leagues_with_stats_coverage()
    except Exception as e:  # sem chave/sem rede: o seletor degrada p/ "todos"
        return [], [], str(e)
    leagues.sort(key=lambda l: (l.get("country") or "", l.get("name") or ""))
    favs = [l for l in leagues if l["league_id"] in FAVORITE_LEAGUE_IDS]
    favs.sort(key=lambda l: FAVORITE_LEAGUE_IDS.index(l["league_id"]))
    return favs, leagues, None


def _home(request: Request, **extra) -> HTMLResponse:
    favorites, leagues, leagues_error = _leagues_for_select()
    return render(request, "index.html",
                  today=extra.pop("today", config.br_today()),
                  rule_names=rules.all_rule_names(),
                  favorites=favorites, leagues=leagues,
                  leagues_error=leagues_error, **extra)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: str = Depends(current_user)):
    """Porta de entrada: o Radar (simples e direto)."""
    return render(request, "radar.html", today=config.br_today(),
                  bookmakers=_bookmakers())


@app.get("/analisar", response_class=HTMLResponse)
def analisar(request: Request, user: str = Depends(current_user)):
    # painel direto: todos os jogos do dia, sem precisar escolher liga antes
    return _render_lobby(request, config.br_today(), 0, None)


@app.post("/fixtures", response_class=HTMLResponse)
def list_fixtures(request: Request, user: str = Depends(current_user),
                  match_date: str = Form(...), league_id: str = Form("0")):
    # o seletor envia "id:temporada" (a API exige season junto com league)
    league, season = 0, None
    if ":" in league_id:
        league, season = (int(x) for x in league_id.split(":", 1))
    elif league_id.strip():
        league = int(league_id)
    return _render_lobby(request, match_date, league, season)


def _render_lobby(request: Request, match_date: str, league: int,
                  season: int | None) -> HTMLResponse:
    """Painel de jogos do dia (todos ou de uma liga), agrupado por campeonato.

    Mostra TODAS as competições do dia (inclusive Copa do Mundo etc.), não só
    as com estatística completa — nada é escondido. As ligas sem cobertura
    completa de cartões/escanteios ficam marcadas; gols/1X2 funcionam sempre.
    """
    try:
        client = ApiFootballClient()
        covered_ids = {lg["league_id"]
                       for lg in client.leagues_with_stats_coverage()}
        if league:
            if season is None:
                season = next((lg["season"]
                               for lg in client.leagues_with_stats_coverage()
                               if lg["league_id"] == league), None)
            fixtures = client.fixtures_by_date(match_date, league_id=league,
                                               season=season)
        else:
            fixtures = client.fixtures_by_date(match_date)
        upcoming = [fx for fx in fixtures
                    if fx["fixture"]["status"]["short"] in ("NS", "TBD")]
    except ApiError as e:
        return _home(request, today=match_date, error=str(e))

    # odds 1X2 do dia inteiro para o lobby (1 lote paginado, cacheado)
    lobby_odds: dict[int, dict] = {}
    try:
        for item in ApiFootballClient().odds_by_date(match_date):
            fid = item.get("fixture", {}).get("id")
            omap = parse_odds([item])
            o = {s: omap.get(("1x2", s, None)) for s in ("home", "draw", "away")}
            if any(o.values()):
                lobby_odds[fid] = o
    except Exception:
        lobby_odds = {}

    upcoming.sort(key=lambda fx: (fx["league"].get("country") or "",
                                  fx["league"].get("name") or "",
                                  fx["fixture"]["date"]))
    # agrupa por "País — Campeonato"; marca quais NÃO têm stats completas
    groups: dict[str, list] = {}
    partial: set[str] = set()
    group_meta: dict[str, dict] = {}
    for fx in upcoming:
        key = f'{fx["league"].get("country") or ""} — {fx["league"]["name"]}'
        groups.setdefault(key, []).append(fx)
        group_meta.setdefault(key, {"logo": fx["league"].get("logo"),
                                    "flag": fx["league"].get("flag")})
        if fx["league"]["id"] not in covered_ids:
            partial.add(key)

    favs, leagues, _ = _leagues_for_select()
    return render(request, "fixtures.html", fixtures=upcoming,
                  fixture_groups=groups, partial_groups=partial,
                  group_meta=group_meta,
                  match_date=match_date, rule_names=rules.all_rule_names(),
                  bookmakers=_bookmakers(), lobby_odds=lobby_odds,
                  favorites=favs, leagues=leagues)


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(request: Request, user: str = Depends(current_user)):
    form = await request.form()
    fixture_ids = [int(x) for x in form.getlist("fixture_id")]
    if not fixture_ids:
        return _home(request, error="Selecione pelo menos um jogo.")

    p_raw = float(form.get("p_min"))
    params = UserParams(
        odd_min=float(form.get("odd_min")),
        odd_max=float(form.get("odd_max")),
        p_min=p_raw / 100 if p_raw > 1 else p_raw,
        ev_min=float(form.get("ev_min")),
        markets=form.getlist("markets") or ["goals", "corners", "cards", "1x2"],
        rules_enabled=form.getlist("rules") or None,
    )
    last_n = int(form.get("last_n", config.USER_DEFAULTS["last_n"]))
    bookmaker = int(form.get("bookmaker", config.DEFAULT_BOOKMAKER_ID))
    flags = {"final": form.get("final") == "on"}

    analyses, errors, injuries = [], [], {}
    client = ApiFootballClient()
    for fid in fixture_ids:
        try:
            fx = client.fixture_by_id(fid)
            if not fx:
                errors.append(f"Jogo {fid} não encontrado.")
                continue
            ctx = build_match_context(client, fx, last_n=last_n,
                                      user_flags=flags)
            odds_map = parse_odds(client.odds(fid, bookmaker),
                                  bookmaker_id=bookmaker)
            fa = analyze_fixture(ctx, odds_map, params)
            # "o que compensa mais": dentro dos critérios primeiro,
            # ordenado por EV decrescente (sem odd vai pro fim)
            fa.markets.sort(key=lambda m: (
                not m.passes_filters,
                -(m.metrics.ev if m.metrics.ev is not None else -999)))
            analyses.append(fa)
            injuries[fid] = _injuries_for(client, fid)
        except ApiError as e:
            errors.append(f"Jogo {fid}: {e}")
        except Exception as e:  # um jogo com dado inesperado não derruba o resto
            logging.exception("Falha analisando o jogo %s", fid)
            errors.append(f"Jogo {fid}: erro inesperado "
                          f"({type(e).__name__}: {e})")

    return render(request, "results.html", analyses=analyses, errors=errors,
                  params=params, injuries=injuries, api_calls=client.calls_made)


def _injuries_for(client: ApiFootballClient, fid: int) -> list:
    """Desfalques do jogo, em formato simples (não derruba a análise se falhar)."""
    try:
        raw = client.fixture_injuries(fid)
    except Exception:
        return []
    out = []
    for i in raw:
        out.append({"player": (i.get("player") or {}).get("name", "?"),
                    "team": (i.get("team") or {}).get("name", ""),
                    "reason": i.get("reason") or i.get("type") or ""})
    return out


# ------------------------------------------------------------------ scanner
@app.get("/scanner", response_class=HTMLResponse)
def scanner_page(request: Request, user: str = Depends(current_user)):
    return render(request, "scanner.html", today=config.br_today(),
                  preset_on=["goals", "corners"], bookmakers=_bookmakers())


# --------------------------------------------------------------- estratégias
@app.get("/estrategias", response_class=HTMLResponse)
def estrategias_page(request: Request, user: str = Depends(current_user)):
    import strategies
    cards = [{"key": k, **v} for k, v in strategies.STRATEGIES.items()]
    return render(request, "estrategias.html", today=config.br_today(),
                  strategies=cards)


@app.post("/estrategia", response_class=HTMLResponse)
def estrategia_run(request: Request, user: str = Depends(current_user),
                   key: str = Form(...), match_date: str = Form(""),
                   deep_limit: int = Form(20)):
    import strategies
    strat = strategies.get(key)
    if not strat:
        return RedirectResponse("/estrategias", status_code=303)
    match_date = match_date or config.br_today()
    try:
        client = ApiFootballClient()
        result = scanner.scan_day(
            client, match_date, strat["criteria"],
            bookmaker=config.DEFAULT_BOOKMAKER_ID,
            last_n=config.USER_DEFAULTS["last_n"],
            match_all=strat.get("match_all", False), deep_limit=deep_limit)
    except ApiError as e:
        import strategies as s
        cards = [{"key": k, **v} for k, v in s.STRATEGIES.items()]
        return render(request, "estrategias.html",
                      today=match_date, strategies=cards, error=str(e))
    return render(request, "scanner_results.html", result=result,
                  criteria=strat["criteria"], match_date=match_date,
                  match_all=strat.get("match_all", False), preset_saved=False,
                  strategy_name=f'{strat["icon"]} {strat["nome"]}',
                  api_calls=client.calls_made)


# -------------------------------------------------------------------- ao vivo
@app.get("/aovivo", response_class=HTMLResponse)
def aovivo(request: Request, user: str = Depends(current_user)):
    return render(request, "aovivo.html")


@app.get("/aovivo.json")
def aovivo_json(user: str = Depends(current_user)):
    """Placar ao vivo (cache de 60s). Lido pelo auto-refresh da tela."""
    try:
        rows = ApiFootballClient().live_fixtures()
    except Exception:
        return JSONResponse({"jogos": [], "erro": True})
    jogos = []
    for fx in rows:
        f, t, g = fx["fixture"], fx["teams"], fx["goals"]
        jogos.append({
            "fid": f["id"],
            "home": t["home"]["name"], "away": t["away"]["name"],
            "hl": t["home"].get("logo"), "al": t["away"].get("logo"),
            "gh": g.get("home"), "ga": g.get("away"),
            "min": f.get("status", {}).get("elapsed"),
            "status": f.get("status", {}).get("short"),
            "league": fx["league"]["name"],
            "country": fx["league"].get("country"),
            "flag": fx["league"].get("flag"),
        })
    jogos.sort(key=lambda j: (j["country"] or "", j["league"] or "",
                              -(j["min"] or 0)))
    return JSONResponse({"jogos": jogos, "erro": False})


_EVENT_ICON = {"goal": "⚽", "card": "🟨", "subst": "🔁", "var": "📺"}

# nomes das estatísticas da API -> rótulo PT (e ordem de exibição)
_STAT_PT = [
    ("Ball Possession", "Posse de bola"),
    ("Total Shots", "Finalizações"),
    ("Shots on Goal", "Chutes no gol"),
    ("Corner Kicks", "Escanteios"),
    ("Fouls", "Faltas"),
    ("Yellow Cards", "Cartões amarelos"),
    ("Red Cards", "Cartões vermelhos"),
    ("Offsides", "Impedimentos"),
    ("Total passes", "Passes"),
]


@app.get("/aovivo/{fid}/{aba}.json")
def aovivo_detalhe(fid: int, aba: str, user: str = Depends(current_user)):
    """Detalhe de UM jogo ao vivo, por aba (sob demanda, cacheado)."""
    cli = ApiFootballClient()
    try:
        if aba == "lances":
            ev = []
            for e in cli.fixture_events(fid):
                tipo = (e.get("type") or "").lower()
                icon = _EVENT_ICON.get(tipo, "•")
                if tipo == "card" and "red" in (e.get("detail") or "").lower():
                    icon = "🟥"
                ev.append({
                    "min": (e.get("time") or {}).get("elapsed"),
                    "extra": (e.get("time") or {}).get("extra"),
                    "icon": icon,
                    "team": (e.get("team") or {}).get("name"),
                    "player": (e.get("player") or {}).get("name"),
                    "detail": e.get("detail")})
            return JSONResponse({"lances": ev})

        if aba == "estatisticas":
            blocks = cli.fixture_statistics_live(fid)
            if len(blocks) < 2:
                return JSONResponse({"times": [], "stats": []})
            casa = {s["type"]: s["value"] for s in blocks[0].get("statistics", [])}
            fora = {s["type"]: s["value"] for s in blocks[1].get("statistics", [])}
            stats = []
            for key, label in _STAT_PT:
                if key in casa or key in fora:
                    stats.append({"label": label,
                                  "home": casa.get(key), "away": fora.get(key)})
            return JSONResponse({
                "times": [blocks[0].get("team", {}).get("name"),
                          blocks[1].get("team", {}).get("name")],
                "stats": stats})

        if aba == "escalacoes":
            out = []
            for lu in cli.fixture_lineups(fid):
                out.append({
                    "team": lu.get("team", {}).get("name"),
                    "formation": lu.get("formation"),
                    "titulares": [p.get("player", {}).get("name")
                                  for p in (lu.get("startXI") or [])],
                    "reservas": [p.get("player", {}).get("name")
                                 for p in (lu.get("substitutes") or [])]})
            return JSONResponse({"escalacoes": out})
    except Exception:
        return JSONResponse({"erro": True})
    return JSONResponse({"erro": "aba desconhecida"})


# -------------------------------------------------------------------- radar
@app.get("/radar", response_class=HTMLResponse)
def radar_page(request: Request, user: str = Depends(current_user)):
    return render(request, "radar.html", today=config.br_today(),
                  bookmakers=_bookmakers())


@app.post("/radar", response_class=HTMLResponse)
async def radar(request: Request, user: str = Depends(current_user)):
    form = await request.form()
    try:
        p_min = float(form.get("p_min") or 85) / 100.0
    except ValueError:
        p_min = 0.85
    markets = form.getlist("markets") or None
    match_date = form.get("match_date") or config.br_today()
    try:
        client = ApiFootballClient()
        result = scanner.radar_day(
            client, match_date, p_min, markets=markets,
            deep_limit=int(form.get("deep_limit") or 15),
            bookmaker=int(form.get("bookmaker") or config.DEFAULT_BOOKMAKER_ID),
            last_n=int(form.get("last_n") or config.USER_DEFAULTS["last_n"]),
            with_odds=form.get("with_odds") == "on",
        )
    except ApiError as e:
        return render(request, "radar.html", today=match_date,
                      bookmakers=_bookmakers(), error=str(e))
    return render(request, "radar_results.html", result=result,
                  match_date=match_date, p_min=p_min,
                  api_calls=client.calls_made)


def _parse_criteria(form) -> list[scanner.Criterion]:
    criteria = []
    side_only = ("1x2", "handicap", "btts", "double_chance")
    for market in ("goals", "team_goals_home", "team_goals_away",
                   "corners", "cards", "handicap", "1x2", "btts",
                   "double_chance"):
        if form.get(f"{market}_on") != "on":
            continue
        raw_line = (form.get(f"{market}_line") or "").strip()
        criteria.append(scanner.Criterion(
            market=market,
            side=form.get(f"{market}_side") or
                 ("any" if market in side_only else "over"),
            line=float(raw_line) if raw_line else None,
            odd_min=float(form.get(f"{market}_odd_min") or 1.01),
            odd_max=float(form.get(f"{market}_odd_max") or 100),
            p_min=float(form.get(f"{market}_p_min") or 0),
            ev_min=float(form.get(f"{market}_ev_min") or -100),
        ))
    return criteria


@app.post("/scan", response_class=HTMLResponse)
async def scan(request: Request, user: str = Depends(current_user)):
    form = await request.form()
    criteria = _parse_criteria(form)
    if not criteria:
        return render(request, "scanner.html",
                      today=form.get("match_date", config.br_today()),
                      preset_on=[],
                      error="Ative pelo menos um mercado para garimpar.")
    impossible = [c for c in criteria if c.impossible]
    match_date = form.get("match_date") or config.br_today()
    bookmaker = int(form.get("bookmaker") or config.DEFAULT_BOOKMAKER_ID)
    last_n = int(form.get("last_n") or config.USER_DEFAULTS["last_n"])
    match_all = form.get("match_all") == "on"
    deep_limit = int(form.get("deep_limit") or 20)

    preset_saved = False
    if form.get("save_preset") == "on":
        import daily_scan
        daily_scan.save_preset(user, criteria, bookmaker, last_n, match_all,
                               deep_limit)
        preset_saved = True

    try:
        client = ApiFootballClient()
        result = scanner.scan_day(
            client, match_date, criteria, bookmaker=bookmaker,
            last_n=last_n, match_all=match_all, deep_limit=deep_limit,
        )
    except ApiError as e:
        return render(request, "scanner.html", today=match_date,
                      preset_on=[], error=str(e))
    return render(request, "scanner_results.html", result=result,
                  criteria=criteria, match_date=match_date,
                  match_all=match_all, preset_saved=preset_saved,
                  impossible=impossible, api_calls=client.calls_made)


# --------------------------------------------------------------- surebet
@app.get("/surebet", response_class=HTMLResponse)
def surebet_page(request: Request, user: str = Depends(current_user)):
    import surebet
    books = []
    try:
        books = sorted({b.get("name") for b in ApiFootballClient().bookmakers()
                        if b.get("name")}, key=str.lower)
    except Exception:
        logging.exception("falha ao listar casas em /surebet")
    return render(request, "surebet.html", today=config.br_today(),
                  all_markets=surebet.ALL_MARKETS,
                  default_markets=surebet.DEFAULT_MARKETS,
                  market_labels=surebet.MARKET_LABELS, books=books)


@app.post("/surebet/scan", response_class=HTMLResponse)
async def surebet_scan(request: Request, user: str = Depends(current_user)):
    import surebet
    form = await request.form()
    match_date = form.get("match_date") or config.br_today()
    markets = [m for m in surebet.ALL_MARKETS if form.get(f"m_{m}") == "on"] \
        or list(surebet.DEFAULT_MARKETS)
    try:
        min_margin = float(form.get("min_margin") or 0) / 100.0
    except ValueError:
        min_margin = 0.0
    include_near = form.get("include_near") == "on"
    near_max = surebet.NEAR_MAX_DEFAULT if include_near else 1.0
    # casas marcadas (checkboxes) + fallback do campo de texto livre
    chosen = set(form.getlist("book"))
    books_raw = (form.get("books") or "").strip()
    chosen |= {b.strip() for b in books_raw.split(",") if b.strip()}
    books = chosen or None
    try:
        bankroll = float((form.get("bankroll") or "100").replace(",", "."))
    except ValueError:
        bankroll = 100.0
    # "só confirmável" = exige a linha em 2+ casas (some as fantasmas de 1 casa)
    min_depth = 2 if form.get("liquid_only") == "on" else 1

    try:
        client = ApiFootballClient()
        scan = surebet.scan_day(client, match_date, markets=markets,
                                min_margin=min_margin, near_max=near_max,
                                books=books, min_depth=min_depth)
    except ApiError as e:
        return render(request, "surebet.html", today=match_date,
                      all_markets=surebet.ALL_MARKETS,
                      default_markets=surebet.DEFAULT_MARKETS,
                      market_labels=surebet.MARKET_LABELS, error=str(e))
    return render(request, "surebet_results.html", scan=scan,
                  match_date=match_date, bankroll=bankroll,
                  include_near=include_near, api_calls=client.calls_made)


# ------------------------------------------------- bilhete compartilhável
def _combine_tolerant(legs: list) -> dict:
    """Combina pernas que podem NÃO ter odd de referência.
    - prob combinada = produto de TODOS os p_model (sempre disponível)
    - odd combinada  = produto só das pernas com odd; 'partial' se faltar
    """
    prob = 1.0
    odd = 1.0
    with_odd = 0
    for l in legs:
        prob *= float(l["p"])
        o = l.get("odd")
        if o:
            odd *= float(o)
            with_odd += 1
    partial = with_odd < len(legs)
    return {"prob": prob, "lose": 1.0 - prob,
            "odd": (odd if with_odd else None), "partial": partial}


@app.post("/ticket/share")
def ticket_share(request: Request, user: str = Depends(current_user),
                 legs_json: str = Form(...)):
    """Cria a página pública do bilhete e leva pro link curto."""
    try:
        legs = json.loads(legs_json)
    except (json.JSONDecodeError, TypeError):
        return RedirectResponse("/tracker?msg=Sele%C3%A7%C3%A3o+inv%C3%A1lida",
                                status_code=303)
    if not legs:
        return RedirectResponse("/tracker?msg=Nenhuma+sele%C3%A7%C3%A3o",
                                status_code=303)
    import share_store
    combo = _combine_tolerant(legs)
    code = share_store.create(legs, combo["odd"], combo["prob"],
                              combo["lose"], partial=combo["partial"],
                              owner=user, source="manual")
    return RedirectResponse(f"/b/{code}", status_code=303)


@app.get("/cartoes", response_class=HTMLResponse)
def cartoes(request: Request, user: str = Depends(current_user), msg: str = ""):
    import share_store
    # Atualiza o resultado das pernas já encerradas (cache curto) para os
    # ✅/❌ aparecerem assim que cada jogo fecha, sem esperar a hora cheia.
    try:
        import daily_scan
        daily_scan.refresh_card_details(ApiFootballClient(), user,
                                        ttl=config.CACHE_TTL["live"])
    except Exception:
        logging.exception("refresh_card_details falhou em /cartoes")
    cards = share_store.list_for_user(user)
    live = _live_map()
    _annotate_live(cards, live)
    for card in cards:
        card["pl"] = share_store.card_pl(card)
        card["corr"] = share_store.same_fixture_warning(card.get("legs", []))
    return render(request, "cartoes.html", cards=cards, msg=msg,
                  summary=share_store.user_summary(user), live=live)


@app.post("/cartoes/aposta")
async def cartoes_aposta(request: Request, user: str = Depends(current_user)):
    """Salva (ou limpa) o valor apostado num cartão, para controle de P/L."""
    import share_store
    form = await request.form()
    code = (form.get("code") or "").strip()
    raw = (form.get("stake") or "").strip().replace(",", ".")
    card = share_store.get(code)
    if not card or card.get("owner") != user:
        return RedirectResponse("/cartoes?msg=Cart%C3%A3o+n%C3%A3o+encontrado",
                                status_code=303)
    try:
        stake = float(raw) if raw else None
        if stake is not None and stake < 0:
            stake = None
    except ValueError:
        stake = None
    share_store.update(code, stake=stake)
    msg = "Valor+apostado+salvo" if stake else "Valor+apostado+removido"
    return RedirectResponse(f"/cartoes?msg={msg}", status_code=303)


@app.post("/cartoes/conferir")
def cartoes_conferir(user: str = Depends(current_user)):
    import daily_scan
    try:
        n = daily_scan.check_cards_for_user(ApiFootballClient(), user)
        msg = f"{n}+cart%C3%A3o(es)+conferido(s)+e+avisado(s)" if n else \
              "Nenhum+cart%C3%A3o+novo+para+conferir+(jogos+ainda+rolando)"
    except ApiError as e:
        msg = f"Erro:+{e}"
    return RedirectResponse(f"/cartoes?msg={msg}", status_code=303)


@app.get("/resultados", response_class=HTMLResponse)
def resultados(request: Request):
    """Página PÚBLICA de prova: histórico e calibração do modelo (agregado)."""
    import share_store
    return render(request, "resultados.html",
                  summary=share_store.global_summary())


@app.get("/b/{code}", response_class=HTMLResponse)
def shared_ticket(request: Request, code: str):
    """Página PÚBLICA (sem login) do bilhete compartilhado."""
    import share_store
    ticket = share_store.get(code)
    if not ticket:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;background:#060a18;"
            "color:#eef3fb;padding:40px'><h2>⚡ ChapaFut</h2>"
            "<p>Bilhete não encontrado (link errado ou removido).</p>"
            "</body></html>", status_code=404)
    live = _live_map()
    _annotate_live([ticket], live)
    corr = share_store.same_fixture_warning(ticket.get("legs", []))
    return render(request, "share.html", ticket=ticket, code=code,
                  live=live, corr=corr,
                  share_url=str(request.base_url).rstrip("/") + f"/b/{code}")
@app.get("/tracker", response_class=HTMLResponse)
def tracker_page(request: Request, user: str = Depends(current_user), msg: str = ""):
    import notify
    from datetime import datetime
    t = user_tracker(user)
    u = users.get_user(user) or {}
    return render(request, "tracker.html", report=t.report(),
                  bets=sorted(t.bets, key=lambda b: -b.id), msg=msg,
                  telegram_chat_id=u.get("telegram_chat_id", ""),
                  send_hour=u.get("send_hour", 9),
                  server_time=datetime.now(config.BR_TZ).strftime("%H:%M"),
                  bot_ready=notify.bot_configured())


@app.post("/tracker/add")
def tracker_add(user: str = Depends(current_user), fixture: str = Form(...),
                market: str = Form(...), odd: float = Form(...),
                stake: float = Form(...), p_model: float = Form(...)):
    bet = user_tracker(user).add(fixture, market, odd, stake, p_model)
    return RedirectResponse(f"/tracker?msg=Aposta+%23{bet.id}+registrada",
                            status_code=303)


@app.post("/tracker/autosettle")
def tracker_autosettle(user: str = Depends(current_user)):
    import settle
    s = settle.auto_settle(user_tracker(user), ApiFootballClient())
    msg = (f"Auto:+{s['ganhou']}+ganhas,+{s['perdeu']}+perdidas,"
           f"+{s['devolvida']}+devolvidas;+{s['pendentes']}+aguardando+jogo,"
           f"+{s['manuais']}+manuais")
    if s["avisos"]:
        msg += f"+({len(s['avisos'])}+aviso(s)+no+log)"
        for a in s["avisos"]:
            logging.warning("auto_settle: %s", a)
    return RedirectResponse(f"/tracker?msg={msg}", status_code=303)


@app.post("/tracker/settle")
def tracker_settle(user: str = Depends(current_user), bet_id: int = Form(...),
                   result: str = Form(...)):
    user_tracker(user).settle(bet_id, result)
    return RedirectResponse(f"/tracker?msg=Aposta+%23{bet_id}+atualizada",
                            status_code=303)


@app.post("/ticket/register")
def ticket_register(user: str = Depends(current_user), legs_json: str = Form(...),
                    stake: float = Form(1.0), mode: str = Form("multipla")):
    """Registra a seleção feita na tela de resultados.

    A conta da múltipla é refeita AQUI no servidor com scoring.combine_legs
    (o JavaScript da página só dá a prévia)."""
    try:
        legs = json.loads(legs_json)
    except (json.JSONDecodeError, TypeError):
        return RedirectResponse("/tracker?msg=Sele%C3%A7%C3%A3o+inv%C3%A1lida",
                                status_code=303)
    if not legs:
        return RedirectResponse("/tracker?msg=Nenhuma+sele%C3%A7%C3%A3o",
                                status_code=303)

    def machine_legs(selection: list) -> list | None:
        out = []
        for l in selection:
            if not l.get("fid") or not l.get("mkt"):
                return None  # sem dados estruturados: liquidação manual
            out.append({"fid": int(l["fid"]), "market": l["mkt"],
                        "side": l["side"], "line": l.get("line"),
                        "label": f"{l['fixture']} — {l['market']}"})
        return out

    # o tracker precisa de odd p/ calcular ROI; sem ela não dá pra registrar
    if any(not l.get("odd") for l in legs):
        return RedirectResponse(
            "/tracker?msg=Para+registrar+no+tracker+todas+as+pernas+"
            "precisam+ter+odd.+Use+'Gerar+meu+cart%C3%A3o'+para+as+sem+odd.",
            status_code=303)

    t = user_tracker(user)
    if mode == "simples":
        for leg in legs:
            t.add(leg["fixture"], leg["market"], float(leg["odd"]),
                  stake, float(leg["p"]), legs=machine_legs([leg]))
        return RedirectResponse(
            f"/tracker?msg={len(legs)}+aposta(s)+simples+registrada(s)",
            status_code=303)

    import scoring
    ticket = scoring.combine_legs([(float(l["odd"]), float(l["p"]))
                                   for l in legs])
    t.add(fixture=" + ".join(l["fixture"] for l in legs),
          market=" + ".join(l["market"] for l in legs),
          odd=ticket.combined_odd, stake=stake,
          p_model=ticket.combined_prob, legs=machine_legs(legs))
    return RedirectResponse("/tracker?msg=M%C3%BAltipla+registrada",
                            status_code=303)
