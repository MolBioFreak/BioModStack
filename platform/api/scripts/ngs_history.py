"""Operator-only historical tooling. Run from platform/api with -m scripts.ngs_history.

No default database, automatic migration, worker start, retry, or activation.
Inventory/report use SQLite mode=ro; backfill requires a reviewed plan and fresh
preflight receipt. This module is intentionally not mounted as an HTTP endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def protected_preimage(connection):
    from services.ngs_historical_backfill import PROTECTED_JOB
    cursor = connection.execute("SELECT * FROM jobs WHERE id=?", (PROTECTED_JOB,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError("protected job preimage is missing; cannot authorize mutation")
    # SQLite values, including original JSON strings, are not reinterpreted.
    value = dict(zip((column[0] for column in cursor.description), row))
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def inspect_database(path):
    from migrations.runner import MIGRATIONS, _validate_applied_migration_identities, _validate_applied_migration_content
    with sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True) as connection:
        applied = dict(connection.execute("SELECT version, name FROM schema_migrations"))
        _validate_applied_migration_identities(applied)
        _validate_applied_migration_content(connection)
        required = {migration.version: migration.name for migration in MIGRATIONS}
        if applied != required:
            raise ValueError("schema installation is incomplete; use the reviewed migration runner separately")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(ngs_alignment_derived_products)")}
        if "historical_owner" not in columns:
            raise ValueError("historical ownership column is missing")
        return {"migrations": [[key, value] for key, value in sorted(applied.items())],
                "protected_preimage_sha256": protected_preimage(connection)}


def verify_preflight(path, database, plan, revision):
    preflight = json.loads(Path(path).read_text())
    required = {"schema", "database", "plan_sha256", "source_revision", "backup_path", "backup_sha256",
                "protected_preimage_sha256", "service_owner", "authorization", "managed_origin",
                "expires_at", "workers_quiescent"}
    if set(preflight) != required or preflight["schema"] != "bms.ngs.historical-preflight.v1":
        raise ValueError("closed historical preflight receipt required")
    if (preflight["database"] != str(database) or preflight["plan_sha256"] != plan["plan_sha256"]
            or preflight["source_revision"] != revision
            or not all(isinstance(preflight[key], str) and preflight[key].strip()
                       for key in ("authorization", "service_owner", "managed_origin"))):
        raise ValueError("preflight is not bound to this target, revision and plan")
    from datetime import datetime, timezone
    import subprocess
    expiry = datetime.fromisoformat(preflight["expires_at"])
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc) or preflight["workers_quiescent"] is not True:
        raise ValueError("preflight expired or worker quiescence was not acknowledged")
    checkout = Path(__file__).resolve().parents[3]
    current_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip()
    if current_revision != revision or subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=checkout, text=True).strip():
        raise ValueError("checkout revision/clean source preflight failed")
    backup = Path(preflight["backup_path"]).resolve(strict=True)
    if backup == database or file_sha256(backup) != preflight["backup_sha256"]:
        raise ValueError("fresh backup verification failed")
    with sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("backup integrity check failed")
        if protected_preimage(connection) != preflight["protected_preimage_sha256"]:
            raise ValueError("backup protected preimage mismatch")
    if inspect_database(database)["protected_preimage_sha256"] != preflight["protected_preimage_sha256"]:
        raise ValueError("current protected preimage differs from verified backup")
    return preflight


async def run(args):
    # Imports are delayed until an explicit operator invocation, never on startup.
    from sqlalchemy import text, select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import Job
    from services import ngs_historical_backfill as history
    database = Path(args.database).resolve(strict=True)
    schema = inspect_database(database)
    plan = json.loads(Path(args.plan).read_text()) if args.plan else None
    if args.action in {"backfill", "report"} and plan is None:
        raise ValueError("a reviewed --plan is required")
    if args.action in {"inventory", "dry-run"} and plan is not None:
        raise ValueError("inventory scope uses --job-id/--all-jobs, not --plan")
    if args.action in {"backfill", "report"} and (args.job_id or args.all_jobs):
        raise ValueError("backfill/report designation comes only from --plan")
    mutation = args.action == "backfill"
    preflight = None
    if mutation:
        if not args.apply or not args.preflight or not args.revision:
            raise ValueError("backfill requires --apply, --preflight and --revision")
        history.validate_plan(plan)
        preflight = verify_preflight(args.preflight, database, plan, args.revision)
    elif args.apply or args.preflight:
        raise ValueError("mutation safeguards are only valid for backfill")
    mode = "rw" if mutation else "ro"
    engine = create_async_engine("sqlite+aiosqlite:///file:" + quote(str(database), safe="/") + "?mode=" + mode + "&uri=true")
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(text("BEGIN IMMEDIATE" if mutation else "BEGIN"))
            try:
                if mutation:
                    # Recheck protected bytes after taking the writer lock.
                    result = await session.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": history.PROTECTED_JOB})
                    protected = dict(result.mappings().one())
                    encoded = json.dumps(protected, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
                    if hashlib.sha256(encoded.encode()).hexdigest() != preflight["protected_preimage_sha256"]:
                        raise ValueError("protected preimage changed before transaction")
                    admitted = await history.backfill(session, plan)
                    await session.commit()
                    output = {"schema": "bms.ngs.historical-backfill-receipt.v1", "database": str(database),
                        "plan_sha256": plan["plan_sha256"], "request_ids": admitted,
                        "construction": "existing_worker_only", "reader_activation": "not_performed"}
                elif args.action == "report":
                    output = await history.report(session, plan)
                else:
                    if args.all_jobs and args.job_id:
                        raise ValueError("select --all-jobs or --job-id, not both")
                    ids = args.job_id or []
                    if args.all_jobs:
                        # Enumerate all rows, including protected/failed/unrelated,
                        # rather than hiding exclusions behind a scientific filter.
                        ids = list((await session.scalars(select(Job.id).order_by(Job.id))).all())
                    if not ids:
                        raise ValueError("explicit --job-id or --all-jobs inventory scope required")
                    output = await history.inventory(session, ids)
                if not mutation:
                    await session.rollback()
            except BaseException:
                await session.rollback()
                raise
        if args.action == "report":
            output.update(database=str(database), schema_preflight=schema)
        print(json.dumps(output, indent=2, ensure_ascii=False))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inventory", "dry-run", "backfill", "report"))
    parser.add_argument("--database", required=True, help="explicit managed Development SQLite path; never created")
    parser.add_argument("--job-id", action="append")
    parser.add_argument("--all-jobs", action="store_true")
    parser.add_argument("--plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--preflight")
    parser.add_argument("--revision")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as exc:
        parser.exit(2, f"Historical operation blocked: {exc}\n")


if __name__ == "__main__":
    main()
