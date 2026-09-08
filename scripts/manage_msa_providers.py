#!/usr/bin/env python3
"""Report configured MSA providers using the same boundary as /api/msa/providers.

No credentials are printed/read and no search or authentication request is made.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "platform" / "api"), str(ROOT)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require", choices=("colabfold_api", "neurosnap_api"),
                        help="Exit nonzero if this provider is not configured")
    args = parser.parse_args()
    from services.msa_provider_setup import provider_readiness
    result = provider_readiness()
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(bool(args.require and not result["providers"][args.require]["configured"]))


if __name__ == "__main__":
    raise SystemExit(main())
