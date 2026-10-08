#!/usr/bin/env python3
"""Verify the exact NGS/MolBio Phase N0 contract package."""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import rfc8785

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "platform/api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import ngs_molbio_capabilities as caps  # noqa: E402
from services.ngs_molbio_capabilities import NgsMolBioCapabilityError  # noqa: E402


def expect_rejected(label: str, callback) -> None:
    try:
        callback()
    except NgsMolBioCapabilityError:
        return
    raise AssertionError(f"expected rejection: {label}")


def event(payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "bms.ngs-molbio.connector-event.v1",
        "event_id": "evt-1",
        "global_domain_experiment_id": "domain-1",
        "binding_revision_id": "binding-1",
        "state_revision_id": "state-1",
        "source_store_id": "molbio",
        "event_stream": "state",
        "stream_generation": 1,
        "source_generation": 1,
        "event_type": "molbio_ngs.domain_state.initialized",
        "payload": payload,
        "payload_sha256": hashlib.sha256(rfc8785.dumps(payload)).hexdigest(),
        "occurred_at": "2026-08-13T05:28:16Z",
    }


def domain_experiment(groups: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema": "bms.domain-experiment.v2",
        "domain_kind": "ngs_molbio",
        "domain_contract_version": "2",
        "name": "Phase N0 verification",
        "objective": "Verify closed NGS/MolBio contract semantics.",
        "status": "draft",
        "tags": [],
        "source_receipt_ids": [],
        "dataset_revision_ids": [],
        "created_by": "phase-n0-verifier",
        "change_summary": "Verify semantic uniqueness.",
        "domain_payload": {
            "schema": "bms.ngs-molbio-experiment.v2",
            "experiment_mode": "sequencing",
            "scientific_objective": "Verify NGS/MolBio contract semantics.",
            "planned_capability_ids": [],
            "grouping_intent": groups,
            "acceptance_criteria": [],
            "evidence_plan": [],
        },
    }


def group(group_id: str, members: list[dict[str, object]]) -> dict[str, object]:
    return {"group_id": group_id, "label": group_id, "members": members}


def member(resource_id: str, ordinal: int) -> dict[str, object]:
    return {
        "member_kind": "receipt",
        "resource_id": resource_id,
        "role": "input",
        "ordinal": ordinal,
    }


def main() -> int:
    os.chdir(REPO_ROOT)
    inventory = caps.capability_inventory()
    adapters = caps.contract_registry("adapter")
    events = caps.contract_registry("event")
    datasets = caps.contract_registry("dataset")
    schemas = caps.contract_registry("schema")

    assert len(inventory["capabilities"]) == 21
    assert sum(1 for row in inventory["capabilities"] if row["plannable"]) == 0
    assert len(schemas["entries"]) == 59
    assert len(adapters["entries"]) == 19
    assert len(events["entries"]) == 9
    assert len(datasets["entries"]) == 6

    current_ont = next(
        row for row in adapters["entries"]
        if row["adapter_id"] == "bms.ngs.ont-run-reference.adapter.v1"
    )
    assert current_ont["allowed_dataset_roles"] == []
    assert current_ont["reopen_contract"]["head_resolution_forbidden"] is False

    payload = {
        "schema": "bms.molbio-ngs.domain-state-initialized.v1",
        "global_domain_experiment_id": "domain-1",
        "global_domain_experiment_revision_id": "domain-revision-1",
        "global_domain_experiment_revision_digest": "0" * 64,
        "project_id": "project-1",
        "project_generation": 1,
        "project_digest": "1" * 64,
        "global_experiment_id": "global-1",
        "global_experiment_generation": 1,
        "global_experiment_digest": "2" * 64,
    }
    caps.validate_connector_event(event(payload))

    wrong_event = event(payload)
    wrong_event["event_type"] = "unknown.event"
    expect_rejected("connector_unknown_event", lambda: caps.validate_connector_event(wrong_event))

    wrong_payload = event({"unexpected": True})
    expect_rejected("connector_payload_shape", lambda: caps.validate_connector_event(wrong_payload))

    wrong_digest = event(payload)
    wrong_digest["payload_sha256"] = "f" * 64
    expect_rejected("connector_payload_digest", lambda: caps.validate_connector_event(wrong_digest))

    wrong_time = event(payload)
    wrong_time["occurred_at"] = "invalid"
    expect_rejected("invalid_rfc3339_datetime", lambda: caps.validate_connector_event(wrong_time))

    duplicate_identity = domain_experiment([
        group("group-a", [member("receipt-a", 0), member("receipt-a", 1)])
    ])
    expect_rejected(
        "duplicate_semantic_identity",
        lambda: caps.validate_domain_experiment(duplicate_identity),
    )
    duplicate_ordinal = domain_experiment([
        group("group-a", [member("receipt-a", 0), member("receipt-b", 0)])
    ])
    expect_rejected(
        "duplicate_ordinal",
        lambda: caps.validate_domain_experiment(duplicate_ordinal),
    )

    protein = domain_experiment([])
    protein["domain_kind"] = "protein_in_silico"
    protein["domain_payload"] = {"schema": "bms.protein-in-silico-experiment.v2"}
    expect_rejected(
        "domain_unregistered_payload",
        lambda: caps.validate_domain_experiment(protein),
    )

    original_loaded = caps._loaded_documents
    def unsafe_current_head_dataset():
        result = original_loaded()
        current = next(
            row for row in result[3]["adapter"]["entries"]
            if row["adapter_id"] == "bms.ngs.ont-run-reference.adapter.v1"
        )
        current["allowed_dataset_roles"] = ["ngs_instrument_run"]
        caps._verify_adapter_identity_contract(current)
        return result
    expect_rejected("current_head_dataset_member", unsafe_current_head_dataset)

    incomplete = copy.deepcopy(next(
        row for row in adapters["entries"]
        if row["adapter_id"] == "bms.ngs.ont-observation.adapter.v1"
    ))
    incomplete["identity_contract"]["generation_fields"] = []
    expect_rejected(
        "adapter_identity_incomplete",
        lambda: caps._verify_adapter_identity_contract(incomplete),
    )

    original_import = importlib.import_module
    def missing_owner(name: str, *args, **kwargs):
        if name == "routers.molbio_ops":
            raise ImportError("synthetic missing owner")
        return original_import(name, *args, **kwargs)
    importlib.import_module = missing_owner
    try:
        expect_rejected("capability_owner_missing", caps.capability_inventory)
    finally:
        importlib.import_module = original_import

    original_path = caps._path
    source_authority = REPO_ROOT / "platform/api/services/ont_ngs_contract.py"
    drift_file = REPO_ROOT / ".hermes-ngs-n0-drift-probe"
    drift_file.write_bytes(source_authority.read_bytes() + b"\n")
    def drifted_path(relative: str) -> Path:
        if relative == "platform/api/services/ont_ngs_contract.py":
            return drift_file
        return original_path(relative)
    caps._path = drifted_path
    try:
        expect_rejected("installed_source_drift", caps.capability_inventory)
    finally:
        caps._path = original_path
        drift_file.unlink(missing_ok=True)

    receipt = json.loads(
        (REPO_ROOT / "docs/reports/ngs-molbio-phase-n0-verification-v1.json").read_text()
    )
    fingerprint = hashlib.sha256()
    for row in sorted(receipt["payload_files"], key=lambda item: item["path"]):
        fingerprint.update(f"{row['path']}\0{row['sha256']}\n".encode("utf-8"))
    assert fingerprint.hexdigest() == receipt["payload_fingerprint_sha256"]

    manifest_drift = REPO_ROOT / ".hermes-ngs-n0-payload-drift-probe"
    manifest_drift.write_bytes(b"drift")
    original_path = caps._path
    target_relative = "docs/reports/ngs-molbio-phase-n0-contract-freeze.md"
    def drifted_payload_path(relative: str) -> Path:
        if relative == target_relative:
            return manifest_drift
        return original_path(relative)
    caps._path = drifted_payload_path
    try:
        expect_rejected("payload_file_drift", caps.capability_inventory)
    finally:
        caps._path = original_path
        manifest_drift.unlink(missing_ok=True)

    output = {
        "closure": "pass",
        "capabilities": 21,
        "plannable": 0,
        "schemas": 59,
        "adapters": 19,
        "events": 9,
        "datasets": 6,
        "payload_fingerprint_sha256": receipt["payload_fingerprint_sha256"],
        "python": platform.python_version(),
        "git_head": subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
    }
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
