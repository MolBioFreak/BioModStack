#!/bin/bash
# BioModStack UI Service Manager
# Usage: ./start_ui.sh [start|start-api|start-target|select-tailnet|stop|stop-api|status|restart|restart-api] [--runtime dev|container] [--target dev|prod|both] [--environment development|production]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
MANAGER="$PROJECT_DIR/scripts/manage_desktop_services.py"
ACTION="${1:-start}"
if [ "$#" -gt 0 ]; then
    shift
fi

case "$ACTION" in
    start|start-api|start-target|select-tailnet|stop|stop-api|status|restart|restart-api)
        exec python3 "$MANAGER" "$ACTION" "$@"
        ;;
    *)
        echo "Usage: $0 {start|start-api|start-target|select-tailnet|stop|stop-api|status|restart|restart-api} [--runtime dev|container] [--target dev|prod|both] [--environment development|production]"
        exit 1
        ;;
 esac
