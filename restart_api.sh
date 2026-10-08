#!/bin/bash
# BioModStack RESTART API - Restart only the uvicorn/API backend

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
API_LOG="/tmp/biomodstack_api.log"
API_LOG_DIR="/tmp/biomodstack_api_logs"
MAX_LOGS=10
API_RELOAD_RAW="${BMS_API_RELOAD:-0}"
CPU_POWER_STRICT_RAW="${BMS_CPU_POWER_STRICT:-1}"
RAPL_ENERGY_PATH="${BMS_CPU_POWER_RAPL_PATH:-/sys/class/powercap/intel-rapl:0/energy_uj}"
SUDO_PASSWORD="${BMS_SUDO_PASSWORD:-}"
SUDO_SESSION_PRIMED=0

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

run_privileged_shell() {
    local cmd=$1
    if command -v sudo >/dev/null 2>&1 && ensure_sudo_session; then
        sudo -n /bin/bash -lc "$cmd"
    else
        pkexec /bin/bash -lc "$cmd"
    fi
}

ensure_sudo_session() {
    if [ "$SUDO_SESSION_PRIMED" -eq 1 ]; then
        return 0
    fi
    if ! command -v sudo >/dev/null 2>&1; then
        return 1
    fi
    if sudo -n true >/dev/null 2>&1; then
        SUDO_SESSION_PRIMED=1
        return 0
    fi
    if [ -z "$SUDO_PASSWORD" ]; then
        return 1
    fi
    printf '%s\n' "$SUDO_PASSWORD" | sudo -S -p '' -v >/dev/null 2>&1 || return 1
    SUDO_SESSION_PRIMED=1
    return 0
}

ensure_api_log_writable() {
    if [ -e "$API_LOG" ] && [ ! -w "$API_LOG" ] && { [ -n "$SUDO_PASSWORD" ] || command -v pkexec >/dev/null 2>&1; }; then
        echo "[API] Reclaiming API log ownership"
        run_privileged_shell "rm -f '$API_LOG'"
    fi
    touch "$API_LOG" 2>/dev/null || true
    chmod 0644 "$API_LOG" 2>/dev/null || true
}

port_8000_in_use() {
    ss -ltn "( sport = :8000 )" 2>/dev/null | tail -n +2 | grep -q .
}

wait_for_port_8000_clear() {
    local attempts=${1:-20}
    local delay=${2:-0.5}
    local i
    for ((i = 0; i < attempts; i++)); do
        if ! port_8000_in_use; then
            return 0
        fi
        sleep "$delay"
    done
    return 1
}

force_clear_port_8000() {
    fuser -k -n tcp 8000 >/dev/null 2>&1 || true
    pkill -9 -f "uvicorn.*main:app" 2>/dev/null || true
    if port_8000_in_use && { [ -n "$SUDO_PASSWORD" ] || command -v pkexec >/dev/null 2>&1; }; then
        echo "[API] Escalating to clear privileged listener on port 8000"
        run_privileged_shell 'fuser -k -n tcp 8000 >/dev/null 2>&1 || true; pkill -9 -f "uvicorn.*main:app" >/dev/null 2>&1 || true'
    fi
}

wait_for_api_health() {
    local attempts=${1:-30}
    local delay=${2:-1}
    local i
    for ((i = 0; i < attempts; i++)); do
        if curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$delay"
    done
    return 1
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
    if ! command -v sudo >/dev/null 2>&1 && ! command -v pkexec >/dev/null 2>&1; then
        echo "❌ pkexec is required for accurate CPU power telemetry"
        return 1
    fi

    echo "[API] Authenticating API launch for accurate CPU RAPL telemetry..."
    if command -v sudo >/dev/null 2>&1 && ensure_sudo_session; then
        sudo -n env \
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
    else
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
    fi
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
force_clear_port_8000
if ! wait_for_port_8000_clear 20 0.5; then
    echo "[API] Port 8000 still busy after initial shutdown; forcing one more cleanup pass"
    force_clear_port_8000
    wait_for_port_8000_clear 20 0.5 || {
        echo "[API] Port 8000 did not clear in time"
        notify-send "BioModStack" "❌ API restart aborted: port 8000 still busy" -i dialog-error
        exit 1
    }
fi

# Start API
cd "$PROJECT_DIR/platform/api"
ensure_api_log_writable
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

if wait_for_api_health 20 1; then
    notify-send "BioModStack" "✅ API restarted successfully (PID: $API_PID)" -i dialog-ok
elif grep -q "Address already in use" "$API_LOG" 2>/dev/null; then
    echo "[API] Initial restart hit address-in-use; retrying once after cleanup"
    force_clear_port_8000
    wait_for_port_8000_clear 20 0.5 || true
    nohup bash -lc "$(build_api_launch_wrapper)" > "$API_LOG" 2>&1 &
    API_PID=$!
    if wait_for_api_health 20 1; then
        notify-send "BioModStack" "✅ API restarted successfully (PID: $API_PID)" -i dialog-ok
    else
        notify-send "BioModStack" "❌ API failed to restart after retry! Check logs: $API_LOG" -i dialog-error
        exit 1
    fi
else
    notify-send "BioModStack" "❌ API failed to restart! Check logs: $API_LOG" -i dialog-error
    exit 1
fi
