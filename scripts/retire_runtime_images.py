#!/usr/bin/env python3
"""Explicit shared-image lifecycle inspection and quarantine (never deletion).

Plan/why-kept prints a dry-run JSON receipt. Apply consumes that exact reviewed
receipt, revalidates roots/leases/identity, and requires external quiescence.
Quarantine does not reclaim disk space. Physical purge and grace policy remain
separately authorized migration work; there is deliberately no unattended GC.
"""
import argparse
import json
from pathlib import Path

from lib.runtime_image_lifecycle import (
    apply_retirement, forget_release, load_state, plan_retirement,
    select_release, transaction,
)
from lib.shared_runtime_images import _directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list exact object reports and retained release/lease records")
    for name in ("plan", "why-kept"):
        commands.add_parser(name).add_argument("--digest", required=True)
    apply = commands.add_parser("apply", help="quarantine only; requires an externally fenced maintenance window")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--maintenance-authorization", required=True,
                       help="operator/change identity asserting fenced admissions, no untracked jobs or aliases")
    forget = commands.add_parser("forget-release", help="explicitly unretain non-current release metadata")
    forget.add_argument("--release", required=True)
    select = commands.add_parser("select-release", help="select exact retained lane release/rollback")
    select.add_argument("--lane", choices=("development", "production"), required=True)
    select.add_argument("--release", required=True)
    args = parser.parse_args()
    if args.command in {"plan", "why-kept"}:
        result = plan_retirement(args.store_root, args.digest)
    elif args.command == "apply":
        result = {"quarantine": str(apply_retirement(args.store_root, json.loads(args.plan.read_text()),
                          maintenance_authorization=args.maintenance_authorization)), "reclaimed_bytes": 0}
    elif args.command == "forget-release":
        forget_release(args.store_root, args.release)
        result = {"forgotten_release": args.release}
    elif args.command == "select-release":
        select_release(args.store_root, args.lane, args.release)
        result = {"selected_release": args.release}
    else:
        # Individual plans are fresh snapshots; apply always revalidates anyway.
        with transaction(args.store_root) as root:
            state = load_state(root)
            with _directory(root / "objects" / "sha256") as fd:
                import os
                digests = sorted(d for d in os.listdir(fd) if not d.startswith(".quarantine-"))
        result = {"references": state, "objects": [plan_retirement(root, d) for d in digests]}
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
