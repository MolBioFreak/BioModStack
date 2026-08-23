import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from routers import jobs
from routers.jobs import _infer_nanopore_stage_outputs, _resolve_nanopore_fastq_qc_mode
from schemas import JobStatus


def test_construct_screening_fastq_assembly_stage_is_inferred_only_when_requested(tmp_path: Path):
    report = tmp_path / "assembly" / "wf_clone_out" / "wf-clone-validation-report.html"
    report.parent.mkdir(parents=True)
    report.write_text("<html>report</html>", encoding="utf-8")

    base_params = {
        "fastq_path": "/inputs/reads.fastq",
        "reference_fasta": "/inputs/reference.fasta",
    }
    disabled = _infer_nanopore_stage_outputs(
        str(tmp_path),
        {**base_params, "run_assembly": False},
    )
    assert "wf_clone_validation" not in disabled

    enabled = _infer_nanopore_stage_outputs(
        str(tmp_path),
        {**base_params, "run_assembly": True},
    )
    assert "wf_clone_validation" in enabled
    assert any(path.endswith("assembly/wf_clone_out") for path in enabled["wf_clone_validation"])
    assert any(path.endswith("assembly/wf_clone_out/wf-clone-validation-report.html") for path in enabled["wf_clone_validation"])


def test_persisted_legacy_multimer_flag_remains_read_compatible():
    assert _resolve_nanopore_fastq_qc_mode({"run_multimer_qc": True}) == (True, True)
    assert _resolve_nanopore_fastq_qc_mode({"run_multimer_qc": False}) == (False, True)
    assert _resolve_nanopore_fastq_qc_mode(
        {"run_multimer_qc": True, "run_fastq_qc": False}
    ) == (False, False)


def test_construct_screening_public_stage_route_plans_and_infers_fastq_assembly(tmp_path: Path):
    report = tmp_path / "assembly" / "wf_clone_out" / "wf-clone-validation-report.html"
    report.parent.mkdir(parents=True)
    report.write_text("<html>report</html>", encoding="utf-8")
    job = Job(
        id="construct-stage-route",
        name="construct stage route",
        model_id="nanopore",
        mode="construct_screening",
        status=JobStatus.RUNNING.value,
        output_dir=str(tmp_path),
        params={
            "fastq_path": "/inputs/reads.fastq",
            "reference_fasta": "/inputs/reference.fasta",
            "run_fastq_qc": False,
            "run_assembly": True,
        },
        completed_stages=[],
        stage_outputs={},
    )

    class FakeResult:
        def scalar_one_or_none(self):
            return job

    class FakeSession:
        async def execute(self, _statement):
            return FakeResult()

    app = FastAPI()
    app.include_router(jobs.router, prefix="/api/jobs")
    app.dependency_overrides[jobs.get_session] = FakeSession

    response = TestClient(app).get(f"/api/jobs/{job.id}/stages")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["all_stages"] == ["fastq_align", "wf_clone_validation"]
    assert "wf_clone_validation" in payload["completed_stages"]
    assert any(
        path.endswith("assembly/wf_clone_out/wf-clone-validation-report.html")
        for path in payload["stage_outputs"]["wf_clone_validation"]
    )


@pytest.mark.asyncio
async def test_legacy_clone_stage_callback_persists_canonical_stage_id(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stage.db'}")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    token = "construct-stage-token-0123456789abcdef"
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with Session() as session:
            job = Job(
                id="construct-stage-persistence",
                name="construct stage persistence",
                model_id="nanopore",
                mode="construct_screening",
                params={"run_assembly": True},
                status=JobStatus.RUNNING.value,
                provenance={
                    "workflow_stage_report_token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest()
                },
            )
            session.add(job)
            await session.commit()
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": f"/api/jobs/{job.id}/stage-complete",
                    "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))],
                }
            )

            await jobs.report_stage_complete(
                job.id,
                request,
                "clone_validation",
                ["/results/assembly/wf_clone_out"],
                session,
            )
            await session.refresh(job)

            assert job.completed_stages == ["wf_clone_validation"]
            assert set(job.stage_outputs) == {"wf_clone_validation"}
            assert set(job.provenance["stage_terminal_states"]) == {"wf_clone_validation"}
    finally:
        await engine.dispose()
