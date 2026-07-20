from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol

from paths import get_data_root


class MDJobRecord(Protocol):
    id: str
    model_id: str
    output_dir: str | None
    child_output_dir: str | None

SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_REPLICAS = 64
MAX_ARTIFACTS = 1024
MAX_ANALYSIS_POINTS = 10_000


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


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MDResultError(code, "MD result manifest is unavailable or invalid", 404) from exc
    if not isinstance(payload, dict):
        raise MDResultError(code, "MD result manifest must be an object")
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
            ))
            if len(inventory) > MAX_ARTIFACTS:
                raise MDResultError("MD_MANIFEST_INVALID", "MD artifact inventory exceeds its bound")
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


def artifact_inventory(job: MDJobRecord) -> dict[str, Any]:
    _root, _aggregate, inventory = _load_inventory(job)
    return {
        "schema": "bms.md.artifact-inventory.v1", "job_id": job.id,
        "source": "validated_job_owned_manifests", "bounded": True,
        "artifacts": [{
            "id": item.artifact_id, "replica": item.replica_index, "name": item.name,
            "bytes": item.bytes, "sha256": item.sha256, "semantic_role": item.semantic_role,
            "atom_order_identity": item.atom_order_identity,
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
        if report.get("schema") != "bms.md.analysis.v1" or report.get("replica") not in {index, None}:
            raise MDResultError("MD_ANALYSIS_REPORT_INVALID", "MD analysis report identity is invalid")
        points = report.get("points", [])
        if not isinstance(points, list) or len(points) > MAX_ANALYSIS_POINTS:
            raise MDResultError("MD_ANALYSIS_REPORT_INVALID", "MD analysis report exceeds its point bound")
        manifest_path = root / "replicas" / f"replica_{index}" / "manifest.json"
        inputs = report.get("inputs")
        if not isinstance(inputs, Mapping) or inputs.get("manifest_sha256") != _digest(manifest_path):
            raise MDResultError("MD_ANALYSIS_REPORT_STALE", "MD analysis report is not bound to the current replica manifest", 409)
        if report.get("status") == "completed":
            replica_artifacts = [item for item in inventory if item.replica_index == index]
            topology = next((item for item in replica_artifacts if item.semantic_role == "analysis_topology"), None)
            trajectory = next((item for item in replica_artifacts if item.semantic_role == "analysis_trajectory"), None)
            if topology is None or trajectory is None or (
                inputs.get("topology_sha256"), inputs.get("trajectory_sha256"), inputs.get("atom_order_identity")
            ) != (topology.sha256, trajectory.sha256, topology.atom_order_identity):
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
    return {
        "schema": "bms.md.summary.v1", "job_id": job.id, "status": aggregate.get("status"),
        "source": "validated_job_owned_manifests", "bounded": True,
        "replica_count": len(aggregate["replicas"]), "artifact_count": len(inventory),
        "replicas": replica_summaries,
        "analysis_status": analysis["status"], "trajectory_playback": {
            "supported": False, "reason": "Molstar 4.5 XTC/DCD playback has not been proven against a real job artifact",
        },
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
