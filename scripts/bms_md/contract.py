from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

MD_JOB_SCHEMA = "bms.md.job.v1"
MD_RUN_SCHEMA = "bms.md.run.v1"
SUPPORTED_ENGINES = {"gromacs", "openmm"}
MAX_INPUT_SNAPSHOT_BYTES = 100 * 1024 * 1024


class MDInputSnapshotError(RuntimeError):
    code = "MD_INPUT_SNAPSHOT_MISMATCH"

_DEFAULT_CONFIG: dict[str, Any] = {
    "schema": MD_JOB_SCHEMA,
    "engine": "gromacs",
    "replicas": 1,
    "random_seed": 20260717,
    "preparation": {
        "force_field": "amber99sb-ildn",
        "water_model": "tip3p",
        "box_type": "dodecahedron",
        "padding_nm": 1.0,
        "salt_molar": 0.15,
        "positive_ion": "NA",
        "negative_ion": "CL",
        "solvent_group": "SOL",
        "solvent_coordinates": "spc216.gro",
        "neutralize": True,
    },
    "stages": {
        "minimization": {
            "enabled": True,
            "steps": 50000,
            "force_tolerance_kj_mol_nm": 1000.0,
        },
        "nvt": {
            "enabled": True,
            "steps": 50000,
            "temperature_k": 300.0,
        },
        "npt": {
            "enabled": True,
            "steps": 50000,
            "temperature_k": 300.0,
            "pressure_bar": 1.0,
        },
        "production": {
            "enabled": True,
            "steps": 500000,
            "temperature_k": 300.0,
            "pressure_bar": 1.0,
            "checkpoint_interval_minutes": 15.0,
            "trajectory_interval_steps": 5000,
            "energy_interval_steps": 1000,
        },
    },
    "execution": {
        "gpu_id": "0",
        "ntmpi": 1,
        "ntomp": 8,
        "gpu_offload": "full",
        "pin": "on",
    },
}


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be >= 1")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be >= 1") from exc
    if normalized < 1:
        raise ValueError(f"{field} must be >= 1")
    return normalized


def normalize_job_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return a validated, explicit v1 MD job configuration.

    The normalized form is deliberately JSON-compatible so the same contract can
    cross the API, Nextflow, container, and result-manifest boundaries without
    engine-specific Python objects.
    """

    if not isinstance(raw, Mapping):
        raise ValueError("MD job config must be an object")
    config = _deep_merge(copy.deepcopy(_DEFAULT_CONFIG), raw)

    if config.get("schema") != MD_JOB_SCHEMA:
        raise ValueError(f"schema must be {MD_JOB_SCHEMA!r}")
    job_id = str(config.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    config["job_id"] = job_id

    engine = str(config.get("engine") or "").strip().lower()
    if engine not in SUPPORTED_ENGINES:
        raise ValueError(f"unsupported MD engine: {engine or '<empty>'}")
    config["engine"] = engine
    config["replicas"] = _positive_int(config.get("replicas"), "replicas")
    config["random_seed"] = _positive_int(config.get("random_seed"), "random_seed")

    input_config = config.get("input")
    if not isinstance(input_config, Mapping):
        raise ValueError("input must be an object")
    has_structure = bool(str(input_config.get("structure") or "").strip())
    has_prepared = bool(str(input_config.get("coordinates") or "").strip()) and bool(
        str(input_config.get("topology") or "").strip()
    )
    if not (has_structure or has_prepared):
        raise ValueError("input requires structure or coordinates plus topology")
    config["input"] = dict(input_config)

    stages = config.get("stages")
    if not isinstance(stages, Mapping):
        raise ValueError("stages must be an object")
    stages_dict = dict(stages)
    for stage_name in ("minimization", "nvt", "npt", "production"):
        stage = stages_dict.get(stage_name)
        if not isinstance(stage, Mapping):
            raise ValueError(f"stages.{stage_name} must be an object")
        stage_dict = dict(stage)
        stage_dict["enabled"] = bool(stage_dict.get("enabled", True))
        if stage_dict["enabled"]:
            stage_dict["steps"] = _positive_int(stage_dict.get("steps"), f"{stage_name}.steps")
        stages_dict[stage_name] = stage_dict
    config["stages"] = stages_dict

    execution = config.get("execution")
    if not isinstance(execution, Mapping):
        raise ValueError("execution must be an object")
    execution = dict(execution)
    execution["gpu_id"] = str(execution.get("gpu_id", "0"))
    if "scheduler_gpu_id" in execution:
        execution["scheduler_gpu_id"] = str(execution["scheduler_gpu_id"])
    execution["ntmpi"] = _positive_int(execution.get("ntmpi"), "execution.ntmpi")
    execution["ntomp"] = _positive_int(execution.get("ntomp"), "execution.ntomp")
    if execution.get("gpu_offload") not in {"auto", "full", "none"}:
        raise ValueError("execution.gpu_offload must be auto, full, or none")
    if execution.get("pin") not in {"on", "off", "auto"}:
        raise ValueError("execution.pin must be on, off, or auto")
    config["execution"] = execution
    return config


def verify_input_snapshots(job_config: Mapping[str, Any]) -> None:
    """Rehash every persisted job-owned input immediately before worker use."""

    input_config = job_config.get("input")
    if not isinstance(input_config, Mapping):
        raise MDInputSnapshotError("MD_INPUT_SNAPSHOT_MISMATCH: input snapshot contract is missing")
    for field in ("structure", "coordinates", "topology"):
        value = input_config.get(field)
        if not value:
            continue
        expected = input_config.get(f"{field}_sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise MDInputSnapshotError("MD_INPUT_SNAPSHOT_MISMATCH: input snapshot digest is missing")
        digest = hashlib.sha256()
        consumed = 0
        try:
            with Path(str(value)).open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    consumed += len(chunk)
                    if consumed > MAX_INPUT_SNAPSHOT_BYTES:
                        raise MDInputSnapshotError(
                            "MD_INPUT_SNAPSHOT_MISMATCH: input snapshot exceeds its bounded size"
                        )
                    digest.update(chunk)
        except (OSError, ValueError) as exc:
            raise MDInputSnapshotError("MD_INPUT_SNAPSHOT_MISMATCH: input snapshot is unavailable") from exc
        if digest.hexdigest() != expected:
            raise MDInputSnapshotError("MD_INPUT_SNAPSHOT_MISMATCH: input snapshot digest changed")


def load_verified_job_config(config_path: Path) -> dict[str, Any]:
    config = normalize_job_config(json.loads(Path(config_path).read_text(encoding="utf-8")))
    verify_input_snapshots(config)
    return config


def _artifact_record(path: Path, output_dir: Path) -> dict[str, Any]:
    resolved_output = output_dir.resolve()
    resolved_path = path.resolve()
    try:
        relative = resolved_path.relative_to(resolved_output)
    except ValueError as exc:
        raise ValueError(f"artifact is outside output directory: {path}") from exc
    if not resolved_path.is_file():
        raise ValueError(f"artifact does not exist: {path}")
    digest = hashlib.sha256()
    with resolved_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": relative.as_posix(),
        "bytes": resolved_path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def build_run_manifest(
    *,
    output_dir: Path,
    job_config: Mapping[str, Any],
    replica_index: int,
    engine_version: str,
    platform: str,
    artifacts: Mapping[str, Path],
    stages: Mapping[str, Any],
) -> dict[str, Any]:
    config = normalize_job_config(job_config)
    if replica_index < 0 or replica_index >= config["replicas"]:
        raise ValueError("replica_index is outside configured replica range")
    return {
        "schema": MD_RUN_SCHEMA,
        "job_schema": MD_JOB_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": config["job_id"],
        "replica_index": replica_index,
        "engine": {
            "name": config["engine"],
            "version": str(engine_version).strip(),
            "platform": str(platform).strip(),
        },
        "config": config,
        "stages": copy.deepcopy(dict(stages)),
        "artifacts": {
            artifact_name: _artifact_record(Path(path), Path(output_dir))
            for artifact_name, path in artifacts.items()
        },
    }
