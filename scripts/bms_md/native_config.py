"""Native GROMACS documents; no force-field or simulation-policy defaults."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any

NATIVE_JOB_SCHEMA = "bms.md.job.v3"
INPUT_FILE_FIELDS = ("structure", "coordinates", "topology", "tpr", "checkpoint", "index", "restraint_reference")


def parse_mdp(text: str) -> dict[str, str]:
    """Import native entries for editing in the same document as typed controls."""
    entries: dict[str, str] = {}
    for line in text.splitlines():
        entry = line.split(";", 1)[0].strip()
        if not entry:
            continue
        key, separator, value = entry.partition("=")
        key = key.strip()
        if not separator or key in entries:
            raise ValueError("MDP import requires one key=value entry per key")
        entries[key] = value.strip()
    return entries


def render_mdp(entries: Mapping[str, Any]) -> str:
    """Render exactly the supplied entries, leaving native semantics to grompp."""
    lines = []
    for key, value in entries.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ValueError("MDP keys must be native option names")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("MDP values must be strings or numbers (use native yes/no strings)")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("MDP numeric values must be finite")
        if any(char in str(value) for char in "\r\n"):
            raise ValueError("MDP values must occupy one line")
        lines.append(f"{key} = {value}\n")
    return "".join(lines)


def normalize_native_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep native stage documents independent of historical fixed-stage recipes."""
    config = copy.deepcopy(dict(raw))
    if config.get("engine") != "gromacs":
        raise ValueError("bms.md.job.v3 is a native GROMACS contract")
    for field in ("job_id", "input", "stages", "execution"):
        if field not in config:
            raise ValueError(f"{field} is required")
    config.setdefault("replicas", 1)
    config.setdefault("random_seed", 20260717)
    for field in ("replicas", "random_seed"):
        if type(config[field]) is not int or config[field] < 1:
            raise ValueError(f"{field} must be >= 1")
    inputs = config["input"]
    if not isinstance(inputs, Mapping):
        raise ValueError("input must be an object")
    modes = (bool(inputs.get("structure")), bool(inputs.get("coordinates") and inputs.get("topology")), bool(inputs.get("tpr")))
    if sum(modes) != 1:
        raise ValueError("input requires exactly one guided, prepared or compiled system")
    stages = config["stages"]
    if not isinstance(stages, list):
        raise ValueError("native stages must be an ordered list")
    if inputs.get("tpr") and stages:
        raise ValueError("compiled TPR input cannot be changed by stage MDP settings")
    names = set()
    for stage in stages:
        name = stage.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) or name in names:
            raise ValueError("native stages require unique filesystem-safe names")
        names.add(name)
        if "mdp" in stage:
            if not isinstance(stage["mdp"], Mapping):
                raise ValueError("stage mdp must be an object")
            render_mdp(stage["mdp"])
        elif not stage.get("mdp_file"):
            raise ValueError("native stages require mdp or mdp_file")
    windows = config.get("windows") or []
    ids = set()
    for window in windows:
        identity = window.get("id")
        if not isinstance(identity, str) or not identity or identity in ids:
            raise ValueError("native windows require unique nonempty ids")
        ids.add(identity)
        if inputs.get("tpr") and window.get("coordinates"):
            raise ValueError("compiled TPR input cannot be changed by window coordinates")
        for name, entries in (window.get("mdp") or {}).items():
            if name not in names:
                raise ValueError("window MDP must name an existing stage")
            render_mdp(entries)
    return config


def lane_count(config: Mapping[str, Any]) -> int:
    return int(config["replicas"]) * ((len(config.get("windows") or []) or 1) if config.get("schema") == NATIVE_JOB_SCHEMA else 1)


def lane_config(config: Mapping[str, Any], replica_index: int) -> dict[str, Any]:
    """Resolve a window once into the ordinary engine replica document.

    The caller retains the root request for replay. replica_index remains the
    flattened execution identity; replicas in a lane is the total roster size.
    No stochastic MDP values are derived from the orchestration seed.
    """
    lane = copy.deepcopy(dict(config))
    windows = lane.get("windows")
    if config.get("schema") != NATIVE_JOB_SCHEMA or not windows:
        return lane
    lane.pop("windows")
    window_index, replicate_index = divmod(replica_index, int(config["replicas"]))
    window = windows[window_index]
    lane["replicas"] = lane_count(config)
    lane["window_id"] = window["id"]
    lane["replicate_index"] = replicate_index
    if window.get("coordinates"):
        for key in ("coordinates", "coordinates_sha256", "coordinates_bytes"):
            if key in window:
                lane["input"][key] = window[key]
    for stage in lane["stages"]:
        stage["mdp"].update((window.get("mdp") or {}).get(stage["name"], {}))
    return lane


def final_stage_name(config: Mapping[str, Any]) -> str:
    if config.get("schema") == NATIVE_JOB_SCHEMA and config.get("stages"):
        return config["stages"][-1]["name"]
    return "production"


def compatibility_key(config: Mapping[str, Any]) -> str:
    payload = {
        "engine": config.get("engine"), "engine_runtime": config.get("engine_runtime"),
        "chemistry": config.get("chemistry"), "protocol": config.get("protocol"),
        "input_hashes": {key: value for key, value in (config.get("input") or {}).items()
                         if key.endswith("_sha256")},
    }
    if config.get("schema") == NATIVE_JOB_SCHEMA:
        payload.update(stages=config["stages"], window_id=config.get("window_id"),
                       topology_closure=(config.get("input") or {}).get("topology_closure", {}).get("files"))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
