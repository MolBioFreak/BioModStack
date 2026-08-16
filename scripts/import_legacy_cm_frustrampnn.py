#!/usr/bin/env python3
"""Import registered legacy CM FrustraMPNN authorities into the global store."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "platform" / "api"
sys.path.insert(0, str(API))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    configured = args.database.expanduser()
    info = os.lstat(configured)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("database must be an existing real regular file")
    database = configured.resolve(strict=True)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    from database import async_session
    from services.frustrampnn.cm_legacy_import import import_legacy_cm_frustrampnn

    async with async_session() as session:
        imported = await import_legacy_cm_frustrampnn(session, job_id=args.job_id)
    return {"status":"complete","job_id":args.job_id,"imported_results":imported,"database":str(database)}


def main() -> int:
    try:
        print(json.dumps(asyncio.run(_run(_parser().parse_args())), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status":"failed","error":str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
