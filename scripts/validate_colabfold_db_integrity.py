#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_msa.db_integrity import validate_db_family_integrity  # noqa: E402

DEFAULT_FAMILIES = ("uniref30_2302_db", "colabfold_envdb_202108_db")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate local ColabFold/MMseqs DB target, _seq, and _aln index keyspaces. "
            "This catches remapped/gapped alignment DBs that make expandaln fail with "
            "Missing alignments / Invalid alignment result record."
        )
    )
    parser.add_argument(
        "--db-root",
        required=True,
        help="Directory containing ColabFold MMseqs DB prefixes, e.g. /mnt/BioModStack/colabfold_db",
    )
    parser.add_argument(
        "--family",
        action="append",
        dest="families",
        help="DB family stem to validate. May be repeated. Defaults to UniRef30 and EnvDB.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only")
    return parser


def validate_colabfold_db_root(db_root: str | Path, families: list[str] | None = None) -> dict:
    selected_families = tuple(families or DEFAULT_FAMILIES)
    reports = [validate_db_family_integrity(db_root, family) for family in selected_families]
    return {
        "db_root": str(Path(db_root).resolve()),
        "compatible": all(report.compatible for report in reports),
        "families": [report.to_dict() for report in reports],
    }


def _format_text_report(payload: dict) -> str:
    lines = [f"ColabFold DB integrity: {payload['db_root']}", f"compatible: {payload['compatible']}"]
    for family in payload["families"]:
        lines.append("")
        lines.append(f"[{family['family']}] compatible={family['compatible']}")
        for label in ("target", "sequence", "alignment"):
            scan = family[label]
            lines.append(
                f"  {label}: exists={scan['exists']} count={scan['count']} "
                f"min={scan['min_id']} max={scan['max_id']} gaps={scan['gap_count']} "
                f"contiguous_from_zero={scan['contiguous_from_zero']} path={scan['index_path']}"
            )
        if family["issues"]:
            lines.append("  issues:")
            for issue in family["issues"]:
                lines.append(f"    - {issue}")
        else:
            lines.append("  issues: none")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    payload = validate_colabfold_db_root(args.db_root, args.families)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_text_report(payload), end="")
    return 0 if payload["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
