"""Fail-closed, model-owned BC2 request adapter. NOT an enabled model schema.

Inventory observations are evidence rather than a promise of complete scientific parity.
The native resolver remains the authority for preset composition and applicability.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from services.bindcraft2_native import PIN, _canonical, compile_for_native

INVENTORY = Path(__file__).parents[1] / "config/models/bindcraft2_native_inventory.json"
SYSTEM_KEYS = frozenset({"project_folder", "resume", "gpu_ids", "auto_multi_gpu", "design_workers",
                         "workers_per_gpu", "max_workers_per_gpu", "worker_launch_stagger",
                         "compile_next_length"})


def schema() -> dict:
    """Stable serializable discovery contract shared by operator and agent adapters."""
    data = json.loads(INVENTORY.read_text())
    if data["upstream_commit"] != PIN:
        raise ValueError("BC2 inventory pin mismatch")
    return data


def _kind(value: object) -> str:
    if isinstance(value, bool): return "boolean"
    if isinstance(value, int): return "integer"
    if isinstance(value, float): return "number"
    if isinstance(value, str): return "string"
    if isinstance(value, list): return "array"
    if isinstance(value, dict): return "object"
    return "null"


def _check(value: object, observed: list[str], path: str) -> None:
    if not observed:
        raise ValueError(f"{path}: unresolved native type; cannot submit until typed")
    actual = _kind(value)
    if actual not in observed and not (actual == "integer" and "number" in observed):
        raise ValueError(f"{path}: expected {observed}, received {actual}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
        raise ValueError(f"{path}: numeric request must be finite")

def _metric_parameter(value: object, descriptor: dict, path: str) -> None:
    kind = descriptor.get("request_type")
    if kind == "nullable":
        if value is not None:
            raise ValueError(f"{path}: native default is null; non-null type unqualified")
    elif kind:
        _check(value, [kind], path)
    elif descriptor["default_literal"] is not None:
        _check(value, [_kind(descriptor["default_literal"])], path)
    else:
        raise ValueError(f"{path}: type unresolved")

def _string_list(value: object, path: str) -> None:
    _check(value, ["array"], path)
    for index, item in enumerate(value):
        _check(item, ["string"], f"{path}[{index}]")


def validate_request(request: dict, data: dict | None = None) -> dict:
    """No unknown/unsupported fields, inferred defaults or opaque nested JSON."""
    data = data or schema()
    if not isinstance(request, dict):
        raise ValueError("BC2 typed settings must be an object")
    fields = data["fields"]
    for name, value in request.items():
        if name in SYSTEM_KEYS:
            raise ValueError(f"{name}: system-owned")
        field = fields.get(name)
        if field is None:
            raise ValueError(f"{name}: unknown BC2 setting")
        if name == "parameter_sweep":
            raise ValueError("parameter_sweep: aggregate arm budget admission not qualified")
        if field["status"] != "typed":
            raise ValueError(f"{name}: unresolved native type")
        _check(value, field["observed_types"], name)
        if isinstance(value, dict):
            if name == "aa_bias":
                for residue, weight in value.items():
                    if residue not in "ACDEFGHIKLMNPQRSTVWY" or len(residue) != 1:
                        raise ValueError(f"aa_bias.{residue}: unknown residue")
                    _check(weight, ["number"], f"aa_bias.{residue}")
            elif name in ("filters", "losses"):
                for metric, entry in value.items():
                    registered = data["registered_metrics"][name].get(metric)
                    if registered is None or not isinstance(entry, dict):
                        raise ValueError(f"{name}.{metric}: unknown metric or invalid entry")
                    allowed = set(data["nested_surfaces"][name][metric]["entry_keys"])
                    for key, entry_value in entry.items():
                        if key not in allowed:
                            raise ValueError(f"{name}.{metric}.{key}: unknown entry field")
                        if key == "params":
                            if not isinstance(entry_value, dict):
                                raise ValueError(f"{name}.{metric}.params: expected object")
                            for parameter, param_value in entry_value.items():
                                source = registered["params"].get(parameter)
                                if source is None:
                                    raise ValueError(f"{name}.{metric}.params.{parameter}: unknown parameter")
                                _metric_parameter(param_value, source, f"{name}.{metric}.params.{parameter}")
                        elif key in ("higher", "mandatory"):
                            _check(entry_value, ["boolean"], f"{name}.{metric}.{key}")
                        elif key == "threshold":
                            _check(entry_value, ["number"], f"{name}.{metric}.{key}")
                        else:
                            _check(entry_value, ["string"], f"{name}.{metric}.{key}")
            else:
                raise ValueError(f"{name}: nested type not yet qualified")
        if name == "crop_fasta_sequence":
            if isinstance(value, list):
                if len(value) != 2 or any(type(x) is not int or x < 1 for x in value):
                    raise ValueError("crop_fasta_sequence: positive two-length range required")
            elif value is not False and (type(value) is not int or value < 1):
                raise ValueError("crop_fasta_sequence: positive length or false required")
        if name == "binder_shapes":
            if any(not isinstance(group, list) for group in value):
                raise ValueError("binder_shapes: groups must be arrays")
            for index, group in enumerate(value):
                _string_list(group, f"binder_shapes[{index}]")
        if name == "validation_models":
            if isinstance(value, list):
                for index, model in enumerate(value):
                    if type(model) not in (int, str):
                        raise ValueError(f"validation_models[{index}]: model name or index required")
            elif type(value) is not int or value < 1:
                raise ValueError("validation_models: positive count or model list required")
        if name == "multitarget_rounds_per_target":
            _string_list(value, name)
        if name == "cyclic_offset_mode" and value not in ("distance", "direction", "neighbours"):
            raise ValueError("cyclic_offset_mode: unknown mode")
        if name == "oligomer_tie" and value not in ("symmetric", "none"):
            raise ValueError("oligomer_tie: unknown tie mode")
        if isinstance(value, list) and name == "binder_lengths":
            if not value or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value):
                raise ValueError("binder_lengths: positive integer lengths required")
            if len(value) == 2 and value[0] > value[1]:
                raise ValueError("binder_lengths: two-element range must be ascending")
        if isinstance(value, list) and name == "paratope_conformations":
            if not value or any(item not in data["paratope_conformations"] for item in value):
                raise ValueError("paratope_conformations: unknown/empty conformation")
        if isinstance(value, list) and name == "targets":
            if not value:
                raise ValueError("targets: at least one target required")
            for index, target in enumerate(value):
                if not isinstance(target, dict):
                    raise ValueError(f"targets[{index}]: expected object")
                for key, item in target.items():
                    if key not in data["target_fields"]:
                        raise ValueError(f"targets[{index}].{key}: unknown field")
                    _check(item, ["number"] if key == "weight" else ["string"], f"targets[{index}].{key}")
                if not all(target.get(key) for key in ("name", "target_path")):
                    raise ValueError(f"targets[{index}]: name and target_path required")
        if isinstance(value, list) and name not in ("modality", "core", "target", "targets", "binder_lengths", "paratope_conformations", "binder_shapes", "validation_models", "multitarget_rounds_per_target", "crop_fasta_sequence"):
            raise ValueError(f"{name}: element schema not yet qualified")
        if name in ("modality", "core", "target"):
            names = value if isinstance(value, list) else [value]
            for item in names:
                _check(item, ["string"], f"{name}[]")
                if item not in data["presets"][name]:
                    raise ValueError(f"{name}: unknown preset {item}")
    if not isinstance(request.get("max_trajectories"), int) or isinstance(request.get("max_trajectories"), bool) or request["max_trajectories"] < 1:
        raise ValueError("max_trajectories: explicit positive integer required")
    return request


def compile_typed(request: dict, project_folder: Path, resolve=None) -> dict:
    validated = validate_request(request)
    compiled = compile_for_native(validated, project_folder, resolve)
    compiled["requested_settings"] = validated
    compiled["request_sha256"] = hashlib.sha256(_canonical(validated)).hexdigest()
    return compiled
