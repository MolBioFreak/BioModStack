"""Controlled operations for workspace export, backup, reconciliation, and analytics."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_migrations import LATEST_MIGRATION_VERSION, attest_schema, health, run_all
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentArtifact,
    ExperimentArtifactBlob,
    ExperimentAuditEvent,
    ExperimentDatasetRevisionMember,
    ExperimentDispatchOutbox,
    ExperimentDomainAdapterReceipt,
    ExperimentDomainConnectorCommand,
    ExperimentDomainConnectorConflict,
    ExperimentDomainConnectorInbox,
    ExperimentDomainConnectorStream,
    ExperimentExternalEntityReceipt,
    ExperimentIdempotencyClaim,
    ExperimentLineageEdge,
    ExperimentLaunchContext,
    ExperimentLogChunk,
    ExperimentLogStream,
    ExperimentOperationalReceipt,
    ExperimentResource,
    ExperimentResourceAdmission,
    ExperimentResourceAdmissionPolicy,
    ExperimentResearchRecord,
    ExperimentRevision,
    ExperimentRevisionEdge,
    ExperimentRunAttempt,
    ExperimentRunControlCommand,
    ExperimentRunEvent,
    ExperimentRunGroup,
    ExperimentRunGroupPreparation,
    ExperimentValidation,
    ExperimentWorkflowDraft,
    ExperimentWorkflowPlanAuthority,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRevisionEdge,
    ExperimentWorkflowRevisionNode,
    ExperimentWorkflowRun,
)
from experiment_services import ExperimentServiceError, IdempotencyConflict, new_id, now, sha256_text
from paths import get_experiment_db_path
from services.ngs_molbio_runtime_status import (
    NgsMolBioRuntimeAuthorityError,
    runtime_implementation_record,
)


class ExperimentOperationError(ExperimentServiceError):
    """A controlled workspace operation failed closed."""


class ExportNotFound(ExperimentOperationError):
    pass


class BackupNotFound(ExperimentOperationError):
    pass


_WORKSPACE_TABLES: tuple[tuple[str, type[Any], str], ...] = (
    ("resources", ExperimentResource, "resource"),
    ("aggregate_heads", ExperimentAggregateHead, "workspace"),
    ("revisions", ExperimentRevision, "resource"),
    ("revision_edges", ExperimentRevisionEdge, "revision"),
    ("workflow_drafts", ExperimentWorkflowDraft, "resource"),
    ("workflow_plan_authority", ExperimentWorkflowPlanAuthority, "workspace"),
    ("dataset_revision_members", ExperimentDatasetRevisionMember, "revision"),
    ("workflow_preparations", ExperimentWorkflowPreparation, "workspace"),
    ("run_groups", ExperimentRunGroup, "workspace"),
    ("run_control_commands", ExperimentRunControlCommand, "workspace"),
    ("run_group_preparations", ExperimentRunGroupPreparation, "run_group"),
    ("workflow_runs", ExperimentWorkflowRun, "workspace"),
    ("run_attempts", ExperimentRunAttempt, "workspace"),
    ("dispatch_outbox", ExperimentDispatchOutbox, "workspace"),
    ("run_events", ExperimentRunEvent, "workspace"),
    ("idempotency_claims", ExperimentIdempotencyClaim, "result_resource"),
    ("external_entity_receipts", ExperimentExternalEntityReceipt, "workspace"),
    ("lineage_edges", ExperimentLineageEdge, "workspace"),
    ("workflow_revision_nodes", ExperimentWorkflowRevisionNode, "revision"),
    ("workflow_revision_edges", ExperimentWorkflowRevisionEdge, "revision"),
    ("artifacts", ExperimentArtifact, "resource"),
    ("validations", ExperimentValidation, "resource"),
    ("log_streams", ExperimentLogStream, "resource"),
    ("log_chunks", ExperimentLogChunk, "none"),
    ("audit_events", ExperimentAuditEvent, "workspace"),
    ("research_records", ExperimentResearchRecord, "workspace"),
    ("domain_adapter_receipts", ExperimentDomainAdapterReceipt, "workspace"),
    ("domain_connector_commands", ExperimentDomainConnectorCommand, "project"),
    ("domain_connector_streams", ExperimentDomainConnectorStream, "domain_experiment"),
    ("domain_connector_inbox", ExperimentDomainConnectorInbox, "domain_experiment"),
    ("domain_connector_conflicts", ExperimentDomainConnectorConflict, "domain_experiment"),
    ("resource_admissions", ExperimentResourceAdmission, "workspace"),
    ("resource_admission_policy", ExperimentResourceAdmissionPolicy, "all"),
    ("operational_receipts", ExperimentOperationalReceipt, "workspace"),
    ("launch_contexts", ExperimentLaunchContext, "project"),
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _runtime_source_identity() -> tuple[str, str]:
    """Resolve exact backup/restoration identity from package-local successor authority."""
    try:
        runtime = runtime_implementation_record()
    except NgsMolBioRuntimeAuthorityError as exc:
        raise ExperimentOperationError(
            "package-local NGS/MolBio runtime authority is required for provenance-bound backup"
        ) from exc
    revision = runtime.get("successor_source_commit")
    tree = runtime.get("successor_source_tree")
    if type(revision) is not str or type(tree) is not str:
        raise ExperimentOperationError("package-local runtime source identity is incomplete")
    return revision, tree


def _consistent_database_digest(source: Path) -> tuple[str, int]:
    descriptor, temporary_name = tempfile.mkstemp(prefix="bms-experiment-snapshot-", suffix=".db")
    os.close(descriptor)
    temporary = Path(temporary_name)
    source_connection = sqlite3.connect(str(source), timeout=30)
    target_connection = sqlite3.connect(str(temporary), timeout=30)
    try:
        source_connection.backup(target_connection)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    try:
        return _sha256_file(temporary)
    finally:
        temporary.unlink(missing_ok=True)


def _sqlite_object_counts(database_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(str(database_path), timeout=30)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables}
    finally:
        connection.close()


def _sqlite_table_content_digests(database_path: Path) -> dict[str, str]:
    connection = sqlite3.connect(str(database_path), timeout=30)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        result: dict[str, str] = {}
        for table in tables:
            quoted_table = table.replace('"', '""')
            table_info = list(connection.execute(f'PRAGMA table_info("{quoted_table}")'))
            columns = [str(row[1]) for row in table_info]
            primary_key_columns = [
                str(row[1]) for row in sorted(table_info, key=lambda item: int(item[5])) if int(row[5]) > 0
            ]
            ordering_columns = primary_key_columns or columns
            order_by = ", ".join(f'"{column.replace(chr(34), chr(34) * 2)}"' for column in ordering_columns)
            digest = hashlib.sha256()
            digest.update((_canonical(columns) + "\n").encode("utf-8"))
            cursor = connection.execute(f'SELECT * FROM "{quoted_table}" ORDER BY {order_by}')
            for row in cursor:
                normalized = [
                    {"blob_hex": value.hex()} if isinstance(value, bytes) else value
                    for value in row
                ]
                digest.update((_canonical(normalized) + "\n").encode("utf-8"))
            result[table] = digest.hexdigest()
        return result
    finally:
        connection.close()


def _controlled_root(env_name: str, default_name: str) -> Path:
    configured = os.getenv(env_name)
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        root = get_experiment_db_path().parent / default_name
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_child(root: Path, name: str) -> Path:
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        raise ExperimentOperationError("operation path escapes the server-owned root")
    return candidate


def _backup_paths(backup_id: str) -> tuple[Path, Path]:
    root = _controlled_root("BMS_EXPERIMENT_BACKUP_ROOT", "experiment-backups")
    if not backup_id or Path(backup_id).name != backup_id or backup_id != Path(backup_id).stem:
        raise BackupNotFound("invalid backup identity")
    return root / f"{backup_id}.db", root / f"{backup_id}.json"


def create_online_backup() -> dict[str, Any]:
    """Create an SQLite online backup and a hash-bound metadata receipt."""
    source_revision, source_tree = _runtime_source_identity()
    source = get_experiment_db_path()
    if not source.exists():
        raise ExperimentOperationError("global experiment database does not exist")
    backup_id = f"exp-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex[:12]}"
    target, metadata_path = _backup_paths(backup_id)
    source_connection = sqlite3.connect(str(source), timeout=30)
    target_connection = sqlite3.connect(str(target), timeout=30)
    try:
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA journal_mode=DELETE")
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    source_health = health(target)
    migration_info = source_health.get("migration")
    schema_version = int(migration_info.get("version") or 0) if isinstance(migration_info, dict) else 0
    object_counts = _sqlite_object_counts(target)
    table_content_sha256 = _sqlite_table_content_digests(target)
    artifact_manifest_snapshot = _artifact_snapshot(target)
    database_sha256, size_bytes = _sha256_file(target)
    metadata = {
        "schema": "bms.experiment.backup.v1",
        "schema_version": schema_version,
        "source_revision": source_revision,
        "source_tree": source_tree,
        "backup_id": backup_id,
        "database_sha256": database_sha256,
        "size_bytes": size_bytes,
        "object_counts": object_counts,
        "table_content_sha256": table_content_sha256,
        "created_at": now(),
        "source_health": source_health,
        "artifact_manifest_snapshot": artifact_manifest_snapshot,
    }
    metadata_path.write_text(_canonical(metadata) + "\n", encoding="utf-8")
    return {"backup_id": backup_id, **metadata}


def _artifact_snapshot(database_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(str(database_path), timeout=30)
    try:
        rows = connection.execute(
            "SELECT sha256, size_bytes, media_type, storage_key, state, verified_at "
            "FROM artifact_blobs ORDER BY sha256"
        ).fetchall()
        return {
            "count": len(rows),
            "blobs": [
                {
                    "sha256": row[0],
                    "size_bytes": row[1],
                    "media_type": row[2],
                    "storage_key": row[3],
                    "state": row[4],
                    "verified_at": row[5],
                }
                for row in rows
            ],
        }
    finally:
        connection.close()


def verify_backup(
    backup_id: str,
    *,
    verifier_id: str,
    creation_receipt: Mapping[str, Any],
    creation_receipt_sha256: str,
) -> dict[str, Any]:
    """Verify a backup in an isolated SQLite connection without activating it."""
    if not verifier_id.strip():
        raise ExperimentOperationError("authenticated backup verifier identity is required")
    source_revision, source_tree = _runtime_source_identity()
    database_path, metadata_path = _backup_paths(backup_id)
    if not database_path.exists() or not metadata_path.exists():
        raise BackupNotFound(f"backup not found: {backup_id}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if (
        type(creation_receipt) is not dict
        or creation_receipt.get("backup_id") != backup_id
        or creation_receipt != metadata
        or type(creation_receipt_sha256) is not str
        or len(creation_receipt_sha256) != 64
        or any(char not in "0123456789abcdef" for char in creation_receipt_sha256)
    ):
        raise ExperimentOperationError("backup creation receipt does not bind the requested immutable backup")
    actual_sha256, size_bytes = _sha256_file(database_path)
    if actual_sha256 != metadata.get("database_sha256") or size_bytes != metadata.get("size_bytes"):
        raise ExperimentOperationError("backup metadata does not match backup bytes")
    connection = sqlite3.connect(str(database_path), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_key_errors = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        schema = attest_schema(connection)
        migration_rows = connection.execute(
            "SELECT version, name, checksum, description, applied_at "
            "FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        actual_counts = _sqlite_object_counts(database_path)
        actual_table_content_sha256 = _sqlite_table_content_digests(database_path)
        provenance_valid = (
            metadata.get("schema") == "bms.experiment.backup.v1"
            and metadata.get("schema_version") == LATEST_MIGRATION_VERSION
            and metadata.get("source_revision") == source_revision
            and metadata.get("source_tree") == source_tree
            and metadata.get("object_counts") == actual_counts
            and metadata.get("table_content_sha256") == actual_table_content_sha256
        )
        result = {
            "schema": "bms.experiment.backup-verification.v1",
            "backup_id": backup_id,
            "creation_receipt_sha256": creation_receipt_sha256,
            "source_revision": source_revision,
            "source_tree": source_tree,
            "verifier_id": verifier_id,
            "verification_authority": "authenticated_operator",
            "database_sha256": actual_sha256,
            "size_bytes": size_bytes,
            "quick_check": quick_check,
            "foreign_key_errors": foreign_key_errors,
            "attestation": schema,
            "migration_ledger": [list(row) for row in migration_rows],
            "object_counts": actual_counts,
            "table_content_sha256": actual_table_content_sha256,
            "provenance_valid": provenance_valid,
            "verified": quick_check == "ok" and not foreign_key_errors and bool(schema["ok"]) and provenance_valid,
            "outcome": "pass" if quick_check == "ok" and not foreign_key_errors and bool(schema["ok"]) and provenance_valid else "fail",
            "verified_at": now(),
        }
    finally:
        connection.close()
    if not result["verified"]:
        raise ExperimentOperationError(f"backup verification failed: {result}")
    return result


def restore_backup_to_staging(
    backup_id: str,
    *,
    verifier_id: str,
    creation_receipt: Mapping[str, Any],
    creation_receipt_sha256: str,
) -> dict[str, Any]:
    """Materialize and verify an isolated restore drill without activating it."""
    if not verifier_id.strip():
        raise ExperimentOperationError("authenticated restoration verifier identity is required")
    verified_source = verify_backup(
        backup_id,
        verifier_id=verifier_id,
        creation_receipt=creation_receipt,
        creation_receipt_sha256=creation_receipt_sha256,
    )
    source_revision = str(verified_source["source_revision"])
    source_tree = str(verified_source["source_tree"])
    source_database, _source_metadata = _backup_paths(backup_id)
    root = _controlled_root("BMS_EXPERIMENT_RESTORE_ROOT", "experiment-restores")
    restore_id = f"restore-{backup_id}-{uuid.uuid4().hex[:12]}"
    target = _safe_child(root, f"{restore_id}.db")
    metadata_path = _safe_child(root, f"{restore_id}.json")
    source_connection = sqlite3.connect(str(source_database), timeout=30)
    target_connection = sqlite3.connect(str(target), timeout=30)
    try:
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA journal_mode=DELETE")
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()
    target_sha256, target_size = _sha256_file(target)
    connection = sqlite3.connect(str(target), timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_key_errors = [list(row) for row in connection.execute("PRAGMA foreign_key_check")]
        attestation = attest_schema(connection)
    finally:
        connection.close()
    object_counts = _sqlite_object_counts(target)
    table_content_sha256 = _sqlite_table_content_digests(target)
    verified = (
        quick_check == "ok"
        and not foreign_key_errors
        and bool(attestation.get("ok"))
        and object_counts == verified_source["object_counts"]
        and table_content_sha256 == verified_source["table_content_sha256"]
    )
    result = {
        "schema": "bms.experiment.restoration.v1",
        "restore_id": restore_id,
        "backup_id": backup_id,
        "backup_creation_receipt_sha256": creation_receipt_sha256,
        "source_backup_sha256": verified_source["database_sha256"],
        "restored_database_sha256": target_sha256,
        "restored_size_bytes": target_size,
        "quick_check": quick_check,
        "foreign_key_errors": foreign_key_errors,
        "attestation": attestation,
        "object_counts": object_counts,
        "table_content_sha256": table_content_sha256,
        "activated": False,
        "verified": verified,
        "outcome": "pass" if verified else "fail",
        "verifier_id": verifier_id,
        "verification_authority": "authenticated_operator",
        "source_revision": source_revision,
        "source_tree": source_tree,
        "verified_at": now(),
    }
    metadata_path.write_text(_canonical(result) + "\n", encoding="utf-8")
    if not verified:
        raise ExperimentOperationError(f"isolated restoration verification failed: {result}")
    return result


def _export_paths(export_id: str) -> tuple[Path, Path]:
    root = _controlled_root("BMS_EXPERIMENT_EXPORT_ROOT", "experiment-exports")
    if not export_id or Path(export_id).name != export_id or export_id != Path(export_id).stem:
        raise ExportNotFound("invalid export identity")
    directory = _safe_child(root, export_id)
    return directory, directory / "manifest.json"


def _object_row(obj: Any) -> dict[str, Any]:
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}


async def _workspace_resource_ids(session: AsyncSession, workspace_id: str) -> set[str]:
    resources = (
        await session.execute(
            select(ExperimentResource).where(
                (ExperimentResource.id == workspace_id) | (ExperimentResource.workspace_id == workspace_id)
            )
        )
    ).scalars().all()
    ids = {resource.id for resource in resources}
    workspace = next((resource for resource in resources if resource.id == workspace_id), None)
    if workspace is None or workspace.kind != "workspace":
        raise ExportNotFound(f"workspace not found: {workspace_id}")
    return ids


async def _rows_for_scope(session: AsyncSession, model: type[Any], scope: str, resource_ids: set[str]) -> list[dict[str, Any]]:
    if scope == "none":
        return []
    if scope == "all":
        result = await session.execute(select(model))
    elif scope == "result_resource":
        result = await session.execute(select(model).where(model.result_resource_id.in_(resource_ids)))
    elif scope == "workspace":
        result = await session.execute(select(model).where(model.workspace_id.in_(resource_ids)))
    elif scope == "project":
        result = await session.execute(select(model).where(model.project_id.in_(resource_ids)))
    elif scope == "domain_experiment":
        result = await session.execute(select(model).where(model.domain_experiment_id.in_(resource_ids)))
    elif scope == "resource":
        key = "resource_id" if hasattr(model, "resource_id") else "id"
        result = await session.execute(select(model).where(getattr(model, key).in_(resource_ids)))
    elif scope == "revision":
        result = await session.execute(select(model).where(model.revision_id.in_(resource_ids)))
    elif scope == "run_group":
        result = await session.execute(select(model).where(model.run_group_id.in_(resource_ids)))
    else:
        raise ExperimentOperationError(f"unknown export scope: {scope}")
    return [_object_row(row) for row in result.scalars().all()]


async def build_workspace_export(session: AsyncSession, workspace_id: str) -> dict[str, Any]:
    """Write a deterministic metadata/artifact bundle for one workspace."""
    source_revision, source_tree = _runtime_source_identity()
    start_data_version = await session.scalar(text("PRAGMA data_version"))
    if not isinstance(start_data_version, int):
        raise ExperimentOperationError("workspace export data-version authority is unavailable")
    resource_ids = await _workspace_resource_ids(session, workspace_id)
    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name, model, scope in _WORKSPACE_TABLES:
        rows = await _rows_for_scope(session, model, scope, resource_ids)
        tables[table_name] = sorted(rows, key=_canonical)
    stream_ids = {row["resource_id"] for row in tables["log_streams"]}
    if stream_ids:
        log_chunks = (
            await session.execute(
                select(ExperimentLogChunk).where(ExperimentLogChunk.stream_id.in_(stream_ids))
            )
        ).scalars().all()
        tables["log_chunks"] = sorted((_object_row(row) for row in log_chunks), key=_canonical)
    artifact_rows = tables["artifacts"]
    blob_ids = {row["blob_sha256"] for row in artifact_rows}
    if blob_ids:
        blobs = (
            await session.execute(
                select(ExperimentArtifactBlob).where(ExperimentArtifactBlob.sha256.in_(blob_ids))
            )
        ).scalars().all()
        tables["artifact_blobs"] = sorted((_object_row(row) for row in blobs), key=_canonical)
    else:
        tables["artifact_blobs"] = []
    export_id = f"workspace-{workspace_id}-{uuid.uuid4().hex[:12]}"
    directory, manifest_path = _export_paths(export_id)
    artifact_root = _controlled_root("BMS_EXPERIMENT_ARTIFACT_ROOT", "experiment-artifacts")
    artifact_entries: list[dict[str, Any]] = []
    for blob in tables["artifact_blobs"]:
        if blob["state"] != "present":
            artifact_entries.append({"sha256": blob["sha256"], "state": blob["state"], "available": False})
            continue
        source = _safe_child(artifact_root, str(blob["storage_key"]))
        if not source.is_file():
            artifact_entries.append({"sha256": blob["sha256"], "state": "unavailable", "available": False})
            continue
        digest, size = _sha256_file(source)
        if digest != blob["sha256"] or size != blob["size_bytes"]:
            raise ExperimentOperationError(f"artifact bytes do not match catalog: {blob['sha256']}")
        target = directory / "artifacts" / blob["sha256"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        artifact_entries.append({"sha256": blob["sha256"], "size_bytes": size, "relative_path": f"artifacts/{blob['sha256']}", "available": True})
    database_sha256, database_size_bytes = _consistent_database_digest(get_experiment_db_path())
    end_data_version = await session.scalar(text("PRAGMA data_version"))
    if end_data_version != start_data_version:
        if directory.exists():
            shutil.rmtree(directory)
        raise ExperimentOperationError(
            "experiment source changed while the workspace export was captured"
        )
    object_counts = {table_name: len(rows) for table_name, rows in sorted(tables.items())}
    manifest = {
        "schema": "bms.experiment.workspace-export.v1",
        "schema_version": LATEST_MIGRATION_VERSION,
        "source_revision": source_revision,
        "source_tree": source_tree,
        "database_sha256": database_sha256,
        "database_size_bytes": database_size_bytes,
        "object_counts": object_counts,
        "export_id": export_id,
        "workspace_id": workspace_id,
        "created_at": now(),
        "tables": tables,
        "artifacts": artifact_entries,
        "artifact_bytes_are_optional": True,
    }
    encoded = (_canonical(manifest) + "\n").encode("utf-8")
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(encoded)
    (directory / "manifest.sha256").write_text(_sha256_bytes(encoded) + "\n", encoding="ascii")
    return {
        "export_id": export_id,
        "workspace_id": workspace_id,
        "manifest_sha256": _sha256_bytes(encoded),
        "artifact_count": len(artifact_entries),
        "artifact_bytes_available": sum(1 for item in artifact_entries if item.get("available")),
        "relative_manifest": str(manifest_path.relative_to(directory.parent)),
    }


def verify_workspace_export(
    export_id: str,
    *,
    verifier_id: str,
    creation_receipt: Mapping[str, Any],
    creation_receipt_sha256: str,
) -> dict[str, Any]:
    if not verifier_id.strip():
        raise ExperimentOperationError("authenticated export verifier identity is required")
    source_revision, source_tree = _runtime_source_identity()
    directory, manifest_path = _export_paths(export_id)
    if not manifest_path.exists():
        raise ExportNotFound(f"export not found: {export_id}")
    encoded = manifest_path.read_bytes()
    manifest_digest = _sha256_bytes(encoded)
    recorded_digest = (directory / "manifest.sha256").read_text(encoding="ascii").strip()
    if recorded_digest != manifest_digest:
        raise ExperimentOperationError("workspace export manifest digest mismatch")
    manifest = json.loads(encoded)
    artifact_entries = manifest.get("artifacts")
    creation_authority = {
        "export_id": export_id,
        "workspace_id": manifest.get("workspace_id"),
        "manifest_sha256": manifest_digest,
        "artifact_count": len(artifact_entries) if isinstance(artifact_entries, list) else -1,
        "artifact_bytes_available": (
            sum(1 for item in artifact_entries if isinstance(item, dict) and item.get("available"))
            if isinstance(artifact_entries, list)
            else -1
        ),
        "relative_manifest": str(manifest_path.relative_to(directory.parent)),
    }
    if (
        type(creation_receipt) is not dict
        or creation_receipt != creation_authority
        or manifest.get("export_id") != export_id
        or type(creation_receipt_sha256) is not str
        or len(creation_receipt_sha256) != 64
        or any(char not in "0123456789abcdef" for char in creation_receipt_sha256)
    ):
        raise ExperimentOperationError("export creation receipt does not bind the requested immutable export")
    artifact_results: list[dict[str, Any]] = []
    for entry in manifest.get("artifacts", []):
        relative = entry.get("relative_path")
        if not relative:
            artifact_results.append({"sha256": entry.get("sha256"), "available": False, "verified": not entry.get("available")})
            continue
        artifact = _safe_child(directory, str(relative))
        digest, size = _sha256_file(artifact) if artifact.is_file() else (None, None)
        artifact_results.append({"sha256": entry.get("sha256"), "actual_sha256": digest, "size_bytes": size, "verified": digest == entry.get("sha256") and size == entry.get("size_bytes")})
    tables = manifest.get("tables")
    actual_counts = (
        {table_name: len(rows) for table_name, rows in sorted(tables.items())}
        if isinstance(tables, dict) and all(isinstance(rows, list) for rows in tables.values())
        else None
    )
    database_digest = manifest.get("database_sha256")
    provenance_valid = (
        manifest.get("schema") == "bms.experiment.workspace-export.v1"
        and manifest.get("schema_version") == LATEST_MIGRATION_VERSION
        and manifest.get("source_revision") == source_revision
        and manifest.get("source_tree") == source_tree
        and isinstance(database_digest, str)
        and len(database_digest) == 64
        and all(char in "0123456789abcdef" for char in database_digest)
        and isinstance(manifest.get("database_size_bytes"), int)
        and manifest.get("object_counts") == actual_counts
    )
    verified = all(item["verified"] for item in artifact_results) and provenance_valid
    result = {
        "schema": "bms.experiment.workspace-export-verification.v1",
        "export_id": export_id,
        "creation_receipt_sha256": creation_receipt_sha256,
        "workspace_id": manifest.get("workspace_id"),
        "source_revision": manifest.get("source_revision"),
        "source_tree": manifest.get("source_tree"),
        "manifest_sha256": manifest_digest,
        "artifact_count": len(artifact_results),
        "verified_artifact_count": sum(1 for item in artifact_results if item["verified"]),
        "artifact_results_sha256": _sha256_bytes(_canonical(artifact_results).encode("utf-8")),
        "object_counts": actual_counts,
        "provenance_valid": provenance_valid,
        "verified": verified,
        "outcome": "pass" if verified else "fail",
        "verifier_id": verifier_id,
        "verification_authority": "authenticated_operator",
        "verified_at": now(),
    }
    if not verified:
        raise ExperimentOperationError(f"workspace export verification failed: {result}")
    return result


async def workspace_analytics(session: AsyncSession, workspace_id: str, limit: int = 100) -> dict[str, Any]:
    """Return bounded Plotly-ready dimensions from global authority only."""
    if limit < 1 or limit > 1000:
        raise ExperimentOperationError("analytics limit must be between 1 and 1000")
    await _workspace_resource_ids(session, workspace_id)
    resources = (
        await session.execute(
            select(ExperimentResource.kind, func.count(ExperimentResource.id))
            .where((ExperimentResource.id == workspace_id) | (ExperimentResource.workspace_id == workspace_id))
            .group_by(ExperimentResource.kind)
            .limit(limit)
        )
    ).all()
    runs = (
        await session.execute(
            select(ExperimentWorkflowRun.state, func.count(ExperimentWorkflowRun.resource_id))
            .where(ExperimentWorkflowRun.workspace_id == workspace_id)
            .group_by(ExperimentWorkflowRun.state)
            .limit(limit)
        )
    ).all()
    attempts = (
        await session.execute(
            select(ExperimentRunAttempt.state, func.count(ExperimentRunAttempt.resource_id))
            .where(ExperimentRunAttempt.workspace_id == workspace_id)
            .group_by(ExperimentRunAttempt.state)
            .limit(limit)
        )
    ).all()
    validations = (
        await session.execute(
            select(ExperimentValidation.outcome, func.count(ExperimentValidation.resource_id))
            .join(ExperimentResource, ExperimentResource.id == ExperimentValidation.resource_id)
            .where((ExperimentResource.id == workspace_id) | (ExperimentResource.workspace_id == workspace_id))
            .group_by(ExperimentValidation.outcome)
            .limit(limit)
        )
    ).all()
    points: list[dict[str, Any]] = []
    for kind, count in resources:
        points.append({"dimension": "resource_kind", "key": kind, "value": int(count)})
    for state, count in runs:
        points.append({"dimension": "workflow_run_state", "key": state, "value": int(count)})
    for state, count in attempts:
        points.append({"dimension": "run_attempt_state", "key": state, "value": int(count)})
    for outcome, count in validations:
        points.append({"dimension": "validation_outcome", "key": outcome, "value": int(count)})
    return {
        "schema": "bms.experiment.analytics.v1",
        "workspace_id": workspace_id,
        "bounded": True,
        "metric_registry": [
            {"name": "resource_count", "type": "integer", "unit": "count", "source": "global.resources"},
            {"name": "workflow_run_count", "type": "integer", "unit": "count", "source": "global.workflow_runs"},
            {"name": "run_attempt_count", "type": "integer", "unit": "count", "source": "global.run_attempts"},
            {"name": "validation_count", "type": "integer", "unit": "count", "source": "global.validations"},
        ],
        "points": points[:limit],
    }


async def register_external_entity_receipt(
    session: AsyncSession,
    *,
    workspace_id: str,
    store_id: str,
    entity_kind: str,
    entity_id: str,
    generation_or_revision: str,
    content_digest: str,
    availability: str = "available",
    acknowledgement: dict[str, Any] | None = None,
    verification_authority: str | None = None,
) -> ExperimentExternalEntityReceipt:
    """Register an immutable bridge; only server-verifier receipts are evidence eligible."""
    if (
        len(content_digest) != 64
        or content_digest != content_digest.lower()
        or any(char not in "0123456789abcdef" for char in content_digest)
    ):
        raise ExperimentOperationError("external receipt content_digest must be a lowercase SHA-256")
    if availability not in {"unknown", "available", "unavailable"}:
        raise ExperimentOperationError("external receipt availability is invalid")
    if not store_id or not entity_kind or not entity_id or not generation_or_revision:
        raise ExperimentOperationError("external receipt identity is incomplete")
    claimed_acknowledgement = acknowledgement or {}
    claimed_contract_digest = claimed_acknowledgement.get("contract_digest")
    if claimed_contract_digest is not None and (
        not isinstance(claimed_contract_digest, str)
        or len(claimed_contract_digest) != 64
        or claimed_contract_digest != claimed_contract_digest.lower()
        or any(char not in "0123456789abcdef" for char in claimed_contract_digest)
    ):
        raise ExperimentOperationError("external receipt contract_digest must be a lowercase SHA-256")
    if verification_authority is not None:
        if claimed_contract_digest is None:
            raise ExperimentOperationError(
                "server-verified receipt acknowledgement requires contract_digest"
            )
        if claimed_acknowledgement.get("availability") != availability:
            raise ExperimentOperationError(
                "server-verified receipt acknowledgement availability disagrees with persisted availability"
            )
        if (
            claimed_acknowledgement.get("schema") != "bms.global.external-entity-receipt.v1"
            or claimed_acknowledgement.get("verifier_id") != verification_authority
            or claimed_acknowledgement.get("store_id") != store_id
            or claimed_acknowledgement.get("entity_kind") != entity_kind
            or claimed_acknowledgement.get("entity_id") != entity_id
            or str(claimed_acknowledgement.get("entity_revision_id")) != generation_or_revision
            or claimed_acknowledgement.get("content_digest") != content_digest
            or not claimed_acknowledgement.get("source_build_revision")
            or not claimed_acknowledgement.get("verified_at")
            or not claimed_acknowledgement.get("reopen_uri")
        ):
            raise ExperimentOperationError("server-verified receipt acknowledgement is invalid")
        effective_store_id = store_id
        effective_availability = availability
        effective_verification_authority = verification_authority
        effective_acknowledgement = claimed_acknowledgement
    else:
        effective_store_id = f"unverified:{store_id}"
        effective_availability = "unavailable"
        effective_verification_authority = "caller_unverified"
        effective_acknowledgement = {
            "schema": "bms.global.unverified-external-reference.v1",
            "claimed_store_id": store_id,
            "claimed_entity_kind": entity_kind,
            "claimed_entity_id": entity_id,
            "claimed_generation_or_revision": generation_or_revision,
            "claimed_content_digest": content_digest,
            "claimed_contract_digest": claimed_contract_digest,
            "caller_acknowledgement": claimed_acknowledgement,
            "reason": "caller assertions are not evidence-eligible",
        }
    effective_contract_digest = str(
        effective_acknowledgement.get("contract_digest")
        or effective_acknowledgement.get("claimed_contract_digest")
        or ""
    )
    workspace = await session.get(ExperimentResource, workspace_id)
    if workspace is None or workspace.kind != "workspace":
        raise ExperimentOperationError(f"workspace not found: {workspace_id}")
    existing = (
        await session.execute(
            select(ExperimentExternalEntityReceipt).where(
                ExperimentExternalEntityReceipt.workspace_id == workspace_id,
                ExperimentExternalEntityReceipt.store_id == effective_store_id,
                ExperimentExternalEntityReceipt.entity_kind == entity_kind,
                ExperimentExternalEntityReceipt.entity_id == entity_id,
                ExperimentExternalEntityReceipt.generation_or_revision == generation_or_revision,
                ExperimentExternalEntityReceipt.content_digest == content_digest,
                ExperimentExternalEntityReceipt.availability == effective_availability,
                func.coalesce(
                    func.json_extract(
                        ExperimentExternalEntityReceipt.acknowledgement_json,
                        "$.contract_digest",
                    ),
                    func.json_extract(
                        ExperimentExternalEntityReceipt.acknowledgement_json,
                        "$.claimed_contract_digest",
                    ),
                    "",
                )
                == effective_contract_digest,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.verification_authority != effective_verification_authority:
            raise IdempotencyConflict("external entity identity already exists under another verification authority")
        if existing.content_digest != content_digest or existing.generation_or_revision != generation_or_revision:
            raise IdempotencyConflict("external entity identity already exists with different content")
        return existing
    resource_id = new_id("external-receipt")
    session.add(ExperimentResource(id=resource_id, kind="external_entity_receipt", workspace_id=workspace_id, lifecycle_owner_id=workspace_id, created_at=now()))
    await session.flush()
    receipt = ExperimentExternalEntityReceipt(
        id=resource_id,
        workspace_id=workspace_id,
        resource_id=resource_id,
        store_id=effective_store_id,
        entity_kind=entity_kind,
        entity_id=entity_id,
        generation_or_revision=generation_or_revision,
        content_digest=content_digest,
        availability=effective_availability,
        verification_authority=effective_verification_authority,
        acknowledgement_json=_canonical(effective_acknowledgement),
        created_at=now(),
    )
    session.add(receipt)
    session.add(ExperimentLineageEdge(id=new_id("owns"), workspace_id=workspace_id, source_resource_id=workspace_id, target_resource_id=resource_id, edge_mode="owns", edge_key="lifecycle-owner:external_entity_receipt", metadata_json="{}", created_at=now()))
    session.add(ExperimentAuditEvent(id=new_id("audit"), workspace_id=workspace_id, resource_id=resource_id, event_type="external_entity_receipt_registered", generation=0, payload_json=_canonical({"store_id": effective_store_id, "entity_kind": entity_kind, "entity_id": entity_id, "content_digest": content_digest, "server_verified": verification_authority is not None, "verification_authority": effective_verification_authority}), created_at=now()))
    await session.flush()
    return receipt


__all__ = [
    "BackupNotFound",
    "ExportNotFound",
    "ExperimentOperationError",
    "build_workspace_export",
    "create_online_backup",
    "register_external_entity_receipt",
    "verify_backup",
    "verify_workspace_export",
    "workspace_analytics",
]
