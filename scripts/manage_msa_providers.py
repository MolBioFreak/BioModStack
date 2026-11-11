#!/usr/bin/env python3
"""Report configured MSA providers using the same boundary as /api/msa/providers.

No credentials are printed/read and no search or authentication request is made.
--enable-colabfold / --disable-colabfold switch public ColabFold searches for this computer.
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
    parser.add_argument("--enable-colabfold", action="store_true",
                        help="Declare this computer the one that submits public ColabFold searches")
    parser.add_argument("--disable-colabfold", action="store_true",
                        help="Remove that declaration; cached alignments keep working")
    args = parser.parse_args()
    if args.enable_colabfold or args.disable_colabfold:
        from biomodstack_msa_controller import disable_on_this_computer, enable_on_this_computer
        from services.msa_provider_setup import controller_config_path
        (enable_on_this_computer if args.enable_colabfold else disable_on_this_computer)(controller_config_path())
    from services.msa_provider_setup import provider_readiness
    result = provider_readiness()
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(bool(args.require and not result["providers"][args.require]["configured"]))


if __name__ == "__main__":
    raise SystemExit(main())
