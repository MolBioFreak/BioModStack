#!/usr/bin/env python3
"""Finalize a descriptor-safe CM import receipt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.conformational_mapping.import_stager import (  # noqa: E402
    ImportStagingError,
    finalize_staged_import,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--staged-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        snapshots = json.loads(args.snapshot.read_text(encoding="utf-8"))
        if not isinstance(snapshots, list) or len(snapshots) != 1 or not isinstance(snapshots[0], dict):
            raise ImportStagingError("external import requires exactly one generated complex snapshot")
        finalize_staged_import(request, snapshots[0], args.staged_root, args.out)
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ImportStagingError) as exc:
        parser.exit(2, f"CM import finalization failed: {exc}\n")


if __name__ == "__main__":
    main()
