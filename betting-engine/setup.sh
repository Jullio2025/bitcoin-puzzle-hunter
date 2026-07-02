#!/usr/bin/env bash
# Instalador automático do motor de apostas.
# Rode no VPS com um único comando (veja o README). Ele:
#   1. instala git/python se faltarem
#   2. baixa (ou atualiza) o código
#   3. cria um ambiente Python isolado e instala as dependências
#   4. pede sua chave da API-Football e grava no .env (só no VPS!)
#   5. roda o teste de conexão (listar ligas com cobertura)
set -e

REPO_URL="https://github.com/Jullio2025/bitcoin-puzzle-hunter.git"
BRANCH="claude/sports-betting-recommender-q9cveu"
DIR="$HOME/bitcoin-puzzle-hunter"

echo "==============================================="
echo " Instalador do motor de apostas (calculadora)"
echo "==============================================="

# 1. dependências do sistema -------------------------------------------------
# instala sempre (é idempotente): o python3 do sistema pode existir sem o
# módulo venv, o que quebra o passo 3
echo "[1/5] Instalando/verificando git, python3, venv e pip..."
apt-get update -y -qq
apt-get install -y -qq git python3 python3-venv python3-pip

# 2. código ------------------------------------------------------------------
if [ -d "$DIR/.git" ]; then
    echo "[2/5] Atualizando o código (forçando sincronização)..."
    git -C "$DIR" fetch origin "$BRANCH"
    # reset --hard ANTES de qualquer checkout: descarta alterações locais
    # (inclusive mudanças de permissão por chmod) que travariam o checkout.
    # .env, cache/ e data/ são ignorados pelo git -> dados ficam intactos.
    git -C "$DIR" reset --hard "origin/$BRANCH"
    git -C "$DIR" checkout -B "$BRANCH" "origin/$BRANCH" 2>/dev/null || true
else
    echo "[2/5] Baixando o código..."
    git clone "$REPO_URL" "$DIR"
    git -C "$DIR" checkout "$BRANCH"
fi
cd "$DIR/betting-engine"

# 3. ambiente python ----------------------------------------------------------
echo "[3/5] Instalando dependências Python..."
# recria o ambiente se estiver quebrado (sem pip) por uma tentativa anterior
if [ ! -x .venv/bin/pip ]; then
    rm -rf .venv
    python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# o modo terminal (main.py/run.sh) foi aposentado — o painel web faz tudo
rm -f run.sh
[ -f run-web.sh ] && chmod +x run-web.sh
[ -f install-service.sh ] && chmod +x install-service.sh
[ -f install-alert.sh ] && chmod +x install-alert.sh
[ -f install-bot.sh ] && chmod +x install-bot.sh
# se o ouvinte do bot já roda, aplica a versão nova
if systemctl is-active --quiet chapafut-bot 2>/dev/null; then
    systemctl restart chapafut-bot
fi

# 4. chave da API -------------------------------------------------------------
if [ -f .env ] && grep -q "APISPORTS_KEY=." .env; then
    echo "[4/5] Chave da API já configurada no .env. OK"
else
    echo "[4/5] Preciso da sua chave da API-Football (api-sports.io)."
    echo "      Ela fica SOMENTE neste servidor, no arquivo .env."
    read -r -p "      Cole a chave e aperte Enter: " KEY
    {
        echo "APISPORTS_KEY=$KEY"
        echo "BOOKMAKER_ID=8"
        echo "REQUEST_PAUSE=0.25"
    } > .env
    echo "      Gravada em $PWD/.env"
fi

# senha do painel web (acesso pelo navegador)
if ! grep -q "PANEL_PASSWORD=." .env 2>/dev/null; then
    echo "      Agora crie uma SENHA para o painel web (acesso pelo navegador)."
    read -r -p "      Digite a senha do painel e aperte Enter: " PANELPWD
    {
        echo "PANEL_USER=admin"
        echo "PANEL_PASSWORD=$PANELPWD"
    } >> .env
    echo "      Senha do painel gravada (usuário: admin)."
fi

# se o ChapaFut já roda como serviço 24h, aplica a versão nova
if systemctl is-active --quiet chapafut 2>/dev/null; then
    systemctl restart chapafut
    echo "      Serviço chapafut reiniciado com a versão nova."
fi

# 5. teste de conexão ----------------------------------------------------------
echo "[5/5] Testando a conexão com a API..."
echo
.venv/bin/python -c "
from api_client import ApiFootballClient
ligas = ApiFootballClient().leagues_with_stats_coverage()
print(f'Conexão OK — {len(ligas)} ligas com cobertura de estatísticas.')
"

echo
echo "==============================================="
echo " Instalação concluída!"
echo " Painel web:  ~/bitcoin-puzzle-hunter/betting-engine/run-web.sh"
echo "   depois acesse no navegador:  http://IP_DO_SEU_VPS:8000"
echo "   (login: admin + a senha do painel que você criou)"
echo "==============================================="
