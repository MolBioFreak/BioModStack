from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from migrations.add_remote_execution import migrate
from services.remote_execution import bundle as bundle_module
from services.remote_execution import executor as executor_module
from services.remote_execution.bundle import (
    RemoteBundleError,
    current_source_identity,
    prepare_remote_bundle,
)
from services.remote_execution.contracts import (
    ExecutionTargetActivateRequest,
    RemoteFileRecord,
    RemoteResultManifest,
)
from services.remote_execution.transport import RemoteConnection, RemoteTransportError
from services.remote_execution.vast import _normalize
from services.nextflow import apply_msa_manifest_to_child_jobs
from tools import bms_remote_worker as worker


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _worker_attempt(tmp_path: Path, command: list[str]) -> Path:
    attempt_id = str(uuid4())
    attempt_dir = tmp_path / "attempt"
    source_dir = attempt_dir / "bundle" / "source"
    source_dir.mkdir(parents=True)
    source_archive = source_dir / ".bms-source.tar"
    source_archive.write_bytes(b"committed-source")
    output_dir = attempt_dir / "results"
    output_dir.mkdir()
    envelope = {
        "schema": "bms.remote-execution.v1",
        "job_id": "job-1",
        "root_job_id": "job-1",
        "parent_job_id": None,
        "attempt_id": attempt_id,
        "execution_target_id": "vast:123",
        "source_revision": "a" * 40,
        "source_tree": "b" * 40,
        "source_archive_sha256": _sha256(source_archive),
        "command": command,
        "working_directory": str(source_dir),
        "environment": {},
        "output_directory": str(output_dir),
        "expected_result_contract": {},
        "path_map": {},
        "files": [{
            "relative_path": "source/.bms-source.tar",
            "size_bytes": source_archive.stat().st_size,
            "sha256": _sha256(source_archive),
            "role": "source",
            "link_target": None,
        }],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (attempt_dir / worker.ENVELOPE_FILE).write_text(
        json.dumps(envelope, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return attempt_dir


def _wait_terminal(attempt_dir: Path, timeout: float = 10.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = worker.status(attempt_dir)
        if status["state"] in {"cancelled", "succeeded", "failed", "lost"}:
            return status
        time.sleep(0.05)
    raise AssertionError("remote worker did not reach a terminal state")


def test_execution_target_root_is_normalized_and_bounded() -> None:
    assert ExecutionTargetActivateRequest(provider_instance_id="123").remote_root == "/opt/biomodstack"
    for invalid in ("/", "relative", "/opt/../root", "/opt/root with space"):
        with pytest.raises(ValidationError):
            ExecutionTargetActivateRequest(provider_instance_id="123", remote_root=invalid)


def test_result_manifest_rejects_duplicate_artifact_paths() -> None:
    artifact = RemoteFileRecord(
        relative_path="result.txt",
        size_bytes=1,
        sha256="a" * 64,
        role="result",
    )
    with pytest.raises(ValidationError, match="duplicate"):
        RemoteResultManifest(
            attempt_id="attempt",
            job_id="job",
            exit_code=0,
            completed_at=datetime.now(timezone.utc),
            artifacts=[artifact, artifact],
            source_revision="b" * 40,
            source_tree="c" * 40,
            execution_envelope_sha256="d" * 64,
        )


def test_vast_inventory_normalization_drops_provider_payload() -> None:
    target = _normalize({
        "id": 123,
        "actual_status": "running",
        "ssh_host": "203.0.113.10",
        "ssh_port": 22,
        "num_gpus": 1,
        "gpu_name": "RTX 4090",
        "api_key": "must-not-survive",
    })
    assert target.provider_instance_id == "123"
    assert target.raw == {}


def test_transport_rejects_shell_unsafe_target_fields() -> None:
    valid = SimpleNamespace(id="vast:123", host="203.0.113.10", port=22, username="root", remote_root="/opt/biomodstack")
    assert RemoteConnection.from_target(valid).remote_root == "/opt/biomodstack"
    for field, value in (("username", "-oProxyCommand=x"), ("host", "host name"), ("remote_root", "/opt/root with space")):
        payload = vars(valid).copy()
        payload[field] = value
        with pytest.raises(RemoteTransportError):
            RemoteConnection.from_target(SimpleNamespace(**payload))


def test_source_identity_rejects_dirty_tracked_checkout(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", tmp_path], check=True)
    subprocess.run(["git", "-C", tmp_path, "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", tmp_path, "config", "user.name", "Test"], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", tmp_path, "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", tmp_path, "commit", "-qm", "fixture"], check=True)
    revision, tree = current_source_identity(tmp_path)
    assert len(revision) == 40
    assert len(tree) == 40
    tracked.write_text("two\n", encoding="utf-8")
    with pytest.raises(RemoteBundleError, match="clean tracked"):
        current_source_identity(tmp_path)


def test_bundle_preserves_committed_source_and_relocates_managed_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", repository], check=True)
    subprocess.run(["git", "-C", repository, "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", repository, "config", "user.name", "Test"], check=True)
    (repository / "main.nf").write_text("workflow { }\n", encoding="utf-8")
    subprocess.run(["git", "-C", repository, "add", "main.nf"], check=True)
    subprocess.run(["git", "-C", repository, "commit", "-qm", "fixture"], check=True)
    revision, tree = current_source_identity(repository)

    data_root = tmp_path / "data"
    container_root = data_root / "containers"
    weights_root = data_root / "weights"
    runtime_root = data_root / "runtime" / "cm-api-python" / "current"
    (runtime_root / "venv" / "bin").mkdir(parents=True)
    (runtime_root / "venv" / "bin" / "python").write_bytes(b"python-runtime")
    (runtime_root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    container_root.mkdir(parents=True)
    weights_root.mkdir(parents=True)
    job_input = data_root / "inputs" / "sequence.fasta"
    job_input.parent.mkdir(parents=True)
    job_input.write_text(">A\nAAAA\n", encoding="utf-8")
    output_dir = data_root / "results" / "job-1"

    monkeypatch.setattr(bundle_module, "get_code_root", lambda: repository)
    monkeypatch.setattr(bundle_module, "get_data_root", lambda: data_root)
    monkeypatch.setattr(bundle_module, "get_container_dir", lambda: container_root)
    monkeypatch.setattr(bundle_module, "get_weights_root", lambda: weights_root)
    monkeypatch.setenv("BMS_REMOTE_API_BASE_URL", "https://bms.example.invalid")
    job = SimpleNamespace(
        id="job-1",
        parent_job_id=None,
        lineage_root_job_id=None,
        execution_target_id="vast:123",
        execution_source_revision=revision,
        execution_source_tree=tree,
        child_output_dir=None,
        output_dir=str(output_dir),
        model_id="unit_remote",
        mode="predict",
        params={"input_path": str(job_input)},
        stage_family=None,
        stage_mode=None,
        selected_input_artifact_class=None,
        provenance={"remote_execution_assignment": {"gpu_indices": [0]}},
        assigned_gpu=0,
    )

    bundle = prepare_remote_bundle(
        job=job,
        target=SimpleNamespace(id="vast:123", remote_root="/opt/biomodstack"),
        command=["nextflow", "run", str(repository / "main.nf"), "--input", str(job_input), "--out", str(output_dir)],
        environment={"NXF_ANSI_LOG": "false"},
    )
    try:
        assert bundle.envelope.source_revision == revision
        assert bundle.envelope.source_tree == tree
        assert bundle.envelope.command[0] == "/opt/biomodstack/runner/nextflow"
        assert "/opt/biomodstack/revisions/" in bundle.envelope.command[2]
        assert "/opt/biomodstack/attempts/" in bundle.envelope.command[4]
        assert bundle.remote_output_alias == str(output_dir)
        assert bundle.envelope.environment["API_BASE_URL"] == "https://bms.example.invalid"
        assert all("token" not in key.lower() for key in bundle.envelope.environment)
        envelope_path = bundle.local_attempt_dir / "execution-envelope.json"
        assert bundle.envelope_sha256 == hashlib.sha256(envelope_path.read_bytes()).hexdigest()
    finally:
        import shutil

        shutil.rmtree(bundle.local_attempt_dir, ignore_errors=True)


def test_remote_worker_detaches_and_publishes_hash_bound_manifest(tmp_path: Path) -> None:
    output_file = tmp_path / "attempt" / "results" / "result.txt"
    attempt_dir = _worker_attempt(
        tmp_path,
        [sys.executable, "-c", f"from pathlib import Path; Path({str(output_file)!r}).write_text('ok')"],
    )
    launched = worker.start(attempt_dir)
    assert launched["state"] in {"running", "succeeded"}
    terminal = _wait_terminal(attempt_dir)
    assert terminal["state"] == "succeeded"
    assert terminal["exit_code"] == 0
    manifest_path = attempt_dir / "results" / worker.RESULT_MANIFEST_FILE
    assert terminal["result_manifest_sha256"] == _sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {artifact["relative_path"] for artifact in manifest["artifacts"]} >= {"result.txt"}
    assert worker.start(attempt_dir)["state"] == "succeeded"


def test_remote_worker_cancellation_is_durable_and_terminal(tmp_path: Path) -> None:
    attempt_dir = _worker_attempt(
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    launched = worker.start(attempt_dir)
    assert launched["state"] == "running"
    cancelled = worker.cancel(attempt_dir, timeout_seconds=2.0)
    if cancelled["state"] != "cancelled":
        cancelled = _wait_terminal(attempt_dir)
    assert cancelled["state"] == "cancelled"
    assert (attempt_dir / worker.CANCEL_REQUEST_FILE).is_file()


def test_remote_execution_migration_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "bridge.db"
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
    migrate(database)
    migrate(database)
    with sqlite3.connect(database) as connection:
        target_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_targets'"
        ).fetchone()
        columns = {row[1] for row in connection.execute("PRAGMA table_info('jobs')")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list('jobs')")}
        target_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('execution_targets')")
        }
        target_indexes = {
            row[1] for row in connection.execute("PRAGMA index_list('execution_targets')")
        }
    assert target_table == ("execution_targets",)
    assert {
        "execution_target_id", "execution_source_revision", "execution_source_tree",
        "execution_bundle_sha256", "remote_attempt_id", "remote_state",
    } <= columns
    assert {"ix_jobs_execution_target_id", "ix_jobs_remote_attempt_id"} <= indexes
    assert {"leased_job_id", "lease_acquired_at"} <= target_columns
    assert "ix_execution_targets_leased_job_id" in target_indexes


def test_verified_remote_generation_replaces_stale_output_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    output = data_root / "results" / "job-1"
    output.mkdir(parents=True)
    (output / "stale.txt").write_text("stale", encoding="utf-8")
    incoming = data_root / "remote-execution" / "incoming" / "attempt-1"
    incoming.mkdir(parents=True)
    (incoming / "result-manifest.json").write_text("{}", encoding="utf-8")
    (incoming / "fresh.txt").write_text("fresh", encoding="utf-8")
    monkeypatch.setattr(executor_module, "get_data_root", lambda: data_root)
    job = SimpleNamespace(
        id="job-1",
        remote_attempt_id="attempt-1",
        child_output_dir=None,
        output_dir=str(output),
    )

    published, previous = executor_module._publish_result_generation(job, incoming)

    assert published == output
    assert (output / "fresh.txt").read_text(encoding="utf-8") == "fresh"
    assert not (output / "result-manifest.json").exists()
    assert not (output / "stale.txt").exists()
    assert previous is not None
    assert (previous / "stale.txt").read_text(encoding="utf-8") == "stale"


class _FakeScalarResult:
    def __init__(self, rows: list[object]):
        self._rows = rows

    def scalars(self) -> "_FakeScalarResult":
        return self

    def all(self) -> list[object]:
        return self._rows


class _FakeAsyncSession:
    def __init__(self, rows: list[object]):
        self._rows = rows

    async def execute(self, _statement: object) -> _FakeScalarResult:
        return _FakeScalarResult(self._rows)


@pytest.mark.asyncio
async def test_msa_manifest_unlock_is_complete_before_child_mutation(tmp_path: Path) -> None:
    sequence = "ACDEFG"
    sequence_hash = hashlib.sha256(sequence.encode()).hexdigest()
    msa_file = tmp_path / "msas" / f"{sequence_hash}.a3m"
    msa_file.parent.mkdir()
    msa_file.write_text(">query\nACDEFG\n", encoding="utf-8")
    manifest = tmp_path / "msa_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "sequences": [
                    {
                        "success": True,
                        "sequence_hash": sequence_hash,
                        "msa_path": str(msa_file),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    child = SimpleNamespace(
        id="child-1",
        params={"sequence": sequence},
        queue_status="pending_msa",
    )

    count = await apply_msa_manifest_to_child_jobs(
        _FakeAsyncSession([child]),
        "msa-parent",
        str(manifest),
    )

    assert count == 1
    assert child.queue_status == "queued"
    assert child.params["msa_path"] == str(msa_file.resolve())


@pytest.mark.asyncio
async def test_msa_manifest_missing_child_artifact_keeps_child_blocked(tmp_path: Path) -> None:
    manifest = tmp_path / "msa_manifest.json"
    manifest.write_text(json.dumps({"sequences": []}), encoding="utf-8")
    child = SimpleNamespace(
        id="child-1",
        params={"sequence": "ACDEFG"},
        queue_status="pending_msa",
    )

    with pytest.raises(RuntimeError, match="no successful artifact"):
        await apply_msa_manifest_to_child_jobs(
            _FakeAsyncSession([child]),
            "msa-parent",
            str(manifest),
        )

    assert child.queue_status == "pending_msa"
    assert "msa_path" not in child.params
