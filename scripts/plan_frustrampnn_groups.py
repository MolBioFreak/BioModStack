#!/usr/bin/env python3
"""Materialize Nextflow group inputs through the shared local/worker authority.

This step plans only. Canonical runners validate each complete scientific input
closure before inference. Its ledger deliberately does not claim science success.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import hashlib
import stat
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))
from component_runtime import GroupingLedger, canonical_bytes, durable_write, plan_frustrampnn

REQUEST = "workflow_component_request_v3.json"


def tree_authority(root: Path) -> dict:
    """Bind names, directory shape and bytes, not physical staging placement.

    A source root may be a Nextflow staging link; nested links/special files
    are not an artifact closure and are rejected rather than dereferenced.
    """
    if not root.is_dir():
        raise ValueError("materialization source is unavailable")
    entries = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        name = path.relative_to(root).as_posix()
        if stat.S_ISDIR(mode):
            entries[name] = {"kind": "directory"}
        elif stat.S_ISREG(mode):
            payload = path.read_bytes()
            entries[name] = {"kind": "file", "size": len(payload),
                             "sha256": hashlib.sha256(payload).hexdigest()}
        else:
            raise ValueError("materialization contains an unsafe entry")
    return entries


def immutable_write(path: Path, payload: bytes) -> None:
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != payload)):
        raise ValueError("materialization authority conflicts")
    if not path.exists():
        durable_write(path, payload)


def reconcile_tree(source: Path, destination: Path, authority: dict, *, staging_root: Path) -> None:
    """Recover missing entries only; never overwrite a conflicting published byte.

    Each file is atomically published. A crash between files leaves a verifiable
    subset, not a truncated published file. Legacy truncated copies fail closed.
    """
    if tree_authority(source) != authority:
        raise ValueError("materialization source authority conflicts")
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise ValueError("materialization destination is unsafe")
    if destination.exists():
        existing = tree_authority(destination)
        if any(authority.get(name) != value for name, value in existing.items()):
            raise ValueError("materialization published bytes conflict")
    destination.mkdir(parents=True, exist_ok=True)
    for name, entry in authority.items():
        target = destination / name
        if entry["kind"] == "directory":
            target.mkdir(exist_ok=True)
        elif not target.exists():
            payload = (source / name).read_bytes()
            if len(payload) != entry["size"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError("materialization source authority conflicts")
            # Unpublished files live outside the authoritative tree. Even a hard
            # kill during write leaves only disposable staging, never bad output.
            if staging_root.is_symlink():
                raise ValueError("materialization staging is unsafe")
            staged = staging_root / uuid.uuid4().hex
            durable_write(staged, payload)
            os.replace(staged, target)
            fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    if tree_authority(destination) != authority:
        raise ValueError("materialization publication conflicts")


def materialize_groups(directories, output_root: Path, *, attempt_id: str):
    if output_root.is_symlink():
        raise ValueError("materialization output root is unsafe")
    records = []
    by_id = {}
    for directory in map(Path, directories):
        payload = (directory / REQUEST).read_bytes()
        record = json.loads(payload)
        if payload != canonical_bytes(record):
            raise ValueError("prepared request must be canonical JSON")
        records.append({**record, "materialized_tree": tree_authority(directory)})
        by_id[record["candidate_id"]] = directory
    if not records:
        raise ValueError("terminal structure dataset is empty")
    plan = plan_frustrampnn(records, records[0]["requested_settings"])
    # Persist the exact expansion BEFORE publishing any group input. Retry may
    # reconcile the same authority but never reinterpret a changed plan.
    ledger = GroupingLedger(output_root / "components.sqlite", attempt_id=attempt_id, plan=plan)
    ledger.check_active()
    immutable_write(output_root / "grouping_plan_v1.json", canonical_bytes(plan.payload))
    expected_groups = {f"group_{i:06d}" for i in range(len(plan.groups))}
    if any(p.name not in expected_groups for p in output_root.glob("group_*")):
        raise ValueError("materialization contains a foreign group")
    authorities = {r["candidate_id"]: r["materialized_tree"] for r in records}
    for ordinal, members in enumerate(plan.groups):
        destination = output_root / f"group_{ordinal:06d}"
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise ValueError("materialization group is unsafe")
        destination.mkdir(exist_ok=True)
        expected = {f"candidate_{i:06d}" for i in range(len(members))}
        if any(p.name not in expected for p in destination.iterdir()):
            raise ValueError("materialization contains a foreign candidate")
        for index, member in enumerate(members):
            reconcile_tree(by_id[member.candidate_id], destination / f"candidate_{index:06d}",
                           authorities[member.candidate_id], staging_root=output_root / ".staging")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, action="append", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("groups"))
    args = parser.parse_args()
    attempt = os.environ.get("BMS_REMOTE_ATTEMPT_ID")
    if not attempt:
        raise ValueError("remote attempt identity is required")
    materialize_groups(args.candidate_dir, args.output_root, attempt_id=attempt)


if __name__ == "__main__":
    main()
