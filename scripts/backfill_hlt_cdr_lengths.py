#!/usr/bin/env python3
"""
Backfill per-design CDR loop lengths from RFantibody HLT REMARK labels.

This is intended for previously ingested antibody jobs where design rows were
populated with job-level configured loop spans instead of actual per-structure
loop lengths. It only updates rows whose PDB files carry RFantibody-style
`REMARK PDBinfo-LABEL` annotations.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Dict, Optional


LOOPS = ("H1", "H2", "H3", "L1", "L2", "L3")


def parse_hlt_cdr_lengths(pdb_path: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if not pdb_path.exists() or pdb_path.suffix.lower() != ".pdb":
        return counts

    with pdb_path.open("r") as handle:
        for line in handle:
            if not line.startswith("REMARK PDBinfo-LABEL:"):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            loop_id = parts[3].upper()
            if loop_id in LOOPS:
                counts[loop_id] = counts.get(loop_id, 0) + 1

    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill antibody CDR lengths from HLT REMARK labels")
    parser.add_argument("--db", required=True, help="Path to SQLite database")
    parser.add_argument("--job-id", action="append", default=[], help="Limit updates to specific job ID(s)")
    parser.add_argument("--job-name", action="append", default=[], help="Limit updates to specific job name(s)")
    parser.add_argument("--dry-run", action="store_true", help="Show updates without writing them")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    where_clauses = ["d.pdb_path IS NOT NULL", "d.pdb_path LIKE '%.pdb'"]
    params: list[str] = []

    if args.job_id:
        where_clauses.append(f"d.job_id IN ({','.join('?' for _ in args.job_id)})")
        params.extend(args.job_id)
    if args.job_name:
        where_clauses.append(f"j.name IN ({','.join('?' for _ in args.job_name)})")
        params.extend(args.job_name)

    sql = f"""
        SELECT
            d.id,
            d.job_id,
            j.name AS job_name,
            d.name,
            d.pdb_path,
            d.cdr_h1_length,
            d.cdr_h2_length,
            d.cdr_h3_length,
            d.cdr_l1_length,
            d.cdr_l2_length,
            d.cdr_l3_length
        FROM designs d
        JOIN jobs j ON j.id = d.job_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY j.created_at DESC, d.name ASC
    """

    rows = conn.execute(sql, params).fetchall()
    updated = 0
    checked = 0

    for row in rows:
        pdb_path = Path(row["pdb_path"])
        lengths = parse_hlt_cdr_lengths(pdb_path)
        if not lengths:
            continue

        checked += 1
        payload = {
            "cdr_h1_length": lengths.get("H1"),
            "cdr_h2_length": lengths.get("H2"),
            "cdr_h3_length": lengths.get("H3"),
            "cdr_l1_length": lengths.get("L1"),
            "cdr_l2_length": lengths.get("L2"),
            "cdr_l3_length": lengths.get("L3"),
        }

        current = {
            "cdr_h1_length": row["cdr_h1_length"],
            "cdr_h2_length": row["cdr_h2_length"],
            "cdr_h3_length": row["cdr_h3_length"],
            "cdr_l1_length": row["cdr_l1_length"],
            "cdr_l2_length": row["cdr_l2_length"],
            "cdr_l3_length": row["cdr_l3_length"],
        }

        if payload == current:
            continue

        updated += 1
        print(
            f"[BACKFILL] {row['job_name']} :: {row['name']} :: "
            f"H1 {current['cdr_h1_length']} -> {payload['cdr_h1_length']}, "
            f"H2 {current['cdr_h2_length']} -> {payload['cdr_h2_length']}, "
            f"H3 {current['cdr_h3_length']} -> {payload['cdr_h3_length']}"
        )

        if not args.dry_run:
            conn.execute(
                """
                UPDATE designs
                SET
                    cdr_h1_length = ?,
                    cdr_h2_length = ?,
                    cdr_h3_length = ?,
                    cdr_l1_length = ?,
                    cdr_l2_length = ?,
                    cdr_l3_length = ?
                WHERE id = ?
                """,
                (
                    payload["cdr_h1_length"],
                    payload["cdr_h2_length"],
                    payload["cdr_h3_length"],
                    payload["cdr_l1_length"],
                    payload["cdr_l2_length"],
                    payload["cdr_l3_length"],
                    row["id"],
                ),
            )

    if not args.dry_run:
        conn.commit()

    print(f"[BACKFILL] checked={checked} updated={updated} dry_run={args.dry_run}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
