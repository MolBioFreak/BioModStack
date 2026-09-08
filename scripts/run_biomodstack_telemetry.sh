#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${BMS_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"
source "$SCRIPT_DIR/configuration_read_guard.sh"
bms_configuration_read_finish
source "$SCRIPT_DIR/python_runtime_guard.sh"
bms_python_runtime_resolve || exit 78
cd "$PROJECT_DIR/platform/api"
if [ -n "$BMS_MANAGED_PYTHON" ]; then
    exec "$BMS_MANAGED_PYTHON" -m tools.telemetry_collector
fi
# Existing installations retain their checkout environment until explicitly bootstrapped.
exec "$PROJECT_DIR/platform/api/.venv/bin/python" -m tools.telemetry_collector
