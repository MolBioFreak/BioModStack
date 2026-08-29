"""Authoritative, bounded multi-record DNA import for the NGS handoff lane."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from io import StringIO
from typing import Any, Literal, Mapping

from Bio import SeqIO
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_models import (
    MolecularImportBatch,
    MolecularOperation,
    MolecularRevision,
    NucleotideSequence,
)
from services.molbio_persistence import (
    IdempotencyConflictError,
    add_operation_edges,
    begin_immediate_molbio_write,
    canonical_request_fingerprint,
    create_operation,
    record_sequence_revision,
)
from services.nucleotide_validation import canonicalize_nucleotide_sequence


MAX_IMPORT_SOURCE_CHARS = 10_000_000
MAX_IMPORT_SOURCE_BYTES = 10 * 1024 * 1024
MAX_IMPORT_REQUEST_BYTES = 12 * 1024 * 1024
MAX_IMPORT_RECORDS = 256
MAX_IMPORT_TOTAL_BASES = 10_000_000
MAX_IMPORT_RECORD_BASES = 2_000_000
MAX_IMPORT_FEATURES_PER_RECORD = 10_000
MAX_IMPORT_ERROR_MESSAGE = 500

Topology = Literal["linear", "circular"]
ImportSourceFormat = Literal["fasta", "genbank", "raw_dna", "raw-dna"]


class ImportFeatureSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    start: StrictInt = Field(ge=0)
    end: StrictInt = Field(ge=1)

    @model_validator(mode="after")
    def require_forward_interval(self) -> "ImportFeatureSegment":
        if self.end <= self.start:
            raise ValueError("feature segment end must be greater than start")
        return self


class ImportFeature(BaseModel):
    """The bounded feature subset accepted for explicit raw-DNA rows."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    id: str | None = Field(default=None, min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=255)
    type: str = Field(default="misc_feature", min_length=1, max_length=80)
    start: StrictInt | None = Field(default=None, ge=0)
    end: StrictInt | None = Field(default=None, ge=1)
    strand: StrictInt = Field(default=1, ge=-1, le=1)
    color: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2_000)
    notes: dict[str, Any] | None = None
    qualifiers: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    segments: list[ImportFeatureSegment] | None = Field(
        default=None,
        max_length=1_024,
    )


class RawDnaRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=255)
    sequence: str = Field(
        min_length=1,
        max_length=MAX_IMPORT_RECORD_BASES,
        validation_alias=AliasChoices("sequence", "dna"),
    )
    description: str | None = Field(default=None, max_length=2_000)
    accession: str | None = Field(default=None, max_length=100)
    organism: str | None = Field(default=None, max_length=255)
    topology: Topology | None = None
    features: list[ImportFeature] = Field(default_factory=list, max_length=MAX_IMPORT_FEATURES_PER_RECORD)


class TopologyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    record_ordinal: StrictInt = Field(ge=1, le=MAX_IMPORT_RECORDS)
    topology: Topology


class SequenceImportRequest(BaseModel):
    """Strict request for one server-parsed import source."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    source_format: ImportSourceFormat = Field(
        validation_alias=AliasChoices("source_format", "format"),
    )
    source_text: str | None = Field(
        default=None,
        max_length=MAX_IMPORT_SOURCE_CHARS,
        validation_alias=AliasChoices("source_text", "content", "text"),
    )
    raw_rows: list[RawDnaRow] | None = Field(
        default=None,
        max_length=MAX_IMPORT_RECORDS,
        validation_alias=AliasChoices("raw_rows", "rows", "records"),
    )
    topology_default: Topology
    topology_overrides: dict[int, Topology] | list[TopologyOverride] = Field(
        default_factory=dict,
        max_length=MAX_IMPORT_RECORDS,
    )
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    origin_surface: Literal["molbio", "ngs"] = "molbio"
    source_provider: Literal["upload", "paste", "ncbi", "library"] | None = None
    source_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_one_source(self) -> "SequenceImportRequest":
        source_format = normalize_source_format(self.source_format)
        has_text = self.source_text is not None
        has_rows = self.raw_rows is not None
        if source_format in {"fasta", "genbank"} and (not has_text or has_rows):
            raise ValueError(f"{source_format} import requires source_text and forbids raw_rows")
        if source_format == "raw_dna" and (not has_rows or has_text):
            raise ValueError("raw_dna import requires raw_rows and forbids source_text")
        if has_rows and len(self.raw_rows or []) == 0:
            raise ValueError("import source must contain at least one raw-DNA row")
        if has_rows and sum(len(row.sequence.encode("utf-8")) for row in self.raw_rows or []) > MAX_IMPORT_TOTAL_BASES:
            raise ValueError("raw-DNA rows exceed the maximum total sequence length")
        if len(json.dumps(self.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=True).encode("utf-8")) > MAX_IMPORT_REQUEST_BYTES:
            raise ValueError("sequence import request exceeds the maximum size")
        return self


# Keep the descriptive alias available to callers that use the domain name.
MolecularSequenceImportRequest = SequenceImportRequest


@dataclass
class _ParsedRecord:
    record_ordinal: int
    source_name: str
    name: str
    description: str | None
    sequence: str
    topology: str
    features: list[dict[str, Any]]
    accession: str | None = None
    organism: str | None = None


class SequenceImportInputError(ValueError):
    """A bounded source error suitable for a preview report."""

    def __init__(self, message: str, *, record_ordinal: int | None = None, code: str = "invalid_record") -> None:
        super().__init__(message)
        self.record_ordinal = record_ordinal
        self.code = code


def normalize_source_format(source_format: str) -> str:
    normalized = str(source_format).strip().lower()
    if normalized == "raw-dna":
        return "raw_dna"
    if normalized not in {"fasta", "genbank", "raw_dna"}:
        raise ValueError("source_format must be fasta, genbank, or raw_dna")
    return normalized


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _request_payload(request: SequenceImportRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    payload["source_format"] = normalize_source_format(request.source_format)
    payload["topology_overrides"] = _normalized_topology_overrides(request.topology_overrides)
    return payload


def import_request_fingerprint(request: SequenceImportRequest) -> str:
    return canonical_request_fingerprint(_request_payload(request))


def _source_payload(request: SequenceImportRequest) -> dict[str, Any]:
    source_format = normalize_source_format(request.source_format)
    if source_format in {"fasta", "genbank"}:
        return {"source_format": source_format, "source_text": request.source_text or ""}
    return {
        "source_format": source_format,
        "raw_rows": [row.model_dump(mode="json", exclude_none=True) for row in request.raw_rows or []],
    }


def source_digest(request: SequenceImportRequest) -> str:
    source_format = normalize_source_format(request.source_format)
    if source_format in {"fasta", "genbank"}:
        return hashlib.sha256((request.source_text or "").encode("utf-8")).hexdigest()
    return hashlib.sha256(_canonical_json(_source_payload(request)).encode("utf-8")).hexdigest()


def _normalized_topology_overrides(
    overrides: dict[int, Topology] | list[TopologyOverride],
) -> dict[str, str]:
    if isinstance(overrides, dict):
        items = overrides.items()
    else:
        items = ((item.record_ordinal, item.topology) for item in overrides)
    normalized: dict[int, str] = {}
    for raw_ordinal, topology in items:
        ordinal = int(raw_ordinal)
        if ordinal in normalized and normalized[ordinal] != topology:
            raise ValueError(f"topology override for record ordinal {ordinal} is duplicated")
        normalized[ordinal] = str(topology)
    # JSON object keys are strings. Normalize once so a direct service retry
    # and an HTTP retry return byte-for-byte equivalent result structures.
    return {str(ordinal): topology for ordinal, topology in sorted(normalized.items())}


def _bounded_message(error: BaseException) -> str:
    message = " ".join(str(error).split()) or type(error).__name__
    return message[:MAX_IMPORT_ERROR_MESSAGE]


def _error(
    message: str,
    *,
    record_ordinal: int | None = None,
    code: str = "invalid_record",
    field: str | None = None,
) -> dict[str, Any]:
    return {
        "record_ordinal": record_ordinal,
        "code": code,
        "field": field,
        "message": message[:MAX_IMPORT_ERROR_MESSAGE],
    }


def _canonicalize_feature(
    raw_feature: Mapping[str, Any],
    *,
    sequence_length: int,
    feature_id: str,
) -> dict[str, Any]:
    feature = dict(raw_feature)
    segments_value = feature.get("segments")
    if segments_value:
        segments = [
            {"start": int(segment["start"]), "end": int(segment["end"])}
            for segment in segments_value
        ]
    else:
        if feature.get("start") is None or feature.get("end") is None:
            raise ValueError("feature requires segments or start and end")
        segments = [{"start": int(feature["start"]), "end": int(feature["end"])}]

    for segment in segments:
        if (
            segment["start"] < 0
            or segment["end"] <= segment["start"]
            or segment["end"] > sequence_length
        ):
            raise ValueError(
                f"feature segment {segment['start']}-{segment['end']} exceeds sequence bounds"
            )

    strand = feature.get("strand", 1)
    if strand not in {-1, 0, 1, None}:
        raise ValueError("feature strand must be -1, 0, 1, or null")
    normalized_strand = -1 if strand == -1 else 1
    result: dict[str, Any] = {
        "id": str(feature.get("id") or feature_id),
        "name": str(feature.get("name") or feature.get("type") or "misc_feature"),
        "type": str(feature.get("type") or "misc_feature"),
        "start": min(segment["start"] for segment in segments),
        "end": max(segment["end"] for segment in segments),
        "strand": normalized_strand,
        "segments": segments,
    }
    if strand in {0, None}:
        result["strand_original"] = strand
    for field in ("color", "description", "notes", "qualifiers", "provenance", "location"):
        if field in feature and feature[field] is not None:
            result[field] = feature[field]
    return result


def _features_from_seqrecord(record: Any, *, ordinal: int, sequence_length: int) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for index, source_feature in enumerate(getattr(record, "features", []) or [], start=1):
        location = getattr(source_feature, "location", None)
        if location is None:
            raise SequenceImportInputError(
                "GenBank feature has no supported location",
                record_ordinal=ordinal,
                code="unsupported_feature",
            )
        parts = list(getattr(location, "parts", None) or [location])
        try:
            segments = [{"start": int(part.start), "end": int(part.end)} for part in parts]
        except (TypeError, ValueError, AttributeError) as exc:
            raise SequenceImportInputError(
                "GenBank feature location was not an integer interval",
                record_ordinal=ordinal,
                code="unsupported_feature",
            ) from exc
        qualifiers = {
            str(key): [str(value) for value in values]
            if isinstance(values, (list, tuple))
            else [str(values)]
            for key, values in (getattr(source_feature, "qualifiers", {}) or {}).items()
        }
        label_values = qualifiers.get("label") or qualifiers.get("gene") or qualifiers.get("locus_tag")
        label = label_values[0] if label_values else str(getattr(source_feature, "type", "misc_feature"))
        features.append(
            _canonicalize_feature(
                {
                    "id": f"record-{ordinal}-feature-{index}",
                    "name": label,
                    "type": str(getattr(source_feature, "type", "misc_feature")),
                    "strand": getattr(location, "strand", None),
                    "segments": segments,
                    "qualifiers": qualifiers,
                    "location": str(location),
                },
                sequence_length=sequence_length,
                feature_id=f"record-{ordinal}-feature-{index}",
            )
        )
    return features


def _seqrecord_to_parsed(record: Any, *, ordinal: int) -> _ParsedRecord:
    try:
        sequence = canonicalize_nucleotide_sequence(str(record.seq), "dna", allow_empty=False)
    except ValueError as exc:
        raise SequenceImportInputError(
            _bounded_message(exc),
            record_ordinal=ordinal,
            code="invalid_sequence",
        ) from exc
    if len(sequence) > MAX_IMPORT_RECORD_BASES:
        raise SequenceImportInputError(
            "record exceeds the maximum sequence length",
            record_ordinal=ordinal,
            code="record_too_large",
        )
    source_name = str(getattr(record, "id", "") or getattr(record, "name", "") or "").strip()
    if not source_name or source_name.startswith("<unknown"):
        source_name = f"record-{ordinal}"
    if len(source_name) > 255:
        raise SequenceImportInputError(
            "record name exceeds the maximum length",
            record_ordinal=ordinal,
            code="metadata_too_large",
        )
    description = str(getattr(record, "description", "") or "").strip() or None
    if description and description.startswith("<unknown"):
        description = None
    if description and len(description) > 2_000:
        raise SequenceImportInputError(
            "record description exceeds the maximum length",
            record_ordinal=ordinal,
            code="metadata_too_large",
        )
    annotations = getattr(record, "annotations", {}) or {}
    accessions = annotations.get("accessions") or []
    accession = str(accessions[0]) if accessions else None
    organism = str(annotations.get("organism") or "") or None
    if accession and len(accession) > 100 or organism and len(organism) > 255:
        raise SequenceImportInputError(
            "record annotation metadata exceeds the maximum length",
            record_ordinal=ordinal,
            code="metadata_too_large",
        )
    return _ParsedRecord(
        record_ordinal=ordinal,
        source_name=source_name,
        name=source_name,
        description=description,
        sequence=sequence,
        topology="linear",
        features=_features_from_seqrecord(record, ordinal=ordinal, sequence_length=len(sequence)),
        accession=accession,
        organism=organism,
    )


def _parse_text_source(
    request: SequenceImportRequest,
    *,
    errors: list[dict[str, Any]],
) -> list[_ParsedRecord]:
    source_format = normalize_source_format(request.source_format)
    source_text = request.source_text or ""
    if len(source_text.encode("utf-8")) > MAX_IMPORT_SOURCE_BYTES:
        errors.append(_error("source exceeds the maximum UTF-8 size", code="source_too_large"))
        return []
    parsed: list[_ParsedRecord] = []
    ordinal = 0
    try:
        records = SeqIO.parse(StringIO(source_text), "fasta" if source_format == "fasta" else "genbank")
        for ordinal, record in enumerate(records, start=1):
            try:
                parsed.append(_seqrecord_to_parsed(record, ordinal=ordinal))
            except SequenceImportInputError as exc:
                errors.append(
                    _error(
                        _bounded_message(exc),
                        record_ordinal=exc.record_ordinal or ordinal,
                        code=exc.code,
                    )
                )
    except Exception as exc:  # Biopython parser errors are source-bound, not server errors.
        errors.append(
            _error(
                "source parser rejected the input: " + _bounded_message(exc),
                record_ordinal=ordinal + 1 if ordinal else None,
                code="parse_error",
            )
        )
    if not parsed and not errors:
        errors.append(_error("source contains no records", code="empty_source"))
    return parsed


def _parse_raw_source(
    request: SequenceImportRequest,
    *,
    errors: list[dict[str, Any]],
) -> list[_ParsedRecord]:
    parsed: list[_ParsedRecord] = []
    total_bases = 0
    for ordinal, row in enumerate(request.raw_rows or [], start=1):
        try:
            sequence = canonicalize_nucleotide_sequence(row.sequence, "dna", allow_empty=False)
            if len(sequence) > MAX_IMPORT_RECORD_BASES:
                raise SequenceImportInputError(
                    "record exceeds the maximum sequence length",
                    record_ordinal=ordinal,
                    code="record_too_large",
                )
            features = [
                _canonicalize_feature(
                    feature.model_dump(mode="json", exclude_none=True),
                    sequence_length=len(sequence),
                    feature_id=f"record-{ordinal}-feature-{index}",
                )
                for index, feature in enumerate(row.features, start=1)
            ]
            total_bases += len(sequence)
            parsed.append(
                _ParsedRecord(
                    record_ordinal=ordinal,
                    source_name=row.name,
                    name=row.name,
                    description=row.description,
                    sequence=sequence,
                    topology=row.topology or "linear",
                    features=features,
                    accession=row.accession,
                    organism=row.organism,
                )
            )
        except SequenceImportInputError as exc:
            errors.append(
                _error(
                    _bounded_message(exc),
                    record_ordinal=exc.record_ordinal or ordinal,
                    code=exc.code,
                )
            )
        except (TypeError, ValueError, KeyError) as exc:
            errors.append(
                _error(
                    _bounded_message(exc),
                    record_ordinal=ordinal,
                    code="invalid_record",
                )
            )
    if total_bases > MAX_IMPORT_TOTAL_BASES:
        errors.append(_error("import exceeds the maximum total sequence length", code="batch_too_large"))
    if not parsed and not errors:
        errors.append(_error("source contains no records", code="empty_source"))
    return parsed


def _apply_topology(
    request: SequenceImportRequest,
    records: list[_ParsedRecord],
    *,
    errors: list[dict[str, Any]],
) -> None:
    overrides = {
        int(ordinal): topology
        for ordinal, topology in _normalized_topology_overrides(request.topology_overrides).items()
    }
    ordinals = {record.record_ordinal for record in records}
    for ordinal in overrides:
        if ordinal not in ordinals and ordinal > len(request.raw_rows or []) and normalize_source_format(request.source_format) == "raw_dna":
            errors.append(
                _error(
                    f"topology override names unknown record ordinal {ordinal}",
                    record_ordinal=ordinal,
                    code="unknown_topology_override",
                    field="topology_overrides",
                )
            )
        elif ordinal not in ordinals and normalize_source_format(request.source_format) != "raw_dna":
            errors.append(
                _error(
                    f"topology override names unknown record ordinal {ordinal}",
                    record_ordinal=ordinal,
                    code="unknown_topology_override",
                    field="topology_overrides",
                )
            )
    raw_rows = request.raw_rows or []
    for record in records:
        mapped = overrides.get(record.record_ordinal)
        row_topology = (
            raw_rows[record.record_ordinal - 1].topology
            if normalize_source_format(request.source_format) == "raw_dna"
            and record.record_ordinal - 1 < len(raw_rows)
            else None
        )
        if mapped and row_topology and mapped != row_topology:
            errors.append(
                _error(
                    "row topology conflicts with topology_overrides",
                    record_ordinal=record.record_ordinal,
                    code="conflicting_topology",
                    field="topology",
                )
            )
        record.topology = mapped or row_topology or request.topology_default


def _record_report(record: _ParsedRecord) -> dict[str, Any]:
    canonical_digest = hashlib.sha256(record.sequence.encode("ascii")).hexdigest()
    return {
        "record_ordinal": record.record_ordinal,
        "source_name": record.source_name,
        "name": record.name,
        "description": record.description,
        "sequence": record.sequence,
        "sequence_type": "dna",
        "topology": record.topology,
        "length": len(record.sequence),
        "canonical_digest": canonical_digest,
        "content_sha256": canonical_digest,
        "features": record.features,
        "accession": record.accession,
        "organism": record.organism,
    }


def build_sequence_import_preview(request: SequenceImportRequest) -> dict[str, Any]:
    """Parse and validate one request without opening or mutating a database."""

    source_format = normalize_source_format(request.source_format)
    errors: list[dict[str, Any]] = []
    records = (
        _parse_raw_source(request, errors=errors)
        if source_format == "raw_dna"
        else _parse_text_source(request, errors=errors)
    )
    try:
        _apply_topology(request, records, errors=errors)
    except ValueError as exc:
        errors.append(_error(_bounded_message(exc), code="invalid_topology", field="topology_overrides"))

    reports = [_record_report(record) for record in records]
    first_by_digest: dict[str, int] = {}
    exact_duplicates: list[dict[str, Any]] = []
    for report in reports:
        digest = report["canonical_digest"]
        duplicate_of = first_by_digest.setdefault(digest, report["record_ordinal"])
        if duplicate_of != report["record_ordinal"]:
            report["exact_duplicate_of"] = duplicate_of
            exact_duplicates.append(
                {
                    "record_ordinal": report["record_ordinal"],
                    "duplicate_of": duplicate_of,
                    "canonical_digest": digest,
                }
            )
        else:
            report["exact_duplicate_of"] = None

    total_bases = sum(int(report["length"]) for report in reports)
    if total_bases > MAX_IMPORT_TOTAL_BASES and not any(error["code"] == "batch_too_large" for error in errors):
        errors.append(_error("import exceeds the maximum total sequence length", code="batch_too_large"))
    return {
        "schema": "bms.molbio.sequence-import-preview.v1",
        "valid": not errors and bool(reports),
        "source_format": source_format,
        "source_digest": source_digest(request),
        "request_fingerprint": import_request_fingerprint(request),
        "topology_default": request.topology_default,
        "topology_overrides": _normalized_topology_overrides(request.topology_overrides),
        "record_count": len(reports),
        "records": reports,
        "exact_duplicates": exact_duplicates,
        "errors": errors,
    }


def _gc_content(sequence: str) -> float:
    return round((sequence.count("G") + sequence.count("C")) / len(sequence) * 100, 2)


def _require_idempotency_key(request: SequenceImportRequest, idempotency_key: str | None) -> str:
    body_key = request.idempotency_key
    if body_key and idempotency_key and body_key != idempotency_key:
        raise IdempotencyConflictError("body and Idempotency-Key values differ")
    key = (idempotency_key or body_key or "").strip()
    if not key or len(key) > 255:
        raise ValueError("sequence import commit requires a bounded idempotency key")
    return key


async def commit_sequence_import(
    session: AsyncSession,
    request: SequenceImportRequest,
    *,
    idempotency_key: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Commit a valid import and leave the caller with a clean session on failure."""

    try:
        return await _commit_sequence_import_transaction(
            session,
            request,
            idempotency_key=idempotency_key,
            created_by=created_by,
        )
    except Exception:
        if session.in_transaction():
            await session.rollback()
        raise


async def _commit_sequence_import_transaction(
    session: AsyncSession,
    request: SequenceImportRequest,
    *,
    idempotency_key: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Commit every valid imported record and revision in one MolBio transaction."""

    key = _require_idempotency_key(request, idempotency_key)
    fingerprint = import_request_fingerprint(request)

    await begin_immediate_molbio_write(session)
    existing = (
        await session.execute(
            select(MolecularImportBatch).where(MolecularImportBatch.idempotency_key == key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            await session.rollback()
            raise IdempotencyConflictError(
                "idempotency key is already bound to a different sequence import request"
            )
        await session.commit()
        return json.loads(_canonical_json(existing.result))

    existing_operation = (
        await session.execute(
            select(MolecularOperation).where(MolecularOperation.idempotency_key == key)
        )
    ).scalar_one_or_none()
    if existing_operation is not None:
        await session.rollback()
        raise IdempotencyConflictError(
            "idempotency key is already bound to another MolBio operation"
        )

    preview = build_sequence_import_preview(request)
    if not preview["valid"]:
        await session.rollback()
        raise SequenceImportInputError(
            "sequence import preview is invalid; no records were written",
            code="invalid_batch",
        )

    operation = await create_operation(
        session,
        operation_kind="sequence_import",
        implementation="server_authoritative_sequence_import",
        parameters={
            "source_format": preview["source_format"],
            "source_digest": preview["source_digest"],
            "record_count": preview["record_count"],
            "topology_default": preview["topology_default"],
            "topology_overrides": preview["topology_overrides"],
            "origin_surface": request.origin_surface,
            "source_provider": request.source_provider,
            "source_id": request.source_id,
        },
        provenance={
            "authority": "server_parser",
            "source_digest": preview["source_digest"],
            "request_fingerprint": fingerprint,
            "origin_surface": request.origin_surface,
            "source_provider": request.source_provider,
            "source_id": request.source_id,
        },
        idempotency_key=key,
        request_fingerprint=fingerprint,
        created_by=created_by,
    )

    persisted_records: list[dict[str, Any]] = []
    output_edges: list[tuple[MolecularRevision, str, dict[str, Any]]] = []
    for report in preview["records"]:
        existing_revision = (
            await session.execute(
                select(MolecularRevision)
                .where(MolecularRevision.content_sha256 == report["canonical_digest"])
                .order_by(MolecularRevision.created_at.asc(), MolecularRevision.id.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_revision is not None:
            existing_sequence = await session.get(NucleotideSequence, existing_revision.document_id)
            if existing_sequence is None:
                raise SequenceImportInputError(
                    "existing molecular revision has no source sequence",
                    code="invalid_existing_revision",
                )
            output_edges.append((
                existing_revision,
                "reused_imported_record",
                {
                    "record_ordinal": report["record_ordinal"],
                    "canonical_digest": report["canonical_digest"],
                    "origin_surface": request.origin_surface,
                    "source_provider": request.source_provider,
                    "source_id": request.source_id,
                },
            ))
            persisted_records.append({
                "record_ordinal": report["record_ordinal"],
                "source_name": report["source_name"],
                "name": existing_sequence.name,
                "description": existing_sequence.description,
                "sequence": existing_sequence.sequence,
                "sequence_type": existing_sequence.sequence_type,
                "accession": existing_sequence.accession,
                "organism": existing_sequence.organism,
                "sequence_id": existing_sequence.id,
                "document_id": existing_sequence.id,
                "revision_id": existing_revision.id,
                "revision_number": existing_revision.revision_number,
                "canonical_digest": existing_revision.content_sha256,
                "content_sha256": existing_revision.content_sha256,
                "topology": "circular" if existing_sequence.is_circular else "linear",
                "length": existing_revision.content_length,
                "features": existing_sequence.features or [],
                "exact_duplicate_of": report["exact_duplicate_of"],
                "reused_existing_revision": True,
            })
            continue
        sequence = NucleotideSequence(
            id=str(uuid.uuid4()),
            name=report["name"],
            description=report["description"],
            sequence=report["sequence"],
            sequence_type="dna",
            molecule_strandedness="double",
            molecule_orientation="not_applicable",
            is_circular=report["topology"] == "circular",
            length=report["length"],
            features=report["features"],
            primers=[],
            analysis_tracks=[],
            organism=report["organism"],
            accession=report["accession"],
            source_file=None,
            operation="sequence_import",
            operation_params={"record_ordinal": report["record_ordinal"]},
            version=1,
            gc_content=_gc_content(report["sequence"]),
        )
        session.add(sequence)
        revision = await record_sequence_revision(
            session,
            sequence,
            change_kind="import",
            provenance={
                "authority": "server_parser",
                "source_format": preview["source_format"],
                "source_digest": preview["source_digest"],
                "request_fingerprint": fingerprint,
                "record_ordinal": report["record_ordinal"],
                "canonical_digest": report["canonical_digest"],
                "topology": report["topology"],
            },
            operation_id=operation.id,
            created_by=created_by,
        )
        output_edges.append(
            (
                revision,
                "imported_record",
                {
                    "record_ordinal": report["record_ordinal"],
                    "canonical_digest": report["canonical_digest"],
                },
            )
        )
        persisted_records.append(
            {
                "record_ordinal": report["record_ordinal"],
                "source_name": report["source_name"],
                "name": report["name"],
                "description": report["description"],
                "sequence": report["sequence"],
                "sequence_type": "dna",
                "accession": report["accession"],
                "organism": report["organism"],
                "sequence_id": sequence.id,
                "document_id": sequence.id,
                "revision_id": revision.id,
                "revision_number": revision.revision_number,
                "canonical_digest": revision.content_sha256,
                "content_sha256": revision.content_sha256,
                "topology": report["topology"],
                "length": report["length"],
                "features": report["features"],
                "exact_duplicate_of": report["exact_duplicate_of"],
                "reused_existing_revision": False,
            }
        )

    await add_operation_edges(session, operation, output_revisions=output_edges)
    result = {
        "schema": "bms.molbio.sequence-import-commit.v1",
        "committed": True,
        "operation_id": operation.id,
        "batch_id": str(uuid.uuid4()),
        "idempotency_key": key,
        "request_fingerprint": fingerprint,
        "source_format": preview["source_format"],
        "source_digest": preview["source_digest"],
        "origin_surface": request.origin_surface,
        "source_provider": request.source_provider,
        "source_id": request.source_id,
        "topology_default": preview["topology_default"],
        "topology_overrides": preview["topology_overrides"],
        "record_count": len(persisted_records),
        "records": persisted_records,
        "exact_duplicates": preview["exact_duplicates"],
        "errors": [],
    }
    batch = MolecularImportBatch(
        id=result["batch_id"],
        operation_id=operation.id,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        source_format=preview["source_format"],
        source_sha256=preview["source_digest"],
        result=result,
        created_by=created_by,
    )
    session.add(batch)
    await session.flush()
    await session.commit()
    return result
