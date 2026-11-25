"""SQLAlchemy publication helpers for immutable JSON-to-Parquet migration."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Iterable, Mapping
import uuid

import pyarrow as pa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from database import ScientificArtifactReceipt, _SCIENTIFIC_JSON_INLINE_LIMIT
from .contracts import artifact_row_reference, canonical_sha256, envelope_rows
from .writer import (
    artifact_root,
    finalize_artifact_publication,
    guarded_delete_new_artifact,
    install_parquet_rows,
)


_TRANSACTION_ARTIFACTS_KEY = "scientific_artifacts_transaction_publications"
_TRANSACTION_ID_KEY = "scientific_artifacts_transaction_id"

DESIGN_ARTIFACT_FIELDS = (
    "confidence_metrics", "residue_plddt", "rfa_loop_metrics", "rfa_hotspot_metrics",
    "provenance", "review_artifact_manifest", "review_role_map", "chain_metrics",
    "frustration_residues", "rfa_design_loops", "rfa_hotspots", "ppiflow_loop_metrics",
    "metric_provenance", "metric_completeness", "pair_chains_iptm", "chains_ptm",
)
DESIGN_ALWAYS_ARTIFACT_FIELDS = frozenset(
    {
        "confidence_metrics",
        "residue_plddt",
        "rfa_loop_metrics",
        "rfa_hotspot_metrics",
    }
)
DESIGN_INLINE_LIMIT = _SCIENTIFIC_JSON_INLINE_LIMIT
_DESIGN_ARTIFACT_REF_SCHEMAS = frozenset(
    {
        "bms.scientific-artifact-reference.v1",
        "bms.scientific-artifact-row-reference.v1",
    }
)


@event.listens_for(Session, "after_commit")
def _forget_committed_artifacts(session: Session) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    publications = session.info.get(_TRANSACTION_ARTIFACTS_KEY, {})
    artifacts = publications.pop(transaction, ())
    session.info.get(_TRANSACTION_ID_KEY, {}).pop(transaction, None)
    if transaction.nested:
        # RELEASE SAVEPOINT transfers custody, not durability, to its parent.
        publications.setdefault(transaction.parent, []).extend(artifacts)
    else:
        for artifact in artifacts:
            finalize_artifact_publication(artifact, committed=True)


@event.listens_for(Session, "after_rollback")
def _cleanup_rolled_back_artifacts(session: Session) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    artifacts = session.info.get(_TRANSACTION_ARTIFACTS_KEY, {}).pop(transaction, ())
    session.info.get(_TRANSACTION_ID_KEY, {}).pop(transaction, None)
    for artifact in reversed(artifacts):
        guarded_delete_new_artifact(artifact)


def _track_new_artifact_sync(sync: Session, artifact: Any) -> None:
    if artifact.transaction_id is not None:
        transaction = sync.get_nested_transaction() or sync.get_transaction()
        sync.info.setdefault(_TRANSACTION_ARTIFACTS_KEY, {}).setdefault(
            transaction, []
        ).append(artifact)


def _track_new_artifact(session: AsyncSession, artifact: Any) -> None:
    _track_new_artifact_sync(session.sync_session, artifact)


def _transaction_id_sync(sync: Session) -> str:
    transaction = sync.get_nested_transaction() or sync.get_transaction() or sync.begin()
    return sync.info.setdefault(_TRANSACTION_ID_KEY, {}).setdefault(
        transaction, uuid.uuid4().hex
    )


def _transaction_id(session: AsyncSession) -> str:
    return _transaction_id_sync(session.sync_session)


async def publish_json_payload(
    session: AsyncSession,
    *,
    owner_kind: str,
    owner_id: str,
    role: str,
    schema_id: str,
    payload: Mapping[str, Any],
    source_sha256: str | None = None,
    installed_artifacts: list[Any] | None = None,
) -> dict[str, Any]:
    source_sha = source_sha256 or canonical_sha256(payload)
    schema = pa.schema(
        [
            ("key", pa.string()),
            ("item_index", pa.int64()),
            ("payload_json", pa.string()),
        ]
    )
    artifact = install_parquet_rows(
        root=artifact_root(),
        owner_kind=owner_kind,
        owner_id=owner_id,
        role=role,
        schema_id=schema_id,
        schema_version=1,
        source_sha256=source_sha,
        rows=envelope_rows(payload),
        schema=schema,
        transaction_id=_transaction_id(session),
    )
    if installed_artifacts is not None:
        installed_artifacts.append(artifact)
    _track_new_artifact(session, artifact)
    await _add_receipt(session, artifact, source_sha)
    return artifact.reference()


async def publish_table_rows(
    session: AsyncSession,
    *,
    owner_kind: str,
    owner_id: str,
    role: str,
    schema_id: str,
    source_sha256: str,
    rows: Iterable[Mapping[str, Any]],
    schema: pa.Schema,
    installed_artifacts: list[Any] | None = None,
    source_receipts: Mapping[str, Any] | None = None,
) -> Any:
    if source_receipts is not None and canonical_sha256(source_receipts) != source_sha256:
        raise ValueError("scientific artifact source receipts do not match source_sha256")
    artifact = install_parquet_rows(
        root=artifact_root(),
        owner_kind=owner_kind,
        owner_id=owner_id,
        role=role,
        schema_id=schema_id,
        schema_version=1,
        source_sha256=source_sha256,
        rows=rows,
        schema=schema,
        transaction_id=_transaction_id(session),
    )
    if installed_artifacts is not None:
        installed_artifacts.append(artifact)
    _track_new_artifact(session, artifact)
    await _add_receipt(session, artifact, source_sha256, source_receipts=source_receipts)
    return artifact


async def _add_receipt(
    session: AsyncSession,
    artifact: Any,
    source_sha256: str,
    *,
    source_receipts: Mapping[str, Any] | None = None,
) -> None:
    existing = await session.get(ScientificArtifactReceipt, artifact.artifact_id)
    if existing is not None:
        if (
            existing.content_sha256 != artifact.content_sha256
            or existing.size_bytes != artifact.size_bytes
            or existing.relative_path != artifact.relative_path
            or (
                source_receipts is not None
                and existing.source_receipts_json != dict(source_receipts)
            )
        ):
            raise ValueError(f"scientific artifact receipt conflict for {artifact.artifact_id}")
        return
    session.add(
        ScientificArtifactReceipt(
            artifact_id=artifact.artifact_id,
            owner_kind=artifact.owner_kind,
            owner_id=artifact.owner_id,
            role=artifact.role,
            schema_id=artifact.schema_id,
            artifact_schema_version=artifact.schema_version,
            content_sha256=artifact.content_sha256,
            size_bytes=artifact.size_bytes,
            row_count=artifact.row_count,
            column_schema_sha256=artifact.column_schema_sha256,
            storage_root="scientific_artifact_root",
            relative_path=artifact.relative_path,
            media_type=artifact.media_type,
            availability="available",
            source_receipts_json=(
                dict(source_receipts)
                if source_receipts is not None
                else {
                    "source_sha256": source_sha256,
                    "owner_kind": artifact.owner_kind,
                    "owner_id": artifact.owner_id,
                    "role": artifact.role,
                }
            ),
            created_at=datetime.utcnow(),
        )
    )


def _design_field_schema() -> pa.Schema:
    return pa.schema([
        ("row_index", pa.int64()),
        ("design_id", pa.string()),
        ("field_name", pa.string()),
        ("payload_json", pa.string()),
    ])


def _iter_design_fields_to_externalize(
    design: Any, fields: Iterable[str] | None = None
) -> list[tuple[str, str, str]]:
    target_fields = tuple(fields) if fields is not None else DESIGN_ARTIFACT_FIELDS
    pending: list[tuple[str, str, str]] = []
    for field_name in target_fields:
        value = getattr(design, field_name, None)
        if value is None or (
            isinstance(value, dict) and value.get("schema") in _DESIGN_ARTIFACT_REF_SCHEMAS
        ):
            continue
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True
        )
        if (
            field_name not in DESIGN_ALWAYS_ARTIFACT_FIELDS
            and len(encoded.encode("utf-8")) <= DESIGN_INLINE_LIMIT
        ):
            continue
        source_sha = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        pending.append((field_name, encoded, source_sha))
    return pending


def _build_design_field_receipt(artifact: Any, design_id: str, field_name: str) -> ScientificArtifactReceipt:
    return ScientificArtifactReceipt(
        artifact_id=artifact.artifact_id,
        owner_kind=artifact.owner_kind,
        owner_id=artifact.owner_id,
        role=artifact.role,
        schema_id=artifact.schema_id,
        artifact_schema_version=artifact.schema_version,
        content_sha256=artifact.content_sha256,
        size_bytes=artifact.size_bytes,
        row_count=artifact.row_count,
        column_schema_sha256=artifact.column_schema_sha256,
        storage_root="scientific_artifacts",
        relative_path=artifact.relative_path,
        media_type=artifact.media_type,
        availability="available",
        source_receipts_json={
            "source_table": "designs",
            "source_column": field_name,
            "source_key": design_id,
        },
    )


def _validate_design_receipt(existing_receipt: ScientificArtifactReceipt, artifact: Any) -> None:
    if (
        existing_receipt.content_sha256 != artifact.content_sha256
        or existing_receipt.size_bytes != artifact.size_bytes
        or existing_receipt.relative_path != artifact.relative_path
    ):
        raise ValueError("design scientific artifact receipt conflict")


async def externalize_design_fields(
    session: AsyncSession,
    design: Any,
    *,
    fields: Iterable[str] | None = None,
) -> list[Any]:
    """Externalize dense/oversized Design JSON fields to transaction-bound Parquet."""
    pending = _iter_design_fields_to_externalize(design, fields)
    if not pending:
        return []
    if not getattr(design, "id", None):
        design.id = str(uuid.uuid4())
    design_id = str(design.id)
    schema = _design_field_schema()
    sync = session.sync_session
    tx_id = _transaction_id_sync(sync)
    installed: list[Any] = []
    for field_name, encoded, source_sha in pending:
        row = {"row_index": 0, "design_id": design_id, "field_name": field_name, "payload_json": encoded}
        artifact = install_parquet_rows(
            root=artifact_root(),
            owner_kind="design_field",
            owner_id=f"{design_id}:{field_name}",
            role="payload",
            schema_id="bms.design.field.v1",
            schema_version=1,
            source_sha256=source_sha,
            rows=[row],
            schema=schema,
            transaction_id=tx_id,
        )
        _track_new_artifact_sync(sync, artifact)
        existing_receipt = next(
            (
                value
                for value in sync.new
                if isinstance(value, ScientificArtifactReceipt)
                and value.artifact_id == artifact.artifact_id
            ),
            None,
        )
        if existing_receipt is None:
            existing_receipt = await session.get(ScientificArtifactReceipt, artifact.artifact_id)
        if existing_receipt is None:
            session.add(_build_design_field_receipt(artifact, design_id, field_name))
        else:
            _validate_design_receipt(existing_receipt, artifact)
        setattr(design, field_name, artifact_row_reference(artifact.reference(), 0, value_field="payload_json"))
        installed.append(artifact)
    return installed


def externalize_design_fields_sync(
    session: Session,
    design: Any,
    *,
    fields: Iterable[str] | None = None,
) -> list[Any]:
    """Synchronous variant of externalize_design_fields for sync SQLAlchemy sessions."""
    pending = _iter_design_fields_to_externalize(design, fields)
    if not pending:
        return []
    if not getattr(design, "id", None):
        design.id = str(uuid.uuid4())
    design_id = str(design.id)
    schema = _design_field_schema()
    tx_id = _transaction_id_sync(session)
    installed: list[Any] = []
    for field_name, encoded, source_sha in pending:
        row = {"row_index": 0, "design_id": design_id, "field_name": field_name, "payload_json": encoded}
        artifact = install_parquet_rows(
            root=artifact_root(),
            owner_kind="design_field",
            owner_id=f"{design_id}:{field_name}",
            role="payload",
            schema_id="bms.design.field.v1",
            schema_version=1,
            source_sha256=source_sha,
            rows=[row],
            schema=schema,
            transaction_id=tx_id,
        )
        _track_new_artifact_sync(session, artifact)
        existing_receipt = next(
            (
                value
                for value in session.new
                if isinstance(value, ScientificArtifactReceipt)
                and value.artifact_id == artifact.artifact_id
            ),
            None,
        )
        if existing_receipt is None:
            existing_receipt = session.get(ScientificArtifactReceipt, artifact.artifact_id)
        if existing_receipt is None:
            session.add(_build_design_field_receipt(artifact, design_id, field_name))
        else:
            _validate_design_receipt(existing_receipt, artifact)
        setattr(design, field_name, artifact_row_reference(artifact.reference(), 0, value_field="payload_json"))
        installed.append(artifact)
    return installed
