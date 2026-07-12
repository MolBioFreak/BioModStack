from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

API_ROOT = Path(__file__).resolve().parents[1]
ROUTERS_ROOT = API_ROOT / "routers"
for path in (API_ROOT, ROUTERS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from model_registry import ModelRegistry  # noqa: E402
import ont_runs  # noqa: E402
from schemas import JobResponse, JobStatus  # noqa: E402


def _client_with_fake_create(monkeypatch, captured: dict[str, Any]) -> TestClient:
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    app.dependency_overrides[ont_runs.get_session] = lambda: object()

    async def fake_create_pipeline_job(job_data, background_tasks, session):
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
    return TestClient(app)


def test_ont_ngs_submit_route_normalizes_alias_and_delegates_to_jobs(monkeypatch) -> None:
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

    assert response.status_code == 201
    assert response.json()["id"] == "job-ont-1"
    job_data = captured["job_data"]
    assert job_data.name == "plasmid A12"
    assert job_data.model_id == "nanopore"
    assert job_data.mode == "plasmid_qc"
    assert job_data.pinned_gpu == 0
    assert job_data.params["ont_workflow_id"] == "ont_plasmid_qc"
    assert job_data.params["run_fastq_qc"] is True
    assert job_data.params["run_modkit"] is False
    assert job_data.params["modified_bases"] == "none"
    assert job_data.params["fastq_minimap2_preset"] == "map-ont"


def test_ont_run_plasmid_handoff_submit_builds_and_submits_job(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    client = _client_with_fake_create(monkeypatch, captured)

    monkeypatch.setattr(
        ont_runs.ont_run_control,
        "build_plasmid_qc_handoff",
        lambda run_id, payload: {
            "model_id": "nanopore",
            "mode": "plasmid_qc",
            "params": {
                "ont_workflow_id": "ont_plasmid_qc",
                "fastq_path": "/data/run/A12.fastq.gz",
                "reference_fasta": payload["reference_fasta"],
                "source_instrument_run_id": run_id,
                "source_minknow_run_id": "MNK-001",
            },
            "fake_or_demo_devices": False,
        },
    )

    response = client.post(
        "/api/ont/runs/ont-run-1/handoff/plasmid-qc/submit",
        json={
            "name": "live run plasmid QC",
            "reference_fasta": "/data/refs/A12.fa",
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
