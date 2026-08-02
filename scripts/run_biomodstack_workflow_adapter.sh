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

SYSTEMD_AUTHORITY_KEYS=(
    BMS_BUILD_SHA
    BMS_TAILNET_CONTROL_SOURCE_REVISION
    BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS
    BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS
    BMS_WORKFLOW_ADAPTER_BIND_HOST
    BMS_WORKFLOW_ADAPTER_PORT
    BMS_FEATURE_MOLECULAR_DYNAMICS
    BMS_MD_ANALYSIS_ENABLED
    BMS_MD_ANALYSIS_CONTAINER
    BMS_MD_ANALYSIS_SIF_SHA256
    BMS_MD_ANALYSIS_IMPLEMENTATION_SHA256
)
declare -A SYSTEMD_AUTHORITY_ENV=()
for key in "${SYSTEMD_AUTHORITY_KEYS[@]}"; do
    if [[ -v "$key" ]]; then
        SYSTEMD_AUTHORITY_ENV["$key"]="${!key}"
    fi
done

restore_systemd_authority_environment() {
    local key
    for key in "${!SYSTEMD_AUTHORITY_ENV[@]}"; do
        export "$key=${SYSTEMD_AUTHORITY_ENV[$key]}"
    done
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
restore_systemd_authority_environment
pin_nextflow_java

# Keep BioModStack dependency resolution isolated from shared caches that may
# have been populated by containers or root-owned maintenance jobs.
export UV_CACHE_DIR="${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/biomodstack/uv}"
mkdir -p "$UV_CACHE_DIR"

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
export BMS_CPU_POWER_COLLECTOR_URL="${BMS_CPU_POWER_COLLECTOR_URL:-http://127.0.0.1:18797/power}"
BMS_WORKFLOW_ADAPTER_BIND_HOST="${BMS_WORKFLOW_ADAPTER_BIND_HOST:-127.0.0.1}"
BMS_WORKFLOW_ADAPTER_PORT="${BMS_WORKFLOW_ADAPTER_PORT:-18001}"
CM_API_RUNTIME_DIR="${BMS_CM_API_RUNTIME_DIR:-${BMS_DATA:-/mnt/BioModStack}/runtime/cm-api-python}"

rewrite_cm_api_pyvenv_home() {
    local config_path="$1" runtime_bin="$2"
    python3 - "$config_path" "$runtime_bin" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
runtime_bin = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
if not any(line.startswith("home = ") for line in lines):
    raise SystemExit(f"missing venv home declaration: {path}")
path.write_text(
    "\n".join(
        f"home = {runtime_bin}" if line.startswith("home = ") else line
        for line in lines
    ) + "\n",
    encoding="utf-8",
)
PY
}

provision_cm_api_runtime() {
    local source_venv="$PROJECT_DIR/platform/api/.venv"
    local source_python source_runtime stage runtime_name runtime_dir target_python next_link
    mkdir -p "$CM_API_RUNTIME_DIR/releases"
    exec 9>"${CM_API_RUNTIME_DIR}/.provision.lock"
    flock -x 9

    source_python="$(readlink -f "$source_venv/bin/python")"
    source_runtime="$(dirname "$(dirname "$source_python")")"
    [ -x "$source_python" ] || { echo "locked API interpreter is unavailable: $source_python" >&2; return 1; }

    stage="$(mktemp -d "${CM_API_RUNTIME_DIR}/.stage.XXXXXX")"
    runtime_name="runtime-${stage##*.stage.}"
    runtime_dir="$CM_API_RUNTIME_DIR/releases/$runtime_name"
    trap 'test -z "$stage" || rm -rf "$stage"' RETURN
    cp -a "$source_runtime" "$stage/python-runtime"
    cp -a "$source_venv" "$stage/venv"
    target_python="$runtime_dir/python-runtime/bin/$(basename "$source_python")"
    [ -x "$stage/python-runtime/bin/$(basename "$source_python")" ] || { echo "copied API interpreter is unavailable: $target_python" >&2; return 1; }
    for link in python python3 python3.12; do
        rm -f "$stage/venv/bin/$link"
        ln -s "$target_python" "$stage/venv/bin/$link"
    done
    rewrite_cm_api_pyvenv_home "$stage/venv/pyvenv.cfg" "$runtime_dir/python-runtime/bin"

    if [ -e "$runtime_dir" ] || [ -L "$runtime_dir" ]; then
        echo "refusing to replace existing CM API runtime generation: $runtime_dir" >&2
        return 1
    fi
    mv -T "$stage" "$runtime_dir"
    stage="$runtime_dir"
    apptainer exec --no-home --bind "$CM_API_RUNTIME_DIR:$CM_API_RUNTIME_DIR" \
        "${BMS_CONTAINER_DIR:-${BMS_DATA:-/mnt/BioModStack}/apptainer}/protenix.sif" \
        "$runtime_dir/venv/bin/python" -c 'import jsonschema'

    next_link="${CM_API_RUNTIME_DIR}/.current.${runtime_name}"
    ln -s "releases/$runtime_name" "$next_link"
    mv -Tf "$next_link" "$CM_API_RUNTIME_DIR/current"
    stage=""
    trap - RETURN
}

cd "$PROJECT_DIR/platform/api"
uv sync --locked
provision_cm_api_runtime
export BMS_CM_API_RUNTIME_DIR
export BMS_API_PYTHON="$CM_API_RUNTIME_DIR/current/venv/bin/python"
exec uv run --no-sync uvicorn workflow_adapter_app:app --port "$BMS_WORKFLOW_ADAPTER_PORT" --host "$BMS_WORKFLOW_ADAPTER_BIND_HOST" --no-proxy-headers --no-access-log
