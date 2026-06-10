#!/usr/bin/env bash
# Deixa o ChapaFut rodando 24h como serviço do sistema (systemd):
# liga sozinho no boot do VPS e reinicia sozinho se cair.
#   instalar:    bash install-service.sh
#   ver status:  systemctl status chapafut
#   ver logs:    journalctl -u chapafut -f
#   parar/tirar: systemctl disable --now chapafut
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8000}"

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "ERRO: ambiente não encontrado em $DIR/.venv — rode o setup.sh antes."
    exit 1
fi
if ! grep -q "PANEL_PASSWORD=." "$DIR/.env" 2>/dev/null; then
    echo "ERRO: defina PANEL_PASSWORD no $DIR/.env antes (o setup.sh pergunta)."
    exit 1
fi

cat > /etc/systemd/system/chapafut.service <<EOF
[Unit]
Description=ChapaFut - painel web da calculadora de apostas
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -m uvicorn webapp:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now chapafut
sleep 2
systemctl --no-pager --lines=5 status chapafut || true

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "==============================================="
echo " ChapaFut no ar 24h! Acesse: http://${IP:-IP_DO_VPS}:$PORT"
echo " Ele volta sozinho se o VPS reiniciar ou se o app cair."
echo " Logs ao vivo:  journalctl -u chapafut -f"
echo "==============================================="
