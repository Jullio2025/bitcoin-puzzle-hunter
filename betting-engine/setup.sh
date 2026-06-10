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
    echo "[2/5] Atualizando o código..."
    git -C "$DIR" fetch origin "$BRANCH"
    git -C "$DIR" checkout "$BRANCH"
    git -C "$DIR" pull origin "$BRANCH"
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

# atalho para rodar depois: ~/bitcoin-puzzle-hunter/betting-engine/run.sh
cat > run.sh <<'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
exec .venv/bin/python main.py "$@"
EOF
chmod +x run.sh

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

# 5. teste de conexão ----------------------------------------------------------
echo "[5/5] Testando a conexão com a API (listando ligas com cobertura)..."
echo
.venv/bin/python main.py ligas

echo
echo "==============================================="
echo " Instalação concluída!"
echo " Para usar o motor a partir de agora, rode:"
echo "   ~/bitcoin-puzzle-hunter/betting-engine/run.sh"
echo "==============================================="
