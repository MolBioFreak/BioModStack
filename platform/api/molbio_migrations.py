"""Backup-first extraction of legacy Mol Bio rows into the owned SQLite store."""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_database import create_molbio_engine, init_molbio_db, make_molbio_session_factory
from molbio_models import (
    MolecularDocument,
    MolecularRevision,
    NucleotideSequence,
    Primer,
    PrimerRevision,
)
from services.sqlite_backup import backup_sqlite_database


class MigrationConflictError(RuntimeError):
    pass


class MigrationVerificationError(RuntimeError):
    pass


async def _acquire_destination_extraction_lock(destination: Path) -> int:
    """Acquire a cooperative process-wide lock for one destination database."""

    lock_path = destination.with_suffix(destination.suffix + ".extract.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return descriptor
            except BlockingIOError:
                await asyncio.sleep(0.01)
    except BaseException:
        os.close(descriptor)
        raise


def _release_destination_extraction_lock(descriptor: int) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


@dataclass(frozen=True)
class LegacyMolBioMigrationReport:
    source_path: Path
    destination_path: Path
    backup_path: Path
    backup_sha256: str
    source_sequence_count: int
    destination_sequence_count: int
    source_primer_count: int
    destination_primer_count: int
    total_bases: int
    sequence_checksums: dict[str, str]
    copied_sequences: int
    skipped_sequences: int
    copied_primers: int
    skipped_primers: int
    foreign_key_violations: list[tuple[Any, ...]]
    orphan_parent_sequences: list[str]
    orphan_primer_targets: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("source_path", "destination_path", "backup_path"):
            payload[key] = str(payload[key])
        return payload


_SEQUENCE_FIELDS = (
    "id",
    "name",
    "description",
    "sequence",
    "sequence_type",
    "molecule_strandedness",
    "molecule_orientation",
    "is_circular",
    "length",
    "features",
    "primers",
    "analysis_tracks",
    "organism",
    "accession",
    "source_file",
    "parent_id",
    "operation",
    "operation_params",
    "version",
    "gc_content",
    "created_at",
    "updated_at",
)

_PRIMER_FIELDS = (
    "id",
    "name",
    "sequence",
    "sequence_type",
    "length",
    "tm",
    "gc_percent",
    "tm_algorithm",
    "tm_salt_correction",
    "tm_settings",
    "primer_type",
    "description",
    "target_sequence_id",
    "binding_start",
    "binding_end",
    "binding_strand",
    "tags",
    "is_favorite",
    "created_at",
    "updated_at",
)

_JSON_FIELDS = {"features", "primers", "analysis_tracks", "operation_params", "tm_settings", "tags"}
_BOOL_FIELDS = {"is_circular", "is_favorite"}
_DATETIME_FIELDS = {"created_at", "updated_at"}


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value)
    return value


def _coerce_record(row: sqlite3.Row, fields: Iterable[str]) -> dict[str, Any]:
    available = set(row.keys())
    record: dict[str, Any] = {}
    for field in fields:
        value = row[field] if field in available else None
        if field in _JSON_FIELDS:
            value = _parse_json(value)
        elif field in _BOOL_FIELDS:
            value = bool(value)
        elif field in _DATETIME_FIELDS:
            value = _parse_datetime(value)
        record[field] = value
    return record


def _normalizable(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is not None:
            normalized = normalized.astimezone(timezone.utc).replace(tzinfo=None)
        return normalized.isoformat(timespec="microseconds")
    if isinstance(value, dict):
        return {key: _normalizable(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalizable(item) for item in value]
    return value


def _canonical_record(record: dict[str, Any]) -> str:
    return json.dumps(_normalizable(record), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _model_record(model: Any, fields: Iterable[str]) -> dict[str, Any]:
    return {field: getattr(model, field) for field in fields}


def _sequence_sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def _sequence_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _normalizable(value) for key, value in record.items()}


def _read_legacy_rows(source: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30.0) as connection:
        connection.row_factory = sqlite3.Row
        table_names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "nucleotide_sequences" not in table_names:
            raise MigrationVerificationError("Legacy source has no nucleotide_sequences table")
        sequence_rows = [
            _coerce_record(row, _SEQUENCE_FIELDS)
            for row in connection.execute("SELECT * FROM nucleotide_sequences ORDER BY id").fetchall()
        ]
        primer_rows: list[dict[str, Any]] = []
        if "primers" in table_names:
            primer_rows = [
                _coerce_record(row, _PRIMER_FIELDS)
                for row in connection.execute("SELECT * FROM primers ORDER BY id").fetchall()
            ]
    return sequence_rows, primer_rows


def _order_sequences_by_parent_dependency(
    sequence_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a stable parent-before-child order, rejecting orphans and cycles."""

    by_id = {str(record["id"]): record for record in sequence_rows}
    if len(by_id) != len(sequence_rows):
        raise MigrationVerificationError("Legacy sequence graph contains duplicate IDs")

    ordered: list[dict[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(sequence_id: str) -> None:
        if sequence_id in visited:
            return
        if sequence_id in visiting:
            raise MigrationVerificationError(
                f"Legacy sequence parent graph contains a cycle at {sequence_id}"
            )
        visiting.add(sequence_id)
        record = by_id[sequence_id]
        parent_value = record.get("parent_id")
        if parent_value not in (None, ""):
            parent_id = str(parent_value)
            if parent_id not in by_id:
                raise MigrationVerificationError(
                    f"Legacy sequence {sequence_id} references missing parent {parent_id}"
                )
            visit(parent_id)
        visiting.remove(sequence_id)
        visited.add(sequence_id)
        ordered.append(record)

    for record in sequence_rows:
        visit(str(record["id"]))
    return ordered


async def _verify_legacy_history_graph(
    session: AsyncSession,
    sequence_rows: list[dict[str, Any]],
    primer_rows: list[dict[str, Any]],
    source_checksums: dict[str, str],
    backup_sha256: str,
) -> None:
    """Fail closed unless the destination has exactly the expected legacy history graph."""

    expected_sequence_ids = {str(record["id"]) for record in sequence_rows}
    expected_primer_ids = {str(record["id"]) for record in primer_rows}
    documents = (await session.execute(select(MolecularDocument))).scalars().all()
    molecular_revisions = (await session.execute(select(MolecularRevision))).scalars().all()
    primer_revisions = (await session.execute(select(PrimerRevision))).scalars().all()

    document_ids = {str(document.id) for document in documents}
    revision_document_ids = {str(revision.document_id) for revision in molecular_revisions}
    revision_primer_ids = {str(revision.primer_id) for revision in primer_revisions}
    if (
        document_ids != expected_sequence_ids
        or revision_document_ids != expected_sequence_ids
        or len(molecular_revisions) != len(expected_sequence_ids)
        or revision_primer_ids != expected_primer_ids
        or len(primer_revisions) != len(expected_primer_ids)
    ):
        raise MigrationVerificationError(
            "Destination immutable history graph has missing or surplus documents/revisions"
        )

    documents_by_id = {str(document.id): document for document in documents}
    revisions_by_document = {
        str(revision.document_id): revision for revision in molecular_revisions
    }
    expected_provenance_base = {
        "source": "legacy_core_sqlite",
        "source_database_backup_sha256": backup_sha256,
    }

    for record in sequence_rows:
        sequence_id = str(record["id"])
        document = documents_by_id[sequence_id]
        revision = revisions_by_document[sequence_id]
        expected_provenance = {
            **expected_provenance_base,
            "source_row_id": record["id"],
        }
        source_created_at = record.get("created_at")
        if (
            document.current_revision_id != revision.id
            or document.document_kind != (record["sequence_type"] or "dna")
            or document.name != record["name"]
            or document.deleted_at is not None
            or (source_created_at is not None and document.created_at != source_created_at)
            or revision.revision_number != int(record["version"] or 1)
            or revision.change_kind != "legacy_import"
            or revision.content_sha256 != source_checksums[sequence_id]
            or revision.content_length != len(str(record["sequence"]))
            or _canonical_record(revision.snapshot) != _canonical_record(record)
            or revision.provenance != expected_provenance
            or revision.operation_id is not None
            or revision.created_by is not None
            or (source_created_at is not None and revision.created_at != source_created_at)
        ):
            raise MigrationVerificationError(
                f"Incomplete or corrupt sequence history graph for {sequence_id}"
            )

    primer_revisions_by_id = {
        str(revision.primer_id): revision for revision in primer_revisions
    }
    for record in primer_rows:
        primer_id = str(record["id"])
        revision = primer_revisions_by_id[primer_id]
        expected_provenance = {
            **expected_provenance_base,
            "source_row_id": record["id"],
        }
        source_created_at = record.get("created_at")
        if (
            revision.revision_number != 1
            or revision.change_kind != "legacy_import"
            or revision.sequence_sha256 != _sequence_sha256(str(record["sequence"]))
            or _canonical_record(revision.snapshot) != _canonical_record(record)
            or revision.provenance != expected_provenance
            or revision.created_by is not None
            or (source_created_at is not None and revision.created_at != source_created_at)
        ):
            raise MigrationVerificationError(
                f"Incomplete or corrupt primer history graph for {primer_id}"
            )


async def extract_legacy_molbio_data(
    source_path: Path,
    destination_path: Path,
    *,
    backup_path: Path,
    expected_sequence_count: int | None = None,
    expected_total_bases: int | None = None,
    expected_primer_count: int | None = None,
) -> LegacyMolBioMigrationReport:
    """Back up, copy, and independently verify legacy Mol Bio records.

    The source database is never modified. Existing matching destination rows
    are skipped; any same-ID difference is an explicit conflict.
    """

    source = Path(source_path).expanduser().resolve()
    destination = Path(destination_path).expanduser().resolve()
    backup = Path(backup_path).expanduser().resolve()
    paths = (("source", source), ("destination", destination), ("backup", backup))
    for index, (left_label, left) in enumerate(paths[:-1]):
        for right_label, right in paths[index + 1 :]:
            aliases = left == right
            if not aliases and left.exists() and right.exists():
                aliases = os.path.samefile(left, right)
            if aliases:
                raise ValueError(
                    f"Mol Bio extraction {left_label} and {right_label} paths must be distinct "
                    "and must not alias the same file"
                )
    backup_report = backup_sqlite_database(source, backup)
    # Extract from the verified online-backup snapshot so a concurrent legacy
    # writer cannot make the copied rows diverge from the provenance artifact.
    sequence_rows, primer_rows = _read_legacy_rows(backup_report.backup_path)
    sequence_rows = _order_sequences_by_parent_dependency(sequence_rows)
    source_checksums = {
        str(row["id"]): _sequence_sha256(str(row["sequence"])) for row in sequence_rows
    }
    source_total_bases = sum(len(str(row["sequence"])) for row in sequence_rows)
    observed_inventory = (len(sequence_rows), source_total_bases, len(primer_rows))
    expected_inventory = (
        expected_sequence_count,
        expected_total_bases,
        expected_primer_count,
    )
    for label, expected, observed in zip(
        ("sequence count", "total bases", "primer count"),
        expected_inventory,
        observed_inventory,
        strict=True,
    ):
        if expected is not None and expected != observed:
            raise MigrationVerificationError(
                f"Legacy {label} changed before extraction: expected={expected} observed={observed}"
            )

    lock_descriptor = await _acquire_destination_extraction_lock(destination)
    try:
        engine = create_molbio_engine(f"sqlite+aiosqlite:///{destination}")
    except BaseException:
        _release_destination_extraction_lock(lock_descriptor)
        raise
    copied_sequences = 0
    skipped_sequences = 0
    copied_primers = 0
    skipped_primers = 0
    try:
        await init_molbio_db(engine=engine)
        factory = make_molbio_session_factory(engine)
        async with factory() as session:
            async with session.begin():
                for record in sequence_rows:
                    existing = await session.get(NucleotideSequence, record["id"])
                    if existing is not None:
                        existing_payload = _canonical_record(_model_record(existing, _SEQUENCE_FIELDS))
                        if existing_payload != _canonical_record(record):
                            raise MigrationConflictError(
                                f"Destination sequence {record['id']} differs from the legacy source"
                            )
                        skipped_sequences += 1
                        continue

                    sequence = NucleotideSequence(**record)
                    document = MolecularDocument(
                        id=record["id"],
                        document_kind=record["sequence_type"] or "dna",
                        name=record["name"],
                        created_at=record["created_at"] or datetime.utcnow(),
                    )
                    revision = MolecularRevision(
                        id=str(uuid.uuid4()),
                        document_id=record["id"],
                        revision_number=int(record["version"] or 1),
                        change_kind="legacy_import",
                        content_sha256=source_checksums[str(record["id"])],
                        content_length=len(str(record["sequence"])),
                        snapshot=_sequence_snapshot(record),
                        provenance={
                            "source": "legacy_core_sqlite",
                            "source_database_backup_sha256": backup_report.sha256,
                            "source_row_id": record["id"],
                        },
                        created_at=record["created_at"] or datetime.utcnow(),
                    )
                    session.add_all([sequence, document, revision])
                    await session.flush()
                    document.current_revision_id = revision.id
                    copied_sequences += 1

                await session.flush()
                for record in primer_rows:
                    existing = await session.get(Primer, record["id"])
                    if existing is not None:
                        existing_payload = _canonical_record(_model_record(existing, _PRIMER_FIELDS))
                        if existing_payload != _canonical_record(record):
                            raise MigrationConflictError(
                                f"Destination primer {record['id']} differs from the legacy source"
                            )
                        skipped_primers += 1
                        continue

                    target_id = record.get("target_sequence_id")
                    if target_id and await session.get(NucleotideSequence, target_id) is None:
                        raise MigrationVerificationError(
                            f"Primer {record['id']} references missing sequence {target_id}"
                        )
                    primer = Primer(**record)
                    primer_revision = PrimerRevision(
                        id=str(uuid.uuid4()),
                        primer_id=record["id"],
                        revision_number=1,
                        change_kind="legacy_import",
                        sequence_sha256=_sequence_sha256(str(record["sequence"])),
                        snapshot=_sequence_snapshot(record),
                        provenance={
                            "source": "legacy_core_sqlite",
                            "source_database_backup_sha256": backup_report.sha256,
                            "source_row_id": record["id"],
                        },
                        created_at=record["created_at"] or datetime.utcnow(),
                    )
                    session.add_all([primer, primer_revision])
                    copied_primers += 1

                await session.flush()
                destination_sequence_count = int(
                    (
                        await session.execute(select(func.count()).select_from(NucleotideSequence))
                    ).scalar_one()
                )
                destination_primer_count = int(
                    (await session.execute(select(func.count()).select_from(Primer))).scalar_one()
                )
                destination_rows = (
                    await session.execute(select(NucleotideSequence.id, NucleotideSequence.sequence))
                ).all()
                destination_checksums = {
                    str(row.id): _sequence_sha256(str(row.sequence)) for row in destination_rows
                }
                destination_quick_check = [
                    str(row[0])
                    for row in (
                        await session.execute(text("PRAGMA quick_check"))
                    ).fetchall()
                ]
                foreign_key_violations = [
                    tuple(row)
                    for row in (await session.execute(text("PRAGMA foreign_key_check"))).fetchall()
                ]
                orphan_parent_sequences = [
                    str(row[0])
                    for row in (
                        await session.execute(
                            text(
                                "SELECT child.id FROM nucleotide_sequences child "
                                "LEFT JOIN nucleotide_sequences parent ON parent.id = child.parent_id "
                                "WHERE child.parent_id IS NOT NULL AND parent.id IS NULL ORDER BY child.id"
                            )
                        )
                    ).fetchall()
                ]
                orphan_primer_targets = [
                    str(row[0])
                    for row in (
                        await session.execute(
                            text(
                                "SELECT primer.id FROM primers primer "
                                "LEFT JOIN nucleotide_sequences target ON target.id = primer.target_sequence_id "
                                "WHERE primer.target_sequence_id IS NOT NULL AND target.id IS NULL ORDER BY primer.id"
                            )
                        )
                    ).fetchall()
                ]

                if destination_sequence_count != len(sequence_rows):
                    raise MigrationVerificationError(
                        "Sequence count mismatch: "
                        f"source={len(sequence_rows)} destination={destination_sequence_count}"
                    )
                if destination_primer_count != len(primer_rows):
                    raise MigrationVerificationError(
                        "Primer count mismatch: "
                        f"source={len(primer_rows)} destination={destination_primer_count}"
                    )
                if destination_checksums != source_checksums:
                    raise MigrationVerificationError("Per-sequence SHA-256 verification failed")
                if destination_quick_check != ["ok"]:
                    raise MigrationVerificationError(
                        "Destination PRAGMA quick_check failed: "
                        + "; ".join(destination_quick_check)
                    )
                if foreign_key_violations or orphan_parent_sequences or orphan_primer_targets:
                    raise MigrationVerificationError(
                        "Destination has foreign-key violations or orphan molecular references"
                    )
                await _verify_legacy_history_graph(
                    session,
                    sequence_rows,
                    primer_rows,
                    source_checksums,
                    backup_report.sha256,
                )

        return LegacyMolBioMigrationReport(
            source_path=source,
            destination_path=destination,
            backup_path=backup_report.backup_path,
            backup_sha256=backup_report.sha256,
            source_sequence_count=len(sequence_rows),
            destination_sequence_count=destination_sequence_count,
            source_primer_count=len(primer_rows),
            destination_primer_count=destination_primer_count,
            total_bases=source_total_bases,
            sequence_checksums=source_checksums,
            copied_sequences=copied_sequences,
            skipped_sequences=skipped_sequences,
            copied_primers=copied_primers,
            skipped_primers=skipped_primers,
            foreign_key_violations=foreign_key_violations,
            orphan_parent_sequences=orphan_parent_sequences,
            orphan_primer_targets=orphan_primer_targets,
        )
    finally:
        try:
            await engine.dispose()
        finally:
            _release_destination_extraction_lock(lock_descriptor)
