#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"
source "$SCRIPT_DIR/configuration_read_guard.sh"

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
declare -A _BMS_LAUNCH_ENV=()
while IFS= read -r key; do
    _BMS_LAUNCH_ENV["$key"]="${!key}"
done < <(compgen -A variable BMS_)

if [ -f "$HOME/.biomodstack/env.sh" ]; then
    source "$HOME/.biomodstack/env.sh"
fi
bms_configuration_read_finish
for key in "${!_BMS_LAUNCH_ENV[@]}"; do
    export "$key=${_BMS_LAUNCH_ENV[$key]}"
done

if [ "${BMS_RUNTIME_MODE,,}" = "dev" ]; then
    export BMS_CORE_RUNTIME_MODE=0
    export BMS_WORKFLOW_ADAPTER_LANE=development
    export BMS_WORKFLOW_ADAPTER_URL=http://127.0.0.1:18001
fi

pin_nextflow_java

export UV_CACHE_DIR="${UV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/biomodstack/uv}"
mkdir -p "$UV_CACHE_DIR"

export BMS_HOME="$PROJECT_DIR"
export BMS_NEXTFLOW_VERSION="${BMS_NEXTFLOW_VERSION:-25.10.1}"
if [[ -z "${BMS_NEXTFLOW_BIN:-}" ]]; then
  managed_nextflow="${HOME}/.local/lib/nextflow/${BMS_NEXTFLOW_VERSION}/nextflow"
  if [[ -x "$managed_nextflow" ]]; then
    export BMS_NEXTFLOW_BIN="$managed_nextflow"
  fi
fi
export BMS_INPUTS="${BMS_INPUTS:-$PROJECT_DIR/inputs}"

source "$SCRIPT_DIR/python_runtime_guard.sh"
bms_python_runtime_resolve || exit 78

if [ -z "$BMS_MANAGED_PYTHON" ] && ! command -v uv >/dev/null 2>&1; then
    echo "BioModStack worker launcher requires uv on PATH" >&2
    exit 1
fi

cd "$PROJECT_DIR/platform/api"

if [ -n "$BMS_MANAGED_PYTHON" ]; then
    "$BMS_MANAGED_PYTHON" run_migrations.py
    exec "$BMS_MANAGED_PYTHON" worker.py
else
    worker_python="$(uv run --frozen python -c 'import sys; print(sys.executable)')"
    export VIRTUAL_ENV="$(dirname "$(dirname "$worker_python")")"
    export PATH="$(dirname "$worker_python"):$PATH"
    "$worker_python" run_migrations.py
    exec "$worker_python" worker.py
fi
