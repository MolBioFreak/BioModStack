#!/usr/bin/env bash
set -euo pipefail
export HOME=/home/dalab
export XDG_CACHE_HOME=/home/dalab/.cache
source /home/dalab/.biomodstack/env.sh >/dev/null 2>&1 || true
unset BMS_WORKFLOW_ADAPTER_URL
export BMS_CORE_RUNTIME_MODE=0
cd /home/dalab/biomodstack/biomodstack/platform/api
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
exec /home/dalab/.local/bin/uv run python /tmp/launch_bms_job.py ed2e4bdf-9ef6-41dd-8eca-d6ba09be2054
