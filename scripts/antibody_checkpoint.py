#!/usr/bin/env python3
"""Antibody review publication, not review authorization or scientific inference.

The host stages the trusted decision after native review. No polling, API, DB,
implicit approval, or computation-complete receipt is emitted at this boundary.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))
from component_runtime import ResultReference, canonical_bytes, digest, durable_write


def seal_review(*, job_id: str, stage: str, sources: list[Path], annotations: list[Path],
                settings: dict, output: Path, native_artifacts: list[Path] | None = None) -> dict:
    if not job_id or not stage or not sources or not annotations:
        raise ValueError("review requires job, stage, structures and native annotations")
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    publications = []
    seen = set()
    for role, files in (("structure", sources), ("annotation", annotations), ("native_analysis", native_artifacts or [])):
        for source in sorted(map(Path, files), key=lambda p: p.name):
            source = source.resolve(strict=True)
            key = f"review/{role}/{source.name}"
            if key in seen:
                raise ValueError(f"duplicate review artifact identity: {key}")
            seen.add(key)
            payload = source.read_bytes()
            destination = output / key
            publications.append((destination, payload))
            ref = ResultReference(job_id, key, hashlib.sha256(payload).hexdigest(), len(payload),
                                  "pdb" if role == "structure" else "anarcii.native.v1" if role == "annotation" else "antibody.native-analysis.v1")
            artifacts.append({"role": role, **asdict(ref)})
    receipt = {"schema_name": "bms.antibody-checkpoint.v1", "schema_version": 1,
               "job_id": job_id, "workflow_id": "antibody_denovo", "stage": stage,
               "status": "awaiting_review", "science_complete": False,
               "settings_sha256": digest(settings), "settings": settings, "artifacts": artifacts}
    receipt["checkpoint_id"] = digest(receipt)
    sealed = output / "checkpoint.json"
    if sealed.exists() and json.loads(sealed.read_text()) != receipt:
        raise ValueError("immutable checkpoint already contains different authority")
    for destination, payload in publications:
        durable_write(destination, payload)
    durable_write(sealed, canonical_bytes(receipt))
    return receipt


def verify_decision(checkpoint: Path, decision: Path, selected_dir: Path) -> dict:
    receipt = json.loads(checkpoint.read_text())
    identity = receipt.pop("checkpoint_id")
    if digest(receipt) != identity or receipt.get("schema_name") != "bms.antibody-checkpoint.v1":
        raise ValueError("checkpoint identity mismatch")
    choice = json.loads(decision.read_text())
    if (choice.get("schema_name") != "bms.antibody-checkpoint-decision.v1"
            or choice.get("checkpoint_id") != identity
            or choice.get("decision") != "continue"
            or not isinstance(choice.get("authorized_by"), str)
            or not choice["authorized_by"].strip()
            or choice.get("job_id") != receipt["job_id"]
            or choice.get("stage") != receipt["stage"]):
        raise ValueError("explicit checkpoint-bound authorized continuation required")
    selected = choice.get("selected_artifacts")
    if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected):
        raise ValueError("nonempty exact selected artifact identities required")
    structures = {a["relative_path"]: a for a in receipt["artifacts"] if a["role"] == "structure"}
    if not set(selected) <= structures.keys():
        raise ValueError("foreign selected artifact")
    expected = {}
    for key in selected:
        item = dict(structures[key]); item.pop("role")
        ref = ResultReference(**item)
        ref.resolve(checkpoint.parent)
        expected[Path(key).name] = (ref.sha256, ref.size_bytes)
    observed = {}
    for path in selected_dir.glob("*.pdb"):
        data = path.read_bytes()
        observed[path.name] = (hashlib.sha256(data).hexdigest(), len(data))
    if observed != expected:
        raise ValueError("continuation inputs differ from the selected review artifacts")
    return {"checkpoint_id": identity, "job_id": receipt["job_id"], "stage": receipt["stage"],
            "selected_artifacts": selected}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("seal")
    seal.add_argument("--manifest", type=Path, required=True)
    seal.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--checkpoint", type=Path, required=True)
    verify.add_argument("--decision", type=Path, required=True)
    verify.add_argument("--selected-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "seal":
        request = json.loads(args.manifest.read_text())
        seal_review(output=args.output, **request)
    else:
        print(json.dumps(verify_decision(args.checkpoint, args.decision, args.selected_dir)))


if __name__ == "__main__":
    main()
