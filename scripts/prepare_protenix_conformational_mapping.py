#!/usr/bin/env python3
"""Materialize a lossless Protenix runtime bundle from canonical authorities."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.conformational_mapping.contracts import canonical_json_bytes  # noqa: E402
from services.conformational_mapping.protenix import (  # noqa: E402
    ProtenixMappingError,
    build_protenix_runtime_bundle,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        snapshots = json.loads(args.snapshots.read_text(encoding="utf-8"))
        if not isinstance(snapshots, list):
            raise ProtenixMappingError("snapshots authority must be an ordered array")
        bundle = build_protenix_runtime_bundle(request, snapshots)
        args.out.mkdir(parents=True, exist_ok=False)
        (args.out / "protenix_input.json").write_bytes(canonical_json_bytes(bundle["input"]))
        (args.out / "cm_protenix_composition_audits_v1.json").write_bytes(
            canonical_json_bytes(bundle["composition_audits"])
        )
        (args.out / "cm_protenix_coordinate_context_v1.json").write_bytes(
            canonical_json_bytes(bundle["coordinate_context"])
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ProtenixMappingError) as exc:
        parser.exit(2, f"Protenix CM preparation failed: {exc}\n")


if __name__ == "__main__":
    main()
