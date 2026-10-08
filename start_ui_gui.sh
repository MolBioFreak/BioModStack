#!/bin/bash
# BioModStack GUI Launcher - Starts services and opens browser

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
API_LOG="/tmp/biomodstack_api.log"
FRONTEND_LOG="/tmp/biomodstack_frontend.log"

# Load NVM if available to ensure correct Node version
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

# Always restart to ensure fresh state (as requested by user)
notify-send "BioModStack" "♻️  Restarting ALL services (API + UI)..." -i applications-science

# Run restart logic
"$PROJECT_DIR/start_ui.sh" restart

# Wait for services to stabilize
sleep 5

# Show notification and open browser
notify-send "BioModStack" "✅ Services restarted! Opening UI..." -i applications-science
xdg-open http://localhost:5173
