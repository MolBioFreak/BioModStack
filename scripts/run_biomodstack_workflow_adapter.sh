#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
export PATH="/usr/local/bin:/usr/bin:/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

pin_nextflow_java() {
    local candidate="${BMS_NEXTFLOW_JAVA_HOME:-}"
    if [ -z "$candidate" ] && [ -x "$HOME/.local/jdks/temurin-17/bin/java" ]; then
        candidate="$HOME/.local/jdks/temurin-17"
    fi
    if [ -n "$candidate" ] && [ -x "$candidate/bin/java" ]; then
        export BMS_NEXTFLOW_JAVA_HOME="$candidate"
        export JAVA_HOME="$candidate"
        export PATH="$candidate/bin:$PATH"
    fi
}

if [ -f "$HOME/.biomodstack/env.sh" ]; then
    source "$HOME/.biomodstack/env.sh"
fi
pin_nextflow_java

load_env_file_overrides() {
    local env_file="$1"
    [ -f "$env_file" ] || return 0

    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*)
                continue
                ;;
        esac
        local key="${line%%=*}"
        local value="${line#*=}"
        if [ -z "$key" ] || [ "$key" = "$line" ]; then
            continue
        fi
        export "$key=$value"
    done < "$env_file"
}

PROFILE_CORE_RUNTIME_ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/biomodstack/core-runtime.env"
LEGACY_CORE_RUNTIME_ENV_FILE="$PROJECT_DIR/.env.core-runtime.local"
CORE_RUNTIME_ENV_FILE="${BMS_CORE_RUNTIME_ENV_FILE:-$PROFILE_CORE_RUNTIME_ENV_FILE}"
if [ -f "$CORE_RUNTIME_ENV_FILE" ]; then
    load_env_file_overrides "$CORE_RUNTIME_ENV_FILE"
elif [ -f "$LEGACY_CORE_RUNTIME_ENV_FILE" ]; then
    load_env_file_overrides "$LEGACY_CORE_RUNTIME_ENV_FILE"
fi
pin_nextflow_java

if ! command -v uv >/dev/null 2>&1; then
    echo "BioModStack workflow adapter launcher requires uv on PATH" >&2
    exit 1
fi

export BMS_HOME="$PROJECT_DIR"
unset BMS_WORKFLOW_ADAPTER_URL
export BMS_CORE_RUNTIME_MODE=0
BMS_NEXTFLOW_HOME="${BMS_NEXTFLOW_HOME:-${BMS_DATA:-/mnt/BioModStack}/nextflow}"
export BMS_NEXTFLOW_HOME
export NXF_HOME="${NXF_HOME:-$BMS_NEXTFLOW_HOME}"
mkdir -p "$NXF_HOME"
export BMS_CPU_POWER_COLLECTOR_URL="${BMS_CPU_POWER_COLLECTOR_URL:-http://127.0.0.1:8797/power}"
BMS_WORKFLOW_ADAPTER_BIND_HOST="${BMS_WORKFLOW_ADAPTER_BIND_HOST:-127.0.0.1}"

cd "$PROJECT_DIR/platform/api"
exec uv run uvicorn workflow_adapter_app:app --port 8001 --host "$BMS_WORKFLOW_ADAPTER_BIND_HOST" --no-access-log
