#!/bin/bash
# BioModStack STOP - Stop all services (API + Frontend)

PROJECT_DIR="/home/dalab/ProteinDJ_fork/Protein-De-Novo-Modification-and-Design-Platform"

notify-send "BioModStack" "🛑 Stopping ALL services..." -i dialog-warning

"$PROJECT_DIR/start_ui.sh" stop

notify-send "BioModStack" "✅ All services stopped." -i dialog-information
