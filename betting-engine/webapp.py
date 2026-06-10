"""
Painel web do motor — mesma calculadora transparente, agora no navegador.

- Protegido por senha (HTTP Basic): defina PANEL_PASSWORD no .env.
- Nenhuma lógica de cálculo vive aqui: o painel só coleta os parâmetros
  do usuário e exibe o que recommender/scoring calculam.

Rodar no VPS:  ./run-web.sh   (e acesse http://IP_DO_VPS:8000)
"""
from __future__ import annotations

import json
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


def render(request: Request, template: str, **ctx) -> HTMLResponse:
    ctx.update({"request": request, "defaults": config.USER_DEFAULTS,
                "bookmaker_default": config.DEFAULT_BOOKMAKER_ID})
    return templates.TemplateResponse(request, template, ctx)


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
                  match_date: str = Form(...), league_id: int = Form(0)):
    try:
        client = ApiFootballClient()
        if league_id:
            fixtures = client.fixtures_by_date(match_date, league_id=league_id)
        else:
            covered = {lg["league_id"]
                       for lg in client.leagues_with_stats_coverage()}
            fixtures = [fx for fx in client.fixtures_by_date(match_date)
                        if fx["league"]["id"] in covered]
        upcoming = [fx for fx in fixtures
                    if fx["fixture"]["status"]["short"] in ("NS", "TBD")]
    except ApiError as e:
        return _home(request, today=match_date, error=str(e))
    return render(request, "fixtures.html", fixtures=upcoming,
                  match_date=match_date, rule_names=rules.all_rule_names())


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
            analyses.append(analyze_fixture(ctx, odds_map, params))
        except ApiError as e:
            errors.append(f"Jogo {fid}: {e}")

    return render(request, "results.html", analyses=analyses, errors=errors,
                  params=params, api_calls=client.calls_made)


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
    t = Tracker()
    if mode == "simples":
        for leg in legs:
            t.add(leg["fixture"], leg["market"], float(leg["odd"]),
                  stake, float(leg["p"]))
        return RedirectResponse(
            f"/tracker?msg={len(legs)}+aposta(s)+simples+registrada(s)",
            status_code=303)

    import scoring
    ticket = scoring.combine_legs([(float(l["odd"]), float(l["p"]))
                                   for l in legs])
    t.add(fixture=" + ".join(l["fixture"] for l in legs),
          market=" + ".join(l["market"] for l in legs),
          odd=ticket.combined_odd, stake=stake,
          p_model=ticket.combined_prob)
    return RedirectResponse("/tracker?msg=M%C3%BAltipla+registrada",
                            status_code=303)
