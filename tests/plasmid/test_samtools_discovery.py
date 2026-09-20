"""Exercise actual API fixture discovery without requiring a native tool."""
from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "platform/api/tests/test_construct_verification_phase2.py"


@pytest.fixture
def phase2(monkeypatch):
    monkeypatch.delenv("BMS_TEST_SAMTOOLS", raising=False)
    return runpy.run_path(str(MODULE))


def _executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_missing_tool_does_not_skip_import_or_pure_python_tests(monkeypatch, tmp_path):
    monkeypatch.delenv("BMS_TEST_SAMTOOLS", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    namespace = runpy.run_path(str(MODULE))
    namespace["test_phase2_contract_files_exist"]()
    assert namespace["_levenshtein_oracle"]("ACGT", "ACAT") == 1


@pytest.mark.parametrize("entrypoint", [
    "_run_case", "test_non_utf8_samtools_version_metadata_does_not_abort_verification",
])
def test_missing_tool_skips_before_artifact_or_process_creation(phase2, monkeypatch, tmp_path, entrypoint):
    monkeypatch.setenv("PATH", str(tmp_path))
    def unexpected_process(*args, **kwargs):
        pytest.fail("missing-tool preflight must not start a subprocess")
    monkeypatch.setattr(subprocess, "run", unexpected_process)
    with pytest.raises(pytest.skip.Exception, match="BMS_TEST_SAMTOOLS") as skipped:
        phase2[entrypoint](tmp_path)
    assert "tests were not run" in str(skipped.value)
    assert not list(tmp_path.iterdir())


def test_path_discovery_returns_absolute_executable(phase2, monkeypatch, tmp_path):
    executable = _executable(tmp_path / "bin" / "samtools")
    monkeypatch.setenv("PATH", str(executable.parent))
    assert phase2["_require_samtools"]() == executable


def test_explicit_override_wins_over_path_and_supports_spaces(phase2, monkeypatch, tmp_path):
    fallback = _executable(tmp_path / "bin" / "samtools")
    override = _executable(tmp_path / "external tools" / "samtools")
    monkeypatch.setenv("PATH", str(fallback.parent))
    monkeypatch.setenv("BMS_TEST_SAMTOOLS", str(override))
    assert phase2["_require_samtools"]() == override


def test_command_name_override_is_resolved_on_path(phase2, monkeypatch, tmp_path):
    executable = _executable(tmp_path / "samtools-for-ci")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("BMS_TEST_SAMTOOLS", executable.name)
    assert phase2["_require_samtools"]() == executable


def test_relative_override_is_anchored_for_execv(phase2, monkeypatch, tmp_path):
    executable = _executable(tmp_path / "bin" / "samtools")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BMS_TEST_SAMTOOLS", "bin/samtools")
    assert phase2["_require_samtools"]() == executable


@pytest.mark.parametrize("kind", ["empty", "blank", "missing", "non_executable", "directory"])
def test_invalid_explicit_override_fails_without_fallback(phase2, monkeypatch, tmp_path, kind):
    fallback = _executable(tmp_path / "bin" / "samtools")
    monkeypatch.setenv("PATH", str(fallback.parent))
    candidate = tmp_path / "override"
    if kind == "non_executable":
        candidate.write_text("not executable", encoding="utf-8")
        candidate.chmod(0o644)
    elif kind == "directory":
        candidate.mkdir()
    value = {"empty": "", "blank": "   "}.get(kind, str(candidate))
    monkeypatch.setenv("BMS_TEST_SAMTOOLS", value)
    with pytest.raises(pytest.fail.Exception, match="BMS_TEST_SAMTOOLS") as failed:
        phase2["_require_samtools"]()
    assert "no PATH fallback" in str(failed.value)


def test_discovery_uses_current_environment_not_import_time(phase2, monkeypatch, tmp_path):
    first = _executable(tmp_path / "one" / "samtools")
    second = _executable(tmp_path / "two" / "samtools")
    monkeypatch.setenv("BMS_TEST_SAMTOOLS", str(first))
    assert phase2["_require_samtools"]() == first
    monkeypatch.setenv("BMS_TEST_SAMTOOLS", str(second))
    assert phase2["_require_samtools"]() == second
