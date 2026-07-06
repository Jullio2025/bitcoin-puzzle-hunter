#!/usr/bin/env bash
# Deixa o @ChapaFut_bot RESPONDENDO /start 24h (entrega o chat_id ao user).
#   instalar:   bash install-bot.sh
#   status:     systemctl status chapafut-bot
#   logs:       journalctl -u chapafut-bot -f
#   desligar:   systemctl disable --now chapafut-bot
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "ERRO: rode o setup.sh antes (não achei $DIR/.venv)."
    exit 1
fi
if ! grep -q "TELEGRAM_BOT_TOKEN=." "$DIR/.env" 2>/dev/null; then
    echo "ERRO: defina TELEGRAM_BOT_TOKEN no $DIR/.env antes."
    exit 1
fi

cat > /etc/systemd/system/chapafut-bot.service <<EOF
[Unit]
Description=ChapaFut - ouvinte do bot do Telegram (/start -> chat_id)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python bot_listener.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now chapafut-bot
sleep 2
systemctl --no-pager --lines=5 status chapafut-bot || true
echo
echo "==============================================="
echo " Bot ouvindo! Agora os testers mandam /start pro"
echo " @ChapaFut_bot e recebem o chat_id na hora."
echo "==============================================="
