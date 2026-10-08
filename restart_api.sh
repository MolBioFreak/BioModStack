#!/bin/bash
# BioModStack RESTART API - Restart only the uvicorn/API backend

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
API_LOG="/tmp/biomodstack_api.log"

# Load uv PATH
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

notify-send "BioModStack" "🔄 Restarting API backend (uvicorn)..." -i view-refresh

# Stop API only
pkill -f "uvicorn.*main:app" 2>/dev/null && echo "API stopped" || echo "API not running"
sleep 1

# Force kill port 8000 if still in use
fuser -k -n tcp 8000 >/dev/null 2>&1
sleep 1

# Start API
cd "$PROJECT_DIR/platform/api"
nohup uv run uvicorn main:app --reload --port 8000 --host 0.0.0.0 > "$API_LOG" 2>&1 &
API_PID=$!

sleep 2

if pgrep -f "uvicorn.*main:app" > /dev/null; then
    notify-send "BioModStack" "✅ API restarted successfully (PID: $API_PID)" -i dialog-ok
else
    notify-send "BioModStack" "❌ API failed to restart! Check logs: $API_LOG" -i dialog-error
fi
