"""Native BC2 entrypoint; run only inside a provisioned pinned accelerator image.

This does not register an enabled BMS route. API discovery imports no native code.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from services.bindcraft2_native import write_compilation
from services.bindcraft2_typed import compile_typed

# Native environment overrides can silently replace operator settings. Refuse those
# until each is assigned a typed BMS setting or scheduler-only ownership.
OVERRIDE_ENV = ("BINDCRAFT_BINDER_LENGTHS", "BINDCRAFT_WORKERS_PER_GPU",
                "BINDCRAFT_MAX_WORKERS_PER_GPU", "BINDCRAFT_DESIGN_WORKERS",
                "BINDCRAFT_GPU_IDS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path, help="native request JSON from the typed admission authority")
    parser.add_argument("project_folder", type=Path)
    parser.add_argument("--preview-only", action="store_true")
    args = parser.parse_args()
    conflicts = [name for name in OVERRIDE_ENV if os.environ.get(name)]
    if conflicts:
        parser.error("unbound native overrides: " + ", ".join(conflicts))
    project = args.project_folder.resolve()
    request = json.loads(args.request.read_text())
    compiled = compile_typed(request, project)
    if project.exists() and any(project.iterdir()):
        parser.error("existing BC2 campaign needs immutable-request resume admission; not implemented")
    project.mkdir(parents=True, exist_ok=True)
    write_compilation(compiled, project / "bms_compilation.json")
    # Native CLI must receive requested (unresolved) settings so it performs its own
    # preset layering exactly once; effective snapshot is a separate receipt.
    native_path = project / "bms_native_request.json"
    native_path.write_text(json.dumps(compiled["native_request"], sort_keys=True) + "\n")
    if not args.preview_only:
        subprocess.run(["bindcraft", "design", str(native_path)], check=True)


if __name__ == "__main__":
    main()
