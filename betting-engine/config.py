"""
Configuração central do motor.

IMPORTANTE: tudo aqui é SUGESTÃO/default. Nenhum valor é trava.
O usuário pode sobrescrever qualquer parâmetro na interface (main.py)
ou via argumentos de linha de comando.
"""
import os
from datetime import timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# Fuso de Brasília (UTC-3; Brasil não tem horário de verão desde 2019).
# Usado nos horários de envio do garimpo, independente do fuso do VPS.
BR_TZ = timezone(timedelta(hours=-3))

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- Credenciais -----------------------------------------------------------
APISPORTS_KEY = os.getenv("APISPORTS_KEY", "")
API_BASE_URL = "https://v3.football.api-sports.io"

# --- Acesso pago (liberação manual) ---------------------------------------
# Chave PIX mostrada na tela de "conta pendente" (fica no .env, nunca no git).
PIX_KEY = os.getenv("PIX_KEY", "")
PIX_CONTATO = os.getenv("PIX_CONTATO", "")   # ex.: @seu_telegram p/ enviar o comprovante
# Teto de contas pagantes ativas ao mesmo tempo.
MAX_ACTIVE_USERS = int(os.getenv("MAX_ACTIVE_USERS", "100"))

# --- Rate limit / cache ----------------------------------------------------
# Pausa (segundos) entre chamadas à API. Plano Pro aguenta bem 0.2s;
# aumente se receber erro de rate limit.
REQUEST_PAUSE = float(os.getenv("REQUEST_PAUSE", "0.25"))

CACHE_DIR = BASE_DIR / "cache"
DATA_DIR = BASE_DIR / "data"

# TTL do cache em segundos, por tipo de dado
CACHE_TTL = {
    "leagues": 7 * 24 * 3600,        # lista de ligas muda raramente
    "fixtures_day": 1 * 3600,        # jogos do dia
    "team_fixtures": 6 * 3600,       # últimos jogos de um time
    "fixture_stats": 30 * 24 * 3600, # estatísticas de jogo encerrado não mudam
    "league_fixtures": 24 * 3600,    # temporada da liga (p/ varrer árbitro)
    "lineups": 15 * 60,              # escalações mudam até a bola rolar
    "odds": 30 * 60,
    "referee": 24 * 3600,
    "live": 60,                      # placar ao vivo: 1 chamada/min (cache)
}

# --- Defaults do usuário (SUGESTÕES, nunca travas) -------------------------
USER_DEFAULTS = {
    "odd_min": 1.50,        # faixa de odd sugerida
    "odd_max": 3.00,
    "p_min": 0.50,          # probabilidade mínima do modelo sugerida
    "ev_min": 0.0,          # EV mínimo sugerido
    "mode": "simples",      # "simples" ou "multipla"
    "last_n": 5,            # últimos N jogos por time (separado casa/fora)
    "referee_games": 10,    # quantos jogos varrer para média do árbitro
}

# --- Odds ------------------------------------------------------------------
# Bookmaker padrão na API-Football (8 = Bet365). Configurável na interface.
DEFAULT_BOOKMAKER_ID = int(os.getenv("BOOKMAKER_ID", "8"))

# Nomes dos mercados (bets) na API-Football -> mercado interno.
# A API varia o nome conforme o bookmaker, por isso lista de candidatos.
MARKET_BET_NAMES = {
    "goals": ["Goals Over/Under", "Over/Under"],
    "corners": ["Corners Over Under", "Total Corners", "Corners Over/Under"],
    "cards": ["Cards Over/Under", "Total Cards"],
    "1x2": ["Match Winner"],
    "team_goals_home": ["Total - Home"],
    "team_goals_away": ["Total - Away"],
    "handicap": ["Asian Handicap"],
    "btts": ["Both Teams Score", "Both Teams To Score"],
    "double_chance": ["Double Chance"],
}

# --- Modelo ----------------------------------------------------------------
MODEL = {
    # Soma da grade de gols no Poisson do 1X2 (0..N gols por time)
    "max_goals_grid": 10,
    # Baseline de cartões por jogo usado pela regra do árbitro
    # (média típica de ligas europeias ~4.5-5.0)
    "referee_baseline_cards": 4.8,
    # Peso do árbitro no ajuste do lambda de cartões (0 = ignora árbitro)
    "referee_weight": 0.5,
    # Cartão vermelho conta como N cartões no total (1 = conta simples)
    "red_card_weight": 1,
    # Regressão à média (encolhimento): com poucos jogos, a média do time é
    # ruidosa e o modelo fica superconfiante. Puxamos cada taxa em direção a
    # um baseline típico, com peso `shrink_k` (equivale a "k jogos médios").
    # shrink_k=0 desliga. ~4 é moderado.
    "shrink_k": 4,
    "baseline_goals_for": 1.30,    # gols marcados/jogo típico
    "baseline_goals_against": 1.30,
    "baseline_corners_for": 5.0,   # escanteios a favor/jogo típico
    "baseline_corners_against": 5.0,
    "baseline_cards_team": 2.0,    # cartões recebidos/jogo por time
    # Correção de Dixon-Coles: ajusta placares baixos (o Poisson puro
    # subestima empates e 0-0). rho<0 sobe esses placares; 0 = desliga.
    # Valor típico da literatura: ~ -0.10 a -0.15.
    "dixon_coles_rho": -0.10,
}

# --- Tracker ---------------------------------------------------------------
TRACKER_FILE = DATA_DIR / "bets.json"
# Abaixo desta amostra, hit rate e ROI não permitem concluir nada (variância)
TRACKER_MIN_SAMPLE = 100
