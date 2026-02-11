#!/bin/bash
# BioModStack RESTART API - Restart only the uvicorn/API backend

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
API_LOG="/tmp/biomodstack_api.log"
API_LOG_DIR="/tmp/biomodstack_api_logs"
MAX_LOGS=10

# ── Log rotation: preserve last N logs ──
rotate_logs() {
    mkdir -p "$API_LOG_DIR"
    if [ -s "$API_LOG" ]; then
        local ts
        ts=$(date +%Y%m%d_%H%M%S)
        cp "$API_LOG" "$API_LOG_DIR/api_${ts}.log"
        echo "[LOG] Archived previous log → $API_LOG_DIR/api_${ts}.log"
    fi
    # Prune oldest logs beyond MAX_LOGS
    local count
    count=$(ls -1 "$API_LOG_DIR"/api_*.log 2>/dev/null | wc -l)
    if [ "$count" -gt "$MAX_LOGS" ]; then
        ls -1t "$API_LOG_DIR"/api_*.log | tail -n +$((MAX_LOGS + 1)) | xargs rm -f
        echo "[LOG] Pruned old logs, keeping last $MAX_LOGS"
    fi
}
rotate_logs

# Load uv PATH
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

# Load BMS environment variables (paths for MSA databases, weights, etc.)
# Use the dedicated non-interactive env file instead of ~/.bashrc.
if [ -f "$HOME/.biomodstack/env.sh" ]; then
    source "$HOME/.biomodstack/env.sh"
fi

# Fan-control backend defaults to CoolerControl unless explicitly overridden.
export BMS_FAN_CONTROL_BACKEND="${BMS_FAN_CONTROL_BACKEND:-coolercontrol}"

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
