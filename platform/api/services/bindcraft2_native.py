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
                       resolve: Callable[[dict], dict] | None = None,
                       sweep_arms: Callable[[dict], tuple] | None = None, *, resume: bool = False) -> dict:
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
    if resolve is None:
        # Never run on API discovery: native dependencies import accelerator libraries.
        from importlib import import_module
        resolve = getattr(import_module("bindcraft.settings"), "load_settings")
    project_folder = Path(project_folder).resolve()
    native = {**request, "project_folder": str(project_folder), "resume": resume}
    effective = resolve(native)
    if not isinstance(effective, dict):
        raise ValueError("native resolver did not return settings")
    if (effective.get("max_trajectories") != limit or effective.get("project_folder") != str(project_folder)
            or effective.get("resume") is not resume):
        raise ValueError("native resolution changed system-bound budget, resume or campaign directory")
    arms = ()
    if isinstance(effective.get("parameter_sweep"), dict) or effective.get("parameter_sweep"):
        if sweep_arms is None:
            from importlib import import_module
            sweep_arms = getattr(import_module("bindcraft.parameter_sweep"), "parameter_sweep_arms")
        arms = sweep_arms(effective)
    arm_count = len(arms)
    per_arm = max(1, limit // arm_count) if arm_count else limit
    allowance = per_arm * arm_count if arm_count else limit
    if allowance > limit:
        raise ValueError(f"native sweep clamps {arm_count} arms to {allowance} attempts, exceeding requested {limit}")
    return {"schema_version": 1, "upstream_commit": PIN, "native_request": native,
            "effective_settings": effective, "sweep_budget": {"arms": arm_count, "per_arm": per_arm,
            "aggregate_allowance": allowance},
            "effective_sha256": hashlib.sha256(_canonical(effective)).hexdigest()}


def relocate_compilation(compiled: dict, destination: Path) -> dict:
    """Rebind only declared placement paths; preserve the operator/science receipt.

    Worker transport carries an untouched preparation tree. This pure operation
    creates its execution receipt at the writable output root; native re-resolution
    in prepare_campaign still verifies all effective settings and the budget.
    """
    destination = Path(destination).resolve()
    if hashlib.sha256(_canonical(compiled['effective_settings'])).hexdigest() != compiled.get('effective_sha256'):
        raise ValueError('BC2 effective settings digest differs before relocation')
    original = compiled['native_request']
    old_root = Path(original['project_folder']).parent
    paths = {original['project_folder']: str(destination / 'campaign')}
    for row in original.get('targets', []):
        path = Path(row['target_path'])
        paths[str(path)] = str(destination / path.relative_to(old_root))
    if original.get('binder_scaffold'):
        path = Path(original['binder_scaffold'])
        paths[str(path)] = str(destination / path.relative_to(old_root))

    def mapped(value):
        if isinstance(value, dict):
            return {key: mapped(item) for key, item in value.items()}
        if isinstance(value, list):
            return [mapped(item) for item in value]
        return paths.get(value, value) if isinstance(value, str) else value

    result = {**compiled, 'native_request': mapped(original),
              'effective_settings': mapped(compiled['effective_settings'])}
    result['effective_sha256'] = hashlib.sha256(_canonical(result['effective_settings'])).hexdigest()
    if original['project_folder'] != str(destination / 'campaign'):
        # request_sha256 remains the exact operator scientific identity. The
        # original effective digest records where that request was compiled.
        result['placement'] = compiled.get('placement') or {
            'original_project_folder': original['project_folder'],
            'original_effective_sha256': compiled['effective_sha256'],
        }
    return result


def write_compilation(compiled: dict, destination: Path) -> None:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_canonical(compiled) + b"\n")
