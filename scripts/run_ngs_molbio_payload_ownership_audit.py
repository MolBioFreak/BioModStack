#!/usr/bin/env python3
"""Run and retain one governed NGS/MolBio payload-ownership audit.

The plan is explicit JSON. Runtime code never resolves Git state or discovers
payload locations. Every database, table, column, artifact root, active-job
predicate, bound, and release identity must be supplied by the release caller.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy.exc import SQLAlchemyError

_REPO_ROOT = Path(__file__).resolve().parents[1]
_API_ROOT = _REPO_ROOT / "platform" / "api"
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

from services.payload_ownership_audit import (  # type: ignore[import-not-found]  # noqa: E402
    ArtifactManifestTarget,
    PayloadOwnershipConfigurationError,
    PayloadOwnershipError,
    PayloadOwnershipScanPlan,
    ReleaseSourceIdentity,
    RetainedPayloadOwnershipAuditStore,
    SQLiteActiveJobCheck,
    SQLiteColumnTarget,
    run_and_retain_payload_ownership_scan,
)
from experiment_database import (  # type: ignore[import-not-found]  # noqa: E402
    create_experiment_engine,
    create_experiment_session_factory,
)
from experiment_services import ExperimentServiceError  # type: ignore[import-not-found]  # noqa: E402
from services.ngs_molbio_n5 import (  # type: ignore[import-not-found]  # noqa: E402
    persist_payload_audit_operational_receipt,
)


class PlanError(ValueError):
    pass


def _object(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise PlanError(f"{label} must be an object")
    return value


def _closed(value: Mapping[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise PlanError(f"{label} has unknown fields: {sorted(unknown)}")
    if missing:
        raise PlanError(f"{label} is missing fields: {sorted(missing)}")


def _path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{label} must be a non-empty path string")
    return Path(value).expanduser()


def _strings(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise PlanError(f"{label} must be a {'possibly empty ' if allow_empty else 'non-empty '}array")
    if any(not isinstance(item, str) or not item for item in value):
        raise PlanError(f"{label} must contain only non-empty strings")
    return tuple(value)


def _sqlite_target(raw: Any, index: int) -> SQLiteColumnTarget:
    value = _object(raw, f"sqlite_targets[{index}]")
    required = {
        "target_id", "database_id", "database_path", "store_or_root", "table",
        "key_columns", "identity_columns", "payload_column", "payload_class",
        "encoding", "location_role",
    }
    optional = {
        "page_size", "max_rows", "max_payload_bytes", "max_json_candidates",
        "source_digest_column", "additional_forbidden_json_keys",
    }
    _closed(value, required | optional, required, f"sqlite_targets[{index}]")
    kwargs = dict(value)
    kwargs["database_path"] = _path(value["database_path"], f"sqlite_targets[{index}].database_path")
    kwargs["key_columns"] = _strings(value["key_columns"], f"sqlite_targets[{index}].key_columns")
    kwargs["identity_columns"] = _strings(value["identity_columns"], f"sqlite_targets[{index}].identity_columns")
    kwargs["additional_forbidden_json_keys"] = _strings(
        value.get("additional_forbidden_json_keys", []),
        f"sqlite_targets[{index}].additional_forbidden_json_keys",
        allow_empty=True,
    )
    return SQLiteColumnTarget(**kwargs)


def _active_job_check(raw: Any, index: int) -> SQLiteActiveJobCheck:
    value = _object(raw, f"active_job_checks[{index}]")
    fields = {
        "check_id", "database_id", "database_path", "table", "key_column",
        "state_column", "active_states",
    }
    _closed(value, fields, fields, f"active_job_checks[{index}]")
    kwargs = dict(value)
    kwargs["database_path"] = _path(value["database_path"], f"active_job_checks[{index}].database_path")
    kwargs["active_states"] = _strings(value["active_states"], f"active_job_checks[{index}].active_states")
    return SQLiteActiveJobCheck(**kwargs)


def _artifact_target(raw: Any, index: int) -> ArtifactManifestTarget:
    value = _object(raw, f"artifact_targets[{index}]")
    required = {
        "target_id", "store_or_root", "root", "manifest_glob", "payload_class",
        "location_role", "entries_pointer", "identity_pointer",
    }
    optional = {
        "artifact_path_pointer", "inline_payload_pointer", "declared_sha256_pointer",
        "declared_size_pointer", "max_manifest_files", "max_manifest_bytes",
        "max_entries", "max_payload_bytes", "additional_forbidden_json_keys",
    }
    _closed(value, required | optional, required, f"artifact_targets[{index}]")
    kwargs = dict(value)
    kwargs["root"] = _path(value["root"], f"artifact_targets[{index}].root")
    kwargs["additional_forbidden_json_keys"] = _strings(
        value.get("additional_forbidden_json_keys", []),
        f"artifact_targets[{index}].additional_forbidden_json_keys",
        allow_empty=True,
    )
    return ArtifactManifestTarget(**kwargs)


def load_plan(path: Path) -> tuple[PayloadOwnershipScanPlan, Path, Path]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError(f"scan plan is unreadable: {path}") from exc
    value = _object(value, "scan plan")
    required = {
        "source_commit", "source_tree", "retained_store_path", "experiment_database_path", "sqlite_targets",
        "active_job_checks", "artifact_targets",
    }
    optional = {"snapshot_directory"}
    _closed(value, required | optional, required, "scan plan")
    if not isinstance(value["sqlite_targets"], list):
        raise PlanError("sqlite_targets must be an array")
    if not isinstance(value["active_job_checks"], list):
        raise PlanError("active_job_checks must be an array")
    if not isinstance(value["artifact_targets"], list):
        raise PlanError("artifact_targets must be an array")
    snapshot = value.get("snapshot_directory")
    plan = PayloadOwnershipScanPlan(
        release=ReleaseSourceIdentity(
            source_commit=str(value["source_commit"]),
            source_tree=str(value["source_tree"]),
        ),
        sqlite_targets=tuple(_sqlite_target(item, index) for index, item in enumerate(value["sqlite_targets"])),
        active_job_checks=tuple(_active_job_check(item, index) for index, item in enumerate(value["active_job_checks"])),
        artifact_targets=tuple(_artifact_target(item, index) for index, item in enumerate(value["artifact_targets"])),
        snapshot_directory=_path(snapshot, "snapshot_directory") if snapshot is not None else None,
    )
    return (
        plan,
        _path(value["retained_store_path"], "retained_store_path"),
        _path(value["experiment_database_path"], "experiment_database_path"),
    )


async def _persist_operational_receipt(
    experiment_database_path: Path,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    configured = experiment_database_path.absolute()
    if configured.is_symlink():
        raise PlanError("experiment_database_path cannot be a symlink")
    try:
        database_path = configured.resolve(strict=True)
    except OSError as exc:
        raise PlanError("experiment_database_path must name an existing migrated database") from exc
    if not database_path.is_file():
        raise PlanError("experiment_database_path must name a regular file")
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{database_path}")
    factory = create_experiment_session_factory(engine)
    try:
        async with factory() as session:
            row = await persist_payload_audit_operational_receipt(session, receipt)
            await session.commit()
            return {
                "receipt_id": row.receipt_id,
                "operation_kind": row.operation_kind,
                "native_identity": row.native_identity,
                "state": row.state,
                "receipt_sha256": row.receipt_sha256,
                "source_revision": row.source_revision,
                "occurred_at": row.occurred_at,
                "verified_at": row.verified_at,
            }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="Closed JSON scan-plan path")
    parser.add_argument(
        "--reconcile-audit-id",
        help="Publish the missing operational receipt for this exact retained audit without rescanning",
    )
    arguments = parser.parse_args()
    receipt: dict[str, Any] | None = None
    try:
        plan, retained_store, experiment_database = load_plan(arguments.plan)
        if arguments.reconcile_audit_id:
            receipt = RetainedPayloadOwnershipAuditStore(retained_store).detail(
                arguments.reconcile_audit_id,
                finding_limit=1,
            )["receipt"]
            mode = "reconcile"
        else:
            receipt = run_and_retain_payload_ownership_scan(plan, retained_store)
            mode = "scan"
        if not isinstance(receipt, dict):
            raise PlanError("retained audit receipt is not an object")
        operational = asyncio.run(_persist_operational_receipt(experiment_database, receipt))
    except (
        PlanError,
        PayloadOwnershipConfigurationError,
        PayloadOwnershipError,
        ExperimentServiceError,
        SQLAlchemyError,
        OSError,
    ) as exc:
        failure: dict[str, Any] = {
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }
        if receipt is not None:
            failure["retained_audit_id"] = receipt.get("audit_id")
            failure["reconcile_required"] = True
        print(json.dumps(failure, sort_keys=True))
        return 2
    print(
        json.dumps(
            {"ok": True, "mode": mode, "receipt": receipt, "operational_receipt": operational},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
