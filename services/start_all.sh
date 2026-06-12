#!/bin/bash
set -e

echo "========================================"
echo "     STARTING ALL DDTOOLS SERVICES"
echo "========================================"

# Try restoring previous PM2 state first
if pm2 resurrect 2>/dev/null; then
  echo "  [OK] Restored previous PM2 state"
  echo ""
  pm2 list
  exit 0
fi

echo "  No saved state — starting each service manually..."
echo ""

SCRIPT_DIR="$(dirname "$0")"

bash "$SCRIPT_DIR/start_dev_system.sh"
sleep 1
bash "$SCRIPT_DIR/start_live_system.sh"

echo ""
echo "========================================"
echo "       ALL SERVICES STARTED"
echo "========================================"
pm2 list
