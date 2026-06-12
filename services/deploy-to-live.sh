#!/bin/bash
set -e
ROOT="$(dirname "$0")/.."
cd "$ROOT"

LIVE_DIR="D:/DivyaDrishti/DDTools-live"

echo "========================================"
echo "         DEPLOY DEV -> LIVE"
echo "========================================"

# 1. Stop live services
echo "-- [1/5] Stopping live services..."
pm2 stop divyadrishti-tunnel 2>/dev/null || true
pm2 stop divyadrishti-live   2>/dev/null || true
echo ""

# 2. Copy files (everything except env-specific data)
echo "-- [2/5] Copying files (dev -> live)..."
rc=0
robocopy "$ROOT" "$LIVE_DIR" \
  //E //COPY:DAT //DCOPY:DA \
  //XD .venv .git __pycache__ node_modules build_temp dist-electron build dist \
  //XD "restart/current_port.json" "services/cloudflared" \
  //XF "*.pyc" .env "*.db" "*.db-shm" "*.db-wal" server_log.txt session_id.txt current_port.json || rc=$?
if [ $rc -ge 8 ]; then echo "  [ERROR] Robocopy failed with code $rc"; exit $rc; fi
echo ""

# 3. Install deps
echo "-- [3/5] Installing dependencies..."
python -m pip install -r "$LIVE_DIR/requirements.txt" --quiet 2>&1 || echo "  [WARN] pip had issues"
echo ""

# 4. Start live server
echo "-- [4/5] Starting live server on port 8080..."
bash "$ROOT/services/start_live_system.sh" "$LIVE_DIR"
echo ""

# 5. Save PM2
echo "-- [5/5] Saving PM2 state..."
pm2 save

echo ""
echo "========================================"
echo "      DEPLOYMENT COMPLETE"
echo "========================================"
echo "Hard refresh browser (Ctrl+Shift+R)."
