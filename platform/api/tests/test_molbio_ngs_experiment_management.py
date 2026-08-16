from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _state_payload() -> dict[str, object]:
    return {
        "schema": "bms.molbio-ngs.domain-state-revision.v1",
        "design": {
            "sample_revision_ids": [],
            "conditions": [],
            "replicates": [],
            "expected_molecule_roles": ["molecular_expected_construct"],
        },
        "reference_policy": {
            "required_roles": ["molecular_expected_construct"],
            "coordinate_policy": "exact_revision",
        },
        "acquisition_policy": {
            "platform": "ont",
            "required_terminal_manifest": True,
        },
        "analysis_policy": {
            "allowed_workflow_ids": ["ont_plasmid_qc"],
            "required_manifest_schemas": ["biomodstack.construct_verification.v2"],
        },
        "assessment_policy": {
            "rule_id": "server-owned-rule",
            "completion_is_scientific_pass": False,
        },
        "notes": "Exact immutable state for the focused fixture.",
    }


def test_domain_store_migration_installs_pragmas_digest_guards_and_immutability(
    tmp_path: Path,
):
    from migrations.sqlite_sha256 import register_sqlite_sha256
    from molbio_ngs_migrations import health, run_all

    db_path = tmp_path / "molbio_ngs.db"
    run_all(db_path)
    report = health(db_path)
    assert report["journal_mode"].lower() == "wal"
    assert report["foreign_keys"] is True
    assert report["synchronous"] == 2
    assert report["attestation"]["ok"] is True

    connection = sqlite3.connect(db_path)
    register_sqlite_sha256(connection)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(
            """
            INSERT INTO molbio_ngs_domain_states(
                global_domain_experiment_id, head_generation, created_at, updated_at
            ) VALUES ('domain-1', 0, '2026-08-08T00:00:00Z', '2026-08-08T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO molbio_ngs_global_bindings(
                global_domain_experiment_id,
                global_domain_experiment_revision_id,
                global_domain_experiment_revision_digest,
                project_id, project_generation, project_digest, project_receipt_id,
                project_reopen_destination, project_acknowledgement,
                global_experiment_id, global_experiment_generation,
                global_experiment_digest, global_experiment_receipt_id,
                global_experiment_reopen_destination, global_experiment_acknowledgement,
                binding_state, created_at
            ) VALUES (
                'domain-1', 'global-domain-rev-1', ?,
                'project-1', '3', ?, 'project-receipt-1', ?, '{}',
                'global-experiment-1', '2', ?, 'experiment-receipt-1', ?, '{}',
                'acknowledged', '2026-08-08T00:00:00Z'
            )
            """,
            (
                "a" * 64,
                "b" * 64,
                _canonical({"surface": "project", "params": {"project_id": "project-1"}}),
                "c" * 64,
                _canonical(
                    {
                        "surface": "global-experiment",
                        "params": {
                            "project_id": "project-1",
                            "experiment_id": "global-experiment-1",
                        },
                    }
                ),
            ),
        )
        payload = _canonical(
            {
                "schema": "bms.molbio-ngs.domain-state-revision.v1",
                "design": {"sample_revision_ids": []},
            }
        )
        with pytest.raises(sqlite3.IntegrityError, match="state revision payload digest"):
            connection.execute(
                """
                INSERT INTO molbio_ngs_domain_state_revisions(
                    id, global_domain_experiment_id,
                    global_domain_experiment_revision_id, revision_number,
                    schema_name, schema_version, canonical_payload, payload_sha256,
                    membership_graph_sha256, created_at
                ) VALUES (
                    'state-rev-bad', 'domain-1', 'global-domain-rev-1', 1,
                    'bms.molbio-ngs.domain-state-revision', '1', ?, ?, ?,
                    '2026-08-08T00:00:00Z'
                )
                """,
                (payload, "0" * 64, _sha("[]")),
            )
        connection.execute(
            """
            INSERT INTO molbio_ngs_domain_state_revisions(
                id, global_domain_experiment_id,
                global_domain_experiment_revision_id, revision_number,
                schema_name, schema_version, canonical_payload, payload_sha256,
                membership_graph_sha256, created_at
            ) VALUES (
                'state-rev-1', 'domain-1', 'global-domain-rev-1', 1,
                'bms.molbio-ngs.domain-state-revision', '1', ?, ?, ?,
                '2026-08-08T00:00:00Z'
            )
            """,
            (payload, _sha(payload), _sha("[]")),
        )
        connection.execute(
            """
            UPDATE molbio_ngs_domain_states
               SET current_state_revision_id='state-rev-1', head_generation=1
             WHERE global_domain_experiment_id='domain-1'
            """
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="state revision is immutable"):
            connection.execute(
                "UPDATE molbio_ngs_domain_state_revisions SET canonical_payload='{}' WHERE id='state-rev-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="state revision is immutable"):
            connection.execute("DELETE FROM molbio_ngs_domain_state_revisions WHERE id='state-rev-1'")
        authority_updates = {
            "global_domain_experiment_id": "domain-other",
            "global_domain_experiment_revision_id": "global-domain-rev-other",
            "global_domain_experiment_revision_digest": "1" * 64,
            "project_id": "project-other",
            "project_generation": "4",
            "project_digest": "2" * 64,
            "project_receipt_id": "project-receipt-other",
            "project_reopen_destination": _canonical({"surface": "project", "params": {"project_id": "project-other"}}),
            "project_acknowledgement": _canonical({"ack": "other"}),
            "global_experiment_id": "global-experiment-other",
            "global_experiment_generation": "3",
            "global_experiment_digest": "3" * 64,
            "global_experiment_receipt_id": "experiment-receipt-other",
            "global_experiment_reopen_destination": _canonical({"surface": "global-experiment", "params": {"experiment_id": "other"}}),
            "global_experiment_acknowledgement": _canonical({"ack": "other"}),
            "created_at": "2026-08-08T00:00:01Z",
        }
        for column, value in authority_updates.items():
            with pytest.raises(sqlite3.IntegrityError, match="global binding authority is immutable"):
                connection.execute(
                    f"UPDATE molbio_ngs_global_bindings SET {column}=? WHERE global_domain_experiment_id='domain-1'",
                    (value,),
                )
        connection.execute(
            """
            UPDATE molbio_ngs_global_bindings
               SET binding_state='degraded', last_verified_at='2026-08-08T00:00:01Z',
                   last_error='verification unavailable', updated_at='2026-08-08T00:00:01Z'
             WHERE global_domain_experiment_id='domain-1'
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="domain state current revision"):
            connection.execute(
                """
                UPDATE molbio_ngs_domain_states
                   SET current_state_revision_id='state-rev-1', head_generation=2
                 WHERE global_domain_experiment_id='domain-1'
                """
            )

        sample_payload = _canonical({"schema": "bms.molbio-ngs.sample-revision.v1", "name": "sample-a"})
        connection.execute(
            """
            INSERT INTO molbio_ngs_samples(id, global_domain_experiment_id, head_generation, created_at, updated_at)
            VALUES ('sample-a', 'domain-1', 0, '2026-08-08T00:00:00Z', '2026-08-08T00:00:00Z'),
                   ('sample-b', 'domain-1', 0, '2026-08-08T00:00:00Z', '2026-08-08T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO molbio_ngs_sample_revisions(
                id, sample_id, global_domain_experiment_id, revision_number,
                schema_name, schema_version, canonical_payload, payload_sha256, created_at
            ) VALUES ('sample-rev-a', 'sample-a', 'domain-1', 1,
                      'bms.molbio-ngs.sample-revision', '1', ?, ?, '2026-08-08T00:00:00Z')
            """,
            (sample_payload, _sha(sample_payload)),
        )
        with pytest.raises(sqlite3.IntegrityError, match="sample current revision"):
            connection.execute(
                "UPDATE molbio_ngs_samples SET current_revision_id='sample-rev-a', head_generation=1 WHERE id='sample-b'"
            )

        reference_payload = _canonical(
            {
                "schema": "bms.molbio-ngs.reference-revision.v1",
                "reference_id": "reference-a",
                "revision_number": 1,
                "canonical_fasta": {"sha256": "4" * 64, "size_bytes": 1},
                "contig_manifest_sha256": "5" * 64,
            }
        )
        connection.execute(
            """
            INSERT INTO molbio_ngs_reference_resources(
                id, global_domain_experiment_id, name, head_generation, created_at, updated_at
            ) VALUES ('reference-a', 'domain-1', 'Reference A', 0, '2026-08-08T00:00:00Z', '2026-08-08T00:00:00Z'),
                     ('reference-b', 'domain-1', 'Reference B', 0, '2026-08-08T00:00:00Z', '2026-08-08T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO molbio_ngs_reference_artifacts(
                id, reference_id, sha256, size_bytes, media_type,
                managed_relative_path, created_at
            ) VALUES ('artifact-a', 'reference-a', ?, 1, 'text/x-fasta; charset=us-ascii',
                      'sha256/aa/artifact-a.fasta', '2026-08-08T00:00:00Z')
            """,
            ("4" * 64,),
        )
        connection.execute(
            """
            INSERT INTO molbio_ngs_reference_revisions(
                id, reference_id, global_domain_experiment_id, revision_number,
                schema_name, schema_version, canonical_payload, payload_sha256,
                artifact_id, canonical_fasta_sha256, canonical_fasta_size_bytes,
                contig_manifest_sha256, molecule_type, topology, coordinate_contract,
                source_provenance, created_at
            ) VALUES ('reference-rev-a', 'reference-a', 'domain-1', 1,
                      'bms.molbio-ngs.reference-revision', '1', ?, ?, 'artifact-a', ?, 1,
                      ?, 'dna', 'linear', 'zero-based-half-open', '{}', '2026-08-08T00:00:00Z')
            """,
            (reference_payload, _sha(reference_payload), "4" * 64, "5" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError, match="reference current revision"):
            connection.execute(
                "UPDATE molbio_ngs_reference_resources SET current_revision_id='reference-rev-a', head_generation=1 WHERE id='reference-b'"
            )

        canonical_receipt = _canonical(
            {
                "schema": "bms.molbio-ngs.external-member-receipt.v1",
                "receipt_id": "receipt-trigger-proof",
                "source_store_id": "molbio",
                "entity_kind": "molecular_revision",
                "entity_id": "canonical-revision",
                "source_generation_or_revision": "1",
                "content_digest": "d" * 64,
                "source_schema": "bms.molbio.molecular-revision.v1",
                "availability": "available",
                "reopen_destination": {
                    "params": {"revision_id": "canonical-revision", "sequence_id": "sequence-1"},
                    "surface": "molbio-sequence-revision",
                },
                "created_at": "2026-08-08T00:00:00Z",
            }
        )
        with pytest.raises(sqlite3.IntegrityError, match="member receipt authority mismatch"):
            connection.execute(
                """
                INSERT INTO molbio_ngs_member_receipts(
                    receipt_id, source_store_id, entity_kind, entity_id,
                    source_generation_or_revision, content_digest, schema_name,
                    schema_version, availability, reopen_destination,
                    canonical_receipt, receipt_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "receipt-trigger-proof", "molbio", "molecular_revision",
                    "divergent-revision", "1", "d" * 64,
                    "bms.molbio-ngs.external-member-receipt", "1", "available",
                    _canonical(
                        {
                            "params": {
                                "revision_id": "canonical-revision",
                                "sequence_id": "sequence-1",
                            },
                            "surface": "molbio-sequence-revision",
                        }
                    ),
                    canonical_receipt, _sha(canonical_receipt),
                    "2026-08-08T00:00:00Z",
                ),
            )

        connection.execute(
            """
            INSERT INTO molbio_ngs_idempotency_claims(
                scope, idempotency_key, status, request_sha256,
                result_resource_id, response_json, created_at, completed_at
            ) VALUES ('proof', 'claim-1', 'pending', ?, 'result-1', NULL, ?, NULL)
            """,
            ("e" * 64, "2026-08-08T00:00:00Z"),
        )
        with pytest.raises(sqlite3.IntegrityError, match="idempotency claim identity is immutable"):
            connection.execute(
                "UPDATE molbio_ngs_idempotency_claims SET request_sha256=? WHERE scope='proof'",
                ("f" * 64,),
            )
        connection.execute(
            """
            UPDATE molbio_ngs_idempotency_claims
               SET status='completed', response_json='{"result_resource_id":"result-1"}',
                   completed_at='2026-08-08T00:00:01Z'
             WHERE scope='proof' AND idempotency_key='claim-1'
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="completed idempotency claim is immutable"):
            connection.execute(
                "UPDATE molbio_ngs_idempotency_claims SET response_json='{}' WHERE scope='proof'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="idempotency claim deletion is forbidden"):
            connection.execute(
                "DELETE FROM molbio_ngs_idempotency_claims WHERE scope='proof'"
            )

        pointer_trigger_name = "trg_molbio_ngs_sample_current_revision_validate_update"
        pointer_trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
            (pointer_trigger_name,),
        ).fetchone()[0]
        connection.execute(f'DROP TRIGGER "{pointer_trigger_name}"')
        connection.execute(
            "UPDATE molbio_ngs_samples SET current_revision_id='sample-rev-a', head_generation=1 WHERE id='sample-b'"
        )
        connection.execute(pointer_trigger_sql)
        connection.commit()
    finally:
        connection.close()

    degraded = health(db_path)
    degraded_attestation = degraded["attestation"]
    assert isinstance(degraded_attestation, dict)
    assert degraded["status"] == "degraded"
    assert degraded_attestation["authority_coherence_errors"]


def test_domain_store_migration_rejects_tampered_ledger(tmp_path: Path):
    from molbio_ngs_migrations import run_all

    db_path = tmp_path / "molbio_ngs.db"
    run_all(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE molbio_ngs_schema_migrations SET checksum = ? WHERE version = 1",
            ("0" * 64,),
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="migration ledger mismatch"):
        run_all(db_path)


def test_online_backup_atomic_restore_and_exact_attestation(tmp_path: Path, monkeypatch):
    from migrations.sqlite_sha256 import register_sqlite_sha256
    from molbio_ngs_migrations import backup_database, health, restore_database, run_all

    source = tmp_path / "source.db"
    backup = tmp_path / "backup.db"
    target = tmp_path / "restored.db"
    reference_root = tmp_path / "managed-reference-root"
    managed_relative_path = "sha256/ab/backup-reference.fasta"
    artifact_bytes = b">backup-reference\nACGT\n"
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    artifact_path = reference_root / managed_relative_path
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(artifact_bytes)
    monkeypatch.setenv("BMS_MOLBIO_NGS_REFERENCE_ROOT", str(reference_root))
    run_all(source)
    canonical_receipt = _canonical(
        {
            "schema": "bms.molbio-ngs.external-member-receipt.v1",
            "receipt_id": "backup-receipt-1",
            "source_store_id": "molbio",
            "entity_kind": "molecular_revision",
            "entity_id": "molecular-revision-1",
            "source_generation_or_revision": "1",
            "content_digest": "d" * 64,
            "source_schema": "bms.molbio.molecular-revision.v1",
            "availability": "available",
            "reopen_destination": {
                "surface": "molbio-sequence-revision",
                "params": {"sequence_id": "sequence-1", "revision_id": "molecular-revision-1"},
            },
            "created_at": "2026-08-08T00:00:00Z",
        }
    )
    with sqlite3.connect(source) as connection:
        register_sqlite_sha256(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO molbio_ngs_domain_states(
                global_domain_experiment_id, head_generation, created_at, updated_at
            ) VALUES ('backup-domain', 0, ?, ?)
            """,
            ("2026-08-08T00:00:00Z", "2026-08-08T00:00:00Z"),
        )
        reference_payload = _canonical(
            {
                "schema": "bms.molbio-ngs.reference-revision.v1",
                "reference_id": "backup-reference",
                "revision_number": 1,
                "canonical_fasta": {
                    "sha256": artifact_sha256,
                    "size_bytes": len(artifact_bytes),
                },
                "contig_manifest_sha256": "9" * 64,
            }
        )
        connection.execute(
            """
            INSERT INTO molbio_ngs_reference_resources(
                id, global_domain_experiment_id, name, head_generation, created_at, updated_at
            ) VALUES ('backup-reference', 'backup-domain', 'Backup reference', 0, ?, ?)
            """,
            ("2026-08-08T00:00:00Z", "2026-08-08T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO molbio_ngs_reference_artifacts(
                id, reference_id, sha256, size_bytes, media_type,
                managed_relative_path, created_at
            ) VALUES ('backup-artifact', 'backup-reference', ?, ?,
                      'text/x-fasta; charset=us-ascii', ?, ?)
            """,
            (
                artifact_sha256,
                len(artifact_bytes),
                managed_relative_path,
                "2026-08-08T00:00:00Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO molbio_ngs_reference_revisions(
                id, reference_id, global_domain_experiment_id, revision_number,
                schema_name, schema_version, canonical_payload, payload_sha256,
                artifact_id, canonical_fasta_sha256, canonical_fasta_size_bytes,
                contig_manifest_sha256, molecule_type, topology, coordinate_contract,
                source_provenance, created_at
            ) VALUES ('backup-reference-revision', 'backup-reference', 'backup-domain', 1,
                      'bms.molbio-ngs.reference-revision', '1', ?, ?, 'backup-artifact', ?, ?,
                      ?, 'dna', 'linear', 'zero-based-half-open', '{}', ?)
            """,
            (
                reference_payload,
                _sha(reference_payload),
                artifact_sha256,
                len(artifact_bytes),
                "9" * 64,
                "2026-08-08T00:00:00Z",
            ),
        )
        connection.execute(
            """
            UPDATE molbio_ngs_reference_resources
               SET current_revision_id='backup-reference-revision', head_generation=1,
                   updated_at='2026-08-08T00:00:01Z'
             WHERE id='backup-reference'
            """
        )
        connection.execute(
            """
            INSERT INTO molbio_ngs_member_receipts(
                receipt_id, source_store_id, entity_kind, entity_id,
                source_generation_or_revision, content_digest, schema_name,
                schema_version, availability, reopen_destination,
                canonical_receipt, receipt_sha256, created_at
            ) VALUES (?, 'molbio', 'molecular_revision', 'molecular-revision-1',
                      '1', ?, 'bms.molbio-ngs.external-member-receipt', '1',
                      'available', ?, ?, ?, ?)
            """,
            (
                "backup-receipt-1",
                "d" * 64,
                _canonical(
                    {
                        "surface": "molbio-sequence-revision",
                        "params": {
                            "sequence_id": "sequence-1",
                            "revision_id": "molecular-revision-1",
                        },
                    }
                ),
                canonical_receipt,
                _sha(canonical_receipt),
                "2026-08-08T00:00:00Z",
            ),
        )
        payload_json = _canonical({"schema": "bms.molbio-ngs.backup-proof.v1"})
        connection.execute(
            """
            INSERT INTO molbio_ngs_outbox_events(
                id, global_domain_experiment_id, state_revision_id, event_type,
                payload_json, payload_sha256, status, retry_count, created_at, updated_at
            ) VALUES ('backup-outbox-1', 'backup-domain', NULL, 'backup.proof',
                      ?, ?, 'pending', 0, ?, ?)
            """,
            (
                payload_json,
                _sha(payload_json),
                "2026-08-08T00:00:00Z",
                "2026-08-08T00:00:00Z",
            ),
        )
        connection.commit()

    manifest = backup_database(source, backup)
    manifest_path = Path(f"{backup}.manifest.json")
    assert manifest_path.read_text(encoding="utf-8") == _canonical(manifest) + "\n"
    assert manifest["backup_sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert manifest["backup_size_bytes"] == backup.stat().st_size
    assert manifest["schema_version"] == 3
    assert manifest["migration_ledger"] == [
        {
            "version": 1,
            "name": "molbio_ngs_domain_state_v1",
            "checksum": manifest["migration_ledger"][0]["checksum"],
        },
        {
            "version": 2,
            "name": "molbio_ngs_samples_references_v2",
            "checksum": manifest["migration_ledger"][1]["checksum"],
        },
        {
            "version": 3,
            "name": "molbio_ngs_immutable_evidence_assessments_v3",
            "checksum": manifest["migration_ledger"][2]["checksum"],
        },
    ]
    assert manifest["member_receipts"] == {
        "count": 1,
        "by_kind": {"molecular_revision": 1},
    }
    assert manifest["outbox_events"] == {"count": 1, "by_status": {"pending": 1}}
    expected_artifact_inventory = [
        {
            "artifact_id": "backup-artifact",
            "managed_relative_path": managed_relative_path,
            "size_bytes": len(artifact_bytes),
            "sha256": artifact_sha256,
        }
    ]
    assert manifest["managed_reference_artifacts"] == expected_artifact_inventory
    artifact_bundle = Path(f"{backup}.artifacts")
    bundled_artifact = artifact_bundle / managed_relative_path
    artifact_bundle_manifest = manifest["artifact_bundle"]
    assert isinstance(artifact_bundle_manifest, dict)
    assert artifact_bundle_manifest["directory_name"] == artifact_bundle.name
    assert artifact_bundle_manifest["count"] == 1
    assert artifact_bundle_manifest["size_bytes"] == len(artifact_bytes)
    assert bundled_artifact.read_bytes() == artifact_bytes

    with sqlite3.connect(source) as connection:
        connection.execute(
            "DROP TRIGGER trg_molbio_ngs_outbox_payload_immutable_update"
        )
        connection.execute(
            """
            CREATE TRIGGER trg_molbio_ngs_outbox_payload_immutable_update
            BEFORE UPDATE ON molbio_ngs_outbox_events BEGIN SELECT 1; END
            """
        )
        connection.commit()
    artifact_path.unlink()
    source_health = health(source)
    assert source_health["status"] == "degraded"
    source_attestation = source_health["attestation"]
    assert isinstance(source_attestation, dict)
    assert source_attestation["changed_objects"]
    assert source_attestation["artifact_errors"]

    restored = restore_database(backup, target)
    assert restored["post_restore_health"]["status"] == "healthy"
    assert artifact_path.read_bytes() == artifact_bytes
    assert restored["external_receipt_availability_reconciliation"] == {
        "mode": "inventory_only_no_receipt_rewrites",
        "count": 1,
        "by_availability": {"available": 1},
        "receipts": [
            {
                "receipt_id": "backup-receipt-1",
                "source_store_id": "molbio",
                "entity_kind": "molecular_revision",
                "entity_id": "molecular-revision-1",
                "availability": "available",
                "reopen_destination": json.loads(
                    _canonical(
                        {
                            "surface": "molbio-sequence-revision",
                            "params": {
                                "sequence_id": "sequence-1",
                                "revision_id": "molecular-revision-1",
                            },
                        }
                    )
                ),
            }
        ],
    }
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT count(*) FROM molbio_ngs_member_receipts").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM molbio_ngs_outbox_events").fetchone()[0] == 1
    Path(f"{target}-wal").unlink(missing_ok=True)
    Path(f"{target}-shm").unlink(missing_ok=True)

    target_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    outside_artifact = tmp_path / "outside-reference.fasta"
    outside_artifact.write_bytes(artifact_bytes)
    bundled_artifact.unlink()
    bundled_artifact.symlink_to(outside_artifact)
    with pytest.raises(ValueError, match="symlink"):
        restore_database(backup, target)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == target_sha256
    assert artifact_path.read_bytes() == artifact_bytes
    Path(f"{target}-wal").unlink(missing_ok=True)
    Path(f"{target}-shm").unlink(missing_ok=True)
    bundled_artifact.unlink()
    bundled_artifact.write_bytes(artifact_bytes)

    backup.write_bytes(b"tampered" + backup.read_bytes()[8:])
    with pytest.raises(ValueError, match="backup digest mismatch"):
        restore_database(backup, target)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == target_sha256


def _empty_restore_package(tmp_path: Path, monkeypatch):
    import molbio_ngs_migrations as migrations

    reference_root = tmp_path / "restore-reference-root"
    reference_root.mkdir()
    monkeypatch.setenv("BMS_MOLBIO_NGS_REFERENCE_ROOT", str(reference_root))
    source = tmp_path / "restore-source.db"
    backup = tmp_path / "restore-backup.db"
    target = tmp_path / "restore-target.db"
    migrations.run_all(source)
    migrations.backup_database(source, backup)
    return migrations, backup, target


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_restore_refuses_existing_sqlite_sidecars(
    tmp_path: Path, monkeypatch, suffix: str
) -> None:
    migrations, backup, target = _empty_restore_package(tmp_path, monkeypatch)
    Path(f"{target}{suffix}").write_bytes(b"busy")

    with pytest.raises(ValueError, match="offline restore refuses existing SQLite sidecars"):
        migrations.restore_database(backup, target)

    assert not Path(f"{target}.restore.lock").exists()
    assert not Path(f"{target}.restore-journal.json").exists()


def test_restore_refuses_existing_lock(tmp_path: Path, monkeypatch) -> None:
    migrations, backup, target = _empty_restore_package(tmp_path, monkeypatch)
    lock = Path(f"{target}.restore.lock")
    lock.write_text("already-owned\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="restore lock already exists"):
        migrations.restore_database(backup, target)

    assert lock.read_text(encoding="utf-8") == "already-owned\n"


def test_restore_malformed_journal_fails_closed(tmp_path: Path, monkeypatch) -> None:
    migrations, backup, target = _empty_restore_package(tmp_path, monkeypatch)
    journal = Path(f"{target}.restore-journal.json")
    journal.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="restore journal schema fields are invalid"):
        migrations.restore_database(backup, target)

    assert journal.read_text(encoding="utf-8") == "{}\n"
    assert not Path(f"{target}.restore.lock").exists()


def test_interrupted_restore_is_rolled_back_and_recovered_on_next_invocation(
    tmp_path: Path, monkeypatch
) -> None:
    migrations, backup, target = _empty_restore_package(tmp_path, monkeypatch)
    migrations.run_all(target)
    original_replace = migrations.os.replace

    def interrupt_after_database_install(source, destination, *args, **kwargs):
        source_path = Path(source)
        if (
            Path(destination) == target
            and source_path.name.startswith(f".{target.name}.restore-")
            and "rollback" not in source_path.name
        ):
            original_replace(source, destination, *args, **kwargs)
            raise KeyboardInterrupt("simulated restore interruption")
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(migrations.os, "replace", interrupt_after_database_install)
    with pytest.raises(KeyboardInterrupt, match="simulated restore interruption"):
        migrations.restore_database(backup, target)
    assert Path(f"{target}.restore-journal.json").exists()
    assert not Path(f"{target}.restore.lock").exists()

    monkeypatch.setattr(migrations.os, "replace", original_replace)
    restored = migrations.restore_database(backup, target)
    assert restored["post_restore_health"]["status"] == "healthy"
    assert not Path(f"{target}.restore-journal.json").exists()


def test_successful_restore_removes_journal_lock_sidecars_and_work_paths(
    tmp_path: Path, monkeypatch
) -> None:
    migrations, backup, target = _empty_restore_package(tmp_path, monkeypatch)

    restored = migrations.restore_database(backup, target)

    assert restored["post_restore_health"]["status"] == "healthy"
    assert not Path(f"{target}.restore.lock").exists()
    assert not Path(f"{target}.restore-journal.json").exists()
    assert not Path(f"{target}-wal").exists()
    assert not Path(f"{target}-shm").exists()
    assert not list(tmp_path.glob(f".{target.name}.restore-*.db"))
    assert not list(tmp_path.glob(".molbio-ngs-artifact-restore-*"))
    assert not list(tmp_path.glob(".molbio-ngs-artifact-rollback-*"))


@pytest.mark.asyncio
async def test_exact_molecular_receipt_resolution_does_not_follow_current_head():
    from types import SimpleNamespace

    from routers.molbio_ops import _resolve_owned_molecular_revision
    from services.molbio_persistence import sha256_text

    exact_revision = SimpleNamespace(
        id="revision-exact",
        document_id="sequence-1",
        content_sha256=sha256_text("ACGT"),
        snapshot={"sequence": "ACGT", "sequence_type": "dna"},
    )
    current_revision = SimpleNamespace(
        id="revision-current",
        document_id="sequence-1",
        content_sha256=sha256_text("TGCA"),
        snapshot={"sequence": "TGCA", "sequence_type": "dna"},
    )
    document = SimpleNamespace(
        id="sequence-1",
        current_revision_id=current_revision.id,
    )

    class MolecularSession:
        async def get(self, model, resource_id):
            if model.__name__ == "MolecularDocument":
                return document if resource_id == document.id else None
            if model.__name__ == "MolecularRevision":
                revisions = {
                    exact_revision.id: exact_revision,
                    current_revision.id: current_revision,
                }
                return revisions.get(resource_id)
            raise AssertionError(f"unexpected model lookup: {model}")

    resolved = await _resolve_owned_molecular_revision(
        MolecularSession(),
        document.id,
        exact_revision.id,
    )
    assert resolved is exact_revision
    assert resolved.id != document.current_revision_id


def test_state_member_role_dto_exactly_matches_runtime_receipt_roles():
    from typing import get_args

    from molbio_ngs_services import _ROLE_ENTITY_KINDS
    from routers.molbio_ngs_experiments import StateMemberRole

    assert set(get_args(StateMemberRole)) == set(_ROLE_ENTITY_KINDS)


@pytest_asyncio.fixture
async def domain_store(tmp_path: Path):
    from molbio_ngs_database import create_molbio_ngs_engine, create_molbio_ngs_session_factory
    from molbio_ngs_migrations import run_all

    db_path = tmp_path / "molbio_ngs.db"
    run_all(db_path)
    engine = create_molbio_ngs_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = create_molbio_ngs_session_factory(engine)
    try:
        yield db_path, engine, factory
    finally:
        await engine.dispose()


def _binding():
    from molbio_ngs_services import InternalVerifiedGlobalBinding

    return InternalVerifiedGlobalBinding(
        global_domain_experiment_id="domain-1",
        global_domain_experiment_revision_id="global-domain-rev-1",
        global_domain_experiment_revision_digest="a" * 64,
        project_id="project-1",
        project_generation="3",
        project_digest="b" * 64,
        project_receipt_id="project-receipt-1",
        project_reopen_destination={
            "surface": "project",
            "params": {"project_id": "project-1"},
        },
        global_experiment_id="global-experiment-1",
        global_experiment_generation="2",
        global_experiment_digest="c" * 64,
        global_experiment_receipt_id="experiment-receipt-1",
        global_experiment_reopen_destination={
            "surface": "global-experiment",
            "params": {
                "project_id": "project-1",
                "experiment_id": "global-experiment-1",
            },
        },
        verified_at="2026-08-08T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_acknowledge_global_binding_rejects_every_authority_change(domain_store):
    from dataclasses import replace

    from molbio_ngs_models import MolBioNGSGlobalBinding
    from molbio_ngs_services import (
        GlobalBindingError,
        acknowledge_global_binding,
        initialize_domain_state,
    )

    _db_path, _engine, factory = domain_store
    async with factory() as session:
        binding = _binding()
        await initialize_domain_state(
            session,
            binding,
            idempotency_key="initialize-authority-immutability",
            created_by="tester",
        )
        await session.commit()

        changed_values = {
            "global_domain_experiment_revision_id": "global-domain-rev-other",
            "global_domain_experiment_revision_digest": "1" * 64,
            "project_id": "project-other",
            "project_generation": "4",
            "project_digest": "2" * 64,
            "project_receipt_id": "project-receipt-other",
            "project_reopen_destination": {
                "surface": "project",
                "params": {"project_id": "project-other"},
            },
            "project_acknowledgement": {"ack": "other"},
            "global_experiment_id": "global-experiment-other",
            "global_experiment_generation": "3",
            "global_experiment_digest": "3" * 64,
            "global_experiment_receipt_id": "experiment-receipt-other",
            "global_experiment_reopen_destination": {
                "surface": "global-experiment",
                "params": {"experiment_id": "global-experiment-other"},
            },
            "global_experiment_acknowledgement": {"ack": "other"},
        }
        for field_name, changed_value in changed_values.items():
            with pytest.raises(GlobalBindingError, match="global binding authority changed"):
                await acknowledge_global_binding(
                    session,
                    replace(binding, **{field_name: changed_value}),
                )

        await acknowledge_global_binding(
            session,
            replace(binding, verified_at="2026-08-08T00:00:01Z"),
        )
        await session.commit()
        stored = await session.get(MolBioNGSGlobalBinding, "domain-1")
        assert stored is not None
        assert stored.last_verified_at == "2026-08-08T00:00:01Z"
        assert stored.binding_state == "acknowledged"
        assert stored.last_error is None


@pytest.mark.asyncio
async def test_state_service_is_global_keyed_revisioned_idempotent_and_audited(domain_store):
    from molbio_ngs_models import (
        MolBioNGSAuditEvent,
        MolBioNGSDomainStateMember,
        MolBioNGSOutboxEvent,
    )
    from molbio_ngs_services import (
        IdempotencyConflict,
        RevisionConflict,
        StateMember,
        get_domain_state,
        initialize_domain_state,
        list_state_revisions,
        save_state_revision,
    )
    from services.molbio_ngs_member_receipts import (
        build_external_member_receipt,
        persist_member_receipt,
    )

    _db_path, _engine, factory = domain_store
    async with factory() as session:
        initialized = await initialize_domain_state(
            session,
            _binding(),
            idempotency_key="initialize-domain-1",
            created_by="tester",
        )
        await session.commit()
        assert initialized.global_domain_experiment_id == "domain-1"
        assert initialized.head_generation == 0

        member_receipt = await persist_member_receipt(
            session,
            build_external_member_receipt(
                source_store_id="molbio",
                entity_kind="molecular_revision",
                entity_id="molecular-rev-1",
                source_generation_or_revision="1",
                content_digest="d" * 64,
                source_schema="bms.molbio.molecular-revision.v1",
                availability="available",
                reopen_destination={
                    "surface": "molbio-sequence-revision",
                    "params": {
                        "sequence_id": "sequence-1",
                        "revision_id": "molecular-rev-1",
                    },
                },
            ),
        )

        payload = _state_payload()
        members = [
            StateMember(
                receipt_id=member_receipt.receipt_id,
                role="molecular_expected_construct",
                ordinal=0,
            )
        ]
        revision = await save_state_revision(
            session,
            global_domain_experiment_id="domain-1",
            global_domain_experiment_revision_id="global-domain-rev-1",
            payload=payload,
            members=members,
            expected_head_generation=0,
            parent_revision_id=None,
            idempotency_key="save-state-1",
            created_by="tester",
        )
        await session.commit()
        replay = await save_state_revision(
            session,
            global_domain_experiment_id="domain-1",
            global_domain_experiment_revision_id="global-domain-rev-1",
            payload=payload,
            members=members,
            expected_head_generation=0,
            parent_revision_id=None,
            idempotency_key="save-state-1",
            created_by="tester",
        )
        assert replay.id == revision.id

        head = await get_domain_state(session, "domain-1")
        assert head.current_state_revision_id == revision.id
        assert head.head_generation == 1
        assert (await list_state_revisions(session, "domain-1"))[0].id == revision.id
        stored_members = (
            await session.execute(
                select(MolBioNGSDomainStateMember).where(
                    MolBioNGSDomainStateMember.state_revision_id == revision.id
                )
            )
        ).scalars().all()
        assert len(stored_members) == 1
        assert stored_members[0].receipt_id == member_receipt.receipt_id
        assert len((await session.execute(select(MolBioNGSAuditEvent))).scalars().all()) == 2
        assert len((await session.execute(select(MolBioNGSOutboxEvent))).scalars().all()) == 2

        with pytest.raises(IdempotencyConflict):
            await save_state_revision(
                session,
                global_domain_experiment_id="domain-1",
                global_domain_experiment_revision_id="global-domain-rev-1",
                payload={**payload, "notes": "different immutable request"},
                members=members,
                expected_head_generation=0,
                parent_revision_id=None,
                idempotency_key="save-state-1",
                created_by="tester",
            )
        with pytest.raises(RevisionConflict):
            await save_state_revision(
                session,
                global_domain_experiment_id="domain-1",
                global_domain_experiment_revision_id="global-domain-rev-1",
                payload=payload,
                members=members,
                expected_head_generation=0,
                parent_revision_id=None,
                idempotency_key="save-state-stale",
                created_by="tester",
            )

    concurrent_binding = replace(
        _binding(),
        global_domain_experiment_id="domain-concurrent",
        global_domain_experiment_revision_id="global-domain-rev-concurrent",
    )

    async def initialize_concurrently() -> str:
        async with factory() as concurrent_session:
            state = await initialize_domain_state(
                concurrent_session,
                concurrent_binding,
                idempotency_key="same-concurrent-request",
                created_by="tester",
            )
            await concurrent_session.commit()
            return state.global_domain_experiment_id

    concurrent_results = await asyncio.gather(
        initialize_concurrently(), initialize_concurrently()
    )
    assert concurrent_results == ["domain-concurrent", "domain-concurrent"]
    async with factory() as verification_session:
        assert len(
            (
                await verification_session.execute(
                    select(MolBioNGSAuditEvent).where(
                        MolBioNGSAuditEvent.global_domain_experiment_id == "domain-concurrent"
                    )
                )
            ).scalars().all()
        ) == 1


@pytest.mark.asyncio
async def test_state_save_rejects_cross_domain_reference_and_evidence_receipts(
    domain_store, tmp_path: Path, monkeypatch
):
    from molbio_ngs_models import MolBioNGSEvidenceAssessment
    from molbio_ngs_services import (
        StateMember,
        StateValidationError,
        initialize_domain_state,
        save_state_revision,
    )
    from services.molbio_ngs_evidence import resolve_evidence_assessment_receipt
    from services.molbio_ngs_member_receipts import (
        build_external_member_receipt,
        persist_member_receipt,
    )
    from services.molbio_ngs_references import (
        create_reference,
        resolve_ngs_reference_revision_receipt,
    )

    monkeypatch.setenv("BMS_MOLBIO_NGS_REFERENCE_ROOT", str(tmp_path / "references"))
    _db_path, _engine, factory = domain_store
    async with factory() as session:
        domain_1 = _binding()
        domain_2 = replace(
            domain_1,
            global_domain_experiment_id="domain-2",
            global_domain_experiment_revision_id="global-domain-rev-2",
        )
        await initialize_domain_state(
            session, domain_1, idempotency_key="initialize-domain-1-ownership"
        )
        await initialize_domain_state(
            session, domain_2, idempotency_key="initialize-domain-2-ownership"
        )
        external = await persist_member_receipt(
            session,
            build_external_member_receipt(
                source_store_id="molbio",
                entity_kind="molecular_revision",
                entity_id="external-revision-domain-2",
                source_generation_or_revision="1",
                content_digest="a" * 64,
                source_schema="bms.molbio.molecular-revision.v1",
                availability="available",
                reopen_destination={
                    "surface": "molbio-sequence-revision",
                    "params": {
                        "sequence_id": "external-sequence-domain-2",
                        "revision_id": "external-revision-domain-2",
                    },
                },
            ),
        )
        domain_2_state = await save_state_revision(
            session,
            global_domain_experiment_id="domain-2",
            global_domain_experiment_revision_id="global-domain-rev-2",
            payload=_state_payload(),
            members=[
                StateMember(
                    receipt_id=external.receipt_id,
                    role="molecular_expected_construct",
                    ordinal=0,
                )
            ],
            expected_head_generation=0,
            parent_revision_id=None,
            idempotency_key="domain-2-base-state",
        )
        reference, reference_revision = await create_reference(
            session,
            global_domain_experiment_id="domain-2",
            name="Domain 2 reference",
            raw_fasta=b">reference\nACGT\n",
            molecule_type="dna",
            topology="linear",
            coordinate_contract="exact",
            source_provenance={"kind": "test"},
            idempotency_key="domain-2-reference",
        )
        reference_receipt = await persist_member_receipt(
            session,
            await resolve_ngs_reference_revision_receipt(
                session,
                global_domain_experiment_id="domain-2",
                reference_id=reference.id,
                revision_id=reference_revision.id,
            ),
        )

        created_at = "2026-08-09T00:00:00+00:00"
        receipt_ids = {
            "ngs_job": external.receipt_id,
            "ngs_result_manifest": external.receipt_id,
            "ngs_reference_revision": reference_receipt.receipt_id,
            "ont_instrument_run": None,
            "molecular_revision": None,
            "ngs_comparison_panel": None,
        }
        wrapper = {
            "schema": "bms.molbio-ngs.ngs-evidence-receipt.v1",
            "evidence_id": "evidence-domain-2",
            "global_domain_experiment_id": "domain-2",
            "state_revision_id": domain_2_state.id,
            "sample_revision_id": None,
            "receipt_ids": receipt_ids,
            "assessment_rule_id": "server-owned-rule",
            "requested_assessment": "REVIEW",
            "scientific_assessment": "REVIEW",
            "job_lifecycle_state": "completed",
            "manifest_integrity": "valid",
            "raw_manifest_sha256": "b" * 64,
            "notes": None,
            "created_at": created_at,
            "created_by": "tester",
        }
        canonical_wrapper = _canonical(wrapper)
        session.add(
            MolBioNGSEvidenceAssessment(
                evidence_id="evidence-domain-2",
                global_domain_experiment_id="domain-2",
                state_revision_id=domain_2_state.id,
                sample_revision_id=None,
                ngs_job_receipt_id=external.receipt_id,
                ngs_result_manifest_receipt_id=external.receipt_id,
                ngs_reference_revision_receipt_id=reference_receipt.receipt_id,
                ont_instrument_run_receipt_id=None,
                molecular_revision_receipt_id=None,
                ngs_comparison_panel_receipt_id=None,
                assessment_rule_id="server-owned-rule",
                requested_assessment="REVIEW",
                scientific_assessment="REVIEW",
                job_lifecycle_state="completed",
                manifest_integrity="valid",
                raw_manifest_sha256="b" * 64,
                notes=None,
                canonical_wrapper=canonical_wrapper,
                wrapper_sha256=_sha(canonical_wrapper),
                created_at=created_at,
                created_by="tester",
            )
        )
        await session.flush()
        evidence_receipt = await persist_member_receipt(
            session,
            await resolve_evidence_assessment_receipt(
                session,
                global_domain_experiment_id="domain-2",
                evidence_id="evidence-domain-2",
            ),
        )
        reference_receipt_id = reference_receipt.receipt_id
        evidence_receipt_id = evidence_receipt.receipt_id
        domain_2_state_id = domain_2_state.id
        await session.commit()

        cross_domain = (
            (reference_receipt_id, "ngs_reference"),
            (evidence_receipt_id, "ngs_verification_assessment"),
        )
        for index, (receipt_id, role) in enumerate(cross_domain):
            with pytest.raises(StateValidationError, match="Domain Experiment"):
                await save_state_revision(
                    session,
                    global_domain_experiment_id="domain-1",
                    global_domain_experiment_revision_id="global-domain-rev-1",
                    payload=_state_payload(),
                    members=[StateMember(receipt_id=receipt_id, role=role, ordinal=0)],
                    expected_head_generation=0,
                    parent_revision_id=None,
                    idempotency_key=f"reject-cross-domain-{index}",
                )
            await session.rollback()

        accepted = await save_state_revision(
            session,
            global_domain_experiment_id="domain-2",
            global_domain_experiment_revision_id="global-domain-rev-2",
            payload=_state_payload(),
            members=[
                StateMember(
                    receipt_id=reference_receipt_id,
                    role="ngs_reference",
                    ordinal=0,
                ),
                StateMember(
                    receipt_id=evidence_receipt_id,
                    role="ngs_verification_assessment",
                    ordinal=1,
                ),
            ],
            expected_head_generation=1,
            parent_revision_id=domain_2_state_id,
            idempotency_key="accept-domain-2-authority",
        )
        assert accepted.global_domain_experiment_id == "domain-2"


@pytest.mark.asyncio
async def test_typed_state_api_fails_closed_until_global_adapter_is_available(
    domain_store,
):
    from experiment_database import get_experiment_session
    from molbio_ngs_database import get_molbio_ngs_session
    from routers.molbio_ngs_experiments import router

    _db_path, _domain_engine, domain_factory = domain_store
    app = FastAPI()
    app.include_router(router)

    async def override_domain_session():
        async with domain_factory() as session:
            yield session

    async def override_unavailable_global_session():
        async with domain_factory() as session:
            yield session

    app.dependency_overrides[get_molbio_ngs_session] = override_domain_session
    app.dependency_overrides[get_experiment_session] = override_unavailable_global_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initialized = await client.post(
            "/api/molbio-ngs/experiments/domain-1/state",
            json={
                "global_domain_experiment_revision_id": "global-domain-rev-1",
                "idempotency_key": "init-api",
            },
        )
        assert initialized.status_code == 503, initialized.text
        assert "global adapter unavailable" in initialized.json()["detail"].lower()

        saved = await client.post(
            "/api/molbio-ngs/experiments/domain-1/state/revisions",
            json={
                "global_domain_experiment_revision_id": "global-domain-rev-1",
                "expected_head_generation": 0,
                "parent_revision_id": None,
                "idempotency_key": "save-api",
                "payload": _state_payload(),
                "members": [],
            },
        )
        assert saved.status_code == 503, saved.text
        assert "global adapter unavailable" in saved.json()["detail"].lower()

        rejected = await client.post(
            "/api/molbio-ngs/experiments/domain-1/state/revisions",
            json={
                "global_domain_experiment_revision_id": "global-domain-rev-1",
                "expected_head_generation": 0,
                "parent_revision_id": None,
                "idempotency_key": "save-extra",
                "payload": _state_payload(),
                "members": [],
                "unexpected": True,
            },
        )
        assert rejected.status_code == 422
