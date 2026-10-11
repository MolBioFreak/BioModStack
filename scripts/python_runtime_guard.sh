#!/usr/bin/env bash
# Native consumers share the setup dependency authority; never repair on launch.
_BMS_PYTHON_GUARD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bms_python_runtime_resolve() {
    # A managed but stale/incomplete environment must not fall back to host uv.
    local resolved_python
    resolved_python="$(PYTHONPATH="$_BMS_PYTHON_GUARD_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -B -c '
import sys
sys.path.insert(0, sys.argv[2])
from pathlib import Path
from biomodstack_python_prerequisites import resolve_python_environment
try:
    resolved = resolve_python_environment(Path(sys.argv[1]))
    print(resolved["python"] if resolved is not None else "")
    print(resolved.get("root", "") if resolved is not None else "")
    receipt = resolved.get("support_payload") if resolved is not None else None
    print(str(Path(receipt["payload_root"]) / "support-payload.json")
          if isinstance(receipt, dict) and receipt.get("payload_root") else "")
except (OSError, ValueError, RuntimeError) as exc:
    print(f"BioModStack Python prerequisite environment is blocked: {exc}", file=sys.stderr)
    raise SystemExit(78)
' "${PROJECT_DIR:-$_BMS_PYTHON_GUARD_ROOT}" "$_BMS_PYTHON_GUARD_ROOT")" || return 78
    local -a resolved_lines
    mapfile -t resolved_lines <<< "$resolved_python"
    BMS_MANAGED_PYTHON="${resolved_lines[0]:-}"
    BMS_MANAGED_PYTHON_ROOT="${resolved_lines[1]:-${BMS_MANAGED_PYTHON%/environment/bin/python}}"
    if [ -n "${resolved_lines[2]:-}" ]; then
        export BMS_SUPPORT_PAYLOAD_RECEIPT="${resolved_lines[2]}"
        export BMS_API_PYTHON="$BMS_MANAGED_PYTHON"
    fi
    export BMS_MANAGED_PYTHON BMS_MANAGED_PYTHON_ROOT
}
