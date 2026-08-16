from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import (
    Base,
    Job,
    MolBioNgsReceipt,
    NgsReferenceSetManifest,
    NgsReferenceSetMapping,
    RFD3LocalRedesignRequest,
)
from experiment_database import create_experiment_engine, create_experiment_session_factory
from experiment_migrations import run_all
from experiment_models import (
    ExperimentDomainAdapterReceipt,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
)
from experiment_services import RevisionConflict, create_domain_experiment, create_global_experiment, create_project
from scripts.rfd3_local_redesign.contract import request_sha256
from services.global_experiments.adapters import AdapterError, NgsReferenceSetAdapter, Rfd3LocalRedesignAdapter
from services.global_experiments.read_models import build_project_manager_read_model
from services.global_experiments.receipts import attach_verified_entity
from services.global_experiments.result_surfaces import result_surface_for_receipt
import services.global_experiments.adapters as adapter_module
import services.ont_barcode_batches as barcode_batches


SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "docs" / "specs" / "schemas"


def _canonical_sha(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    ).hexdigest()


def _write_rfd3_result(tmp_path: Path, *, job_id: str, request_id: str) -> tuple[dict, str, str, Path]:
    source = tmp_path / "inputs" / f"{job_id}-source.pdb"
    source.write_bytes(b"ATOM      1  CA  ALA A   1\n")
    request = {
        "schema": "bms.rfd3.local-redesign.request.v1",
        "request_id": request_id,
        "profile_id": "default",
        "profile_registry_sha256": "3" * 64,
        "profile": {"name": "default"},
        "input": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
    }
    request_digest = request_sha256(request)
    output_root = tmp_path / "results" / job_id
    collected = output_root / "collected" / "protein_local_redesign"
    candidate_root = collected / "candidates" / "candidate-1"
    candidate_root.mkdir(parents=True)
    native_request = collected / "native_request.json"
    structure = candidate_root / "candidate.pdb"
    metadata = candidate_root / "prediction.json"
    native_request.write_text(json.dumps(request), encoding="utf-8")
    structure.write_bytes(b"ATOM      1  CA  GLY A   1\n")
    metadata.write_text('{"confidence":0.9}', encoding="utf-8")

    def descriptor(role: str, path: Path, relative_path: str) -> dict:
        return {
            "role": role,
            "relative_path": relative_path,
            "storage_path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "media_type": "application/octet-stream",
        }

    source_descriptor = descriptor("source_structure", source, "source.pdb")
    request_descriptor = descriptor(
        "native_request", native_request, "collected/protein_local_redesign/native_request.json"
    )
    candidate_descriptors = [
        descriptor(
            "structure", structure, "collected/protein_local_redesign/candidates/candidate-1/candidate.pdb"
        ),
        descriptor(
            "native_prediction_metadata",
            metadata,
            "collected/protein_local_redesign/candidates/candidate-1/prediction.json",
        ),
    ]
    unsigned_manifest = {
        "schema": "bms.rfd3.local-redesign.result.v1",
        "request_sha256": request_digest,
        "result_contract_id": "rfd3_local_redesign_v1",
        "profile_id": "default",
        "profile_registry_sha256": "3" * 64,
        "profile": {"name": "default"},
        "artifacts": [source_descriptor, request_descriptor, *candidate_descriptors],
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "artifacts": candidate_descriptors,
                "artifact_manifest_sha256": _canonical_sha(candidate_descriptors),
            }
        ],
    }
    manifest_digest = _canonical_sha(unsigned_manifest)
    (collected / "rfd3_result_manifest.json").write_text(
        json.dumps({**unsigned_manifest, "manifest_sha256": manifest_digest}), encoding="utf-8"
    )
    return request, request_digest, manifest_digest, output_root


def _project_payload() -> dict:
    return {
        "schema": "bms.project.v1",
        "name": "Adapter project",
        "description": "",
        "research_objective": "Verify attachments",
        "owner": "operator",
        "contributors": [],
        "tags": [],
        "status": "active",
        "start_date": None,
        "target_end_date": None,
        "external_references": [],
        "created_by": "operator",
        "change_summary": "created",
    }


def _global_payload() -> dict:
    return {
        "schema": "bms.global-experiment.v1",
        "name": "Adapter experiment",
        "objective": "Verify source authorities",
        "scientific_question": "Can records reopen from verified receipts?",
        "hypothesis": None,
        "description": "",
        "status": "active",
        "priority": "normal",
        "tags": [],
        "shared_source_receipt_ids": [],
        "shared_dataset_ids": [],
        "comparison_plan": None,
        "success_criteria": ["Both sources verify"],
        "review_summary": None,
        "conclusion": None,
        "created_by": "operator",
        "change_summary": "created",
    }


def _domain_payload(kind: str) -> dict:
    domain_payload = (
        {
            "schema": "bms.protein-in-silico-experiment.v1",
            "experiment_mode": "redesign",
            "targets": [
                {
                    "target_id": "target-1",
                    "label": "Target 1",
                    "entity_receipt_ids": [],
                    "role": "target",
                }
            ],
            "scientific_objective": "Redesign a local region",
            "design_constraints": [],
            "planned_capabilities": ["rfd3_local_redesign"],
            "comparison_groups": [],
            "validation_strategy": ["boltz2"],
        }
        if kind == "protein_in_silico"
        else {"schema": "bms.ngs-molbio-experiment.v1"}
    )
    return {
        "schema": "bms.domain-experiment.v1",
        "domain_kind": kind,
        "domain_contract_version": "1",
        "name": "Protein" if kind == "protein_in_silico" else "NGS",
        "objective": "Verify attachment",
        "status": "active",
        "tags": [],
        "source_receipt_ids": [],
        "dataset_ids": [],
        "created_by": "operator",
        "change_summary": "created",
        "domain_payload": domain_payload,
    }


@pytest_asyncio.fixture
async def stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BMS_BUILD_SHA", "adapter-test-build")
    results_root = tmp_path / "results"
    results_root.mkdir()
    monkeypatch.setenv("BMS_RESULTS_DIR", str(results_root))
    inputs_root = tmp_path / "inputs"
    inputs_root.mkdir()
    monkeypatch.setattr(adapter_module, "get_inputs_dir", lambda: inputs_root)
    monkeypatch.setattr(barcode_batches, "get_inputs_dir", lambda: inputs_root)
    experiment_path = tmp_path / "experiments.db"
    run_all(experiment_path)
    experiment_engine = create_experiment_engine(f"sqlite+aiosqlite:///{experiment_path}")
    experiment_factory = create_experiment_session_factory(experiment_engine)
    core_path = tmp_path / "core.db"
    core_engine = create_async_engine(f"sqlite+aiosqlite:///{core_path}")
    async with core_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    core_factory = async_sessionmaker(core_engine, expire_on_commit=False)
    try:
        yield tmp_path, experiment_factory, core_factory
    finally:
        await experiment_engine.dispose()
        await core_engine.dispose()


@pytest.mark.asyncio
async def test_rfd3_and_ngs_adapters_attach_idempotently_and_reopen(stores):
    tmp_path, experiment_factory, core_factory = stores
    native_request, native_digest, rfd3_manifest_digest, rfd3_output_root = _write_rfd3_result(
        tmp_path, job_id="rfd3-job-1", request_id="request-1"
    )
    manifest_payload = {
        "schema": "bms.ngs.reference-set.v1",
        "reference_set_id": "ngs-reference-1",
        "source_job_id": "ngs-source-job",
        "mode": "barcoded",
        "entries": [{"unit_id": "barcode01", "child_job_id": "ngs-child-job"}],
    }
    manifest_bytes = json.dumps(
        manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path = tmp_path / "inputs" / "ngs_reference_sets" / "ngs-reference-1" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_bytes(manifest_bytes)
    async with core_factory() as core_session:
        core_session.add(
            Job(
                id="rfd3-job-1",
                name="RFD3 redesign",
                status="completed",
                model_id="protein_local_redesign",
                mode="local_redesign",
                params={
                    "rfd3_request": native_request,
                    "rfd3_request_sha256": native_digest,
                    "rfd3_request_id": "request-1",
                    "rfd3_result_contract_id": "rfd3_local_redesign_v1",
                },
                output_dir=str(rfd3_output_root),
            )
        )
        core_session.add(
            RFD3LocalRedesignRequest(
                request_id="request-1",
                job_id="rfd3-job-1",
                request_sha256=native_digest,
                profile_id="default",
                profile_registry_sha256="3" * 64,
                redesign_mode="local_redesign",
                sequence_policy="fixed",
                status="completed",
                request_json=native_request,
                result_manifest_sha256=rfd3_manifest_digest,
            )
        )
        core_session.add(
            Job(
                id="ngs-source-job",
                name="NGS source",
                status="completed",
                model_id="ont_fastq_qc",
                mode="analysis",
                params={},
            )
        )
        core_session.add(
            Job(
                id="ngs-child-job",
                name="NGS child",
                status="queued",
                model_id="ont_fastq_qc",
                mode="analysis",
                params={},
            )
        )
        core_session.add(
            NgsReferenceSetManifest(
                id="ngs-reference-1",
                manifest_schema="bms.ngs.reference-set.v1",
                mode="barcoded",
                source_job_id="ngs-source-job",
                target_workflow="ont_fastq_qc",
                idempotency_key="ngs-reference-1",
                request_fingerprint="2" * 64,
                manifest_path=str(manifest_path),
                manifest_sha256=manifest_digest,
                manifest_json=manifest_payload,
            )
        )
        core_session.add(
            MolBioNgsReceipt(
                id="ngs-receipt-1",
                sequence_id="sequence-1",
                revision_id="revision-1",
                revision_sha256="5" * 64,
                reference_snapshot_path=str(manifest_path),
                reference_snapshot_sha256=manifest_digest,
                expires_at=datetime.utcnow() + timedelta(hours=1),
                consumed_at=datetime.utcnow(),
                consumed_job_id="ngs-child-job",
            )
        )
        core_session.add(
            NgsReferenceSetMapping(
                id="mapping-1",
                reference_set_id="ngs-reference-1",
                child_job_id="ngs-child-job",
                unit_id="barcode01",
                sample_alias="sample-1",
                sequence_id="sequence-1",
                revision_id="revision-1",
                revision_sha256="5" * 64,
                receipt_id="ngs-receipt-1",
                fasta_snapshot_sha256="6" * 64,
                source_bam_path="source.bam",
                source_bam_sha256="7" * 64,
                source_calls_sha256="8" * 64,
                preflight_sha256="9" * 64,
                demux_manifest_sha256="a" * 64,
                unit_manifest_sha256="b" * 64,
            )
        )
        await core_session.commit()

    async with experiment_factory() as experiment_session:
        project = await create_project(experiment_session, _project_payload())
        global_experiment = await create_global_experiment(
            experiment_session, project.id, _global_payload()
        )
        protein = await create_domain_experiment(
            experiment_session, project.id, global_experiment.id, _domain_payload("protein_in_silico")
        )
        ngs = await create_domain_experiment(
            experiment_session, project.id, global_experiment.id, _domain_payload("ngs_molbio")
        )
        await experiment_session.commit()
        initial_project_generation = project.head_generation

    async with experiment_factory() as experiment_session, core_factory() as core_session:
        rfd3_receipt = await attach_verified_entity(
            experiment_session,
            core_session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_experiment_id=protein.id,
            adapter_id=Rfd3LocalRedesignAdapter.adapter_id,
            entity_id="rfd3-job-1",
            operation="link_output",
            role="produced",
            note=None,
            expected_head_generation=initial_project_generation,
        )
        rfd3_replay = await attach_verified_entity(
            experiment_session,
            core_session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_experiment_id=protein.id,
            adapter_id=Rfd3LocalRedesignAdapter.adapter_id,
            entity_id="rfd3-job-1",
            operation="link_output",
            role="produced",
            note=None,
            expected_head_generation=initial_project_generation,
        )
        assert rfd3_receipt["project_head_generation"] == initial_project_generation + 1
        await experiment_session.commit()
        with pytest.raises(RevisionConflict):
            await attach_verified_entity(
                experiment_session,
                core_session,
                project_id=project.id,
                global_experiment_id=global_experiment.id,
                domain_experiment_id=ngs.id,
                adapter_id=NgsReferenceSetAdapter.adapter_id,
                entity_id="ngs-reference-1",
                operation="attach_reference",
                role="references",
                note=None,
                expected_head_generation=initial_project_generation,
            )
        await experiment_session.rollback()
        ngs_receipt = await attach_verified_entity(
            experiment_session,
            core_session,
            project_id=project.id,
            global_experiment_id=global_experiment.id,
            domain_experiment_id=ngs.id,
            adapter_id=NgsReferenceSetAdapter.adapter_id,
            entity_id="ngs-reference-1",
            operation="attach_reference",
            role="references",
            note=None,
            expected_head_generation=rfd3_receipt["project_head_generation"],
        )
        await experiment_session.commit()
        assert rfd3_replay["attachment_receipt_id"] == rfd3_receipt["attachment_receipt_id"]
        assert rfd3_receipt["operation"] == "link_output"
        assert rfd3_receipt["role"] == "produced"
        assert rfd3_receipt["note"] is None
        assert ngs_receipt["source_receipt"]["content_digest"] == manifest_digest
        adapter_rows = (await experiment_session.execute(select(ExperimentDomainAdapterReceipt))).scalars().all()
        edge_rows = (
            await experiment_session.execute(
                select(ExperimentLineageEdge).where(
                    ExperimentLineageEdge.edge_mode.in_(("produced", "references"))
                )
            )
        ).scalars().all()
        external_rows = (await experiment_session.execute(select(ExperimentExternalEntityReceipt))).scalars().all()
        assert len(adapter_rows) == 2
        assert len(edge_rows) == 2
        assert len(external_rows) == 2
        surface = await result_surface_for_receipt(
            experiment_session,
            project_id=project.id,
            receipt_id=rfd3_receipt["source_receipt_id"],
        )
        assert surface["route"] == "/designs/rfd3-job-1"
        assert surface["readiness"] == "ready"
        read_model = await build_project_manager_read_model(
            experiment_session,
            project_id=project.id,
            focus_id=global_experiment.id,
            selected_node_key=f"external_entity_receipt:{rfd3_receipt['source_receipt_id']}",
        )
        schema = json.loads((SCHEMA_ROOT / "project-manager-read-model-v1.schema.json").read_text())
        Draft202012Validator(schema).validate(read_model)
        assert read_model["selection"]["canonical_surface"]["route"] == "/designs/rfd3-job-1"
        assert read_model["counts"]["attached_entities"] == 2
        assert {node["node_type"] for node in read_model["tree"]["nodes"]} >= {
            "project", "global_experiment", "domain_experiment", "virtual_folder"
        }


@pytest.mark.asyncio
async def test_adapter_digest_mismatch_fails_closed(stores):
    _tmp_path, _experiment_factory, core_factory = stores
    request = {"schema": "bms.rfd3.local-redesign.request.v1", "request_id": "bad"}
    async with core_factory() as core_session:
        core_session.add(
            Job(
                id="rfd3-bad",
                name="Bad digest",
                status="completed",
                model_id="protein_local_redesign",
                mode="local_redesign",
                params={"rfd3_request": request, "rfd3_request_sha256": "0" * 64},
            )
        )
        core_session.add(
            RFD3LocalRedesignRequest(
                request_id="bad",
                job_id="rfd3-bad",
                request_sha256="0" * 64,
                profile_id="default",
                profile_registry_sha256="1" * 64,
                redesign_mode="local_redesign",
                sequence_policy="fixed",
                status="completed",
                request_json=request,
                result_manifest_sha256="2" * 64,
            )
        )
        await core_session.commit()
        with pytest.raises(AdapterError, match="digest"):
            await Rfd3LocalRedesignAdapter().verify(core_session, "rfd3-bad")


@pytest.mark.asyncio
async def test_completed_rfd3_adapter_rejects_missing_native_result_manifest(stores):
    tmp_path, _experiment_factory, core_factory = stores
    request = {"schema": "bms.rfd3.local-redesign.request.v1", "request_id": "missing"}
    digest = request_sha256(request)
    output_root = tmp_path / "results" / "rfd3-missing"
    output_root.mkdir()
    async with core_factory() as core_session:
        core_session.add(
            Job(
                id="rfd3-missing",
                name="Missing manifest",
                status="completed",
                model_id="protein_local_redesign",
                mode="local_redesign",
                params={},
                output_dir=str(output_root),
            )
        )
        core_session.add(
            RFD3LocalRedesignRequest(
                request_id="missing",
                job_id="rfd3-missing",
                request_sha256=digest,
                profile_id="default",
                profile_registry_sha256="1" * 64,
                redesign_mode="local_redesign",
                sequence_policy="fixed",
                status="completed",
                request_json=request,
                result_manifest_sha256="2" * 64,
            )
        )
        await core_session.commit()
        with pytest.raises(AdapterError) as caught:
            await Rfd3LocalRedesignAdapter().verify(core_session, "missing")
    assert caught.value.code == "source_contract_unavailable"


@pytest.mark.parametrize(
    ("damage", "expected_code"),
    [("corrupt_manifest", "source_digest_mismatch"), ("missing_artifact", "source_artifact_unavailable")],
)
@pytest.mark.asyncio
async def test_completed_rfd3_adapter_rejects_corrupt_manifest_or_required_artifact(
    stores, damage: str, expected_code: str
):
    tmp_path, _experiment_factory, core_factory = stores
    job_id = f"rfd3-{damage}"
    request, request_digest, manifest_digest, output_root = _write_rfd3_result(
        tmp_path, job_id=job_id, request_id=damage
    )
    collected = output_root / "collected" / "protein_local_redesign"
    if damage == "corrupt_manifest":
        manifest_path = collected / "rfd3_result_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["manifest_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        (collected / "candidates" / "candidate-1" / "candidate.pdb").unlink()

    async with core_factory() as core_session:
        core_session.add(
            Job(
                id=job_id,
                name="Damaged RFD3 result",
                status="completed",
                model_id="protein_local_redesign",
                mode="local_redesign",
                params={},
                output_dir=str(output_root),
            )
        )
        core_session.add(
            RFD3LocalRedesignRequest(
                request_id=damage,
                job_id=job_id,
                request_sha256=request_digest,
                profile_id="default",
                profile_registry_sha256="3" * 64,
                redesign_mode="local_redesign",
                sequence_policy="fixed",
                status="completed",
                request_json=request,
                result_manifest_sha256=manifest_digest,
            )
        )
        await core_session.commit()
        with pytest.raises(AdapterError) as caught:
            await Rfd3LocalRedesignAdapter().verify(core_session, damage)
    assert caught.value.code == expected_code


@pytest.mark.asyncio
async def test_rfd3_artifact_replacement_cannot_escape_descriptor_bound_root(
    stores,
    monkeypatch,
):
    tmp_path, _experiment_factory, core_factory = stores
    job_id = "rfd3-symlink-swap"
    request, request_digest, manifest_digest, output_root = _write_rfd3_result(
        tmp_path,
        job_id=job_id,
        request_id="symlink-swap",
    )
    relative_target = "collected/protein_local_redesign/candidates/candidate-1/candidate.pdb"
    target = output_root / relative_target
    outside = tmp_path / "outside-candidate.pdb"
    outside.write_bytes(target.read_bytes())
    original_resolve = Path.resolve
    original_stat = adapter_module.os.stat
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes
    source_path = Path(request["input"]["path"]).resolve()
    manifest_path = output_root / "collected" / "protein_local_redesign" / "rfd3_result_manifest.json"
    target_resolve_count = 0
    swapped = False

    def swap_after_storage_identity(self: Path, *args, **kwargs) -> Path:
        nonlocal target_resolve_count, swapped
        resolved = original_resolve(self, *args, **kwargs)
        if self == target and not swapped:
            target_resolve_count += 1
            if target_resolve_count == 2:
                target.unlink()
                target.symlink_to(outside)
                swapped = True
        return resolved

    def swap_after_descriptor_stat(path, *args, **kwargs):
        nonlocal swapped
        observed = original_stat(path, *args, **kwargs)
        if (
            path == target.name
            and kwargs.get("dir_fd") is not None
            and kwargs.get("follow_symlinks") is False
            and not swapped
        ):
            target.unlink()
            target.symlink_to(outside)
            swapped = True
        return observed

    def forbid_unbound_text_reopen(self: Path, *args, **kwargs):
        if self in {manifest_path, source_path}:
            raise AssertionError("RFD3 authority file was reopened by path")
        return original_read_text(self, *args, **kwargs)

    def forbid_unbound_bytes_reopen(self: Path, *args, **kwargs):
        if self in {manifest_path, source_path}:
            raise AssertionError("RFD3 authority file was reopened by path")
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", swap_after_storage_identity)
    monkeypatch.setattr(Path, "read_text", forbid_unbound_text_reopen)
    monkeypatch.setattr(Path, "read_bytes", forbid_unbound_bytes_reopen)
    monkeypatch.setattr(adapter_module.os, "stat", swap_after_descriptor_stat)
    async with core_factory() as core_session:
        core_session.add(
            Job(
                id=job_id,
                name="RFD3 symlink swap",
                status="completed",
                model_id="protein_local_redesign",
                mode="local_redesign",
                params={},
                output_dir=str(output_root),
            )
        )
        core_session.add(
            RFD3LocalRedesignRequest(
                request_id="symlink-swap",
                job_id=job_id,
                request_sha256=request_digest,
                profile_id="default",
                profile_registry_sha256="3" * 64,
                redesign_mode="local_redesign",
                sequence_policy="fixed",
                status="completed",
                request_json=request,
                result_manifest_sha256=manifest_digest,
            )
        )
        await core_session.commit()
        with pytest.raises(AdapterError) as caught:
            await Rfd3LocalRedesignAdapter().verify(core_session, job_id)

    assert caught.value.code in {"source_artifact_unavailable", "source_contract_invalid"}
    assert swapped is True, (caught.value.code, str(caught.value))
