from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import services.global_experiments.adapters as adapter_module
from database import (
    Base,
    ConformationalMappingArtifact,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    Design,
    FrustraMPNNResult,
    FrustraMPNNComparison,
    FrustraMPNNGuidancePlan,
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
from experiment_services import ValidationFailure
from services.global_experiments.result_surfaces import result_surface_for_receipt
PROTEIN_TYPED_CORE_JOB_MODELS = {
    "boltz2", "boltz_cp_experimental", "boltzgen", "esmfold2", "ppiflow",
    "protein_local_redesign", "protein_modification_experimental", "protenix", "rf3", "template_antibody_denovo",
}


EXPECTED_ADAPTER_IDS = {
    "bms.core.protein-result-reference.adapter.v1",

    "bms.cm.protenix_v2.adapter.v1",
    "bms.cm.confornets.adapter.v1",
    "bms.md.result-reference.adapter.v1",
    "bms.frustrampnn.result-reference.adapter.v1",
    "bms.frustrampnn.comparison-reference.adapter.v1",
    "bms.frustrampnn.guidance-reference.adapter.v1",
    "bms.molbio.revision-reference.adapter.v1",
    "bms.molbio.construct-reference.adapter.v1",
    "bms.molbio.operation-reference.adapter.v1",
    "bms.ngs.expected-reference-receipt.adapter.v1",
    "bms.ngs.reference-set-reference.adapter.v1",
    "bms.ngs.ont-run-reference.adapter.v1",
    "bms.ngs.pooled-assignment-release.adapter.v1",
    "bms.ngs.sequence-qc-reference.adapter.v1",
    "bms.ngs.analysis-reference.adapter.v1",
    "bms.ngs.alignment-viewer-reference.adapter.v1",
    "bms.molbio.member-molecular-revision.adapter.v1",
    "bms.molbio.member-operation.adapter.v1",
    "bms.molbio.pcr-experiment-revision.adapter.v1",
    "bms.molbio.primer-revision.adapter.v1",
    "bms.ngs-molbio.evidence-assessment.adapter.v1",
    "bms.ngs-molbio.sample-revision.adapter.v1",
    "bms.ngs-molbio.state-revision.adapter.v1",
    "bms.ngs.comparison-panel.adapter.v1",
    "bms.ngs.job-reference.adapter.v1",
    "bms.ngs.ont-observation.adapter.v1",
    "bms.ngs.reference-revision.adapter.v1",
    "bms.ngs.result-manifest.adapter.v1",
}

RESULT_SURFACE_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[3] / "docs/specs/schemas/result-surface-v1.schema.json").read_text(
        encoding="utf-8"
    )
)
RESULT_SURFACE_VALIDATOR = Draft202012Validator(RESULT_SURFACE_SCHEMA)


@pytest_asyncio.fixture
async def adapter_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BMS_BUILD_SHA", "adapter-domain-test-build")
    monkeypatch.setattr(
        adapter_module,
        "source_build_revision",
        lambda: "adapter-domain-test-build",
    )
    monkeypatch.setenv("BMS_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("BMS_INPUTS_DIR", str(tmp_path / "inputs"))
    monkeypatch.setenv("BMS_MD_RESULT_ROOT", str(tmp_path / "results"))
    monkeypatch.setattr(adapter_module, "get_inputs_dir", lambda: tmp_path / "inputs")

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
    assert {item["adapter_id"] for item in adapter_module.registry.list()} == EXPECTED_ADAPTER_IDS | {
        f"bms.core-job.{model_id}.adapter.v1" for model_id in PROTEIN_TYPED_CORE_JOB_MODELS
    }
    with pytest.raises(adapter_module.AdapterError):
        adapter_module.registry.get("core.rfd3-local-redesign.v1")


@pytest.mark.asyncio
async def test_core_rfd3_cm_and_frustrampnn_verify_native_authorities(adapter_stores):
    tmp_path, core_factory, _molbio_factory = adapter_stores
    core_result_root = tmp_path / "results" / "core-job-1"
    core_result_root.mkdir(parents=True)
    structure = core_result_root / "design.pdb"
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
        "profile_id": "default",
        "profile_registry_sha256": "2" * 64,
        "profile": {"name": "default"},
        "input": {"path": str(structure), "sha256": structure_sha},
    }
    rfd3_digest = request_sha256(rfd3_request)
    rfd3_root = tmp_path / "results" / "rfd3-job-1"
    rfd3_collected = rfd3_root / "collected" / "protein_local_redesign"
    rfd3_candidate_root = rfd3_collected / "candidates" / "candidate-1"
    rfd3_candidate_root.mkdir(parents=True)
    rfd3_native_request = rfd3_collected / "native_request.json"
    rfd3_candidate_structure = rfd3_candidate_root / "candidate.pdb"
    rfd3_candidate_metadata = rfd3_candidate_root / "prediction.json"
    rfd3_native_request.write_text(json.dumps(rfd3_request), encoding="utf-8")
    rfd3_candidate_structure.write_bytes(b"ATOM      1  CA  GLY A   1\n")
    rfd3_candidate_metadata.write_text('{"confidence":0.9}', encoding="utf-8")

    def rfd3_descriptor(role: str, path: Path, relative_path: str) -> dict:
        return {
            "role": role,
            "relative_path": relative_path,
            "storage_path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "media_type": "application/octet-stream",
        }

    rfd3_candidate_descriptors = [
        rfd3_descriptor(
            "structure",
            rfd3_candidate_structure,
            "collected/protein_local_redesign/candidates/candidate-1/candidate.pdb",
        ),
        rfd3_descriptor(
            "native_prediction_metadata",
            rfd3_candidate_metadata,
            "collected/protein_local_redesign/candidates/candidate-1/prediction.json",
        ),
    ]
    rfd3_unsigned_manifest = {
        "schema": "bms.rfd3.local-redesign.result.v1",
        "request_sha256": rfd3_digest,
        "result_contract_id": "rfd3_local_redesign_v1",
        "profile_id": "default",
        "profile_registry_sha256": "2" * 64,
        "profile": {"name": "default"},
        "artifacts": [
            rfd3_descriptor("source_structure", structure, "source.pdb"),
            rfd3_descriptor(
                "native_request",
                rfd3_native_request,
                "collected/protein_local_redesign/native_request.json",
            ),
            *rfd3_candidate_descriptors,
        ],
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "artifacts": rfd3_candidate_descriptors,
                "artifact_manifest_sha256": hashlib.sha256(
                    json.dumps(
                        rfd3_candidate_descriptors,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        allow_nan=False,
                    ).encode()
                ).hexdigest(),
            }
        ],
    }
    rfd3_manifest_sha = hashlib.sha256(
        json.dumps(
            rfd3_unsigned_manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    (rfd3_collected / "rfd3_result_manifest.json").write_text(
        json.dumps({**rfd3_unsigned_manifest, "manifest_sha256": rfd3_manifest_sha}), encoding="utf-8"
    )

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
    cm_result_root = tmp_path / "results" / "cm-request-1"
    cm_result_root.mkdir(parents=True)
    cm_artifact = cm_result_root / "cm.cif"
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
    comparison_payload = {
        "comparison_id": "comparison-1",
        "compatibility": {"status": "comparable", "reason": "same contract"},
        "reference": {"parent_job_id": "frustra-job-1", "invocation_id": "invoke-1"},
        "target": {"parent_job_id": "frustra-job-1", "invocation_id": "invoke-1"},
    }
    comparison_sha = hashlib.sha256(
        json.dumps(comparison_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    guidance_payload = {
        "guidance_id": "guidance-1",
        "comparison_id": "comparison-1",
        "actions": [{"action": "review_hotspot", "residue": 42}],
    }
    guidance_sha = hashlib.sha256(
        json.dumps(guidance_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    async with core_factory() as session:
        session.add_all(
            [
                Job(
                    id="core-job-1",
                    name="Core",
                    status="completed",
                    model_id="boltz2",
                    mode="predict",
                    params={},
                    output_dir=str(core_result_root),
                ),
                Job(
                    id="rfd3-job-1",
                    name="RFD3",
                    status="completed",
                    model_id="protein_local_redesign",
                    mode="local_redesign",
                    params={},
                    output_dir=str(rfd3_root),
                ),
                Job(
                    id="cm-request-1",
                    name="CM",
                    status="completed",
                    model_id="conformational_mapping",
                    mode="protenix_v2_ensemble",
                    params={},
                    output_dir=str(cm_result_root),
                ),
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
                    result_manifest_sha256=rfd3_manifest_sha,
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
                FrustraMPNNComparison(
                    comparison_id="comparison-1",
                    reference_parent_job_id="frustra-job-1",
                    reference_invocation_id="invoke-1",
                    target_parent_job_id="frustra-job-1",
                    target_invocation_id="invoke-1",
                    reference_landscape_sha256="a" * 64,
                    target_landscape_sha256="b" * 64,
                    configuration_id="config-1",
                    configuration_sha256="9" * 64,
                    status="completed",
                    comparison_sha256=comparison_sha,
                    payload_json=comparison_payload,
                ),
                FrustraMPNNGuidancePlan(
                    guidance_id="guidance-1",
                    source_landscape_sha256="b" * 64,
                    source_comparison_id="comparison-1",
                    source_parent_job_id="frustra-job-1",
                    source_invocation_id="invoke-1",
                    configuration_id="config-1",
                    configuration_sha256="9" * 64,
                    guidance_sha256=guidance_sha,
                    payload_json=guidance_payload,
                ),
            ]
        )
        await session.commit()

        core_receipt = await _class("CoreProteinResultAdapter")().verify(session, "design-1")
        rfd3_receipt = await _class("Rfd3LocalRedesignAdapter")().verify(session, "rfd3-request-1")
        cm_receipt = await _class("ConformationalMappingProtenixAdapter")().verify(session, "cm-request-1")
        frustra_entity_id = urlencode({"parent_job_id": "frustra-job-1", "invocation_id": "invoke-1"})
        frustra_receipt = await _class("FrustraMpnnResultAdapter")().verify(session, frustra_entity_id)
        comparison_receipt = await _class("FrustraMpnnComparisonAdapter")().verify(session, "comparison-1")
        guidance_receipt = await _class("FrustraMpnnGuidanceAdapter")().verify(session, "guidance-1")

    assert core_receipt["content_digest"] == structure_sha
    assert core_receipt["reopen_uri"] == "/designs/core-job-1"
    assert rfd3_receipt["entity_id"] == "rfd3-request-1"
    assert rfd3_receipt["content_digest"] == rfd3_manifest_sha
    assert rfd3_receipt["metadata"]["job_status"] == "completed"
    assert cm_receipt["metadata"]["record_count"] == 1
    assert cm_receipt["metadata"]["artifact_count"] == 1
    assert cm_receipt["reopen_uri"] == "/designs/cm-request-1"
    assert frustra_receipt["entity_id"] == frustra_entity_id
    assert frustra_receipt["content_digest"] == frustrampnn_manifest_sha
    assert frustra_receipt["reopen_uri"] == "/designs/frustra-job-1?frustrampnn_invocation_id=invoke-1"
    assert comparison_receipt["metadata"]["compatibility"]["status"] == "comparable"
    assert "frustrampnn_comparison_id=comparison-1" in comparison_receipt["reopen_uri"]
    assert guidance_receipt["metadata"]["source_comparison_id"] == "comparison-1"
    result_lineage = next(
        item for item in guidance_receipt["metadata"]["native_lineage"]
        if item["entity_kind"] == "frustrampnn_result"
    )
    assert result_lineage["source_landscape_sha256"] == "b" * 64
    assert "receipt_content_digest" not in result_lineage
    assert "frustrampnn_guidance_id=guidance-1" in guidance_receipt["reopen_uri"]


@pytest.mark.asyncio
async def test_frustrampnn_adapter_verifies_real_v3_source_binding_and_rejects_tampering(
    adapter_stores,
) -> None:
    _tmp_path, core_factory, _molbio_factory = adapter_stores
    manifest = {
        "schema_name": "frustrampnn_result_manifest",
        "schema_version": 3,
        "invocation_id": "invoke-v3-adapter",
        "parent_job_id": "job-v3-adapter",
        "candidate_id": "candidate-v3-adapter",
        "request_sha256": "7" * 64,
        "source_artifact_sha256": "8" * 64,
        "artifacts": [],
    }
    summary = {
        "schema_name": "frustrampnn_summary",
        "schema_version": 3,
        "candidate_id": "candidate-v3-adapter",
    }
    manifest_sha256 = hashlib.sha256(frustrampnn_canonical_bytes(manifest)).hexdigest()
    summary_sha256 = hashlib.sha256(frustrampnn_canonical_bytes(summary)).hexdigest()
    entity_id = urlencode(
        {
            "parent_job_id": "job-v3-adapter",
            "invocation_id": "invoke-v3-adapter",
        }
    )
    adapter = _class("FrustraMpnnResultAdapter")()

    async with core_factory() as session:
        session.add(
            Job(
                id="job-v3-adapter",
                name="Real v3 adapter result",
                status="completed",
                model_id="frustrampnn",
                mode="analyze",
                params={},
            )
        )
        session.add(FrustraMPNNResult(
            parent_job_id="job-v3-adapter",
            invocation_id="invoke-v3-adapter",
            parent_workflow_id="structure_prediction",
            candidate_id="candidate-v3-adapter",
            design_id=None,
            requiredness="required",
            request_sha256="7" * 64,
            source_artifact_id=None,
            source_artifact_sha256="8" * 64,
            manifest_sha256=manifest_sha256,
            manifest_json=manifest,
            summary_sha256=summary_sha256,
            summary_json=summary,
            runtime_identity_json={},
            assigned_gpu_json={},
            terminal_result_json={"status": "completed"},
        ))
        await session.commit()

        receipt = await adapter.verify(session, entity_id)
        assert manifest["schema_version"] == 3
        assert receipt["metadata"]["source_artifact_sha256"] == manifest[
            "source_artifact_sha256"
        ]

        result = await session.get(
            FrustraMPNNResult,
            ("job-v3-adapter", "invoke-v3-adapter"),
        )
        assert result is not None
        result.source_artifact_sha256 = "0" * 64
        await session.flush()
        with pytest.raises(adapter_module.AdapterError) as caught:
            await adapter.verify(session, entity_id)

    assert caught.value.code == "source_digest_mismatch"


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


async def _persist_core_design(
    core_factory,
    *,
    job_id: str,
    design_id: str,
    output_root: Path,
    artifact_path: str,
    artifact_bytes: bytes,
) -> None:
    artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
    async with core_factory() as session:
        session.add(
            Job(
                id=job_id,
                name=job_id,
                status="completed",
                model_id="boltz2",
                mode="predict",
                params={},
                output_dir=str(output_root),
            )
        )
        session.add(
            Design(
                id=design_id,
                job_id=job_id,
                name=design_id,
                pdb_path=artifact_path,
                artifact_class="shape_candidate",
                review_profile_id="shape_blueprint",
                review_contract_version=1,
                review_contract_source="producer",
                review_artifact_manifest={
                    "schema": "bms.review-artifacts.v1",
                    "structure": {
                        "kind": "structure",
                        "state": "ready",
                        "path": artifact_path,
                        "sha256": artifact_digest,
                        "bytes": len(artifact_bytes),
                    },
                    "roles": {"result_role": "structure"},
                },
                review_role_map={"result_role": "structure"},
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_core_adapter_rejects_artifact_outside_source_owned_job_root(adapter_stores):
    tmp_path, core_factory, _molbio_factory = adapter_stores
    output_root = tmp_path / "results" / "core-hostile-root"
    output_root.mkdir(parents=True)
    artifact = tmp_path / "attacker-owned.pdb"
    artifact_bytes = b"ATOM      1  CA  GLY A   1\n"
    artifact.write_bytes(artifact_bytes)
    await _persist_core_design(
        core_factory,
        job_id="core-hostile-root",
        design_id="design-hostile-root",
        output_root=output_root,
        artifact_path=str(artifact),
        artifact_bytes=artifact_bytes,
    )

    async with core_factory() as session:
        with pytest.raises(adapter_module.AdapterError) as caught:
            await _class("CoreProteinResultAdapter")().verify(session, "design-hostile-root")

    assert caught.value.code == "source_artifact_unavailable"


@pytest.mark.asyncio
async def test_core_adapter_hashes_the_descriptor_opened_before_path_replacement(
    adapter_stores,
    monkeypatch: pytest.MonkeyPatch,
):
    tmp_path, core_factory, _molbio_factory = adapter_stores
    output_root = tmp_path / "results" / "core-descriptor"
    output_root.mkdir(parents=True)
    artifact = output_root / "design.pdb"
    artifact_bytes = b"ATOM      1  CA  ALA A   1\n"
    artifact.write_bytes(artifact_bytes)
    await _persist_core_design(
        core_factory,
        job_id="core-descriptor",
        design_id="design-descriptor",
        output_root=output_root,
        artifact_path=str(artifact),
        artifact_bytes=artifact_bytes,
    )

    real_open = os.open
    swapped = False

    def replace_after_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if not swapped and dir_fd is not None and os.fspath(path) == artifact.name:
            artifact.rename(output_root / "opened-design.pdb")
            artifact.write_bytes(b"ATTACKER REPLACEMENT\n")
            swapped = True
        return descriptor

    monkeypatch.setattr(adapter_module.os, "open", replace_after_open)
    async with core_factory() as session:
        receipt = await _class("CoreProteinResultAdapter")().verify(session, "design-descriptor")

    assert swapped is True
    assert receipt["content_digest"] == hashlib.sha256(artifact_bytes).hexdigest()


@pytest.mark.asyncio
async def test_conformational_mapping_adapter_rejects_symlinked_artifact_parent(adapter_stores):
    tmp_path, core_factory, _molbio_factory = adapter_stores
    output_root = tmp_path / "results" / "cm-symlink-parent"
    output_root.mkdir(parents=True)
    outside_root = tmp_path / "cm-attacker"
    outside_root.mkdir()
    outside_artifact = outside_root / "candidate.cif"
    artifact_bytes = b"data_attacker\n"
    outside_artifact.write_bytes(artifact_bytes)
    (output_root / "linked").symlink_to(outside_root, target_is_directory=True)
    linked_artifact = output_root / "linked" / outside_artifact.name

    request_without_hash = {
        "schema_name": "cm_request",
        "schema_version": 1,
        "request_id": "cm-symlink-parent",
        "backend": "protenix_v2_ensemble",
    }
    request = {**request_without_hash, "request_sha256": cm_sha256(request_without_hash)}
    plan_without_hash = {
        "schema_name": "cm_coordinate_plan",
        "schema_version": 1,
        "request_id": "cm-symlink-parent",
        "backend": "protenix_v2_ensemble",
        "request_sha256": request["request_sha256"],
        "expected_cardinality": 1,
        "coordinates": [{"candidate": 0}],
    }
    plan = {**plan_without_hash, "coordinate_plan_sha256": cm_sha256(plan_without_hash)}
    record_payload = {"schema_name": "cm_ensemble", "request_id": "cm-symlink-parent"}
    async with core_factory() as session:
        session.add_all(
            [
                Job(
                    id="cm-symlink-parent",
                    name="CM hostile parent",
                    status="completed",
                    model_id="conformational_mapping",
                    mode="protenix_v2_ensemble",
                    params={},
                    output_dir=str(output_root),
                ),
                ConformationalMappingRequest(
                    request_id="cm-symlink-parent",
                    job_id="cm-symlink-parent",
                    principal_id="operator",
                    backend="protenix_v2_ensemble",
                    status="completed",
                    request_sha256=request["request_sha256"],
                    coordinate_plan_sha256=plan["coordinate_plan_sha256"],
                    resume_key="4" * 64,
                    result_contract_id="conformational_mapping_protenix_v1",
                    request_json=request,
                    coordinate_plan_json=plan,
                    progress_json={},
                ),
                ConformationalMappingRecord(
                    id="cm-record-symlink-parent",
                    request_id="cm-symlink-parent",
                    record_type="ensemble",
                    record_key="ensemble",
                    content_sha256=cm_sha256(record_payload),
                    payload_json=record_payload,
                ),
                ConformationalMappingArtifact(
                    artifact_id="cm-artifact-symlink-parent",
                    request_id="cm-symlink-parent",
                    candidate_id="candidate-1",
                    role="authoritative_cif",
                    relative_path="linked/candidate.cif",
                    storage_path=str(linked_artifact),
                    content_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
                    size_bytes=len(artifact_bytes),
                    media_type="chemical/x-mmcif",
                    metadata_json={},
                ),
            ]
        )
        await session.commit()
        with pytest.raises(adapter_module.AdapterError) as caught:
            await _class("ConformationalMappingProtenixAdapter")().verify(session, "cm-symlink-parent")

    assert caught.value.code == "source_artifact_unavailable"


@pytest.mark.asyncio
async def test_expected_reference_adapter_rejects_traversal_outside_inputs_root(adapter_stores):
    tmp_path, core_factory, molbio_factory = adapter_stores
    inputs_root = tmp_path / "inputs"
    inputs_root.mkdir(exist_ok=True)
    outside_root = tmp_path / "outside-inputs"
    outside_root.mkdir()
    reference = outside_root / "expected_reference.fasta"
    reference_bytes = b">molbio_revision_revision-traversal\nATGC\n"
    reference.write_bytes(reference_bytes)
    traversal_path = str(inputs_root / ".." / outside_root.name / reference.name)
    revision_digest = hashlib.sha256(b"ATGC").hexdigest()

    async with molbio_factory() as session:
        session.add(
            MolecularRevision(
                id="revision-traversal",
                document_id="sequence-traversal",
                revision_number=1,
                change_kind="create",
                content_sha256=revision_digest,
                content_length=4,
                snapshot={"name": "Traversal", "sequence": "ATGC", "sequence_type": "dna"},
                provenance={},
            )
        )
        await session.commit()

    async with core_factory() as session:
        session.add(
            MolBioNgsReceipt(
                id="receipt-traversal",
                sequence_id="sequence-traversal",
                revision_id="revision-traversal",
                revision_sha256=revision_digest,
                reference_snapshot_path=traversal_path,
                reference_snapshot_sha256=hashlib.sha256(reference_bytes).hexdigest(),
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
        )
        await session.commit()
        adapter = _class("NgsExpectedReferenceReceiptAdapter")(molbio_session_factory=molbio_factory)
        with pytest.raises(adapter_module.AdapterError) as caught:
            await adapter.verify(session, "receipt-traversal")

    assert caught.value.code == "source_artifact_unavailable"


@pytest.mark.asyncio
async def test_expected_reference_adapter_rejects_symlinked_final_artifact(adapter_stores):
    tmp_path, core_factory, molbio_factory = adapter_stores
    receipt_root = tmp_path / "inputs" / "molbio_ngs_receipts" / "receipt-final-link"
    receipt_root.mkdir(parents=True)
    outside = tmp_path / "outside-reference.fasta"
    reference_bytes = b">molbio_revision_revision-final-link\nATGC\n"
    outside.write_bytes(reference_bytes)
    reference = receipt_root / "expected_reference.fasta"
    reference.symlink_to(outside)
    revision_digest = hashlib.sha256(b"ATGC").hexdigest()

    async with molbio_factory() as session:
        session.add(
            MolecularRevision(
                id="revision-final-link",
                document_id="sequence-final-link",
                revision_number=1,
                change_kind="create",
                content_sha256=revision_digest,
                content_length=4,
                snapshot={"name": "Final link", "sequence": "ATGC", "sequence_type": "dna"},
                provenance={},
            )
        )
        await session.commit()

    async with core_factory() as session:
        session.add(
            MolBioNgsReceipt(
                id="receipt-final-link",
                sequence_id="sequence-final-link",
                revision_id="revision-final-link",
                revision_sha256=revision_digest,
                reference_snapshot_path=str(reference),
                reference_snapshot_sha256=hashlib.sha256(reference_bytes).hexdigest(),
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
        )
        await session.commit()
        adapter = _class("NgsExpectedReferenceReceiptAdapter")(molbio_session_factory=molbio_factory)
        with pytest.raises(adapter_module.AdapterError) as caught:
            await adapter.verify(session, "receipt-final-link")

    assert caught.value.code == "source_artifact_unavailable"


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
        "workflow_id": "ont_fastq_qc",
        "job_id": "qc-job-1",
        "input_mode": "fastq",
        "analysis_status": "completed",
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
                Job(id="qc-job-1", name="QC", status="completed", model_id="ont_fastq_qc", mode="analysis", params={"ont_workflow_id": "ont_fastq_qc", "ont_input_mode": "fastq"}, output_dir=str(results_root / "qc-job-1")),
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
        qc_adapter = _class("SequenceQcReferenceAdapter")()
        qc_receipt = await qc_adapter.verify(session, "qc-job-1")
        mismatched_qc = {**qc_without_digest, "job_id": "qc-job-other"}
        mismatched_digest = hashlib.sha256(
            json.dumps(mismatched_qc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        (qc_root / "qc_manifest.json").write_text(
            json.dumps({**mismatched_qc, "manifest_sha256": mismatched_digest}), encoding="utf-8"
        )
        with pytest.raises(adapter_module.AdapterError) as mismatched:
            await qc_adapter.verify(session, "qc-job-1")
        assert mismatched.value.code == "source_contract_invalid"
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
    assert qc_receipt["metadata"]["workflow_id"] == "ont_fastq_qc"
    assert qc_receipt["metadata"]["input_mode"] == "fastq"
    assert qc_receipt["metadata"]["analysis_status"] == "completed"
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
    def __init__(self, acknowledgement: dict, **receipt_overrides):
        receipt = {
            "id": "receipt-1",
            "workspace_id": "project-1",
            "store_id": acknowledgement.get("store_id", "core"),
            "entity_kind": acknowledgement.get("entity_kind"),
            "entity_id": acknowledgement.get("entity_id"),
            "generation_or_revision": str(acknowledgement.get("entity_revision_id", "1")),
            "content_digest": acknowledgement.get("content_digest"),
            "availability": "available",
            "verification_authority": acknowledgement.get("verifier_id"),
            "acknowledgement_json": json.dumps(acknowledgement),
        }
        receipt.update(receipt_overrides)
        self.receipt = SimpleNamespace(**receipt)

    async def get(self, _model, _receipt_id):
        return self.receipt


@pytest.mark.parametrize(
    ("acknowledgement_overrides", "receipt_overrides"),
    [
        ({}, {"availability": "unavailable"}),
        ({}, {"verification_authority": "legacy_unverified"}),
        ({}, {"verification_authority": "other.server-adapter.v1"}),
        ({}, {"store_id": "other-store"}),
        ({}, {"entity_kind": "other-kind"}),
        ({}, {"entity_id": "other-entity"}),
        ({}, {"generation_or_revision": "other-revision"}),
        ({}, {"content_digest": "f" * 64}),
        ({"availability": "unavailable"}, {}),
    ],
)
@pytest.mark.asyncio
async def test_result_surface_rejects_receipt_authority_or_acknowledgement_mismatch(
    acknowledgement_overrides: dict,
    receipt_overrides: dict,
) -> None:
    acknowledgement = {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": "core",
        "entity_kind": "design",
        "entity_id": "entity-1",
        "entity_revision_id": "revision-1",
        "content_digest": "a" * 64,
        "source_build_revision": "build-1",
        "verifier_id": "test.server-adapter.v1",
        "verified_at": "2026-08-09T00:00:00Z",
        "availability": "available",
        "reopen_uri": "/designs/entity-1",
        "metadata": {"job_status": "completed"},
        **acknowledgement_overrides,
    }

    with pytest.raises(ValidationFailure, match="verified|authority|available|match"):
        await result_surface_for_receipt(
            _ReceiptSession(acknowledgement, **receipt_overrides),
            project_id="project-1",
            receipt_id="receipt-1",
        )


@pytest.mark.asyncio
async def test_result_surfaces_dispatch_explicitly_for_every_adapter_entity_kind() -> None:
    broad_surface_kinds = {
        "design": "protein_design",
        "rfd3_local_redesign_request": "protein_design",
        "conformational_mapping_request": "conformational_mapping",
        "md_result": "molecular_dynamics",
        "frustrampnn_result": "frustrampnn",
        "frustrampnn_comparison": "frustrampnn",
        "frustrampnn_guidance": "frustrampnn",
        "molbio_revision": "molbio",
        "molbio_construct_revision": "molbio",
        "molbio_operation": "molbio",
        "molecular_revision": "molbio",
        "molecular_operation": "molbio",
        "primer_revision": "molbio",
        "pcr_experiment_revision": "molbio",
        "sample_revision": "molbio",
        "ngs_molbio_state_revision": "molbio",
        "ngs_expected_reference_receipt": "ngs",
        "ngs_reference_set": "ngs",
        "sequence_qc_job": "ngs",
        "ngs_alignment_job": "ngs",
        "ont_instrument_run": "ngs",
        "ngs_pooled_assignment_release": "ngs",
        "ngs_analysis_job": "ngs",
        "ngs_reference_revision": "ngs",
        "ngs_comparison_panel": "ngs",
        "ngs_job": "ngs",
        "ngs_result_manifest": "ngs",
        "ngs_evidence_assessment": "ngs",
        "typed_core_job_result": "protein_design",
    }
    registered = adapter_module.registry.list()
    assert {item["adapter_id"] for item in registered} == EXPECTED_ADAPTER_IDS | {
        f"bms.core-job.{model_id}.adapter.v1" for model_id in PROTEIN_TYPED_CORE_JOB_MODELS
    }
    for adapter in registered:
        entity_kind = adapter["entity_kind"]
        reopen_uri = f"/native/results/entity-1?adapter={adapter['adapter_id']}&view=summary%2Fdetails"
        metadata = {
            "canonical_state": "available",
            "job_status": "completed",
            "request_status": "completed",
            "result_state": "completed",
            "ready_session_count": 1,
            "result_contract_id": "contract-1",
        }
        if entity_kind == "ont_instrument_run":
            metadata.update({
                "state": "available",
                "observed_generation": 1,
                "event_type": "status",
                "observation_reason": "event=status; state=available; observed_generation=1",
            })
        elif entity_kind == "ngs_evidence_assessment":
            metadata.update({
                "scientific_assessment": "REVIEW",
                "assessment_rule_id": "test-rule",
                "job_lifecycle_state": "completed",
                "manifest_integrity": "verified",
                "scientific_assessment_reason": "rule=test-rule; job_lifecycle_state=completed; manifest_integrity=verified",
            })
        acknowledgement = {
            "schema": "bms.global.external-entity-receipt.v1",
            "store_id": "core",
            "entity_kind": entity_kind,
            "entity_id": "entity-1",
            "entity_revision_id": "1",
            "content_digest": "a" * 64,
            "source_build_revision": "build-1",
            "verifier_id": adapter["adapter_id"],
            "verified_at": "2026-08-09T00:00:00Z",
            "availability": "available",
            "reopen_uri": reopen_uri,
            "metadata": metadata,
        }
        surface = await result_surface_for_receipt(
            _ReceiptSession(acknowledgement),
            project_id="project-1",
            receipt_id="receipt-1",
        )
        RESULT_SURFACE_VALIDATOR.validate(surface)
        assert surface["surface_kind"] == broad_surface_kinds[entity_kind]
        assert surface["route"] == {
            "template_id": "bms.route.verified-external-entity.v1",
            "path": "/native/results/entity-1",
            "query": {"adapter": adapter["adapter_id"], "view": "summary/details"},
        }
        assert surface["native_summary"]["payload"] == acknowledgement["metadata"]


@pytest.mark.asyncio
async def test_result_surface_fallback_states_conform_to_frozen_schema() -> None:
    for entity_kind in {item["entity_kind"] for item in adapter_module.registry.list()}:
        metadata = {}
        if entity_kind == "ont_instrument_run":
            metadata = {
                "state": "available",
                "observed_generation": 1,
                "event_type": "status",
                "observation_reason": "event=status; state=available; observed_generation=1",
            }
        elif entity_kind == "ngs_evidence_assessment":
            metadata = {
                "scientific_assessment": "REVIEW",
                "assessment_rule_id": "test-rule",
                "job_lifecycle_state": "completed",
                "manifest_integrity": "verified",
                "scientific_assessment_reason": "rule=test-rule; job_lifecycle_state=completed; manifest_integrity=verified",
            }
        acknowledgement = {
            "schema": "bms.global.external-entity-receipt.v1",
            "store_id": "core",
            "entity_kind": entity_kind,
            "entity_id": "entity-1",
            "entity_revision_id": "1",
            "content_digest": "b" * 64,
            "source_build_revision": "build-1",
            "verifier_id": "adapter-1",
            "verified_at": "2026-08-09T00:00:00Z",
            "availability": "available",
            "reopen_uri": "/native/results/entity-1?state=unknown",
            "metadata": metadata,
        }
        surface = await result_surface_for_receipt(
            _ReceiptSession(acknowledgement),
            project_id="project-1",
            receipt_id="receipt-1",
        )
        RESULT_SURFACE_VALIDATOR.validate(surface)


@pytest.mark.asyncio
async def test_unknown_adapter_receipt_returns_schema_valid_unsupported_descriptor() -> None:
    acknowledgement = {
        "schema": "bms.global.external-entity-receipt.v1",
        "store_id": "core",
        "entity_kind": "future_adapter_receipt",
        "entity_id": "entity-1",
        "entity_revision_id": "1",
        "content_digest": "c" * 64,
        "source_build_revision": "build-1",
        "verifier_id": "future.adapter.v1",
        "verified_at": "2026-08-09T00:00:00Z",
        "availability": "available",
        "reopen_uri": "/future/results/entity-1?mode=exact%2Fnative",
        "metadata": {"canonical_state": "available", "detail": "bounded native summary"},
    }

    surface = await result_surface_for_receipt(
        _ReceiptSession(acknowledgement),
        project_id="project-1",
        receipt_id="receipt-1",
    )

    RESULT_SURFACE_VALIDATOR.validate(surface)
    assert surface["surface_kind"] == "unsupported"
    assert surface["readiness"] == "unsupported"
    assert surface["route"] is None
    assert surface["native_summary"]["payload"] == acknowledgement["metadata"]
    assert surface["scientific_acceptance"]["state"] == "not_applicable"
    assert 0 < len(surface["scientific_acceptance"]["reason"]) <= 256
    assert surface["available_actions"] == []
