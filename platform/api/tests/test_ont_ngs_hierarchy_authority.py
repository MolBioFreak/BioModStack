from __future__ import annotations

import copy
import hashlib
import importlib
import json
from types import SimpleNamespace
from typing import Any

import pytest
import rfc8785

JOB_ID = "31f02bd5-830f-4558-aa78-3873c515de68"
PROJECT_ID = "4af72c1d-27d8-4e14-8f39-4259a80494a0"
GLOBAL_ID = "9a10c5a8-b233-4bf3-af14-9c2880525278"
DOMAIN_ID = "916a611b-6879-486f-bf9e-e1b5a796e01c"
STATE_ID = "state-1"
BINDING_ID = "binding-1"
MEMBER_ID = "member-1"
SAMPLE_ID = "sample-revision-1"
REFERENCE_ID = "reference-revision-1"
SHA = {
    name: hashlib.sha256(name.encode()).hexdigest()
    for name in ("project", "global", "domain", "state", "members", "sample", "reference", "canonical-reference", "sequence", "receipt")
}


def _canonical_document(payload: dict[str, Any]) -> tuple[str, str]:
    raw = rfc8785.dumps(payload).decode("utf-8")
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def _valid_contract() -> dict[str, Any]:
    binding_receipt = {
        "schema": "bms.ngs-molbio.global-binding-receipt.v1",
        "receipt_id": "binding-receipt-1",
        "acknowledgement": {"status": "verified"},
        "project": {
            "id": PROJECT_ID,
            "revision_id": "project-revision-1",
            "digest": SHA["project"],
            "generation": 22,
        },
        "global_experiment": {
            "id": GLOBAL_ID,
            "revision_id": "global-revision-1",
            "digest": SHA["global"],
            "generation": 1,
        },
        "domain_experiment": {
            "id": DOMAIN_ID,
            "revision_id": "domain-revision-1",
            "digest": SHA["domain"],
            "generation": 1,
            "domain_kind": "ngs_molbio",
        },
    }
    binding_json, binding_sha = _canonical_document(binding_receipt)
    state_payload, state_sha = _canonical_document({"schema": "bms.molbio-ngs.domain-state-revision.v1"})
    sample_payload, sample_sha = _canonical_document({"schema": "bms.molbio-ngs.sample-revision.v1"})
    reference_payload, reference_sha = _canonical_document({"schema": "bms.molbio-ngs.reference-revision.v1"})
    contract = {
        "job_id": JOB_ID,
        "model_id": "nanopore",
        "params": {
            "ont_workflow_id": "ont_fastq_qc",
            "input_mode": "fastq",
            "global_domain_experiment_id": DOMAIN_ID,
            "molbio_ngs_state_revision_id": STATE_ID,
            "state_membership_receipt_id": MEMBER_ID,
            "ngs_reference_id": "reference-1",
            "ngs_reference_revision_id": REFERENCE_ID,
            "reference_sequence_sha256": SHA["sequence"],
            "managed_reference_snapshot_sha256": SHA["canonical-reference"],
            "managed_reference_snapshot_size_bytes": 5654,
        },
        "state_revision": {
            "id": STATE_ID,
            "global_domain_experiment_id": DOMAIN_ID,
            "global_domain_experiment_revision_id": "domain-revision-1",
            "binding_revision_id": BINDING_ID,
            "canonical_payload": state_payload,
            "payload_sha256": state_sha,
            "membership_graph_sha256": SHA["members"],
        },
        "membership_graph": [{
            "receipt_id": MEMBER_ID,
            "receipt_sha256": SHA["receipt"],
            "entity_kind": "ngs_reference_revision",
            "entity_id": REFERENCE_ID,
            "content_digest": SHA["canonical-reference"],
            "availability": "available",
            "role": "ngs_reference",
            "ordinal": 0,
            "sample_revision_id": SAMPLE_ID,
        }],
        "binding": {
            "binding_revision_id": BINDING_ID,
            "binding_state": "acknowledged",
            "global_domain_experiment_id": DOMAIN_ID,
            "global_domain_experiment_revision_id": "domain-revision-1",
            "global_domain_experiment_revision_digest": SHA["domain"],
            "project_id": PROJECT_ID,
            "project_generation": "22",
            "project_digest": SHA["project"],
            "global_experiment_id": GLOBAL_ID,
            "global_experiment_generation": "1",
            "global_experiment_digest": SHA["global"],
            "global_binding_receipt_id": "binding-receipt-1",
            "global_binding_receipt_json": binding_json,
            "global_binding_receipt_sha256": binding_sha,
        },
        "project_revision": {
            "resource_id": "project-revision-1",
            "subject_id": PROJECT_ID,
            "payload_sha256": SHA["project"],
        },
        "global_revision": {
            "resource_id": "global-revision-1",
            "subject_id": GLOBAL_ID,
            "payload_sha256": SHA["global"],
        },
        "domain_revision": {
            "resource_id": "domain-revision-1",
            "subject_id": DOMAIN_ID,
            "payload_sha256": SHA["domain"],
        },
        "sample_revision": {
            "id": SAMPLE_ID,
            "global_domain_experiment_id": DOMAIN_ID,
            "canonical_payload": sample_payload,
            "payload_sha256": sample_sha,
        },
        "reference_revision": {
            "id": REFERENCE_ID,
            "global_domain_experiment_id": DOMAIN_ID,
            "canonical_payload": reference_payload,
            "payload_sha256": reference_sha,
            "canonical_fasta_sha256": SHA["canonical-reference"],
            "canonical_fasta_size_bytes": 5654,
            "normalized_sequence_sha256": SHA["sequence"],
        },
    }
    contract["state_revision"]["membership_graph_sha256"] = hashlib.sha256(
        rfc8785.dumps(contract["membership_graph"]),
    ).hexdigest()
    return contract


def _service():
    return importlib.import_module("services.ont_ngs_hierarchy")


def test_hierarchy_authority_is_complete_path_opaque_and_stable() -> None:
    service = _service()
    build = getattr(service, "build_ont_ngs_hierarchy_authority", None)
    assert callable(build), "hierarchy authority builder is missing"

    authority: Any = build(**_valid_contract())

    assert authority.project_id == PROJECT_ID
    assert authority.document["job"] == {
        "id": JOB_ID,
        "workflow_id": "ont_fastq_qc",
        "input_mode": "fastq",
    }
    assert authority.document["project"]["revision_id"] == "project-revision-1"
    assert authority.document["global_experiment"]["id"] == GLOBAL_ID
    assert authority.document["domain_experiment"]["state_revision_id"] == STATE_ID
    assert authority.document["member"]["receipt_id"] == MEMBER_ID
    assert authority.document["sample"]["revision_id"] == SAMPLE_ID
    assert authority.document["reference"]["revision_id"] == REFERENCE_ID
    assert authority.digest == hashlib.sha256(rfc8785.dumps(authority.document)).hexdigest()
    assert "/home/" not in repr(authority.document)


def test_capability_hierarchy_record_rejects_missing_rewritten_and_cross_job_authority() -> None:
    service = _service()
    authority = service.build_ont_ngs_hierarchy_authority(**_valid_contract())
    record = service.hierarchy_authority_record(authority)
    job = SimpleNamespace(id=JOB_ID, provenance={service.PROVENANCE_HIERARCHY_KEY: record})

    assert service.capability_hierarchy_matches(job, authority) is True
    for mutation in ("missing", "digest", "document", "cross_job"):
        candidate = copy.deepcopy(job)
        if mutation == "missing":
            candidate.provenance = {}
        elif mutation == "digest":
            candidate.provenance[service.PROVENANCE_HIERARCHY_KEY]["digest"] = SHA["global"]
        elif mutation == "document":
            candidate.provenance[service.PROVENANCE_HIERARCHY_KEY]["document"]["project"]["id"] = "foreign-project"
        else:
            candidate.id = "00000000-0000-0000-0000-000000000000"
        assert service.capability_hierarchy_matches(candidate, authority) is False


def test_reconciliation_source_binding_uses_only_validated_source_authority() -> None:
    service = _service()
    authority = service.build_ont_ngs_hierarchy_authority(**_valid_contract())
    bind = getattr(service, "bind_ont_ngs_hierarchy_source_authority", None)
    assert callable(bind)

    bound = bind(
        authority,
        source_fastq_sha256="f" * 64,
        artifact_set_sha256="a" * 64,
        sequence_qc_manifest_sha256="b" * 64,
        verification_manifest_sha256="c" * 64,
        reference_sequence_sha256=SHA["sequence"],
    )

    assert bound.document["source_fastq"] == {
        "sha256": "f" * 64,
        "artifact_set_sha256": "a" * 64,
        "sequence_qc_manifest_sha256": "b" * 64,
        "verification_manifest_sha256": "c" * 64,
    }
    assert bound.digest == hashlib.sha256(rfc8785.dumps(bound.document)).hexdigest()
    with pytest.raises(service.OntNgsHierarchyError, match="cross-bound"):
        bind(
            authority,
            source_fastq_sha256="f" * 64,
            artifact_set_sha256="a" * 64,
            sequence_qc_manifest_sha256="b" * 64,
            verification_manifest_sha256="c" * 64,
            reference_sequence_sha256=SHA["global"],
        )


def test_workflow_aliases_produce_one_canonical_hierarchy_digest() -> None:
    service = _service()
    canonical = _valid_contract()
    alias = copy.deepcopy(canonical)
    alias["params"]["workflow_id"] = alias["params"].pop("ont_workflow_id")

    canonical_authority = service.build_ont_ngs_hierarchy_authority(**canonical)
    alias_authority = service.build_ont_ngs_hierarchy_authority(**alias)

    assert alias_authority.digest == canonical_authority.digest
    assert alias_authority.document["job"]["workflow_id"] == "ont_fastq_qc"


def test_canonical_scientific_documents_use_document_bounds_not_identifier_bounds() -> None:
    service = _service()
    contract = _valid_contract()
    reference_payload, reference_sha = _canonical_document({
        "schema": "bms.molbio-ngs.reference-revision.v1",
        "notes": "x" * 4096,
    })
    contract["reference_revision"]["canonical_payload"] = reference_payload
    contract["reference_revision"]["payload_sha256"] = reference_sha
    binding_receipt = json.loads(contract["binding"]["global_binding_receipt_json"])
    binding_receipt["verified_evidence"] = "y" * 4096
    binding_json, binding_sha = _canonical_document(binding_receipt)
    contract["binding"]["global_binding_receipt_json"] = binding_json
    contract["binding"]["global_binding_receipt_sha256"] = binding_sha

    authority = service.build_ont_ngs_hierarchy_authority(**contract)

    assert authority.document["reference"]["payload_sha256"] == reference_sha
    assert authority.document["binding"]["receipt_sha256"] == binding_sha


def _mutate(contract: dict[str, Any], case: str) -> None:
    if case == "cross_domain":
        contract["reference_revision"]["global_domain_experiment_id"] = "foreign-domain"
    elif case == "stale_binding":
        contract["state_revision"]["binding_revision_id"] = "foreign-binding"
    elif case == "foreign_project_revision":
        contract["project_revision"]["payload_sha256"] = SHA["global"]
    elif case == "foreign_sample":
        contract["membership_graph"][0]["sample_revision_id"] = "foreign-sample"
    elif case == "foreign_reference":
        contract["reference_revision"]["normalized_sequence_sha256"] = SHA["global"]
    elif case == "member_substitution":
        contract["membership_graph"][0]["receipt_id"] = "foreign-member"
    elif case == "binding_receipt_rewrite":
        contract["binding"]["global_binding_receipt_json"] += " "


@pytest.mark.parametrize(
    "case",
    [
        "cross_domain",
        "stale_binding",
        "foreign_project_revision",
        "foreign_sample",
        "foreign_reference",
        "member_substitution",
        "binding_receipt_rewrite",
    ],
)
def test_hierarchy_authority_rejects_cross_bound_or_rewritten_evidence(case: str) -> None:
    service = _service()
    build = getattr(service, "build_ont_ngs_hierarchy_authority", None)
    assert callable(build), "hierarchy authority builder is missing"
    contract = _valid_contract()
    _mutate(contract, case)

    with pytest.raises(service.OntNgsHierarchyError):
        build(**contract)


@pytest.mark.asyncio
async def test_resolver_loads_the_frozen_binding_not_the_current_domain_head(monkeypatch) -> None:
    service = _service()
    resolve = getattr(service, "resolve_ont_ngs_hierarchy_authority", None)
    assert callable(resolve), "hierarchy authority resolver is missing"
    contract = _valid_contract()
    job = SimpleNamespace(
        id=contract["job_id"],
        model_id=contract["model_id"],
        params=contract["params"],
        provenance={
            "result_integrity": {
                "result_kind": "ngs_sequence_qc",
                "source_fastq_sha256": "f" * 64,
                "artifact_set_sha256": "a" * 64,
                "sequence_qc_manifest_sha256": "b" * 64,
                "construct_verification_manifest_sha256": "c" * 64,
                "reference_sequence_sha256": SHA["sequence"],
            },
        },
    )

    rows = {
        "MolBioNGSGlobalBinding": SimpleNamespace(**contract["binding"]),
        "MolBioNGSSampleRevision": SimpleNamespace(sample_id="sample-1", **contract["sample_revision"]),
        "ExperimentRevision": {
            "project-revision-1": SimpleNamespace(**contract["project_revision"]),
            "global-revision-1": SimpleNamespace(**contract["global_revision"]),
            "domain-revision-1": SimpleNamespace(**contract["domain_revision"]),
        },
    }

    class FakeSession:
        def __init__(self, kind: str):
            self.kind = kind

        async def get(self, model, key):
            name = model.__name__
            if name == "ExperimentRevision":
                return rows[name].get(key)
            row = rows.get(name)
            expected_key = BINDING_ID if name == "MolBioNGSGlobalBinding" else SAMPLE_ID
            return row if key == expected_key else None

    async def fake_state(_session, domain_id, state_id):
        assert (domain_id, state_id) == (DOMAIN_ID, STATE_ID)
        return SimpleNamespace(**contract["state_revision"])

    async def fake_verify(_session, _revision):
        return ({"verified": True}, contract["membership_graph"])

    async def fake_reference(_session, _reference_resource_id, revision_id):
        assert revision_id == REFERENCE_ID
        return SimpleNamespace(reference_id="reference-1", **contract["reference_revision"])

    async def fake_sample(_session, domain_id, sample_id, revision_id):
        assert (domain_id, sample_id, revision_id) == (DOMAIN_ID, "sample-1", SAMPLE_ID)
        return rows["MolBioNGSSampleRevision"]

    monkeypatch.setattr(service, "get_state_revision", fake_state)
    monkeypatch.setattr(service, "verify_state_revision_integrity", fake_verify)
    monkeypatch.setattr(service, "get_reference_revision", fake_reference)
    monkeypatch.setattr(service, "get_sample_revision", fake_sample)

    authority = await resolve(job, FakeSession("domain"), FakeSession("experiment"))

    assert authority.document["source_fastq"]["sha256"] == "f" * 64
    assert authority.digest == hashlib.sha256(rfc8785.dumps(authority.document)).hexdigest()
    assert authority.document["domain_experiment"]["binding_revision_id"] == BINDING_ID


def build_ont_digest(contract: dict[str, Any]) -> str:
    service = _service()
    return service.build_ont_ngs_hierarchy_authority(**contract).digest


@pytest.mark.parametrize("historical", [False, True])
def test_persisted_result_authority_binds_source_fastq_into_capability_hierarchy(historical: bool) -> None:
    service = _service()
    base = service.build_ont_ngs_hierarchy_authority(**_valid_contract())
    source = {
        "source_fastq_sha256": "f" * 64,
        "artifact_set_sha256": "a" * 64,
        "sequence_qc_manifest_sha256": "b" * 64,
        "reference_sequence_sha256": SHA["sequence"],
        ("verification_manifest_sha256" if historical else "construct_verification_manifest_sha256"): "c" * 64,
    }
    provenance = {
        "ont_fastq_qc_reconciliation_v1" if historical else "result_integrity": {
            "schema": "bms.ont-fastq-qc-reconciliation.v1" if historical else "bms.ngs.result-integrity.v1",
            "result_kind": "design" if historical else "ngs_sequence_qc",
            **source,
        },
    }
    bind: Any = getattr(service, "_bind_persisted_result_source_authority", None)
    assert callable(bind)

    bound = bind(SimpleNamespace(provenance=provenance), base)

    assert bound.document["source_fastq"] == {
        "sha256": "f" * 64,
        "artifact_set_sha256": "a" * 64,
        "sequence_qc_manifest_sha256": "b" * 64,
        "verification_manifest_sha256": "c" * 64,
    }
    assert bound.digest == hashlib.sha256(rfc8785.dumps(bound.document)).hexdigest()


def test_capability_hierarchy_rejects_missing_persisted_result_source_authority() -> None:
    service = _service()
    base = service.build_ont_ngs_hierarchy_authority(**_valid_contract())
    bind: Any = getattr(service, "_bind_persisted_result_source_authority", None)
    assert callable(bind)

    with pytest.raises(service.OntNgsHierarchyError, match="source FASTQ result authority"):
        bind(SimpleNamespace(provenance={}), base)
