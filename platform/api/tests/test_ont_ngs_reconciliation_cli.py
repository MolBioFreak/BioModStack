from __future__ import annotations

import argparse
import asyncio
import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/reconcile_ont_fastq_qc_job.py"


def _module():
    spec = importlib.util.spec_from_file_location("reconcile_ont_fastq_qc_job", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _environment(code_root: Path, data_root: Path, results_root: Path) -> str:
    return " ".join([
        f"BMS_RUNTIME_MODE=dev",
        f"BMS_HOME={code_root}",
        f"BMS_DATA={data_root}",
        f"BMS_RESULTS_DIR={results_root}",
        f"BMS_RESULTS_ROOT={results_root}",
    ])


def test_managed_development_environment_derives_database_without_caller_path(tmp_path: Path) -> None:
    module = _module()
    code_root = tmp_path / "dev-test-canonical"
    data_root = tmp_path / ".biomodstack-dev"
    results_root = data_root / "bms_results"
    for path in (code_root, data_root, results_root):
        path.mkdir(parents=True, exist_ok=True)
    (data_root / "biomodstack.db").touch()

    lane = module.parse_managed_development_environment(
        _environment(code_root, data_root, results_root),
        script_repo_root=code_root,
    )

    assert lane.code_root == code_root.resolve()
    assert lane.database_path == (data_root / "biomodstack.db").resolve()
    assert lane.results_root == results_root.resolve()


def test_managed_development_environment_rejects_wrong_lane_or_source_root(tmp_path: Path) -> None:
    module = _module()
    code_root = tmp_path / "dev-test-canonical"
    other_root = tmp_path / "feature-worktree"
    data_root = tmp_path / ".biomodstack-dev"
    results_root = data_root / "bms_results"
    for path in (code_root, other_root, data_root, results_root):
        path.mkdir(parents=True, exist_ok=True)
    (data_root / "biomodstack.db").touch()

    with pytest.raises(module.ReconciliationCliError, match="canonical Development source"):
        module.parse_managed_development_environment(
            _environment(code_root, data_root, results_root),
            script_repo_root=other_root,
        )
    with pytest.raises(module.ReconciliationCliError, match="Development runtime"):
        module.parse_managed_development_environment(
            _environment(code_root, data_root, results_root).replace("BMS_RUNTIME_MODE=dev", "BMS_RUNTIME_MODE=prod"),
            script_repo_root=code_root,
        )


def test_cli_has_only_job_and_mode_mutation_selectors() -> None:
    module = _module()
    parser: argparse.ArgumentParser = module.build_parser()

    parsed = parser.parse_args(["--job-id", "31f02bd5-830f-4558-aa78-3873c515de68", "--dry-run"])
    assert parsed.dry_run is True
    assert parsed.apply is False
    with pytest.raises(SystemExit):
        parser.parse_args(["--job-id", "31f02bd5-830f-4558-aa78-3873c515de68", "--database", "/tmp/foreign.db", "--dry-run"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--job-id", "31f02bd5-830f-4558-aa78-3873c515de68", "--dry-run", "--apply"])


def test_runtime_environment_overrides_inherited_database_authority(tmp_path: Path) -> None:
    module = _module()
    repo = tmp_path / "repo"
    data = tmp_path / "dev-data"
    results = data / "bms_results"
    repo.mkdir()
    results.mkdir(parents=True)
    (data / "biomodstack.db").touch()
    lane = module.parse_managed_development_environment(
        _environment(repo, data, results),
        script_repo_root=repo,
    )

    runtime = module.managed_runtime_environment(lane)

    assert runtime["BMS_DATA"] == str(data.resolve())
    assert runtime["BMS_DB_PATH"] == str((data / "biomodstack.db").resolve())
    assert runtime["DATABASE_URL"] == f"sqlite+aiosqlite:///{(data / 'biomodstack.db').resolve()}"
    assert runtime["BMS_RESULTS_DIR"] == str(results.resolve())


def test_source_identity_requires_clean_commit_and_tree(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = _module()

    def fake_run(command, **_kwargs):
        if command[-2:] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n", stderr="")
        if command[-2:] == ["rev-parse", "HEAD^{tree}"]:
            return subprocess.CompletedProcess(command, 0, stdout="b" * 40 + "\n", stderr="")
        if command[-2:] == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module._source_identity(tmp_path) == ("a" * 40, "b" * 40)


def test_database_identity_is_path_opaque_and_inode_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _module()
    code = tmp_path / "code"
    data = tmp_path / "data"
    results = data / "bms_results"
    code.mkdir()
    results.mkdir(parents=True)
    database = data / "biomodstack.db"
    database.touch()
    lane = module.ManagedDevelopmentLane(code, data, database, results, {})

    identity = module._database_identity_sha256(lane)

    assert len(identity) == 64
    assert str(database) not in identity
    observed = database.stat()
    monkeypatch.setattr(
        module,
        "_lstat",
        lambda _path: SimpleNamespace(st_dev=observed.st_dev, st_ino=observed.st_ino + 1),
    )
    assert module._database_identity_sha256(lane) != identity


@pytest.mark.parametrize("state", ["active", "activating", "reloading", "deactivating"])
def test_owned_or_transitional_workflow_unit_refuses_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    module = _module()
    captured: dict[str, object] = {}
    result = subprocess.CompletedProcess(
        ["systemctl"],
        0,
        stdout=f"biomodstack-development-job-attempt-1.service loaded {state} running\n",
        stderr="",
    )

    def fake_run(argv, **_kwargs):
        captured["argv"] = argv
        return result

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(module.ReconciliationCliError, match="workflow owner"):
        module._assert_no_active_workflow_units()
    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "--state=active,activating,reloading,deactivating" in argv


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(2, 2), (3, 3), (4, 4), (99, 4), (None, 4)],
)
def test_exception_exit_code_is_closed(exit_code: int | None, expected: int) -> None:
    module = _module()
    error = RuntimeError("failure")
    if exit_code is not None:
        setattr(error, "exit_code", exit_code)
    assert module._exception_exit_code(error) == expected


def test_precommit_hierarchy_resolution_uses_new_factory_sessions() -> None:
    module = _module()
    domain_session = object()
    hierarchy_session = object()
    calls: list[tuple[object, object]] = []

    class Context:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_args):
            return False

    async def resolver(_job, observed_domain, observed_hierarchy, **kwargs):
        calls.append((observed_domain, observed_hierarchy))
        assert kwargs == {
            "source_fastq_sha256": "a" * 64,
            "artifact_set_sha256": "b" * 64,
            "sequence_qc_manifest_sha256": "c" * 64,
            "verification_manifest_sha256": "d" * 64,
            "reference_sequence_sha256": "e" * 64,
        }
        return "fresh-authority"

    evidence = SimpleNamespace(
        source_fastq_sha256="a" * 64,
        artifact_set_sha256="b" * 64,
        sequence_qc_manifest_sha256="c" * 64,
        verification_manifest_sha256="d" * 64,
        reference_sequence_sha256="e" * 64,
    )
    result = asyncio.run(module._resolve_fresh_hierarchy_authority(
        object(),
        evidence,
        domain_session_factory=lambda: Context(domain_session),
        hierarchy_session_factory=lambda: Context(hierarchy_session),
        resolver=resolver,
    ))

    assert result == "fresh-authority"
    assert calls == [(domain_session, hierarchy_session)]
