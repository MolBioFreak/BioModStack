#!/bin/bash
# BioModStack UI Service Manager
# Usage: ./start_ui.sh [start|stop|status]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
API_LOG="/tmp/biomodstack_api.log"
FRONTEND_LOG="/tmp/biomodstack_frontend.log"
API_RELOAD_RAW="${BMS_API_RELOAD:-1}"
CPU_POWER_STRICT_RAW="${BMS_CPU_POWER_STRICT:-1}"
RAPL_ENERGY_PATH="${BMS_CPU_POWER_RAPL_PATH:-/sys/class/powercap/intel-rapl:0/energy_uj}"

# Load NVM if available to ensure correct Node version
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# Add uv to PATH (usually in $HOME/.cargo/bin or $HOME/.local/bin)
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

# Load BMS environment variables (paths for MSA databases, weights, etc.)
# Note: ~/.bashrc has a non-interactive guard, so we use a dedicated env file
if [ -f "$HOME/.biomodstack/env.sh" ]; then
    source "$HOME/.biomodstack/env.sh"
fi

# Keep runtime-generated inputs outside the watched API source tree.
export BMS_INPUTS="${BMS_INPUTS:-$PROJECT_DIR/inputs}"

# Fan-control backend defaults to CoolerControl unless explicitly overridden.
export BMS_FAN_CONTROL_BACKEND="${BMS_FAN_CONTROL_BACKEND:-coolercontrol}"

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

    echo "   Authenticating API launch for accurate CPU RAPL telemetry..."
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

check_port() {
    local port=$1
    if lsof -i :$port > /dev/null; then
        echo "   ⚠️  Port $port is still in use. Killing process..."
        fuser -k -n tcp $port > /dev/null 2>&1
    fi
}

start_services() {
    echo "🚀 Starting BioModStack services..."
    
    # Ensure uv is available
    if ! command -v uv &> /dev/null; then
        echo "❌ 'uv' not found. Please install uv or check your PATH."
        exit 1
    fi

    # Start API
    cd "$PROJECT_DIR/platform/api"
    # Check/Kill port 8000
    check_port 8000
    
    echo "   Starting API with uv..."
    API_CMD="$(build_api_cmd)"
    echo "   API command: $API_CMD"
    if rapl_requires_privileged_api_launch; then
        if cpu_power_strict_enabled; then
            API_PID="$(launch_api_with_rapl_caps "$API_CMD")" || exit 1
        else
            echo "   Warning: RAPL powercap is unreadable and strict CPU power is disabled."
            nohup bash -lc "$(build_api_launch_wrapper)" > "$API_LOG" 2>&1 &
            API_PID=$!
        fi
    else
        nohup bash -lc "$(build_api_launch_wrapper)" > "$API_LOG" 2>&1 &
        API_PID=$!
    fi
    echo "   API started (PID: $API_PID) → http://localhost:8000"
    
    sleep 3
    
    # Start Frontend
    cd "$PROJECT_DIR/platform/frontend"
    # Check/Kill port 5173
    check_port 5173
    
    echo "   Starting Frontend..."
    nohup npm run dev -- --host 127.0.0.1 --port 5173 > "$FRONTEND_LOG" 2>&1 &
    FRONTEND_PID=$!
    echo "   Frontend started (PID: $FRONTEND_PID) → http://localhost:5173/bms/"
    
    echo "✅ Services started! Logs at: $API_LOG, $FRONTEND_LOG"
}

stop_services() {
    echo "🛑 Stopping BioModStack services..."
    pkill -f "uvicorn.*main:app" 2>/dev/null && echo "   API stopped" || echo "   API not running"
    pkill -f "vite" 2>/dev/null && echo "   Frontend stopped" || echo "   Frontend not running"
    
    # Force kill ports if still open
    check_port 8000
    check_port 5173
    
    echo "✅ Services stopped"
}

status_services() {
    echo "📊 BioModStack Service Status:"
    if pgrep -f "uvicorn.*main:app" > /dev/null; then
        echo "   API: ✅ Running ($(pgrep -f 'uvicorn.*main:app'))"
    else
        echo "   API: ❌ Stopped"
    fi
    
    if pgrep -f "vite" > /dev/null; then
        echo "   Frontend: ✅ Running ($(pgrep -f 'vite'))"
    else
        echo "   Frontend: ❌ Stopped"
    fi
}

case "${1:-start}" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    status)
        status_services
        ;;
    restart)
        stop_services
        sleep 2
        start_services
        ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
