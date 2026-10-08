from __future__ import annotations

import importlib.util
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts" / "biomodstack_dev_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location("biomodstack_dev_sync", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _content_sha256(document: dict[str, object]) -> str:
    value = dict(document)
    value.pop("content_sha256", None)
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def test_sync_units_make_sixty_second_policy_obvious() -> None:
    sync = load_module()

    units = sync.render_sync_units(REPO_ROOT)

    timer = units["biomodstack-dev-sync.timer"]
    service = units["biomodstack-dev-sync.service"]
    assert "Description=BioModStack Development origin/test sync every 60 seconds" in timer
    assert "OnActiveSec=60s" in timer
    assert "OnUnitInactiveSec=60s" in timer
    assert "Persistent=true" in timer
    assert f"ExecStart=/usr/bin/env python3 {MODULE_PATH} --once" in service


def test_install_sync_units_uses_a_stable_libexec_copy(tmp_path: Path, monkeypatch) -> None:
    sync = load_module()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(sync, "_run", lambda _cwd, *command, **kwargs: calls.append(command))

    systemd_dir = tmp_path / "systemd"
    libexec_dir = tmp_path / "libexec"
    sync.install_sync_units(
        REPO_ROOT,
        systemd_dir,
        state_dir=tmp_path / "state",
        libexec_dir=libexec_dir,
    )

    installed_script = libexec_dir / "biomodstack_dev_sync.py"
    assert installed_script.read_bytes() == MODULE_PATH.read_bytes()
    service = (systemd_dir / "biomodstack-dev-sync.service").read_text(encoding="utf-8")
    assert f"ExecStart=/usr/bin/env python3 {installed_script} --once" in service
    assert ("systemctl", "--user", "enable", "--now", "biomodstack-dev-sync.timer") in calls


def test_plan_sync_fast_forwards_when_remote_is_newer() -> None:
    sync = load_module()

    decision = sync.plan_sync(
        dirty=False,
        local_revision="a" * 40,
        remote_revision="b" * 40,
        deployed_revision="a" * 40,
        remote_descends_from_local=True,
        active_work=False,
    )

    assert decision == "fast-forward-deploy"


def test_plan_sync_redeploys_current_remote_when_live_identity_is_stale() -> None:
    sync = load_module()

    decision = sync.plan_sync(
        dirty=False,
        local_revision="b" * 40,
        remote_revision="b" * 40,
        deployed_revision="a" * 40,
        remote_descends_from_local=True,
        active_work=False,
    )

    assert decision == "deploy-current"


def test_plan_sync_does_not_restart_current_services_when_health_is_temporarily_unavailable() -> None:
    sync = load_module()

    decision = sync.plan_sync(
        dirty=False,
        local_revision="b" * 40,
        remote_revision="b" * 40,
        deployed_revision=None,
        remote_descends_from_local=True,
        active_work=False,
    )

    assert decision == "blocked-health-unavailable"


def test_plan_sync_blocks_dirty_or_diverged_canonical_tree() -> None:
    sync = load_module()

    assert sync.plan_sync(
        dirty=True,
        local_revision="a" * 40,
        remote_revision="b" * 40,
        deployed_revision="a" * 40,
        remote_descends_from_local=True,
        active_work=False,
    ) == "blocked-dirty"
    assert sync.plan_sync(
        dirty=False,
        local_revision="a" * 40,
        remote_revision="b" * 40,
        deployed_revision="a" * 40,
        remote_descends_from_local=False,
        active_work=False,
    ) == "blocked-diverged"


def test_plan_sync_is_idle_only_when_remote_and_live_identity_match() -> None:
    sync = load_module()

    assert sync.plan_sync(
        dirty=False,
        local_revision="b" * 40,
        remote_revision="b" * 40,
        deployed_revision="b" * 40,
        remote_descends_from_local=True,
        active_work=False,
    ) == "idle"


def test_plan_sync_defers_source_and_restart_while_work_is_active() -> None:
    sync = load_module()

    for local, remote, deployed in (
        ("a" * 40, "b" * 40, "a" * 40),
        ("b" * 40, "b" * 40, "a" * 40),
    ):
        assert sync.plan_sync(
            dirty=False,
            local_revision=local,
            remote_revision=remote,
            deployed_revision=deployed,
            remote_descends_from_local=True,
            active_work=True,
        ) == "deferred-active-work"


def test_active_development_work_reads_jobs_fail_closed(tmp_path: Path, monkeypatch) -> None:
    sync = load_module()
    database = tmp_path / "biomodstack.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE jobs (status TEXT, queue_status TEXT, awaiting_input INTEGER, nextflow_run_id TEXT, completed_at TEXT)"
    )
    connection.execute("INSERT INTO jobs VALUES ('running', 'running', 0, 'run-1', NULL)")
    connection.execute("INSERT INTO jobs VALUES ('cancelled', 'failed', 0, 'stale-run', NULL)")
    connection.execute("INSERT INTO jobs VALUES ('awaiting_input', 'completed', 1, 'waiting-run', NULL)")
    connection.commit()
    connection.close()
    monkeypatch.setattr(sync, "_development_database", lambda _root: database)

    assert sync._active_development_work(tmp_path) == (True, 1)


def test_deployment_fence_blocks_api_mutation_admission(tmp_path: Path, monkeypatch) -> None:
    sync = load_module()
    import runtime_policy

    lock_path = tmp_path / "deployment-admission.lock"
    monkeypatch.setenv("BMS_DEPLOYMENT_ADMISSION_LOCK", str(lock_path))
    with sync._deployment_fence(tmp_path):
        with pytest.raises(runtime_policy.WorkflowAdmissionBlocked):
            with runtime_policy.workflow_mutation_admission():
                pass

    with runtime_policy.workflow_mutation_admission():
        pass


def _authority_blobs(*, runtime_bytes: bytes, include_uncovered_pin: bool = False) -> dict[str, bytes]:
    source_bytes = b"current source\n"
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    denominator = {
        "paths": ["schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json", "source.py"],
        "schema": "bms.ngs-molbio.runtime-source-denominator.v2",
    }
    denominator["content_sha256"] = _content_sha256(denominator)
    denominator_bytes = json.dumps(denominator).encode()
    runtime = json.loads(runtime_bytes)
    runtime["source_denominator"]["content_sha256"] = denominator["content_sha256"]
    runtime["source_authorities"].append(
        {
            "path": "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json",
            "sha256": hashlib.sha256(denominator_bytes).hexdigest(),
            "size_bytes": len(denominator_bytes),
        }
    )
    runtime["source_authorities"] = sorted(runtime["source_authorities"], key=lambda row: row["path"])
    runtime["content_sha256"] = _content_sha256(runtime)
    source_pin_authorities = [
        {"path": "source.py", "sha256": "0" * 64},
    ]
    source_pin = {
        "authorities": source_pin_authorities,
        "baseline_commit": "d" * 40,
        "baseline_tree": "e" * 40,
        "schema": "bms.ngs-molbio.source-pin.v1",
    }
    source_pin["content_sha256"] = _content_sha256(source_pin)
    blobs = {
        "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json": denominator_bytes,
        "platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json": json.dumps(runtime).encode(),
        "platform/api/config/ngs_molbio/source_pin_v1.json": json.dumps(source_pin).encode(),
        "source.py": source_bytes,
    }
    if include_uncovered_pin:
        blobs["uncovered.py"] = b"changed uncovered source\n"
        source_pin_authorities.append({"path": "uncovered.py", "sha256": "1" * 64})
        source_pin["content_sha256"] = _content_sha256(source_pin)
        blobs["platform/api/config/ngs_molbio/source_pin_v1.json"] = json.dumps(source_pin).encode()
    return blobs


def _runtime_record(*, source_sha: str) -> bytes:
    denominator = {
        "paths": ["source.py"],
        "schema": "bms.ngs-molbio.runtime-source-denominator.v2",
    }
    denominator_digest = _content_sha256(denominator)
    document: dict[str, object] = {
        "adapter_runtime_count": 27,
        "baseline_source_commit": "d" * 40,
        "baseline_source_tree": "e" * 40,
        "binding_runtime_state": "implemented_unverified",
        "capability_exposure_state": "fail_closed",
        "connector_event_runtime_count": 12,
        "dataset_exposure_state": "fail_closed",
        "implementation_state": "implemented_unverified",
        "n0_package_fingerprint": "a" * 64,
        "n0_receipt_content_sha256": "b" * 64,
        "payload_scanner_runtime_state": "implemented_unverified",
        "phases": [
            {
                "acceptance_state": "unverified",
                "evidence": f"phase {number}",
                "phase_id": f"N{number}",
                "source_state": "implemented",
            }
            for number in range(1, 7)
        ],
        "release_acceptance_state": "open",
        "schema": "bms.ngs-molbio.runtime-implementation.v1",
        "source_authorities": [
            {"path": "source.py", "sha256": source_sha, "size_bytes": len(b"current source\n")}
        ],
        "source_denominator": {
            "content_sha256": denominator_digest,
            "path": "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json",
        },
        "successor_source_commit": "c" * 40,
        "successor_source_tree": "3" * 40,
        "tests_run": 0,
        "verification_state": "source_audit_only",
    }
    document["content_sha256"] = _content_sha256(document)
    return json.dumps(document).encode()


def test_candidate_runtime_authority_accepts_exact_final_tree(monkeypatch) -> None:
    sync = load_module()
    source_sha = hashlib.sha256(b"current source\n").hexdigest()
    blobs = _authority_blobs(runtime_bytes=_runtime_record(source_sha=source_sha))
    monkeypatch.setattr(sync, "_git_blob", lambda _root, _revision, path: blobs[path])
    monkeypatch.setattr(sync, "_candidate_tree_without_record", lambda _root, _revision: "3" * 40)

    result = sync.validate_candidate_runtime_authority(Path("/repo"), "a" * 40)

    assert result == {
        "candidate_revision": "a" * 40,
        "runtime_source_count": 2,
        "source_pin_overlay_count": 1,
    }


def test_candidate_tree_forces_index_only_runtime_record_removal(monkeypatch, tmp_path: Path) -> None:
    sync = load_module()
    calls: list[tuple[str, ...]] = []

    def fake_run(command, **_kwargs):
        calls.append(tuple(command))
        stdout = "f" * 40 + "\n" if tuple(command) == ("git", "write-tree") else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(sync.subprocess, "run", fake_run)

    result = sync._candidate_tree_without_record(tmp_path, "a" * 40)

    assert result == "f" * 40
    assert (
        "git",
        "rm",
        "--cached",
        "--quiet",
        "-f",
        "--",
        sync.RUNTIME_IMPLEMENTATION_PATH,
    ) in calls


def test_candidate_runtime_authority_rejects_stale_runtime_digest(monkeypatch) -> None:
    sync = load_module()
    blobs = _authority_blobs(runtime_bytes=_runtime_record(source_sha="2" * 64))
    monkeypatch.setattr(sync, "_git_blob", lambda _root, _revision, path: blobs[path])
    monkeypatch.setattr(sync, "_candidate_tree_without_record", lambda _root, _revision: "3" * 40)

    with pytest.raises(RuntimeError, match="runtime digest mismatch: source.py"):
        sync.validate_candidate_runtime_authority(Path("/repo"), "b" * 40)


def test_candidate_runtime_authority_rejects_uncovered_source_pin_drift(monkeypatch) -> None:
    sync = load_module()
    source_sha = hashlib.sha256(b"current source\n").hexdigest()
    blobs = _authority_blobs(
        runtime_bytes=_runtime_record(source_sha=source_sha),
        include_uncovered_pin=True,
    )
    monkeypatch.setattr(sync, "_git_blob", lambda _root, _revision, path: blobs[path])
    monkeypatch.setattr(sync, "_candidate_tree_without_record", lambda _root, _revision: "3" * 40)

    with pytest.raises(RuntimeError, match="source pin drift lacks runtime coverage: uncovered.py"):
        sync.validate_candidate_runtime_authority(Path("/repo"), "c" * 40)


def test_candidate_runtime_authority_rejects_duplicate_json_keys(monkeypatch) -> None:
    sync = load_module()
    source_sha = hashlib.sha256(b"current source\n").hexdigest()
    blobs = _authority_blobs(runtime_bytes=_runtime_record(source_sha=source_sha))
    blobs[sync.RUNTIME_DENOMINATOR_PATH] = b'{"schema":"x","schema":"y","paths":[],"content_sha256":"z"}'
    monkeypatch.setattr(sync, "_git_blob", lambda _root, _revision, path: blobs[path])

    with pytest.raises(RuntimeError, match="duplicate candidate authority key: schema"):
        sync.validate_candidate_runtime_authority(Path("/repo"), "d" * 40)


@pytest.mark.parametrize("target", ["denominator", "runtime", "source_pin"])
def test_candidate_runtime_authority_rejects_stale_canonical_digest(target: str, monkeypatch) -> None:
    sync = load_module()
    source_sha = hashlib.sha256(b"current source\n").hexdigest()
    blobs = _authority_blobs(runtime_bytes=_runtime_record(source_sha=source_sha))
    path = {
        "denominator": sync.RUNTIME_DENOMINATOR_PATH,
        "runtime": sync.RUNTIME_IMPLEMENTATION_PATH,
        "source_pin": sync.SOURCE_PIN_PATH,
    }[target]
    payload = json.loads(blobs[path])
    payload["content_sha256"] = "0" * 64
    blobs[path] = json.dumps(payload).encode()
    monkeypatch.setattr(sync, "_git_blob", lambda _root, _revision, name: blobs[name])

    with pytest.raises(RuntimeError, match=f"candidate {target.replace('_', '-')} authority .*digest"):
        sync.validate_candidate_runtime_authority(Path("/repo"), "e" * 40)


def test_candidate_runtime_authority_rejects_wrong_v2_schema(monkeypatch) -> None:
    sync = load_module()
    source_sha = hashlib.sha256(b"current source\n").hexdigest()
    blobs = _authority_blobs(runtime_bytes=_runtime_record(source_sha=source_sha))
    denominator = json.loads(blobs[sync.RUNTIME_DENOMINATOR_PATH])
    denominator["schema"] = "bms.ngs-molbio.runtime-source-denominator.v1"
    denominator["content_sha256"] = _content_sha256(denominator)
    blobs[sync.RUNTIME_DENOMINATOR_PATH] = json.dumps(denominator).encode()
    monkeypatch.setattr(sync, "_git_blob", lambda _root, _revision, path: blobs[path])

    with pytest.raises(RuntimeError, match="candidate denominator authority shape"):
        sync.validate_candidate_runtime_authority(Path("/repo"), "f" * 40)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("authority", "blocked-runtime-authority"),
        ("rolled_back", "blocked-deployment-rolled-back"),
        ("rollback_failed", "blocked-deployment-rollback-failed"),
    ],
)
def test_deployment_failure_receipt_states_are_truthful(error: str, expected: str) -> None:
    sync = load_module()
    exception = {
        "authority": sync.RuntimeAuthorityError("invalid authority"),
        "rolled_back": sync.DeploymentRolledBackError("rolled back"),
        "rollback_failed": sync.DeploymentRollbackFailedError("rollback failed"),
    }[error]

    assert sync._deployment_failure_state(exception) == expected


def test_deploy_transaction_validates_before_fast_forward(tmp_path: Path, monkeypatch) -> None:
    sync = load_module()
    calls: list[tuple[str, ...]] = []
    local = "a" * 40
    remote = "b" * 40

    monkeypatch.setattr(
        sync,
        "validate_candidate_runtime_authority",
        lambda _root, revision: calls.append(("validate", revision)) or {"candidate_revision": revision},
    )
    monkeypatch.setattr(sync, "_git", lambda _root, *args, **_kwargs: calls.append(tuple(args)) or "")
    monkeypatch.setattr(sync, "_run", lambda _root, *args, **_kwargs: calls.append(tuple(args)))
    monkeypatch.setattr(sync, "_deployed_revision", lambda _root: remote)
    installed = tmp_path / "libexec" / "biomodstack_dev_sync.py"
    monkeypatch.setattr(sync, "DEFAULT_INSTALLED_SYNC", installed)
    source = tmp_path / "scripts" / "biomodstack_dev_sync.py"
    source.parent.mkdir()
    source.write_bytes(b"new stable synchronizer\n")

    result = sync._deploy_candidate(tmp_path, "fast-forward-deploy", local, remote, local)

    assert result["runtime_authority"] == {"candidate_revision": remote}
    assert calls.index(("validate", remote)) < calls.index(("merge", "--ff-only", "refs/remotes/origin/test"))
    assert installed.read_bytes() == source.read_bytes()


def test_deploy_transaction_rolls_back_source_and_services_on_live_identity_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sync = load_module()
    calls: list[tuple[str, ...]] = []
    local = "a" * 40
    remote = "b" * 40
    observed = iter([None, local])

    monkeypatch.setattr(
        sync,
        "validate_candidate_runtime_authority",
        lambda _root, revision: {"candidate_revision": revision},
    )
    monkeypatch.setattr(sync, "_git", lambda _root, *args, **_kwargs: calls.append(tuple(args)) or "")
    monkeypatch.setattr(sync, "_run", lambda _root, *args, **_kwargs: calls.append(tuple(args)))
    monkeypatch.setattr(sync, "_deployed_revision", lambda _root: next(observed))

    with pytest.raises(RuntimeError, match="rolled back to"):
        sync._deploy_candidate(tmp_path, "fast-forward-deploy", local, remote, local)

    assert ("reset", "--hard", local) in calls
    restart = (str(sync.sys.executable), str(tmp_path / "scripts" / "manage_desktop_services.py"), "restart", "--runtime", "dev")
    assert calls.count(restart) == 2


def test_deploy_transaction_restores_stable_sync_when_refresh_fails_after_replace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sync = load_module()
    local = "a" * 40
    remote = "b" * 40
    installed = tmp_path / "libexec" / "biomodstack_dev_sync.py"
    installed.parent.mkdir()
    installed.write_bytes(b"old stable synchronizer\n")
    source = tmp_path / "scripts" / "biomodstack_dev_sync.py"
    source.parent.mkdir()
    source.write_bytes(b"new stable synchronizer\n")
    observed = iter([remote, local])

    monkeypatch.setattr(sync, "DEFAULT_INSTALLED_SYNC", installed)
    monkeypatch.setattr(sync, "validate_candidate_runtime_authority", lambda _root, revision: {"candidate_revision": revision})
    monkeypatch.setattr(sync, "_git", lambda _root, *args, **_kwargs: "")
    monkeypatch.setattr(sync, "_run", lambda _root, *args, **_kwargs: None)
    monkeypatch.setattr(sync, "_deployed_revision", lambda _root: next(observed))

    real_copy = sync._atomic_copy

    def fail_after_replace(source_path: Path, target_path: Path) -> None:
        real_copy(source_path, target_path)
        raise OSError("directory fsync failed")

    monkeypatch.setattr(sync, "_atomic_copy", fail_after_replace)

    with pytest.raises(sync.DeploymentRolledBackError):
        sync._deploy_candidate(tmp_path, "fast-forward-deploy", local, remote, local)

    assert installed.read_bytes() == b"old stable synchronizer\n"
