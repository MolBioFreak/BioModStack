"""Atomic server-owned orchestration for barcoded ONT reference-set runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    Job,
    MolBioNgsReceipt,
    NgsReferenceSetManifest,
    NgsReferenceSetMapping,
)
from paths import get_inputs_dir
from services import alignment_access, ont_submission_trust
from services.molbio_ngs_receipts import consume_molbio_ngs_receipt
from services.ont_barcode_units import load_barcode_units
from services.ont_ngs_contract import normalized_fasta_sequence_sha256


REFERENCE_SET_SCHEMA = "bms.ngs.reference-set.v1"
REFERENCE_SET_LIST_SCHEMA = "bms.ngs.reference-set-list.v1"
REFERENCE_SET_MODE = "barcoded"
REFERENCE_SET_ROOT_NAME = "ngs_reference_sets"
CANONICAL_BARCODE_RE = re.compile(r"^barcode(?:0[1-9]|[1-8][0-9]|9[0-6])$")
UNIT_RE = re.compile(r"^(?:barcode(?:0[1-9]|[1-8][0-9]|9[0-6])|unclassified)$")
SAFE_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CHILD_ID_NAMESPACE = uuid.UUID("f7fbb8bc-0c1f-4ce4-bae1-4da2bbd0c8fb")
MAPPING_ID_NAMESPACE = uuid.UUID("d69c116b-fab9-48dc-bf5b-8e1c7c7db1bc")


class BarcodeBatchError(ValueError):
    """A fail-closed barcode-batch validation or persistence error."""

    def __init__(self, message: str, *, status_code: int = 422, code: str = "BARCODE_BATCH_INVALID") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class BarcodeBatchRequestMapping(BaseModel):
    """One browser mapping from a retained source barcode to one receipt."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    unit_id: str = Field(min_length=1, max_length=32)
    sample_alias: str | None = Field(default=None, max_length=128)
    molbio_ngs_receipt_id: str = Field(min_length=1, max_length=36)

    @field_validator("unit_id", "molbio_ngs_receipt_id")
    @classmethod
    def reject_outer_whitespace(cls, value: str) -> str:
        if value != value.strip() or not value:
            raise ValueError("mapping identifiers must not contain outer whitespace")
        return value

    @field_validator("sample_alias")
    @classmethod
    def validate_sample_alias(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip() or not SAFE_TEXT_RE.fullmatch(value) or ".." in value:
            raise ValueError("sample_alias contains unsafe characters")
        return value


class BarcodeBatchRequest(BaseModel):
    """Strict body for one atomic barcoded reference-set launch."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    idempotency_key: str = Field(min_length=1, max_length=255)
    target_workflow: str = Field(pattern="^(ont_plasmid_qc|ont_construct_screening)$")
    name_prefix: str | None = Field(default=None, max_length=128)
    pinned_gpu: int | None = Field(default=None, ge=0, le=15)
    mappings: list[BarcodeBatchRequestMapping] = Field(min_length=1, max_length=96)

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, value: str) -> str:
        if value != value.strip() or not IDEMPOTENCY_RE.fullmatch(value):
            raise ValueError("idempotency_key contains unsafe characters")
        return value

    @field_validator("name_prefix")
    @classmethod
    def validate_name_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value != value.strip() or not SAFE_TEXT_RE.fullmatch(value) or ".." in value:
            raise ValueError("name_prefix contains unsafe characters")
        return value


def canonical_json_bytes(value: Any) -> bytes:
    """Render the repository's deterministic JSON profile for identity hashes."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BarcodeBatchError("reference-set manifest is not canonical JSON") from exc


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _confined_file(path: Path, root: Path, label: str) -> Path:
    """Resolve one regular file while rejecting symlinks and root escapes."""

    if root.is_symlink():
        raise BarcodeBatchError(f"{label} root symlink is forbidden", status_code=409)
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise BarcodeBatchError(f"{label} root is unavailable", status_code=409) from exc
    if not root_resolved.is_dir():
        raise BarcodeBatchError(f"{label} root must be a directory", status_code=409)
    if path.is_symlink():
        raise BarcodeBatchError(f"{label} symlink is forbidden", status_code=409)
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise BarcodeBatchError(f"{label} must be confined beneath its server-owned root", status_code=409) from exc
    cursor = root_resolved
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BarcodeBatchError(f"{label} symlink is forbidden", status_code=409)
    if not resolved.is_file():
        raise BarcodeBatchError(f"{label} must be a regular file", status_code=409)
    return resolved


def _read_json_file(path: Path, root: Path, label: str) -> tuple[Path, dict[str, Any], bytes]:
    resolved = _confined_file(path, root, label)
    try:
        payload_bytes = resolved.read_bytes()
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BarcodeBatchError(f"{label} is unreadable or malformed", status_code=409) from exc
    if not isinstance(payload, dict):
        raise BarcodeBatchError(f"{label} must contain a JSON object", status_code=409)
    return resolved, payload, payload_bytes


def _normalized_request(request: BarcodeBatchRequest, *, source_job_id: str) -> dict[str, Any]:
    mappings = [
        {
            "unit_id": item.unit_id,
            "sample_alias": item.sample_alias,
            "molbio_ngs_receipt_id": item.molbio_ngs_receipt_id,
        }
        for item in request.mappings
    ]
    mappings.sort(key=lambda item: (item["unit_id"], item["molbio_ngs_receipt_id"], item["sample_alias"] or ""))
    return {
        "source_job_id": source_job_id,
        "idempotency_key": request.idempotency_key,
        "target_workflow": request.target_workflow,
        "name_prefix": request.name_prefix,
        "pinned_gpu": request.pinned_gpu,
        "mappings": mappings,
    }


def request_fingerprint(request: BarcodeBatchRequest, *, source_job_id: str) -> str:
    return canonical_json_sha256(_normalized_request(request, source_job_id=source_job_id))


def _source_snapshot(source_job: Job, source_root: Path) -> dict[str, Any]:
    provenance = dict(source_job.provenance or {})
    terminal = provenance.get("ont_dorado_terminal_products")
    return {
        "id": str(source_job.id),
        "name": str(source_job.name or ""),
        "mode": str(source_job.mode or ""),
        "model_id": str(source_job.model_id or ""),
        "lineage_root_job_id": str(source_job.lineage_root_job_id or source_job.id),
        "stage_family": str(source_job.stage_family or "ont_ngs"),
        "stage_mode": str(source_job.stage_mode or source_job.mode or "basecall_dna"),
        "params": dict(source_job.params or {}),
        "provenance": provenance,
        "root": Path(source_root).expanduser(),
        "terminal": terminal,
    }


def _terminal_product_digests(source: Mapping[str, Any]) -> dict[str, str]:
    terminal = source.get("terminal")
    products = terminal.get("products") if isinstance(terminal, dict) else None
    required = (
        "demux_manifest",
        "barcode_units_manifest",
        "dorado_preflight",
        "dorado_runtime_provenance",
    )
    if not isinstance(terminal, dict) or terminal.get("schema") != "biomodstack.ont_dorado_terminal_products.v1":
        raise BarcodeBatchError("source job terminal Dorado product anchor is missing", status_code=409)
    if not isinstance(products, dict):
        raise BarcodeBatchError("source job terminal Dorado product anchor is malformed", status_code=409)
    result = {key: str(products.get(key, {}).get("sha256") or "") for key in required if isinstance(products.get(key), dict)}
    if set(result) != set(required) or any(not _is_sha256(value) for value in result.values()):
        raise BarcodeBatchError("source job terminal Dorado product digests are incomplete", status_code=409)
    return result


def _validate_source_products(source: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(source["root"])
    digests = _terminal_product_digests(source)
    paths = {
        "demux_manifest": root / "demux" / "demux_manifest.json",
        "barcode_units_manifest": root / "demux" / "per_barcode_units.json",
        "dorado_preflight": root / "basecall" / "dorado_preflight.json",
        "dorado_runtime_provenance": root / "basecall" / "dorado_runtime_provenance.json",
    }
    documents: dict[str, dict[str, Any]] = {}
    for label, path in paths.items():
        resolved, payload, payload_bytes = _read_json_file(path, root, label)
        observed = _sha256_bytes(payload_bytes)
        if observed != digests[label]:
            raise BarcodeBatchError(f"source {label} digest mismatch", status_code=409, code="SOURCE_DIGEST_MISMATCH")
        documents[label] = payload
        documents[f"{label}_path"] = resolved  # type: ignore[assignment]

    preflight = documents["dorado_preflight"]
    runtime = documents["dorado_runtime_provenance"]
    demux = documents["demux_manifest"]
    catalog = documents["barcode_units_manifest"]
    if (
        preflight.get("schema") != "biomodstack.dorado_preflight.v1"
        or runtime.get("schema") != "biomodstack.dorado_runtime_provenance.v1"
        or demux.get("schema") != "biomodstack.dorado_demux.v1"
        or catalog.get("schema") != "biomodstack.dorado_barcode_units.v1"
    ):
        raise BarcodeBatchError("source Dorado product schema is unsupported", status_code=409)
    runtime_calls = runtime.get("calls_bam")
    source_calls = demux.get("source_calls")
    if not isinstance(runtime_calls, dict) or not isinstance(source_calls, dict):
        raise BarcodeBatchError("source calls provenance is missing", status_code=409)
    source_calls_sha256 = str(source_calls.get("sha256") or "").lower()
    runtime_calls_sha256 = str(runtime_calls.get("sha256") or "").lower()
    preflight_sha256 = digests["dorado_preflight"]
    if (
        not _is_sha256(source_calls_sha256)
        or source_calls_sha256 != runtime_calls_sha256
        or source_calls.get("read_count") != runtime_calls.get("read_count")
        or demux.get("preflight_sha256") != preflight_sha256
        or runtime.get("preflight_sha256") != preflight_sha256
    ):
        raise BarcodeBatchError("source calls/preflight identities are inconsistent", status_code=409)

    try:
        demux_units = load_barcode_units(
            paths["demux_manifest"],
            root,
            expected_manifest_sha256=digests["demux_manifest"],
            expected_source_calls_sha256=source_calls_sha256,
            expected_preflight_sha256=preflight_sha256,
        )
        catalog_units = load_barcode_units(
            paths["barcode_units_manifest"],
            root,
            expected_manifest_sha256=digests["barcode_units_manifest"],
            expected_source_calls_sha256=source_calls_sha256,
            expected_preflight_sha256=preflight_sha256,
        )
    except (OSError, ValueError) as exc:
        raise BarcodeBatchError("source BAM or barcode unit manifest validation failed", status_code=409, code="SOURCE_UNIT_INVALID") from exc

    identity_fields = (
        "unit_id",
        "bam_sha256",
        "read_count",
        "unit_manifest_sha256",
        "source_calls_sha256",
        "preflight_sha256",
    )
    if [tuple(item[field] for field in identity_fields) for item in demux_units] != [
        tuple(item[field] for field in identity_fields) for item in catalog_units
    ]:
        raise BarcodeBatchError("source demux and barcode-unit manifests disagree", status_code=409, code="SOURCE_UNIT_INVALID")

    return {
        "root": root,
        "demux_manifest_sha256": digests["demux_manifest"],
        "barcode_units_manifest_sha256": digests["barcode_units_manifest"],
        "dorado_preflight_sha256": digests["dorado_preflight"],
        "dorado_runtime_provenance_sha256": digests["dorado_runtime_provenance"],
        "source_calls_sha256": source_calls_sha256,
        "preflight_sha256": preflight_sha256,
        "units": demux_units,
    }


async def _validate_receipts(
    session: AsyncSession,
    receipt_ids: list[str],
) -> dict[str, MolBioNgsReceipt]:
    if len(receipt_ids) != len(set(receipt_ids)):
        raise BarcodeBatchError("duplicate MolBio NGS receipts are forbidden")
    rows = (
        await session.execute(
            select(MolBioNgsReceipt).where(MolBioNgsReceipt.id.in_(receipt_ids))
        )
    ).scalars().all()
    by_id = {str(row.id): row for row in rows}
    if set(by_id) != set(receipt_ids):
        raise BarcodeBatchError("one or more MolBio NGS receipts are missing")

    inputs_root = get_inputs_dir().resolve()
    for receipt_id in receipt_ids:
        receipt = by_id[receipt_id]
        try:
            await consume_molbio_ngs_receipt(session, receipt_id=receipt_id)
        except ValueError as exc:
            raise BarcodeBatchError(str(exc), status_code=409, code="RECEIPT_INVALID") from exc
        if (
            not str(receipt.sequence_id or "").strip()
            or not str(receipt.revision_id or "").strip()
            or not _is_sha256(str(receipt.revision_sha256 or "").lower())
            or not _is_sha256(str(receipt.reference_snapshot_sha256 or "").lower())
        ):
            raise BarcodeBatchError("MolBio NGS receipt has no complete immutable revision identity", code="REVISION_MISSING")
        path_value = str(receipt.reference_snapshot_path or "")
        if not Path(path_value).is_absolute():
            raise BarcodeBatchError("MolBio NGS receipt reference snapshot must be an absolute server path", status_code=409, code="RECEIPT_INVALID")
        snapshot = _confined_file(Path(path_value), inputs_root, "MolBio NGS receipt FASTA")
        observed_snapshot_sha256 = _sha256_file(snapshot)
        if observed_snapshot_sha256 != str(receipt.reference_snapshot_sha256).lower():
            raise BarcodeBatchError("MolBio NGS receipt FASTA digest mismatch", status_code=409, code="RECEIPT_DIGEST_MISMATCH")
        try:
            observed_revision_sha256 = normalized_fasta_sequence_sha256(snapshot)
        except (OSError, UnicodeError, ValueError) as exc:
            raise BarcodeBatchError("MolBio NGS receipt FASTA is malformed", status_code=409, code="RECEIPT_DIGEST_MISMATCH") from exc
        if observed_revision_sha256 != str(receipt.revision_sha256).lower():
            raise BarcodeBatchError("MolBio NGS receipt revision digest mismatch", status_code=409, code="RECEIPT_DIGEST_MISMATCH")
    return by_id


def _relative_to_root(path: str, root: Path, label: str) -> str:
    try:
        return Path(path).resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise BarcodeBatchError(f"{label} is outside the authorized source root", status_code=409) from exc


def _validate_request_mapping(request: BarcodeBatchRequest, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_ids = [str(unit.get("unit_id") or "") for unit in units]
    source_set = set(source_ids)
    source_by_id = {str(unit.get("unit_id") or ""): unit for unit in units}
    canonical_source_ids = {unit_id for unit_id in source_set if CANONICAL_BARCODE_RE.fullmatch(unit_id)}
    if "unclassified" in source_set and any(str(item.unit_id) == "unclassified" for item in request.mappings):
        raise BarcodeBatchError("literal unclassified is retained as a source unit and cannot map to an intended construct")
    normalized: list[dict[str, Any]] = []
    seen_units: set[str] = set()
    seen_receipts: set[str] = set()
    seen_aliases: set[str] = set()
    for item in request.mappings:
        unit_id = item.unit_id
        receipt_id = item.molbio_ngs_receipt_id
        if not UNIT_RE.fullmatch(unit_id) or unit_id not in source_set:
            raise BarcodeBatchError(f"mapping names an unknown or malformed source unit: {unit_id}")
        if unit_id == "unclassified":
            raise BarcodeBatchError("unclassified cannot be mapped to an intended construct")
        if unit_id in seen_units:
            raise BarcodeBatchError(f"duplicate barcode mapping: {unit_id}")
        if receipt_id in seen_receipts:
            raise BarcodeBatchError("duplicate MolBio NGS receipts are forbidden")
        source_alias_raw = source_by_id[unit_id].get("sample_alias")
        source_alias = str(source_alias_raw).strip() if source_alias_raw is not None else None
        if source_alias == "":
            source_alias = None
        if source_alias is not None and item.sample_alias not in {None, source_alias}:
            raise BarcodeBatchError(
                f"sample_alias for {unit_id} does not match the authoritative Dorado sample sheet"
            )
        alias = source_alias or item.sample_alias
        alias_key = alias.casefold() if alias is not None else None
        if alias_key is not None and alias_key in seen_aliases:
            raise BarcodeBatchError("duplicate sample_alias values are forbidden")
        seen_units.add(unit_id)
        seen_receipts.add(receipt_id)
        if alias_key is not None:
            seen_aliases.add(alias_key)
        normalized.append(
            {
                "unit_id": unit_id,
                "sample_alias": alias,
                "molbio_ngs_receipt_id": receipt_id,
            }
        )
    if seen_units != canonical_source_ids:
        missing = sorted(canonical_source_ids - seen_units)
        extra = sorted(seen_units - canonical_source_ids)
        detail = []
        if missing:
            detail.append(f"missing={','.join(missing)}")
        if extra:
            detail.append(f"extra={','.join(extra)}")
        raise BarcodeBatchError("barcode mapping is incomplete or does not match the source demux" + (f" ({'; '.join(detail)})" if detail else ""))
    normalized.sort(key=lambda item: item["unit_id"])
    return normalized


def _safe_child_name(prefix: str, unit_id: str) -> str:
    name = f"{prefix} {unit_id}"
    if len(name) > 128:
        name = f"{prefix[: 128 - len(unit_id) - 1].rstrip()} {unit_id}"
    if len(name) > 128 or not name:
        raise BarcodeBatchError("name_prefix produces an oversized child job name")
    return name


def _stage_manifest(manifest_id: str, payload: dict[str, Any]) -> tuple[Path, str]:
    inputs_root = get_inputs_dir().resolve()
    destination_parent = inputs_root / REFERENCE_SET_ROOT_NAME
    if destination_parent.is_symlink():
        raise BarcodeBatchError("reference-set staging root symlink is forbidden", status_code=409)
    destination_parent.mkdir(parents=True, exist_ok=True)
    if destination_parent.resolve() != inputs_root / REFERENCE_SET_ROOT_NAME:
        raise BarcodeBatchError("reference-set staging root escaped the inputs root", status_code=409)
    destination = destination_parent / manifest_id
    if destination.exists() or destination.is_symlink():
        raise BarcodeBatchError("reference-set identity already has a staged directory", status_code=409)
    temporary = Path(tempfile.mkdtemp(prefix=f".{manifest_id}.", dir=destination_parent))
    try:
        path = temporary / "reference_set.json"
        payload_bytes = canonical_json_bytes(payload)
        with path.open("wb") as handle:
            handle.write(payload_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination / "reference_set.json", _sha256_bytes(payload_bytes)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _remove_staged_manifest(manifest_path: Path | None) -> None:
    if manifest_path is None:
        return
    try:
        manifest_path.parent.relative_to(get_inputs_dir().resolve() / REFERENCE_SET_ROOT_NAME)
    except (OSError, ValueError):
        return
    shutil.rmtree(manifest_path.parent, ignore_errors=True)


def _manifest_result(
    manifest_row: NgsReferenceSetManifest,
    mappings: list[NgsReferenceSetMapping],
    payload: dict[str, Any],
) -> dict[str, Any]:
    child_job_ids = [str(mapping.child_job_id) for mapping in mappings]
    return {
        "schema": REFERENCE_SET_SCHEMA,
        "reference_set_id": str(manifest_row.id),
        "source_job_id": str(manifest_row.source_job_id),
        "target_workflow": str(manifest_row.target_workflow),
        "mode": str(manifest_row.mode),
        "manifest_sha256": str(manifest_row.manifest_sha256),
        "manifest": payload,
        "child_job_ids": child_job_ids,
    }


async def _read_existing_manifest(
    session: AsyncSession,
    manifest_row: NgsReferenceSetManifest,
) -> dict[str, Any]:
    root = get_inputs_dir().resolve()
    path = _confined_file(Path(str(manifest_row.manifest_path)), root, "reference-set manifest")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BarcodeBatchError("existing reference-set manifest is unavailable or malformed", status_code=409, code="REFERENCE_SET_CORRUPT") from exc
    if not isinstance(payload, dict) or _sha256_bytes(raw) != manifest_row.manifest_sha256 or canonical_json_bytes(payload) != raw:
        raise BarcodeBatchError("existing reference-set manifest digest mismatch", status_code=409, code="REFERENCE_SET_CORRUPT")
    if (
        payload.get("schema") != REFERENCE_SET_SCHEMA
        or payload.get("reference_set_id") != manifest_row.id
        or payload.get("source_job_id") != manifest_row.source_job_id
        or payload.get("mode") != REFERENCE_SET_MODE
    ):
        raise BarcodeBatchError("existing reference-set manifest identity is invalid", status_code=409, code="REFERENCE_SET_CORRUPT")
    mappings = (
        await session.execute(
            select(NgsReferenceSetMapping)
            .where(NgsReferenceSetMapping.reference_set_id == manifest_row.id)
            .order_by(NgsReferenceSetMapping.unit_id)
        )
    ).scalars().all()
    if not mappings:
        raise BarcodeBatchError("existing reference-set mapping rows are missing", status_code=409, code="REFERENCE_SET_CORRUPT")
    child_ids = [str(mapping.child_job_id) for mapping in mappings]
    children = (
        await session.execute(select(Job.id).where(Job.id.in_(child_ids)))
    ).scalars().all()
    if set(str(child_id) for child_id in children) != set(child_ids):
        raise BarcodeBatchError("existing reference-set child jobs are missing", status_code=409, code="REFERENCE_SET_CORRUPT")
    entries_by_unit = {str(entry.get("unit_id")): entry for entry in payload.get("entries", []) if isinstance(entry, dict)}
    if len(entries_by_unit) != len(mappings) or any(
        entries_by_unit.get(str(mapping.unit_id), {}).get("child_job_id") != str(mapping.child_job_id)
        for mapping in mappings
    ):
        raise BarcodeBatchError("existing reference-set mapping does not match its manifest", status_code=409, code="REFERENCE_SET_CORRUPT")
    return _manifest_result(manifest_row, list(mappings), payload)


async def _find_idempotent_manifest(
    session: AsyncSession,
    *,
    idempotency_key: str,
    fingerprint: str,
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
    if row.request_fingerprint != fingerprint:
        raise BarcodeBatchError(
            "idempotency_key is already bound to a different request",
            status_code=409,
            code="IDEMPOTENCY_CONFLICT",
        )
    return await _read_existing_manifest(session, row)


async def _begin_writer_transaction(session: AsyncSession) -> None:
    if session.in_transaction():
        if session.new or session.dirty or session.deleted:
            raise BarcodeBatchError("barcode-batch session has uncommitted external writes", status_code=409)
        await session.rollback()
    await session.execute(text("BEGIN IMMEDIATE"))


async def _create_one_child(
    *,
    session: AsyncSession,
    background_tasks: Any,
    source: Mapping[str, Any],
    source_products: Mapping[str, Any],
    mapping: Mapping[str, Any],
    receipt: MolBioNgsReceipt,
    reference_set_id: str,
    reference_set_sha256: str,
    reference_set_path: Path,
    child_job_id: str,
    mapping_id: str,
    request: BarcodeBatchRequest,
) -> tuple[str, str]:
    unit = source_products["unit_by_id"][mapping["unit_id"]]
    revision_binding = {
        "sequence_id": str(receipt.sequence_id),
        "revision_id": str(receipt.revision_id),
        "revision_sha256": str(receipt.revision_sha256).lower(),
        "reference_snapshot_sha256": str(receipt.reference_snapshot_sha256).lower(),
        "receipt_id": str(receipt.id),
        "binding_source": "server_issued_one_time_receipt",
    }
    reference_set_binding = {
        "reference_set_id": reference_set_id,
        "manifest_sha256": reference_set_sha256,
        "manifest_path": str(reference_set_path.resolve()),
        "binding_source": "server_staged_immutable_reference_set",
    }
    barcode_mapping_binding = {
        "mapping_id": mapping_id,
        "reference_set_id": reference_set_id,
        "unit_id": str(mapping["unit_id"]),
        "sample_alias": mapping["sample_alias"],
        "source_bam_sha256": str(unit["bam_sha256"]),
        "source_unit_manifest_sha256": str(unit["unit_manifest_sha256"]),
        "binding_source": "server_authorized_completed_dorado_demux",
    }
    params = {
        "bam_path": str(unit["bam_path"]),
        "reference_fasta": str(receipt.reference_snapshot_path),
        "bam_force_realign": True,
        "bam_source_sha256": str(unit["bam_sha256"]),
        "source_ont_job_id": str(source["id"]),
        "source_barcode_unit": str(unit["unit_id"]),
        "source_barcode_manifest_sha256": str(source_products["demux_manifest_sha256"]),
        "source_barcode_demux_manifest_sha256": str(source_products["demux_manifest_sha256"]),
        "source_barcode_units_manifest_sha256": str(source_products["barcode_units_manifest_sha256"]),
        "source_barcode_unit_manifest_sha256": str(unit["unit_manifest_sha256"]),
        "source_barcode_source_calls_sha256": str(unit["source_calls_sha256"]),
        "source_barcode_preflight_sha256": str(unit["preflight_sha256"]),
        "molbio_revision_binding": revision_binding,
        "reference_set_binding": reference_set_binding,
        "ngs_reference_set_binding": reference_set_binding,
        "barcode_mapping_binding": barcode_mapping_binding,
        "lineage_root_job_id": str(source["lineage_root_job_id"]),
        "source_stage_job_id": str(source["id"]),
        "source_stage_family": str(source["stage_family"]),
        "source_stage_mode": str(source["stage_mode"]),
        "selection_source_type": "ont_dorado_demux",
        "selection_source_job_id": str(source["id"]),
        "source_selection_count": len(source_products["unit_by_id"]),
    }
    prefix = request.name_prefix or str(source["name"]).strip() or f"ONT {request.target_workflow}"
    from routers.jobs import create_job  # noqa: PLC0415

    from routers.ont_runs import OntNgsSubmitRequest, _job_create_for_ont_submit  # noqa: PLC0415

    submit = OntNgsSubmitRequest(
        name=_safe_child_name(prefix, str(mapping["unit_id"])),
        params=params,
        pinned_gpu=request.pinned_gpu,
    )
    trusted_params = frozenset(
        {
            "bam_source_sha256",
            "source_ont_job_id",
            "source_barcode_unit",
            "source_barcode_manifest_sha256",
            "source_barcode_demux_manifest_sha256",
            "source_barcode_units_manifest_sha256",
            "source_barcode_unit_manifest_sha256",
            "source_barcode_source_calls_sha256",
            "source_barcode_preflight_sha256",
            "molbio_revision_binding",
            "reference_set_binding",
            "ngs_reference_set_binding",
            "barcode_mapping_binding",
        }
    )
    job = _job_create_for_ont_submit(
        request.target_workflow,
        submit,
        trusted_server_params=trusted_params,
        trusted_result_paths=frozenset({"bam_path"}),
    ).model_copy(
        update={
            "parent_job_id": str(source["id"]),
            "child_stage": f"barcoded_reference_set_{reference_set_id[:8]}",
            "batch_id": reference_set_id,
            "batch_name": f"{prefix} barcoded batch",
        }
    )
    token, token_digest = alignment_access.issue_alignment_access_token()
    trust_token = ont_submission_trust.begin_trusted_ont_job_creation(token_digest)
    try:
        created = await create_job(
            job,
            background_tasks,
            session,
            _preallocated_job_id=child_job_id,
            _commit=False,
        )
    finally:
        ont_submission_trust.end_trusted_ont_job_creation(trust_token)
    created_id = getattr(created, "id", None)
    if created_id is not None and str(created_id) != child_job_id:
        raise BarcodeBatchError("canonical job creation returned an unexpected child identity", status_code=409, code="CHILD_IDENTITY_CONFLICT")
    return child_job_id, token


async def create_barcoded_reference_set(
    *,
    session: AsyncSession,
    source_job: Job,
    source_root: Path,
    source_demux_manifest_sha256: str,
    request: BarcodeBatchRequest,
    background_tasks: Any,
    http_request: Any,
    response: Any,
) -> dict[str, Any]:
    """Validate and atomically create one server-authorized barcode child set."""

    source = _source_snapshot(source_job, source_root)
    source_id = str(source["id"])
    fingerprint = request_fingerprint(request, source_job_id=source_id)
    existing = await _find_idempotent_manifest(
        session,
        idempotency_key=request.idempotency_key,
        fingerprint=fingerprint,
    )
    if existing is not None:
        return existing

    staged_manifest_path: Path | None = None
    committed = False
    try:
        await _begin_writer_transaction(session)
        existing = await _find_idempotent_manifest(
            session,
            idempotency_key=request.idempotency_key,
            fingerprint=fingerprint,
        )
        if existing is not None:
            await session.rollback()
            return existing

        source_products = _validate_source_products(source)
        if source_products["demux_manifest_sha256"] != source_demux_manifest_sha256:
            raise BarcodeBatchError("authorized source demux digest changed before batch creation", status_code=409, code="SOURCE_DIGEST_MISMATCH")
        units = list(source_products["units"])
        source_products = dict(source_products)
        source_products["unit_by_id"] = {str(unit["unit_id"]): unit for unit in units}
        mappings = _validate_request_mapping(request, units)
        receipt_ids = [str(item["molbio_ngs_receipt_id"]) for item in mappings]
        receipts = await _validate_receipts(session, receipt_ids)

        reference_set_id = str(uuid.uuid4())
        child_ids = {
            item["unit_id"]: str(uuid.uuid5(CHILD_ID_NAMESPACE, f"{reference_set_id}:{item['unit_id']}"))
            for item in mappings
        }
        mapping_ids = {
            item["unit_id"]: str(uuid.uuid5(MAPPING_ID_NAMESPACE, f"{reference_set_id}:{item['unit_id']}"))
            for item in mappings
        }
        root = Path(source_products["root"])
        source_units = [
            {
                "unit_id": str(unit["unit_id"]),
                "read_count": unit["read_count"],
                "bam_sha256": str(unit["bam_sha256"]),
                "source_calls_sha256": str(unit["source_calls_sha256"]),
                "preflight_sha256": str(unit["preflight_sha256"]),
                "demux_manifest_sha256": str(source_products["demux_manifest_sha256"]),
                "unit_manifest_sha256": str(unit["unit_manifest_sha256"]),
                "source_bam_path": _relative_to_root(str(unit["bam_path"]), root, "source BAM"),
            }
            for unit in units
        ]
        manifest_entries = []
        for item in mappings:
            unit = source_products["unit_by_id"][item["unit_id"]]
            receipt = receipts[item["molbio_ngs_receipt_id"]]
            manifest_entries.append(
                {
                    "unit_id": item["unit_id"],
                    "sample_alias": item["sample_alias"],
                    "child_job_id": child_ids[item["unit_id"]],
                    "mapping_id": mapping_ids[item["unit_id"]],
                    "sequence_id": str(receipt.sequence_id),
                    "revision_id": str(receipt.revision_id),
                    "revision_sha256": str(receipt.revision_sha256).lower(),
                    "receipt_id": str(receipt.id),
                    "fasta_snapshot_sha256": str(receipt.reference_snapshot_sha256).lower(),
                    "source_bam_path": _relative_to_root(str(unit["bam_path"]), root, "source BAM"),
                    "source_bam_sha256": str(unit["bam_sha256"]),
                    "source_calls_sha256": str(unit["source_calls_sha256"]),
                    "preflight_sha256": str(unit["preflight_sha256"]),
                    "demux_manifest_sha256": str(source_products["demux_manifest_sha256"]),
                    "unit_manifest_sha256": str(unit["unit_manifest_sha256"]),
                }
            )
        manifest_payload = {
            "schema": REFERENCE_SET_SCHEMA,
            "version": 1,
            "reference_set_id": reference_set_id,
            "source_job_id": source_id,
            "target_workflow": request.target_workflow,
            "mode": REFERENCE_SET_MODE,
            "source": {
                "demux_manifest_sha256": str(source_products["demux_manifest_sha256"]),
                "barcode_units_manifest_sha256": str(source_products["barcode_units_manifest_sha256"]),
                "dorado_preflight_sha256": str(source_products["dorado_preflight_sha256"]),
                "dorado_runtime_provenance_sha256": str(source_products["dorado_runtime_provenance_sha256"]),
                "source_calls_sha256": str(source_products["source_calls_sha256"]),
                "preflight_sha256": str(source_products["preflight_sha256"]),
                "unit_ids": [str(unit["unit_id"]) for unit in units],
            },
            "source_units": source_units,
            "entries": manifest_entries,
        }
        staged_manifest_path, staged_manifest_sha256 = _stage_manifest(reference_set_id, manifest_payload)
        manifest_row = NgsReferenceSetManifest(
            id=reference_set_id,
            manifest_schema=REFERENCE_SET_SCHEMA,
            mode=REFERENCE_SET_MODE,
            source_job_id=source_id,
            target_workflow=request.target_workflow,
            idempotency_key=request.idempotency_key,
            request_fingerprint=fingerprint,
            manifest_path=str(staged_manifest_path.resolve()),
            manifest_sha256=staged_manifest_sha256,
            manifest_json=manifest_payload,
            created_at=datetime.utcnow(),
        )
        session.add(manifest_row)

        child_tokens: list[tuple[str, str]] = []
        for item in mappings:
            receipt = receipts[item["molbio_ngs_receipt_id"]]
            child_id, token = await _create_one_child(
                session=session,
                background_tasks=background_tasks,
                source=source,
                source_products=source_products,
                mapping=item,
                receipt=receipt,
                reference_set_id=reference_set_id,
                reference_set_sha256=staged_manifest_sha256,
                reference_set_path=staged_manifest_path,
                child_job_id=child_ids[item["unit_id"]],
                mapping_id=mapping_ids[item["unit_id"]],
                request=request,
            )
            child_tokens.append((child_id, token))

        now = datetime.utcnow()
        mapping_rows: list[NgsReferenceSetMapping] = []
        for item in mappings:
            unit = source_products["unit_by_id"][item["unit_id"]]
            receipt = receipts[item["molbio_ngs_receipt_id"]]
            child_id = child_ids[item["unit_id"]]
            receipt.consumed_at = now
            receipt.consumed_job_id = child_id
            mapping_row = NgsReferenceSetMapping(
                id=mapping_ids[item["unit_id"]],
                reference_set_id=reference_set_id,
                child_job_id=child_id,
                unit_id=item["unit_id"],
                sample_alias=item["sample_alias"],
                sequence_id=str(receipt.sequence_id),
                revision_id=str(receipt.revision_id),
                revision_sha256=str(receipt.revision_sha256).lower(),
                receipt_id=str(receipt.id),
                fasta_snapshot_sha256=str(receipt.reference_snapshot_sha256).lower(),
                source_bam_path=_relative_to_root(str(unit["bam_path"]), root, "source BAM"),
                source_bam_sha256=str(unit["bam_sha256"]),
                source_calls_sha256=str(unit["source_calls_sha256"]),
                preflight_sha256=str(unit["preflight_sha256"]),
                demux_manifest_sha256=str(source_products["demux_manifest_sha256"]),
                unit_manifest_sha256=str(unit["unit_manifest_sha256"]),
                created_at=now,
            )
            session.add(mapping_row)
            mapping_rows.append(mapping_row)
        await session.flush()
        await session.commit()
        committed = True
        for child_id, token in child_tokens:
            alignment_access.set_alignment_access_cookie(child_id, token, response, http_request)
        return _manifest_result(manifest_row, sorted(mapping_rows, key=lambda row: str(row.unit_id)), manifest_payload)
    except BarcodeBatchError:
        if committed:
            raise
        await session.rollback()
        _remove_staged_manifest(staged_manifest_path)
        raise
    except IntegrityError as exc:
        if committed:
            raise
        await session.rollback()
        _remove_staged_manifest(staged_manifest_path)
        raise BarcodeBatchError("barcode batch could not be committed atomically", status_code=409, code="BARCODE_BATCH_CONFLICT") from exc
    except Exception as exc:
        if committed:
            raise
        await session.rollback()
        _remove_staged_manifest(staged_manifest_path)
        raise BarcodeBatchError("barcode batch was rolled back and could not be committed", status_code=422, code="BARCODE_BATCH_ROLLED_BACK") from exc


async def list_reference_sets(session: AsyncSession, *, source_job_id: str) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(NgsReferenceSetManifest)
            .where(NgsReferenceSetManifest.source_job_id == source_job_id)
            .order_by(NgsReferenceSetManifest.created_at, NgsReferenceSetManifest.id)
        )
    ).scalars().all()
    manifests = []
    for row in rows:
        result = await _read_existing_manifest(session, row)
        manifests.append(result)
    return {
        "schema": REFERENCE_SET_LIST_SCHEMA,
        "source_job_id": source_job_id,
        "reference_sets": manifests,
    }


async def get_reference_set(
    session: AsyncSession,
    *,
    source_job_id: str,
    reference_set_id: str,
) -> dict[str, Any]:
    row = await session.get(NgsReferenceSetManifest, reference_set_id)
    if row is None or str(row.source_job_id) != source_job_id:
        raise BarcodeBatchError("reference-set manifest not found", status_code=404, code="REFERENCE_SET_NOT_FOUND")
    return await _read_existing_manifest(session, row)


__all__ = [
    "BarcodeBatchError",
    "BarcodeBatchRequest",
    "BarcodeBatchRequestMapping",
    "REFERENCE_SET_SCHEMA",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "create_barcoded_reference_set",
    "get_reference_set",
    "list_reference_sets",
    "request_fingerprint",
]
