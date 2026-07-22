#!/usr/bin/env python3
"""Produce a fail-closed Phase 12 review from authenticated current-run evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

from phase_review_common import PhaseReviewError, adjudicate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        review = adjudicate(12, args.evidence_root, args.manifest, args.out)
    except (OSError, ValueError, PhaseReviewError) as exc:
        parser.exit(2, f"Phase 12 review failed closed: {exc}\n")
    print(review["decision"])


if __name__ == "__main__":
    main()
