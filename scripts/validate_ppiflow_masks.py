#!/usr/bin/env python3
"""Validate stage-specific PPIFlow residue masks.

The structural fixed mask must not overlap residues requested as movable CDR / partial-flow
positions. If an overlap is intended, it should be represented by an explicit motif-lock
mechanism rather than automatic interface-anchor selection.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable, Set, Tuple

ResidueKey = Tuple[str, int]


def parse_position_spec(value: str | None) -> Set[ResidueKey]:
    residues: Set[ResidueKey] = set()
    for token in (value or "").split(","):
        token = token.strip()
        if not token:
            continue
        match = re.match(r"^([A-Za-z])(-?\d+)(?:[A-Za-z]?)(?:-(-?\d+)(?:[A-Za-z]?)?)?$", token)
        if not match:
            continue
        chain, start_text, end_text = match.groups()
        start = int(start_text)
        end = int(end_text) if end_text is not None else start
        lo, hi = sorted((start, end))
        for resnum in range(lo, hi + 1):
            residues.add((chain, resnum))
    return residues


def format_position_spec(residues: Iterable[ResidueKey]) -> list[str]:
    return [f"{chain}{resnum}" for chain, resnum in sorted(set(residues), key=lambda item: (item[0], item[1]))]


def validate_masks(fixed_positions: str | None, movable_positions: str | None) -> dict[str, object]:
    fixed = parse_position_spec(fixed_positions)
    movable = parse_position_spec(movable_positions)
    overlap = fixed & movable
    return {
        "valid": not overlap,
        "fixed_position_count": len(fixed),
        "movable_position_count": len(movable),
        "overlap_count": len(overlap),
        "overlap_positions": format_position_spec(overlap),
    }


def _read_spec(raw: str | None, path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return raw or ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate PPIFlow fixed/movable residue masks")
    parser.add_argument("--fixed_positions", default="", help="Fixed-position spec such as H27,H50-55")
    parser.add_argument("--fixed_positions_file", default="", help="File containing fixed-position spec")
    parser.add_argument("--movable_positions", default="", help="Movable/CDR spec such as H105-117")
    parser.add_argument("--movable_positions_file", default="", help="File containing movable-position spec")
    parser.add_argument("--report_json", default="", help="Optional JSON report path")
    args = parser.parse_args()

    report = validate_masks(
        _read_spec(args.fixed_positions, args.fixed_positions_file),
        _read_spec(args.movable_positions, args.movable_positions_file),
    )
    if args.report_json:
        Path(args.report_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["valid"]:
        raise SystemExit(
            "[PPIFlow] fixed_positions overlap movable cdr_position: "
            + ",".join(report["overlap_positions"])
        )


if __name__ == "__main__":
    main()
