"""SQLAlchemy publication helpers for immutable JSON-to-Parquet migration."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping
import uuid

import pyarrow as pa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from database import ScientificArtifactReceipt
from .contracts import canonical_sha256, envelope_rows
from .writer import (
    artifact_root,
    finalize_artifact_publication,
    guarded_delete_new_artifact,
    install_parquet_rows,
)


_TRANSACTION_ARTIFACTS_KEY = "scientific_artifacts_transaction_publications"
_TRANSACTION_ID_KEY = "scientific_artifacts_transaction_id"


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


def _track_new_artifact(session: AsyncSession, artifact: Any) -> None:
    if artifact.transaction_id is not None:
        sync = session.sync_session
        transaction = sync.get_nested_transaction() or sync.get_transaction()
        sync.info.setdefault(_TRANSACTION_ARTIFACTS_KEY, {}).setdefault(
            transaction, []
        ).append(artifact)


def _transaction_id(session: AsyncSession) -> str:
    sync = session.sync_session
    transaction = sync.get_nested_transaction() or sync.get_transaction() or sync.begin()
    return sync.info.setdefault(_TRANSACTION_ID_KEY, {}).setdefault(
        transaction, uuid.uuid4().hex
    )


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
