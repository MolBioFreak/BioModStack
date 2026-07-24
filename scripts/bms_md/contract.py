from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
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


def _snapshot_mismatch(message: str) -> MDInputSnapshotError:
    return MDInputSnapshotError(f"MD_INPUT_SNAPSHOT_MISMATCH: {message}")


def _expected_snapshot_metadata(input_config: Mapping[str, Any], field: str) -> tuple[str, int]:
    expected_digest = input_config.get(f"{field}_sha256")
    expected_bytes = input_config.get(f"{field}_bytes")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise _snapshot_mismatch("input snapshot digest is missing")
    if isinstance(expected_bytes, bool) or not isinstance(expected_bytes, int) or not 0 <= expected_bytes <= MAX_INPUT_SNAPSHOT_BYTES:
        raise _snapshot_mismatch("input snapshot size is missing or invalid")
    return expected_digest, expected_bytes


def _verify_open_snapshot(source: Path, expected_digest: str, expected_bytes: int) -> None:
    digest = hashlib.sha256()
    consumed = 0
    try:
        with source.open("rb") as handle:
            opened_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_size != expected_bytes:
                raise _snapshot_mismatch("input snapshot size changed")
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                consumed += len(chunk)
                if consumed > MAX_INPUT_SNAPSHOT_BYTES:
                    raise _snapshot_mismatch("input snapshot exceeds its bounded size")
                digest.update(chunk)
    except MDInputSnapshotError:
        raise
    except (OSError, ValueError) as exc:
        raise _snapshot_mismatch("input snapshot is unavailable") from exc
    if consumed != expected_bytes or digest.hexdigest() != expected_digest:
        raise _snapshot_mismatch("input snapshot digest or size changed")


def _safe_closure_relative(raw: Any) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw:
        raise _snapshot_mismatch("topology closure path is invalid")
    path = PurePosixPath(raw)
    if path.as_posix() != raw or any(part in {"", ".", ".."} for part in path.parts):
        raise _snapshot_mismatch("topology closure path is invalid")
    return path


def _topology_closure(job_config: Mapping[str, Any]) -> tuple[Path, list[dict[str, Any]]]:
    input_config = job_config.get("input")
    closure = input_config.get("topology_closure") if isinstance(input_config, Mapping) else None
    if not isinstance(closure, Mapping):
        raise _snapshot_mismatch("prepared topology closure manifest is missing")
    root_value = closure.get("root")
    files = closure.get("files")
    if not isinstance(root_value, str) or not root_value or not isinstance(files, list) or len(files) > 256:
        raise _snapshot_mismatch("prepared topology closure manifest is invalid")
    root = Path(root_value)
    normalized: list[dict[str, Any]] = []
    for record in files:
        if not isinstance(record, Mapping):
            raise _snapshot_mismatch("topology closure record is invalid")
        relative = _safe_closure_relative(record.get("path"))
        digest = record.get("sha256")
        size = record.get("bytes")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise _snapshot_mismatch("topology closure digest is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_INPUT_SNAPSHOT_BYTES:
            raise _snapshot_mismatch("topology closure size is invalid")
        normalized.append({**dict(record), "_relative": relative, "sha256": digest, "bytes": size})
    return root, normalized


def verify_input_snapshots(job_config: Mapping[str, Any]) -> None:
    """Rehash every persisted job-owned input and declared local topology include."""

    input_config = job_config.get("input")
    if not isinstance(input_config, Mapping):
        raise _snapshot_mismatch("input snapshot contract is missing")
    for field in ("structure", "coordinates", "topology"):
        value = input_config.get(field)
        if not value:
            continue
        expected_digest, expected_bytes = _expected_snapshot_metadata(input_config, field)
        _verify_open_snapshot(Path(str(value)), expected_digest, expected_bytes)
    if input_config.get("topology"):
        closure_root, closure_files = _topology_closure(job_config)
        for record in closure_files:
            _verify_open_snapshot(
                closure_root.joinpath(*record["_relative"].parts),
                record["sha256"],
                record["bytes"],
            )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_verified_snapshot(
    source: Path,
    destination: Path,
    *,
    expected_digest: str,
    expected_bytes: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".verified.", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    consumed = 0
    published = False
    try:
        try:
            source_handle = source.open("rb")
        except OSError as exc:
            raise _snapshot_mismatch("input snapshot is unavailable") from exc
        with source_handle, os.fdopen(descriptor, "wb") as private_handle:
            opened_stat = os.fstat(source_handle.fileno())
            if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_size != expected_bytes:
                raise _snapshot_mismatch("input snapshot size changed")
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                consumed += len(chunk)
                if consumed > MAX_INPUT_SNAPSHOT_BYTES:
                    raise _snapshot_mismatch("input snapshot exceeds its bounded size")
                digest.update(chunk)
                private_handle.write(chunk)
            private_handle.flush()
            os.fsync(private_handle.fileno())
        if consumed != expected_bytes or digest.hexdigest() != expected_digest:
            raise _snapshot_mismatch("input snapshot digest or size changed")
        os.chmod(temporary, 0o444)
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise _snapshot_mismatch("private input destination conflict") from exc
        published = True
        temporary.unlink()
        _fsync_directory(destination.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        if published:
            destination.unlink(missing_ok=True)
        raise


def prepare_verified_worker_inputs(config_path: Path, worker_root: Path) -> dict[str, Any]:
    """Verify-and-copy job snapshots into one invocation-private, read-only input tree."""

    config = normalize_job_config(json.loads(Path(config_path).read_text(encoding="utf-8")))
    input_config = dict(config["input"])
    worker_root = Path(worker_root)
    worker_root.mkdir(parents=True, exist_ok=True)
    private_root = Path(tempfile.mkdtemp(prefix="verified-inputs-", dir=worker_root))
    try:
        for field in ("structure", "coordinates"):
            source_value = input_config.get(field)
            if not source_value:
                continue
            expected_digest, expected_bytes = _expected_snapshot_metadata(input_config, field)
            source = Path(str(source_value))
            suffix = source.suffix.lower() if re.fullmatch(r"\.[a-z0-9]{1,10}", source.suffix.lower()) else ".bin"
            destination = private_root / f"{field}{suffix}"
            _copy_verified_snapshot(
                source,
                destination,
                expected_digest=expected_digest,
                expected_bytes=expected_bytes,
            )
            input_config[field] = str(destination.resolve())

        topology_value = input_config.get("topology")
        if topology_value:
            expected_digest, expected_bytes = _expected_snapshot_metadata(input_config, "topology")
            closure_source_root, closure_files = _topology_closure(config)
            private_closure_root = private_root / "topology_closure"
            private_topology = private_closure_root / "topology.top"
            _copy_verified_snapshot(
                Path(str(topology_value)),
                private_topology,
                expected_digest=expected_digest,
                expected_bytes=expected_bytes,
            )
            copied: dict[str, tuple[str, int]] = {}
            for record in closure_files:
                relative: PurePosixPath = record["_relative"]
                previous = copied.get(relative.as_posix())
                identity = (record["sha256"], record["bytes"])
                if previous is not None and previous != identity:
                    raise _snapshot_mismatch("duplicate topology closure path has conflicting identity")
                if previous is None:
                    _copy_verified_snapshot(
                        closure_source_root.joinpath(*relative.parts),
                        private_closure_root.joinpath(*relative.parts),
                        expected_digest=record["sha256"],
                        expected_bytes=record["bytes"],
                    )
                    copied[relative.as_posix()] = identity
            closure = dict(input_config["topology_closure"])
            closure["root"] = str(private_closure_root.resolve())
            input_config["topology_closure"] = closure
            input_config["topology"] = str(private_topology.resolve())
        config["input"] = input_config
        for directory in sorted((path for path in private_root.rglob("*") if path.is_dir()), reverse=True):
            os.chmod(directory, 0o555)
        os.chmod(private_root, 0o555)
        _fsync_directory(private_root)
        _fsync_directory(worker_root)
        return config
    except Exception:
        shutil.rmtree(private_root, ignore_errors=True)
        raise


def load_verified_job_config(config_path: Path) -> dict[str, Any]:
    config = normalize_job_config(json.loads(Path(config_path).read_text(encoding="utf-8")))
    verify_input_snapshots(config)
    return config


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def build_atom_order_manifest(topology_path: Path) -> dict[str, Any]:
    """Build a deterministic atom-order contract from a GRO or PDB topology.

    Sequential position is authoritative. Source serials are retained only as
    provenance because GRO/PDB serial fields can wrap or contain insertion codes.
    """

    topology_path = Path(topology_path).resolve()
    suffix = topology_path.suffix.lower()
    try:
        lines = topology_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("analysis topology is unavailable or not text") from exc
    atoms: list[dict[str, Any]] = []
    if suffix == ".gro":
        if len(lines) < 3:
            raise ValueError("GRO topology is truncated")
        try:
            declared_count = int(lines[1].strip())
        except ValueError as exc:
            raise ValueError("GRO topology atom count is invalid") from exc
        atom_lines = lines[2 : 2 + declared_count]
        if declared_count < 1 or len(atom_lines) != declared_count or len(lines) < declared_count + 3:
            raise ValueError("GRO topology atom count does not match its records")
        for index, line in enumerate(atom_lines):
            if len(line) < 20:
                raise ValueError("GRO topology atom record is truncated")
            try:
                resid = int(line[0:5])
                source_serial = int(line[15:20])
            except ValueError as exc:
                raise ValueError("GRO topology atom identity is invalid") from exc
            name = line[10:15].strip()
            resname = line[5:10].strip()
            if not name or not resname:
                raise ValueError("GRO topology atom identity is incomplete")
            atoms.append({
                "index": index,
                "name": name,
                "resid": str(resid),
                "resname": resname,
                "chain_id": "",
                "insertion_code": "",
                "segid": "",
                "source_serial": source_serial,
            })
    elif suffix in {".pdb", ".ent"}:
        atom_lines = [line for line in lines if line.startswith(("ATOM  ", "HETATM"))]
        if not atom_lines:
            raise ValueError("PDB topology contains no atom records")
        for index, line in enumerate(atom_lines):
            padded = line.ljust(80)
            name = padded[12:16].strip()
            resname = padded[17:20].strip()
            resid = padded[22:26].strip()
            serial = padded[6:11].strip()
            if not name or not resname or not resid:
                raise ValueError("PDB topology atom identity is incomplete")
            atoms.append({
                "index": index,
                "name": name,
                "resid": resid,
                "resname": resname,
                "chain_id": padded[21:22].strip(),
                "insertion_code": padded[26:27].strip(),
                "segid": padded[72:76].strip(),
                "source_serial": int(serial) if serial.isdigit() else serial,
            })
    else:
        raise ValueError("atom-order manifests support only GRO and PDB analysis topologies")
    return {
        "schema": "bms.md.atom-order.v1",
        "topology_format": suffix.removeprefix("."),
        "atom_count": len(atoms),
        "atoms": atoms,
    }


def atom_order_identity(payload: Mapping[str, Any]) -> str:
    if payload.get("schema") != "bms.md.atom-order.v1":
        raise ValueError("atom-order manifest schema is invalid")
    digest = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    return f"sha256:{digest}"


def write_atom_order_manifest(topology_path: Path, output_path: Path) -> tuple[Path, str]:
    payload = build_atom_order_manifest(topology_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + f".tmp-{os.getpid()}")
    try:
        temporary.write_bytes(_canonical_json_bytes(payload))
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
        _fsync_directory(output_path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return output_path, atom_order_identity(payload)


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
            "runtime": copy.deepcopy(config.get("engine_runtime")),
        },
        "config": config,
        "stages": copy.deepcopy(dict(stages)),
        "artifacts": {
            artifact_name: _artifact_record(Path(path), Path(output_dir))
            for artifact_name, path in artifacts.items()
        },
    }
