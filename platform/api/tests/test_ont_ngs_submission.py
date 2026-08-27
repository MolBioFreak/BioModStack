from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
ROUTERS_ROOT = API_ROOT / "routers"
for path in (API_ROOT, ROUTERS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from model_registry import ModelRegistry  # noqa: E402
import ont_runs  # noqa: E402
import routers.jobs as jobs_router  # noqa: E402
from schemas import JobResponse, JobStatus  # noqa: E402


def _client_with_fake_create(monkeypatch, captured: dict[str, Any]) -> TestClient:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")

    class FakeSession:
        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    app.dependency_overrides[ont_runs.get_session] = FakeSession
    app.dependency_overrides[ont_runs.get_experiment_session] = FakeSession
    app.dependency_overrides[ont_runs.get_molbio_ngs_session] = FakeSession

    async def fake_create_pipeline_job(
        job_data,
        background_tasks,
        session,
        experiment_session,
        response,
        request,
        *,
        commit=True,
        **_kwargs,
    ):
        captured["job_data"] = job_data
        captured["session"] = session
        return JobResponse(
            id="job-ont-1",
            name=job_data.name,
            status=JobStatus.QUEUED,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params=job_data.params,
            created_at=datetime(2026, 7, 8),
            output_dir="/tmp/out/job-ont-1",
            design_count=0,
        )

    monkeypatch.setattr(ont_runs, "_create_pipeline_job", fake_create_pipeline_job)
    monkeypatch.setattr(ont_runs, "_confine_submitted_path", lambda value, _label, **_kwargs: str(value))
    return TestClient(app)


def test_reference_required_alias_rejects_caller_path_before_job_creation(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    response = client.post(
        "/api/ont/ngs/plasmid_qc/submit",
        json={
            "name": "plasmid A12",
            "params": {
                "fastq_path": "/data/run/A12.fastq.gz",
                "reference_fasta": "/data/refs/A12.fa",
            },
            "pinned_gpu": 0,
        },
    )

    assert response.status_code == 422
    assert "server-controlled" in response.text
    assert captured == {}


@pytest.mark.parametrize(
    ("workflow_id", "params", "mode"),
    [
        ("ont_basecall_dna", {"pod5_dir": "/data/run/pod5"}, "basecall_dna"),
        ("ont_basecall_rna", {"pod5_dir": "/data/run/pod5"}, "basecall_rna"),
    ],
)
def test_each_canonical_ont_workflow_has_a_typed_prelaunch_route(monkeypatch, workflow_id, params, mode) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    response = client.post(f"/api/ont/ngs/{workflow_id}/submit", json={"name": workflow_id, "params": params})

    assert response.status_code == 201, response.text
    job_data = captured["job_data"]
    assert job_data.mode == mode
    assert job_data.params["ont_workflow_id"] == workflow_id
    assert job_data.params["ont_input_mode"] in {"pod5", "bam", "fastq"}


@pytest.mark.parametrize(
    "legacy_params",
    [
        {"run_multimer_qc": True},
        {"run_multimer_qc": True, "run_fastq_qc": False},
    ],
)
def test_typed_ont_submit_rejects_legacy_multimer_qc_for_fresh_jobs(monkeypatch, legacy_params) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    response = client.post(
        "/api/ont/ngs/ont_basecall_dna/submit",
        json={
            "name": "fresh-legacy-alias",
            "params": {"pod5_dir": "/data/run/pod5", **legacy_params},
        },
    )

    assert response.status_code == 422
    assert "run_multimer_qc is read-only legacy compatibility" in response.text
    assert captured == {}


@pytest.mark.parametrize(
    "workflow_id",
    [
        "ont_plasmid_qc",
        "ont_construct_screening",
        "ont_methylation_analysis",
        "ont_fastq_qc",
        "wf_clone_validation",
    ],
)
def test_reference_required_routes_reject_mutable_reference_paths(monkeypatch, workflow_id: str) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)
    response = client.post(
        f"/api/ont/ngs/{workflow_id}/submit",
        json={
            "name": workflow_id,
            "params": {
                "fastq_path": "/data/run/reads.fastq.gz",
                "reference_fasta": "/data/refs/ref.fa",
            },
        },
    )
    assert response.status_code == 422
    assert "immutable MolBio receipt" in response.text
    assert captured == {}


@pytest.mark.parametrize(
    "params",
    [
        {"pod5_dir": "/data/run/pod5", "dorado_basecall_mode": "duplex", "duplex_pairs": "/data/run/pairs.tsv"},
        {"pod5_dir": "/data/run/pod5", "barcode_kit": "SQK-RBK114-96", "sample_sheet": "/data/run/samples.csv"},
    ],
)
def test_dna_duplex_and_rbk114_demux_remain_typed_basecall_modes(monkeypatch, params) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    response = client.post("/api/ont/ngs/ont_basecall_dna/submit", json={"name": "dna mode", "params": params})

    assert response.status_code == 201, response.text
    assert captured["job_data"].params["ont_workflow_id"] == "ont_basecall_dna"


def test_ont_run_plasmid_handoff_submit_builds_and_submits_job(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    async def fake_build_plasmid_qc_handoff(run_id, payload):
        return {
            "model_id": "nanopore",
            "mode": "plasmid_qc",
            "params": {
                "ont_workflow_id": "ont_plasmid_qc",
                "fastq_path": "/data/run/A12.fastq.gz",
                "reference_fasta": payload["reference_fasta"],
                "source_instrument_run_id": run_id,
                "source_instrument_observed_generation": 11,
                "source_minknow_run_id": "MNK-001",
                "source_instrument_observed_generation": 1,
            },
            "fake_or_demo_devices": False,
        }

    monkeypatch.setattr(ont_runs.ont_run_control, "build_plasmid_qc_handoff", fake_build_plasmid_qc_handoff)

    receipt = SimpleNamespace(
        id="receipt-1",
        sequence_id="sequence-1",
        revision_id="revision-1",
        revision_sha256="a" * 64,
        reference_snapshot_sha256="b" * 64,
        reference_snapshot_path="/data/refs/A12.fa",
        consumed_at=None,
        consumed_job_id=None,
    )

    async def fake_validate_receipt(_session, *, receipt_id):
        assert receipt_id == "receipt-1"
        return receipt

    async def fake_consume_receipt(_session, *, receipt_id):
        assert receipt_id == "receipt-1"
        return receipt

    async def fake_attach_instrument_run_evidence(*_args, **kwargs):
        assert kwargs["global_domain_experiment_id"] == "domain-1"
        assert kwargs["state_revision_id"] == "state-revision-1"
        return SimpleNamespace(receipt_id="instrument-receipt-1", content_digest="c" * 64)

    monkeypatch.setattr(ont_runs, "validate_molbio_ngs_receipt", fake_validate_receipt)
    monkeypatch.setattr(ont_runs, "consume_molbio_ngs_receipt", fake_consume_receipt)
    monkeypatch.setattr(ont_runs, "attach_instrument_run_evidence", fake_attach_instrument_run_evidence)

    response = client.post(
        "/api/ont/runs/ont-run-1/handoff/plasmid-qc/submit",
        json={
            "name": "live run plasmid QC",
            "molbio_ngs_receipt_id": "receipt-1",
            "global_domain_experiment_id": "domain-1",
            "molbio_ngs_state_revision_id": "state-revision-1",
            "params": {"igv_report_max_sites": 12},
        },
    )

    assert response.status_code == 201
    job_data = captured["job_data"]
    assert job_data.name == "live run plasmid QC"
    assert job_data.model_id == "nanopore"
    assert job_data.mode == "plasmid_qc"
    assert job_data.params["ont_workflow_id"] == "ont_plasmid_qc"
    assert job_data.params["source_instrument_run_id"] == "ont-run-1"
    assert job_data.params["source_minknow_run_id"] == "MNK-001"
    assert job_data.params["igv_report_max_sites"] == 12


def test_instrument_handoff_submit_rejects_browser_reference_path_before_building_server_handoff(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    app.dependency_overrides[ont_runs.get_session] = lambda: object()
    monkeypatch.setattr(
        ont_runs.ont_run_control,
        "build_plasmid_qc_handoff",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("browser reference paths must not reach handoff builder")),
    )

    response = TestClient(app).post(
        "/api/ont/runs/ont-run-1/handoff/plasmid-qc/submit",
        json={"reference_fasta": "/caller/chosen/reference.fasta"},
    )

    assert response.status_code == 422
    assert "/caller/chosen/reference.fasta" not in response.text


def test_created_ont_job_receives_opaque_alignment_capability(monkeypatch) -> None:
    import routers.jobs as jobs_router
    from services import alignment_access, ont_submission_trust

    created = JobResponse(
        id="job-capability-1",
        name="capability test",
        status=JobStatus.QUEUED,
        model_id="nanopore",
        mode="plasmid_qc",
        params={},
        created_at=datetime(2026, 7, 18),
        output_dir="/tmp/out/job-capability-1",
        design_count=0,
    )
    captured: dict[str, str] = {}

    async def fake_create_job(_job, _tasks, _session, **_kwargs):
        digest = ont_submission_trust.alignment_capability_digest()
        assert digest is not None
        captured["digest"] = digest
        return created

    class Session:
        pass

    monkeypatch.setattr(jobs_router, "create_job", fake_create_job)
    response = Response()
    request = Request(
        {"type": "http", "method": "POST", "scheme": "https", "path": "/api/ont/ngs/plasmid_qc/submit", "headers": [(b"x-forwarded-proto", b"https")]}
    )
    session = Session()

    result = asyncio.run(
        ont_runs._create_pipeline_job(
            type("JobData", (), {})(),
            BackgroundTasks(),
            session,
            session,
            response,
            request,
        )
    )

    assert result.id == created.id
    assert ont_submission_trust.alignment_capability_digest() is None
    digest = captured["digest"]
    cookie_header = response.headers["set-cookie"]
    token = cookie_header.split("=", 1)[1].split(";", 1)[0]
    assert alignment_access.capability_matches(token, digest)
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert "SameSite=strict" in cookie_header
    assert "Max-Age=1800" in cookie_header
    assert "Path=/" in cookie_header
    assert cookie_header.startswith("__Host-bms-ngs-")


def test_capability_issuance_failure_occurs_before_ont_job_creation(monkeypatch) -> None:
    import routers.jobs as jobs_router
    from services import alignment_access, ont_submission_trust

    created = False

    async def fake_create_job(_job, _tasks, _session, **_kwargs):
        nonlocal created
        created = True
        raise AssertionError("job creation must not be reached")

    def fail_issuance():
        raise RuntimeError("injected issuance failure")

    monkeypatch.setattr(jobs_router, "create_job", fake_create_job)
    monkeypatch.setattr(alignment_access, "issue_alignment_access_token", fail_issuance)
    request = Request(
        {"type": "http", "method": "POST", "scheme": "https", "path": "/api/ont/ngs/plasmid_qc/submit", "headers": []}
    )

    with pytest.raises(RuntimeError, match="injected issuance failure"):
        asyncio.run(
            ont_runs._create_pipeline_job(
                type("JobData", (), {})(),
                BackgroundTasks(),
                object(),
                object(),
                Response(),
                request,
            )
        )
    assert created is False
    assert ont_submission_trust.is_trusted_ont_job_creation() is False
    assert ont_submission_trust.alignment_capability_digest() is None


def test_nanopore_model_registry_accepts_direct_ont_product_modes() -> None:
    registry = ModelRegistry()
    for mode, params in {
        "plasmid_qc": {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"},
        "fastq_qc": {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"},
        "basecall_dna": {"pod5_dir": "/tmp/pod5"},
        "basecall_rna": {"pod5_dir": "/tmp/pod5"},
        "construct_screening": {"fastq_path": "/tmp/reads.fastq", "reference_fasta": "/tmp/ref.fa"},
    }.items():
        assert registry.validate_job_params("nanopore", mode, params) == []


def test_public_jobs_route_rejects_all_direct_nanopore_creation() -> None:
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    app.dependency_overrides[jobs_router.get_session] = lambda: object()
    client = TestClient(app)

    for key in sorted(ont_runs.ONT_SERVER_CONTROLLED_PROVENANCE_PARAMS | ont_runs.ONT_SERVER_CONTROLLED_RUNTIME_PARAMS):
        response = client.post(
            "/api/jobs",
            json={
                "name": "untrusted nanopore job",
                "model_id": "nanopore",
                "mode": "plasmid_qc",
                "params": {
                    "fastq_path": "/data/reads.fastq",
                    "reference_fasta": "/data/reference.fasta",
                    key: "/caller/value",
                },
            },
        )
        assert response.status_code == 422, (key, response.text)
        assert "typed /api/ont/ngs" in response.text

    unknown = client.post(
        "/api/jobs",
        json={
            "name": "unknown nanopore key",
            "model_id": "nanopore",
            "mode": "plasmid_qc",
            "params": {
                "fastq_path": "/data/reads.fastq",
                "reference_fasta": "/data/reference.fasta",
                "future_executable_selector": "/caller/code",
            },
        },
    )
    assert unknown.status_code == 422
    assert "typed /api/ont/ngs" in unknown.text
