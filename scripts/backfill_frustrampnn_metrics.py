#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


API_DIR = Path(__file__).resolve().parents[1] / "platform" / "api"
sys.path.insert(0, str(API_DIR))

from paths import get_db_path  # noqa: E402
from services.result_ingester import parse_frustration_csv  # noqa: E402


MODEL_SUFFIX_RE = re.compile(r"_model_\d+$")


def _candidate_csv_paths(
    design_name: str,
    pdb_path: str | None,
    job_output_dir: str | None,
    frustration_csv_path: str | None,
) -> list[Path]:
    seen: set[Path] = set()
    candidates: list[Path] = []

    def add(path_like: str | Path | None) -> None:
        if not path_like:
            return
        path = Path(path_like)
        if path in seen:
            return
        seen.add(path)
        candidates.append(path)

    add(frustration_csv_path)

    if pdb_path:
        add(Path(pdb_path).with_suffix(".frustration.csv"))

    base_name = MODEL_SUFFIX_RE.sub("", str(design_name or "").strip())
    if job_output_dir and base_name:
        add(Path(job_output_dir) / "frustration" / f"{base_name}_frustration.csv")

    return [path for path in candidates if path.exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill FrustraMPNN metrics from saved CSV outputs.")
    parser.add_argument("--db-path", default=str(get_db_path()), help="SQLite database path")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without writing")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        """
        SELECT d.id, d.name, d.pdb_path, d.frustration_csv_path, j.output_dir
        FROM designs d
        JOIN jobs j ON j.id = d.job_id
        """
    )
    rows = cur.fetchall()

    updated = 0
    skipped = 0
    repaired_nonzero = 0

    for row in rows:
        csv_candidates = _candidate_csv_paths(
            design_name=row["name"],
            pdb_path=row["pdb_path"],
            job_output_dir=row["output_dir"],
            frustration_csv_path=row["frustration_csv_path"],
        )
        if not csv_candidates:
            skipped += 1
            continue

        frust_data = None
        used_path: Path | None = None
        for csv_path in csv_candidates:
            frust_data = parse_frustration_csv(csv_path, pdb_name_filter=row["name"])
            if frust_data is None:
                frust_data = parse_frustration_csv(csv_path)
            if frust_data is not None:
                used_path = csv_path
                break

        if frust_data is None or used_path is None:
            skipped += 1
            continue

        if frust_data["high_count"] > 0 or frust_data["pct_high"] > 0:
            repaired_nonzero += 1

        if not args.dry_run:
            cur.execute(
                """
                UPDATE designs
                SET frustration_high_count = ?,
                    frustration_min_count = ?,
                    frustration_pct_high = ?,
                    frustration_residues = ?,
                    frustration_csv_path = ?
                WHERE id = ?
                """,
                (
                    int(frust_data["high_count"]),
                    int(frust_data["min_count"]),
                    float(frust_data["pct_high"]),
                    json.dumps(frust_data["residues"]),
                    str(used_path),
                    row["id"],
                ),
            )
        updated += 1

    if not args.dry_run:
        conn.commit()
    conn.close()

    mode = "dry-run" if args.dry_run else "write"
    print(
        json.dumps(
            {
                "mode": mode,
                "db_path": args.db_path,
                "updated_designs": updated,
                "repaired_nonzero_designs": repaired_nonzero,
                "skipped_designs": skipped,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
