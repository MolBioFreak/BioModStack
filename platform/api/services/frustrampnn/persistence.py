"""Manifest-first transactional persistence for canonical FrustraMPNN bundles."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    Design,
    FrustraMPNNArtifact,
    FrustraMPNNLandscapeRow,
    FrustraMPNNResult,
    Job,
)
from .contracts import canonical_json_bytes, canonical_json_loads
from .manifests import MANIFEST_PATH, ManifestValidationError, validate_result_manifest
from .structure import StructureNormalizationError, read_structure_bytes


class FrustraMPNNPersistenceError(RuntimeError):
    """A result bundle cannot be validated or persisted atomically."""


class FrustraMPNNConflictError(FrustraMPNNPersistenceError):
    """An invocation ID already names a different immutable closure."""


@dataclass(frozen=True)
class ValidatedResultBundle:
    root: Path
    manifest_bytes: bytes
    manifest: dict[str, Any]
    request: dict[str, Any]
    summary: dict[str, Any]
    receipt: dict[str, Any]
    terminal_result: dict[str, Any]
    landscape: dict[str, Any]
    artifact_records: tuple[dict[str, Any], ...]


_ARTIFACT_CONTRACT: dict[str, tuple[str, str]] = {
    "workflow_component_request_v1.json": ("component_request", "application/json"),
    "authority_artifact_v1.json": ("identity_authority", "application/json"),
    "normalized_input.pdb": ("normalized_input", "chemical/x-pdb"),
    "frustrampnn_structure_map_v1.json": ("structure_map", "application/json"),
    "raw_frustrampnn.csv": ("raw_csv", "text/csv"),
    "frustrampnn_landscape_v1.json": ("landscape", "application/json"),
    "frustrampnn_summary_v1.json": ("summary", "application/json"),
    "frustrampnn_stdout.log": ("stdout", "text/plain"),
    "frustrampnn_stderr.log": ("stderr", "text/plain"),
    "frustrampnn_execution_receipt_v1.json": ("execution_receipt", "application/json"),
    "workflow_component_result_v1.json": ("terminal_result", "application/json"),
}
_TERMINAL_AUTHORITY_FIELDS = (
    "invocation_id",
    "parent_job_id",
    "parent_workflow_id",
    "candidate_id",
    "request_sha256",
    "source_artifact",
    "runtime_identity",
    "assigned_gpu",
    "status",
)
_RESULT_IMMUTABLE_FIELDS = (
    "invocation_id",
    "parent_job_id",
    "parent_workflow_id",
    "candidate_id",
    "design_id",
    "requiredness",
    "request_sha256",
    "source_artifact_id",
    "source_artifact_sha256",
    "manifest_sha256",
    "manifest_json",
    "summary_sha256",
    "summary_json",
    "runtime_identity_json",
    "assigned_gpu_json",
    "terminal_result_json",
    "parent_metadata_json",
)


def _read_regular(root: Path, relative_path: str) -> bytes:
    """Read one direct child without following the root or artifact symlink."""

    if not relative_path or relative_path in {".", ".."} or "/" in relative_path or "\\" in relative_path:
        raise FrustraMPNNPersistenceError(
            f"FrustraMPNN artifact path is not a direct canonical child: {relative_path!r}"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    root_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        root_fd = os.open(os.fspath(root), root_flags)
    except OSError as exc:
        raise FrustraMPNNPersistenceError(
            f"FrustraMPNN bundle root is not an exact regular directory: {root}"
        ) from exc
    descriptor = -1
    try:
        descriptor = os.open(relative_path, flags, dir_fd=root_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise FrustraMPNNPersistenceError(
                f"FrustraMPNN artifact is not a regular file: {relative_path}"
            )
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            return handle.read()
    except OSError as exc:
        raise FrustraMPNNPersistenceError(
            f"FrustraMPNN artifact cannot be opened without following symlinks: {relative_path}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_fd)


def _canonical_object(payload: bytes, relative_path: str) -> dict[str, Any]:
    try:
        value = canonical_json_loads(payload)
    except Exception as exc:
        raise FrustraMPNNPersistenceError(
            f"FrustraMPNN artifact is invalid JSON: {relative_path}"
        ) from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != payload:
        raise FrustraMPNNPersistenceError(
            f"FrustraMPNN artifact is not an exact canonical JSON object: {relative_path}"
        )
    return value


def _terminal_identity_matches(
    terminal_envelope: Mapping[str, Any], terminal_result: Mapping[str, Any]
) -> bool:
    return all(
        field in terminal_envelope
        and terminal_envelope[field] == terminal_result.get(field)
        for field in _TERMINAL_AUTHORITY_FIELDS
    )


def load_and_validate_result_bundle(
    bundle_root: Path | str,
    *,
    expected_parent_job_id: str,
    terminal_envelope: Mapping[str, Any],
) -> ValidatedResultBundle:
    """Load the physical manifest first, then invoke the public full validator."""

    root = Path(bundle_root).absolute()
    manifest_bytes = _read_regular(root, MANIFEST_PATH)
    manifest = _canonical_object(manifest_bytes, MANIFEST_PATH)
    try:
        payloads = validate_result_manifest(root, manifest)
    except ManifestValidationError as exc:
        raise FrustraMPNNPersistenceError(
            f"FrustraMPNN result manifest validation failed: {exc}"
        ) from exc

    manifest_bytes = payloads[MANIFEST_PATH]
    manifest = _canonical_object(manifest_bytes, MANIFEST_PATH)
    for record in manifest["artifacts"]:
        relative_path = record["relative_path"]
        payload = payloads[relative_path]
        if hashlib.sha256(payload).hexdigest() != record["sha256"]:
            raise FrustraMPNNPersistenceError(
                f"FrustraMPNN artifact changed after validation: {relative_path}"
            )
        if len(payload) != record["bytes"]:
            raise FrustraMPNNPersistenceError(
                f"FrustraMPNN artifact size changed after validation: {relative_path}"
            )


    request = _canonical_object(
        payloads["workflow_component_request_v1.json"],
        "workflow_component_request_v1.json",
    )
    summary = _canonical_object(
        payloads["frustrampnn_summary_v1.json"],
        "frustrampnn_summary_v1.json",
    )
    receipt = _canonical_object(
        payloads["frustrampnn_execution_receipt_v1.json"],
        "frustrampnn_execution_receipt_v1.json",
    )
    terminal_result = _canonical_object(
        payloads["workflow_component_result_v1.json"],
        "workflow_component_result_v1.json",
    )
    landscape = _canonical_object(
        payloads["frustrampnn_landscape_v1.json"],
        "frustrampnn_landscape_v1.json",
    )

    if not expected_parent_job_id or manifest["parent_job_id"] != expected_parent_job_id:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN manifest parent job does not equal the current job"
        )
    if request["parent_job_id"] != expected_parent_job_id:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN request parent job does not equal the current job"
        )
    if not isinstance(terminal_envelope, Mapping) or not _terminal_identity_matches(
        terminal_envelope, terminal_result
    ):
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN terminal envelope does not exactly match result authority"
        )
    if terminal_envelope["parent_job_id"] != expected_parent_job_id:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN terminal envelope parent job does not equal the current job"
        )

    try:
        final_payloads = validate_result_manifest(root, manifest)
    except ManifestValidationError as exc:
        raise FrustraMPNNPersistenceError(
            f"FrustraMPNN result bundle changed after validated ingestion snapshot: {exc}"
        ) from exc
    if final_payloads != payloads:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN result bundle changed after validated ingestion snapshot"
        )

    return ValidatedResultBundle(
        root=root,
        manifest_bytes=manifest_bytes,
        manifest=manifest,
        request=request,
        summary=summary,
        receipt=receipt,
        terminal_result=terminal_result,
        landscape=landscape,
        artifact_records=tuple(dict(record) for record in manifest["artifacts"]),
    )


def _stable_id(kind: str, *parts: Any) -> str:
    payload = canonical_json_bytes([kind, *parts])
    return hashlib.sha256(payload).hexdigest()


def _artifact_values(bundle: ValidatedResultBundle) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    invocation_id = bundle.manifest["invocation_id"]
    parent_job_id = bundle.manifest["parent_job_id"]
    for record in bundle.artifact_records:
        relative_path = record["relative_path"]
        try:
            role, media_type = _ARTIFACT_CONTRACT[relative_path]
        except KeyError as exc:
            raise FrustraMPNNPersistenceError(
                f"FrustraMPNN manifest contains an unknown artifact role: {relative_path}"
            ) from exc
        values.append(
            {
                "artifact_id": _stable_id(
                    "frustrampnn-artifact-v1",
                    parent_job_id,
                    invocation_id,
                    relative_path,
                    record["sha256"],
                ),
                "parent_job_id": parent_job_id,
                "invocation_id": invocation_id,
                "role": role,
                "relative_path": relative_path,
                "storage_path": os.fspath(bundle.root / relative_path),
                "content_sha256": record["sha256"],
                "size_bytes": record["bytes"],
                "media_type": media_type,
                "metadata_json": dict(record),
            }
        )
    return values


def _landscape_values(bundle: ValidatedResultBundle) -> list[dict[str, Any]]:
    landscape = bundle.landscape
    invocation_id = bundle.manifest["invocation_id"]
    parent_job_id = bundle.manifest["parent_job_id"]
    provenance = {
        "landscape_sha256": hashlib.sha256(
            canonical_json_bytes(landscape)
        ).hexdigest(),
        "structure_map_sha256": landscape["structure_map_sha256"],
        "normalized_pdb_sha256": landscape["normalized_pdb_sha256"],
        "raw_csv_sha256": landscape["raw_csv_sha256"],
        "threshold_policy": landscape["threshold_policy"],
        "threshold_policy_sha256": landscape["threshold_policy_sha256"],
    }
    values: list[dict[str, Any]] = []
    for residue in landscape["residues"]:
        residue_json = {key: value for key, value in residue.items() if key != "slots"}
        for slot in residue["slots"]:
            row_json = {"residue": residue_json, "slot": dict(slot)}
            identity = (
                landscape["target_id"],
                residue["entity_instance_id"],
                residue["auth_asym_id"],
                str(residue["auth_seq_id"]),
                residue["insertion_code"],
                residue["sequence_index"],
                residue["wt"],
                slot["mutation_aa"],
            )
            values.append(
                {
                    "id": _stable_id(
                        "frustrampnn-landscape-row-v1",
                        parent_job_id,
                        invocation_id,
                        *identity,
                    ),
                    "parent_job_id": parent_job_id,
                    "invocation_id": invocation_id,
                    "target_id": landscape["target_id"],
                    "entity_instance_id": residue["entity_instance_id"],
                    "auth_asym_id": residue["auth_asym_id"],
                    "auth_seq_id": str(residue["auth_seq_id"]),
                    "insertion_code": residue["insertion_code"],
                    "sequence_index": residue["sequence_index"],
                    "wt": residue["wt"],
                    "mutation_aa": slot["mutation_aa"],
                    "score": slot["score"],
                    "score_class": slot["class"],
                    "scoreable": slot["scoreable"],
                    "status": slot["status"],
                    "reason": slot["reason"],
                    "row_json": row_json,
                    "provenance_json": dict(provenance),
                }
            )
    return values


def _result_values(
    bundle: ValidatedResultBundle,
    design_id: str | None,
    parent_metadata_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    request = bundle.request
    manifest = bundle.manifest
    summary_record = next(
        record
        for record in bundle.artifact_records
        if record["relative_path"] == "frustrampnn_summary_v1.json"
    )
    return {
        "invocation_id": manifest["invocation_id"],
        "parent_job_id": manifest["parent_job_id"],
        "parent_workflow_id": request["parent_workflow_id"],
        "candidate_id": manifest["candidate_id"],
        "design_id": design_id,
        "requiredness": request["requiredness"],
        "request_sha256": manifest["request_sha256"],
        "source_artifact_id": request["source_artifact"].get("artifact_id"),
        "source_artifact_sha256": manifest["source_sha256"],
        "manifest_sha256": hashlib.sha256(bundle.manifest_bytes).hexdigest(),
        "manifest_json": dict(manifest),
        "summary_sha256": summary_record["sha256"],
        "summary_json": dict(bundle.summary),
        "runtime_identity_json": dict(bundle.receipt),
        "assigned_gpu_json": dict(bundle.terminal_result["assigned_gpu"]),
        "terminal_result_json": dict(bundle.terminal_result),
        "parent_metadata_json": (
            canonical_json_loads(canonical_json_bytes(dict(parent_metadata_snapshot)))
            if parent_metadata_snapshot is not None
            else None
        ),
    }


async def _exact_design_link(
    session: AsyncSession,
    *,
    source_artifact_id: str | None,
    source_artifact_sha256: str,
    parent_job_id: str,
) -> Design | None:
    job = await session.get(Job, parent_job_id)
    if job is None:
        raise FrustraMPNNPersistenceError("FrustraMPNN current parent job does not exist")
    child_envelope = (job.params or {}).get("_frustrampnn_child_v1")
    if job.model_id == "frustrampnn" and isinstance(child_envelope, dict):
        selections = child_envelope.get("selection")
        if not isinstance(selections, list):
            raise FrustraMPNNPersistenceError("FrustraMPNN child selection authority is malformed")
        matches = [
            item for item in selections
            if isinstance(item, dict)
            and item.get("design_id") == source_artifact_id
            and item.get("normalized_source_sha256") == source_artifact_sha256
        ]
        if len(matches) != 1:
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN result does not exactly match one child source selection"
            )
        if source_artifact_id is None:
            if matches[0].get("source_job_id") is not None:
                raise FrustraMPNNPersistenceError("uploaded FrustraMPNN child has foreign Design authority")
            return None
        design = await session.get(Design, source_artifact_id)
        if design is None or str(design.job_id) != str(matches[0].get("source_job_id") or ""):
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN child source Design authority no longer matches its immutable selection"
            )
        return design
    if source_artifact_id is None:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN source artifact identity is required for exact Design association"
        )
    design = await session.get(Design, source_artifact_id)
    if design is not None and design.job_id != parent_job_id:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN source design/artifact does not belong to the authorized job"
        )
    if design is None:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN source artifact does not exactly match a persisted Design"
        )
    try:
        observed = hashlib.sha256(read_structure_bytes(design.pdb_path)).hexdigest()
    except (OSError, StructureNormalizationError) as exc:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN physical source structure is unavailable or unsafe"
        ) from exc
    if observed != source_artifact_sha256:
        raise FrustraMPNNPersistenceError(
            "FrustraMPNN physical source SHA-256 does not match the canonical bundle"
        )
    return design


def _model_values(model: Any, fields: Sequence[str]) -> dict[str, Any]:
    return {field: getattr(model, field) for field in fields}


def _strict_authority_values_equal(
    observed: Mapping[str, Any],
    expected: Mapping[str, Any],
    fields: Sequence[str],
) -> bool:
    """Compare JSON columns canonically so signed zero cannot evade replay checks."""

    for field in fields:
        left = observed.get(field)
        right = expected.get(field)
        if field.endswith("_json"):
            try:
                if canonical_json_bytes(left) != canonical_json_bytes(right):
                    return False
            except (TypeError, ValueError):
                return False
        elif left != right:
            return False
    return True


async def _assert_identical_replay(
    session: AsyncSession,
    existing: FrustraMPNNResult,
    result_values: Mapping[str, Any],
    artifact_values: Sequence[Mapping[str, Any]],
    landscape_values: Sequence[Mapping[str, Any]],
) -> None:
    if not _strict_authority_values_equal(
        _model_values(existing, _RESULT_IMMUTABLE_FIELDS),
        result_values,
        _RESULT_IMMUTABLE_FIELDS,
    ):
        raise FrustraMPNNConflictError(
            "FrustraMPNN invocation already exists with different result authority"
        )

    existing_artifacts = (
        await session.execute(
            select(FrustraMPNNArtifact)
            .where(
                FrustraMPNNArtifact.parent_job_id == existing.parent_job_id,
                FrustraMPNNArtifact.invocation_id == existing.invocation_id,
            )
            .order_by(FrustraMPNNArtifact.relative_path)
        )
    ).scalars().all()
    expected_artifacts = sorted(artifact_values, key=lambda value: value["relative_path"])
    artifact_fields = (
        "artifact_id",
        "parent_job_id",
        "invocation_id",
        "role",
        "relative_path",
        "content_sha256",
        "size_bytes",
        "media_type",
        "metadata_json",
    )
    if len(existing_artifacts) != len(expected_artifacts) or any(
        not _strict_authority_values_equal(
            _model_values(artifact, artifact_fields), value, artifact_fields
        )
        for artifact, value in zip(existing_artifacts, expected_artifacts, strict=True)
    ):
        raise FrustraMPNNConflictError(
            "FrustraMPNN invocation already exists with different artifact authority"
        )

    existing_rows = (
        await session.execute(
            select(FrustraMPNNLandscapeRow)
            .where(
                FrustraMPNNLandscapeRow.parent_job_id == existing.parent_job_id,
                FrustraMPNNLandscapeRow.invocation_id == existing.invocation_id,
            )
            .order_by(FrustraMPNNLandscapeRow.id)
        )
    ).scalars().all()
    expected_rows = sorted(landscape_values, key=lambda value: value["id"])
    landscape_fields = (
        "id",
        "parent_job_id",
        "invocation_id",
        "target_id",
        "entity_instance_id",
        "auth_asym_id",
        "auth_seq_id",
        "insertion_code",
        "sequence_index",
        "wt",
        "mutation_aa",
        "score",
        "score_class",
        "scoreable",
        "status",
        "reason",
        "row_json",
        "provenance_json",
    )
    if len(existing_rows) != len(expected_rows) or any(
        not _strict_authority_values_equal(
            _model_values(row, landscape_fields), value, landscape_fields
        )
        for row, value in zip(existing_rows, expected_rows, strict=True)
    ):
        raise FrustraMPNNConflictError(
            "FrustraMPNN invocation already exists with different landscape authority"
        )


def _apply_canonical_projection(design: Design, bundle: ValidatedResultBundle) -> None:
    result = bundle.terminal_result
    summary = bundle.summary
    # These compact scalar columns are the shared Design/AnalyticsDashboard
    # projection of the immutable canonical summary. Per-residue authority
    # remains exclusively in FrustraMPNNLandscapeRow and is never synthesized
    # into the historical JSON/CSV fields.
    design.frustration_high_count = int(summary["native_slot_counts"]["high"])
    design.frustration_min_count = int(summary["native_slot_counts"]["minimal"])
    design.frustration_pct_high = float(summary["native_slot_fractions"]["high"]) * 100.0
    design.frustrampnn_contract_version = str(result["component_contract_version"])
    design.frustrampnn_status = result["status"]
    design.frustrampnn_source_sha256 = bundle.request["source_artifact"]["sha256"]
    design.frustrampnn_manifest_relpath = MANIFEST_PATH
    design.frustrampnn_landscape_relpath = "frustrampnn_landscape_v1.json"
    design.frustrampnn_summary_relpath = "frustrampnn_summary_v1.json"
    design.frustrampnn_runtime_sha256 = result["runtime_identity"]["sif_sha256"]
    design.frustrampnn_failure_class = result["failure_class"]
    diagnostic = result["diagnostic"]
    design.frustrampnn_failure_detail = diagnostic[:1000] if diagnostic is not None else None


async def ingest_result_bundle(
    session: AsyncSession,
    bundle_root: Path | str,
    *,
    parent_job_id: str,
    terminal_envelope: Mapping[str, Any],
    commit: bool = True,
    validated_bundle: ValidatedResultBundle | None = None,
    parent_metadata_snapshot: Mapping[str, Any] | None = None,
) -> FrustraMPNNResult:
    """Validate and insert one immutable closure, or replay it byte-identically."""

    try:
        if validated_bundle is None:
            bundle = load_and_validate_result_bundle(
                bundle_root,
                expected_parent_job_id=parent_job_id,
                terminal_envelope=terminal_envelope,
            )
        else:
            bundle = validated_bundle
            if (
                bundle.root != Path(bundle_root).absolute()
                or bundle.manifest["parent_job_id"] != parent_job_id
                or not _terminal_identity_matches(terminal_envelope, bundle.terminal_result)
            ):
                raise FrustraMPNNPersistenceError(
                    "prevalidated FrustraMPNN bundle authority does not match ingestion request"
                )
        job = await session.get(Job, parent_job_id)
        if job is None:
            raise FrustraMPNNPersistenceError(
                "FrustraMPNN current parent job does not exist"
            )
        source_artifact_id = bundle.request["source_artifact"].get("artifact_id")
        artifact_values = _artifact_values(bundle)
        landscape_values = _landscape_values(bundle)
        design = await _exact_design_link(
            session,
            source_artifact_id=source_artifact_id,
            source_artifact_sha256=bundle.manifest["source_sha256"],
            parent_job_id=parent_job_id,
        )
        existing = await session.get(
            FrustraMPNNResult,
            (parent_job_id, bundle.manifest["invocation_id"]),
        )
        if existing is not None:
            result_values = _result_values(
                bundle, design.id if design is not None else None, parent_metadata_snapshot
            )
            await _assert_identical_replay(
                session,
                existing,
                result_values,
                artifact_values,
                landscape_values,
            )
            if design is not None:
                _apply_canonical_projection(design, bundle)
                await session.flush()
                if commit:
                    await session.commit()
            return existing

        result_values = _result_values(
            bundle, design.id if design is not None else None, parent_metadata_snapshot
        )

        result = FrustraMPNNResult(**result_values)
        session.add(result)
        # SQLite enforces the composite child foreign keys in production. Flush
        # the immutable parent authority before adding artifacts and landscape
        # rows; ORM add order alone does not establish that dependency here.
        await session.flush()
        session.add_all(FrustraMPNNArtifact(**values) for values in artifact_values)
        session.add_all(
            FrustraMPNNLandscapeRow(**values) for values in landscape_values
        )
        if design is not None:
            _apply_canonical_projection(design, bundle)
        await session.flush()
        if commit:
            await session.commit()
        return result
    except FrustraMPNNConflictError:
        await session.rollback()
        raise
    except FrustraMPNNPersistenceError:
        await session.rollback()
        raise
    except IntegrityError as exc:
        await session.rollback()
        raise FrustraMPNNConflictError(
            f"FrustraMPNN immutable persistence constraint conflict: {exc}"
        ) from exc
    except Exception as exc:
        await session.rollback()
        raise FrustraMPNNPersistenceError(
            f"FrustraMPNN bundle persistence rolled back: {exc}"
        ) from exc


async def get_result_projection(
    session: AsyncSession, parent_job_id: str, invocation_id: str
) -> dict[str, Any]:
    result = await session.get(FrustraMPNNResult, (parent_job_id, invocation_id))
    if result is None:
        raise FrustraMPNNPersistenceError("FrustraMPNN result does not exist")
    return {
        "invocation_id": result.invocation_id,
        "parent_job_id": result.parent_job_id,
        "parent_workflow_id": result.parent_workflow_id,
        "candidate_id": result.candidate_id,
        "design_id": result.design_id,
        "requiredness": result.requiredness,
        "request_sha256": result.request_sha256,
        "source_artifact_id": result.source_artifact_id,
        "source_artifact_sha256": result.source_artifact_sha256,
        "manifest_sha256": result.manifest_sha256,
        "summary_sha256": result.summary_sha256,
        "summary": result.summary_json,
        "runtime_identity": result.runtime_identity_json,
        "assigned_gpu": result.assigned_gpu_json,
        "created_at": result.created_at,
    }


async def list_result_artifacts(
    session: AsyncSession, parent_job_id: str, invocation_id: str
) -> list[dict[str, Any]]:
    artifacts = (
        await session.execute(
            select(FrustraMPNNArtifact)
            .where(
                FrustraMPNNArtifact.parent_job_id == parent_job_id,
                FrustraMPNNArtifact.invocation_id == invocation_id,
            )
            .order_by(FrustraMPNNArtifact.relative_path)
        )
    ).scalars().all()
    return [
        {
            "artifact_id": artifact.artifact_id,
            "invocation_id": artifact.invocation_id,
            "role": artifact.role,
            "relative_path": artifact.relative_path,
            "content_sha256": artifact.content_sha256,
            "size_bytes": artifact.size_bytes,
            "media_type": artifact.media_type,
            "metadata": artifact.metadata_json,
            "created_at": artifact.created_at,
        }
        for artifact in artifacts
    ]


async def paged_landscape(
    session: AsyncSession,
    parent_job_id: str,
    invocation_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    target_id: str | None = None,
    entity_instance_id: str | None = None,
    auth_asym_id: str | None = None,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 500))
    bounded_offset = max(0, int(offset))
    statement = select(FrustraMPNNLandscapeRow).where(
        FrustraMPNNLandscapeRow.parent_job_id == parent_job_id,
        FrustraMPNNLandscapeRow.invocation_id == invocation_id,
    )
    if target_id is not None:
        statement = statement.where(FrustraMPNNLandscapeRow.target_id == target_id)
    if entity_instance_id is not None:
        statement = statement.where(
            FrustraMPNNLandscapeRow.entity_instance_id == entity_instance_id
        )
    if auth_asym_id is not None:
        statement = statement.where(
            FrustraMPNNLandscapeRow.auth_asym_id == auth_asym_id
        )
    statement = statement.order_by(
        FrustraMPNNLandscapeRow.target_id,
        FrustraMPNNLandscapeRow.entity_instance_id,
        FrustraMPNNLandscapeRow.auth_asym_id,
        FrustraMPNNLandscapeRow.auth_seq_id,
        FrustraMPNNLandscapeRow.insertion_code,
        FrustraMPNNLandscapeRow.sequence_index,
        FrustraMPNNLandscapeRow.mutation_aa,
        FrustraMPNNLandscapeRow.id,
    ).offset(bounded_offset).limit(bounded_limit)
    rows = (await session.execute(statement)).scalars().all()
    return [
        {
            "id": row.id,
            "invocation_id": row.invocation_id,
            "target_id": row.target_id,
            "entity_instance_id": row.entity_instance_id,
            "auth_asym_id": row.auth_asym_id,
            "auth_seq_id": row.auth_seq_id,
            "insertion_code": row.insertion_code,
            "sequence_index": row.sequence_index,
            "wt": row.wt,
            "mutation_aa": row.mutation_aa,
            "score": row.score,
            "class": row.score_class,
            "scoreable": row.scoreable,
            "status": row.status,
            "reason": row.reason,
            "row": row.row_json,
            "provenance": row.provenance_json,
        }
        for row in rows
    ]


__all__ = [
    "FrustraMPNNPersistenceError",
    "FrustraMPNNConflictError",
    "ValidatedResultBundle",
    "load_and_validate_result_bundle",
    "ingest_result_bundle",
    "get_result_projection",
    "paged_landscape",
    "list_result_artifacts",
]
