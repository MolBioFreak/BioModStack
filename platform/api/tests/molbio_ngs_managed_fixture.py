"""Isolated Domain fixtures at the managed connector's receipt-consumer boundary.

These fixtures do not qualify the global issuer. The real connector validates the
receipt schema/digest and writes binding, acknowledgement and ordered events with
all native SQL constraints enabled; no production authority is monkeypatched.
"""
from __future__ import annotations

import hashlib
import json
import uuid


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def initialize_managed_domain(session, binding, *, idempotency_key, created_by=None):
    from experiment_models import ExperimentDomainConnectorCommand
    from molbio_ngs_services import initialize_domain_state
    from services.ngs_molbio_connector import BINDING_ADAPTER_ID, _append_local_binding

    domain_id = binding.global_domain_experiment_id
    command_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"fixture:{domain_id}:{idempotency_key}"))
    receipt_id = f"fixture-receipt-{command_id}"
    receipt = {
        "schema": "bms.ngs-molbio.global-binding-receipt.v1",
        "receipt_id": receipt_id,
        "project": {
            "id": binding.project_id, "revision_id": f"{binding.project_id}-revision",
            "generation": int(binding.project_generation), "digest": binding.project_digest,
            "reopen_destination": f"/projects/{binding.project_id}",
        },
        "global_experiment": {
            "id": binding.global_experiment_id,
            "revision_id": f"{binding.global_experiment_id}-revision",
            "generation": int(binding.global_experiment_generation),
            "digest": binding.global_experiment_digest,
            "reopen_destination": f"/projects/{binding.project_id}?focus={binding.global_experiment_id}",
        },
        "domain_experiment": {
            "id": domain_id, "revision_id": binding.global_domain_experiment_revision_id,
            "generation": 1, "digest": binding.global_domain_experiment_revision_digest,
            "reopen_destination": f"/projects/{binding.project_id}?selected=domain:{domain_id}",
            "lifecycle_state": "active", "domain_kind": "ngs_molbio", "domain_contract_version": "2",
        },
        "adapter_id": BINDING_ADAPTER_ID, "adapter_version": "1",
        "verified_at": binding.verified_at, "acknowledgement": {"status": "verified"},
    }
    receipt_json = canonical(receipt)
    command = ExperimentDomainConnectorCommand(
        command_id=command_id, operation="initialize", project_id=binding.project_id,
        global_experiment_id=binding.global_experiment_id, domain_experiment_id=domain_id,
        domain_revision_id=binding.global_domain_experiment_revision_id,
        domain_revision_sha256=binding.global_domain_experiment_revision_digest,
        global_receipt_id=receipt_id,
        global_receipt_sha256=hashlib.sha256(receipt_json.encode()).hexdigest(),
    )
    ack, state = await _append_local_binding(session, command, receipt_json)
    assert ack.binding_revision_id == state.current_binding_revision_id
    assert json.loads(ack.acknowledgement_json)["accepted_payload_sha256"] == command.global_receipt_sha256
    # Exercise the surviving compatibility/idempotency path only after governed setup.
    return await initialize_domain_state(
        session, binding, idempotency_key=idempotency_key, created_by=created_by
    )


def seed_migrated_domain(connection, domain_id, revision_id):
    """Complete v4 migrated binding/state cycle in its native deferred transaction."""
    binding_id = f"{domain_id}-binding"
    connection.execute(
        """INSERT INTO molbio_ngs_global_binding_revisions (
            binding_revision_id, global_domain_experiment_id, revision_number,
            global_domain_experiment_revision_id, global_domain_experiment_revision_digest,
            project_id, project_generation, project_digest, project_receipt_id,
            project_reopen_destination, project_acknowledgement,
            global_experiment_id, global_experiment_generation, global_experiment_digest,
            global_experiment_receipt_id, global_experiment_reopen_destination,
            global_experiment_acknowledgement, binding_state, created_at
        ) VALUES (?, ?, 1, ?, ?, 'project-1', '3', ?, 'project-receipt-1', '{}', '{}',
                  'global-experiment-1', '2', ?, 'experiment-receipt-1', '{}', '{}',
                  'needs_reverification', '2026-08-08T00:00:00Z')""",
        (binding_id, domain_id, revision_id, "a" * 64, "b" * 64, "c" * 64),
    )
    connection.execute(
        """INSERT INTO molbio_ngs_domain_states (
            global_domain_experiment_id, current_binding_revision_id,
            head_generation, created_at, updated_at
        ) VALUES (?, ?, 0, '2026-08-08T00:00:00Z', '2026-08-08T00:00:00Z')""",
        (domain_id, binding_id),
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    return binding_id
