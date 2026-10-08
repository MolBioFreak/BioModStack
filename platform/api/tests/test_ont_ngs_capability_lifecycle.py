from __future__ import annotations

import hashlib
from http.cookies import SimpleCookie
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from routers import jobs
from schemas import JobStatus
from services import alignment_access


def _request(path: str, token: str | None = None) -> Request:
    headers = [(b"x-forwarded-proto", b"https")]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "headers": headers,
        }
    )


def _assert_job_scoped_capability(response: Response, job: Job) -> None:
    header = response.headers.get("set-cookie")
    assert header is not None
    cookie = SimpleCookie()
    cookie.load(header)
    name = alignment_access.cookie_name(job.id)
    token = cookie[name].value
    provenance = job.provenance
    assert isinstance(provenance, dict)
    assert hashlib.sha256(token.encode("utf-8")).hexdigest() == provenance[alignment_access.PROVENANCE_DIGEST_KEY]
    assert provenance[alignment_access.PROVENANCE_SCHEME_KEY] == alignment_access.SCHEME
    assert cookie[name]["path"] == f"/api/jobs/{job.id}"
    assert cookie[name]["httponly"] is True
    assert cookie[name]["secure"] is True
    assert cookie[name]["samesite"].lower() == "strict"


@pytest.mark.asyncio
async def test_nanopore_resubmit_and_resume_each_issue_fresh_job_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jobs, "get_results_dir", lambda: tmp_path)
    original_output = tmp_path / "original"
    (original_output / "work").mkdir(parents=True)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            source_token = "source-job-capability"
            original = Job(
                id="original-job",
                name="ont-capability",
                model_id="nanopore",
                mode="fastq_qc",
                params={"fastq_path": "/tmp/reads.fastq"},
                status=JobStatus.FAILED.value,
                output_dir=str(original_output),
                completed_stages=[],
                stage_outputs={},
                provenance={
                    alignment_access.PROVENANCE_DIGEST_KEY: alignment_access.token_sha256(source_token),
                    alignment_access.PROVENANCE_SCHEME_KEY: alignment_access.SCHEME,
                },
            )
            session.add(original)
            await session.commit()

            with pytest.raises(HTTPException) as denied_resubmit:
                await jobs.resubmit_job(
                    original.id,
                    request=_request(f"/api/jobs/{original.id}/resubmit", "wrong-job-capability"),
                    response=Response(),
                    session=session,
                )
            assert denied_resubmit.value.status_code == 403

            with pytest.raises(HTTPException) as denied_resume:
                await jobs.resume_job(
                    original.id,
                    request_context=_request(f"/api/jobs/{original.id}/resume", "wrong-job-capability"),
                    response=Response(),
                    request=None,
                    background_tasks=BackgroundTasks(),
                    session=session,
                )
            assert denied_resume.value.status_code == 403

            for reserved_key in (
                "code_root",
                "wf_clone_source",
                "wf_clone_revision",
                "out_dir",
                "rfd_models",
                "af2_models",
                "boltz_models",
                "alphafold_params",
                "job_id",
                "fastq_path",
                "reference_fasta",
                "future_runtime_selector",
            ):
                with pytest.raises(HTTPException) as denied_override:
                    await jobs.resume_job(
                        original.id,
                        request_context=_request(f"/api/jobs/{original.id}/resume", source_token),
                        response=Response(),
                        request=jobs.ResumeJobRequest(param_overrides={reserved_key: "/caller/value"}),
                        background_tasks=BackgroundTasks(),
                        session=session,
                    )
                assert denied_override.value.status_code == 422
                assert reserved_key in str(denied_override.value.detail)

            resubmit_response = Response()
            resubmitted = await jobs.resubmit_job(
                original.id,
                request=_request(f"/api/jobs/{original.id}/resubmit", source_token),
                response=resubmit_response,
                session=session,
            )
            resubmitted_job = await session.get(Job, resubmitted["new_job_id"])
            assert resubmitted_job is not None
            _assert_job_scoped_capability(resubmit_response, resubmitted_job)

            resume_response = Response()
            resumed = await jobs.resume_job(
                original.id,
                request_context=_request(f"/api/jobs/{original.id}/resume", source_token),
                response=resume_response,
                request=None,
                background_tasks=BackgroundTasks(),
                session=session,
            )
            resumed_job = await session.get(Job, resumed["new_job_id"])
            assert resumed_job is not None
            _assert_job_scoped_capability(resume_response, resumed_job)

            assert resubmitted_job.id != resumed_job.id
            assert (
                resubmitted_job.provenance[alignment_access.PROVENANCE_DIGEST_KEY]
                != resumed_job.provenance[alignment_access.PROVENANCE_DIGEST_KEY]
            )
    finally:
        await engine.dispose()
