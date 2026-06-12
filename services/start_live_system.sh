#!/bin/bash
set -e
ROOT="${1:-$(dirname "$0")/..}"
cd "$ROOT"

echo "========================================"
echo "  STARTING LIVE SYSTEM (port 8080)"
echo "  ROOT=$ROOT"
echo "========================================"

pm2 delete divyadrishti-live 2>/dev/null || true
pm2 start "$ROOT/run_server.py" \
  --name divyadrishti-live \
  --interpreter python \
  --cwd "$ROOT" \
  -- 8080

sleep 2

if [ -f "$ROOT/services/cloudflared/cloudflared.exe" ]; then
  pm2 start "$ROOT/services/cloudflared/cloudflared.exe" \
    --name divyadrishti-tunnel \
    -- tunnel --config "$ROOT/services/cloudflared/config.yml" \
    --origincert "$ROOT/services/cloudflared/cert.pem" run divyadrishti 2>/dev/null || true
fi

pm2 save
echo ""
pm2 list
