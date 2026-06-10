#!/usr/bin/env bash
# Sobe o painel web em http://IP_DO_VPS:8000 (proteja com PANEL_PASSWORD no .env)
cd "$(dirname "$0")"
exec .venv/bin/python -m uvicorn webapp:app --host 0.0.0.0 --port "${PORT:-8000}"
