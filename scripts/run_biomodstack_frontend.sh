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

FRONTEND_MODE_RAW="${BMS_FRONTEND_MODE:-dev}"

frontend_mode() {
    printf '%s' "${FRONTEND_MODE_RAW,,}"
}

cd "$PROJECT_DIR/platform/frontend"
case "$(frontend_mode)" in
    dev)
        exec npm run dev -- --host 127.0.0.1 --port "${BMS_DEV_WEB_HOST_PORT:-18082}"
        ;;
    prod)
        echo "BioModStack frontend production mode is provided by the bms-web container." >&2
        echo "Use scripts/run_biomodstack_core_runtime.sh or ./start_ui.sh start --runtime container." >&2
        exit 2
        ;;
    *)
        echo "Unknown BMS_FRONTEND_MODE='$FRONTEND_MODE_RAW' (expected dev or prod)" >&2
        exit 1
        ;;
esac
