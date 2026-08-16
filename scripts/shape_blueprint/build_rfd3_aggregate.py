#!/usr/bin/env python3
"""Build one immutable global manifest from deterministic RFD3 child outcomes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value) if not isinstance(value, (bytes, bytearray)) else value).hexdigest()


def _validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("schema") != "bms_rfd3_batch_plan_v1" or plan.get("status") != "planned":
        raise ValueError("RFD3 aggregate requires a planned batch manifest")
    claimed = plan.get("batch_plan_sha256")
    unsigned = dict(plan)
    unsigned.pop("batch_plan_sha256", None)
    if not isinstance(claimed, str) or _sha256(unsigned) != claimed:
        raise ValueError("RFD3 batch plan hash mismatch")
    expected_total = int(plan["candidate_count_total"])
    observed = [candidate for batch in plan["batches"] for candidate in batch["candidates"]]
    if len(observed) != expected_total or len({candidate["candidate_id"] for candidate in observed}) != expected_total:
        raise ValueError("RFD3 batch plan candidate accounting is not unique and complete")
    if any(batch["candidate_count"] != len(batch["candidates"]) or batch["candidate_count"] > int(plan["child_batch_limit"]) for batch in plan["batches"]):
        raise ValueError("RFD3 child batch violates declared candidate limit")


def _expected_candidates(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for batch in plan["batches"]:
        for candidate in batch["candidates"]:
            expected[candidate["candidate_id"]] = {
                "candidate_id": candidate["candidate_id"],
                "candidate_index": candidate["candidate_index"],
                "local_candidate_index": candidate["local_candidate_index"],
                "batch_index": batch["batch_index"],
                "batch_id": batch["batch_id"],
                "length": batch["length"],
                "effective_seed": candidate["effective_seed"],
            }
    return expected


def _record_key(record: dict[str, Any]) -> str:
    candidate_id = record.get("candidate_id")
    if not isinstance(candidate_id, str) or len(candidate_id) != 64:
        raise ValueError("RFD3 admission record lacks a full candidate ID")
    return candidate_id


def build_aggregate(*, plan: dict[str, Any], admission_records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    _validate_plan(plan)
    expected = _expected_candidates(plan)
    records_by_id: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []
    unknown_ids: list[str] = []
    for record in admission_records:
        candidate_id = _record_key(record)
        if candidate_id not in expected:
            unknown_ids.append(candidate_id)
            continue
        if candidate_id in records_by_id:
            duplicate_ids.append(candidate_id)
            continue
        status = record.get("status")
        if status not in {"accepted", "rejected", "failed"}:
            raise ValueError(f"RFD3 admission record has invalid status for {candidate_id}")
        records_by_id[candidate_id] = record
    outcomes: list[dict[str, Any]] = []
    for candidate_id, identity in sorted(expected.items(), key=lambda item: item[1]["candidate_index"]):
        record = records_by_id.get(candidate_id)
        outcome = dict(identity)
        outcome["status"] = record.get("status", "missing") if record else "missing"
        outcome["candidate_sha256"] = record.get("candidate_sha256") if record else None
        outcome["metrics"] = dict(record.get("metrics") or {}) if record else {}
        outcome["reason"] = record.get("reason") if record else {"code": "admission_missing", "message": "no admission record was emitted"}
        outcome["admission_artifact_sha256"] = record.get("admission_artifact_sha256") if record else None
        outcomes.append(outcome)
    accepted = [outcome for outcome in outcomes if outcome["status"] == "accepted"]
    rejected = [outcome for outcome in outcomes if outcome["status"] == "rejected"]
    failed = [outcome for outcome in outcomes if outcome["status"] == "failed"]
    missing = [outcome for outcome in outcomes if outcome["status"] == "missing"]
    ranked = sorted(
        accepted,
        key=lambda outcome: (
            float(outcome["metrics"].get("cad_bidirectional_mean_angstrom", float("inf"))),
            float(outcome["metrics"].get("cad_sdf_outside_fraction", float("inf"))),
            outcome["candidate_id"],
        ),
    )
    ranked = [{"rank": rank, **outcome} for rank, outcome in enumerate(ranked, start=1)]
    accounting = {
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "failed_count": len(failed),
        "missing_count": len(missing),
        "unknown_count": len(unknown_ids),
        "duplicate_count": len(duplicate_ids),
        "accounted_count": len(accepted) + len(rejected) + len(failed),
    }
    if accounting["missing_count"] or accounting["unknown_count"] or accounting["duplicate_count"]:
        status = "incomplete"
    elif not accepted:
        status = "no_yield"
    else:
        status = "complete"
    aggregate = {
        "schema": "bms_rfd3_aggregate_manifest_v1",
        "status": status,
        "request_sha256": plan["request_sha256"],
        "geometry_sha256": plan.get("geometry_sha256"),
        "batch_plan_sha256": plan["batch_plan_sha256"],
        "candidate_count_total": plan["candidate_count_total"],
        "child_batch_limit": plan["child_batch_limit"],
        "allocation_policy_id": plan["allocation_policy_id"],
        "allocation_policy_sha256": plan["allocation_policy_sha256"],
        "resource_admission": plan["resource_admission"],
        "accounting": accounting,
        "unknown_candidate_ids": sorted(unknown_ids),
        "duplicate_candidate_ids": sorted(duplicate_ids),
        "candidate_outcomes": outcomes,
        "ranked_candidates": ranked,
    }
    aggregate["aggregate_sha256"] = _sha256(aggregate)
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--admission-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(args.admission_dir.glob("*.json"))]
    aggregate = build_aggregate(plan=plan, admission_records=records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(_canonical(aggregate) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
