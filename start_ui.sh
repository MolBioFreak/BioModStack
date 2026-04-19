#!/bin/bash
# BioModStack UI Service Manager
# Usage: ./start_ui.sh [start|stop|status|restart]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
MANAGER="$PROJECT_DIR/scripts/manage_desktop_services.py"
ACTION="${1:-start}"

case "$ACTION" in
    start|stop|status|restart)
        exec python3 "$MANAGER" "$ACTION"
        ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        exit 1
        ;;
 esac
