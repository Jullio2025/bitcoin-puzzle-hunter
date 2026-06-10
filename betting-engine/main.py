"""
Interface do motor — calculadora TRANSPARENTE de apostas.

Este programa NÃO recomenda estratégia e NÃO decide nada: ele calcula e
mostra os números de todos os mercados pedidos. Quem decide o risco e o
que apostar é SEMPRE você, na sua própria casa de apostas.

Uso:
    python main.py            -> menu interativo amigável
    python main.py ligas      -> teste de conexão + ligas com cobertura
    python main.py relatorio  -> relatório do tracker
"""
from __future__ import annotations

import sys
from datetime import date

import config
import recommender
import rules
from api_client import ApiError, ApiFootballClient
from features import build_match_context
from odds_parser import parse_odds
from recommender import MultipleLeg, UserParams, analyze_fixture
from tracker import Tracker

LINE = "-" * 78


# ----------------------------------------------------------------- helpers
def ask(prompt: str, default=None, cast=str):
    """Pergunta amigável: Enter aceita a sugestão; QUALQUER valor é aceito.

    Defaults são só sugestão — nunca trava (princípio do produto).
    """
    suffix = f" [Enter = {default}]" if default is not None else ""
    while True:
        raw = input(f"  {prompt}{suffix}: ").strip()
        if not raw:
            if default is not None:
                return default
            print("    (campo obrigatório)")
            continue
        try:
            return cast(raw)
        except ValueError:
            print(f"    Valor inválido para {cast.__name__}, tente de novo.")


def ask_yes(prompt: str, default: bool = False) -> bool:
    d = "s" if default else "n"
    raw = input(f"  {prompt} (s/n) [Enter = {d}]: ").strip().lower()
    return default if not raw else raw.startswith("s")


def fmt_pct(v) -> str:
    return f"{v:6.1%}" if v is not None else "   --"


def fmt(v, spec=".2f") -> str:
    return format(v, spec) if v is not None else "--"


# ------------------------------------------------------------------ etapas
def cmd_ligas(client: ApiFootballClient) -> None:
    """Etapa 1 do build: testa a conexão e conta ligas com cobertura."""
    print("\nConsultando ligas na API-Football...")
    covered = client.leagues_with_stats_coverage()
    total = len(client.leagues())
    print(f"\nLigas cadastradas: {total}")
    print(f"Ligas/temporadas atuais com statistics_fixtures=true: "
          f"{len(covered)}")
    print("Só essas garantem cartões/escanteios preenchidos. Exemplos:")
    for lg in covered[:20]:
        print(f"  [{lg['league_id']:>5}] {lg['country']} - {lg['name']} "
              f"(temporada {lg['season']})")
    if len(covered) > 20:
        print(f"  ... e mais {len(covered) - 20}.")


def collect_params() -> UserParams:
    d = config.USER_DEFAULTS
    print("\nSeus filtros (sugestões entre colchetes; aceita qualquer valor;")
    print("nada será escondido — mercados fora do filtro aparecem marcados):")
    p = UserParams(
        odd_min=ask("Odd mínima", d["odd_min"], float),
        odd_max=ask("Odd máxima", d["odd_max"], float),
        p_min=ask("Probabilidade mínima do modelo (0-1)", d["p_min"], float),
        ev_min=ask("EV mínimo por unidade", d["ev_min"], float),
        mode="multipla" if ask_yes("Modo múltipla?", d["mode"] == "multipla")
             else "simples",
    )
    all_markets = ["goals", "corners", "cards", "1x2"]
    raw = ask("Mercados (goals,corners,cards,1x2)", ",".join(all_markets))
    p.markets = [m.strip() for m in raw.split(",") if m.strip()]

    if ask_yes("Quer desativar alguma regra de análise?", False):
        names = rules.all_rule_names()
        print(f"    Regras disponíveis: {', '.join(names)}")
        raw = ask("Regras ATIVAS separadas por vírgula", ",".join(names))
        p.rules_enabled = [r.strip() for r in raw.split(",") if r.strip()]
    return p


def print_fixture_analysis(fa: recommender.FixtureAnalysis) -> None:
    ctx = fa.ctx
    print(f"\n{LINE}\n{ctx.label}  |  {ctx.fixture['fixture']['date'][:16]}"
          f"  |  árbitro: {ctx.fixture['fixture'].get('referee') or '?'}")
    print(LINE)

    print("Lambdas do modelo (Poisson) e de onde saíram:")
    for key, text in fa.lambda_explanations.items():
        print(f"  - {key}: {text} -> ajustado: {fa.lambdas[key]:.2f}")

    print("\nRegras aplicadas (cada ajuste explicado):")
    for res in fa.rule_results:
        mults = ", ".join(f"{k} x{v:.3f}" for k, v in res.multipliers.items())
        print(f"  [{res.rule}] {('ajuste: ' + mults + ' — ') if mults else ''}"
              f"{res.explanation}")

    print(f"\nMercados (TODOS os pedidos — a decisão é sua):")
    header = (f"  {'#':>2} {'mercado':<32} {'p_model':>7} {'odd':>6} "
              f"{'1/odd':>6} {'edge':>7} {'EV':>7} {'rec.':>5}  filtros")
    print(header)
    for i, m in enumerate(fa.markets, 1):
        mt = m.metrics
        status = "OK" if m.passes_filters else "; ".join(m.failed_filters)
        print(f"  {i:>2} {m.label:<32} {fmt_pct(mt.p_model)} "
              f"{fmt(mt.odd):>6} {fmt_pct(mt.implied)} "
              f"{fmt(mt.edge, '+.3f'):>7} {fmt(mt.ev, '+.3f'):>7} "
              f"{fmt(mt.wins_to_recover, '.1f'):>5}  {status}")
        for note in mt.notes:
            print(f"       nota: {note}")
    print("\n  Legenda: 1/odd = prob. implícita (e taxa de acerto p/ empatar);"
          "\n  rec. = vitórias necessárias p/ recuperar 1 derrota = 1/(odd-1).")


def cmd_analisar(client: ApiFootballClient) -> None:
    today = date.today().isoformat()
    when = ask("Data dos jogos (YYYY-MM-DD)", today)
    league = ask("ID da liga (0 = todas com cobertura)", 0, int)

    if league:
        fixtures = client.fixtures_by_date(when, league_id=league)
    else:
        covered = {lg["league_id"] for lg in
                   client.leagues_with_stats_coverage()}
        fixtures = [fx for fx in client.fixtures_by_date(when)
                    if fx["league"]["id"] in covered]

    upcoming = [fx for fx in fixtures
                if fx["fixture"]["status"]["short"] in ("NS", "TBD")]
    if not upcoming:
        print("\nNenhum jogo (ainda não iniciado) encontrado nesse recorte.")
        return

    print(f"\nJogos encontrados ({len(upcoming)}):")
    for i, fx in enumerate(upcoming, 1):
        print(f"  {i:>3}. {fx['teams']['home']['name']} x "
              f"{fx['teams']['away']['name']} — {fx['league']['name']} "
              f"({fx['fixture']['date'][11:16]})")

    raw = ask("Quais analisar? (ex.: 1,3,5 ou 'todos')", "1")
    if raw.strip().lower() in ("todos", "todas", "all"):
        chosen = upcoming
    else:
        idx = [int(x) for x in raw.replace(" ", "").split(",") if x]
        chosen = [upcoming[i - 1] for i in idx if 1 <= i <= len(upcoming)]

    params = collect_params()
    last_n = ask("Últimos N jogos por mando", config.USER_DEFAULTS["last_n"],
                 int)
    bookmaker = ask("ID do bookmaker (8 = Bet365)",
                    config.DEFAULT_BOOKMAKER_ID, int)
    flags = {"final": ask_yes("Algum desses jogos é final/decisão?", False)}

    analyses = []
    for fx in chosen:
        print(f"\nColetando dados de {fx['teams']['home']['name']} x "
              f"{fx['teams']['away']['name']} (cache + API, aguarde)...")
        ctx = build_match_context(client, fx, last_n=last_n,
                                  user_flags=flags)
        odds_map = parse_odds(client.odds(fx["fixture"]["id"], bookmaker),
                              bookmaker_id=bookmaker)
        fa = analyze_fixture(ctx, odds_map, params)
        analyses.append(fa)
        print_fixture_analysis(fa)

    if params.mode == "multipla":
        build_multiple_interactive(analyses)

    print(f"\n(Chamadas reais à API nesta sessão: {client.calls_made})")


def build_multiple_interactive(analyses: list) -> None:
    print(f"\n{LINE}\nMONTAGEM DE MÚLTIPLA")
    legs: list[MultipleLeg] = []
    for fa in analyses:
        with_odds = [(i, m) for i, m in enumerate(fa.markets, 1)
                     if m.metrics.odd is not None]
        if not with_odds:
            continue
        print(f"\n{fa.ctx.label} — mercados numerados como na tabela acima.")
        raw = ask("Número do mercado p/ a múltipla (0 = pular)", 0, int)
        if raw and 1 <= raw <= len(fa.markets):
            m = fa.markets[raw - 1]
            if m.metrics.odd is None:
                print("    Esse mercado está sem odd; pulando.")
                continue
            legs.append(MultipleLeg(fixture_label=fa.ctx.label,
                                    market_label=m.label,
                                    odd=m.metrics.odd,
                                    p_model=m.metrics.p_model))
    if not legs:
        print("Nenhuma perna selecionada.")
        return

    ticket = recommender.build_multiple(legs)
    print(f"\nBilhete com {len(legs)} perna(s):")
    for leg in legs:
        print(f"  - {leg.fixture_label} | {leg.market_label} | "
              f"odd {leg.odd:.2f} | p_model {leg.p_model:.1%}")
    # Os 3 números SEMPRE juntos — nunca a odd combinada sozinha:
    print(f"\n  Odd combinada:            {ticket.combined_odd:.2f}")
    print(f"  Probabilidade combinada:  {ticket.combined_prob:.1%}")
    print(f"  Chance de PERDER bilhete: {ticket.lose_prob:.1%}")
    print(f"  Aviso: {ticket.warning}")

    if ask_yes("Registrar este bilhete no tracker?", False):
        stake = ask("Stake (unidades)", 1.0, float)
        t = Tracker()
        bet = t.add(fixture=" + ".join(l.fixture_label for l in legs),
                    market=" + ".join(l.market_label for l in legs),
                    odd=ticket.combined_odd, stake=stake,
                    p_model=ticket.combined_prob)
        print(f"  Registrado como aposta #{bet.id}.")


# ------------------------------------------------------------------ tracker
def cmd_tracker_add() -> None:
    t = Tracker()
    bet = t.add(fixture=ask("Jogo (ex.: Time A x Time B 2026-06-11)"),
                market=ask("Mercado (ex.: Cartões: Mais de 4.5)"),
                odd=ask("Odd", cast=float),
                stake=ask("Stake (unidades)", 1.0, float),
                p_model=ask("p_model na hora da aposta (0-1)", cast=float))
    print(f"  Registrado como aposta #{bet.id}.")


def cmd_tracker_settle() -> None:
    t = Tracker()
    pending = [b for b in t.bets if b.result == "pendente"]
    if not pending:
        print("  Nenhuma aposta pendente.")
        return
    for b in pending:
        print(f"  #{b.id} {b.fixture} | {b.market} | odd {b.odd}")
    bet_id = ask("ID da aposta", cast=int)
    result = ask("Resultado (ganhou/perdeu/devolvida)")
    t.settle(bet_id, result)
    print("  Atualizado.")


def cmd_tracker_report() -> None:
    rep = Tracker().report()
    print("\nRELATÓRIO DO TRACKER")
    print(f"  Registradas: {rep['total_registradas']} "
          f"(pendentes: {rep['pendentes']})")
    print(f"  Liquidadas:  {rep['liquidadas']} | acertos: {rep['acertos']}")
    print(f"  Hit rate real:   {fmt_pct(rep['hit_rate'])}")
    print(f"  p_model médio:   {fmt_pct(rep['p_model_medio'])} "
          "(compare com o hit rate real — calibração)")
    print(f"  Apostado: {rep['total_apostado']:.2f} u | "
          f"lucro: {rep['lucro']:+.2f} u | "
          f"ROI: {fmt_pct(rep['roi'])}")
    if rep["warning"]:
        print(f"\n  AVISO: {rep['warning']}")


# --------------------------------------------------------------------- menu
DISCLAIMER = """
==============================================================================
 ChapaFut — calculadora transparente de apostas (futebol)
 Este programa só CALCULA e MOSTRA números. Ele não recomenda estratégia,
 não opera casa de apostas e não decide nada por você. Toda decisão de
 risco e de aposta é SUA, na SUA casa de apostas. Modelo: Poisson auditável.
=============================================================================="""


def main() -> None:
    print(DISCLAIMER)

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "ligas":
            cmd_ligas(ApiFootballClient())
        elif cmd == "relatorio":
            cmd_tracker_report()
        else:
            print(f"Comando desconhecido: {cmd}. Use: ligas | relatorio")
        return

    while True:
        print("\nMENU")
        print("  1. Testar conexão / ligas com cobertura de estatísticas")
        print("  2. Analisar jogos (simples ou múltipla)")
        print("  3. Tracker: registrar aposta")
        print("  4. Tracker: liquidar aposta")
        print("  5. Tracker: relatório (hit rate / ROI reais)")
        print("  0. Sair")
        choice = ask("Opção", "2")
        try:
            if choice == "1":
                cmd_ligas(ApiFootballClient())
            elif choice == "2":
                cmd_analisar(ApiFootballClient())
            elif choice == "3":
                cmd_tracker_add()
            elif choice == "4":
                cmd_tracker_settle()
            elif choice == "5":
                cmd_tracker_report()
            elif choice == "0":
                return
            else:
                print("  Opção inválida.")
        except ApiError as e:
            print(f"\n  ERRO DA API: {e}")
        except KeyboardInterrupt:
            print("\n  (interrompido)")


if __name__ == "__main__":
    main()
