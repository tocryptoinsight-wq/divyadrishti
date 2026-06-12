#!/bin/bash
set -e
ROOT="$(dirname "$0")/.."
cd "$ROOT"

echo "========================================"
echo "   STARTING DEV SYSTEM (port 8000)"
echo "========================================"

pm2 start "$ROOT/run_server.py" \
  --name divyadrishti-dev \
  --interpreter "$ROOT/.venv/Scripts/python.exe" \
  --cwd "$ROOT" \
  -- 8000

pm2 save
echo ""
pm2 list
