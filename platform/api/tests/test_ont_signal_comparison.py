from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql.dml import Update

from migrations.add_ont_signal_comparisons import migrate
from migrations.runner import MIGRATIONS
from routers import ont_signal_workbench as router_module
from routers.ont_signal_workbench import (
    ComparisonArtifactResponse,
    ComparisonCreate,
    ComparisonEffectiveSettings,
    ComparisonJobResponse,
    ComparisonOutputManifest,
    ComparisonPreviewCreate,
    ComparisonPreviewResponse,
    ComparisonResourceSnapshot,
    ComparisonRenderParams,
    ComparisonRuntimeIdentities,
    ComparisonViewerSettings,
    ComparisonReviewCreate,
)
from services import ont_signal_workbench as service, ont_submission_trust
from services.ont_signal_worker import OntSignalWorker


class _LookupSession:
    def __init__(self, rows: dict[tuple[str, str], object]) -> None:
        self.rows = rows

    async def get(self, model: type[object], row_id: str) -> object | None:
        return self.rows.get((model.__name__, row_id))


def _comparison_auth_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        role = request.headers.get("x-test-role")
        if role:
            request.state.authenticated_principal = {"subject": "operator-1", "roles": [role]}
        return await call_next(request)

    async def session_override():
        yield object()

    async def forbidden_dispatch(*_args, **_kwargs):
        raise AssertionError("unauthorized comparison request reached the service")

    app.dependency_overrides[router_module.get_session] = session_override
    monkeypatch.setattr(router_module.service, "resolve_signal_comparison_artifact", forbidden_dispatch)
    monkeypatch.setattr(router_module.service, "create_signal_comparison_review", forbidden_dispatch)
    app.include_router(router_module.router, prefix="/api/ont/signal-workbench")
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "method", "body"),
    [
        ("/api/ont/signal-workbench/comparisons/comparison-1/artifacts/artifact-1", "get", None),
        ("/api/ont/signal-workbench/comparisons/comparison-1/reviews", "post", {
            "predecessor_review_id": None,
            "review_question": "Do the traces agree?",
            "required_outcome": "record_only",
            "note": "review",
            "reviewed_start": 10,
            "reviewed_end": 40,
        }),
    ],
)
def test_comparison_artifacts_and_reviews_fail_closed_without_authorized_principal(
    monkeypatch: pytest.MonkeyPatch, path: str, method: str, body: dict[str, object] | None
) -> None:
    client = _comparison_auth_client(monkeypatch)
    unauthenticated = client.request(method, path, json=body)
    unauthorized = client.request(method, path, json=body, headers={"x-test-role": "viewer"})
    assert unauthenticated.status_code == 401
    assert unauthorized.status_code == 403


def test_comparison_principal_uses_only_validated_actor_or_trusted_application_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})
    request.state.authenticated_principal = {"subject": "scientist-1", "roles": ["scientist"]}
    assert router_module._comparison_principal(request) == "scientist-1"

    trusted = Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [(b"x-bms-cm-proxy-secret", b"trusted")],
    })
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "trusted")
    assert router_module._comparison_principal(trusted) == "local-application-operator"


def test_external_alignment_submit_route_uses_only_server_resolved_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.authenticated_principal = {
            "subject": "operator-1", "roles": ["operator"],
        }
        return await call_next(request)

    class FakeSession:
        def __init__(self) -> None:
            self.commits = 0
            self.rollbacks = 0

        async def commit(self) -> None:
            self.commits += 1

        async def rollback(self) -> None:
            self.rollbacks += 1

    fake_session = FakeSession()

    async def session_override():
        yield fake_session

    server_params = {
        "dataset_id": "receipt-1",
        "source_instrument_run_id": "run-1",
        "source_instrument_observed_generation": 7,
        "source_instrument_artifact_manifest_sha256": "e" * 64,
        "source_instrument_artifact_sha256": "c" * 64,
        "source_instrument_artifact_bytes": 123,
        "source_raw_representation_id": "raw-1",
        "source_move_source_id": "moves-1",
        "source_external_move_registration_receipt_id": "receipt-1",
        "source_move_bam_sha256": "a" * 64,
        "source_filtered_move_bam_sha256": "c" * 64,
        "source_read_inventory_sha256": "b" * 64,
        "bam_source_sha256": "c" * 64,
        "global_domain_experiment_id": "domain-1",
        "molbio_ngs_state_revision_id": "state-1",
        "ngs_reference_id": "reference-aggregate-1",
        "ngs_reference_revision_id": "reference-1",
        "ngs_reference_artifact_id": "reference-artifact-1",
        "state_membership_receipt_id": "membership-1",
        "selected_reference_sha256": "d" * 64,
        "expected_reference_fasta_sha256": "d" * 64,
        "managed_reference_snapshot_sha256": "d" * 64,
        "managed_reference_snapshot_size_bytes": 456,
        "expected_result_manifest_schema": "bms.ont-fastq-qc-result.v1",
    }

    async def resolve_authority(*_args, **_kwargs):
        return {
            "dataset_id": "receipt-1",
            "bam_path": "/managed/inputs/external.bam",
            "reference_fasta": "/managed/inputs/reference.fasta",
            "params": server_params,
        }

    captured: dict[str, Any] = {}

    def build_job(workflow_id, request, **kwargs):
        captured.update(workflow_id=workflow_id, request=request, build_kwargs=kwargs)
        return SimpleNamespace(
            name=request.name, model_id="nanopore", mode="plasmid_qc",
            params=dict(request.params), pinned_gpu=None,
        )

    async def create_job(job, *_args, **kwargs):
        captured["create_kwargs"] = kwargs
        return {
            "id": "alignment-job-1", "name": job.name, "status": "queued",
            "model_id": job.model_id, "mode": job.mode, "params": job.params,
        }

    monkeypatch.setattr(
        router_module.service, "resolve_external_alignment_launch_authority",
        resolve_authority,
    )
    monkeypatch.setattr(router_module, "_job_create_for_ont_submit", build_job)
    monkeypatch.setattr(router_module, "_create_pipeline_job", create_job)
    app.dependency_overrides[router_module.get_session] = session_override
    app.dependency_overrides[router_module.get_molbio_ngs_session] = session_override
    app.dependency_overrides[router_module.get_experiment_session] = session_override
    app.include_router(router_module.router, prefix="/api/ont/signal-workbench")

    with TestClient(app) as client:
        response = client.post(
            "/api/ont/signal-workbench/external-alignment-jobs",
            json={
                "move_source_id": "moves-1",
                "reference_revision_id": "reference-1",
                "global_domain_experiment_id": "domain-1",
                "molbio_ngs_state_revision_id": "state-1",
                "name": "BFX6NB exact signal alignment",
            },
        )

    assert response.status_code == 201, response.text
    assert response.json() == {
        "job_id": "alignment-job-1", "name": "BFX6NB exact signal alignment",
        "status": "queued", "dataset_id": "receipt-1", "run_id": "run-1",
        "observed_generation": 7, "move_source_id": "moves-1",
        "reference_revision_id": "reference-1",
    }
    assert "/managed/" not in response.text
    request = captured["request"]
    assert request.source_instrument_run_id == "run-1"
    assert request.params == {
        "bam_path": "/managed/inputs/external.bam",
        "reference_fasta": "/managed/inputs/reference.fasta",
        "bam_force_realign": True,
        **server_params,
    }
    assert captured["workflow_id"] == "ont_plasmid_qc"
    build_kwargs = captured["build_kwargs"]
    assert build_kwargs["trusted_server_params"] == frozenset(
        {*server_params, "bam_force_realign"}
    )
    assert build_kwargs["trusted_result_paths"] == frozenset({"bam_path"})
    assert build_kwargs["trusted_reference_fasta"] == Path(
        "/managed/inputs/reference.fasta"
    )
    assert captured["create_kwargs"]["commit"] is False
    assert fake_session.commits == 1
    assert fake_session.rollbacks == 0


def test_external_alignment_route_validates_before_snapshot_and_discards_unclaimed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.authenticated_principal = {
            "subject": "operator-1", "roles": ["operator"],
        }
        return await call_next(request)

    class FakeSession:
        def __init__(self) -> None:
            self.rollbacks = 0

        async def rollback(self) -> None:
            self.rollbacks += 1

    fake_session = FakeSession()

    async def session_override():
        yield fake_session

    resolver_calls = 0
    authority = {
        "dataset_id": "receipt-1",
        "bam_path": "/managed/inputs/external.bam",
        "reference_fasta": "/managed/inputs/reference.fasta",
        "params": {"source_instrument_run_id": "run-1"},
    }

    async def resolve_authority(*_args, **_kwargs):
        nonlocal resolver_calls
        resolver_calls += 1
        return authority

    def build_job(_workflow_id, request, **_kwargs):
        return SimpleNamespace(
            name=request.name, model_id="nanopore", mode="plasmid_qc",
            params=dict(request.params), pinned_gpu=None,
        )

    async def create_job(*_args, **_kwargs):
        raise ValueError("forced pre-commit failure")

    discarded: list[dict[str, Any]] = []

    def discard(value):
        discarded.append(dict(value))

    monkeypatch.setattr(
        router_module.service, "resolve_external_alignment_launch_authority",
        resolve_authority,
    )
    monkeypatch.setattr(
        router_module.service, "discard_unclaimed_external_alignment_snapshots",
        discard,
        raising=False,
    )
    monkeypatch.setattr(router_module, "_job_create_for_ont_submit", build_job)
    monkeypatch.setattr(router_module, "_create_pipeline_job", create_job)
    app.dependency_overrides[router_module.get_session] = session_override
    app.dependency_overrides[router_module.get_molbio_ngs_session] = session_override
    app.dependency_overrides[router_module.get_experiment_session] = session_override
    app.include_router(router_module.router, prefix="/api/ont/signal-workbench")
    body = {
        "move_source_id": "moves-1",
        "reference_revision_id": "reference-1",
        "global_domain_experiment_id": "domain-1",
        "molbio_ngs_state_revision_id": "state-1",
    }

    with TestClient(app) as client:
        invalid = client.post(
            "/api/ont/signal-workbench/external-alignment-jobs",
            json={**body, "name": "../invalid"},
        )
        failed = client.post(
            "/api/ont/signal-workbench/external-alignment-jobs",
            json={**body, "name": "valid alignment"},
        )

    assert invalid.status_code == 422
    assert failed.status_code == 409
    assert resolver_calls == 1
    assert discarded == [authority]
    assert fake_session.rollbacks == 1


@pytest.mark.parametrize(
    ("failure_stage", "expected_status"),
    [("pipeline_http", 503), ("commit", 500)],
)
def test_external_alignment_route_discards_snapshots_for_every_preclaim_exception(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
    expected_status: int,
) -> None:
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.authenticated_principal = {
            "subject": "operator-1", "roles": ["operator"],
        }
        return await call_next(request)

    class FakeSession:
        def __init__(self) -> None:
            self.rollbacks = 0

        async def commit(self) -> None:
            if failure_stage == "commit":
                raise RuntimeError("forced commit failure")

        async def rollback(self) -> None:
            self.rollbacks += 1

    fake_session = FakeSession()

    async def session_override():
        yield fake_session

    authority = {
        "dataset_id": "receipt-1",
        "bam_path": "/managed/inputs/external.bam",
        "reference_fasta": "/managed/inputs/reference.fasta",
        "params": {"source_instrument_run_id": "run-1"},
    }

    async def resolve_authority(*_args, **_kwargs):
        return authority

    def build_job(_workflow_id, request, **_kwargs):
        return SimpleNamespace(
            name=request.name, model_id="nanopore", mode="plasmid_qc",
            params=dict(request.params), pinned_gpu=None,
        )

    async def create_job(*_args, **_kwargs):
        if failure_stage == "pipeline_http":
            raise HTTPException(status_code=503, detail="forced launch rejection")
        return SimpleNamespace(
            id="alignment-job-1", name="valid alignment", status="queued",
        )

    discarded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        router_module.service, "resolve_external_alignment_launch_authority",
        resolve_authority,
    )
    monkeypatch.setattr(
        router_module.service, "discard_unclaimed_external_alignment_snapshots",
        lambda value: discarded.append(dict(value)),
    )
    monkeypatch.setattr(router_module, "_job_create_for_ont_submit", build_job)
    monkeypatch.setattr(router_module, "_create_pipeline_job", create_job)
    app.dependency_overrides[router_module.get_session] = session_override
    app.dependency_overrides[router_module.get_molbio_ngs_session] = session_override
    app.dependency_overrides[router_module.get_experiment_session] = session_override
    app.include_router(router_module.router, prefix="/api/ont/signal-workbench")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/ont/signal-workbench/external-alignment-jobs",
            json={
                "move_source_id": "moves-1",
                "reference_revision_id": "reference-1",
                "global_domain_experiment_id": "domain-1",
                "molbio_ngs_state_revision_id": "state-1",
                "name": "valid alignment",
            },
        )

    assert response.status_code == expected_status
    if failure_stage == "pipeline_http":
        assert response.json() == {"detail": "forced launch rejection"}
    assert discarded == [authority]
    assert fake_session.rollbacks == 1


@pytest.mark.asyncio
async def test_external_move_source_resolves_exact_server_owned_alignment_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    results_root = tmp_path / "results"
    inputs_root = tmp_path / "inputs"
    filtered_bam = results_root / "move-source-1" / "filtered_moves.bam"
    filtered_bam.parent.mkdir(parents=True)
    bam_bytes = b"filtered-move-bam"
    filtered_bam.write_bytes(bam_bytes)
    filtered_sha = hashlib.sha256(bam_bytes).hexdigest()
    reference_bytes = b">eGFP\nACGT\n"
    reference_sha = hashlib.sha256(reference_bytes).hexdigest()
    managed_reference = (
        inputs_root / "molbio_ngs_managed_launch_snapshots" / "one" / "reference.fasta"
    )
    managed_reference.parent.mkdir(parents=True)
    managed_reference.write_bytes(reference_bytes)
    managed_reference.chmod(0o400)
    receipt_body = {
        "candidate_id": "c" * 64, "server_relative_path": "external.bam",
        "root_device": 1, "root_inode": 2, "file_device": 3, "file_inode": 4,
        "file_mtime_ns": 5, "file_ctime_ns": 6, "artifact_sha256": "a" * 64,
        "artifact_size_bytes": 123, "run_id": "run-1", "observed_generation": 7,
        "raw_representation_id": "raw-1", "molecule_type": "dna",
    }
    receipt_id = f"ont-external-move-{service._digest(receipt_body)}"
    source = SimpleNamespace(
        id="moves-1", run_id="run-1", observed_generation=7,
        raw_representation_id="raw-1", validation_state="ready",
        source_job_id=None, external_registration_receipt_id=receipt_id,
        artifact_sha256="a" * 64, artifact_size_bytes=123,
        read_inventory_sha256="b" * 64, molecule_type="dna",
        validation_receipt={
            "managed_outputs": {"filtered_move_bam": str(filtered_bam)},
            "managed_output_sha256s": {
                "filtered_move_bam_sha256": filtered_sha,
                "filtered_move_bam_size_bytes": len(bam_bytes),
            },
        },
    )
    receipt = SimpleNamespace(id=receipt_id, **receipt_body)
    raw = SimpleNamespace(
        id="raw-1", run_id="run-1", observed_generation=7, state="ready",
        manifest_sha256="e" * 64,
    )
    core = _LookupSession({
        ("OntMoveTableSource", "moves-1"): source,
        ("OntExternalMoveBamRegistrationReceipt", receipt_id): receipt,
        ("OntRawSignalRepresentation", "raw-1"): raw,
    })
    domain = _LookupSession({})

    async def resolve_reference(*_args, **_kwargs):
        return SimpleNamespace(
            global_domain_experiment_id="domain-1",
            molbio_ngs_state_revision_id="state-1",
            ngs_reference_id="reference-aggregate-1",
            ngs_reference_revision_id="reference-revision-1",
            ngs_reference_artifact_id="reference-artifact-1",
            state_membership_receipt_id="membership-1",
            reference_fasta_path=managed_reference,
            selected_reference_sha256=reference_sha,
            expected_reference_fasta_sha256=reference_sha,
            expected_reference_fasta_size_bytes=len(reference_bytes),
            launch_snapshot_sha256=reference_sha,
            launch_snapshot_size_bytes=len(reference_bytes),
        )

    async def resolve_policy(*_args, **_kwargs):
        return "bms.ont-fastq-qc-result.v1"

    monkeypatch.setattr(service, "get_results_dir", lambda: results_root)
    monkeypatch.setattr(service, "get_inputs_dir", lambda: inputs_root, raising=False)
    monkeypatch.setattr(ont_submission_trust, "get_inputs_dir", lambda: inputs_root)
    monkeypatch.setattr(
        service, "resolve_managed_reference_for_launch", resolve_reference,
        raising=False,
    )
    monkeypatch.setattr(
        service, "resolve_state_analysis_launch_policy", resolve_policy,
    )

    authority = await service.resolve_external_alignment_launch_authority(
        core, domain,
        move_source_id="moves-1",
        reference_revision_id="reference-revision-1",
        global_domain_experiment_id="domain-1",
        molbio_ngs_state_revision_id="state-1",
    )

    bam_snapshot = Path(authority["bam_path"])
    assert authority["dataset_id"] == receipt_id
    assert bam_snapshot != filtered_bam
    assert bam_snapshot.is_relative_to(inputs_root / "ont_external_move_launch_snapshots")
    assert bam_snapshot.read_bytes() == bam_bytes
    assert bam_snapshot.stat().st_mode & 0o222 == 0
    assert Path(authority["reference_fasta"]) == managed_reference
    assert authority["params"] == {
        "dataset_id": receipt_id,
        "source_instrument_run_id": "run-1",
        "source_instrument_observed_generation": 7,
        "source_instrument_artifact_manifest_sha256": "e" * 64,
        "source_instrument_artifact_sha256": filtered_sha,
        "source_instrument_artifact_bytes": len(bam_bytes),
        "source_raw_representation_id": "raw-1",
        "source_move_source_id": "moves-1",
        "source_external_move_registration_receipt_id": receipt_id,
        "source_move_bam_sha256": "a" * 64,
        "source_filtered_move_bam_sha256": filtered_sha,
        "source_read_inventory_sha256": "b" * 64,
        "bam_source_sha256": filtered_sha,
        "global_domain_experiment_id": "domain-1",
        "molbio_ngs_state_revision_id": "state-1",
        "ngs_reference_id": "reference-aggregate-1",
        "ngs_reference_revision_id": "reference-revision-1",
        "ngs_reference_artifact_id": "reference-artifact-1",
        "state_membership_receipt_id": "membership-1",
        "selected_reference_sha256": reference_sha,
        "expected_reference_fasta_sha256": reference_sha,
        "managed_reference_snapshot_sha256": reference_sha,
        "managed_reference_snapshot_size_bytes": len(reference_bytes),
        "expected_result_manifest_schema": "bms.ont-fastq-qc-result.v1",
    }

    filtered_bam.rename(filtered_bam.with_suffix(".replaced"))
    filtered_bam.write_bytes(b"mutated")
    ont_submission_trust.verify_launch_input_snapshots({
        "ont_workflow_id": "ont_plasmid_qc",
        "bam_path": str(bam_snapshot),
        "reference_fasta": str(managed_reference),
        **authority["params"],
    })


@pytest.mark.asyncio
async def test_external_move_source_alignment_binding_accepts_only_exact_receipt_authority() -> None:
    receipt_body = {
        "candidate_id": "c" * 64, "server_relative_path": "external.bam",
        "root_device": 1, "root_inode": 2, "file_device": 3, "file_inode": 4,
        "file_mtime_ns": 5, "file_ctime_ns": 6, "artifact_sha256": "a" * 64,
        "artifact_size_bytes": 123, "run_id": "run-1", "observed_generation": 7,
        "raw_representation_id": "raw-1", "molecule_type": "dna",
    }
    receipt_id = f"ont-external-move-{service._digest(receipt_body)}"
    source = SimpleNamespace(
        id="moves-1", source_job_id=None, external_registration_receipt_id=receipt_id,
        run_id="run-1", observed_generation=7, raw_representation_id="raw-1",
        artifact_sha256="a" * 64, artifact_size_bytes=123,
        read_inventory_sha256="b" * 64, molecule_type="dna",
        validation_receipt={
            "managed_output_sha256s": {"filtered_move_bam_sha256": "c" * 64},
        },
    )
    receipt = SimpleNamespace(id=receipt_id, **receipt_body)
    alignment = SimpleNamespace(params={
        "dataset_id": receipt_id,
        "source_instrument_run_id": "run-1",
        "source_instrument_observed_generation": 7,
        "source_raw_representation_id": "raw-1",
        "source_move_source_id": "moves-1",
        "source_external_move_registration_receipt_id": receipt_id,
        "source_move_bam_sha256": "a" * 64,
        "source_filtered_move_bam_sha256": "c" * 64,
        "bam_source_sha256": "c" * 64,
        "source_read_inventory_sha256": "b" * 64,
    })
    session = _LookupSession({
        ("OntExternalMoveBamRegistrationReceipt", receipt_id): receipt,
    })

    binding = await service._require_exact_alignment_read_set_binding(
        session, alignment_job=alignment, move_source=source,
        run_id="run-1", observed_generation=7,
    )

    assert binding == {
        "dataset_id": receipt_id,
        "run_id": "run-1",
        "observed_generation": 7,
        "read_inventory_sha256": "b" * 64,
    }

    alignment.params.pop("source_filtered_move_bam_sha256")
    with pytest.raises(
        service.OntSignalError, match="external move-source alignment authority diverged",
    ):
        await service._require_exact_alignment_read_set_binding(
            session, alignment_job=cast(Any, alignment), move_source=cast(Any, source),
            run_id="run-1", observed_generation=7,
        )

    alignment.params["source_filtered_move_bam_sha256"] = "d" * 64
    alignment.params["bam_source_sha256"] = "d" * 64
    with pytest.raises(
        service.OntSignalError, match="external move-source alignment authority diverged",
    ):
        await service._require_exact_alignment_read_set_binding(
            session, alignment_job=cast(Any, alignment), move_source=cast(Any, source),
            run_id="run-1", observed_generation=7,
        )


def test_alignment_reference_binding_requires_exact_revision_state_and_membership() -> None:
    revision = SimpleNamespace(
        id="reference-revision-1",
        reference_id="reference-1",
        global_domain_experiment_id="domain-1",
        revision_number=4,
        normalized_sequence_sha256="a" * 64,
        canonical_fasta_sha256="b" * 64,
    )
    artifact = SimpleNamespace(id="reference-artifact-1", sha256="b" * 64)
    member = {
        "receipt_id": "membership-1",
        "role": "ngs_reference",
        "source_store_id": "molbio-ngs-domain",
        "entity_kind": "ngs_reference_revision",
        "entity_id": revision.id,
        "source_generation_or_revision": "4",
        "content_digest": revision.canonical_fasta_sha256,
        "source_schema": "bms.molbio-ngs.reference-revision.v1",
        "availability": "available",
        "reopen_destination": {
            "surface": "molbio-ngs-reference-revision",
            "params": {
                "global_domain_experiment_id": revision.global_domain_experiment_id,
                "reference_id": revision.reference_id,
                "revision_id": revision.id,
            },
        },
    }
    params = {
        "reference_sequence_sha256": revision.normalized_sequence_sha256,
        "global_domain_experiment_id": revision.global_domain_experiment_id,
        "molbio_ngs_state_revision_id": "state-1",
        "ngs_reference_id": revision.reference_id,
        "ngs_reference_revision_id": revision.id,
        "ngs_reference_artifact_id": artifact.id,
        "state_membership_receipt_id": member["receipt_id"],
        "selected_reference_sha256": revision.canonical_fasta_sha256,
    }

    assert service._require_exact_alignment_reference_binding(
        params, revision=revision, artifact=artifact, state_membership=member,
    ) == "state-1"

    for field, divergent in (
        ("global_domain_experiment_id", "domain-other"),
        ("molbio_ngs_state_revision_id", ""),
        ("ngs_reference_id", "reference-other"),
        ("ngs_reference_revision_id", "reference-revision-other"),
        ("ngs_reference_artifact_id", "reference-artifact-other"),
        ("state_membership_receipt_id", "membership-other"),
        ("selected_reference_sha256", "c" * 64),
    ):
        candidate = {**params, field: divergent}
        with pytest.raises(service.OntSignalError, match="reference authority"):
            service._require_exact_alignment_reference_binding(
                candidate, revision=revision, artifact=artifact, state_membership=member,
            )


def _request() -> dict[str, object]:
    return {
        "viewer_session_id": "viewer-1",
        "expected_viewer_revision": 3,
        "mapping_artifact_id": "mapping-artifact-1",
        "selected_read_id": "read-1",
        "reference_contig": "plasmid",
        "reference_start": 10,
        "reference_end": 40,
        "simulation_settings": {"profile_id": "dna-r10-min", "seed": 7},
        "render_params": {
            "scale": "none", "point_size": 0.5, "fixed_width": False,
            "base_width": 10, "base_limit": 1000, "signal_sample_limit": 100000,
            "show_samples": True, "show_base_colours": True,
            "remove_signal_outliers": False,
        },
    }


def test_closed_comparison_request_defaults_and_seed_zero_rejection() -> None:
    request = ComparisonPreviewCreate.model_validate(_request())
    assert request.simulation_settings.seed == 7
    assert request.simulation_settings.profile_id == "dna-r10-min"
    unknown = {**_request(), "command": ["squigulator"]}
    with pytest.raises(ValidationError):
        ComparisonPreviewCreate.model_validate(unknown)
    invalid = _request()
    invalid["simulation_settings"] = {"profile_id": "dna-r10-min", "seed": 0}
    with pytest.raises(ValidationError):
        ComparisonPreviewCreate.model_validate(invalid)
    with pytest.raises(ValidationError):
        ComparisonCreate.model_validate({**_request(), "preview_digest": "not-a-digest"})


def test_effective_settings_include_profile_fixed_and_workflow_fixed_values() -> None:
    compiled = service.compile_ideal_comparison_settings(
        {"profile_id": "dna-r10-min", "seed": 7}, _request()["render_params"]
    )
    assert compiled["profile"]["sample_rate"] == 5000
    assert compiled["profile"]["dwell_mean"] == 13.0
    assert compiled["workflow_fixed"] == {
        "simulation_mode": "ideal", "full_contigs": True,
        "amplitude_noise_factor": 0, "dwell_noise": 0, "prefix": False,
        "input_sequence_count": 1, "simulated_signal_record_count": 1,
        "threads": 1, "batch_size": 1, "signal_units": "pA",
        "real_read_count": 1, "reference_hypothesis_count": 1,
        "sequence_basis": "managed_reference",
    }
    assert compiled["compatibility_floor"] == "approximate_profile"
    assert service.comparison_request_fingerprint({**compiled, "x": 1}) != service.comparison_request_fingerprint({**compiled, "x": 2})


def test_comparison_interval_cannot_exceed_effective_base_limit() -> None:
    raw_render_params = _request()["render_params"]
    assert isinstance(raw_render_params, dict)
    render_params = dict(raw_render_params)
    render_params["base_limit"] = 1
    effective = service.compile_ideal_comparison_settings(
        {"profile_id": "dna-r10-min", "seed": 7}, render_params
    )

    with pytest.raises(service.OntSignalError, match="effective base limit"):
        service._require_comparison_interval_within_base_limit(10, 40, effective)


def test_comparison_authority_response_fields_use_closed_typed_models() -> None:
    render_params = _request()["render_params"]
    assert isinstance(render_params, dict)
    compiled = service.compile_ideal_comparison_settings(
        {"profile_id": "dna-r10-min", "seed": 7}, render_params
    )
    compiled.update({
        "compatibility_disposition": "matched_profile",
        "compatibility_evidence": {
            "disposition": "matched_profile",
            "evidence": {
                "mapping_profile_molecule_type": "dna", "mapping_profile_basecall_model_id": "model",
                "mapping_profile_kmer_length": 9, "move_source_molecule_type": "dna",
                "move_source_basecall_model_id": "model", "move_source_runtime_authority": "verified",
                "raw_sample_rate": "5000", "raw_digitisation": "8192", "raw_range": "1536.598389",
                "run_flow_cell_generation": "R10.4.1", "run_device_class": "MinION",
            },
            "missing_authorities": [], "mismatches": [],
        },
    })
    typed = ComparisonEffectiveSettings.model_validate(compiled)
    assert typed.operator_owned.seed == 7
    for model, fields in (
        (ComparisonPreviewResponse, ("selected_read_span", "derived_window", "effective_request")),
        (ComparisonJobResponse, ("simulation_settings", "render_params", "resource_snapshot",
                                 "stage_receipts", "output_manifest")),
    ):
        for field in fields:
            assert "dict[str, Any]" not in str(model.model_fields[field].annotation)


def test_nested_comparison_authority_models_reject_unknown_keys() -> None:
    parents = {
        "reference_fasta_sha256": "a" * 64,
        "mapping_sha256": "b" * 64,
        "mapping_index_sha256": "c" * 64,
        "real_blow5": {
            "routing_sha256": "d" * 64,
            "blow5": [{"sha256": "e" * 64, "index_sha256": "f" * 64}],
        },
        "real_moves_sha256": "1" * 64,
        "raw_manifest_sha256": "2" * 64,
        "run_id": "run-1",
        "observed_generation": 3,
        "selected_read_id": "read-1",
    }
    with pytest.raises(ValidationError):
        ComparisonResourceSnapshot.model_validate({"parents": {**parents, "rogue": True}})
    artifact = {
        "artifact_id": "artifact-1", "kind": "comparison_html",
        "authority_class": "comparison_derived", "media_type": "text/html",
        "sha256": "3" * 64, "size_bytes": 9, "parent_identities": parents,
        "squigulator_runtime_identity": None, "squigualiser_runtime_identity": None,
        "validation_receipt": {"schema": "bms.ont-comparison-render-receipt.v1", "content_sha256": "4" * 64,
                               "rogue": True},
        "created_at": "2026-08-27T00:00:00Z",
    }
    with pytest.raises(ValidationError):
        ComparisonArtifactResponse.model_validate(artifact)
    runtime_identity = {
        "stage": "squigulator_producer", "image": "producer:0.5.0",
        "image_digest": "sha256:" + "5" * 64, "policy_sha256": "6" * 64,
        "wrapper_sha256": "7" * 64,
    }
    with pytest.raises(ValidationError):
        ComparisonRuntimeIdentities.model_validate({"squigulator_producer": runtime_identity, "rogue": {}})
    with pytest.raises(ValidationError):
        ComparisonOutputManifest.model_validate({
            "producer": {"schema": "bms.ont-squigulator-producer-manifest.v1",
                         "content_sha256": "8" * 64, "rogue": True},
        })
    viewer_settings = {
        "simulation_settings": {"profile_id": "dna-r10-min", "seed": 7, "rogue": True},
        "render_params": _request()["render_params"],
    }
    with pytest.raises(ValidationError):
        ComparisonViewerSettings.model_validate(viewer_settings)


def test_persisted_viewer_settings_must_equal_the_immutable_comparison_job() -> None:
    render_params = dict(_request()["render_params"])
    effective = service.compile_ideal_comparison_settings(
        {"profile_id": "dna-r10-min", "seed": 7}, render_params
    )
    comparison = SimpleNamespace(simulation_settings=effective, render_params=render_params)
    expected = {
        "simulation_settings": {"profile_id": "dna-r10-min", "seed": 7},
        "render_params": render_params,
    }
    service._require_comparison_settings_authority(comparison, expected)
    mutations = {
        "profile_id": "dna-r10-prom",
        "seed": 8,
        "scale": "znorm",
        "point_size": 1.0,
        "fixed_width": True,
        "base_width": 11,
        "base_limit": 999,
        "signal_sample_limit": 99999,
        "show_samples": False,
        "show_base_colours": False,
        "remove_signal_outliers": True,
    }
    for field, value in mutations.items():
        divergent = json.loads(json.dumps(expected))
        owner = "simulation_settings" if field in {"profile_id", "seed"} else "render_params"
        divergent[owner][field] = value
        with pytest.raises(service.OntSignalError, match="settings diverge"):
            service._require_comparison_settings_authority(comparison, divergent)


def test_ready_comparison_public_projection_validates_after_path_sanitization() -> None:
    render_params = dict(_request()["render_params"])
    effective = service.compile_ideal_comparison_settings(
        {"profile_id": "dna-r10-min", "seed": 7}, render_params
    )
    effective.update({
        "compatibility_disposition": "matched_profile",
        "compatibility_evidence": {
            "disposition": "matched_profile",
            "evidence": {
                "mapping_profile_molecule_type": "dna", "mapping_profile_basecall_model_id": "model",
                "mapping_profile_kmer_length": 9, "move_source_molecule_type": "dna",
                "move_source_basecall_model_id": "model", "move_source_runtime_authority": "verified",
                "raw_sample_rate": "5000", "raw_digitisation": "8192", "raw_range": "1536.598389",
                "run_flow_cell_generation": "R10.4.1", "run_device_class": "MinION",
            },
            "missing_authorities": [], "mismatches": [],
        },
    })
    parents = {
        "reference_fasta_sha256": "a" * 64, "mapping_sha256": "b" * 64,
        "mapping_index_sha256": "c" * 64,
        "real_blow5": {"routing_sha256": "d" * 64,
                       "blow5": [{"sha256": "e" * 64, "index_sha256": "f" * 64}]},
        "real_moves_sha256": "1" * 64, "raw_manifest_sha256": "2" * 64,
        "run_id": "run-1", "observed_generation": 1, "selected_read_id": "read-1",
    }
    job = _comparison_job(job_id="job-ready-model", state="ready")
    job.simulation_settings = effective
    job.generated_read_id = "generated-1"
    job.resource_snapshot = {"parents": parents}
    job.output_manifest = {
        "schema": "bms.ont-signal-comparison-manifest.v1", "parents": parents,
        "stage_receipts": {},
        "artifacts": [{"kind": "comparison_manifest", "filename": "comparison_manifest.json",
                       "media_type": "application/json", "sha256": "3" * 64, "size_bytes": 9,
                       "validation_receipt": {"schema": "bms.ont-signal-comparison-manifest.v1"}}],
        "producer": {"schema": "bms.ont-squigulator-producer-manifest.v1", "filename": "hidden.json"},
        "renderer": {"schema": "bms.ont-comparison-render-receipt.v1"},
    }
    artifact = service.OntSignalComparisonArtifact(
        id="artifact-ready-model", comparison_job_id=job.id, kind="comparison_manifest",
        authority_class="comparison_derived", managed_relative_path="comparisons/job/manifest.json",
        media_type="application/json", sha256="3" * 64, size_bytes=9,
        parent_identities=parents, validation_receipt={"schema": "bms.ont-signal-comparison-manifest.v1"},
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    typed = ComparisonJobResponse.model_validate(service._comparison_job_public(job, [artifact]))
    assert typed.output_manifest.artifacts
    assert typed.output_manifest.artifacts[0].validation_receipt.content_sha256


def test_failed_comparison_public_projection_redacts_host_path() -> None:
    job = _comparison_job(job_id="job-failed-path", state="failed")
    job.failure_message = "failed to open /mnt/private/run/raw.blow5"
    projected = service._comparison_job_public(job, [])
    assert projected["failure_message"] == "failed to open [redacted-path]"


def test_comparison_point_size_rejects_value_outside_closed_authority_enum() -> None:
    render_params = _request()["render_params"]
    assert isinstance(render_params, dict)
    with pytest.raises(ValidationError):
        ComparisonRenderParams.model_validate({
            **render_params,
            "point_size": 1.5,
        })


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mapping_override", "message"),
    [
        ({"alignment_session_id": "alignment-session-other"}, "alignment authority"),
        ({"parent_mapping_job_id": "read-map-other"}, "saved read mapping"),
    ],
)
async def test_comparison_preview_rejects_cross_alignment_or_parent_mapping_substitution(
    mapping_override: dict[str, str], message: str,
) -> None:
    viewer = SimpleNamespace(
        id="viewer-1", revision=3, selected_read_id="read-1", reference_revision_id="reference-1",
        contig="plasmid", locus_start=10, locus_end=40, raw_representation_id="raw-1",
        mapping_profile_id="profile-1", run_id="run-1", observed_generation=1,
        alignment_job_id="alignment-1", alignment_session_id="alignment-session-1",
        signal_state={"read_mapping_job_id": "read-map-1", "reference_mapping_job_id": "reference-map-1"},
    )
    mapping = SimpleNamespace(**{
        "id": "reference-map-1", "state": "ready", "mode": "signal_to_reference",
        "run_id": "run-1", "observed_generation": 1, "raw_representation_id": "raw-1",
        "mapping_profile_id": "profile-1", "reference_revision_id": "reference-1",
        "move_source_id": "moves-1", "alignment_job_id": "alignment-1",
        "alignment_session_id": "alignment-session-1", "parent_mapping_job_id": "read-map-1",
        **mapping_override,
    })
    read_mapping = SimpleNamespace(
        id="read-map-1", state="ready", mode="signal_to_read", run_id="run-1",
        observed_generation=1, raw_representation_id="raw-1", mapping_profile_id="profile-1",
        move_source_id="moves-1",
    )
    values = {
        (service.OntSignalViewerSession, "viewer-1"): viewer,
        (service.OntSignalMappingArtifact, "artifact-1"): SimpleNamespace(
            id="artifact-1", mapping_job_id="reference-map-1"),
        (service.OntSignalMappingJob, "reference-map-1"): mapping,
        (service.OntSignalMappingJob, "read-map-1"): read_mapping,
        (service.OntRawSignalRepresentation, "raw-1"): SimpleNamespace(
            id="raw-1", state="ready", format="blow5", validation_receipts={"adjacent_index": True}),
        (service.OntSignalMappingProfile, "profile-1"): SimpleNamespace(
            id="profile-1", molecule_type="dna", kmer_length=9),
        (service.OntMoveTableSource, "moves-1"): SimpleNamespace(id="moves-1"),
        (service.OntInstrumentRun, "run-1"): SimpleNamespace(id="run-1"),
    }

    class Session:
        async def get(self, model, identifier):
            return values.get((model, identifier))

    class ForbiddenDomainSession:
        async def get(self, *_args):
            raise AssertionError("substituted mapping reached domain authority")

    render_params = _request()["render_params"]
    assert isinstance(render_params, dict)
    with pytest.raises(service.OntSignalError, match=message):
        await service.preview_signal_comparison(
            Session(), ForbiddenDomainSession(), viewer_session_id="viewer-1",
            expected_viewer_revision=3, mapping_artifact_id="artifact-1", selected_read_id="read-1",
            reference_contig="plasmid", reference_start=10, reference_end=40,
            simulation_settings={"profile_id": "dna-r10-min", "seed": 7},
            render_params=render_params,
        )


def test_review_uses_existing_manual_criterion_vocabulary() -> None:
    approved = ComparisonReviewCreate(
        review_question="Does the real trace visually agree with the ideal expectation?",
        required_outcome="approve", note="Agreement across the selected interval.",
        reviewed_start=10, reviewed_end=40, predecessor_review_id=None,
    )
    assert approved.required_outcome == "approve"
    with pytest.raises(ValidationError):
        ComparisonReviewCreate(
            review_question="question", required_outcome="pass", note="note",
            reviewed_start=10, reviewed_end=40, predecessor_review_id=None,
        )


def _parents(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE ont_instrument_runs (id VARCHAR(80) PRIMARY KEY);
    CREATE TABLE ont_raw_signal_representations (id VARCHAR(96) PRIMARY KEY);
    CREATE TABLE ont_signal_mapping_artifacts (id VARCHAR(96) PRIMARY KEY);
    CREATE TABLE ont_signal_viewer_sessions (id VARCHAR(96) PRIMARY KEY);
    """)


async def _comparison_session_factory(tmp_path: Path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: service.OntSignalComparisonJob.__table__.create(sync, checkfirst=True)
        )
        await connection.run_sync(
            lambda sync: service.OntSignalComparisonEvent.__table__.create(sync, checkfirst=True)
        )
        await connection.run_sync(
            lambda sync: service.OntSignalComparisonArtifact.__table__.create(sync, checkfirst=True)
        )
        await connection.run_sync(
            lambda sync: service.OntSignalManualReview.__table__.create(sync, checkfirst=True)
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _comparison_job(*, job_id: str, state: str, attempt: int = 1, predecessor: str | None = None):
    now = datetime.now(UTC).replace(tzinfo=None)
    return service.OntSignalComparisonJob(
        id=job_id, viewer_session_id="viewer-1", viewer_session_revision=3,
        run_id="run-1", observed_generation=1, raw_representation_id="raw-1",
        mapping_artifact_id="mapping-1", reference_revision_id="reference-1",
        selected_read_id="read-1", reference_contig="plasmid", reference_start=10,
        reference_end=40, simulation_orientation="forward",
        simulation_settings={"profile_id": "dna-r10-min", "seed": 7},
        sequence_basis="managed_reference", render_params=_request()["render_params"],
        preview_digest="a" * 64, request_fingerprint=(job_id.encode().hex() * 64)[:64],
        attempt_number=attempt, predecessor_job_id=predecessor, state=state,
        reason_code=f"comparison_{state}", resource_snapshot={}, stage_receipts={},
        output_manifest={}, created_at=now, updated_at=now,
    )


def _comparison_html(job_id: str):
    return service.OntSignalComparisonArtifact(
        id=f"artifact-{job_id}", comparison_job_id=job_id, kind="comparison_html",
        authority_class="comparison_derived", managed_relative_path=f"comparisons/{job_id}/comparison.html",
        media_type="text/html", sha256="c" * 64, size_bytes=100,
        parent_identities={}, validation_receipt={}, created_at=datetime.now(UTC).replace(tzinfo=None),
    )


@pytest.mark.asyncio
async def test_comparison_expired_leases_are_receipted_and_fail_after_three_executions(
    tmp_path: Path,
) -> None:
    engine, factory = await _comparison_session_factory(tmp_path, "comparison-expiry.db")
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    job = _comparison_job(job_id="job-expiry", state="running")
    job.claim_token = "claim-1"
    job.lease_expires_at = now
    async with factory() as session:
        session.add(job)
        await session.commit()
    worker = OntSignalWorker(factory, factory)

    for execution in range(1, 4):
        recovered_at = now + timedelta(seconds=execution)
        await worker._recover_expired_table(service.OntSignalComparisonJob, "state", recovered_at)
        async with factory() as session:
            current = await session.get(service.OntSignalComparisonJob, "job-expiry")
            assert current is not None
            expected_state = "failed" if execution == 3 else "requested"
            assert current.state == expected_state
            assert current.stage_receipts["lease_recoveries"][-1] == {
                "recovered_at": recovered_at.isoformat(),
                "expired_attempt": execution,
                "max_attempts": 3,
            }
            if execution < 3:
                current.state = "running"
                current.claim_token = f"claim-{execution + 1}"
                current.lease_expires_at = recovered_at
                await session.commit()

    async with factory() as session:
        current = await session.get(service.OntSignalComparisonJob, "job-expiry")
        events = list((await session.execute(
            select(service.OntSignalComparisonEvent)
            .where(service.OntSignalComparisonEvent.comparison_job_id == "job-expiry")
            .order_by(service.OntSignalComparisonEvent.created_at)
        )).scalars())
        assert current is not None
        assert current.reason_code == "expired_lease_retry_exhausted"
        assert current.completed_at == now + timedelta(seconds=3)
        assert [(event.state, event.reason_code) for event in events] == [
            ("requested", "expired_lease_recovered"),
            ("requested", "expired_lease_recovered"),
            ("failed", "expired_lease_retry_exhausted"),
        ]
        assert [event.receipt for event in events] == [
            {"lease_recovery": current.stage_receipts["lease_recoveries"][index]}
            for index in range(3)
        ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_comparison_expired_lease_cancellation_publishes_equal_terminal_event(
    tmp_path: Path,
) -> None:
    engine, factory = await _comparison_session_factory(tmp_path, "comparison-expiry-cancel.db")
    now = datetime.now(UTC).replace(tzinfo=None, microsecond=0)
    job = _comparison_job(job_id="job-expiry-cancel", state="running")
    job.claim_token = "claim-cancel"
    job.lease_expires_at = now
    job.cancel_requested_at = now
    async with factory() as session:
        session.add(job)
        await session.commit()

    worker = OntSignalWorker(factory, factory)
    await worker._recover_expired_table(service.OntSignalComparisonJob, "state", now)

    async with factory() as session:
        current = await session.get(service.OntSignalComparisonJob, "job-expiry-cancel")
        event = (await session.execute(select(service.OntSignalComparisonEvent).where(
            service.OntSignalComparisonEvent.comparison_job_id == "job-expiry-cancel"
        ))).scalar_one()
        assert current is not None
        assert (current.state, current.reason_code, current.completed_at) == (
            "cancelled", "cancelled_after_expired_lease", now,
        )
        assert (event.state, event.reason_code) == (
            "cancelled", "cancelled_after_expired_lease",
        )
        assert event.receipt == {
            "lease_recovery": current.stage_receipts["lease_recoveries"][0]
        }
    await engine.dispose()


@pytest.mark.asyncio
async def test_manual_review_chain_rejects_cross_job_and_nonlatest_predecessors(tmp_path: Path) -> None:
    engine, factory = await _comparison_session_factory(tmp_path, "review-chain.db")
    async with factory() as session:
        session.add_all([_comparison_job(job_id="job-1", state="ready"), _comparison_job(job_id="job-2", state="ready"),
                         _comparison_html("job-1"), _comparison_html("job-2")])
        await session.commit()
        first = await service.create_signal_comparison_review(
            session, "job-1", reviewer_identity="reviewer", predecessor_review_id=None,
            review_question="question", required_outcome="record_only", note="first",
            reviewed_start=10, reviewed_end=40,
        )
        await session.commit()
        with pytest.raises(service.OntSignalError, match="predecessor diverges"):
            await service.create_signal_comparison_review(
                session, "job-2", reviewer_identity="reviewer", predecessor_review_id=first["review_id"],
                review_question="question", required_outcome="record_only", note="cross",
                reviewed_start=10, reviewed_end=40,
            )
        with pytest.raises(service.OntSignalError, match="immutable comparison interval"):
            await service.create_signal_comparison_review(
                session, "job-1", reviewer_identity="reviewer", predecessor_review_id=first["review_id"],
                review_question="question", required_outcome="record_only", note="wrong interval",
                reviewed_start=11, reviewed_end=40,
            )
        with pytest.raises(service.OntSignalError, match="latest review"):
            await service.create_signal_comparison_review(
                session, "job-1", reviewer_identity="reviewer", predecessor_review_id=None,
                review_question="question", required_outcome="approve", note="fork",
                reviewed_start=10, reviewed_end=40,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_comparison_artifact_download_rejects_symlinked_intermediate_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, factory = await _comparison_session_factory(tmp_path, "artifact-confinement.db")
    root = tmp_path / "results" / "ont_signal_workbench"
    real = root / "real"
    real.mkdir(parents=True)
    payload = b"governed"
    (real / "comparison.html").write_bytes(payload)
    (root / "alias").symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(service, "get_results_dir", lambda: tmp_path / "results")
    job = _comparison_job(job_id="job-symlink", state="ready")
    artifact = service.OntSignalComparisonArtifact(
        id="artifact-symlink", comparison_job_id=job.id, kind="comparison_html",
        authority_class="comparison_derived", managed_relative_path="alias/comparison.html",
        media_type="text/html", sha256=service.hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload), parent_identities={}, validation_receipt={},
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    async with factory() as session:
        session.add_all([job, artifact])
        await session.commit()
        with pytest.raises(service.OntSignalError, match="symbolic links"):
            await service.resolve_signal_comparison_artifact(session, job.id, artifact.id)
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_root_reviews_create_one_revision_without_integrity_error(tmp_path: Path) -> None:
    engine, factory = await _comparison_session_factory(tmp_path, "review-root-race.db")
    async with factory() as session:
        session.add_all([_comparison_job(job_id="job-1", state="ready"), _comparison_html("job-1")])
        await session.commit()

    async def invoke(note: str):
        async with factory() as session:
            try:
                result = await service.create_signal_comparison_review(
                    session, "job-1", reviewer_identity="reviewer", predecessor_review_id=None,
                    review_question="question", required_outcome="record_only", note=note,
                    reviewed_start=10, reviewed_end=40,
                )
                await session.commit()
                return result
            except service.OntSignalError as exc:
                return exc

    outcomes = await asyncio.gather(invoke("first"), invoke("second"))
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, service.OntSignalError) for item in outcomes) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(service.OntSignalManualReview)) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_create_replays_the_single_winning_comparison(tmp_path: Path, monkeypatch) -> None:
    engine, factory = await _comparison_session_factory(tmp_path, "create-race.db")
    arrivals = 0
    ready = asyncio.Event()

    async def preview(*_args, **_kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=2)
        return {
            "preview_digest": "a" * 64,
            "effective_request": {
                "authority": {
                    "viewer_session_id": "viewer-1", "viewer_session_revision": 3,
                    "run_id": "run-1", "observed_generation": 1,
                    "raw_representation_id": "raw-1", "mapping_artifact_id": "mapping-1",
                    "reference_revision_id": "reference-1", "selected_read_id": "read-1",
                    "derived_window": {"contig": "plasmid"}, "simulation_orientation": "forward",
                },
                "effective_settings": {"profile_id": "dna-r10-min", "seed": 7},
            },
        }

    monkeypatch.setattr(service, "preview_signal_comparison", preview)

    async def invoke():
        async with factory() as session:
            result = await service.create_signal_comparison(
                session, session, preview_digest="a" * 64, reference_start=10,
                reference_end=40, render_params=_request()["render_params"],
            )
            await session.commit()
            return result

    first, second = await asyncio.gather(invoke(), invoke())
    assert first["comparison_job_id"] == second["comparison_job_id"]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(service.OntSignalComparisonJob)) == 1
        assert await session.scalar(select(func.count()).select_from(service.OntSignalComparisonEvent)) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_cancel_emits_one_terminal_event(tmp_path: Path) -> None:
    engine, factory = await _comparison_session_factory(tmp_path, "cancel-race.db")
    async with factory() as session:
        session.add(_comparison_job(job_id="job-1", state="requested")); await session.commit()

    async def invoke():
        async with factory() as session:
            result = await service.cancel_signal_comparison(session, "job-1")
            await session.commit()
            return result

    first, second = await asyncio.gather(invoke(), invoke())
    assert first["state"] == second["state"] == "cancelled"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(service.OntSignalComparisonEvent)) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_sets_intent_when_worker_claim_wins_the_requested_race(tmp_path: Path) -> None:
    engine, ordinary_factory = await _comparison_session_factory(tmp_path, "cancel-claim-race.db")
    async with ordinary_factory() as session:
        session.add(_comparison_job(job_id="job-1", state="requested")); await session.commit()

    class ClaimWinningSession(AsyncSession):
        claim_injected = False

        async def execute(self, statement, params=None, **kwargs):
            if isinstance(statement, Update) and not self.claim_injected:
                self.claim_injected = True
                async with ordinary_factory() as competitor:
                    await competitor.execute(update(service.OntSignalComparisonJob).where(
                        service.OntSignalComparisonJob.id == "job-1",
                        service.OntSignalComparisonJob.state == "requested",
                    ).values(state="running", reason_code="worker_claimed", claim_token="claim-1"))
                    await competitor.commit()
            return await super().execute(statement, params=params, **kwargs)

    racing_factory = async_sessionmaker(engine, class_=ClaimWinningSession, expire_on_commit=False)
    async with racing_factory() as session:
        result = await service.cancel_signal_comparison(session, "job-1")
        await session.commit()
    assert result["state"] == "running"
    async with ordinary_factory() as session:
        row = await session.get(service.OntSignalComparisonJob, "job-1")
        assert row is not None and row.cancel_requested_at is not None
        assert await session.scalar(select(func.count()).select_from(service.OntSignalComparisonEvent)) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_fresh_attempt_replays_one_successor(tmp_path: Path) -> None:
    engine, factory = await _comparison_session_factory(tmp_path, "fresh-race.db")
    async with factory() as session:
        session.add(_comparison_job(job_id="job-1", state="failed")); await session.commit()

    async def invoke():
        async with factory() as session:
            result = await service.fresh_signal_comparison_attempt(session, "job-1")
            await session.commit()
            return result

    first, second = await asyncio.gather(invoke(), invoke())
    assert first["comparison_job_id"] == second["comparison_job_id"]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(service.OntSignalComparisonJob)) == 2
        assert await session.scalar(select(func.count()).select_from(service.OntSignalComparisonEvent)) == 1
    await engine.dispose()


def test_router_exposes_complete_comparison_lifecycle() -> None:
    from fastapi.routing import APIRoute
    from routers import ont_signal_workbench as comparison_router

    routes = {(route.path, tuple(sorted(route.methods or ()))) for route in comparison_router.router.routes if isinstance(route, APIRoute)}
    expected_paths = {
        "/comparisons/preview", "/comparisons", "/comparisons/{comparison_job_id}",
        "/comparisons/{comparison_job_id}/cancel", "/comparisons/{comparison_job_id}/fresh-attempt",
        "/comparisons/{comparison_job_id}/artifacts/{artifact_id}",
        "/comparisons/{comparison_job_id}/reviews",
    }
    assert expected_paths <= {path for path, _methods in routes}


@pytest.mark.asyncio
async def test_comparison_artifact_resolves_from_stored_workbench_relative_path(
    tmp_path: Path, monkeypatch
) -> None:
    raw = b"<html>Bokeh governed comparison</html>"
    path = tmp_path / "ont_signal_workbench" / "comparisons" / "job-1" / "comparison.html"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    artifact = SimpleNamespace(
        id="artifact-1", comparison_job_id="job-1", kind="comparison_html",
        authority_class="simulated_reference_derived", media_type="text/html",
        managed_relative_path="comparisons/job-1/comparison.html",
        sha256=service.hashlib.sha256(raw).hexdigest(), size_bytes=len(raw),
        parent_identities={}, squigulator_runtime_identity={},
        squigualiser_runtime_identity={}, validation_receipt={}, created_at=None,
    )
    job = SimpleNamespace(id="job-1", state="ready")

    class Session:
        async def get(self, model, identity):
            if model is service.OntSignalComparisonArtifact and identity == "artifact-1":
                return artifact
            if model is service.OntSignalComparisonJob and identity == "job-1":
                return job
            return None

    monkeypatch.setattr(service, "get_results_dir", lambda: tmp_path)
    resolved, metadata = await service.resolve_signal_comparison_artifact(
        Session(), "job-1", "artifact-1"
    )
    assert resolved == raw
    assert metadata["sha256"] == artifact.sha256


def test_migration_42_registers_immutable_comparison_ledgers(tmp_path: Path) -> None:
    db = tmp_path / "comparison.db"
    with sqlite3.connect(db) as connection:
        _parents(connection)
    migrate(str(db)); migrate(str(db))
    with sqlite3.connect(db) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"ont_signal_comparison_jobs", "ont_signal_comparison_events", "ont_signal_comparison_artifacts", "ont_signal_manual_reviews"} <= tables
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {"trg_ont_signal_comparison_job_identity_no_update", "trg_ont_signal_comparison_job_terminal_no_update", "trg_ont_signal_comparison_artifact_no_update", "trg_ont_signal_comparison_events_no_update", "trg_ont_signal_manual_reviews_no_update"} <= triggers
    registration = [(item.version, item.name) for item in MIGRATIONS if item.name == "add_ont_signal_comparisons"]
    assert registration == [(43, "add_ont_signal_comparisons")]


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_external_alignment_resolver_discards_reference_if_bam_publication_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cleanup_fails: bool,
) -> None:
    inputs_root = tmp_path / "inputs"
    results_root = tmp_path / "results"
    reference_snapshot = inputs_root / "molbio_ngs_managed_launch_snapshots" / "one" / "reference.fasta"
    reference_snapshot.parent.mkdir(parents=True)
    reference_bytes = b">eGFP\nACGT\n"
    reference_snapshot.write_bytes(reference_bytes)
    reference_sha = hashlib.sha256(reference_bytes).hexdigest()
    filtered_bam = results_root / "filtered.bam"
    results_root.mkdir()
    filtered_bam.write_bytes(b"bam")
    filtered_sha = hashlib.sha256(filtered_bam.read_bytes()).hexdigest()
    receipt_body = {
        "candidate_id": "c" * 64, "server_relative_path": "external.bam",
        "root_device": 1, "root_inode": 2, "file_device": 3, "file_inode": 4,
        "file_mtime_ns": 5, "file_ctime_ns": 6, "artifact_sha256": "a" * 64,
        "artifact_size_bytes": 123, "run_id": "run-1", "observed_generation": 7,
        "raw_representation_id": "raw-1", "molecule_type": "dna",
    }
    receipt_id = f"ont-external-move-{service._digest(receipt_body)}"
    source = SimpleNamespace(
        id="moves-1", run_id="run-1", observed_generation=7,
        raw_representation_id="raw-1", validation_state="ready",
        source_job_id=None, external_registration_receipt_id=receipt_id,
        artifact_sha256="a" * 64, artifact_size_bytes=123,
        read_inventory_sha256="b" * 64, molecule_type="dna",
        validation_receipt={
            "managed_outputs": {"filtered_move_bam": str(filtered_bam)},
            "managed_output_sha256s": {
                "filtered_move_bam_sha256": filtered_sha,
                "filtered_move_bam_size_bytes": 3,
            },
        },
    )
    core = _LookupSession({
        ("OntMoveTableSource", "moves-1"): source,
        ("OntExternalMoveBamRegistrationReceipt", receipt_id): SimpleNamespace(
            id=receipt_id, **receipt_body,
        ),
        ("OntRawSignalRepresentation", "raw-1"): SimpleNamespace(
            id="raw-1", run_id="run-1", observed_generation=7,
            state="ready", manifest_sha256="e" * 64,
        ),
    })

    async def resolve_reference(*_args, **_kwargs):
        return SimpleNamespace(
            global_domain_experiment_id="domain-1",
            molbio_ngs_state_revision_id="state-1",
            ngs_reference_id="reference-aggregate-1",
            ngs_reference_revision_id="reference-revision-1",
            ngs_reference_artifact_id="reference-artifact-1",
            state_membership_receipt_id="membership-1",
            reference_fasta_path=reference_snapshot,
            selected_reference_sha256=reference_sha,
            expected_reference_fasta_sha256=reference_sha,
            launch_snapshot_sha256=reference_sha,
            launch_snapshot_size_bytes=len(reference_bytes),
        )

    async def resolve_policy(*_args, **_kwargs):
        return "bms.ont-fastq-qc-result.v1"

    def fail_bam_publication(*_args, **_kwargs):
        raise ValueError("forced BAM publication failure")

    monkeypatch.setattr(service, "get_results_dir", lambda: results_root)
    monkeypatch.setattr(service, "get_inputs_dir", lambda: inputs_root)
    monkeypatch.setattr(ont_submission_trust, "get_inputs_dir", lambda: inputs_root)
    monkeypatch.setattr(service, "resolve_managed_reference_for_launch", resolve_reference)
    monkeypatch.setattr(service, "resolve_state_analysis_launch_policy", resolve_policy)
    monkeypatch.setattr(service, "publish_immutable_launch_snapshot", fail_bam_publication)
    if cleanup_fails:
        def fail_cleanup(*_args, **_kwargs):
            raise ValueError("forced cleanup failure")

        monkeypatch.setattr(service, "discard_unclaimed_launch_snapshot", fail_cleanup)

    with pytest.raises(
        service.OntSignalError, match="forced BAM publication failure"
    ) as caught:
        await service.resolve_external_alignment_launch_authority(
            core,
            _LookupSession({}),
            move_source_id="moves-1",
            reference_revision_id="reference-revision-1",
            global_domain_experiment_id="domain-1",
            molbio_ngs_state_revision_id="state-1",
        )

    assert reference_snapshot.exists() is cleanup_fails
    if cleanup_fails:
        assert caught.value.__cause__ is not None
        assert any(
            "ValueError: forced cleanup failure" in note
            for note in getattr(caught.value.__cause__, "__notes__", [])
        )
