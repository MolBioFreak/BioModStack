"""Return-transfer placement and readiness avoid controller filesystem aliases."""
from pathlib import Path
from types import SimpleNamespace
import tempfile

import pytest

from services.remote_execution import executor, transport


@pytest.mark.asyncio
async def test_readiness_never_creates_controller_storage(tmp_path, monkeypatch):
    controller = tmp_path/'controller-storage'
    captured = []
    monkeypatch.setattr(transport, 'get_data_root', lambda: controller)
    async def run(connection, argv, **kwargs):
        captured.extend(argv)
        return transport.CommandResult(0, '{"gpus": []}', '')
    monkeypatch.setattr(transport, 'run_remote', run)
    connection = transport.RemoteConnection('target', 'unused', 22, 'user', '/remote')
    await transport.probe_readiness(connection)
    assert str(controller) not in ' '.join(captured)


@pytest.mark.asyncio
async def test_incoming_transfer_stages_on_results_filesystem(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='bms-return-', dir='/dev/shm') as directory:
        output = Path(directory)/'results/job'
        output.parent.mkdir()
        job = SimpleNamespace(execution_target_id='target', remote_attempt_id='attempt', output_dir=str(output), child_output_dir=None)
        class Session:
            async def get(self, *args, **kwargs):
                return SimpleNamespace()
        monkeypatch.setattr(executor, 'get_data_root', lambda: tmp_path/'state')
        monkeypatch.setattr(executor, '_connection_for_attempt', lambda *_: (None, '/remote/attempt'))
        async def fetch(connection, remote, incoming, job, status):
            assert incoming.parent.stat().st_dev == output.parent.stat().st_dev
            assert incoming.is_relative_to(output.parent)
            incoming.mkdir()
            return SimpleNamespace(artifacts=[])
        monkeypatch.setattr(executor, '_fetch_result_manifest', fetch)
        monkeypatch.setattr(executor, '_verify_result_package', lambda *args: SimpleNamespace(artifacts=[]))
        _, incoming = await executor.collect_remote_results(Session(), job, None)
        assert incoming.exists()
