from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import ViewerSnapshotRecord
from services.viewer_resource_contracts import (
    SHA256_RE,
    UUID_RE,
    ValidatedSnapshotCreate,
    ViewerResourceError,
    canonical_json_bytes,
)

MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_VOLUMES = 32
MAX_SEGMENTATIONS = 32
MAX_REGISTRATIONS = 128
MAX_AXIS = 4096
MAX_VOXELS = 536_870_912
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024


def _valid_structure_document_id(value: Any) -> bool:
    """Accept direct-viewer document identities without treating them as filesystem paths."""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 255
        and all(character.isalnum() or character in "._:-" for character in value)
    )


@dataclass(frozen=True)
class ViewerArtifact:
    artifact_id: str
    path: Path
    sha256: str
    size_bytes: int
    mime_type: str


def _output_root(job: Any) -> Path:
    output_dir = getattr(job, "output_dir", None)
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise ViewerResourceError("Job has no viewer output directory", code="VIEWER_RESOURCE_NOT_FOUND", status_code=404)
    root = Path(output_dir).expanduser().resolve()
    if not root.is_dir():
        raise ViewerResourceError("Job viewer output directory is unavailable", code="VIEWER_RESOURCE_NOT_FOUND", status_code=404)
    return root


def _contained_file(root: Path, relative_path: Any) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip() or Path(relative_path).is_absolute():
        raise ViewerResourceError("Viewer artifact path must be a contained relative path")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ViewerResourceError("Viewer artifact path is not contained by the job output") from exc
    if not candidate.is_file():
        raise ViewerResourceError("Viewer artifact is unavailable", code="VIEWER_RESOURCE_NOT_FOUND", status_code=404)
    return candidate


def _validate_volume(volume: Any, root: Path) -> dict[str, Any]:
    if not isinstance(volume, dict) or volume.get("schemaVersion") != 1 or volume.get("format") != "ccp4":
        raise ViewerResourceError("Unsupported volume descriptor schema or format")
    if volume.get("semanticKind") not in {"density", "electrostatic_potential", "segmentation", "other_scalar"}:
        raise ViewerResourceError("Unsupported volume semantic kind")
    if not isinstance(volume.get("volumeId"), str) or not UUID_RE.fullmatch(volume["volumeId"]):
        raise ViewerResourceError("Volume ID must be an opaque UUID")
    if not isinstance(volume.get("artifactId"), str) or not UUID_RE.fullmatch(volume["artifactId"]):
        raise ViewerResourceError("Volume artifact ID must be an opaque UUID")
    dimensions = volume.get("dimensions")
    if not isinstance(dimensions, list) or len(dimensions) != 3 or any(not isinstance(value, int) or isinstance(value, bool) or value < 1 or value > MAX_AXIS for value in dimensions):
        raise ViewerResourceError("Volume dimensions exceed admission limits")
    if dimensions[0] * dimensions[1] * dimensions[2] > MAX_VOXELS:
        raise ViewerResourceError("Volume voxel count exceeds admission limits")
    axis_order = volume.get("axisOrder")
    transform = volume.get("gridToWorldRowMajor4x4")
    if not isinstance(axis_order, list) or sorted(axis_order) != [0, 1, 2]:
        raise ViewerResourceError("Volume axis order is invalid")
    if not isinstance(transform, list) or len(transform) != 16 or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in transform):
        raise ViewerResourceError("Volume grid transform is invalid")
    if volume.get("coordinateUnits") != "Å" or not isinstance(volume.get("channelCount"), int) or isinstance(volume.get("channelCount"), bool) or volume["channelCount"] < 1:
        raise ViewerResourceError("Volume coordinate units or channel count are invalid")
    if volume["semanticKind"] in {"density", "electrostatic_potential"} and volume.get("valueUnits") not in {"e/Å³", "V", "kT/e", "dimensionless", "arbitrary"}:
        raise ViewerResourceError("Density and electrostatic volumes require explicit value units")
    if volume["semanticKind"] == "segmentation" and volume.get("valueUnits") is not None:
        raise ViewerResourceError("Segmentation volume value units must be absent")
    statistics = volume.get("statistics")
    if statistics is not None:
        if not isinstance(statistics, dict) or not {"min", "max"}.issubset(statistics) or any(key not in {"min", "max", "mean", "sigma"} for key in statistics):
            raise ViewerResourceError("Volume statistics are incomplete")
        values = [statistics.get("min"), statistics.get("max")]
        if "mean" in statistics:
            values.append(statistics["mean"])
        if "sigma" in statistics:
            values.append(statistics["sigma"])
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in values):
            raise ViewerResourceError("Volume statistics are invalid")
        if float(statistics["min"]) > float(statistics["max"]) or "sigma" in statistics and float(statistics["sigma"]) <= 0:
            raise ViewerResourceError("Volume statistics are invalid")
    recommended = volume.get("recommendedDisplay")
    if recommended is not None:
        if not isinstance(recommended, dict) or any(key not in {"channel", "contourAbsolute", "contourSigma", "opacity"} for key in recommended):
            raise ViewerResourceError("Recommended volume presentation is invalid")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in recommended.values()):
            raise ViewerResourceError("Recommended volume presentation is invalid")
        if "channel" in recommended and (not isinstance(recommended["channel"], int) or not 0 <= recommended["channel"] < volume["channelCount"]):
            raise ViewerResourceError("Recommended volume channel is invalid")
        if "opacity" in recommended and not 0 <= float(recommended["opacity"]) <= 1:
            raise ViewerResourceError("Recommended volume opacity is invalid")
        if "contourSigma" in recommended and (statistics is None or statistics.get("mean") is None or statistics.get("sigma") is None):
            raise ViewerResourceError("Recommended sigma contour lacks authoritative mean/sigma")
    artifact_hash = volume.get("artifactSha256")
    if not isinstance(artifact_hash, str) or not SHA256_RE.fullmatch(artifact_hash) or not isinstance(volume.get("provenanceRef"), str) or not volume["provenanceRef"].strip():
        raise ViewerResourceError("Volume artifact SHA-256 or provenance is invalid")
    path = _contained_file(root, volume.get("relativePath"))
    size = path.stat().st_size
    if size < 1 or size > MAX_ARTIFACT_BYTES or volume.get("byteLength") != size:
        raise ViewerResourceError("Volume artifact byte length mismatch")
    return {**volume, "_resolved_path": str(path)}


def _validate_segmentation(segmentation: Any, volumes: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    if not isinstance(segmentation, dict) or segmentation.get("schema") != "bms.viewer.volume-segmentation.v1":
        raise ViewerResourceError("Unsupported segmentation schema")
    for key in ("segmentationId", "volumeId", "artifactId"):
        if not isinstance(segmentation.get(key), str) or not UUID_RE.fullmatch(segmentation[key]):
            raise ViewerResourceError(f"Segmentation {key} must be an opaque UUID")
    if not isinstance(segmentation.get("artifactSha256"), str) or not SHA256_RE.fullmatch(segmentation["artifactSha256"]):
        raise ViewerResourceError("Segmentation artifact SHA-256 is invalid")
    if not isinstance(segmentation.get("provenanceRef"), str) or not segmentation["provenanceRef"].strip():
        raise ViewerResourceError("Segmentation provenance is required")
    source = next((entry for entry in volumes if entry.get("volumeId") == segmentation["volumeId"]), None)
    if source is None or source.get("semanticKind") != "segmentation":
        raise ViewerResourceError("Segmentation must reference a supplied segmentation volume")
    labels = segmentation.get("labels")
    if not isinstance(labels, list):
        raise ViewerResourceError("Segmentation labels must be a list")
    ids: set[int] = set()
    by_id: dict[int, dict[str, Any]] = {}
    for label in labels:
        if not isinstance(label, dict) or not isinstance(label.get("segmentId"), int) or isinstance(label.get("segmentId"), bool) or label["segmentId"] < 0:
            raise ViewerResourceError("Segment IDs must be nonnegative integers")
        segment_id = label["segmentId"]
        if segment_id in ids:
            raise ViewerResourceError("Segment IDs must be unique")
        ids.add(segment_id)
        by_id[segment_id] = label
        color = label.get("recommendedColor")
        if color is not None and (not isinstance(color, int) or isinstance(color, bool) or not 0 <= color <= 0xFFFFFF):
            raise ViewerResourceError("Segment recommended color is invalid")
        if label.get("label") is not None and not isinstance(label.get("label"), str):
            raise ViewerResourceError("Segment label must be text or null")
    for segment_id, label in by_id.items():
        parent = label.get("parentSegmentId")
        if parent is not None and (parent == segment_id or parent not in ids):
            raise ViewerResourceError("Segment hierarchy parent is invalid")
        visited = {segment_id}
        while parent is not None:
            if parent in visited:
                raise ViewerResourceError("Segment hierarchy must be acyclic")
            visited.add(parent)
            parent = by_id[parent].get("parentSegmentId")
    if "relativePath" in segmentation:
        path = _contained_file(root, segmentation["relativePath"])
        if sha256(path.read_bytes()).hexdigest() != segmentation["artifactSha256"]:
            raise ViewerResourceError("Segmentation artifact hash mismatch", code="VIEWER_HASH_MISMATCH", status_code=412)
        return {**segmentation, "_resolved_path": str(path)}
    return dict(segmentation)


def _validate_registration(registration: Any, volumes: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(registration, dict) or registration.get("schema") != "bms.viewer.volume-registration.v1":
        raise ViewerResourceError("Volume registration schema is invalid")
    if registration.get("method") != "supplied_transform_v1" or not isinstance(registration.get("provenanceRef"), str) or not registration["provenanceRef"].strip():
        raise ViewerResourceError("Only provenance-bound supplied registration is supported")
    for key in ("registrationId", "volumeId"):
        if not isinstance(registration.get(key), str) or not UUID_RE.fullmatch(registration[key]):
            raise ViewerResourceError(f"Volume registration {key} is invalid")
    if not _valid_structure_document_id(registration.get("structureDocumentId")):
        raise ViewerResourceError("Volume registration structureDocumentId is invalid")
    for key in ("artifactSha256", "structureSha256", "volumeSha256"):
        if not isinstance(registration.get(key), str) or not SHA256_RE.fullmatch(registration[key]):
            raise ViewerResourceError(f"Volume registration {key} is invalid")
    volume = next((entry for entry in volumes if entry["volumeId"] == registration["volumeId"]), None)
    if volume is None or registration["volumeSha256"] != volume["artifactSha256"] or volume.get("registrationRef") != registration["registrationId"]:
        raise ViewerResourceError("Volume registration binding does not match the governed volume")
    matrix = registration.get("transformRowMajor4x4")
    if not isinstance(matrix, list) or len(matrix) != 16 or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in matrix):
        raise ViewerResourceError("Volume registration transform is invalid")
    canonical_payload = {key: value for key, value in registration.items() if key != "artifactSha256"}
    if sha256(canonical_json_bytes(canonical_payload)).hexdigest() != registration["artifactSha256"]:
        raise ViewerResourceError("Volume registration canonical hash mismatch", code="VIEWER_HASH_MISMATCH", status_code=412)
    return dict(registration)


def load_volume_inventory(job: Any) -> dict[str, Any]:
    root = _output_root(job)
    manifest_path = _contained_file(root, "viewer/volumes.json")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ViewerResourceError("Volume manifest exceeds 8 MiB", code="VIEWER_REQUEST_TOO_LARGE", status_code=413)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ViewerResourceError("Volume manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != "bms.viewer.volume-list.v1" or manifest.get("jobId") != getattr(job, "id", None):
        raise ViewerResourceError("Volume manifest identity is invalid")
    volumes = manifest.get("volumes")
    segmentations = manifest.get("segmentations", [])
    registrations = manifest.get("registrations", [])
    if (not isinstance(volumes, list) or len(volumes) > MAX_VOLUMES or not isinstance(segmentations, list) or len(segmentations) > MAX_SEGMENTATIONS
            or not isinstance(registrations, list) or len(registrations) > MAX_REGISTRATIONS):
        raise ViewerResourceError("Volume manifest resource count exceeds v1 limits", code="VIEWER_REQUEST_TOO_LARGE", status_code=413)
    validated = [_validate_volume(volume, root) for volume in volumes]
    validated_segmentations = [_validate_segmentation(segmentation, validated, root) for segmentation in segmentations]
    validated_registrations = [_validate_registration(registration, validated) for registration in registrations]
    public_volumes = [{key: value for key, value in volume.items() if key not in {"relativePath", "_resolved_path"}} for volume in validated]
    public_segmentations = [{key: value for key, value in entry.items() if key not in {"relativePath", "_resolved_path", "byteLength", "mimeType"}} for entry in validated_segmentations]
    return {"schema": manifest["schema"], "jobId": manifest["jobId"], "volumes": public_volumes, "segmentations": public_segmentations, "registrations": validated_registrations}


def resolve_viewer_artifact(job: Any, artifact_id: str, *, verify: bool) -> ViewerArtifact:
    root = _output_root(job)
    manifest_path = _contained_file(root, "viewer/volumes.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ViewerResourceError("Volume manifest is unreadable") from exc
    volumes = manifest.get("volumes", [])
    volume = next((entry for entry in volumes if isinstance(entry, dict) and entry.get("artifactId") == artifact_id), None)
    if volume is not None:
        validated = _validate_volume(volume, root)
        path = Path(validated["_resolved_path"])
        expected = str(validated["artifactSha256"])
        mime_type = "application/octet-stream"
    else:
        segmentation = next((entry for entry in manifest.get("segmentations", []) if isinstance(entry, dict) and entry.get("artifactId") == artifact_id), None)
        if segmentation is None:
            raise ViewerResourceError("Viewer artifact not found", code="VIEWER_RESOURCE_NOT_FOUND", status_code=404)
        validated_segmentation = _validate_segmentation(segmentation, [_validate_volume(entry, root) for entry in volumes], root)
        if "_resolved_path" not in validated_segmentation:
            raise ViewerResourceError("Segmentation metadata has no separately deliverable artifact", code="VIEWER_RESOURCE_NOT_FOUND", status_code=404)
        path = Path(validated_segmentation["_resolved_path"])
        expected = str(validated_segmentation["artifactSha256"])
        mime_type = "application/json"
    if verify:
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ViewerResourceError("Viewer artifact hash mismatch", code="VIEWER_HASH_MISMATCH", status_code=412)
    return ViewerArtifact(artifact_id=artifact_id, path=path, sha256=expected, size_bytes=path.stat().st_size, mime_type=mime_type)


async def create_snapshot_record(
    session: AsyncSession,
    job_id: str,
    validated: ValidatedSnapshotCreate,
    *,
    created_by: str,
) -> ViewerSnapshotRecord:
    existing = (
        await session.execute(
            select(ViewerSnapshotRecord).where(
                ViewerSnapshotRecord.id == validated.snapshot_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ViewerResourceError("Viewer snapshot already exists", code="VIEWER_STATE_CONFLICT", status_code=409)
    record = ViewerSnapshotRecord(
        id=validated.snapshot_id,
        job_id=job_id,
        label=validated.label,
        created_by=created_by,
        schema_version=2,
        snapshot_sha256=validated.snapshot_sha256,
        snapshot_json=validated.snapshot,
    )
    session.add(record)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ViewerResourceError("Viewer snapshot already exists", code="VIEWER_STATE_CONFLICT", status_code=409) from exc
    await session.refresh(record)
    return record


async def list_snapshot_records(
    session: AsyncSession,
    job_id: str,
    *,
    limit: int,
    created_by: str,
) -> list[ViewerSnapshotRecord]:
    bounded = max(1, min(limit, 100))
    result = await session.execute(
        select(ViewerSnapshotRecord)
        .where(ViewerSnapshotRecord.job_id == job_id, ViewerSnapshotRecord.created_by == created_by)
        .order_by(ViewerSnapshotRecord.created_at.desc(), ViewerSnapshotRecord.id.asc())
        .limit(bounded)
    )
    return list(result.scalars().all())


async def get_snapshot_record(
    session: AsyncSession,
    job_id: str,
    snapshot_id: str,
    *,
    created_by: str,
) -> ViewerSnapshotRecord:
    record = (
        await session.execute(
            select(ViewerSnapshotRecord).where(
                ViewerSnapshotRecord.id == snapshot_id,
                ViewerSnapshotRecord.job_id == job_id,
                ViewerSnapshotRecord.created_by == created_by,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise ViewerResourceError("Viewer snapshot not found", code="VIEWER_RESOURCE_NOT_FOUND", status_code=404)
    return record


async def delete_snapshot_record(session: AsyncSession, job_id: str, snapshot_id: str, *, created_by: str) -> None:
    record = await get_snapshot_record(session, job_id, snapshot_id, created_by=created_by)
    await session.delete(record)
    await session.commit()


def serialize_snapshot_record(record: ViewerSnapshotRecord, *, include_snapshot: bool) -> dict[str, Any]:
    return {
        "schema": "bms.viewer.snapshot-record.v2",
        "snapshotId": record.id,
        "jobId": record.job_id,
        "label": record.label,
        "createdBy": record.created_by,
        "createdAt": record.created_at.isoformat() + ("Z" if record.created_at.tzinfo is None else ""),
        "schemaVersion": record.schema_version,
        "snapshotSha256": record.snapshot_sha256,
        **({"snapshot": record.snapshot_json} if include_snapshot else {}),
    }
