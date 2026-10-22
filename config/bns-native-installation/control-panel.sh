#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
exec /usr/bin/python3 "$BMS_HOME/biomodstack_panel.py" "$@"
