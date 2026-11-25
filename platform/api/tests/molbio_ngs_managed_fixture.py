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
    from molbio_ngs_services import initialize_domain_state

    return await initialize_domain_state(
        session, binding, idempotency_key=idempotency_key, created_by=created_by
    )


def seed_migrated_domain(connection, domain_id, revision_id):
    """Seed a v6 MolBio/NGS domain state row without connector binding tables."""
    binding_id = f"{domain_id}-binding"
    connection.execute(
        """INSERT INTO molbio_ngs_domain_states (
            global_domain_experiment_id, project_id, global_experiment_id,
            global_domain_experiment_revision_id, binding_receipt_id,
            head_generation, created_at, updated_at
        ) VALUES (?, 'project-1', 'global-experiment-1', ?, ?, 0,
                  '2026-08-08T00:00:00Z', '2026-08-08T00:00:00Z')""",
        (domain_id, revision_id, binding_id),
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    return binding_id
