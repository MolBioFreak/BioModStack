#!/usr/bin/env python3
"""Compute server-owned CM analysis and deterministic rankings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.conformational_mapping.analysis import (  # noqa: E402
    ConformationalAnalysisError,
    analyze_landscapes,
)
from services.conformational_mapping.contracts import canonical_json_bytes  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = analyze_landscapes(
            payload["ensemble"], payload["landscapes_by_candidate"],
            policy=payload["analysis_policy"], clash_rows={},
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(canonical_json_bytes(result))
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ConformationalAnalysisError) as exc:
        parser.exit(2, f"CM analysis failed: {exc}\n")


if __name__ == "__main__":
    main()
