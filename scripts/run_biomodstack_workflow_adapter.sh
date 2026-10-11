#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"
source "$SCRIPT_DIR/configuration_read_guard.sh"

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
    BMS_WORKFLOW_ADAPTER_LANE
    BMS_STATE_DIR
    BMS_DB_PATH
    BMS_WORK
    BMS_RESULTS_DIR
    BMS_RESULTS_ROOT
    BMS_DATA
    BMS_INPUTS
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

# A generated user unit is the authority for the lane and all mutable roots.
# Preserve its explicit BMS_* values across compatibility/profile files.
declare -A _BMS_LAUNCH_ENV=()
while IFS= read -r key; do
    _BMS_LAUNCH_ENV["$key"]="${!key}"
done < <(compgen -A variable BMS_)

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
for key in "${!_BMS_LAUNCH_ENV[@]}"; do
    export "$key=${_BMS_LAUNCH_ENV[$key]}"
done
bms_configuration_read_finish
restore_systemd_authority_environment
pin_nextflow_java

# Keep BioModStack dependency resolution isolated from shared caches that may
# have been populated by containers or root-owned maintenance jobs.
export UV_CACHE_DIR="${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/biomodstack/uv}"
mkdir -p "$UV_CACHE_DIR"

source "$SCRIPT_DIR/python_runtime_guard.sh"
bms_python_runtime_resolve || exit $?

export BMS_HOME="$PROJECT_DIR"
unset BMS_WORKFLOW_ADAPTER_URL
export BMS_CORE_RUNTIME_MODE=0
BMS_WORKFLOW_ADAPTER_LANE="${BMS_WORKFLOW_ADAPTER_LANE:-}"
case "${BMS_WORKFLOW_ADAPTER_LANE,,}" in
    development)
        expected_adapter_port=18001
        ;;
    production)
        expected_adapter_port=18101
        ;;
    *)
        echo "BioModStack workflow adapter is blocked: BMS_WORKFLOW_ADAPTER_LANE must be development or production." >&2
        exit 78
        ;;
esac
BMS_WORKFLOW_ADAPTER_PORT="${BMS_WORKFLOW_ADAPTER_PORT:-$expected_adapter_port}"
if [ "$BMS_WORKFLOW_ADAPTER_PORT" != "$expected_adapter_port" ]; then
    echo "BioModStack workflow adapter is blocked: port $BMS_WORKFLOW_ADAPTER_PORT does not belong to lane $BMS_WORKFLOW_ADAPTER_LANE." >&2
    exit 78
fi
for required_root in BMS_STATE_DIR BMS_DB_PATH BMS_WORK; do
    if [ -z "${!required_root:-}" ] || [[ "${!required_root}" != /* ]]; then
        echo "BioModStack workflow adapter is blocked: $required_root must be an explicit absolute lane-local root." >&2
        exit 78
    fi
done
BMS_RESULTS_DIR="${BMS_RESULTS_DIR:-${BMS_RESULTS_ROOT:-}}"
if [ -z "$BMS_RESULTS_DIR" ] || [[ "$BMS_RESULTS_DIR" != /* ]]; then
    echo "BioModStack workflow adapter is blocked: BMS_RESULTS_DIR must be an explicit absolute lane-local root." >&2
    exit 78
fi
export BMS_WORKFLOW_ADAPTER_LANE BMS_WORKFLOW_ADAPTER_PORT BMS_STATE_DIR BMS_DB_PATH BMS_WORK BMS_RESULTS_DIR
export BMS_RESULTS_ROOT="$BMS_RESULTS_DIR"
BMS_NEXTFLOW_HOME="${BMS_NEXTFLOW_HOME:-${BMS_DATA:-/mnt/BioModStack}/nextflow}"
export BMS_NEXTFLOW_HOME
export NXF_HOME="${NXF_HOME:-$BMS_NEXTFLOW_HOME}"
mkdir -p "$NXF_HOME"
export BMS_CPU_POWER_COLLECTOR_URL="${BMS_CPU_POWER_COLLECTOR_URL:-http://127.0.0.1:18797/power}"
BMS_WORKFLOW_ADAPTER_BIND_HOST="${BMS_WORKFLOW_ADAPTER_BIND_HOST:-127.0.0.1}"
CM_API_RUNTIME_DIR="${BMS_CM_API_RUNTIME_DIR:-${BMS_DATA:-/mnt/BioModStack}/runtime/cm-api-python}"

# This subshell owns the existing lock and every unpublished stage. EXIT runs
# under errexit (RETURN did not); signals leave the old selected version intact.
provision_cm_api_runtime() (
    set -euo pipefail
    local source_venv="$API_SOURCE_VENV"
    local source_python source_runtime stage="" runtime_dir payload_id owned="" pending
    cleanup_cm_api_runtime() {
        if [ -n "$stage" ] && [ -d "$stage" ]; then
            chmod -R u+w "$stage"
            rm -rf -- "$stage"
        fi
        if [ -n "$owned" ] && [ -d "$owned" ]; then
            chmod -R u+w "$owned"
            rm -rf -- "$owned"
        fi
        exec 9>&-
    }
    trap cleanup_cm_api_runtime EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    mkdir -p "$CM_API_RUNTIME_DIR/releases"
    # Only this owner's unpublished stage prefix is recoverable. Never prune
    # published versions by count, process FDs, timestamps or current selection.
    for stage in "$CM_API_RUNTIME_DIR"/.stage.*; do
        [ -d "$stage" ] && [ ! -L "$stage" ] || continue
        chmod -R u+w "$stage"
        rm -rf -- "$stage"
    done
    stage=""
    for pending in "$CM_API_RUNTIME_DIR"/releases/runtime-*/.pending-support-payload.json; do
        [ -f "$pending" ] || continue
        local incomplete="${pending%/.pending-support-payload.json}"
        if [ ! -L "$incomplete" ] && [ ! -e "$incomplete/support-payload.json" ]; then
            chmod -R u+w "$incomplete"
            rm -rf -- "$incomplete"
        fi
    done
    source_venv="$(cd "$source_venv" && pwd)"
    source_python="$(readlink -f "$source_venv/bin/python")"
    [ -x "$source_python" ] || { echo "locked API interpreter is unavailable: $source_python" >&2; exit 1; }
    source_runtime="$(python3 "$SCRIPT_DIR/cm_api_support_runtime.py" python-runtime "$source_python")"
    payload_id="$(python3 "$SCRIPT_DIR/cm_api_support_runtime.py" receipt "$source_venv" "$source_runtime" "$PROJECT_DIR" "${sync_log:-}")"
    runtime_dir="$CM_API_RUNTIME_DIR/releases/runtime-$payload_id"
    if [ ! -f "$runtime_dir/support-payload.json" ]; then
        # An interrupted publication with no completion marker is our private
        # incomplete target, not a retained completed runtime.
        if [ -d "$runtime_dir" ]; then
            chmod -R u+w "$runtime_dir"
            rm -rf -- "$runtime_dir"
        fi
        stage="$(mktemp -d "${CM_API_RUNTIME_DIR}/.stage.XXXXXX")"
        cp -a "$source_runtime" "$stage/python-runtime"
        cp -a "$source_venv" "$stage/venv"
        python3 "$SCRIPT_DIR/cm_api_support_runtime.py" prepare "$stage" "$runtime_dir" "$source_venv" "$source_runtime"
        # Keep completion outside the visible payload until the existing probe
        # succeeds. Both publication and failure cleanup remain lock-owned.
        chmod u+w "$stage"
        mv "$stage/support-payload.json" "$stage/.pending-support-payload.json"
        mv -T "$stage" "$runtime_dir"
        stage=""
        owned="$runtime_dir"
    fi
    apptainer exec --no-home --bind "$CM_API_RUNTIME_DIR:$CM_API_RUNTIME_DIR:ro" \
        "$CM_API_SUPPORT_IMAGE" "$runtime_dir/venv/bin/python" -B -c 'import jsonschema' >&2
    if [ -n "$owned" ]; then
        mv "$runtime_dir/.pending-support-payload.json" "$runtime_dir/support-payload.json"
        chmod 555 "$runtime_dir"
        owned=""
    fi
    # An old current may be embedded in queued/resume commands: never retarget.
    if [ ! -e "$CM_API_RUNTIME_DIR/current" ] && [ ! -L "$CM_API_RUNTIME_DIR/current" ]; then
        ln -s "releases/runtime-$payload_id" "$CM_API_RUNTIME_DIR/current"
    fi
    python3 "$SCRIPT_DIR/cm_api_support_runtime.py" published "$source_venv" "$runtime_dir"
    printf '%s\n' "$runtime_dir"
)

cd "$PROJECT_DIR/platform/api"
sync_log=""
image_status=0
CM_API_SUPPORT_IMAGE="$(python3 "$SCRIPT_DIR/cm_api_support_runtime.py" image "$PROJECT_DIR")" || image_status=$?
if [ -n "$CM_API_SUPPORT_IMAGE" ] && [ "$image_status" = 0 ]; then
    mkdir -p "$CM_API_RUNTIME_DIR"
    exec 9>"$CM_API_RUNTIME_DIR/.provision.lock"
    flock -x 9
fi
trap 'test -z "$sync_log" || rm -f -- "$sync_log"; exec 9>&-' EXIT
if [ -n "$BMS_MANAGED_PYTHON" ]; then
    API_SOURCE_VENV="${BMS_MANAGED_PYTHON_ROOT}/environment"
else
    if ! command -v uv >/dev/null 2>&1; then
        echo "BioModStack workflow adapter launcher requires uv on PATH" >&2
        exit 1
    fi
    # Serialize the legacy mutation boundary as well as its receipt/publication.
    # No change to supported uv flags or default dependency-group selection.
    sync_log="$(mktemp "$UV_CACHE_DIR/.support-sync.XXXXXX")"
    if uv sync --locked 2> "$sync_log"; then
        cat "$sync_log" >&2
    else
        sync_status=$?
        cat "$sync_log" >&2
        exit "$sync_status"
    fi
    API_SOURCE_VENV="${UV_PROJECT_ENVIRONMENT:-$PROJECT_DIR/platform/api/.venv}"
fi
[ "$image_status" = 0 ] || exit "$image_status"
if [ -n "$CM_API_SUPPORT_IMAGE" ]; then
    selected_runtime="$(provision_cm_api_runtime)"
    export BMS_CM_API_RUNTIME_DIR="$CM_API_RUNTIME_DIR"
    export BMS_API_PYTHON="$selected_runtime/venv/bin/python"
    export BMS_SUPPORT_PAYLOAD_RECEIPT="$selected_runtime/support-payload.json"
    if [ -n "$BMS_MANAGED_PYTHON" ]; then
        BMS_MANAGED_PYTHON="$BMS_API_PYTHON"
    fi
elif [ "${BMS_WORKFLOW_ADAPTER_LANE,,}" = development ]; then
    # The host control plane does not require optional scientific containers.
    # Do not advertise a stale/unprobed portable interpreter. Model admission
    # continues to require the existing registered runtime and attestation.
    unset BMS_API_PYTHON BMS_SUPPORT_PAYLOAD_RECEIPT
    echo "CM support runtime unprovisioned: no installed/selected Protenix or Frustra image; scientific runtime admission remains required." >&2
else
    echo "BioModStack workflow adapter is blocked: CM support image is unprovisioned." >&2
    exit 78
fi
if [ -n "$sync_log" ]; then
    rm -f -- "$sync_log"
    sync_log=""
fi
exec 9>&-
trap - EXIT
if [ -n "$BMS_MANAGED_PYTHON" ]; then
    exec "$BMS_MANAGED_PYTHON" -m uvicorn workflow_adapter_app:app --port "$BMS_WORKFLOW_ADAPTER_PORT" --host "$BMS_WORKFLOW_ADAPTER_BIND_HOST" --no-proxy-headers --no-access-log
fi
exec uv run --no-sync uvicorn workflow_adapter_app:app --port "$BMS_WORKFLOW_ADAPTER_PORT" --host "$BMS_WORKFLOW_ADAPTER_BIND_HOST" --no-proxy-headers --no-access-log
