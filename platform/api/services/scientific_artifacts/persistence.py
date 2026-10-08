"""SQLAlchemy publication helpers for immutable JSON-to-Parquet migration."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

import pyarrow as pa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from database import ScientificArtifactReceipt
from .contracts import canonical_sha256, envelope_rows
from .writer import artifact_root, guarded_delete_new_artifact, install_parquet_rows


_ROLLBACK_ARTIFACTS_KEY = "scientific_artifacts_newly_installed"


@event.listens_for(Session, "after_commit")
def _forget_committed_artifacts(session: Session) -> None:
    session.info.pop(_ROLLBACK_ARTIFACTS_KEY, None)


@event.listens_for(Session, "after_rollback")
def _cleanup_rolled_back_artifacts(session: Session) -> None:
    artifacts = session.info.pop(_ROLLBACK_ARTIFACTS_KEY, ())
    for artifact in reversed(artifacts):
        guarded_delete_new_artifact(artifact)


def _track_new_artifact(session: AsyncSession, artifact: Any) -> None:
    if artifact.newly_installed:
        session.sync_session.info.setdefault(_ROLLBACK_ARTIFACTS_KEY, []).append(
            artifact
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
) -> Any:
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
    )
    if installed_artifacts is not None:
        installed_artifacts.append(artifact)
    _track_new_artifact(session, artifact)
    await _add_receipt(session, artifact, source_sha256)
    return artifact


async def _add_receipt(session: AsyncSession, artifact: Any, source_sha256: str) -> None:
    existing = await session.get(ScientificArtifactReceipt, artifact.artifact_id)
    if existing is not None:
        if (
            existing.content_sha256 != artifact.content_sha256
            or existing.size_bytes != artifact.size_bytes
            or existing.relative_path != artifact.relative_path
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
            source_receipts_json={
                "source_sha256": source_sha256,
                "owner_kind": artifact.owner_kind,
                "owner_id": artifact.owner_id,
                "role": artifact.role,
            },
            created_at=datetime.utcnow(),
        )
    )
