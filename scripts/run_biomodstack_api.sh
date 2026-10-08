#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

if [ -f "$HOME/.biomodstack/env.sh" ]; then
    source "$HOME/.biomodstack/env.sh"
fi

API_RELOAD_RAW="${BMS_API_RELOAD:-1}"
CPU_POWER_STRICT_RAW="${BMS_CPU_POWER_STRICT:-1}"
RAPL_ENERGY_PATH="${BMS_CPU_POWER_RAPL_PATH:-/sys/class/powercap/intel-rapl:0/energy_uj}"

export BMS_HOME="$PROJECT_DIR"
export BMS_INPUTS="${BMS_INPUTS:-$PROJECT_DIR/inputs}"
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

if ! command -v uv >/dev/null 2>&1; then
    echo "BioModStack API launcher requires uv on PATH" >&2
    exit 1
fi

if [ -e "$RAPL_ENERGY_PATH" ] && [ ! -r "$RAPL_ENERGY_PATH" ] && cpu_power_strict_enabled; then
    echo "BioModStack API service cannot read $RAPL_ENERGY_PATH as the unprivileged user."
    echo "Move RAPL access into a dedicated privileged helper or set BMS_CPU_POWER_STRICT=0 for workstation runtime." >&2
    exit 1
fi

cd "$PROJECT_DIR/platform/api"
cmd=(uv run uvicorn main:app --port 8000 --host 127.0.0.1)
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

exec "${cmd[@]}"
