from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.global_experiments.adapters as adapter_module
from database import (
    Base,
    ConformationalMappingArtifact,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    Design,
    FrustraMPNNResult,
    Job,
    MdRun,
    MolBioNgsReceipt,
    RFD3LocalRedesignRequest,
)
from molbio_models import MolBioBase, MolecularDocument, MolecularRevision
from scripts.rfd3_local_redesign.contract import request_sha256
from services.conformational_mapping.contracts import canonical_sha256 as cm_sha256
from services.frustrampnn.contracts import canonical_json_bytes as frustrampnn_canonical_bytes
from services.md.state import canonical_sha256 as md_sha256
from services.global_experiments.result_surfaces import result_surface_for_receipt


EXPECTED_ADAPTER_IDS = {
    "bms.core.protein-result-reference.adapter.v1",
    "bms.rfd3.local-redesign-reference.adapter.v1",
    "bms.cm.protenix_v2.adapter.v1",
    "bms.cm.confornets.adapter.v1",
    "bms.md.result-reference.adapter.v1",
    "bms.frustrampnn.result-reference.adapter.v1",
    "bms.molbio.revision-reference.adapter.v1",
    "bms.ngs.expected-reference-receipt.adapter.v1",
    "bms.ngs.reference-set-reference.adapter.v1",
    "bms.ngs.sequence-qc-reference.adapter.v1",
    "bms.ngs.alignment-viewer-reference.adapter.v1",
}


@pytest_asyncio.fixture
async def adapter_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BMS_BUILD_SHA", "adapter-domain-test-build")
    monkeypatch.setenv("BMS_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("BMS_INPUTS_DIR", str(tmp_path / "inputs"))
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path / "results"))

    core_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    async with core_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    core_factory = async_sessionmaker(core_engine, expire_on_commit=False)

    molbio_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'molbio.db'}")
    async with molbio_engine.begin() as connection:
        await connection.run_sync(MolBioBase.metadata.create_all)
    molbio_factory = async_sessionmaker(molbio_engine, expire_on_commit=False)
    try:
        yield tmp_path, core_factory, molbio_factory
    finally:
        await core_engine.dispose()
        await molbio_engine.dispose()


def _class(name: str):
    value = getattr(adapter_module, name, None)
    assert value is not None, f"missing adapter class: {name}"
    return value


def test_registry_exposes_complete_exact_adapter_id_set() -> None:
    assert {item["adapter_id"] for item in adapter_module.registry.list()} == EXPECTED_ADAPTER_IDS
    with pytest.raises(adapter_module.AdapterError):
        adapter_module.registry.get("core.rfd3-local-redesign.v1")


@pytest.mark.asyncio
async def test_core_rfd3_cm_and_frustrampnn_verify_native_authorities(adapter_stores):
    tmp_path, core_factory, _molbio_factory = adapter_stores
    structure = tmp_path / "design.pdb"
    structure.write_bytes(b"ATOM      1  CA  ALA A   1\n")
    structure_sha = hashlib.sha256(structure.read_bytes()).hexdigest()
    review_manifest = {
        "schema": "bms.review-artifacts.v1",
        "structure": {
            "kind": "structure",
            "state": "ready",
            "path": str(structure),
            "sha256": structure_sha,
            "bytes": structure.stat().st_size,
        },
        "roles": {"result_role": "structure"},
    }

    rfd3_request = {
        "schema": "bms.rfd3.local-redesign.request.v1",
        "request_id": "rfd3-request-1",
        "input": {"sha256": "1" * 64},
    }
    rfd3_digest = request_sha256(rfd3_request)

    cm_request_without_hash = {
        "schema_name": "cm_request",
        "schema_version": 1,
        "request_id": "cm-request-1",
        "backend": "protenix_v2_ensemble",
    }
    cm_request = {**cm_request_without_hash, "request_sha256": cm_sha256(cm_request_without_hash)}
    cm_plan_without_hash = {
        "schema_name": "cm_coordinate_plan",
        "schema_version": 1,
        "request_id": "cm-request-1",
        "backend": "protenix_v2_ensemble",
        "request_sha256": cm_request["request_sha256"],
        "expected_cardinality": 1,
        "coordinates": [{"candidate": 0}],
    }
    cm_plan = {**cm_plan_without_hash, "coordinate_plan_sha256": cm_sha256(cm_plan_without_hash)}
    cm_record_payload = {"schema_name": "cm_ensemble", "request_id": "cm-request-1"}
    cm_artifact = tmp_path / "cm.cif"
    cm_artifact.write_bytes(b"data_cm\n")
    cm_artifact_sha = hashlib.sha256(cm_artifact.read_bytes()).hexdigest()

    frustrampnn_manifest = {
        "schema_name": "frustrampnn_result_manifest",
        "invocation_id": "invoke-1",
        "parent_job_id": "frustra-job-1",
        "candidate_id": "candidate-1",
        "request_sha256": "7" * 64,
        "source_sha256": "8" * 64,
        "artifacts": [],
    }
    frustrampnn_summary = {"schema_name": "frustrampnn_summary", "candidate_id": "candidate-1"}
    frustrampnn_manifest_sha = hashlib.sha256(frustrampnn_canonical_bytes(frustrampnn_manifest)).hexdigest()
    frustrampnn_summary_sha = hashlib.sha256(frustrampnn_canonical_bytes(frustrampnn_summary)).hexdigest()

    async with core_factory() as session:
        session.add_all(
            [
                Job(id="core-job-1", name="Core", status="completed", model_id="boltz2", mode="predict", params={}),
                Job(id="rfd3-job-1", name="RFD3", status="completed", model_id="protein_local_redesign", mode="local_redesign", params={}),
                Job(id="cm-request-1", name="CM", status="completed", model_id="conformational_mapping", mode="protenix_v2_ensemble", params={}),
                Job(id="frustra-job-1", name="Frustra", status="completed", model_id="frustrampnn", mode="analyze", params={}),
                Design(
                    id="design-1",
                    job_id="core-job-1",
                    name="Design 1",
                    pdb_path=str(structure),
                    artifact_class="shape_candidate",
                    review_profile_id="shape_blueprint",
                    review_contract_version=1,
                    review_contract_source="producer",
                    review_artifact_manifest=review_manifest,
                    review_role_map={"result_role": "structure"},
                ),
                RFD3LocalRedesignRequest(
                    request_id="rfd3-request-1",
                    job_id="rfd3-job-1",
                    request_sha256=rfd3_digest,
                    profile_id="default",
                    profile_registry_sha256="2" * 64,
                    redesign_mode="local_redesign",
                    sequence_policy="fixed",
                    status="completed",
                    request_json=rfd3_request,
                    result_manifest_sha256="3" * 64,
                ),
                ConformationalMappingRequest(
                    request_id="cm-request-1",
                    job_id="cm-request-1",
                    principal_id="operator",
                    backend="protenix_v2_ensemble",
                    status="completed",
                    request_sha256=cm_request["request_sha256"],
                    coordinate_plan_sha256=cm_plan["coordinate_plan_sha256"],
                    resume_key="4" * 64,
                    result_contract_id="conformational_mapping_protenix_v1",
                    request_json=cm_request,
                    coordinate_plan_json=cm_plan,
                    progress_json={},
                ),
                ConformationalMappingRecord(
                    id="cm-record-1",
                    request_id="cm-request-1",
                    record_type="ensemble",
                    record_key="ensemble",
                    content_sha256=cm_sha256(cm_record_payload),
                    payload_json=cm_record_payload,
                ),
                ConformationalMappingArtifact(
                    artifact_id="cm-artifact-1",
                    request_id="cm-request-1",
                    candidate_id="candidate-1",
                    role="authoritative_cif",
                    relative_path="candidate.cif",
                    storage_path=str(cm_artifact),
                    content_sha256=cm_artifact_sha,
                    size_bytes=cm_artifact.stat().st_size,
                    media_type="chemical/x-mmcif",
                    metadata_json={},
                ),
                FrustraMPNNResult(
                    parent_job_id="frustra-job-1",
                    invocation_id="invoke-1",
                    parent_workflow_id="workflow-1",
                    candidate_id="candidate-1",
                    design_id=None,
                    requiredness="required",
                    request_sha256="7" * 64,
                    source_artifact_id=None,
                    source_artifact_sha256="8" * 64,
                    manifest_sha256=frustrampnn_manifest_sha,
                    manifest_json=frustrampnn_manifest,
                    summary_sha256=frustrampnn_summary_sha,
                    summary_json=frustrampnn_summary,
                    runtime_identity_json={},
                    assigned_gpu_json={},
                    terminal_result_json={"status": "completed"},
                ),
            ]
        )
        await session.commit()

        core_receipt = await _class("CoreProteinResultAdapter")().verify(session, "design-1")
        rfd3_receipt = await _class("Rfd3LocalRedesignAdapter")().verify(session, "rfd3-request-1")
        cm_receipt = await _class("ConformationalMappingProtenixAdapter")().verify(session, "cm-request-1")
        frustra_entity_id = urlencode({"parent_job_id": "frustra-job-1", "invocation_id": "invoke-1"})
        frustra_receipt = await _class("FrustraMpnnResultAdapter")().verify(session, frustra_entity_id)

    assert core_receipt["content_digest"] == structure_sha
    assert core_receipt["reopen_uri"] == "/designs/core-job-1"
    assert rfd3_receipt["entity_id"] == "rfd3-request-1"
    assert rfd3_receipt["content_digest"] == "3" * 64
    assert rfd3_receipt["metadata"]["job_status"] == "completed"
    assert cm_receipt["metadata"]["record_count"] == 1
    assert cm_receipt["metadata"]["artifact_count"] == 1
    assert cm_receipt["reopen_uri"] == "/designs/cm-request-1"
    assert frustra_receipt["entity_id"] == frustra_entity_id
    assert frustra_receipt["content_digest"] == frustrampnn_manifest_sha
    assert frustra_receipt["reopen_uri"] == "/designs/frustra-job-1"


@pytest.mark.asyncio
async def test_core_adapter_fails_without_authoritative_design_digest(adapter_stores):
    _tmp_path, core_factory, _molbio_factory = adapter_stores
    async with core_factory() as session:
        session.add(Job(id="job-no-digest", name="No digest", status="completed", model_id="boltz2", mode="predict", params={}))
        session.add(
            Design(
                id="design-no-digest",
                job_id="job-no-digest",
                name="No digest",
                pdb_path="/not/used/as/identity.pdb",
                review_profile_id="structure_prediction_v1",
                review_contract_version=1,
                review_contract_source="job_identity",
                review_artifact_manifest={"schema": "bms.review-artifacts.v1", "artifacts": {}},
            )
        )
        await session.commit()
        with pytest.raises(adapter_module.AdapterError) as caught:
            await _class("CoreProteinResultAdapter")().verify(session, "design-no-digest")
    assert caught.value.code == "source_contract_unavailable"


@pytest.mark.asyncio
async def test_md_molbio_receipt_sequence_qc_and_alignment_use_native_verifiers(
    adapter_stores, monkeypatch: pytest.MonkeyPatch
):
    tmp_path, core_factory, molbio_factory = adapter_stores
    results_root = tmp_path / "results"
    md_root = results_root / "md-job-1"
    md_root.mkdir(parents=True)
    qc_root = results_root / "qc-job-1" / "fastq_qc"
    qc_root.mkdir(parents=True)
    qc_artifact = qc_root / "summary.tsv"
    qc_artifact.write_bytes(b"metric\tvalue\nreads\t10\n")
    qc_artifact_sha = hashlib.sha256(qc_artifact.read_bytes()).hexdigest()
    qc_without_digest = {
        "schema": "sequence_qc.manifest.v1",
        "artifact_schema_version": 2,
        "job_id": "qc-job-1",
        "artifacts": [
            {
                "kind": "summary",
                "required": True,
                "path": "summary.tsv",
                "sha256": qc_artifact_sha,
                "size_bytes": qc_artifact.stat().st_size,
            }
        ],
    }
    qc_digest = hashlib.sha256(
        json.dumps(qc_without_digest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    (qc_root / "qc_manifest.json").write_text(
        json.dumps({**qc_without_digest, "manifest_sha256": qc_digest}), encoding="utf-8"
    )

    receipt_path = tmp_path / "inputs" / "molbio_ngs_receipts" / "receipt-1" / "expected_reference.fasta"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_bytes(b">molbio_revision_revision-1\nATGC\n")
    receipt_digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()

    normalized_md_request = {
        "schema": "bms.md.job.v2",
        "engine": "openmm",
        "chemistry": {"profile_id": "profile-1", "profile_sha256": "a" * 64},
    }
    aggregate_digest = "b" * 64
    replica_set_digest = "c" * 64

    async with molbio_factory() as session:
        session.add(MolecularDocument(id="sequence-1", document_kind="dna", name="Sequence 1", current_revision_id="revision-1"))
        session.add(
            MolecularRevision(
                id="revision-1",
                document_id="sequence-1",
                revision_number=1,
                change_kind="create",
                content_sha256=hashlib.sha256(b"ATGC").hexdigest(),
                content_length=4,
                snapshot={"name": "Sequence 1", "sequence": "ATGC", "sequence_type": "dna", "topology": "linear"},
                provenance={},
            )
        )
        await session.commit()

    async with core_factory() as session:
        session.add_all(
            [
                Job(
                    id="md-job-1",
                    name="MD",
                    status="completed",
                    queue_status="completed",
                    model_id="molecular_dynamics",
                    mode="simulate",
                    params={},
                    output_dir=str(md_root),
                    provenance={
                        "md": {
                            "state": "completed",
                            "result_state": "completed",
                            "aggregate_manifest_sha256": aggregate_digest,
                            "replica_manifest_set_sha256": replica_set_digest,
                        }
                    },
                ),
                MdRun(
                    job_id="md-job-1",
                    normalized_request=normalized_md_request,
                    request_sha256=md_sha256(normalized_md_request),
                    phase="completed",
                    state_version=7,
                    chemistry_profile_id="profile-1",
                    chemistry_profile_sha256="a" * 64,
                    chemistry_assurance="reviewed",
                    verification_status="completed",
                ),
                Job(id="qc-job-1", name="QC", status="completed", model_id="ont_fastq_qc", mode="analysis", params={}, output_dir=str(results_root / "qc-job-1")),
                MolBioNgsReceipt(
                    id="receipt-1",
                    sequence_id="sequence-1",
                    revision_id="revision-1",
                    revision_sha256=hashlib.sha256(b"ATGC").hexdigest(),
                    reference_snapshot_path=str(receipt_path),
                    reference_snapshot_sha256=receipt_digest,
                    expires_at=datetime.utcnow() + timedelta(hours=1),
                ),
            ]
        )
        await session.commit()

        monkeypatch.setattr(
            adapter_module,
            "md_result_summary",
            lambda job: {
                "schema": "bms.md.summary.v1",
                "job_id": job.id,
                "status": "completed",
                "result_state": "completed",
                "source": "validated_job_owned_manifests",
                "bounded": True,
                "aggregate_manifest_sha256": aggregate_digest,
                "replica_count": 2,
                "artifact_count": 8,
            },
        )
        monkeypatch.setattr(
            adapter_module,
            "build_alignment_sessions",
            lambda job_id, **_kwargs: [
                {
                    "session_id": "derived-session-must-not-be-identity",
                    "job_id": job_id,
                    "mode": "primary",
                    "ready": True,
                    "unavailable_reason": None,
                    "artifacts": {
                        "alignment": {"sha256": "d" * 64, "size_bytes": 10, "integrity_valid": True},
                        "alignment_index": {"sha256": "e" * 64, "size_bytes": 11, "integrity_valid": True},
                        "reference": {"sha256": "f" * 64, "size_bytes": 12, "integrity_valid": True},
                    },
                }
            ],
        )

        md_receipt = await _class("MolecularDynamicsResultAdapter")().verify(session, "md-job-1")
        receipt_adapter = _class("NgsExpectedReferenceReceiptAdapter")(molbio_session_factory=molbio_factory)
        expected_reference_receipt = await receipt_adapter.verify(session, "receipt-1")
        qc_receipt = await _class("SequenceQcReferenceAdapter")().verify(session, "qc-job-1")
        alignment_receipt = await _class("NgsAlignmentViewerReferenceAdapter")().verify(session, "qc-job-1")

    revision_adapter = _class("MolBioRevisionAdapter")(molbio_session_factory=molbio_factory)
    revision_entity_id = urlencode({"sequence_id": "sequence-1", "revision_id": "revision-1"})
    async with core_factory() as session:
        revision_receipt = await revision_adapter.verify(session, revision_entity_id)

    assert md_receipt["metadata"]["state_version"] == 7
    assert md_receipt["metadata"]["job_status"] == "completed"
    assert md_receipt["metadata"]["result_state"] == "completed"
    assert expected_reference_receipt["content_digest"] == receipt_digest
    assert expected_reference_receipt["reopen_uri"] == "/designer?sequence_id=sequence-1&revision_id=revision-1&receipt_id=receipt-1"
    assert revision_receipt["content_digest"] == hashlib.sha256(b"ATGC").hexdigest()
    assert revision_receipt["reopen_uri"] == "/designer?sequence_id=sequence-1&revision_id=revision-1"
    assert qc_receipt["content_digest"] == qc_digest
    assert qc_receipt["reopen_uri"] == "/ngs?job_id=qc-job-1"
    assert alignment_receipt["entity_id"] == "qc-job-1"
    assert "derived-session-must-not-be-identity" not in json.dumps(alignment_receipt)
    assert alignment_receipt["reopen_uri"] == "/ngs?job_id=qc-job-1"


@pytest.mark.asyncio
async def test_search_limits_and_query_lengths_are_bounded(adapter_stores):
    _tmp_path, core_factory, _molbio_factory = adapter_stores
    adapter = _class("CoreProteinResultAdapter")()
    async with core_factory() as session:
        with pytest.raises(adapter_module.AdapterError) as bad_limit:
            await adapter.search(session, query="", limit=101)
        with pytest.raises(adapter_module.AdapterError) as bad_query:
            await adapter.search(session, query="x" * 257, limit=10)
    assert bad_limit.value.code == "invalid_limit"
    assert bad_query.value.code == "invalid_query"


class _ReceiptSession:
    def __init__(self, acknowledgement: dict):
        self.receipt = SimpleNamespace(
            id="receipt-1",
            workspace_id="project-1",
            acknowledgement_json=json.dumps(acknowledgement),
        )

    async def get(self, _model, _receipt_id):
        return self.receipt


@pytest.mark.asyncio
async def test_result_surfaces_dispatch_explicitly_for_every_adapter_entity_kind() -> None:
    cases = {
        "design": "protein_design",
        "rfd3_local_redesign_request": "rfd3_local_redesign",
        "conformational_mapping_request": "conformational_mapping",
        "md_result": "molecular_dynamics",
        "frustrampnn_result": "frustrampnn",
        "molbio_revision": "molbio_revision",
        "ngs_expected_reference_receipt": "ngs_expected_reference",
        "ngs_reference_set": "ngs_reference_set",
        "sequence_qc_job": "sequence_qc",
        "ngs_alignment_job": "ngs_alignment",
    }
    for entity_kind, surface_kind in cases.items():
        acknowledgement = {
            "schema": "bms.global.external-entity-receipt.v1",
            "entity_kind": entity_kind,
            "entity_id": "entity-1",
            "content_digest": "a" * 64,
            "source_build_revision": "build-1",
            "verifier_id": "adapter-1",
            "verified_at": "2026-08-09T00:00:00Z",
            "reopen_uri": "/designs/entity-1",
            "metadata": {"canonical_state": "completed", "result_contract_id": "contract-1"},
        }
        surface = await result_surface_for_receipt(
            _ReceiptSession(acknowledgement),
            project_id="project-1",
            receipt_id="receipt-1",
        )
        assert surface["surface_kind"] == surface_kind
