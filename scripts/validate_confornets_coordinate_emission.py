#!/usr/bin/env python3
"""Validate append-only write-time ConforNets coordinates against a plan."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in args.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    expected = {canonical(value) for value in plan["coordinates"]}
    observed = [canonical(row["coordinates"]) for row in rows]
    if len(observed) != len(set(observed)) or set(observed) != expected:
        parser.exit(2, "coordinate ledger does not equal the expected coordinate set\n")
    root = args.native_root.resolve(strict=True)
    paths = set()
    for row in rows:
        path = (root / row["source_relative_path"]).resolve(strict=True)
        path.relative_to(root)
        if path in paths or not path.is_file() or path.is_symlink():
            parser.exit(2, "coordinate ledger contains a shared or unsafe file\n")
        paths.add(path)
        payload = path.read_bytes()
        if len(payload) != row["bytes"] or hashlib.sha256(payload).hexdigest() != row["sha256"]:
            parser.exit(2, "coordinate file byte identity mismatch\n")
    print(json.dumps({"coordinates": len(rows), "status": "valid"}, sort_keys=True))


if __name__ == "__main__":
    main()
