#!/bin/bash
# BioModStack RESTART API - Restart only the API backend (dev or container runtime)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
exec python3 "$PROJECT_DIR/scripts/manage_desktop_services.py" restart-api --notify "$@"
