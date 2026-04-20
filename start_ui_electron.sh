#!/bin/bash
# BioModStack Electron launcher - starts services and opens the Electron shell

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$SCRIPT_DIR}"
LAUNCHER="$PROJECT_DIR/scripts/launch_biomodstack_ui.py"

exec python3 "$LAUNCHER" --surface electron "$@"
