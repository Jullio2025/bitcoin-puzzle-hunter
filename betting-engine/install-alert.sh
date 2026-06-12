#!/usr/bin/env bash
# Agenda o garimpo automático diário (Scanner + liquidação + Telegram).
#   instalar:        bash install-alert.sh           (padrão: 09:00)
#   outro horário:   HORA=07:30 bash install-alert.sh
#   testar agora:    systemctl start chapafut-daily
#   ver logs:        journalctl -u chapafut-daily -n 50
#   desligar:        systemctl disable --now chapafut-daily.timer
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
HORA="${HORA:-09:00}"

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "ERRO: rode o setup.sh antes (não achei $DIR/.venv)."
    exit 1
fi
if ! grep -q "TELEGRAM_BOT_TOKEN=." "$DIR/.env" 2>/dev/null; then
    echo "AVISO: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID não estão no .env —"
    echo "       o garimpo vai rodar, mas sem mandar mensagem."
    echo "       (crie um bot com o @BotFather e preencha o .env)"
fi

cat > /etc/systemd/system/chapafut-daily.service <<EOF
[Unit]
Description=ChapaFut - garimpo diario + liquidacao + Telegram
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python daily_scan.py
EOF

cat > /etc/systemd/system/chapafut-daily.timer <<EOF
[Unit]
Description=ChapaFut - dispara o garimpo diario as $HORA

[Timer]
OnCalendar=*-*-* $HORA:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now chapafut-daily.timer
echo
echo "==============================================="
echo " Garimpo automático agendado todo dia às $HORA!"
echo " Teste agora:  systemctl start chapafut-daily"
echo " Logs:         journalctl -u chapafut-daily -n 50"
echo "==============================================="
