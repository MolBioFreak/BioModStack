from __future__ import annotations

from pathlib import Path


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
