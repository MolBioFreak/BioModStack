"""BC2 native campaign boundary (not an enabled BMS model integration).

Imported safely by API discovery: native settings are imported only by compile_for_native,
which must run inside the pinned execution image. The incomplete operator schema is
intentionally NOT advertised as a full model parameter contract.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Callable

PIN = "d5bae16e9fee95f4c97fc16bc05dcbde4ccb885f"


def receipt_json(value: object) -> object:
    """Lossless JSON receipt encoding for native's infinity sentinel thresholds."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"$bc2_nonfinite_float": "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")}
    if isinstance(value, dict):
        return {key: receipt_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [receipt_json(item) for item in value]
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(receipt_json(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def compile_for_native(request: dict, project_folder: Path,
                       resolve: Callable[[dict], dict] | None = None) -> dict:
    """Resolve with upstream's own settings code and bind a finite job-owned campaign.

    The entire supplied native request is passed through unchanged, except system-owned
    project_folder and resume. This is *not* a typed UI/API admission authority yet.
    """
    if not isinstance(request, dict):
        raise ValueError("BC2 request must be an object")
    if "project_folder" in request or "resume" in request:
        raise ValueError("project_folder and resume are system-owned at this boundary")
    limit = request.get("max_trajectories")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("max_trajectories must be an explicit positive integer")
    if "parameter_sweep" in request:
        raise ValueError("native sweep may exceed a requested total through per-arm clamping; budget admission pending")
    if resolve is None:
        # Never run on API discovery: native dependencies import accelerator libraries.
        from importlib import import_module
        resolve = getattr(import_module("bindcraft.settings"), "load_settings")
    project_folder = Path(project_folder).resolve()
    native = {**request, "project_folder": str(project_folder), "resume": False}
    effective = resolve(native)
    if not isinstance(effective, dict):
        raise ValueError("native resolver did not return settings")
    if (effective.get("max_trajectories") != limit or effective.get("project_folder") != str(project_folder)
            or effective.get("resume") is not False):
        raise ValueError("native resolution changed system-bound budget, resume or campaign directory")
    return {"schema_version": 1, "upstream_commit": PIN, "native_request": native,
            "effective_settings": effective,
            "effective_sha256": hashlib.sha256(_canonical(effective)).hexdigest()}


def write_compilation(compiled: dict, destination: Path) -> None:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_canonical(compiled) + b"\n")
