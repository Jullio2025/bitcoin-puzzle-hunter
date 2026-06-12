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

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
import rules
import scanner
from api_client import ApiError, ApiFootballClient
from features import build_match_context
from odds_parser import parse_odds
from recommender import UserParams, analyze_fixture
from tracker import Tracker

BASE = config.BASE_DIR
app = FastAPI(title="ChapaFut — calculadora transparente de apostas")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
security = HTTPBasic()


def auth(creds: HTTPBasicCredentials = Depends(security)) -> str:
    user = os.getenv("PANEL_USER", "admin")
    password = os.getenv("PANEL_PASSWORD", "")
    if not password:
        raise HTTPException(
            status_code=500,
            detail="Defina PANEL_PASSWORD no arquivo .env para liberar o painel.")
    ok = (secrets.compare_digest(creds.username, user)
          and secrets.compare_digest(creds.password, password))
    if not ok:
        raise HTTPException(status_code=401, detail="Login inválido",
                            headers={"WWW-Authenticate": "Basic"})
    return creds.username


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> HTMLResponse:
    """Nada de 'Internal Server Error' seco: mostra o que quebrou."""
    logging.exception("Erro não tratado em %s", request.url.path)
    detail = f"{type(exc).__name__}: {exc}"
    html = (f"<html><body style='font-family:sans-serif;background:#0a0e1a;"
            f"color:#eef3fb;padding:40px'><h2>⚡ ChapaFut — algo quebrou</h2>"
            f"<p>Erro em <code>{request.url.path}</code>:</p>"
            f"<pre style='background:#131c33;padding:16px;border-radius:8px;"
            f"white-space:pre-wrap'>{detail}</pre>"
            f"<p>Detalhes completos: <code>journalctl -u chapafut -n 50</code>"
            f"</p><a href='/' style='color:#3dff8b'>← Voltar</a></body></html>")
    return HTMLResponse(html, status_code=500)


def render(request: Request, template: str, **ctx) -> HTMLResponse:
    ctx.update({"request": request, "defaults": config.USER_DEFAULTS,
                "bookmaker_default": config.DEFAULT_BOOKMAKER_ID})
    return templates.TemplateResponse(request, template, ctx)


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
                  today=extra.pop("today", date.today().isoformat()),
                  rule_names=rules.all_rule_names(),
                  favorites=favorites, leagues=leagues,
                  leagues_error=leagues_error, **extra)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, _: str = Depends(auth)):
    return _home(request)


@app.post("/fixtures", response_class=HTMLResponse)
def list_fixtures(request: Request, _: str = Depends(auth),
                  match_date: str = Form(...), league_id: str = Form("0")):
    # o seletor envia "id:temporada" (a API exige season junto com league)
    league, season = 0, None
    if ":" in league_id:
        league, season = (int(x) for x in league_id.split(":", 1))
    elif league_id.strip():
        league = int(league_id)
    try:
        client = ApiFootballClient()
        if league:
            if season is None:
                season = next(
                    (lg["season"]
                     for lg in client.leagues_with_stats_coverage()
                     if lg["league_id"] == league), None)
            fixtures = client.fixtures_by_date(match_date, league_id=league,
                                               season=season)
        else:
            covered = {lg["league_id"]
                       for lg in client.leagues_with_stats_coverage()}
            fixtures = [fx for fx in client.fixtures_by_date(match_date)
                        if fx["league"]["id"] in covered]
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

    upcoming.sort(key=lambda fx: fx["fixture"]["date"])
    return render(request, "fixtures.html", fixtures=upcoming,
                  match_date=match_date, rule_names=rules.all_rule_names(),
                  bookmakers=_bookmakers(), lobby_odds=lobby_odds)


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(request: Request, _: str = Depends(auth)):
    form = await request.form()
    fixture_ids = [int(x) for x in form.getlist("fixture_id")]
    if not fixture_ids:
        return _home(request, error="Selecione pelo menos um jogo.")

    params = UserParams(
        odd_min=float(form.get("odd_min")),
        odd_max=float(form.get("odd_max")),
        p_min=float(form.get("p_min")),
        ev_min=float(form.get("ev_min")),
        markets=form.getlist("markets") or ["goals", "corners", "cards", "1x2"],
        rules_enabled=form.getlist("rules") or None,
    )
    last_n = int(form.get("last_n", config.USER_DEFAULTS["last_n"]))
    bookmaker = int(form.get("bookmaker", config.DEFAULT_BOOKMAKER_ID))
    flags = {"final": form.get("final") == "on"}

    analyses, errors = [], []
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
        except ApiError as e:
            errors.append(f"Jogo {fid}: {e}")
        except Exception as e:  # um jogo com dado inesperado não derruba o resto
            logging.exception("Falha analisando o jogo %s", fid)
            errors.append(f"Jogo {fid}: erro inesperado "
                          f"({type(e).__name__}: {e})")

    return render(request, "results.html", analyses=analyses, errors=errors,
                  params=params, api_calls=client.calls_made)


# ------------------------------------------------------------------ scanner
@app.get("/scanner", response_class=HTMLResponse)
def scanner_page(request: Request, _: str = Depends(auth)):
    return render(request, "scanner.html", today=date.today().isoformat(),
                  preset_on=["goals", "corners"], bookmakers=_bookmakers())


def _parse_criteria(form) -> list[scanner.Criterion]:
    criteria = []
    for market in ("goals", "team_goals_home", "team_goals_away",
                   "corners", "cards", "handicap", "1x2"):
        if form.get(f"{market}_on") != "on":
            continue
        raw_line = (form.get(f"{market}_line") or "").strip()
        criteria.append(scanner.Criterion(
            market=market,
            side=form.get(f"{market}_side") or
                 ("any" if market in ("1x2", "handicap") else "over"),
            line=float(raw_line) if raw_line else None,
            odd_min=float(form.get(f"{market}_odd_min") or 1.01),
            odd_max=float(form.get(f"{market}_odd_max") or 100),
            p_min=float(form.get(f"{market}_p_min") or 0),
        ))
    return criteria


@app.post("/scan", response_class=HTMLResponse)
async def scan(request: Request, _: str = Depends(auth)):
    form = await request.form()
    criteria = _parse_criteria(form)
    if not criteria:
        return render(request, "scanner.html",
                      today=form.get("match_date", date.today().isoformat()),
                      preset_on=[],
                      error="Ative pelo menos um mercado para garimpar.")
    match_date = form.get("match_date") or date.today().isoformat()
    bookmaker = int(form.get("bookmaker") or config.DEFAULT_BOOKMAKER_ID)
    last_n = int(form.get("last_n") or config.USER_DEFAULTS["last_n"])
    match_all = form.get("match_all") == "on"
    deep_limit = int(form.get("deep_limit") or 20)

    preset_saved = False
    if form.get("save_preset") == "on":
        import daily_scan
        daily_scan.save_preset(criteria, bookmaker, last_n, match_all,
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
                  api_calls=client.calls_made)


# ------------------------------------------------------------------ tracker
@app.get("/tracker", response_class=HTMLResponse)
def tracker_page(request: Request, _: str = Depends(auth), msg: str = ""):
    t = Tracker()
    return render(request, "tracker.html", report=t.report(),
                  bets=sorted(t.bets, key=lambda b: -b.id), msg=msg)


@app.post("/tracker/add")
def tracker_add(_: str = Depends(auth), fixture: str = Form(...),
                market: str = Form(...), odd: float = Form(...),
                stake: float = Form(...), p_model: float = Form(...)):
    bet = Tracker().add(fixture, market, odd, stake, p_model)
    return RedirectResponse(f"/tracker?msg=Aposta+%23{bet.id}+registrada",
                            status_code=303)


@app.post("/tracker/autosettle")
def tracker_autosettle(_: str = Depends(auth)):
    import settle
    s = settle.auto_settle(Tracker(), ApiFootballClient())
    msg = (f"Auto:+{s['ganhou']}+ganhas,+{s['perdeu']}+perdidas,"
           f"+{s['devolvida']}+devolvidas;+{s['pendentes']}+aguardando+jogo,"
           f"+{s['manuais']}+manuais")
    if s["avisos"]:
        msg += f"+({len(s['avisos'])}+aviso(s)+no+log)"
        for a in s["avisos"]:
            logging.warning("auto_settle: %s", a)
    return RedirectResponse(f"/tracker?msg={msg}", status_code=303)


@app.post("/tracker/settle")
def tracker_settle(_: str = Depends(auth), bet_id: int = Form(...),
                   result: str = Form(...)):
    Tracker().settle(bet_id, result)
    return RedirectResponse(f"/tracker?msg=Aposta+%23{bet_id}+atualizada",
                            status_code=303)


@app.post("/ticket/register")
def ticket_register(_: str = Depends(auth), legs_json: str = Form(...),
                    stake: float = Form(1.0), mode: str = Form("multipla")):
    """Registra a seleção feita na tela de resultados.

    A conta da múltipla é refeita AQUI no servidor com scoring.combine_legs
    (o JavaScript da página só dá a prévia)."""
    legs = json.loads(legs_json)
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

    t = Tracker()
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
