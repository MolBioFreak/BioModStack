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

if ! command -v uv >/dev/null 2>&1; then
    echo "BioModStack workflow adapter launcher requires uv on PATH" >&2
    exit 1
fi

export BMS_HOME="$PROJECT_DIR"
unset BMS_WORKFLOW_ADAPTER_URL
export BMS_CORE_RUNTIME_MODE=0
BMS_WORKFLOW_ADAPTER_BIND_HOST="${BMS_WORKFLOW_ADAPTER_BIND_HOST:-0.0.0.0}"

cd "$PROJECT_DIR/platform/api"
exec uv run uvicorn workflow_adapter_app:app --port 8001 --host "$BMS_WORKFLOW_ADAPTER_BIND_HOST"
