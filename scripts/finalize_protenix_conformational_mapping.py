#!/usr/bin/env python3
"""Finalize authoritative Protenix coordinates into canonical manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.conformational_mapping.protenix import (  # noqa: E402
    ProtenixMappingError,
    finalize_protenix,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        snapshots = json.loads(args.snapshots.read_text(encoding="utf-8"))
        runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
        finalize_protenix(request, snapshots, args.native_root, args.out, runtime)
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ProtenixMappingError) as exc:
        parser.exit(2, f"Protenix CM finalization failed: {exc}\n")


if __name__ == "__main__":
    main()
