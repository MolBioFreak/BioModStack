#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
export PATH="$HOME/bin:$HOME/.cargo/bin:$HOME/.local/bin:$PATH"

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

# Explicit launcher/service-manager environment wins over the compatibility file.
# Capture it before sourcing ~/.biomodstack/env.sh, which is shared with the
# container runtime and can otherwise overwrite the dev data/runtime boundary.
declare -A _BMS_LAUNCH_ENV=()
while IFS= read -r key; do
    _BMS_LAUNCH_ENV["$key"]="${!key}"
done < <(compgen -A variable BMS_)

if [ -f "$HOME/.biomodstack/env.sh" ]; then
    source "$HOME/.biomodstack/env.sh"
fi
for key in "${!_BMS_LAUNCH_ENV[@]}"; do
    export "$key=${_BMS_LAUNCH_ENV[$key]}"
done

# The compatibility env file also contains settings used by the container
# runtime.  A systemd-owned native dev API must never inherit those settings:
# otherwise GPU/CPU/RAM telemetry and workflow launches are silently proxied to
# the optional container workflow adapter.  Re-assert the selected runtime
# boundary after sourcing compatibility configuration.
if [ "${BMS_RUNTIME_MODE,,}" = "dev" ]; then
    export BMS_CORE_RUNTIME_MODE=0
    export BMS_WORKFLOW_ADAPTER_LANE=development
    export BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:18001
fi

# Native Development shares the pinned pLannotate micromamba environment with
# the container runtime, but it does not run inside the pLannotate-enabled API
# image. Discover the user-owned micromamba installation without hard-coding a
# host path into the API or requiring generated env.sh to carry this setting.
if [ -z "${BMS_MICROMAMBA_BIN:-}" ]; then
    if micromamba_bin="$(command -v micromamba 2>/dev/null)"; then
        export BMS_MICROMAMBA_BIN="$micromamba_bin"
    fi
fi
if [ -n "${BMS_MICROMAMBA_BIN:-}" ] && [ -z "${BMS_MICROMAMBA_ROOT_PREFIX:-}" ]; then
    micromamba_root="$("$BMS_MICROMAMBA_BIN" info --base 2>/dev/null || true)"
    micromamba_root="${micromamba_root#"${micromamba_root%%[![:space:]]*}"}"
    micromamba_root="${micromamba_root#base environment : }"
    micromamba_root="${micromamba_root%"${micromamba_root##*[![:space:]]}"}"
    if [[ "$micromamba_root" == /* ]]; then
        export BMS_MICROMAMBA_ROOT_PREFIX="$micromamba_root"
    fi
fi
export BMS_PLANNOTATE_ENV="${BMS_PLANNOTATE_ENV:-plannotate}"

pin_nextflow_java

# Keep BioModStack dependency resolution isolated from shared caches that may
# have been populated by containers or root-owned maintenance jobs.
export UV_CACHE_DIR="${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/biomodstack/uv}"
mkdir -p "$UV_CACHE_DIR"

API_MODE_RAW="${BMS_API_MODE:-dev}"
API_RELOAD_RAW="${BMS_API_RELOAD:-0}"
CPU_POWER_STRICT_RAW="${BMS_CPU_POWER_STRICT:-1}"
RAPL_ENERGY_PATH="${BMS_CPU_POWER_RAPL_PATH:-/sys/class/powercap/intel-rapl:0/energy_uj}"

export BMS_HOME="$PROJECT_DIR"
export BMS_NEXTFLOW_VERSION="${BMS_NEXTFLOW_VERSION:-25.10.1}"
if [[ -z "${BMS_NEXTFLOW_BIN:-}" ]]; then
  managed_nextflow="${HOME}/.local/lib/nextflow/${BMS_NEXTFLOW_VERSION}/nextflow"
  if [[ -x "$managed_nextflow" ]]; then
    export BMS_NEXTFLOW_BIN="$managed_nextflow"
  fi
fi
export BMS_INPUTS="${BMS_INPUTS:-$PROJECT_DIR/inputs}"
export BMS_FAN_CONTROL_BACKEND="${BMS_FAN_CONTROL_BACKEND:-coolercontrol}"

api_mode() {
    printf '%s' "${API_MODE_RAW,,}"
}

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
bms_api_port="${BMS_API_BIND_PORT:-${BMS_DEV_API_HOST_PORT:-18002}}"
export API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:${bms_api_port}}"

# Apply every registered forward migration before the API can accept traffic.
# A migration failure aborts the managed start and preserves the previous owner.
uv run --frozen python run_migrations.py

cmd=(uv run uvicorn main:app --port "$bms_api_port" --host 127.0.0.1 --no-access-log)
case "$(api_mode)" in
    dev)
        if api_reload_enabled; then
            cmd+=(
                --reload
                --reload-dir "$PROJECT_DIR/platform/api"
                --reload-exclude ".pytest_cache/*"
                --reload-exclude "tests/*"
                --reload-exclude "inputs/*"
                --reload-exclude "*.db"
                --reload-exclude "__pycache__/*"
                --reload-exclude ".venv/*"
            )
        fi
        ;;
    prod)
        ;;
    *)
        echo "Unknown BMS_API_MODE='$API_MODE_RAW' (expected dev or prod)" >&2
        exit 1
        ;;
esac

exec "${cmd[@]}"
