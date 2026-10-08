#!/bin/bash
# BioModStack RESTART API - Restart only the uvicorn/API backend

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
API_LOG="/tmp/biomodstack_api.log"
API_LOG_DIR="/tmp/biomodstack_api_logs"
MAX_LOGS=10
API_RELOAD_RAW="${BMS_API_RELOAD:-1}"
CPU_POWER_STRICT_RAW="${BMS_CPU_POWER_STRICT:-1}"
RAPL_ENERGY_PATH="${BMS_CPU_POWER_RAPL_PATH:-/sys/class/powercap/intel-rapl:0/energy_uj}"

api_reload_enabled() {
    case "${API_RELOAD_RAW,,}" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

cpu_power_strict_enabled() {
    case "${CPU_POWER_STRICT_RAW,,}" in
        0|false|no|off) return 1 ;;
        *) return 0 ;;
    esac
}

rapl_requires_privileged_api_launch() {
    [ -e "$RAPL_ENERGY_PATH" ] && [ ! -r "$RAPL_ENERGY_PATH" ]
}

build_api_cmd() {
    local cmd=(uv run uvicorn main:app --port 8000 --host 127.0.0.1)
    if api_reload_enabled; then
        cmd+=(
            --reload
            --reload-dir "$PROJECT_DIR/platform/api"
            --reload-exclude "inputs/*"
            --reload-exclude "*.db"
            --reload-exclude "__pycache__/*"
            --reload-exclude ".venv/*"
        )
    fi
    printf '%q ' "${cmd[@]}"
}

build_api_launch_wrapper() {
    local api_cmd
    api_cmd="$(build_api_cmd)"
    printf '%s\n' "set -o errexit"
    printf '%s\n' "set -o nounset"
    printf '%s\n' "set +o errexit"
    printf '%s\n' "API_CMD=\"$api_cmd\""
    printf '%s\n' 'echo "[API] Launch wrapper PID $$ starting at $(date -Is)"'
    printf '%s\n' 'echo "[API] Command: $API_CMD"'
    printf '%s\n' 'eval "$API_CMD"'
    printf '%s\n' 'status=$?'
    printf '%s\n' 'echo "[API] Uvicorn exited with status $status at $(date -Is)"'
    printf '%s\n' 'exit $status'
}

launch_api_with_rapl_caps() {
    local api_cmd=$1
    local helper="$PROJECT_DIR/scripts/launch_api_with_rapl_caps.sh"
    if [ ! -x "$helper" ]; then
        echo "❌ Missing RAPL capability launcher: $helper"
        return 1
    fi
    if ! command -v pkexec >/dev/null 2>&1; then
        echo "❌ pkexec is required for accurate CPU power telemetry"
        return 1
    fi

    echo "[API] Authenticating API launch for accurate CPU RAPL telemetry..."
    DISPLAY="${DISPLAY:-:0}" \
    XAUTHORITY="${XAUTHORITY:-/run/user/$(id -u)/gdm/Xauthority}" \
        pkexec env \
        TARGET_USER="$(id -un)" \
        TARGET_UID="$(id -u)" \
        TARGET_GID="$(id -g)" \
        TARGET_HOME="$HOME" \
        TARGET_PATH="$PATH" \
        PROJECT_DIR="$PROJECT_DIR" \
        API_LOG="$API_LOG" \
        API_CMD="$api_cmd" \
        BMS_INPUTS="${BMS_INPUTS:-}" \
        BMS_FAN_CONTROL_BACKEND="${BMS_FAN_CONTROL_BACKEND:-}" \
        "$helper"
}

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

# Keep runtime-generated inputs outside the watched API source tree.
export BMS_INPUTS="${BMS_INPUTS:-$PROJECT_DIR/inputs}"

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
API_CMD="$(build_api_cmd)"
echo "[API] Starting command: $API_CMD"
if rapl_requires_privileged_api_launch; then
    if cpu_power_strict_enabled; then
        API_PID="$(launch_api_with_rapl_caps "$API_CMD")" || exit 1
    else
        echo "[API] Warning: RAPL powercap is unreadable and strict CPU power is disabled."
        nohup bash -lc "$(build_api_launch_wrapper)" > "$API_LOG" 2>&1 &
        API_PID=$!
    fi
else
    nohup bash -lc "$(build_api_launch_wrapper)" > "$API_LOG" 2>&1 &
    API_PID=$!
fi

sleep 2

if pgrep -f "uvicorn.*main:app" > /dev/null; then
    notify-send "BioModStack" "✅ API restarted successfully (PID: $API_PID)" -i dialog-ok
else
    notify-send "BioModStack" "❌ API failed to restart! Check logs: $API_LOG" -i dialog-error
fi
