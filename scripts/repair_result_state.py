#!/usr/bin/env python3
"""Dry-run-first repair tool for BioModStack job/result state integrity."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1] / "platform" / "api"
sys.path.insert(0, str(API_DIR))


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit and optionally repair contradictory BioModStack job/result state"
    )
    parser.add_argument(
        "--database",
        type=Path,
        help="SQLite database path (defaults to the configured BioModStack database)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the reported repairs; without this flag the command is read-only",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        help="Copy the SQLite database here before --apply (recommended)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Write the complete JSON before/after report to this path",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict:
    if args.database:
        database_path = args.database.expanduser().resolve()
        if not database_path.exists():
            raise SystemExit(f"database does not exist: {database_path}")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    else:
        from paths import get_db_path

        database_path = get_db_path().expanduser().resolve()
        if not database_path.exists():
            raise SystemExit(f"database does not exist: {database_path}")

    if args.backup and not args.apply:
        raise SystemExit("--backup is only meaningful with --apply")

    before_hash = _sha256(database_path)
    backup_path = None
    if args.apply and args.backup:
        backup_path = args.backup.expanduser().resolve()
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(database_path, backup_path)

    # Import only after DATABASE_URL has been selected.
    from database import async_session
    from services.result_state_integrity import repair_result_state

    async with async_session() as session:
        repair = await repair_result_state(session, apply=args.apply)

    payload = {
        "tool": "repair_result_state",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if args.apply else "dry-run",
        "database_path": str(database_path),
        "database_sha256_before": before_hash,
        "database_sha256_after": _sha256(database_path),
        "backup_path": str(backup_path) if backup_path else None,
        "backup_recommendation": (
            "Use --backup PATH with --apply; this report also contains complete before/after state."
        ),
        **repair.to_dict(),
    }
    return payload


def main() -> int:
    args = _parser().parse_args()
    payload = asyncio.run(_run(args))
    rendered = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.report:
        report_path = args.report.expanduser().resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
