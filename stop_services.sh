#!/bin/bash
# BioModStack STOP - Stop all services (API + Frontend)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"

notify-send "BioModStack" "🛑 Stopping ALL services..." -i dialog-warning

"$PROJECT_DIR/start_ui.sh" stop

notify-send "BioModStack" "✅ All services stopped." -i dialog-information
