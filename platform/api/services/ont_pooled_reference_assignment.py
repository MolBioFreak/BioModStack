"""Atomic pooled ONT reference-assignment submission and operator release."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    Job,
    MolBioNgsReceipt,
    NgsPooledAssignmentRelease,
    NgsPooledAssignmentReleaseTarget,
    NgsPooledReferenceTarget,
    NgsReferenceSetManifest,
)
from paths import get_inputs_dir, get_results_dir
from schemas import JobCreate, JobStatus
from services import alignment_access, ont_submission_trust
from services.molbio_ngs_receipts import consume_molbio_ngs_receipt

REFERENCE_SET_SCHEMA = "bms.ngs.reference-set.v1"
REFERENCE_SET_MODE = "pooled"
REFERENCE_SET_ROOT_NAME = "ngs_reference_sets"
ASSIGNMENT_WORKFLOW_ID = "ont_pooled_reference_assignment"
ASSIGNMENT_MODE = "pooled_reference_assignment"
SUMMARY_SCHEMA = "bms.ngs.pooled-reference-assignment-summary.v1"
OCCURRENCE_MAP_SCHEMA = "bms.ngs.fastq-occurrence-map.v1"
TARGET_LIST_SCHEMA = "bms.ngs.pooled-reference-target-list.v1"
RELEASE_SCHEMA = "bms.ngs.pooled-assignment-release.v1"
ASSIGNMENT_JOB_NAMESPACE = uuid.UUID("5b2d202a-a156-4f31-b97d-86eb9fb501dc")
TARGET_ROW_NAMESPACE = uuid.UUID("a2b9b4db-810d-4c34-a1b3-1040079e3275")
RELEASE_CHILD_NAMESPACE = uuid.UUID("615e97a5-78d0-45ff-8777-51143d1fa722")
RELEASE_TARGET_NAMESPACE = uuid.UUID("5a863e90-e2e5-4640-b067-c099ae5c0d56")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
TARGET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
IDENTITY_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{1,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OCCURRENCE_RE = re.compile(r"^occurrence_([1-9][0-9]*)$")
DNA_ALPHABET = frozenset("ACGTN")
MAX_ALIGNMENT_SCORE_MARGIN = 1_000_000


class PooledAssignmentError(ValueError):
    """Fail-closed pooled-assignment request, evidence, or persistence error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 422,
        code: str = "POOLED_ASSIGNMENT_INVALID",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class PooledReferenceTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    target_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=255)
    indistinguishable_group: str | None = Field(default=None, max_length=128)
    molbio_ngs_receipt_id: str = Field(min_length=1, max_length=36)

    @field_validator("target_id")
    @classmethod
    def validate_target_id(cls, value: str) -> str:
        if value != value.strip() or TARGET_ID_RE.fullmatch(value) is None:
            raise ValueError("target_id contains unsafe characters")
        return value

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("target label contains unsafe characters")
        return value

    @field_validator("indistinguishable_group")
    @classmethod
    def validate_group(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or GROUP_RE.fullmatch(value) is None):
            raise ValueError("indistinguishable_group contains unsafe characters")
        return value

    @field_validator("molbio_ngs_receipt_id")
    @classmethod
    def validate_receipt_id(cls, value: str) -> str:
        if value != value.strip() or not value:
            raise ValueError("MolBio NGS receipt ID has outer whitespace")
        return value


class PooledReferenceAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    idempotency_key: str = Field(min_length=1, max_length=255)
    fastq_path: str = Field(min_length=1, max_length=1000)
    targets: list[PooledReferenceTargetRequest] = Field(min_length=2, max_length=96)
    min_mapq: int = Field(default=20, ge=0, le=60)
    min_alignment_score_margin: int = Field(
        default=10, ge=0, le=MAX_ALIGNMENT_SCORE_MARGIN
    )
    name: str | None = Field(default=None, max_length=128)
    pinned_gpu: int | None = Field(default=None, ge=0, le=15)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if value != value.strip() or IDEMPOTENCY_RE.fullmatch(value) is None:
            raise ValueError("idempotency_key contains unsafe characters")
        return value

    @field_validator("fastq_path")
    @classmethod
    def validate_fastq_path(cls, value: str) -> str:
        if value != value.strip() or not value:
            raise ValueError("fastq_path has outer whitespace")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or ".." in value or SAFE_NAME_RE.fullmatch(value) is None
        ):
            raise ValueError("name contains unsafe characters")
        return value


class PooledAssignmentReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    idempotency_key: str = Field(min_length=1, max_length=255)
    target_workflow: str = Field(pattern=r"^(ont_plasmid_qc|ont_construct_screening)$")
    target_ids: list[str] = Field(min_length=1, max_length=96)
    name_prefix: str | None = Field(default=None, max_length=128)
    pinned_gpu: int | None = Field(default=None, ge=0, le=15)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if value != value.strip() or IDEMPOTENCY_RE.fullmatch(value) is None:
            raise ValueError("idempotency_key contains unsafe characters")
        return value

    @field_validator("target_ids")
    @classmethod
    def validate_target_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("duplicate target_ids are forbidden")
        if any(value != value.strip() or TARGET_ID_RE.fullmatch(value) is None for value in values):
            raise ValueError("target_ids contain unsafe characters")
        return values

    @field_validator("name_prefix")
    @classmethod
    def validate_name_prefix(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or ".." in value or SAFE_NAME_RE.fullmatch(value) is None
        ):
            raise ValueError("name_prefix contains unsafe characters")
        return value


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PooledAssignmentError("pooled assignment payload is not canonical JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise PooledAssignmentError(f"cannot digest required file: {path}", status_code=409) from exc
    return digest.hexdigest()


def _canonical_manifest_sha256(payload: Mapping[str, Any]) -> str:
    return _sha256_bytes(canonical_json_bytes({key: value for key, value in payload.items() if key != "manifest_sha256"}))


def _duplicate_rejecting_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PooledAssignmentError(f"duplicate JSON key is forbidden: {key}", status_code=409)
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_object)
    except PooledAssignmentError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PooledAssignmentError(f"{label} is unreadable or malformed", status_code=409) from exc
    if type(payload) is not dict:
        raise PooledAssignmentError(f"{label} must contain a JSON object", status_code=409)
    return payload, raw


def _confined_file(path: Path, root: Path, label: str, *, nonempty: bool = False) -> Path:
    if root.is_symlink():
        raise PooledAssignmentError(f"{label} root symlink is forbidden", status_code=409)
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise PooledAssignmentError(f"{label} root is unavailable", status_code=409) from exc
    if not root_resolved.is_dir():
        raise PooledAssignmentError(f"{label} root must be a directory", status_code=409)
    if path.is_symlink():
        raise PooledAssignmentError(f"{label} symlink is forbidden", status_code=409)
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise PooledAssignmentError(
            f"{label} must be confined beneath its server-owned root", status_code=409
        ) from exc
    cursor = root_resolved
    for component in relative.parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise PooledAssignmentError(f"{label} symlink is forbidden", status_code=409)
    if not resolved.is_file():
        raise PooledAssignmentError(f"{label} must be a regular file", status_code=409)
    if nonempty and resolved.stat().st_size == 0:
        raise PooledAssignmentError(f"{label} must be non-empty", status_code=409)
    return resolved


def _confined_directory(path: Path, root: Path, label: str) -> Path:
    if root.is_symlink() or path.is_symlink():
        raise PooledAssignmentError(f"{label} symlink is forbidden", status_code=409)
    try:
        root_resolved = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise PooledAssignmentError(f"{label} must be confined beneath its server-owned root", status_code=409) from exc
    cursor = root_resolved
    for component in relative.parts:
        cursor = cursor / component
        if cursor.is_symlink():
            raise PooledAssignmentError(f"{label} symlink is forbidden", status_code=409)
    if not resolved.is_dir():
        raise PooledAssignmentError(f"{label} must be a directory", status_code=409)
    return resolved


def _safe_relative_path(value: Any, label: str) -> PurePosixPath:
    if type(value) is not str or not value or "\\" in value or value.startswith("/"):
        raise PooledAssignmentError(f"{label} must be a relative POSIX path", status_code=409)
    path = PurePosixPath(value)
    if path.is_absolute() or any(component in {"", ".", ".."} for component in path.parts):
        raise PooledAssignmentError(f"{label} must be a canonical relative path", status_code=409)
    return path


def _read_single_fasta(path: Path) -> str:
    try:
        text_value = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise PooledAssignmentError("MolBio NGS receipt FASTA is unreadable", status_code=409) from exc
    records = 0
    sequence_chunks: list[str] = []
    saw_header = False
    for raw_line in text_value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            records += 1
            if records > 1 or len(line) == 1:
                raise PooledAssignmentError("MolBio NGS receipt FASTA must contain exactly one record", status_code=409)
            saw_header = True
            continue
        if not saw_header or any(character.isspace() for character in line):
            raise PooledAssignmentError("MolBio NGS receipt FASTA is malformed", status_code=409)
        sequence_chunks.append(line.upper())
    sequence = "".join(sequence_chunks)
    if records != 1 or not sequence or set(sequence) - DNA_ALPHABET:
        raise PooledAssignmentError("MolBio NGS receipt FASTA has an invalid DNA sequence", status_code=409)
    return sequence


def _normalized_submit_request(request: PooledReferenceAssignmentRequest, fastq_sha256: str) -> dict[str, Any]:
    targets = [target.model_dump() for target in request.targets]
    targets.sort(key=lambda value: value["target_id"])
    return {
        "idempotency_key": request.idempotency_key,
        "fastq_path": str(Path(request.fastq_path).expanduser()),
        "fastq_sha256": fastq_sha256,
        "targets": targets,
        "min_mapq": request.min_mapq,
        "min_alignment_score_margin": request.min_alignment_score_margin,
        "name": request.name,
        "pinned_gpu": request.pinned_gpu,
    }


def _submit_fingerprint(request: PooledReferenceAssignmentRequest, fastq_sha256: str) -> str:
    return _sha256_bytes(canonical_json_bytes(_normalized_submit_request(request, fastq_sha256)))


def _normalized_release_request(
    assignment_job_id: str,
    request: PooledAssignmentReleaseRequest,
    summary_sha256: str,
) -> dict[str, Any]:
    return {
        "assignment_job_id": assignment_job_id,
        "idempotency_key": request.idempotency_key,
        "target_workflow": request.target_workflow,
        "target_ids": sorted(request.target_ids),
        "name_prefix": request.name_prefix,
        "pinned_gpu": request.pinned_gpu,
        "assignment_summary_sha256": summary_sha256,
    }


def _release_fingerprint(
    assignment_job_id: str,
    request: PooledAssignmentReleaseRequest,
    summary_sha256: str,
) -> str:
    return _sha256_bytes(canonical_json_bytes(_normalized_release_request(assignment_job_id, request, summary_sha256)))


async def _begin_immediate(session: AsyncSession) -> None:
    if session.in_transaction():
        if session.new or session.dirty or session.deleted:
            raise PooledAssignmentError("session has uncommitted external writes", status_code=409)
        await session.rollback()
    await session.execute(text("BEGIN IMMEDIATE"))


async def _validate_receipts(
    session: AsyncSession,
    requests: Sequence[PooledReferenceTargetRequest],
) -> list[dict[str, Any]]:
    target_ids = [target.target_id for target in requests]
    receipt_ids = [target.molbio_ngs_receipt_id for target in requests]
    if len(target_ids) != len(set(target_ids)):
        raise PooledAssignmentError("duplicate target_id values are forbidden")
    if len(receipt_ids) != len(set(receipt_ids)):
        raise PooledAssignmentError("duplicate MolBio NGS receipts are forbidden")
    rows = (
        await session.execute(select(MolBioNgsReceipt).where(MolBioNgsReceipt.id.in_(receipt_ids)))
    ).scalars().all()
    by_id = {str(row.id): row for row in rows}
    if set(by_id) != set(receipt_ids):
        raise PooledAssignmentError("one or more MolBio NGS receipts are missing")

    inputs_root = get_inputs_dir()
    validated: list[dict[str, Any]] = []
    sequences: dict[str, list[dict[str, Any]]] = {}
    revision_ids: set[str] = set()
    for target in sorted(requests, key=lambda value: value.target_id):
        receipt = by_id[target.molbio_ngs_receipt_id]
        try:
            await consume_molbio_ngs_receipt(session, receipt_id=str(receipt.id))
        except ValueError as exc:
            raise PooledAssignmentError(str(exc), status_code=409, code="RECEIPT_INVALID") from exc
        sequence_id = str(receipt.sequence_id or "")
        revision_id = str(receipt.revision_id or "")
        revision_sha256 = str(receipt.revision_sha256 or "").lower()
        snapshot_sha256 = str(receipt.reference_snapshot_sha256 or "").lower()
        if (
            IDENTITY_RE.fullmatch(sequence_id) is None
            or IDENTITY_RE.fullmatch(revision_id) is None
            or SHA256_RE.fullmatch(revision_sha256) is None
            or SHA256_RE.fullmatch(snapshot_sha256) is None
        ):
            raise PooledAssignmentError("MolBio NGS receipt immutable revision identity is incomplete")
        if revision_id in revision_ids:
            raise PooledAssignmentError("duplicate MolBio revision identities are forbidden")
        revision_ids.add(revision_id)
        snapshot = _confined_file(
            Path(str(receipt.reference_snapshot_path)), inputs_root, "MolBio NGS receipt FASTA", nonempty=True
        )
        if _sha256_file(snapshot) != snapshot_sha256:
            raise PooledAssignmentError(
                "MolBio NGS receipt FASTA digest mismatch", status_code=409, code="RECEIPT_DIGEST_MISMATCH"
            )
        sequence = _read_single_fasta(snapshot)
        if _sha256_bytes(sequence.encode("ascii")) != revision_sha256:
            raise PooledAssignmentError(
                "MolBio NGS receipt revision digest mismatch", status_code=409, code="RECEIPT_DIGEST_MISMATCH"
            )
        item = {
            "request": target,
            "receipt": receipt,
            "sequence_id": sequence_id,
            "revision_id": revision_id,
            "revision_sha256": revision_sha256,
            "receipt_snapshot_sha256": snapshot_sha256,
            "sequence": sequence,
        }
        validated.append(item)
        sequences.setdefault(sequence, []).append(item)

    for matching in sequences.values():
        if len(matching) < 2:
            continue
        groups = {item["request"].indistinguishable_group for item in matching}
        if None in groups or len(groups) != 1:
            names = ", ".join(item["request"].target_id for item in matching)
            raise PooledAssignmentError(
                f"identical sequence content requires one explicit common indistinguishable_group: {names}"
            )
    return validated


def _stage_reference_set(
    reference_set_id: str,
    validated: Sequence[dict[str, Any]],
) -> tuple[Path, dict[str, Any], str, str]:
    inputs_root = get_inputs_dir()
    parent = inputs_root / REFERENCE_SET_ROOT_NAME
    if inputs_root.is_symlink() or parent.is_symlink():
        raise PooledAssignmentError("reference-set staging root symlink is forbidden", status_code=409)
    inputs_root.mkdir(parents=True, exist_ok=True)
    parent.mkdir(parents=True, exist_ok=True)
    if parent.resolve() != inputs_root.resolve() / REFERENCE_SET_ROOT_NAME:
        raise PooledAssignmentError("reference-set staging root escaped the inputs root", status_code=409)
    destination = parent / reference_set_id
    if destination.exists() or destination.is_symlink():
        raise PooledAssignmentError("reference-set staging identity already exists", status_code=409)
    temporary = Path(tempfile.mkdtemp(prefix=f".{reference_set_id}.", dir=parent))
    try:
        refs = temporary / "refs"
        refs.mkdir()
        entries: list[dict[str, Any]] = []
        for item in validated:
            target = item["request"]
            relative = f"refs/{target.target_id}.fasta"
            fasta_bytes = f">{target.target_id}\n{item['sequence']}\n".encode("ascii")
            path = temporary / relative
            with path.open("xb") as handle:
                handle.write(fasta_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            entries.append(
                {
                    "target_id": target.target_id,
                    "label": target.label,
                    "molbio_sequence_id": item["sequence_id"],
                    "molbio_revision_id": item["revision_id"],
                    "revision_sha256": item["revision_sha256"],
                    "fasta_path": relative,
                    "fasta_sha256": _sha256_bytes(fasta_bytes),
                    "indistinguishable_group": target.indistinguishable_group,
                }
            )
        payload: dict[str, Any] = {
            "schema": REFERENCE_SET_SCHEMA,
            "mode": REFERENCE_SET_MODE,
            "manifest_id": reference_set_id,
            "entries": entries,
        }
        manifest_sha256 = _canonical_manifest_sha256(payload)
        payload["manifest_sha256"] = manifest_sha256
        manifest_bytes = canonical_json_bytes(payload)
        manifest_path = temporary / "reference_set.json"
        with manifest_path.open("xb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return (
            destination / "reference_set.json",
            payload,
            manifest_sha256,
            _sha256_bytes(manifest_bytes),
        )
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _remove_reference_set(manifest_path: Path | None) -> None:
    if manifest_path is None:
        return
    try:
        manifest_path.parent.resolve().relative_to(
            (get_inputs_dir().resolve() / REFERENCE_SET_ROOT_NAME)
        )
    except (OSError, ValueError):
        return
    shutil.rmtree(manifest_path.parent, ignore_errors=True)


def _remove_job_dirs(job_ids: Sequence[str]) -> None:
    try:
        root = get_results_dir().resolve()
    except OSError:
        return
    for job_id in job_ids:
        path = root / job_id
        try:
            if path.is_symlink() or path.resolve().parent != root:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)


async def _manifest_row_for_job(
    session: AsyncSession, assignment_job_id: str
) -> NgsReferenceSetManifest:
    row = (
        await session.execute(
            select(NgsReferenceSetManifest).where(
                NgsReferenceSetManifest.source_job_id == assignment_job_id,
                NgsReferenceSetManifest.mode == REFERENCE_SET_MODE,
                NgsReferenceSetManifest.target_workflow == ASSIGNMENT_WORKFLOW_ID,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise PooledAssignmentError(
            "pooled assignment reference-set manifest not found",
            status_code=404,
            code="POOLED_ASSIGNMENT_NOT_FOUND",
        )
    return row


async def _read_manifest(
    session: AsyncSession, row: NgsReferenceSetManifest
) -> tuple[dict[str, Any], list[NgsPooledReferenceTarget]]:
    manifest_path = _confined_file(
        Path(str(row.manifest_path)), get_inputs_dir(), "pooled reference-set manifest", nonempty=True
    )
    payload, raw = _load_json(manifest_path, "pooled reference-set manifest")
    if set(payload) != {"schema", "mode", "manifest_id", "manifest_sha256", "entries"}:
        raise PooledAssignmentError("pooled reference-set manifest keys are not exact", status_code=409)
    if (
        payload.get("schema") != REFERENCE_SET_SCHEMA
        or payload.get("mode") != REFERENCE_SET_MODE
        or payload.get("manifest_id") != row.id
        or payload.get("manifest_sha256") != row.manifest_sha256
        or _canonical_manifest_sha256(payload) != row.manifest_sha256
        or canonical_json_bytes(payload) != raw
        or payload != row.manifest_json
    ):
        raise PooledAssignmentError(
            "pooled reference-set manifest digest or identity mismatch",
            status_code=409,
            code="REFERENCE_SET_CORRUPT",
        )
    entries = payload.get("entries")
    if type(entries) is not list or not 2 <= len(entries) <= 96:
        raise PooledAssignmentError("pooled reference-set manifest entry count is invalid", status_code=409)
    targets = (
        await session.execute(
            select(NgsPooledReferenceTarget)
            .where(NgsPooledReferenceTarget.reference_set_id == row.id)
            .order_by(NgsPooledReferenceTarget.target_id)
        )
    ).scalars().all()
    if len(targets) != len(entries):
        raise PooledAssignmentError("pooled reference target rows are incomplete", status_code=409)
    entry_by_id = {
        entry.get("target_id"): entry for entry in entries if type(entry) is dict
    }
    if len(entry_by_id) != len(entries):
        raise PooledAssignmentError("pooled reference target identities are not unique", status_code=409)
    for target in targets:
        entry = entry_by_id.get(str(target.target_id))
        expected = {
            "target_id": str(target.target_id),
            "label": str(target.label),
            "molbio_sequence_id": str(target.sequence_id),
            "molbio_revision_id": str(target.revision_id),
            "revision_sha256": str(target.revision_sha256),
            "fasta_path": str(target.fasta_path),
            "fasta_sha256": str(target.fasta_sha256),
            "indistinguishable_group": target.indistinguishable_group,
        }
        if entry != expected:
            raise PooledAssignmentError("pooled reference target row disagrees with manifest", status_code=409)
        relative = _safe_relative_path(target.fasta_path, "pooled target FASTA path")
        fasta_path = _confined_file(
            manifest_path.parent.joinpath(*relative.parts),
            manifest_path.parent,
            "pooled target FASTA",
            nonempty=True,
        )
        if _sha256_file(fasta_path) != target.fasta_sha256:
            raise PooledAssignmentError("pooled target FASTA digest mismatch", status_code=409)
        sequence = _read_single_fasta(fasta_path)
        if _sha256_bytes(sequence.encode("ascii")) != target.revision_sha256:
            raise PooledAssignmentError("pooled target revision digest mismatch", status_code=409)
    return payload, list(targets)


def _submit_result(
    manifest_row: NgsReferenceSetManifest,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": REFERENCE_SET_SCHEMA,
        "assignment_job_id": str(manifest_row.source_job_id),
        "reference_set_id": str(manifest_row.id),
        "manifest_sha256": str(manifest_row.manifest_sha256),
        "manifest": payload,
        "scientific_status": "REVIEW",
        "release_state": "awaiting_operator_release",
    }


async def _find_submit_replay(
    session: AsyncSession, idempotency_key: str, fingerprint: str
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            select(NgsReferenceSetManifest).where(
                NgsReferenceSetManifest.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.request_fingerprint != fingerprint or row.mode != REFERENCE_SET_MODE:
        raise PooledAssignmentError(
            "idempotency_key is already bound to a different request",
            status_code=409,
            code="IDEMPOTENCY_CONFLICT",
        )
    job = await session.get(Job, row.source_job_id)
    if job is None:
        raise PooledAssignmentError("idempotent pooled assignment job is missing", status_code=409)
    payload, _ = await _read_manifest(session, row)
    return _submit_result(row, payload)


async def submit_pooled_reference_assignment(
    *,
    session: AsyncSession,
    request: PooledReferenceAssignmentRequest,
    background_tasks: Any,
    http_request: Any,
    response: Any,
) -> dict[str, Any]:
    """Stage one immutable pooled set and atomically launch its review-only assignment job."""

    fastq = _confined_file(
        Path(request.fastq_path).expanduser(), get_inputs_dir(), "pooled FASTQ", nonempty=True
    )
    fastq_sha256 = _sha256_file(fastq)
    fingerprint = _submit_fingerprint(request, fastq_sha256)
    existing = await _find_submit_replay(session, request.idempotency_key, fingerprint)
    if existing is not None:
        return existing

    staged_manifest_path: Path | None = None
    assignment_job_id: str | None = None
    committed = False
    try:
        await _begin_immediate(session)
        existing = await _find_submit_replay(session, request.idempotency_key, fingerprint)
        if existing is not None:
            await session.rollback()
            return existing
        fastq = _confined_file(fastq, get_inputs_dir(), "pooled FASTQ", nonempty=True)
        if _sha256_file(fastq) != fastq_sha256:
            raise PooledAssignmentError("pooled FASTQ changed during submission", status_code=409)
        validated = await _validate_receipts(session, request.targets)
        reference_set_id = str(uuid.uuid4())
        assignment_job_id = str(uuid.uuid5(ASSIGNMENT_JOB_NAMESPACE, reference_set_id))
        staged_manifest_path, payload, manifest_sha256, manifest_file_sha256 = _stage_reference_set(
            reference_set_id, validated
        )
        binding = {
            "reference_set_id": reference_set_id,
            "manifest_path": str(staged_manifest_path.resolve()),
            "manifest_sha256": manifest_sha256,
            "manifest_file_sha256": manifest_file_sha256,
            "mode": REFERENCE_SET_MODE,
            "binding_source": "server_staged_immutable_reference_set",
        }
        params = {
            "fastq_path": str(fastq),
            "reference_set_manifest": str(staged_manifest_path.resolve()),
            "pooled_assignment_min_mapq": request.min_mapq,
            "pooled_assignment_min_alignment_score_margin": request.min_alignment_score_margin,
            "fastq_sha256": fastq_sha256,
            "reference_set_manifest_sha256": manifest_sha256,
            "reference_set_manifest_file_sha256": manifest_file_sha256,
            "reference_set_binding": binding,
            "ngs_reference_set_binding": binding,
            "ont_workflow_id": ASSIGNMENT_WORKFLOW_ID,
            "scientific_status": "REVIEW",
            "release_state": "awaiting_operator_release",
            "lineage_root_job_id": assignment_job_id,
            "stage_family": "ont_ngs",
            "stage_mode": ASSIGNMENT_MODE,
        }
        job_data = JobCreate(
            name=request.name or "ONT pooled reference assignment",
            model_id="nanopore",
            mode=ASSIGNMENT_MODE,
            params=params,
            pinned_gpu=request.pinned_gpu,
        )
        token, token_digest = alignment_access.issue_alignment_access_token()
        trust_tokens = ont_submission_trust.begin_trusted_ont_job_creation(token_digest)
        try:
            from routers.jobs import create_job  # noqa: PLC0415

            created = await create_job(
                job_data,
                background_tasks,
                session,
                _preallocated_job_id=assignment_job_id,
                _commit=False,
            )
        finally:
            ont_submission_trust.end_trusted_ont_job_creation(trust_tokens)
        if str(getattr(created, "id", assignment_job_id)) != assignment_job_id:
            raise PooledAssignmentError("canonical assignment job identity changed", status_code=409)

        now = datetime.utcnow()
        manifest_row = NgsReferenceSetManifest(
            id=reference_set_id,
            manifest_schema=REFERENCE_SET_SCHEMA,
            mode=REFERENCE_SET_MODE,
            source_job_id=assignment_job_id,
            target_workflow=ASSIGNMENT_WORKFLOW_ID,
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            manifest_path=str(staged_manifest_path.resolve()),
            manifest_sha256=manifest_sha256,
            manifest_json=payload,
            created_at=now,
        )
        session.add(manifest_row)
        for receipt_index, (item, entry) in enumerate(
            zip(validated, payload["entries"], strict=True)
        ):
            receipt: MolBioNgsReceipt = item["receipt"]
            receipt.consumed_at = now
            # The legacy receipt table has a UNIQUE consumed_job_id constraint.
            # Every pooled receipt is consumed_at here; the immutable target row
            # carries the complete many-receipts-to-one-assignment binding.
            receipt.consumed_job_id = assignment_job_id if receipt_index == 0 else None
            session.add(
                NgsPooledReferenceTarget(
                    id=str(uuid.uuid5(TARGET_ROW_NAMESPACE, f"{reference_set_id}:{entry['target_id']}")),
                    reference_set_id=reference_set_id,
                    target_id=entry["target_id"],
                    label=entry["label"],
                    indistinguishable_group=entry["indistinguishable_group"],
                    sequence_id=entry["molbio_sequence_id"],
                    revision_id=entry["molbio_revision_id"],
                    revision_sha256=entry["revision_sha256"],
                    receipt_id=str(receipt.id),
                    fasta_path=entry["fasta_path"],
                    fasta_sha256=entry["fasta_sha256"],
                    created_at=now,
                )
            )
        await session.flush()
        await session.commit()
        committed = True
        alignment_access.set_alignment_access_cookie(assignment_job_id, token, response, http_request)
        return _submit_result(manifest_row, payload)
    except PooledAssignmentError:
        if not committed:
            await session.rollback()
            _remove_reference_set(staged_manifest_path)
            if assignment_job_id:
                _remove_job_dirs([assignment_job_id])
        raise
    except IntegrityError as exc:
        if not committed:
            await session.rollback()
            _remove_reference_set(staged_manifest_path)
            if assignment_job_id:
                _remove_job_dirs([assignment_job_id])
        raise PooledAssignmentError(
            "pooled assignment could not be committed atomically",
            status_code=409,
            code="POOLED_ASSIGNMENT_CONFLICT",
        ) from exc
    except Exception as exc:
        if not committed:
            await session.rollback()
            _remove_reference_set(staged_manifest_path)
            if assignment_job_id:
                _remove_job_dirs([assignment_job_id])
        raise PooledAssignmentError(
            "pooled assignment was rolled back and could not be committed",
            code="POOLED_ASSIGNMENT_ROLLED_BACK",
        ) from exc


async def get_pooled_assignment_manifest(
    session: AsyncSession, *, assignment_job_id: str
) -> dict[str, Any]:
    row = await _manifest_row_for_job(session, assignment_job_id)
    payload, _ = await _read_manifest(session, row)
    job = await session.get(Job, assignment_job_id)
    if job is None:
        raise PooledAssignmentError("pooled assignment job not found", status_code=404)
    return {
        "schema": REFERENCE_SET_SCHEMA,
        "mode": str(payload["mode"]),
        "assignment_job_id": assignment_job_id,
        "reference_set_id": str(row.id),
        "manifest_id": str(payload["manifest_id"]),
        "manifest_sha256": str(payload["manifest_sha256"]),
        "scientific_status": "REVIEW",
        "execution_status": str(job.status),
        "manifest": payload,
    }


async def get_pooled_assignment_targets(
    session: AsyncSession, *, assignment_job_id: str
) -> dict[str, Any]:
    row = await _manifest_row_for_job(session, assignment_job_id)
    _, targets = await _read_manifest(session, row)
    return {
        "schema": TARGET_LIST_SCHEMA,
        "assignment_job_id": assignment_job_id,
        "reference_set_id": str(row.id),
        "manifest_sha256": str(row.manifest_sha256),
        "targets": [
            {
                "target_id": str(target.target_id),
                "label": str(target.label),
                "indistinguishable_group": target.indistinguishable_group,
                "sequence_id": str(target.sequence_id),
                "revision_id": str(target.revision_id),
                "revision_sha256": str(target.revision_sha256),
                "receipt_id": str(target.receipt_id),
                "fasta_path": str(target.fasta_path),
                "fasta_sha256": str(target.fasta_sha256),
            }
            for target in targets
        ],
    }


def _require_exact_keys(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise PooledAssignmentError(f"{label} keys are not exact", status_code=409)
    return value


def _require_count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PooledAssignmentError(f"{label} must be a non-negative integer", status_code=409)
    return value


def _source_fastq_occurrences(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="ascii", newline="") as handle:
            ordinal = 0
            while True:
                header = handle.readline()
                if header == "":
                    break
                ordinal += 1
                sequence = handle.readline().rstrip("\r\n")
                plus = handle.readline().rstrip("\r\n")
                quality = handle.readline().rstrip("\r\n")
                header = header.rstrip("\r\n")
                if (
                    not header.startswith("@")
                    or len(header) == 1
                    or not sequence
                    or not plus.startswith("+")
                    or len(sequence) != len(quality)
                ):
                    raise PooledAssignmentError(
                        "assignment source FASTQ no longer satisfies strict input policy",
                        status_code=409,
                    )
                source_header = header[1:]
                source_read_id = source_header.split()[0]
                records.append(
                    {
                        "occurrence_id": f"occurrence_{ordinal}",
                        "source_read_id": source_read_id,
                        "source_header": source_header,
                        "input_ordinal": ordinal,
                    }
                )
    except PooledAssignmentError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PooledAssignmentError("assignment source FASTQ is unreadable", status_code=409) from exc
    return records


def _validate_occurrence_map(
    root: Path,
    summary: Mapping[str, Any],
    assignment_job: Job,
) -> list[dict[str, Any]]:
    path_value = summary.get("occurrence_map_path")
    relative = _safe_relative_path(path_value, "occurrence-map path")
    if relative.as_posix() != "occurrence_map.json":
        raise PooledAssignmentError("occurrence-map path is not exact", status_code=409)
    path = _confined_file(root.joinpath(*relative.parts), root, "occurrence map", nonempty=True)
    observed = _sha256_file(path)
    if observed != summary.get("occurrence_map_sha256"):
        raise PooledAssignmentError("occurrence-map digest mismatch", status_code=409)
    payload, _ = _load_json(path, "occurrence map")
    _require_exact_keys(
        payload,
        {"schema", "input_fastq_filename", "input_fastq_sha256", "count", "records"},
        "occurrence map",
    )
    params = dict(assignment_job.params or {})
    source_fastq = _confined_file(
        Path(str(params.get("fastq_path") or "")), get_inputs_dir(), "assignment source FASTQ", nonempty=True
    )
    if (
        payload.get("schema") != OCCURRENCE_MAP_SCHEMA
        or payload.get("input_fastq_filename") != source_fastq.name
        or payload.get("input_fastq_sha256") != params.get("fastq_sha256")
        or _sha256_file(source_fastq) != params.get("fastq_sha256")
    ):
        raise PooledAssignmentError("occurrence map source FASTQ binding is invalid", status_code=409)
    count = _require_count(payload.get("count"), "occurrence map count")
    records = payload.get("records")
    if type(records) is not list or len(records) != count:
        raise PooledAssignmentError("occurrence map count does not match records", status_code=409)
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    previous = 0
    for index, raw in enumerate(records, start=1):
        row = _require_exact_keys(
            raw,
            {"occurrence_id", "source_read_id", "source_header", "input_ordinal"},
            f"occurrence map record {index}",
        )
        occurrence_id = row.get("occurrence_id")
        source_read_id = row.get("source_read_id")
        source_header = row.get("source_header")
        ordinal = row.get("input_ordinal")
        match = OCCURRENCE_RE.fullmatch(occurrence_id) if type(occurrence_id) is str else None
        if (
            match is None
            or type(source_read_id) is not str
            or not source_read_id
            or type(source_header) is not str
            or not source_header
            or source_header.split()[0] != source_read_id
            or isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or int(match.group(1)) != ordinal
            or ordinal <= previous
            or occurrence_id in seen_ids
            or ordinal in seen_ordinals
        ):
            raise PooledAssignmentError("occurrence map record identity is invalid", status_code=409)
        seen_ids.add(occurrence_id)
        seen_ordinals.add(ordinal)
        previous = ordinal
        result.append(row)
    if result != _source_fastq_occurrences(source_fastq):
        raise PooledAssignmentError(
            "occurrence map does not close exactly to the assignment source FASTQ",
            status_code=409,
        )
    return result


def _validate_per_read_tsv(
    root: Path,
    summary_assignments: Sequence[dict[str, Any]],
) -> None:
    path = _confined_file(root / "per_read_assignment.tsv", root, "per-read assignment", nonempty=True)
    expected_header = [
        "occurrence_id",
        "source_read_id",
        "source_header",
        "input_ordinal",
        "disposition",
        "target_id",
        "best_alignment_score",
        "second_alignment_score",
        "alignment_score_delta",
        "best_mapq",
        "reason",
    ]
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if reader.fieldnames != expected_header:
                raise PooledAssignmentError("per-read assignment header is not exact", status_code=409)
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise PooledAssignmentError("per-read assignment is unreadable", status_code=409) from exc
    if len(rows) != len(summary_assignments):
        raise PooledAssignmentError("per-read assignment row count does not close", status_code=409)
    for tsv, summary in zip(rows, summary_assignments, strict=True):
        try:
            ordinal = int(tsv["input_ordinal"])
        except (TypeError, ValueError) as exc:
            raise PooledAssignmentError("per-read assignment ordinal is invalid", status_code=409) from exc
        expected = {
            "occurrence_id": summary["occurrence_id"],
            "source_read_id": summary["source_read_id"],
            "source_header": summary["source_header"],
            "input_ordinal": summary["input_ordinal"],
            "disposition": summary["disposition"],
            "target_id": summary["target_id"] or "",
            "reason": summary["reason"],
        }
        observed = {
            "occurrence_id": tsv["occurrence_id"],
            "source_read_id": tsv["source_read_id"],
            "source_header": tsv["source_header"],
            "input_ordinal": ordinal,
            "disposition": tsv["disposition"],
            "target_id": tsv["target_id"],
            "reason": tsv["reason"],
        }
        if observed != expected:
            raise PooledAssignmentError("per-read assignment row disagrees with summary", status_code=409)


def _fastq_occurrence_ids(path: Path) -> list[str]:
    ids: list[str] = []
    try:
        with path.open("r", encoding="ascii", newline="") as handle:
            while True:
                header = handle.readline()
                if header == "":
                    break
                sequence = handle.readline().rstrip("\r\n")
                plus = handle.readline().rstrip("\r\n")
                quality = handle.readline().rstrip("\r\n")
                header = header.rstrip("\r\n")
                if (
                    not header.startswith("@")
                    or len(header) == 1
                    or not sequence
                    or not plus.startswith("+")
                    or len(sequence) != len(quality)
                ):
                    raise PooledAssignmentError("assigned target FASTQ is malformed", status_code=409)
                occurrence_id = header[1:].split()[0]
                if OCCURRENCE_RE.fullmatch(occurrence_id) is None or header != f"@{occurrence_id}":
                    raise PooledAssignmentError("assigned target FASTQ QNAME is invalid", status_code=409)
                ids.append(occurrence_id)
    except PooledAssignmentError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PooledAssignmentError("assigned target FASTQ is unreadable", status_code=409) from exc
    if len(ids) != len(set(ids)):
        raise PooledAssignmentError("assigned target FASTQ has duplicate occurrences", status_code=409)
    return ids


async def _load_release_context(
    session: AsyncSession,
    assignment_job_id: str,
) -> dict[str, Any]:
    job = await session.get(Job, assignment_job_id)
    if job is None:
        raise PooledAssignmentError("pooled assignment job not found", status_code=404)
    if job.model_id != "nanopore" or job.mode != ASSIGNMENT_MODE:
        raise PooledAssignmentError("job is not a pooled ONT assignment", status_code=422)
    if job.status != JobStatus.COMPLETED.value:
        raise PooledAssignmentError("pooled assignment job is not completed", status_code=409)
    manifest_row = await _manifest_row_for_job(session, assignment_job_id)
    manifest, target_rows = await _read_manifest(session, manifest_row)
    params = dict(job.params or {})
    if (
        params.get("reference_set_manifest_sha256") != manifest_row.manifest_sha256
        or params.get("scientific_status") != "REVIEW"
        or params.get("release_state") != "awaiting_operator_release"
    ):
        raise PooledAssignmentError("assignment job launch binding is invalid", status_code=409)
    output_root = _confined_directory(Path(str(job.output_dir or "")), get_results_dir(), "assignment output")
    evidence_root = _confined_directory(
        output_root / "pooled_reference_assignment", output_root, "pooled assignment evidence"
    )
    summary_path = _confined_file(
        evidence_root / "assignment_summary.json",
        evidence_root,
        "assignment_summary.json",
        nonempty=True,
    )
    summary, raw = _load_json(summary_path, "assignment_summary.json")
    _require_exact_keys(
        summary,
        {
            "schema",
            "workflow_id",
            "mode",
            "manifest_id",
            "manifest_sha256",
            "scientific_status",
            "release_state",
            "policy",
            "counts",
            "disposition_counts",
            "accounting",
            "occurrence_map_path",
            "occurrence_map_sha256",
            "occurrence_map_count",
            "read_assignments",
            "targets",
            "artifacts",
        },
        "assignment summary",
    )
    if (
        summary.get("schema") != SUMMARY_SCHEMA
        or summary.get("workflow_id") != ASSIGNMENT_WORKFLOW_ID
        or summary.get("mode") != REFERENCE_SET_MODE
        or summary.get("manifest_id") != manifest_row.id
        or summary.get("manifest_sha256") != manifest_row.manifest_sha256
        or summary.get("scientific_status") != "REVIEW"
        or summary.get("release_state") != "awaiting_operator_release"
    ):
        raise PooledAssignmentError("assignment summary identity/status binding is invalid", status_code=409)
    policy = _require_exact_keys(
        summary.get("policy"),
        {
            "fastq_input_policy",
            "min_mapq",
            "min_alignment_score_margin",
            "minimap2_preset",
            "secondary_alignments",
            "identical_targets",
            "occurrence_id_policy",
        },
        "assignment policy",
    )
    if policy != {
        "fastq_input_policy": "strict",
        "min_mapq": params.get("pooled_assignment_min_mapq"),
        "min_alignment_score_margin": params.get("pooled_assignment_min_alignment_score_margin"),
        "minimap2_preset": "map-ont",
        "secondary_alignments": "retained",
        "identical_targets": "ambiguous_at_individual_target_level",
        "occurrence_id_policy": "synthetic_occurrence_id_from_one_based_input_ordinal",
    }:
        raise PooledAssignmentError("assignment summary policy differs from launch policy", status_code=409)

    counts = _require_exact_keys(
        summary.get("counts"),
        {
            "input_fastq_records",
            "valid_fastq_reads",
            "occurrence_map_count",
            "rejected_by_input_policy",
            "target_assigned_reads",
            "ambiguous_reads",
            "unclassified_reads",
        },
        "assignment counts",
    )
    parsed_counts = {key: _require_count(value, f"assignment count {key}") for key, value in counts.items()}
    accounting = _require_exact_keys(
        summary.get("accounting"),
        {
            "valid_fastq_reads",
            "occurrence_map_count",
            "sum_of_dispositions",
            "input_fastq_records",
            "valid_plus_rejected",
            "occurrence_map_matches_valid_fastq_reads",
            "closure",
        },
        "assignment accounting",
    )
    for key in (
        "valid_fastq_reads",
        "occurrence_map_count",
        "sum_of_dispositions",
        "input_fastq_records",
        "valid_plus_rejected",
    ):
        _require_count(accounting.get(key), f"assignment accounting {key}")
    if accounting.get("occurrence_map_matches_valid_fastq_reads") is not True or accounting.get("closure") is not True:
        raise PooledAssignmentError("assignment accounting closure is not true", status_code=409)

    occurrence_rows = _validate_occurrence_map(evidence_root, summary, job)
    valid = parsed_counts["valid_fastq_reads"]
    assigned = parsed_counts["target_assigned_reads"]
    ambiguous = parsed_counts["ambiguous_reads"]
    unclassified = parsed_counts["unclassified_reads"]
    rejected = parsed_counts["rejected_by_input_policy"]
    input_count = parsed_counts["input_fastq_records"]
    occurrence_count = len(occurrence_rows)
    if (
        occurrence_count != valid
        or parsed_counts["occurrence_map_count"] != valid
        or summary.get("occurrence_map_count") != valid
        or valid != assigned + ambiguous + unclassified
        or input_count != valid + rejected
        or accounting.get("valid_fastq_reads") != valid
        or accounting.get("occurrence_map_count") != valid
        or accounting.get("sum_of_dispositions") != assigned + ambiguous + unclassified
        or accounting.get("input_fastq_records") != input_count
        or accounting.get("valid_plus_rejected") != valid + rejected
    ):
        raise PooledAssignmentError("assignment counts do not close arithmetically", status_code=409)

    manifest_target_ids = {str(target.target_id) for target in target_rows}
    dispositions = summary.get("disposition_counts")
    if type(dispositions) is not dict:
        raise PooledAssignmentError("disposition_counts must be an object", status_code=409)
    allowed_dispositions = {"ambiguous", "unclassified"} | {
        f"target:{target_id}" for target_id in manifest_target_ids
    }
    if not set(dispositions) <= allowed_dispositions:
        raise PooledAssignmentError("disposition_counts names an unknown target", status_code=409)
    parsed_dispositions = {
        key: _require_count(value, f"disposition count {key}") for key, value in dispositions.items()
    }
    if (
        sum(parsed_dispositions.values()) != valid
        or sum(value for key, value in parsed_dispositions.items() if key.startswith("target:")) != assigned
        or parsed_dispositions.get("ambiguous", 0) != ambiguous
        or parsed_dispositions.get("unclassified", 0) != unclassified
    ):
        raise PooledAssignmentError("disposition counts do not close arithmetically", status_code=409)

    raw_assignments = summary.get("read_assignments")
    if type(raw_assignments) is not list or len(raw_assignments) != valid:
        raise PooledAssignmentError("read assignment count does not close", status_code=409)
    assignments: list[dict[str, Any]] = []
    occurrence_by_id = {row["occurrence_id"]: row for row in occurrence_rows}
    seen_assignments: set[str] = set()
    counted_dispositions: dict[str, int] = {}
    for index, raw_assignment in enumerate(raw_assignments, start=1):
        row = _require_exact_keys(
            raw_assignment,
            {
                "occurrence_id",
                "source_read_id",
                "source_header",
                "input_ordinal",
                "disposition",
                "target_id",
                "reason",
            },
            f"read assignment {index}",
        )
        occurrence_id = row.get("occurrence_id")
        source = occurrence_by_id.get(occurrence_id)
        disposition = row.get("disposition")
        target_id = row.get("target_id")
        if (
            source is None
            or occurrence_id in seen_assignments
            or row.get("source_read_id") != source["source_read_id"]
            or row.get("source_header") != source["source_header"]
            or row.get("input_ordinal") != source["input_ordinal"]
            or type(disposition) is not str
            or disposition not in allowed_dispositions
            or (disposition.startswith("target:") and target_id != disposition.split(":", 1)[1])
            or (not disposition.startswith("target:") and target_id is not None)
            or type(row.get("reason")) is not str
            or not row.get("reason")
        ):
            raise PooledAssignmentError("read assignment does not close to occurrence map", status_code=409)
        seen_assignments.add(occurrence_id)
        counted_dispositions[disposition] = counted_dispositions.get(disposition, 0) + 1
        assignments.append(row)
    if counted_dispositions != parsed_dispositions:
        raise PooledAssignmentError("per-read dispositions disagree with disposition_counts", status_code=409)
    _validate_per_read_tsv(evidence_root, assignments)

    raw_targets = summary.get("targets")
    if type(raw_targets) is not list or len(raw_targets) != len(target_rows):
        raise PooledAssignmentError("assignment target summaries are incomplete", status_code=409)
    db_by_id = {str(target.target_id): target for target in target_rows}
    target_evidence: dict[str, dict[str, Any]] = {}
    for index, raw_target in enumerate(raw_targets, start=1):
        target = _require_exact_keys(
            raw_target,
            {
                "target_id",
                "label",
                "molbio_sequence_id",
                "molbio_revision_id",
                "revision_sha256",
                "indistinguishable_group",
                "read_count",
                "read_ids_path",
                "fastq_path",
            },
            f"assignment target {index}",
        )
        target_id = target.get("target_id")
        db_target = db_by_id.get(target_id)
        read_count = _require_count(target.get("read_count"), f"target {target_id} read_count")
        if (
            db_target is None
            or target_id in target_evidence
            or target.get("label") != db_target.label
            or target.get("molbio_sequence_id") != db_target.sequence_id
            or target.get("molbio_revision_id") != db_target.revision_id
            or target.get("revision_sha256") != db_target.revision_sha256
            or target.get("indistinguishable_group") != db_target.indistinguishable_group
            or read_count != parsed_dispositions.get(f"target:{target_id}", 0)
        ):
            raise PooledAssignmentError("assignment target summary binding is invalid", status_code=409)
        expected_fastq = f"target_{target_id}.fastq"
        expected_ids = f"target_{target_id}.read_ids.txt"
        if target.get("fastq_path") != expected_fastq or target.get("read_ids_path") != expected_ids:
            raise PooledAssignmentError("assignment target artifact path is not exact", status_code=409)
        fastq_path = _confined_file(evidence_root / expected_fastq, evidence_root, "assigned target FASTQ")
        ids_path = _confined_file(evidence_root / expected_ids, evidence_root, "assigned target read IDs")
        fastq_ids = _fastq_occurrence_ids(fastq_path)
        try:
            id_rows = [value for value in ids_path.read_text(encoding="utf-8").splitlines() if value]
        except (OSError, UnicodeError) as exc:
            raise PooledAssignmentError("assigned target read IDs are unreadable", status_code=409) from exc
        assigned_ids = [
            row["occurrence_id"] for row in assignments if row["target_id"] == target_id
        ]
        if fastq_ids != assigned_ids or id_rows != assigned_ids or len(assigned_ids) != read_count:
            raise PooledAssignmentError("assigned target FASTQ/read IDs do not close", status_code=409)
        target_evidence[target_id] = {
            "target": db_target,
            "summary": target,
            "fastq_path": fastq_path,
            "fastq_sha256": _sha256_file(fastq_path),
            "read_count": read_count,
        }
    if set(target_evidence) != manifest_target_ids:
        raise PooledAssignmentError("assignment target set is incomplete", status_code=409)

    artifacts = _require_exact_keys(
        summary.get("artifacts"),
        {
            "per_read_assignment",
            "fastq_preflight",
            "occurrence_map",
            "combined_reference",
            "combined_reference_index",
            "alignment_bam",
            "alignment_bai",
            "alignment_log",
            "ambiguous_read_ids",
            "ambiguous_fastq",
            "unclassified_read_ids",
            "unclassified_fastq",
            "igv_session",
        },
        "assignment artifacts",
    )
    expected_artifacts = {
        "per_read_assignment": "per_read_assignment.tsv",
        "fastq_preflight": "fastq_preflight.json",
        "occurrence_map": "occurrence_map.json",
        "combined_reference": "combined_intended_reference.fasta",
        "combined_reference_index": "combined_intended_reference.fasta.fai",
        "alignment_bam": "pooled_assignment.bam",
        "alignment_bai": "pooled_assignment.bam.bai",
        "alignment_log": "pooled_reference_assignment.minimap2.log",
        "ambiguous_read_ids": "ambiguous.read_ids.txt",
        "ambiguous_fastq": "ambiguous.fastq",
        "unclassified_read_ids": "unclassified.read_ids.txt",
        "unclassified_fastq": "unclassified.fastq",
        "igv_session": "intended_pool.igv_session.json",
    }
    if artifacts != expected_artifacts:
        raise PooledAssignmentError("assignment artifact inventory is not exact", status_code=409)
    return {
        "job": job,
        "manifest_row": manifest_row,
        "manifest": manifest,
        "target_rows": target_rows,
        "summary": summary,
        "summary_path": summary_path,
        "summary_sha256": _sha256_bytes(raw),
        "target_evidence": target_evidence,
    }


def _release_result(
    row: NgsPooledAssignmentRelease,
    targets: Sequence[NgsPooledAssignmentReleaseTarget],
) -> dict[str, Any]:
    return {
        "schema": RELEASE_SCHEMA,
        "release_id": str(row.id),
        "assignment_job_id": str(row.assignment_job_id),
        "reference_set_id": str(row.reference_set_id),
        "target_workflow": str(row.target_workflow),
        "assignment_summary_sha256": str(row.assignment_summary_sha256),
        "target_ids": [str(target.target_id) for target in targets],
        "child_job_ids": [str(target.child_job_id) for target in targets],
    }


async def _find_release_replay(
    session: AsyncSession,
    idempotency_key: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            select(NgsPooledAssignmentRelease).where(
                NgsPooledAssignmentRelease.idempotency_key == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.request_fingerprint != fingerprint:
        raise PooledAssignmentError(
            "release idempotency_key is already bound to a different request",
            status_code=409,
            code="IDEMPOTENCY_CONFLICT",
        )
    targets = (
        await session.execute(
            select(NgsPooledAssignmentReleaseTarget)
            .where(NgsPooledAssignmentReleaseTarget.release_id == row.id)
            .order_by(NgsPooledAssignmentReleaseTarget.target_id)
        )
    ).scalars().all()
    child_ids = [str(target.child_job_id) for target in targets]
    children = (
        await session.execute(select(Job.id).where(Job.id.in_(child_ids)))
    ).scalars().all()
    if not targets or set(map(str, children)) != set(child_ids):
        raise PooledAssignmentError("idempotent release rows or child jobs are incomplete", status_code=409)
    return _release_result(row, list(targets))


def _safe_child_name(prefix: str, target_id: str) -> str:
    name = f"{prefix} {target_id}"
    if len(name) > 128:
        name = f"{prefix[: 127 - len(target_id)].rstrip()} {target_id}"
    if SAFE_NAME_RE.fullmatch(name) is None:
        raise PooledAssignmentError("release child name is unsafe")
    return name


async def _create_release_child(
    *,
    session: AsyncSession,
    background_tasks: Any,
    assignment: Job,
    manifest_row: NgsReferenceSetManifest,
    release_id: str,
    child_id: str,
    evidence: Mapping[str, Any],
    request: PooledAssignmentReleaseRequest,
) -> tuple[str, str]:
    target: NgsPooledReferenceTarget = evidence["target"]
    manifest_path = Path(str(manifest_row.manifest_path)).resolve()
    reference_path = _confined_file(
        manifest_path.parent / str(target.fasta_path),
        manifest_path.parent,
        "released target reference FASTA",
        nonempty=True,
    )
    revision_binding = {
        "sequence_id": str(target.sequence_id),
        "revision_id": str(target.revision_id),
        "revision_sha256": str(target.revision_sha256),
        "reference_snapshot_sha256": str(target.fasta_sha256),
        "receipt_id": str(target.receipt_id),
        "binding_source": "pooled_reference_set_receipt",
    }
    reference_binding = {
        "reference_set_id": str(manifest_row.id),
        "manifest_sha256": str(manifest_row.manifest_sha256),
        "manifest_path": str(manifest_path),
        "binding_source": "server_staged_immutable_reference_set",
    }
    target_binding = {
        "release_id": release_id,
        "assignment_job_id": str(assignment.id),
        "reference_set_id": str(manifest_row.id),
        "target_id": str(target.target_id),
        "revision_id": str(target.revision_id),
        "revision_sha256": str(target.revision_sha256),
        "assigned_fastq_sha256": str(evidence["fastq_sha256"]),
        "assigned_read_count": int(evidence["read_count"]),
        "binding_source": "reviewed_pooled_assignment_summary",
    }
    release_binding = {
        "release_id": release_id,
        "assignment_job_id": str(assignment.id),
        "target_workflow": request.target_workflow,
        "binding_source": "operator_pooled_assignment_release",
    }
    params = {
        "fastq_path": str(evidence["fastq_path"]),
        "reference_fasta": str(reference_path),
        "run_fastq_qc": True,
        "fastq_minimap2_preset": "map-ont",
        "fastq_minimap2_allow_secondary": False,
        "molbio_revision_binding": revision_binding,
        "reference_set_binding": reference_binding,
        "ngs_reference_set_binding": reference_binding,
        "pooled_assignment_target_binding": target_binding,
        "pooled_assignment_release_binding": release_binding,
        "lineage_root_job_id": str(assignment.lineage_root_job_id or assignment.id),
        "source_stage_job_id": str(assignment.id),
        "source_stage_family": "ont_ngs",
        "source_stage_mode": ASSIGNMENT_MODE,
        "selection_source_type": ASSIGNMENT_WORKFLOW_ID,
        "selection_source_job_id": str(assignment.id),
        "source_selection_count": len(request.target_ids),
    }
    from routers.ont_runs import OntNgsSubmitRequest, _job_create_for_ont_submit  # noqa: PLC0415

    submit = OntNgsSubmitRequest(
        name=_safe_child_name(
            request.name_prefix or str(assignment.name or "Pooled assignment release"),
            str(target.target_id),
        ),
        params=params,
        pinned_gpu=request.pinned_gpu,
    )
    trusted = frozenset(params)
    job_data = _job_create_for_ont_submit(
        request.target_workflow,
        submit,
        trusted_server_params=trusted,
        trusted_result_paths=frozenset({"fastq_path"}),
    ).model_copy(
        update={
            "parent_job_id": str(assignment.id),
            "child_stage": f"pooled_assignment_release_{release_id[:8]}",
            "batch_id": release_id,
            "batch_name": f"{assignment.name} pooled release",
        }
    )
    token, token_digest = alignment_access.issue_alignment_access_token()
    trust_tokens = ont_submission_trust.begin_trusted_ont_job_creation(token_digest)
    try:
        from routers.jobs import create_job  # noqa: PLC0415

        created = await create_job(
            job_data,
            background_tasks,
            session,
            _preallocated_job_id=child_id,
            _commit=False,
        )
    finally:
        ont_submission_trust.end_trusted_ont_job_creation(trust_tokens)
    if str(getattr(created, "id", child_id)) != child_id:
        raise PooledAssignmentError("canonical release child identity changed", status_code=409)
    return child_id, token


async def release_pooled_assignment(
    *,
    session: AsyncSession,
    assignment_job_id: str,
    request: PooledAssignmentReleaseRequest,
    background_tasks: Any,
    http_request: Any,
    response: Any,
) -> dict[str, Any]:
    """Atomically release selected, nonempty pooled targets into canonical consensus-QC jobs."""

    context = await _load_release_context(session, assignment_job_id)
    summary_sha256 = context["summary_sha256"]
    fingerprint = _release_fingerprint(assignment_job_id, request, summary_sha256)
    existing = await _find_release_replay(session, request.idempotency_key, fingerprint)
    if existing is not None:
        return existing
    selected_ids = sorted(request.target_ids)
    if not set(selected_ids) <= set(context["target_evidence"]):
        raise PooledAssignmentError("release names an unknown pooled target")
    for target_id in selected_ids:
        evidence = context["target_evidence"][target_id]
        if evidence["read_count"] <= 0 or not evidence["fastq_path"].is_file() or evidence["fastq_path"].stat().st_size == 0:
            raise PooledAssignmentError("selected target must have a non-empty assigned FASTQ")

    child_ids: list[str] = []
    committed = False
    try:
        await _begin_immediate(session)
        context = await _load_release_context(session, assignment_job_id)
        if context["summary_sha256"] != summary_sha256:
            raise PooledAssignmentError("assignment summary changed during release", status_code=409)
        existing = await _find_release_replay(session, request.idempotency_key, fingerprint)
        if existing is not None:
            await session.rollback()
            return existing
        release_id = str(uuid.uuid4())
        child_ids = [
            str(uuid.uuid5(RELEASE_CHILD_NAMESPACE, f"{release_id}:{target_id}"))
            for target_id in selected_ids
        ]
        release_row = NgsPooledAssignmentRelease(
            id=release_id,
            assignment_job_id=assignment_job_id,
            reference_set_id=str(context["manifest_row"].id),
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            target_workflow=request.target_workflow,
            assignment_summary_path=str(context["summary_path"]),
            assignment_summary_sha256=summary_sha256,
            created_at=datetime.utcnow(),
        )
        session.add(release_row)
        tokens: list[tuple[str, str]] = []
        now = datetime.utcnow()
        rows: list[NgsPooledAssignmentReleaseTarget] = []
        for target_id, child_id in zip(selected_ids, child_ids, strict=True):
            evidence = context["target_evidence"][target_id]
            target: NgsPooledReferenceTarget = evidence["target"]
            created_id, token = await _create_release_child(
                session=session,
                background_tasks=background_tasks,
                assignment=context["job"],
                manifest_row=context["manifest_row"],
                release_id=release_id,
                child_id=child_id,
                evidence=evidence,
                request=request,
            )
            tokens.append((created_id, token))
            manifest_path = Path(str(context["manifest_row"].manifest_path)).resolve()
            row = NgsPooledAssignmentReleaseTarget(
                id=str(uuid.uuid5(RELEASE_TARGET_NAMESPACE, f"{release_id}:{target_id}")),
                release_id=release_id,
                assignment_job_id=assignment_job_id,
                reference_set_id=str(context["manifest_row"].id),
                target_id=target_id,
                child_job_id=child_id,
                sequence_id=str(target.sequence_id),
                revision_id=str(target.revision_id),
                revision_sha256=str(target.revision_sha256),
                receipt_id=str(target.receipt_id),
                fasta_path=str((manifest_path.parent / str(target.fasta_path)).resolve()),
                fasta_sha256=str(target.fasta_sha256),
                assigned_fastq_path=str(evidence["fastq_path"]),
                assigned_fastq_sha256=str(evidence["fastq_sha256"]),
                assigned_read_count=int(evidence["read_count"]),
                created_at=now,
            )
            session.add(row)
            rows.append(row)
        await session.flush()
        await session.commit()
        committed = True
        for child_id, token in tokens:
            alignment_access.set_alignment_access_cookie(child_id, token, response, http_request)
        return _release_result(release_row, rows)
    except PooledAssignmentError:
        if not committed:
            await session.rollback()
            _remove_job_dirs(child_ids)
        raise
    except IntegrityError as exc:
        if not committed:
            await session.rollback()
            _remove_job_dirs(child_ids)
        raise PooledAssignmentError(
            "pooled assignment release could not be committed atomically",
            status_code=409,
            code="POOLED_RELEASE_CONFLICT",
        ) from exc
    except Exception as exc:
        if not committed:
            await session.rollback()
            _remove_job_dirs(child_ids)
        raise PooledAssignmentError(
            "pooled assignment release was rolled back and could not be committed",
            code="POOLED_RELEASE_ROLLED_BACK",
        ) from exc


__all__ = [
    "ASSIGNMENT_MODE",
    "ASSIGNMENT_WORKFLOW_ID",
    "PooledAssignmentError",
    "PooledAssignmentReleaseRequest",
    "PooledReferenceAssignmentRequest",
    "PooledReferenceTargetRequest",
    "REFERENCE_SET_ROOT_NAME",
    "get_pooled_assignment_manifest",
    "get_pooled_assignment_targets",
    "release_pooled_assignment",
    "submit_pooled_reference_assignment",
]
