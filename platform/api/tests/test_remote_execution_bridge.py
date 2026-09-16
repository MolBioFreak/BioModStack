from __future__ import annotations

import hashlib
import json
import re
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
from services.remote_execution import transport as transport_module
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
from services.remote_execution.vast import _fetch_owned_instances, _normalize
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
            "mode": source_archive.stat().st_mode & 0o777,
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


def test_vast_inventory_prefers_current_direct_ssh_mapping() -> None:
    target = _normalize({
        "id": 123,
        "actual_status": "running",
        "ssh_host": "ssh3.vast.ai",
        "ssh_port": 14414,
        "public_ipaddr": "220.135.0.171",
        "direct_port_start": -1,
        "ports": {
            "22/tcp": [
                {"HostIp": "0.0.0.0", "HostPort": "24404"},
                {"HostIp": "::", "HostPort": "24404"},
            ],
        },
        "num_gpus": 1,
        "gpu_name": "RTX 4090",
        "api_key": "must-not-survive",
    })

    assert target.provider_instance_id == "123"
    assert target.host == "220.135.0.171"
    assert target.port == 24404
    assert target.raw == {}


def test_vast_inventory_falls_back_to_proxy_ssh_endpoint() -> None:
    target = _normalize({
        "id": 123,
        "actual_status": "running",
        "ssh_host": "ssh3.vast.ai",
        "ssh_port": 14415,
        "public_ipaddr": "220.135.0.171",
        "direct_port_start": -1,
        "ports": {
            "22/tcp": [
                {"HostIp": "0.0.0.0", "HostPort": "-1"},
                {"HostIp": "::", "HostPort": "70000"},
            ],
        },
    })

    assert target.host == "ssh3.vast.ai"
    assert target.port == 14415


def test_vast_inventory_uses_current_v1_instances_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Headers:
        @staticmethod
        def get_content_type() -> str:
            return "application/json"

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        @staticmethod
        def read(_limit: int) -> bytes:
            return b'{"instances":[],"success":true}'

    class Opener:
        @staticmethod
        def open(request, timeout: int):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setenv("VAST_API_KEY", "test-key")
    monkeypatch.delenv("BMS_VAST_API_BASE_URL", raising=False)
    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: Opener())

    inventory = _fetch_owned_instances()

    assert captured == {
        "url": "https://console.vast.ai/api/v1/instances/?owner=me",
        "timeout": 15,
    }
    assert inventory.available is True
    assert inventory.instances == []


def test_transport_rejects_shell_unsafe_target_fields() -> None:
    valid = SimpleNamespace(id="vast:123", host="203.0.113.10", port=22, username="root", remote_root="/opt/biomodstack")
    assert RemoteConnection.from_target(valid).remote_root == "/opt/biomodstack"
    for field, value in (("username", "-oProxyCommand=x"), ("host", "host name"), ("remote_root", "/opt/root with space")):
        payload = vars(valid).copy()
        payload[field] = value
        with pytest.raises(RemoteTransportError):
            RemoteConnection.from_target(SimpleNamespace(**payload))


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("missing:java\n", "Remote readiness prerequisite is missing: java"),
        (
            "occupied:/mnt/BioModStack/dev/apptainer\n",
            "Remote readiness path is occupied: /mnt/BioModStack/dev/apptainer",
        ),
    ],
)
@pytest.mark.asyncio
async def test_remote_failure_reports_readiness_marker_before_login_banner(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    expected: str,
) -> None:
    connection = RemoteConnection(
        target_id="vast:123",
        host="203.0.113.10",
        port=22,
        username="root",
        remote_root="/opt/biomodstack",
    )

    async def fake_run(*_args: object, **_kwargs: object) -> transport_module.CommandResult:
        return transport_module.CommandResult(
            returncode=20,
            stdout=stdout,
            stderr="untrusted login banner\n",
        )

    monkeypatch.setattr(transport_module, "_run", fake_run)
    monkeypatch.setattr(transport_module, "_ssh_base", lambda _connection: ["ssh"])

    with pytest.raises(RemoteTransportError, match=f"^{re.escape(expected)}$"):
        await transport_module.run_remote(connection, ["bash", "-lc", "probe"])


@pytest.mark.asyncio
async def test_provision_transport_wraps_command_stdin_and_upload_receiver(monkeypatch, tmp_path):
    import shlex
    from dataclasses import replace
    connection = RemoteConnection('test', 'unused', 22, 'user', str(tmp_path))
    bound = replace(connection, provision_operation_id='operation-1')
    calls = []
    async def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return transport_module.CommandResult(0, '', '')
    monkeypatch.setattr(transport_module, '_run', fake_run)
    monkeypatch.setattr(transport_module, '_ssh_base', lambda c: ['ssh', 'user@unused'])
    await transport_module.run_remote(bound, ['python3', '-', 'argument'], input_bytes=b'{"helper":true}')
    command = shlex.split(calls[-1][0][-1])
    assert command[0:2] == ['python3', '-c']
    assert command[3:] == ['run', str(tmp_path), 'operation-1', 'python3', '-', 'argument']
    assert calls[-1][1]['input_bytes'] == b'{"helper":true}'
    await transport_module.rsync_to_remote(bound, tmp_path, '/remote/incoming', delete=False)
    receiver = next(arg for arg in calls[-1][0] if arg.startswith('--rsync-path='))
    assert shlex.split(receiver.split('=', 1)[1])[3:] == ['run', str(tmp_path), 'operation-1', 'rsync']
    await transport_module.rsync_to_remote(connection, tmp_path, '/remote/incoming')
    assert not any(arg.startswith('--rsync-path=') for arg in calls[-1][0])


@pytest.mark.asyncio
@pytest.mark.parametrize('reply,expected', [
    ('{"schema":"bms.provision-transport.v1","operation_id":"op","quiescent":true}', True),
    ('{"schema":"bms.provision-transport.v1","operation_id":"other","quiescent":true}', False),
    ('{"schema":"bms.provision-transport.v1","operation_id":"op","quiescent":false}', False),
    ('not a receipt', False),
])
async def test_provision_quiescence_requires_exact_remote_receipt(monkeypatch, reply, expected):
    async def fake_run(*args, **kwargs):
        return transport_module.CommandResult(0, reply, '')
    monkeypatch.setattr(transport_module, '_run', fake_run)
    monkeypatch.setattr(transport_module, '_ssh_base', lambda c: ['ssh'])
    assert await transport_module.quiesce_provision(
        RemoteConnection('test', 'unused', 22, 'user', '/remote'), 'op') is expected


def _provision_command(root, operation, mode, *argv):
    connection = RemoteConnection('test', 'unused', 22, 'user', str(root))
    command = transport_module._provision_argv(connection, operation, mode, argv)
    command[0] = sys.executable
    return command


def test_provision_envelope_preserves_stdin_and_fences_late_command(tmp_path):
    command = _provision_command(tmp_path, 'stdin', 'run', sys.executable, '-c',
        'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())')
    result = subprocess.run(command, input=b'{"selection":"sanitized"}', capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == b'{"selection":"sanitized"}'
    receipt = subprocess.run(_provision_command(tmp_path, 'stdin', 'quiesce'), capture_output=True, timeout=10)
    assert json.loads(receipt.stdout)['quiescent'] is True
    denied = subprocess.run(command, input=b'late', capture_output=True, timeout=10)
    assert denied.returncode != 0
    assert denied.stdout == b''


def test_provision_cancel_stops_group_after_controller_loss(tmp_path):
    import os
    marker = tmp_path / 'mutation'
    mutation = 'import pathlib,time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); p=pathlib.Path(' + repr(str(marker)) + '); p.write_text("started"); time.sleep(30); p.write_text("late")'
    script = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(mutation) + ']); time.sleep(30)'
    process = subprocess.Popen(_provision_command(tmp_path, 'cancel', 'run', sys.executable, '-c', script),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists()
        from services.remote_execution._provision_transport import identity, live_group, same_process
        records = tmp_path / 'managed-assets/provision-operations/cancel'
        leader = json.loads(next(records.glob('*.json')).read_text())['leader']
        assert same_process(identity(leader['pid']), leader)
        assert live_group(leader['group'])
        process.kill()  # API/SSH supervisor loss is NOT a quiescence receipt.
        process.wait(timeout=5)
        receipt = subprocess.run(_provision_command(tmp_path, 'cancel', 'quiesce'), capture_output=True, timeout=10)
        assert json.loads(receipt.stdout)['quiescent'] is True
        rows = list((tmp_path / 'managed-assets/provision-operations/cancel').glob('*.json'))
        assert all(json.loads(p.read_text()).get('phase') == 'stopped' for p in rows if p.name != 'cancelled.json')
        assert marker.read_text() == 'started'
        # TERM-resistant descendant must be killed, not just a stopped receipt.
        assert not live_group(leader['group'])
        late = subprocess.run(_provision_command(tmp_path, 'cancel', 'run', sys.executable,
            '-c', 'raise SystemExit(0)'), capture_output=True, timeout=10)
        assert late.returncode != 0
    finally:
        # The same exact-identity owner performs cleanup, never a guessed PID.
        subprocess.run(_provision_command(tmp_path, 'cancel', 'quiesce'), capture_output=True, timeout=10)
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


@pytest.mark.asyncio
async def test_provision_bound_helper_upload_and_rsync_execute_offline(tmp_path, monkeypatch):
    import shlex
    import shutil
    from services.remote_execution import cache
    if not shutil.which('rsync'):
        pytest.skip('local rsync unavailable')
    root = tmp_path / 'worker'
    connection = RemoteConnection('test', 'unused', 22, 'user', str(root), provision_operation_id='uploads')
    # Local shell stands in for the SSH command channel, never contacts a host.
    monkeypatch.setattr(transport_module, '_ssh_base', lambda c: ['bash', '-c'])
    async def fence():
        pass
    installed = await cache._install_helper(connection, fence, 'bms_managed_runtime.py')
    assert Path(installed).read_bytes() == (Path(__file__).parents[1] / 'tools/bms_managed_runtime.py').read_bytes()
    fake_ssh = tmp_path / 'offline-ssh'
    fake_ssh.write_text('#!' + sys.executable + '\nimport os,sys\na=sys.argv[1:]\nif a[:1]==["-l"]: a=a[2:]\nos.execvp("sh", ["sh", "-c", " ".join(a[1:])])\n')
    fake_ssh.chmod(0o700)
    monkeypatch.setattr(transport_module, '_ssh_base', lambda c: [str(fake_ssh), 'unused'])
    source = tmp_path / 'bytes'
    source.write_bytes(b'fixture upload')
    destination = root / 'uploaded'
    await transport_module.rsync_to_remote(connection, source, str(destination), delete=False, timeout=10)
    assert destination.read_bytes() == source.read_bytes()
    monkeypatch.setattr(transport_module, '_ssh_base', lambda c: ['bash', '-c'])
    assert await transport_module.quiesce_provision(connection, 'uploads')
    with pytest.raises(RemoteTransportError):
        await cache._install_helper(connection, fence, 'bms_managed_runtime.py')
    monkeypatch.setattr(transport_module, '_ssh_base', lambda c: [str(fake_ssh), 'unused'])
    source.write_bytes(b'late mutation')
    with pytest.raises(RemoteTransportError):
        await transport_module.rsync_to_remote(connection, source, str(destination), delete=False, timeout=10)
    assert destination.read_bytes() == b'fixture upload'


def test_provision_unknown_start_and_reused_identity_remain_blocked(tmp_path):
    import os
    operation = tmp_path / 'managed-assets/provision-operations/unknown'
    operation.mkdir(parents=True)
    row = dict(schema='bms.provision-transport.v1', phase='starting',
               boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    witness = operation / 'command.json'
    for leader in (None, dict(pid=os.getpid(), start='not-current', group=os.getpid(), session=os.getpid())):
        witness.write_text(json.dumps(row | ({'leader': leader} if leader else {})))
        result = subprocess.run(_provision_command(tmp_path, 'unknown', 'quiesce'), capture_output=True, timeout=10)
        assert json.loads(result.stdout)['quiescent'] is False
        assert json.loads(witness.read_text())['phase'] == 'starting'


def test_source_identity_rejects_dirty_tracked_checkout(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", tmp_path], check=True)
    subprocess.run(["git", "-C", tmp_path, "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", tmp_path, "config", "user.name", "Test"], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", tmp_path, "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", tmp_path, "commit", "-qm", "fixture"], check=True)
    revision, tree = current_source_identity(tmp_path)
    from component_runtime import SourceIdentity

    assert SourceIdentity.from_checkout(tmp_path) == SourceIdentity(revision, tree)
    assert len(revision) == 40
    assert len(tree) == 40
    tracked.write_text("two\n", encoding="utf-8")
    # Compiler metadata is deliberately not a replacement for clean-source admission.
    assert SourceIdentity.from_checkout(tmp_path) == SourceIdentity(revision, tree)
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
    # Metadata-only fixture; real interpreter/container execution is covered in
    # test_remote_bundle_runtime_gaps and test_remote_bundle_container_gaps.
    base_bin = runtime_root / "python-runtime" / "bin"
    base_bin.mkdir(parents=True)
    (base_bin / "python3").write_bytes(b"fixture-python-not-executed")
    (base_bin / "python3").chmod(0o755)
    (runtime_root / "venv" / "bin" / "python").symlink_to(base_bin / "python3")
    (runtime_root / "venv" / "pyvenv.cfg").write_text(f"home = {base_bin}\n", encoding="utf-8")
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
    monkeypatch.setenv("BMS_CM_API_RUNTIME_DIR", str(runtime_root.parent))
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
        provenance={"remote_execution_assignment": {"gpu_indices": [0], "lease_id": "fixture-lease"}},
        assigned_gpu=0,
    )

    # Lower-layer bundle fixture, not evidence of scientific compilation.
    from dataclasses import replace
    from component_runtime import NativeInvocation, SourceIdentity

    invocation = replace(NativeInvocation.capture(
        model_id=job.model_id, mode=job.mode,
        command=["nextflow", "run", str(repository / "main.nf"),
                 "--input", str(job_input), "--out", str(output_dir)],
        requested=job.params, effective=job.params,
        native_parameters={"input": str(job_input), "out": str(output_dir)},
        entrypoint="main.nf",
    ), source_identity=SourceIdentity(revision, tree))
    from component_runtime import SelectedExecutionPlan
    # Transport-only projection retains the real CPU descriptor closure.
    from test_remote_bundle_runtime_gaps import bundle_metadata_fixture
    metadata = bundle_metadata_fixture()
    assert invocation.source_identity is not None and invocation.entrypoint is not None
    invocation = replace(invocation, execution_plan=SelectedExecutionPlan(
        source_identity=invocation.source_identity, workflow='fixture',
        model_id=invocation.model_id, mode=invocation.mode, entrypoint=invocation.entrypoint,
        requested_json=invocation.requested_json, effective_json=invocation.effective_json,
        native_parameters_json=invocation.native_parameters_json, metadata=metadata))
    invocation.materialize_inputs(output_dir)
    bundle = prepare_remote_bundle(
        job=job,
        target=SimpleNamespace(id="vast:123", remote_root="/opt/biomodstack"),
        command=list(invocation.command),
        native_invocation=invocation,
        environment={"NXF_ANSI_LOG": "false"},
    )
    try:
        assert bundle.envelope.source_revision == revision
        assert bundle.envelope.source_tree == tree
        assert bundle.envelope.command[0] == "/opt/biomodstack/runner/nextflow"
        assert bundle.envelope.command[2] == bundle.remote_source_dir + "/main.nf"
        assert "/opt/biomodstack/attempts/" in bundle.envelope.command[4]
        assert bundle.remote_output_alias == bundle.envelope.output_directory
        assert bundle.remote_output_alias.startswith("/opt/biomodstack/attempts/")
        assert str(data_root) not in " ".join(bundle.envelope.command)
        assert "API_BASE_URL" not in bundle.envelope.environment
        assert bundle.envelope.environment["BMS_REMOTE_EXECUTION"] == "1"
        assert bundle.envelope.environment["BMS_REMOTE_JOB_ID"] == job.id
        assert bundle.envelope.environment["BMS_REMOTE_ATTEMPT_ID"] == bundle.envelope.attempt_id
        assert bundle.envelope.environment["BMS_REMOTE_OUTPUT_ROOT"] == bundle.envelope.output_directory
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


@pytest.mark.asyncio
async def test_stage_bundle_never_uploads_existing_local_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_output = tmp_path / "old-results"
    local_output.mkdir()
    (local_output / "stale-result.cif").write_text("stale", encoding="utf-8")
    local_attempt = tmp_path / "attempt"
    local_attempt.mkdir()
    (local_attempt / "execution-envelope.json").write_text("{}", encoding="utf-8")
    bundle = SimpleNamespace(
        remote_runtime_dir="/remote/runtime",
        remote_attempt_dir="/remote/attempts/current",
        remote_output_alias="/remote/data/results/current",
        remote_source_dir="/remote/source",
        local_output_dir=local_output,
        local_attempt_dir=local_attempt,
        source_transfer=SimpleNamespace(remote_destination="/remote/source/archive"),
        runtime_transfers=(
            SimpleNamespace(
                remote_destination="/remote/runtime/data/runtime/cm-api-python/releases/release-1"
            ),
        ),
        input_transfers=(),
    )
    copied_sources: list[Path] = []

    async def fake_remote(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    async def fake_rsync(
        _connection: object,
        source: Path,
        _destination: str,
        **_kwargs: object,
    ) -> None:
        copied_sources.append(source)

    monkeypatch.setattr(executor_module, "run_remote", fake_remote)
    monkeypatch.setattr(executor_module, "rsync_to_remote", fake_rsync)
    monkeypatch.setattr(executor_module, "_transfer_plan", fake_remote)

    monkeypatch.setattr("services.remote_execution.cache.stage_cached_bundle", fake_remote)
    await executor_module._stage_bundle(SimpleNamespace(), bundle)  # type: ignore[arg-type]

    assert local_attempt in copied_sources
    assert local_output not in copied_sources


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
        execution_target_id="target-1",
        execution_source_revision="a" * 40,
        execution_source_tree="b" * 40,
        execution_bundle_sha256="c" * 64,
        provenance={},
        child_output_dir=None,
        output_dir=str(output),
    )

    published, previous = executor_module._publish_result_generation(job, incoming)

    assert published == output
    assert (output / "fresh.txt").read_text(encoding="utf-8") == "fresh"
    # Retain the transfer manifest as the durable provenance of this generation.
    assert (output / "result-manifest.json").read_text() == "{}"
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
