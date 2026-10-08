#!/bin/bash
# BioModStack STOP - Stop all services (API + Frontend)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
exec python3 "$PROJECT_DIR/scripts/manage_desktop_services.py" stop --notify
