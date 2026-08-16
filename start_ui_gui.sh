#!/bin/bash
# BioModStack browser launcher - restarts services and opens the hosted web UI.
# For the Electron shell, use ./start_ui_electron.sh instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
RUNTIME_MODE="${BMS_RUNTIME_MODE:-container}"
FORWARDED_ARGS=()

while [ "$#" -gt 0 ]; do
    case "$1" in
        --runtime)
            if [ "$#" -lt 2 ]; then
                echo "start_ui_gui.sh: --runtime requires a value" >&2
                exit 2
            fi
            RUNTIME_MODE="$2"
            FORWARDED_ARGS+=("$1" "$2")
            shift 2
            ;;
        --runtime=*)
            RUNTIME_MODE="${1#*=}"
            FORWARDED_ARGS+=("$1")
            shift
            ;;
        *)
            FORWARDED_ARGS+=("$1")
            shift
            ;;
    esac
done

notify() {
    if command -v notify-send >/dev/null 2>&1; then
        notify-send "BioModStack" "$1" -i applications-science || true
    fi
}

browser_url="http://localhost:${BMS_WEB_HOST_PORT:-18080}/bms/"
case "${RUNTIME_MODE,,}" in
    dev)
        dev_web_port="${BMS_DEV_WEB_HOST_PORT:-18082}"
        # Desktop sessions can retain this exact pre-governance export for
        # hours after the listener moved.  Never reopen the retired endpoint.
        if [ "$dev_web_port" = "5173" ]; then
            dev_web_port="18082"
        fi
        browser_url="http://localhost:${dev_web_port}/"
        ;;
    container)
        ;;
    *)
        ;;
esac

notify "♻️  Restarting ALL services (API + UI)..."
bash "$PROJECT_DIR/start_ui.sh" restart "${FORWARDED_ARGS[@]}"

sleep 5

notify "✅ Services restarted! Opening UI..."
xdg_open_path="$(command -v xdg-open || true)"
if [ -n "$xdg_open_path" ]; then
    if head -c 2 "$xdg_open_path" 2>/dev/null | grep -q '^#!'; then
        bash "$xdg_open_path" "$browser_url" || true
    else
        "$xdg_open_path" "$browser_url" || true
    fi
fi
