#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pwd
import re
import shlex
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import rfc8785
from sqlalchemy import text

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
_JOB_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class ReconciliationCliError(RuntimeError):
    """Raised when the one-shot managed Development repair cannot run safely."""

    exit_code = 2


class ReconciliationCliTransactionError(ReconciliationCliError):
    """Raised when backup or transaction work cannot finish atomically."""

    exit_code = 4


class ReconciliationCliConflict(ReconciliationCliError):
    """Raised when managed authority changes after planning."""

    exit_code = 3


class ManagedDevelopmentLane(NamedTuple):
    code_root: Path
    data_root: Path
    database_path: Path
    results_root: Path
    environment: dict[str, str]


def _reconciliation_digest(label: str, value: object) -> str:
    payload = rfc8785.dumps(value)
    return hashlib.sha256(
        b"bms.ont-fastq-qc-reconciliation.v1\0" + label.encode("utf-8") + b"\0" + payload
    ).hexdigest()


def _database_identity_sha256(lane: ManagedDevelopmentLane) -> str:
    database_stat = _lstat(lane.database_path)
    identity = {
        "schema": "bms.sqlite-database-identity.v1",
        "lane": "development",
        "logical_name": "biomodstack",
        "service_unit": "biomodstack-api.service",
        "resolved_path_sha256": hashlib.sha256(str(lane.database_path).encode("utf-8")).hexdigest(),
        "device": int(database_stat.st_dev),
        "inode": int(database_stat.st_ino),
    }
    return _reconciliation_digest("database-identity", identity)


def _assert_no_active_workflow_units() -> None:
    listed = subprocess.run(
        [
            "systemctl",
            "--user",
            "list-units",
            "biomodstack-development-job-*.service",
            "--type=service",
            "--state=active,activating,reloading,deactivating",
            "--no-legend",
            "--plain",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0:
        raise ReconciliationCliError("active Development workflow owner state cannot be read")
    if listed.stdout.strip():
        raise ReconciliationCliError("an active Development workflow owner blocks reconciliation")


def _exception_exit_code(exc: BaseException) -> int:
    value = getattr(exc, "exit_code", 4)
    return value if value in {2, 3, 4} else 4


def _parse_environment(raw: str) -> dict[str, str]:
    try:
        values = dict(item.split("=", 1) for item in shlex.split(raw) if "=" in item)
    except ValueError as exc:
        raise ReconciliationCliError("managed Development environment is malformed") from exc
    return values


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReconciliationCliError("managed Development path authority is unavailable") from exc


def parse_managed_development_environment(
    raw: str,
    *,
    script_repo_root: Path,
) -> ManagedDevelopmentLane:
    environment = _parse_environment(raw)
    if environment.get("BMS_RUNTIME_MODE") != "dev":
        raise ReconciliationCliError("managed API is not the Development runtime")
    required = ("BMS_HOME", "BMS_DATA", "BMS_RESULTS_DIR", "BMS_RESULTS_ROOT")
    if any(not environment.get(key) for key in required):
        raise ReconciliationCliError("managed Development environment is incomplete")

    code_root = Path(environment["BMS_HOME"]).expanduser().resolve()
    expected_source = Path(script_repo_root).expanduser().resolve()
    if code_root != expected_source:
        raise ReconciliationCliError("tool source is not the canonical Development source")
    data_root = Path(environment["BMS_DATA"]).expanduser().resolve()
    results_root = Path(environment["BMS_RESULTS_DIR"]).expanduser().resolve()
    results_root_alias = Path(environment["BMS_RESULTS_ROOT"]).expanduser().resolve()
    if results_root != results_root_alias:
        raise ReconciliationCliError("managed Development results roots disagree")
    try:
        results_root.relative_to(data_root)
    except ValueError as exc:
        raise ReconciliationCliError("managed Development results root escapes its data root") from exc
    database_path = (data_root / "biomodstack.db").resolve()

    for path in (code_root, data_root, results_root):
        path_stat = _lstat(path)
        if not stat.S_ISDIR(path_stat.st_mode) or path.is_symlink():
            raise ReconciliationCliError("managed Development directory authority is invalid")
    database_stat = _lstat(database_path)
    if not stat.S_ISREG(database_stat.st_mode) or database_path.is_symlink():
        raise ReconciliationCliError("managed Development database authority is invalid")
    return ManagedDevelopmentLane(
        code_root=code_root,
        data_root=data_root,
        database_path=database_path,
        results_root=results_root,
        environment=environment,
    )


def managed_runtime_environment(lane: ManagedDevelopmentLane) -> dict[str, str]:
    environment = dict(lane.environment)
    environment.update({
        "BMS_HOME": str(lane.code_root),
        "BMS_DATA": str(lane.data_root),
        "BMS_DB_PATH": str(lane.database_path),
        "DATABASE_URL": f"sqlite+aiosqlite:///{lane.database_path}",
        "BMS_RESULTS_DIR": str(lane.results_root),
        "BMS_RESULTS_ROOT": str(lane.results_root),
    })
    return environment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Revalidate and reconcile one completed Development ONT FASTQ-QC Job without compute.",
    )
    parser.add_argument("--job-id", required=True, help="Exact completed ONT FASTQ-QC Job UUID")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate and print the planned mirror repair")
    mode.add_argument("--apply", action="store_true", help="Back up the managed DB and apply one guarded repair")
    return parser


def _managed_environment_text() -> str:
    active = subprocess.run(
        ["systemctl", "--user", "is-active", "biomodstack-api.service"],
        check=False,
        capture_output=True,
        text=True,
    )
    if active.returncode != 0 or active.stdout.strip() != "active":
        raise ReconciliationCliError("managed Development API owner is not active")
    shown = subprocess.run(
        ["systemctl", "--user", "show", "biomodstack-api.service", "--property=Environment", "--value"],
        check=False,
        capture_output=True,
        text=True,
    )
    if shown.returncode != 0:
        raise ReconciliationCliError("managed Development environment cannot be read")
    return shown.stdout


def _source_identity(code_root: Path) -> tuple[str, str]:
    revision = subprocess.run(
        ["git", "-C", str(code_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    tree = subprocess.run(
        ["git", "-C", str(code_root), "rev-parse", "HEAD^{tree}"],
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "-C", str(code_root), "status", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    revision_value = revision.stdout.strip()
    tree_value = tree.stdout.strip()
    if (
        revision.returncode != 0
        or tree.returncode != 0
        or status.returncode != 0
        or status.stdout.strip()
        or re.fullmatch(r"[0-9a-f]{40}", revision_value) is None
        or re.fullmatch(r"[0-9a-f]{40}", tree_value) is None
    ):
        raise ReconciliationCliError("canonical Development source is dirty or has no exact commit/tree")
    return revision_value, tree_value


def _public_plan(plan) -> dict[str, object]:
    receipt = plan.provenance.get("ont_fastq_qc_reconciliation_v1", {})
    return {
        "schema": "biomodstack.ont-fastq-qc-reconciliation-cli.v1",
        "job_id": plan.job_id,
        "requires_write": plan.requires_write,
        "completed_stages": list(plan.completed_stages),
        "stage_output_counts": {key: len(value) for key, value in plan.stage_outputs.items()},
        "protected_preimage_sha256": plan.protected_preimage_sha256,
        "mirror_postimage_sha256": plan.mirror_postimage_sha256,
        "sequence_qc_manifest_sha256": receipt.get("sequence_qc_manifest_sha256"),
        "verification_manifest_sha256": receipt.get("verification_manifest_sha256"),
        "artifact_set_sha256": receipt.get("artifact_set_sha256"),
        "declared_artifact_count": receipt.get("declared_artifact_count"),
        "result_root_identity_sha256": receipt.get("result_root_identity_sha256"),
        "hierarchy_authority_sha256": receipt.get("hierarchy_authority_sha256"),
        "database_identity_sha256": receipt.get("database_identity_sha256"),
        "source_commit": receipt.get("source_commit"),
        "source_tree": receipt.get("source_tree"),
        "receipt_sha256": receipt.get("receipt_sha256"),
        "backup": receipt.get("backup"),
        "compute_invoked": False,
        "scientific_artifacts_modified": False,
    }


async def _resolve_fresh_hierarchy_authority(
    job,
    evidence,
    *,
    domain_session_factory,
    hierarchy_session_factory,
    resolver,
):
    """Resolve pre-commit hierarchy through new sessions with empty identity maps."""
    async with (
        domain_session_factory() as fresh_domain_session,
        hierarchy_session_factory() as fresh_experiment_session,
    ):
        return await resolver(
            job,
            fresh_domain_session,
            fresh_experiment_session,
            source_fastq_sha256=evidence.source_fastq_sha256,
            artifact_set_sha256=evidence.artifact_set_sha256,
            sequence_qc_manifest_sha256=evidence.sequence_qc_manifest_sha256,
            verification_manifest_sha256=evidence.verification_manifest_sha256,
            reference_sequence_sha256=evidence.reference_sequence_sha256,
        )


async def _execute(
    args: argparse.Namespace,
    lane: ManagedDevelopmentLane,
    source_revision: str,
    source_tree: str,
    database_identity_sha256: str,
) -> dict[str, object]:
    for key, value in managed_runtime_environment(lane).items():
        if key.startswith("BMS_") or key == "DATABASE_URL":
            os.environ[key] = value
    if str(API_ROOT) not in sys.path:
        sys.path.insert(0, str(API_ROOT))

    from database import DEFAULT_DB_PATH, Job, async_session
    from experiment_database import experiment_session_factory
    from molbio_ngs_database import molbio_ngs_session_factory
    from services.ont_ngs_hierarchy import (
        OntNgsHierarchyError,
        hierarchy_authority_record,
        resolve_ont_ngs_hierarchy_authority_for_reconciliation,
    )
    from services.ont_ngs_reconciliation import (
        OntFastqQcReconciliationBackup,
        apply_ont_fastq_qc_reconciliation_plan,
        bind_ont_fastq_qc_reconciliation_backup,
        build_ont_fastq_qc_reconciliation_plan,
        collect_ont_fastq_qc_reconciliation_evidence,
    )
    from services.sqlite_backup import (
        backup_sqlite_database,
        checkpoint_sqlite_wal,
        inspect_sqlite_source_snapshot,
        open_attested_sqlite_readonly_connection,
        verify_sqlite_backup,
    )

    if Path(DEFAULT_DB_PATH).resolve() != lane.database_path:
        raise ReconciliationCliError("loaded database identity is not managed Development")
    if os.environ.get("BMS_RESULTS_DIR") != str(lane.results_root):
        raise ReconciliationCliError("loaded results identity is not managed Development")

    applied_at = datetime.now(timezone.utc)
    principal = f"uid:{os.getuid()}:{pwd.getpwuid(os.getuid()).pw_name}"
    async with (
        async_session() as session,
        molbio_ngs_session_factory() as domain_session,
        experiment_session_factory() as experiment_session,
    ):
        job = await session.get(Job, args.job_id)
        if job is None:
            raise ReconciliationCliError("requested Job does not exist in managed Development")
        evidence = await collect_ont_fastq_qc_reconciliation_evidence(job)
        try:
            hierarchy = await resolve_ont_ngs_hierarchy_authority_for_reconciliation(
                job,
                domain_session,
                experiment_session,
                source_fastq_sha256=evidence.source_fastq_sha256,
                artifact_set_sha256=evidence.artifact_set_sha256,
                sequence_qc_manifest_sha256=evidence.sequence_qc_manifest_sha256,
                verification_manifest_sha256=evidence.verification_manifest_sha256,
                reference_sequence_sha256=evidence.reference_sequence_sha256,
            )
        except OntNgsHierarchyError as exc:
            raise ReconciliationCliError("managed retry3 hierarchy authority is invalid") from exc
        hierarchy_record = hierarchy_authority_record(hierarchy)
        plan = build_ont_fastq_qc_reconciliation_plan(
            job,
            evidence,
            hierarchy_record=hierarchy_record,
            database_identity_sha256=database_identity_sha256,
            source_revision=source_revision,
            source_tree=source_tree,
            applied_at=applied_at,
            principal=principal,
            authorization_class="development_service_owner",
        )
        if not plan.requires_write:
            receipt = plan.provenance.get("ont_fastq_qc_reconciliation_v1")
            backup = receipt.get("backup") if isinstance(receipt, dict) else None
            if not isinstance(backup, dict):
                raise ReconciliationCliTransactionError("reconciliation replay has no backup authority")
            backup_id = str(backup.get("backup_id") or "")
            if Path(backup_id).name != backup_id:
                raise ReconciliationCliTransactionError("reconciliation replay backup identity is invalid")
            replay_backup_path = lane.data_root / "backups" / "ngs-reconciliation" / backup_id
            try:
                await asyncio.to_thread(
                    verify_sqlite_backup,
                    replay_backup_path,
                    expected_size_bytes=int(backup["size_bytes"]),
                    expected_sha256=str(backup["sha256"]),
                )
            except Exception as exc:
                raise ReconciliationCliTransactionError(
                    "reconciliation replay backup verification failed"
                ) from exc
        if args.dry_run or not plan.requires_write:
            result = _public_plan(plan)
            result["mode"] = "dry_run" if args.dry_run else "idempotent_no_op"
            result["applied"] = False
            return result

        backup_id = (
            f"ngs-reconciliation-{args.job_id}-{plan.protected_preimage_sha256[:12]}-"
            f"{applied_at.strftime('%Y%m%dT%H%M%S%fZ')}.sqlite"
        )
        backup_path = lane.data_root / "backups" / "ngs-reconciliation" / backup_id
        _assert_no_active_workflow_units()
        retained_source_connection = await asyncio.to_thread(
            open_attested_sqlite_readonly_connection,
            lane.database_path,
        )
        try:
            await session.rollback()
            await session.execute(text("BEGIN IMMEDIATE"))
            await asyncio.to_thread(checkpoint_sqlite_wal, lane.database_path, mode="PASSIVE")
            _assert_no_active_workflow_units()
            current_database_identity_sha256 = _database_identity_sha256(lane)
            if current_database_identity_sha256 != database_identity_sha256:
                raise ReconciliationCliConflict("managed Development database identity changed before backup")
            try:
                backup_report = await asyncio.to_thread(
                    backup_sqlite_database,
                    lane.database_path,
                    backup_path,
                    database_identity_sha256=database_identity_sha256,
                    checkpoint_wal=False,
                    source_connection=retained_source_connection,
                )
            except Exception as exc:
                raise ReconciliationCliTransactionError("managed Development backup failed") from exc
            plan = bind_ont_fastq_qc_reconciliation_backup(
                plan,
                OntFastqQcReconciliationBackup(
                    backup_id=backup_id,
                    sha256=backup_report.sha256,
                    size_bytes=backup_report.size_bytes,
                    integrity_check=backup_report.integrity_check,
                    foreign_key_violations=backup_report.foreign_key_violations,
                    source_snapshot=backup_report.source_snapshot,
                ),
            )
            current_job = await session.get(Job, args.job_id, populate_existing=True)
            if current_job is None:
                raise ReconciliationCliConflict("reconciliation Job disappeared before apply")
            try:
                current_source_snapshot = await asyncio.to_thread(
                    inspect_sqlite_source_snapshot,
                    lane.database_path,
                    database_identity_sha256=current_database_identity_sha256,
                )
            except Exception as exc:
                raise ReconciliationCliConflict(
                    "managed Development source snapshot changed after backup"
                ) from exc
            changed = await apply_ont_fastq_qc_reconciliation_plan(
                session,
                current_job,
                plan,
                current_source_snapshot=current_source_snapshot,
                current_database_identity_sha256=current_database_identity_sha256,
            )
            after_evidence = await collect_ont_fastq_qc_reconciliation_evidence(current_job)
            if after_evidence != evidence:
                raise ReconciliationCliError("scientific evidence changed before repair commit")
            try:
                replay_hierarchy = await _resolve_fresh_hierarchy_authority(
                    current_job,
                    after_evidence,
                    domain_session_factory=molbio_ngs_session_factory,
                    hierarchy_session_factory=experiment_session_factory,
                    resolver=resolve_ont_ngs_hierarchy_authority_for_reconciliation,
                )
            except OntNgsHierarchyError as exc:
                raise ReconciliationCliTransactionError(
                    "managed retry3 hierarchy authority changed before commit"
                ) from exc
            replay = build_ont_fastq_qc_reconciliation_plan(
                current_job,
                after_evidence,
                hierarchy_record=hierarchy_authority_record(replay_hierarchy),
                database_identity_sha256=database_identity_sha256,
                source_revision=source_revision,
                source_tree=source_tree,
                applied_at=datetime.now(timezone.utc),
                principal=principal,
                authorization_class="development_service_owner",
            )
            if replay.requires_write:
                raise ReconciliationCliTransactionError("reconciliation did not become idempotent before commit")
            await asyncio.to_thread(
                verify_sqlite_backup,
                backup_path,
                expected_size_bytes=backup_report.size_bytes,
                expected_sha256=backup_report.sha256,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await asyncio.to_thread(retained_source_connection.close)

    result = _public_plan(replay)
    result.update({
        "mode": "apply",
        "applied": changed,
    })
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if re.fullmatch(_JOB_ID_PATTERN, args.job_id) is None:
        print(json.dumps({"schema": "biomodstack.ont-fastq-qc-reconciliation-cli.v1", "status": "error", "error": "job ID is invalid"}, sort_keys=True))
        return 2
    try:
        raw_environment = _managed_environment_text()
        lane = parse_managed_development_environment(raw_environment, script_repo_root=REPO_ROOT)
        _assert_no_active_workflow_units()
        source_revision, source_tree = _source_identity(lane.code_root)
        database_identity_sha256 = _database_identity_sha256(lane)
        result = asyncio.run(
            _execute(
                args,
                lane,
                source_revision,
                source_tree,
                database_identity_sha256,
            )
        )
    except Exception as exc:
        print(json.dumps({
            "schema": "biomodstack.ont-fastq-qc-reconciliation-cli.v1",
            "status": "error",
            "error": str(exc),
        }, sort_keys=True))
        return _exception_exit_code(exc)
    print(json.dumps({**result, "status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
