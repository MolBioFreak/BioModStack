from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import statistics
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Mapping, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from paths import get_data_root


class MDJobRecord(Protocol):
    id: str
    model_id: str
    output_dir: str | None
    child_output_dir: str | None
    params: Mapping[str, Any] | None
    provenance: Mapping[str, Any] | None


SCHEMA_ROOT = Path(__file__).resolve().parents[4] / "schemas"


@lru_cache(maxsize=2)
def _schema_validator(name: str) -> Draft202012Validator:
    payload = json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(payload)
    return Draft202012Validator(payload)


SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_REPLICAS = 64
MAX_ARTIFACTS = 1024
MAX_ANALYSIS_POINTS = 10_000
MAX_RESIDUE_METRICS = 10_000


def _replica_protocol_matches(requested: Mapping[str, Any], observed: Any) -> bool:
    if observed == requested:
        return True
    if requested.get("schema") != "bms.md.job.v2" or not isinstance(observed, Mapping):
        return False
    if observed.get("schema") != "bms.md.job.v2":
        return False
    requested_input = requested.get("input")
    observed_input = observed.get("input")
    if not isinstance(requested_input, Mapping) or not isinstance(observed_input, Mapping):
        return False
    requested_sha = requested_input.get("structure_sha256")
    observed_sha = observed_input.get("structure_sha256")
    requested_bytes = requested_input.get("structure_bytes")
    observed_bytes = observed_input.get("structure_bytes")
    if (
        not isinstance(requested_sha, str)
        or SHA256.fullmatch(requested_sha) is None
        or observed_sha != requested_sha
        or type(requested_bytes) is not int
        or requested_bytes < 1
        or observed_bytes != requested_bytes
        or not isinstance(requested_input.get("structure"), str)
        or not isinstance(observed_input.get("structure"), str)
    ):
        return False
    normalized_observed = dict(observed)
    normalized_observed["input"] = {
        **observed_input,
        "structure": requested_input["structure"],
    }
    return normalized_observed == requested
MAX_TRAJECTORY_PLAYBACK_FRAMES = 10_000


class MDResultError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ResolvedMDArtifact:
    artifact_id: str
    replica_index: int
    name: str
    path: Path
    bytes: int
    sha256: str
    semantic_role: str | None
    atom_order_identity: str | None
    selection_method: str | None
    source_frame: int | None
    time_ps: float | None
    source_trajectory_sha256: str | None


def _contained(root: Path, raw: str) -> Path:
    if not raw or raw.startswith("/") or "\\" in raw:
        raise MDResultError("MD_ARTIFACT_PATH_INVALID", "MD artifact path is not contained")
    relative = PurePosixPath(raw)
    if relative.as_posix() != raw or any(part in {"", ".", ".."} for part in relative.parts):
        raise MDResultError("MD_ARTIFACT_PATH_INVALID", "MD artifact path is not contained")
    root = root.resolve()
    path = root.joinpath(*relative.parts).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise MDResultError("MD_ARTIFACT_PATH_INVALID", "MD artifact escapes the job-owned result root") from exc
    return path


def _assert_finite_json(value: Any, code: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise MDResultError(code, "MD result manifest contains a non-finite number")
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_finite_json(item, code)
    elif isinstance(value, list):
        for item in value:
            _assert_finite_json(item, code)


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MDResultError(code, "MD result manifest is unavailable or invalid", 404) from exc
    if not isinstance(payload, dict):
        raise MDResultError(code, "MD result manifest must be an object")
    _assert_finite_json(payload, code)
    return payload


def _job_root(job: MDJobRecord) -> Path:
    if job.model_id != "molecular_dynamics":
        raise MDResultError("MD_JOB_REQUIRED", "Job is not a molecular-dynamics job", 404)
    raw = job.child_output_dir or job.output_dir
    if not raw:
        raise MDResultError("MD_RESULTS_ABSENT", "MD job has no result directory", 404)
    root = Path(raw).expanduser().resolve()
    allowed_root = Path(os.getenv("BMS_MD_RESULT_ROOT") or get_data_root()).expanduser().resolve()
    try:
        root.relative_to(allowed_root)
    except ValueError as exc:
        raise MDResultError("MD_RESULTS_ROOT_FORBIDDEN", "MD job result directory is outside the admitted data root", 403) from exc
    if not root.is_dir():
        raise MDResultError("MD_RESULTS_ABSENT", "MD job result directory is unavailable", 404)
    return root


def _load_inventory(job: MDJobRecord) -> tuple[Path, dict[str, Any], list[ResolvedMDArtifact]]:
    root = _job_root(job)
    aggregate = _load_json(root / "manifest.json", "MD_RESULTS_ABSENT")
    if aggregate.get("schema") != "bms.md.aggregate.v1" or aggregate.get("job_id") != job.id:
        raise MDResultError("MD_MANIFEST_JOB_MISMATCH", "MD aggregate manifest is not associated with this job")
    replicas = aggregate.get("replicas")
    if not isinstance(replicas, list) or len(replicas) > MAX_REPLICAS:
        raise MDResultError("MD_MANIFEST_INVALID", "MD replica inventory is invalid")
    inventory: list[ResolvedMDArtifact] = []
    for aggregate_replica in replicas:
        if not isinstance(aggregate_replica, Mapping):
            raise MDResultError("MD_MANIFEST_INVALID", "MD replica entry is invalid")
        replica_index = aggregate_replica.get("replica_index")
        if isinstance(replica_index, bool) or not isinstance(replica_index, int) or replica_index < 0:
            raise MDResultError("MD_MANIFEST_INVALID", "MD replica identity is invalid")
        manifest_path = root / "replicas" / f"replica_{replica_index}" / "manifest.json"
        manifest = _load_json(manifest_path, "MD_REPLICA_MANIFEST_INVALID")
        if (manifest.get("schema"), manifest.get("job_id"), manifest.get("replica_index")) != (
            "bms.md.run.v1", job.id, replica_index
        ):
            raise MDResultError("MD_MANIFEST_JOB_MISMATCH", "Replica manifest identity does not match this job")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, Mapping):
            raise MDResultError("MD_REPLICA_MANIFEST_INVALID", "Replica artifact inventory is invalid")
        for name, record in artifacts.items():
            if not isinstance(name, str) or not isinstance(record, Mapping):
                raise MDResultError("MD_REPLICA_MANIFEST_INVALID", "Replica artifact record is invalid")
            size, digest = record.get("bytes"), record.get("sha256")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not SHA256.fullmatch(digest):
                raise MDResultError("MD_REPLICA_MANIFEST_INVALID", "Replica artifact identity is invalid")
            path = _contained(manifest_path.parent, str(record.get("path") or ""))
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise MDResultError("MD_ARTIFACT_PATH_INVALID", "Replica artifact is outside the job result root") from exc
            opaque_id = hashlib.sha256(f"{job.id}:{replica_index}:{name}:{digest}".encode("utf-8")).hexdigest()[:32]
            inventory.append(ResolvedMDArtifact(
                artifact_id=opaque_id, replica_index=replica_index, name=name,
                path=path, bytes=size, sha256=digest,
                semantic_role=record.get("semantic_role") if isinstance(record.get("semantic_role"), str) else None,
                atom_order_identity=record.get("atom_order_identity") if isinstance(record.get("atom_order_identity"), str) else None,
                selection_method=record.get("selection_method") if isinstance(record.get("selection_method"), str) else None,
                source_frame=record.get("source_frame") if type(record.get("source_frame")) is int else None,
                time_ps=float(record["time_ps"]) if type(record.get("time_ps")) in {int, float} else None,
                source_trajectory_sha256=record.get("source_trajectory_sha256")
                if isinstance(record.get("source_trajectory_sha256"), str) and SHA256.fullmatch(record["source_trajectory_sha256"])
                else None,
            ))
            if len(inventory) > MAX_ARTIFACTS:
                raise MDResultError("MD_MANIFEST_INVALID", "MD artifact inventory exceeds its bound")
        trajectory_hashes = {
            record.get("sha256")
            for record in artifacts.values()
            if isinstance(record, Mapping) and record.get("semantic_role") == "analysis_trajectory"
        }
        representative_records = [
            record
            for record in artifacts.values()
            if isinstance(record, Mapping) and record.get("semantic_role") == "representative_structure"
        ]
        if representative_records and (
            len(trajectory_hashes) != 1
            or any(record.get("source_trajectory_sha256") not in trajectory_hashes for record in representative_records)
        ):
            raise MDResultError(
                "MD_REPRESENTATIVE_STRUCTURE_PROVENANCE_INVALID",
                "Replica final structure is not bound to the governed analysis trajectory",
                409,
            )
    analysis_root = root / "analysis"
    for aggregate_replica in replicas:
        replica_index = int(aggregate_replica["replica_index"])
        sidecar_path = analysis_root / f"md_analysis_replica_{replica_index}.artifacts.json"
        if not sidecar_path.is_file():
            continue
        sidecar = _load_json(sidecar_path, "MD_ANALYSIS_ARTIFACT_MANIFEST_INVALID")
        replica_manifest = root / "replicas" / f"replica_{replica_index}" / "manifest.json"
        if (
            sidecar.get("schema") != "bms.md.analysis-artifacts.v1"
            or sidecar.get("job_id") != job.id
            or sidecar.get("replica") != replica_index
            or sidecar.get("input_manifest_sha256") != _digest(replica_manifest)
        ):
            raise MDResultError("MD_ANALYSIS_ARTIFACT_MANIFEST_INVALID", "MD analysis artifact lineage is invalid")
        records = sidecar.get("artifacts")
        if not isinstance(records, Mapping):
            raise MDResultError("MD_ANALYSIS_ARTIFACT_MANIFEST_INVALID", "MD analysis artifact inventory is invalid")
        for name, record in records.items():
            if not isinstance(name, str) or not isinstance(record, Mapping):
                raise MDResultError("MD_ANALYSIS_ARTIFACT_MANIFEST_INVALID", "MD analysis artifact record is invalid")
            size, digest = record.get("bytes"), record.get("sha256")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not SHA256.fullmatch(digest):
                raise MDResultError("MD_ANALYSIS_ARTIFACT_MANIFEST_INVALID", "MD analysis artifact identity is invalid")
            path = _contained(analysis_root, str(record.get("path") or ""))
            opaque_id = hashlib.sha256(f"{job.id}:analysis:{replica_index}:{name}:{digest}".encode("utf-8")).hexdigest()[:32]
            inventory.append(ResolvedMDArtifact(
                artifact_id=opaque_id,
                replica_index=replica_index,
                name=name,
                path=path,
                bytes=size,
                sha256=digest,
                semantic_role=record.get("semantic_role") if isinstance(record.get("semantic_role"), str) else None,
                atom_order_identity=None,
                selection_method=None,
                source_frame=None,
                time_ps=None,
                source_trajectory_sha256=None,
            ))
            if len(inventory) > MAX_ARTIFACTS:
                raise MDResultError("MD_MANIFEST_INVALID", "MD artifact inventory exceeds its bound")
    return root, aggregate, inventory


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_analysis_implementation_sha256() -> str:
    repo_root = Path(__file__).resolve().parents[4]
    paths = [
        repo_root / "scripts" / "bms_md" / "analysis.py",
        repo_root / "scripts" / "bms_md" / "analyzers.py",
        repo_root / "scripts" / "bms_md" / "contract.py",
        repo_root / "schemas" / "md_analysis_v1.schema.json",
    ]
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda value: value.relative_to(repo_root).as_posix()):
        digest.update(path.relative_to(repo_root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _analysis_identity(payload: Mapping[str, Any]) -> str:
    scientific_content = {
        key: value
        for key, value in payload.items()
        if key not in {"created_at", "analysis_identity_sha256"}
    }
    encoded = json.dumps(scientific_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_artifact(job: MDJobRecord, artifact_id: str, *, verify: bool = True) -> ResolvedMDArtifact:
    _root, _aggregate, inventory = _load_inventory(job)
    artifact = next((item for item in inventory if item.artifact_id == artifact_id), None)
    if artifact is None:
        raise MDResultError("MD_ARTIFACT_UNKNOWN", "Unknown MD artifact", 404)
    if not artifact.path.is_file():
        raise MDResultError("MD_ARTIFACT_MISSING", "MD artifact is unavailable", 404)
    if verify and (artifact.path.stat().st_size != artifact.bytes or _digest(artifact.path) != artifact.sha256):
        raise MDResultError("MD_ARTIFACT_CHECKSUM_MISMATCH", "MD artifact no longer matches its immutable manifest", 409)
    return artifact


def _open_artifact_beneath(root: Path, path: Path) -> int:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise MDResultError("MD_ARTIFACT_PATH_INVALID", "MD artifact escapes the job result root", 403) from exc
    directory_descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_descriptor,
            )
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        return os.open(
            relative.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor,
        )
    except OSError as exc:
        raise MDResultError("MD_ARTIFACT_MISSING", "MD artifact is unavailable", 404) from exc
    finally:
        os.close(directory_descriptor)


def open_verified_artifact(job: MDJobRecord, artifact_id: str) -> tuple[ResolvedMDArtifact, BinaryIO]:
    root, _aggregate, inventory = _load_inventory(job)
    artifact = next((item for item in inventory if item.artifact_id == artifact_id), None)
    if artifact is None:
        raise MDResultError("MD_ARTIFACT_UNKNOWN", "Unknown MD artifact", 404)
    descriptor = _open_artifact_beneath(root, artifact.path)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size != artifact.bytes:
            raise MDResultError("MD_ARTIFACT_CHECKSUM_MISMATCH", "MD artifact no longer matches its immutable manifest", 409)
        digest = hashlib.sha256()
        consumed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            consumed += len(chunk)
            digest.update(chunk)
        if consumed != artifact.bytes or digest.hexdigest() != artifact.sha256:
            raise MDResultError("MD_ARTIFACT_CHECKSUM_MISMATCH", "MD artifact no longer matches its immutable manifest", 409)
        os.lseek(descriptor, 0, os.SEEK_SET)
        handle = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        return artifact, handle
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def artifact_inventory(job: MDJobRecord) -> dict[str, Any]:
    _root, _aggregate, inventory = _load_inventory(job)
    return {
        "schema": "bms.md.artifact-inventory.v1", "job_id": job.id,
        "source": "validated_job_owned_manifests", "bounded": True,
        "artifacts": [{
            "id": item.artifact_id, "replica": item.replica_index, "name": item.name,
            "bytes": item.bytes, "sha256": item.sha256, "semantic_role": item.semantic_role,
            "atom_order_identity": item.atom_order_identity,
            "selection_method": item.selection_method,
            "source_frame": item.source_frame,
            "time_ps": item.time_ps,
            "source_trajectory_sha256": item.source_trajectory_sha256,
            "format": item.path.suffix.lower().removeprefix("."),
            "content_url": f"/api/jobs/{job.id}/md/artifacts/{item.artifact_id}/content",
        } for item in inventory],
    }


def analysis_report(job: MDJobRecord) -> dict[str, Any]:
    root, aggregate, inventory = _load_inventory(job)
    reports: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    for replica in aggregate["replicas"]:
        index = int(replica["replica_index"])
        path = root / "analysis" / f"md_analysis_replica_{index}.json"
        if not path.is_file():
            states.append({"replica": index, "status": "absent"})
            continue
        report_artifact = next(
            (item for item in inventory if item.replica_index == index and item.semantic_role == "md_analysis_report"),
            None,
        )
        if report_artifact is None or path != report_artifact.path or path.stat().st_size != report_artifact.bytes or _digest(path) != report_artifact.sha256:
            raise MDResultError("MD_ANALYSIS_REPORT_STALE", "MD analysis report does not match its immutable artifact manifest", 409)
        report = _load_json(path, "MD_ANALYSIS_REPORT_INVALID")
        report_identity = report.get("analysis_identity_sha256")
        sidecar = _load_json(
            root / "analysis" / f"md_analysis_replica_{index}.artifacts.json",
            "MD_ANALYSIS_ARTIFACT_MANIFEST_INVALID",
        )
        if (
            report.get("schema") != "bms.md.analysis.v1"
            or report.get("replica") not in {index, None}
            or not isinstance(report_identity, str)
            or not SHA256.fullmatch(report_identity)
            or report_identity != _analysis_identity(report)
            or sidecar.get("analysis_identity_sha256") != report_identity
        ):
            raise MDResultError("MD_ANALYSIS_REPORT_INVALID", "MD analysis report identity is invalid")
        derived = report.get("derived_artifacts")
        sidecar_artifacts = sidecar.get("artifacts") if isinstance(sidecar, Mapping) else None
        if report.get("status") == "completed":
            if not isinstance(derived, Mapping) or set(derived) != {"timeseries", "residue_metrics"} or not isinstance(sidecar_artifacts, Mapping):
                raise MDResultError("MD_ANALYSIS_REPORT_INVALID", "MD analysis derived-artifact set is incomplete")
            for name, declared in derived.items():
                sidecar_record = sidecar_artifacts.get(name)
                if (
                    not isinstance(declared, Mapping)
                    or not isinstance(sidecar_record, Mapping)
                    or {key: declared.get(key) for key in ("path", "bytes", "sha256")}
                    != {key: sidecar_record.get(key) for key in ("path", "bytes", "sha256")}
                    or sidecar_record.get("semantic_role") != f"md_analysis_{name}"
                ):
                    raise MDResultError("MD_ANALYSIS_REPORT_INVALID", "MD analysis derived artifacts do not match their sidecar")
        points = report.get("points", [])
        if not isinstance(points, list) or len(points) > MAX_ANALYSIS_POINTS:
            raise MDResultError("MD_ANALYSIS_REPORT_INVALID", "MD analysis report exceeds its point bound")
        residue_metrics = report.get("residue_metrics", [])
        if not isinstance(residue_metrics, list) or len(residue_metrics) > MAX_RESIDUE_METRICS:
            raise MDResultError("MD_ANALYSIS_REPORT_INVALID", "MD analysis report exceeds its residue-metric bound")
        manifest_path = root / "replicas" / f"replica_{index}" / "manifest.json"
        inputs = report.get("inputs")
        if not isinstance(inputs, Mapping) or inputs.get("manifest_sha256") != _digest(manifest_path):
            raise MDResultError("MD_ANALYSIS_REPORT_STALE", "MD analysis report is not bound to the current replica manifest", 409)
        if report.get("status") == "completed":
            tool = report.get("tool")
            if (
                not isinstance(tool, Mapping)
                or tool.get("runtime_sif_sha256") != "3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68"
                or tool.get("implementation_sha256") != expected_analysis_implementation_sha256()
            ):
                raise MDResultError("MD_ANALYSIS_REPORT_STALE", "MD analysis report runtime or implementation identity is not authoritative", 409)
            replica_artifacts = [item for item in inventory if item.replica_index == index]
            topology = next((item for item in replica_artifacts if item.semantic_role == "analysis_topology"), None)
            trajectory = next((item for item in replica_artifacts if item.semantic_role == "analysis_trajectory"), None)
            atom_order = next((item for item in replica_artifacts if item.semantic_role == "atom_order_manifest"), None)
            if topology is None or trajectory is None or atom_order is None or (
                inputs.get("topology_sha256"),
                inputs.get("trajectory_sha256"),
                inputs.get("atom_order_manifest_sha256"),
                inputs.get("atom_order_identity"),
            ) != (
                topology.sha256,
                trajectory.sha256,
                atom_order.sha256,
                topology.atom_order_identity,
            ) or atom_order.atom_order_identity != topology.atom_order_identity:
                raise MDResultError("MD_ANALYSIS_REPORT_STALE", "MD analysis report inputs do not match current artifacts", 409)
        reports.append(report)
        states.append({"replica": index, "status": report.get("status", "failed")})
    completed_reports = [report for report in reports if report.get("status") == "completed" and isinstance(report.get("summary"), Mapping)]
    replica_means = [float(report["summary"]["mean"]) for report in completed_reports]
    replica_finals = [float(report["summary"]["final"]) for report in completed_reports]
    ensemble = {
        "statistical_unit": "replica",
        "frame_pooling": False,
        "completed_replicas": len(completed_reports),
        "mean_of_replica_mean_rmsd_angstrom": statistics.fmean(replica_means) if replica_means else None,
        "sample_stdev_of_replica_mean_rmsd_angstrom": statistics.stdev(replica_means) if len(replica_means) >= 2 else None,
        "mean_of_replica_final_rmsd_angstrom": statistics.fmean(replica_finals) if replica_finals else None,
        "sample_stdev_of_replica_final_rmsd_angstrom": statistics.stdev(replica_finals) if len(replica_finals) >= 2 else None,
    }
    overall = "absent" if not reports else ("completed" if all(item["status"] == "completed" for item in states) else "partial")
    return {
        "schema": "bms.md.analysis-report-set.v1", "job_id": job.id,
        "source": "validated_job_owned_analysis_reports", "status": overall,
        "bounded": True, "replica_states": states, "reports": reports,
        "ensemble": ensemble,
        "evidence": {
            "status": "insufficient_evidence",
            "reason": "RMSD/RMSF/Rg traces and replica summaries do not by themselves establish equilibrium or population-level certainty",
            "frames_are_independent_replicates": False,
        },
    }


def completion_barrier(job: MDJobRecord) -> dict[str, Any]:
    root, aggregate, inventory = _load_inventory(job)
    replica_indices = [int(item["replica_index"]) for item in aggregate["replicas"]]
    if (
        aggregate.get("status") != "completed"
        or len(replica_indices) != len(set(replica_indices))
        or sorted(replica_indices) != list(range(len(replica_indices)))
    ):
        raise MDResultError("MD_COMPLETION_BLOCKED", "MD replica collection is incomplete or inconsistent", 409)

    lineage = aggregate.get("lineage")
    if not isinstance(lineage, Mapping):
        raise MDResultError("MD_COMPLETION_BLOCKED", "MD replica lineage is missing", 409)
    replica_child_ids = lineage.get("child_ids")
    if (
        lineage.get("completed_children") != len(replica_indices)
        or lineage.get("failed_children") != 0
        or lineage.get("cancelled_children") != 0
        or not isinstance(replica_child_ids, list)
        or len(replica_child_ids) != len(replica_indices)
        or len(set(replica_child_ids)) != len(replica_child_ids)
    ):
        raise MDResultError("MD_COMPLETION_BLOCKED", "MD replica child cardinality is invalid", 409)

    job_spec = (job.params or {}).get("md_job_spec") if isinstance(job.params, Mapping) else None
    if not isinstance(job_spec, Mapping):
        raise MDResultError("MD_COMPLETION_BLOCKED", "MD requested scientific protocol is missing", 409)
    expected_count = job_spec.get("replicas")
    base_seed = job_spec.get("random_seed")
    if type(expected_count) is not int or expected_count != len(replica_indices):
        raise MDResultError("MD_COMPLETION_BLOCKED", "MD replica count does not match the requested job contract", 409)
    if type(base_seed) is not int or base_seed < 1:
        raise MDResultError("MD_COMPLETION_BLOCKED", "MD requested seed contract is missing", 409)

    replica_hashes: dict[int, str] = {}
    replica_seeds: set[int] = set()
    required_roles = {
        "analysis_topology",
        "analysis_trajectory",
        "atom_order_manifest",
        "representative_structure",
    }
    roles_by_replica: dict[int, set[str]] = {index: set() for index in replica_indices}
    for artifact in inventory:
        if artifact.semantic_role:
            roles_by_replica[artifact.replica_index].add(artifact.semantic_role)
    for index in replica_indices:
        manifest_path = root / "replicas" / f"replica_{index}" / "manifest.json"
        manifest = _load_json(manifest_path, "MD_REPLICA_MANIFEST_INVALID")
        try:
            _schema_validator("md_run_v1.schema.json").validate(manifest)
        except ValidationError as exc:
            raise MDResultError("MD_COMPLETION_BLOCKED", f"MD replica {index} fails its run schema", 409) from exc
        seed = manifest.get("replica_seed")
        if not _replica_protocol_matches(job_spec, manifest.get("config")):
            raise MDResultError("MD_COMPLETION_BLOCKED", "MD replica configuration does not match the requested scientific protocol", 409)
        engine = manifest.get("engine")
        if (
            not isinstance(engine, Mapping)
            or engine.get("name") != job_spec.get("engine")
            or engine.get("runtime") != job_spec.get("engine_runtime")
        ):
            raise MDResultError("MD_COMPLETION_BLOCKED", "MD replica engine identity does not match the requested scientific protocol", 409)
        if type(seed) is not int or manifest.get("replica_index") != index or seed != base_seed + index or seed in replica_seeds:
            raise MDResultError("MD_COMPLETION_BLOCKED", "MD replica index or seed lineage is invalid", 409)
        if not required_roles.issubset(roles_by_replica[index]):
            raise MDResultError("MD_COMPLETION_BLOCKED", f"MD replica {index} is missing required artifact roles", 409)
        replica_seeds.add(seed)
        replica_hashes[index] = _digest(manifest_path)

    collection_path = root / "analysis" / "manifest.json"
    collection = _load_json(collection_path, "MD_ANALYSIS_COLLECTION_INVALID")
    analyses = collection.get("analyses")
    analysis_child_ids = collection.get("child_ids")
    analysis_indices = (
        [int(item["replica_index"]) for item in analyses]
        if isinstance(analyses, list)
        and all(isinstance(item, Mapping) and type(item.get("replica_index")) is int for item in analyses)
        else []
    )
    replica_manifest_set_sha256 = hashlib.sha256(
        json.dumps(sorted(replica_hashes.items()), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if (
        collection.get("schema") != "bms.md.analysis-collection.v1"
        or collection.get("status") != "completed"
        or collection.get("job_id") != job.id
        or collection.get("aggregate_manifest_sha256") != _digest(root / "manifest.json")
        or collection.get("replica_manifest_set_sha256") != replica_manifest_set_sha256
        or collection.get("required_analysis_children") != len(replica_indices)
        or collection.get("completed_analysis_children") != len(replica_indices)
        or collection.get("failed_analysis_children") != 0
        or collection.get("cancelled_analysis_children") != 0
        or not isinstance(analyses, list)
        or sorted(analysis_indices) != replica_indices
        or not isinstance(analysis_child_ids, list)
        or len(analysis_child_ids) != len(replica_indices)
        or len(set(analysis_child_ids)) != len(analysis_child_ids)
    ):
        raise MDResultError("MD_COMPLETION_BLOCKED", "MD analysis collection is incomplete or inconsistent", 409)

    report_set = analysis_report(job)
    reports = report_set.get("reports") or []
    if report_set.get("status") != "completed" or len(reports) != len(replica_indices):
        raise MDResultError("MD_COMPLETION_BLOCKED", "Required MD analysis reports are not complete", 409)
    for expected_index, report in zip(replica_indices, reports, strict=True):
        try:
            _schema_validator("md_analysis_v1.schema.json").validate(report)
        except ValidationError as exc:
            raise MDResultError("MD_COMPLETION_BLOCKED", f"MD analysis report {expected_index} fails its schema", 409) from exc
        if not isinstance(report, Mapping) or report.get("replica") != expected_index:
            raise MDResultError("MD_COMPLETION_BLOCKED", "MD analysis report replica lineage is invalid", 409)

    for artifact in inventory:
        _artifact, handle = open_verified_artifact(job, artifact.artifact_id)
        handle.close()

    barrier = _load_json(root / "md_completion_barrier.json", "MD_COMPLETION_BARRIER_INVALID")
    aggregate_sha256 = _digest(root / "manifest.json")
    analysis_sha256 = _digest(collection_path)
    if (
        barrier.get("schema") != "bms.md.completion-barrier.v1"
        or barrier.get("status") != "completed"
        or barrier.get("job_id") != job.id
        or barrier.get("aggregate_manifest_sha256") != aggregate_sha256
        or barrier.get("analysis_manifest_sha256") != analysis_sha256
    ):
        raise MDResultError("MD_COMPLETION_BLOCKED", "MD completion marker does not bind the accepted generation", 409)

    prior_md = (job.provenance or {}).get("md") if isinstance(job.provenance, Mapping) else None
    accepted = {
        "aggregate_manifest_sha256": aggregate_sha256,
        "replica_manifest_set_sha256": replica_manifest_set_sha256,
        "analysis_manifest_sha256": analysis_sha256,
        "completion_barrier_sha256": _digest(root / "md_completion_barrier.json"),
    }
    if isinstance(prior_md, Mapping):
        for key in ("aggregate_manifest_sha256", "replica_manifest_set_sha256"):
            prior_value = prior_md.get(key)
            if isinstance(prior_value, str) and prior_value != accepted[key]:
                raise MDResultError("MD_COMPLETION_CONFLICT", "MD completion conflicts with the accepted dynamics generation", 409)
        if prior_md.get("state") == "completed" and any(prior_md.get(key) != value for key, value in accepted.items()):
            raise MDResultError("MD_COMPLETION_CONFLICT", "MD completion replay conflicts with the accepted generation", 409)

    return {
        "schema": "bms.md.lifecycle.v1",
        "state": "completed",
        "result_state": "completed",
        "dynamics_state": "completed",
        "analysis_state": "completed",
        "aggregate_manifest_sha256": aggregate_sha256,
        "replica_manifest_set_sha256": replica_manifest_set_sha256,
        "analysis_manifest_sha256": analysis_sha256,
        "completion_barrier_sha256": _digest(root / "md_completion_barrier.json"),
        "replica_child_ids": list(replica_child_ids),
        "analysis_child_ids": list(analysis_child_ids),
        "replica_count": len(replica_indices),
    }


def apply_completion_barrier(job: MDJobRecord) -> dict[str, Any]:
    snapshot = completion_barrier(job)
    provenance = dict(getattr(job, "provenance", None) or {})
    provenance["md"] = snapshot
    setattr(job, "provenance", provenance)
    setattr(job, "status", "completed")
    setattr(job, "queue_status", "completed")
    setattr(job, "current_stage", "Complete")
    setattr(job, "stage_progress", None)
    setattr(job, "error_message", None)
    setattr(job, "completed_at", datetime.utcnow())
    return snapshot


def _trajectory_playback(inventory: list[ResolvedMDArtifact]) -> dict[str, Any]:
    by_replica: dict[int, list[ResolvedMDArtifact]] = {}
    for artifact in inventory:
        by_replica.setdefault(artifact.replica_index, []).append(artifact)
    replicas: list[dict[str, Any]] = []
    for replica_index, artifacts in sorted(by_replica.items()):
        trajectories = [item for item in artifacts if item.semantic_role == "analysis_trajectory" and item.path.suffix.lower() == ".xtc"]
        topologies = [item for item in artifacts if item.semantic_role == "analysis_topology"]
        frame_maps = [item for item in artifacts if item.semantic_role == "trajectory_frame_map"]
        if not trajectories and not frame_maps:
            continue
        if len(trajectories) != 1 or len(topologies) != 1 or len(frame_maps) != 1:
            raise MDResultError("MD_TRAJECTORY_PLAYBACK_MANIFEST_INVALID", "MD playback requires one XTC trajectory, topology, and frame map per replica")
        trajectory, topology, frame_map = trajectories[0], topologies[0], frame_maps[0]
        if not trajectory.atom_order_identity or topology.atom_order_identity != trajectory.atom_order_identity:
            raise MDResultError("MD_TRAJECTORY_PLAYBACK_ATOM_ORDER_INVALID", "MD playback topology and trajectory atom order are not identical")
        if frame_map.source_trajectory_sha256 != trajectory.sha256:
            raise MDResultError("MD_TRAJECTORY_PLAYBACK_PROVENANCE_INVALID", "MD playback frame map is not bound to the governed trajectory")
        if frame_map.path.stat().st_size != frame_map.bytes or _digest(frame_map.path) != frame_map.sha256:
            raise MDResultError("MD_TRAJECTORY_PLAYBACK_FRAME_MAP_CHECKSUM_MISMATCH", "MD playback frame map no longer matches its immutable manifest", 409)
        payload = _load_json(frame_map.path, "MD_TRAJECTORY_PLAYBACK_FRAME_MAP_INVALID")
        if payload.get("schema") != "bms.md.trajectory-frame-map.v1" or payload.get("replica") != replica_index or payload.get("trajectory_sha256") != trajectory.sha256:
            raise MDResultError("MD_TRAJECTORY_PLAYBACK_FRAME_MAP_INVALID", "MD playback frame map identity is invalid")
        frames = payload.get("frames")
        if not isinstance(frames, list) or not frames or len(frames) > MAX_TRAJECTORY_PLAYBACK_FRAMES:
            raise MDResultError("MD_TRAJECTORY_PLAYBACK_FRAME_MAP_INVALID", "MD playback frame map is empty or exceeds its bound")
        previous_source_frame = -1
        previous_time_ps = -1.0
        for display_frame, frame in enumerate(frames):
            if not isinstance(frame, Mapping) or frame.get("display_frame") != display_frame:
                raise MDResultError("MD_TRAJECTORY_PLAYBACK_FRAME_MAP_INVALID", "MD playback display-frame mapping is invalid")
            source_frame, time_ps, step = frame.get("source_frame"), frame.get("time_ps"), frame.get("step")
            if (
                type(source_frame) is not int or source_frame < 0 or source_frame <= previous_source_frame
                or type(step) is not int or step < 0
                or not isinstance(time_ps, (int, float)) or isinstance(time_ps, bool)
            ):
                raise MDResultError("MD_TRAJECTORY_PLAYBACK_FRAME_MAP_INVALID", "MD playback source-frame, time, or step mapping is invalid")
            time_value = float(time_ps)
            if not math.isfinite(time_value) or time_value < previous_time_ps:
                raise MDResultError("MD_TRAJECTORY_PLAYBACK_FRAME_MAP_INVALID", "MD playback source-frame, time, or step mapping is invalid")
            previous_source_frame, previous_time_ps = source_frame, time_value
        replicas.append({
            "replica": replica_index, "trajectory_sha256": trajectory.sha256,
            "frame_map_artifact_id": frame_map.artifact_id, "frame_count": len(frames),
            "first_source_frame": frames[0]["source_frame"], "last_source_frame": frames[-1]["source_frame"],
            "first_time_ps": float(frames[0]["time_ps"]), "last_time_ps": float(frames[-1]["time_ps"]),
        })
    if not replicas:
        return {"supported": False, "reason": "No checksum-bound XTC trajectory, topology, and authoritative frame map are available"}
    return {"supported": True, "replicas": replicas}


def summary(job: MDJobRecord) -> dict[str, Any]:
    root, aggregate, inventory = _load_inventory(job)
    analysis = analysis_report(job)
    replica_summaries = []
    for replica in aggregate["replicas"]:
        index = int(replica["replica_index"])
        manifest = _load_json(root / "replicas" / f"replica_{index}" / "manifest.json", "MD_REPLICA_MANIFEST_INVALID")
        engine = manifest.get("engine") if isinstance(manifest.get("engine"), Mapping) else {}
        production = manifest.get("stages", {}).get("production", {}) if isinstance(manifest.get("stages"), Mapping) else {}
        performance = production.get("performance", {}) if isinstance(production, Mapping) else {}
        replica_summaries.append({
            "replica": index,
            "status": manifest.get("status"),
            "engine": {key: engine.get(key) for key in ("name", "version", "platform") if isinstance(engine.get(key), (str, int, float, bool))},
            "performance": {str(key): value for key, value in list(performance.items())[:16] if isinstance(value, (int, float)) and not isinstance(value, bool)} if isinstance(performance, Mapping) else {},
        })
    lifecycle = (job.provenance or {}).get("md") if isinstance(job.provenance, Mapping) else None
    return {
        "schema": "bms.md.summary.v1", "job_id": job.id, "status": aggregate.get("status"),
        "result_state": lifecycle.get("result_state") if isinstance(lifecycle, Mapping) else None,
        "source": "validated_job_owned_manifests", "bounded": True,
        "aggregate_manifest_sha256": _digest(root / "manifest.json"),
        "replica_count": len(aggregate["replicas"]), "artifact_count": len(inventory),
        "replicas": replica_summaries,
        "analysis_status": analysis["status"], "trajectory_playback": _trajectory_playback(inventory),
    }


def build_analysis_work_items(job: MDJobRecord) -> dict[str, Any]:
    root, aggregate, _inventory = _load_inventory(job)
    items = []
    for replica in aggregate["replicas"]:
        index = int(replica["replica_index"])
        manifest = root / "replicas" / f"replica_{index}" / "manifest.json"
        items.append({
            "schema": "bms.md.analysis-work-item.v1", "job_id": job.id, "replica_index": index,
            "manifest": str(manifest), "manifest_sha256": _digest(manifest),
            "state_authority": "source_contract_only_no_durable_md_analysis_table",
        })
    return {"schema": "bms.md.analysis-work-items.v1", "job_id": job.id, "items": items, "retryable": True}
