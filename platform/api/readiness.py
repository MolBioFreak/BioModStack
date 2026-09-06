from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from sqlalchemy import text

from database import engine
from migrations.add_frustrampnn_reviews import (
    REQUIRED_COLUMNS,
    REQUIRED_FOREIGN_KEYS,
    REQUIRED_INDEX_COLUMNS,
    REQUIRED_NOT_NULL,
    REQUIRED_PRIMARY_KEYS,
    REQUIRED_TYPES,
    REQUIRED_UNIQUE_COLUMNS,
)
from migrations.runner import MIGRATIONS
from runtime_policy import core_runtime_mode_enabled, workflow_launches_allowed
from services.workflow_adapter import workflow_adapter_base_url
from services.restriction_catalog import catalog_authority
from services.restriction_products import product_authority
from telemetry_store import (
    TELEMETRY_FRESHNESS_STALE_AFTER_MS,
    TelemetryStore,
    telemetry_db_path,
)


async def core_database_readiness() -> tuple[bool, str]:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True, "ready"
    except Exception as exc:  # noqa: BLE001 - readiness must report degradation, not crash.
        return False, _failure_status(exc)


async def core_migration_readiness() -> tuple[bool, str, dict[str, Any]]:
    expected = [(migration.version, migration.name) for migration in MIGRATIONS]
    expected_version, expected_name = expected[-1]
    metadata: dict[str, Any] = {
        "expected_version": expected_version,
        "expected_name": expected_name,
        "applied_version": None,
        "applied_name": None,
    }
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(text("SELECT version, name FROM schema_migrations ORDER BY version"))
            ).all()
            tables = {
                str(row[0])
                for row in (
                    await connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
                ).all()
            }
            indexes = {
                str(row[0])
                for row in (
                    await connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'index'"))
                ).all()
            }
            schema_errors: list[str] = []
            for table, required_columns in REQUIRED_COLUMNS.items():
                info = (await connection.execute(text(f'PRAGMA table_info("{table}")'))).all()
                by_name = {str(row[1]): row for row in info}
                if set(by_name) != required_columns:
                    schema_errors.append(f"{table}:columns")
                    continue
                primary_key = tuple(name for _, name in sorted((int(row[5]), str(row[1])) for row in info if int(row[5]) > 0))
                if primary_key != REQUIRED_PRIMARY_KEYS[table]:
                    schema_errors.append(f"{table}:primary_key")
                if any(str(by_name[column][2]).upper() != declared_type for column, declared_type in REQUIRED_TYPES[table].items()):
                    schema_errors.append(f"{table}:types")
                if any(int(by_name[column][3]) != 1 for column in REQUIRED_NOT_NULL[table]):
                    schema_errors.append(f"{table}:not_null")
            for index, expected_columns in REQUIRED_INDEX_COLUMNS.items():
                actual = tuple(str(row[2]) for row in (await connection.execute(text(f'PRAGMA index_info("{index}")'))).all())
                if actual != expected_columns:
                    schema_errors.append(f"{index}:columns")
            for table, expected_unique_columns in REQUIRED_UNIQUE_COLUMNS.items():
                unique_columns: set[tuple[str, ...]] = set()
                for row in (await connection.execute(text(f'PRAGMA index_list("{table}")'))).all():
                    if int(row[2]) == 1:
                        columns = (await connection.execute(text(f'PRAGMA index_info("{row[1]}")'))).all()
                        unique_columns.add(tuple(str(column[2]) for column in columns))
                if not expected_unique_columns.issubset(unique_columns):
                    schema_errors.append(f"{table}:unique")
            for table, expected_foreign_keys in REQUIRED_FOREIGN_KEYS.items():
                foreign_key_rows = (await connection.execute(text(f'PRAGMA foreign_key_list("{table}")'))).all()
                grouped: dict[int, list[Any]] = {}
                for row in foreign_key_rows:
                    grouped.setdefault(int(row[0]), []).append(row)
                actual_foreign_keys = {(tuple(str(row[3]) for row in sorted(group, key=lambda item: int(item[1]))), str(group[0][2]), tuple(str(row[4]) for row in sorted(group, key=lambda item: int(item[1]))), str(group[0][5]).upper(), str(group[0][6]).upper()) for group in grouped.values()}
                if actual_foreign_keys != expected_foreign_keys:
                    schema_errors.append(f"{table}:foreign_keys")
    except Exception as exc:  # noqa: BLE001 - readiness must report degradation, not crash.
        return False, _failure_status(exc), metadata
    applied = [(int(row[0]), str(row[1])) for row in rows]
    if applied:
        metadata["applied_version"], metadata["applied_name"] = applied[-1]
    if applied == expected:
        required_tables = {"frustrampnn_reviews", "frustrampnn_exports", "frustrampnn_review_artifacts"}
        required_indexes = {
            "ix_frustrampnn_reviews_parent_job_id", "ix_frustrampnn_reviews_owner_job",
            "ix_frustrampnn_reviews_created_at", "ix_frustrampnn_exports_owner_job",
            "ix_frustrampnn_review_artifacts_owner_review",
        }
        missing_objects = sorted((required_tables - tables) | (required_indexes - indexes))
        metadata["missing_schema_objects"] = missing_objects
        if missing_objects:
            return False, "schema_objects_missing", metadata
        metadata["physical_schema_errors"] = sorted(schema_errors)
        if schema_errors:
            return False, "physical_schema_invalid", metadata
        return True, "at_head", metadata
    if applied == expected[: len(applied)]:
        return False, "behind", metadata
    return False, "invalid_ledger", metadata


async def http_readiness(url: str) -> tuple[bool, str]:
    def _probe() -> tuple[bool, str]:
        try:
            with urllib_request.urlopen(url, timeout=0.75) as response:  # noqa: S310 - operator-configured local readiness URL.
                code = int(getattr(response, "status", 200))
            return (200 <= code < 400), f"http_{code}"
        except (OSError, urllib_error.URLError, ValueError) as exc:
            return False, _failure_status(exc)

    return await asyncio.to_thread(_probe)


async def telemetry_collection_readiness() -> tuple[bool, str, dict[str, Any]]:
    def _probe() -> tuple[bool, str, dict[str, Any]]:
        empty_metadata = {
            "latest_timestamp_ms": None,
            "age_ms": None,
            "stale_after_ms": TELEMETRY_FRESHNESS_STALE_AFTER_MS,
        }
        try:
            path = telemetry_db_path()
            if not path.is_file():
                return False, "unavailable", empty_metadata
            freshness = TelemetryStore(path).read_freshness(
                stale_after_ms=TELEMETRY_FRESHNESS_STALE_AFTER_MS,
            )
        except Exception as exc:  # noqa: BLE001 - readiness reports a closed degradation state.
            return False, _failure_status(exc), empty_metadata
        metadata = {
            "latest_timestamp_ms": freshness["latest_timestamp_ms"],
            "age_ms": freshness["age_ms"],
            "stale_after_ms": freshness["stale_after_ms"],
            "future_sample_count": freshness["future_sample_count"],
            "latest_future_timestamp_ms": freshness["latest_future_timestamp_ms"],
        }
        return bool(freshness["ready"]), str(freshness["status"]), metadata

    return await asyncio.to_thread(_probe)


def _failure_status(exc: BaseException) -> str:
    return f"unavailable:{exc.__class__.__name__}"


def _check(*, required: bool, ready: bool, status: str, **extra: Any) -> dict[str, Any]:
    return {"required": required, "ready": ready, "status": status, **extra}


def _restriction_digest_readiness_is_exact(candidate: object) -> bool:
    """Require the exact Phase 3 policy, migration, route, and guard authority."""

    try:
        import hashlib

        import rfc8785

        from molbio_migrations import restriction_digest_migration_attestation
        from services.restriction_digest import resource_policy_receipt

        if not isinstance(candidate, dict) or set(candidate) != {
            "required", "ready", "status", "resource_policy",
            "resource_policy_sha256", "migration", "routes",
        }:
            return False
        policy = resource_policy_receipt().model_dump(mode="json", by_alias=True)
        expected_routes = [
            "POST /api/molbio/restriction/digests/simulate",
            "POST /api/molbio/restriction/digests",
            "GET /api/molbio/restriction/digests/{operation_id}",
        ]
        return bool(
            candidate["required"] is True
            and candidate["ready"] is True
            and candidate["status"] == "ready"
            and candidate["resource_policy"] == policy
            and candidate["resource_policy_sha256"]
            == hashlib.sha256(rfc8785.dumps(policy)).hexdigest()
            and candidate["migration"] == restriction_digest_migration_attestation()
            and candidate["routes"] == expected_routes
        )
    except Exception:
        return False


async def collect_runtime_readiness(
    *,
    molbio: dict[str, Any],
    molbio_ngs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    container_mode = core_runtime_mode_enabled()
    mode = "container" if container_mode else "native"

    core_ready, core_status = await core_database_readiness()
    migration_ready, migration_status, migration_metadata = await core_migration_readiness()
    telemetry_ready, telemetry_status, telemetry_metadata = await telemetry_collection_readiness()
    molbio_ready = molbio.get("status") == "healthy" or molbio.get("ready") is True
    molbio_ngs_required = molbio_ngs is not None
    molbio_ngs = molbio_ngs or {}
    molbio_ngs_attestation = molbio_ngs.get("attestation")
    molbio_ngs_ready = bool(
        isinstance(molbio_ngs_attestation, dict)
        and molbio_ngs_attestation.get("ok") is True
        and molbio_ngs.get("migration")
    )

    adapter_url = workflow_adapter_base_url()
    adapter_required = container_mode or bool(adapter_url)
    if adapter_url:
        adapter_ready, adapter_status = await http_readiness(f"{adapter_url}/api/workflow-adapter/health")
    elif adapter_required:
        adapter_ready, adapter_status = False, "not_configured"
    else:
        adapter_ready, adapter_status = True, "not_required"

    frontend_url = os.getenv("BMS_FRONTEND_HEALTH_URL", "").strip()
    if frontend_url:
        frontend_ready, frontend_status = await http_readiness(frontend_url)
        frontend_required = True
    else:
        frontend_ready, frontend_status = True, "not_configured"
        frontend_required = False

    launch_allowed = workflow_launches_allowed() and adapter_ready
    restriction_catalog = catalog_authority.readiness()
    restriction_products = product_authority.readiness()
    restriction_digest = molbio.get("restriction_digest")
    restriction_digest_ready = _restriction_digest_readiness_is_exact(restriction_digest)
    checks = {
        "process_liveness": _check(required=True, ready=True, status="alive"),
        "core_database": _check(required=True, ready=core_ready, status=core_status),
        "telemetry_collection": _check(
            required=True,
            ready=telemetry_ready,
            status=telemetry_status,
            **telemetry_metadata,
        ),
        "core_schema_migrations": _check(
            required=True,
            ready=migration_ready,
            status=migration_status,
            **migration_metadata,
        ),
        "molbio_database": _check(
            required=True,
            ready=molbio_ready,
            status="ready" if molbio_ready else str(molbio.get("status", "unavailable")),
        ),
        "restriction_catalog": restriction_catalog,
        "restriction_products": restriction_products,
        "restriction_digest": _check(
            required=True,
            ready=restriction_digest_ready,
            status="ready" if restriction_digest_ready else "unavailable",
        ),
        "molbio_ngs_database": _check(
            required=molbio_ngs_required,
            ready=molbio_ngs_ready if molbio_ngs_required else True,
            status=("ready" if molbio_ngs_ready else "unavailable")
            if molbio_ngs_required
            else "not_supplied",
        ),
        "workflow_adapter": _check(
            required=adapter_required,
            ready=adapter_ready,
            status=adapter_status,
        ),
        "frontend": _check(required=frontend_required, ready=frontend_ready, status=frontend_status),
        "workflow_launch": {
            "required": True,
            "ready": launch_allowed,
            "allowed": launch_allowed,
            "status": "allowed" if launch_allowed else "blocked",
        },
    }
    overall_ready = all(
        bool(check["ready"])
        for check in checks.values()
        if bool(check.get("required", False))
    )
    return {"mode": mode, "ready": overall_ready, "checks": checks}
