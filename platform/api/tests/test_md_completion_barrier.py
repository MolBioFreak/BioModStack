from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import services.md.completion as completion_module
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_md_completion_service_is_the_named_terminal_authority() -> None:
    service = (REPO_ROOT / "platform/api/services/md/completion.py").read_text(encoding="utf-8")
    results = (REPO_ROOT / "platform/api/services/md/results.py").read_text(encoding="utf-8")
    assert "def validate_and_finalize_md_job" in service
    assert "apply_completion_barrier(job)" in service
    assert "md_run_v1.schema.json" in results
    assert "md_analysis_v1.schema.json" in results
    assert "replica_manifest_set_sha256" in results
    assert "MD_COMPLETION_CONFLICT" in results


@pytest.mark.asyncio
async def test_md_terminal_authority_closes_durable_run_state_in_the_callers_transaction(monkeypatch) -> None:
    job = SimpleNamespace(id="job-1")
    run = SimpleNamespace(phase="finalizing", verification_status="not_run", state_version=7, controls_blocked=True)

    class Session:
        async def get(self, model, identity):
            assert identity == "job-1"
            return run

    monkeypatch.setattr(completion_module, "apply_completion_barrier", lambda candidate: {"state": "completed"})

    async def no_artifacts(_job, _session) -> None:
        return None

    monkeypatch.setattr(completion_module, "_ingest_durable_artifacts", no_artifacts)

    result = await completion_module.validate_and_finalize_md_job(job, Session())

    assert result == {"state": "completed"}
    assert run.phase == "completed"
    assert run.verification_status == "verified"
    assert run.state_version == 8
    assert run.controls_blocked is False
