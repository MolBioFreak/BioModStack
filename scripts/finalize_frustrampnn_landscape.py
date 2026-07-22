#!/usr/bin/env python3
"""Join raw FrustraMPNN rows to authoritative conformational-map identity."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.conformational_mapping.contracts import canonical_json_bytes  # noqa: E402
from services.conformational_mapping.frustration import (  # noqa: E402
    FrustrationLandscapeError,
    finalize_landscape,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path, required=True)
    parser.add_argument("--checkpoint-id", required=True)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--tool-id", required=True)
    parser.add_argument("--tool-sha256", required=True)
    parser.add_argument("--container-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        mapping = json.loads(args.map_path.read_text(encoding="utf-8"))
        landscape = finalize_landscape(
            args.raw,
            mapping,
            checkpoint_id=args.checkpoint_id,
            checkpoint_sha256=args.checkpoint_sha256,
            tool_id=args.tool_id,
            tool_sha256=args.tool_sha256,
            container_sha256=args.container_sha256,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{args.out.name}.", dir=args.out.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(canonical_json_bytes(landscape))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, args.out)
        finally:
            temporary.unlink(missing_ok=True)
    except (OSError, KeyError, TypeError, json.JSONDecodeError, FrustrationLandscapeError) as exc:
        parser.exit(2, f"FrustraMPNN landscape finalization failed: {exc}\n")


if __name__ == "__main__":
    main()
