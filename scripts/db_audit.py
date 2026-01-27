#!/usr/bin/env python3
"""
Audit BioModStack SQLite databases and optionally archive extras.

Default behavior: dry-run report only.
Use --apply to move non-canonical DB files to an archive directory.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from pathlib import Path
import sys


def load_paths():
    api_dir = Path(__file__).parent.parent / "platform" / "api"
    sys.path.insert(0, str(api_dir))
    from paths import get_code_root, get_db_path
    return get_code_root(), get_db_path()


def get_db_stats(db_path: Path) -> dict:
    stats = {"path": str(db_path), "size_bytes": db_path.stat().st_size}
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        cur = conn.cursor()
        tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        stats["tables"] = sorted(tables)
        if "jobs" in tables:
            stats["jobs"] = cur.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        if "designs" in tables:
            stats["designs"] = cur.execute("SELECT COUNT(*) FROM designs").fetchone()[0]
        conn.close()
    except Exception as e:
        stats["error"] = str(e)
    return stats


def main():
    parser = argparse.ArgumentParser(description="Audit SQLite DB files in BioModStack")
    parser.add_argument("--archive-dir", default=None, help="Archive directory (default: <repo>/db_archive)")
    parser.add_argument("--apply", action="store_true", help="Move non-canonical DBs into archive dir")
    args = parser.parse_args()

    code_root, canonical_db = load_paths()
    archive_dir = Path(args.archive_dir) if args.archive_dir else code_root / "db_archive"

    db_files = sorted(code_root.rglob("*.db"))
    print(f"Found {len(db_files)} DB files under {code_root}")
    print(f"Canonical DB: {canonical_db}")

    for db_file in db_files:
        stats = get_db_stats(db_file)
        label = "CANONICAL" if db_file.resolve() == canonical_db.resolve() else "EXTRA"
        jobs = stats.get("jobs", "?")
        designs = stats.get("designs", "?")
        size_mb = stats["size_bytes"] / (1024 * 1024)
        print(f"- [{label}] {db_file} | size={size_mb:.2f}MB | jobs={jobs} | designs={designs}")
        if stats.get("error"):
            print(f"  error: {stats['error']}")

    if args.apply:
        archive_dir.mkdir(parents=True, exist_ok=True)
        for db_file in db_files:
            if db_file.resolve() == canonical_db.resolve():
                continue
            target = archive_dir / db_file.name
            if target.exists():
                stem = db_file.stem
                suffix = db_file.suffix
                target = archive_dir / f"{stem}_{db_file.stat().st_mtime_ns}{suffix}"
            print(f"ARCHIVE: {db_file} -> {target}")
            shutil.move(str(db_file), str(target))


if __name__ == "__main__":
    main()
