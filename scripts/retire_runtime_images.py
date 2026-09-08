#!/usr/bin/env python3
"""Explicit shared-image retirement; no unattended garbage collection.

CAS plan/apply only quarantines unretained objects. Legacy-plan takes one exact
external --source, expected --digest and repeatable --survivor paths. Review its
JSON, then legacy-apply --plan FILE under an externally fenced maintenance window.
It renames in place, reclaiming zero bytes, and emits a durable receipt. Review
that receipt before explicit legacy-purge --receipt FILE, which rehashes all
survivors and the quarantined file before unlinking that single allocation.
--remove-empty-snapshot-directory binds removal of only the known private NGS
parent (containing runtime.sif alone), using rmdir, never recursive deletion.
Both mutations require --maintenance-authorization CHANGE_ID; this assertion is
NOT proof of fenced admissions, quiescent jobs/qualification or absent aliases.
Interrupted operations fail closed and retain evidence for manual reconciliation.
"""
import argparse
import json
from pathlib import Path

from lib.runtime_image_lifecycle import (
    apply_retirement, forget_release, load_state, plan_retirement,
    select_release, transaction, plan_legacy_retirement, apply_legacy_retirement,
    purge_legacy_retirement, _read, _unique_keys,
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
    legacy = commands.add_parser("legacy-plan", help="review one external redundant allocation and preserved survivors")
    legacy.add_argument("--source", type=Path, required=True)
    legacy.add_argument("--survivor", type=Path, action="append", required=True)
    legacy.add_argument("--digest", required=True)
    legacy.add_argument("--remove-empty-snapshot-directory", action="store_true")
    for name, option in (("legacy-apply", "--plan"), ("legacy-purge", "--receipt")):
        command = commands.add_parser(name)
        command.add_argument(option, type=Path, required=True)
        command.add_argument("--maintenance-authorization", required=True,
                             help="externally established admissions/qualification/job/alias fence identity")
    args = parser.parse_args()
    if args.command == "legacy-plan":
        result = plan_legacy_retirement(args.store_root, args.source, args.survivor, args.digest,
            remove_directory=args.remove_empty_snapshot_directory)
    elif args.command in {"legacy-apply", "legacy-purge"}:
        path = args.plan if args.command == "legacy-apply" else args.receipt
        evidence = json.loads(_read(path), object_pairs_hook=_unique_keys)
        action = apply_legacy_retirement if args.command == "legacy-apply" else purge_legacy_retirement
        result = action(args.store_root, evidence,
                        maintenance_authorization=args.maintenance_authorization)
    elif args.command in {"plan", "why-kept"}:
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
