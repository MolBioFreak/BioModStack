#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/environment.sh"
case "${1:-/designer}" in
  /designer|/ngs) workspace_route="${1:-/designer}" ;;
  *) exit 2 ;;
esac
"$BMS_HOME/start_ui.sh" start --runtime dev
exec xdg-open "http://127.0.0.1:18082$workspace_route"
