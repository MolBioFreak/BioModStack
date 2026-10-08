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


@pytest.mark.parametrize('fail_promotion', [False, True])
def test_existing_generation_publication_and_rollback_on_results_filesystem(tmp_path, monkeypatch, fail_promotion):
    with tempfile.TemporaryDirectory(prefix='bms-publish-', dir='/dev/shm') as directory:
        root = Path(directory)
        assert root.stat().st_dev != tmp_path.stat().st_dev
        output = root/'job'
        output.mkdir()
        (output/'old.txt').write_text('retained-original')
        incoming = root/'incoming'
        incoming.mkdir()
        (incoming/'new.txt').write_text('verified-new-generation')
        (incoming/'result-manifest.json').write_text('{}')
        job = SimpleNamespace(id='job', remote_attempt_id='attempt', output_dir=str(output), child_output_dir=None)
        monkeypatch.setattr(executor, 'get_data_root', lambda: tmp_path/'state')
        replace = executor.os.replace
        def promote(source, destination):
            if fail_promotion and Path(source) == incoming:
                raise OSError('injected promotion failure')
            return replace(source, destination)
        monkeypatch.setattr(executor.os, 'replace', promote)
        if fail_promotion:
            with pytest.raises(OSError, match='injected promotion failure'):
                executor._publish_result_generation(job, incoming)
            assert (output/'old.txt').read_text() == 'retained-original'
            assert (incoming/'new.txt').read_text() == 'verified-new-generation'
        else:
            published, backup = executor._publish_result_generation(job, incoming)
            assert published == output
            assert (output/'new.txt').read_text() == 'verified-new-generation'
            assert not (output/'old.txt').exists()
            assert backup is not None and backup.stat().st_dev == output.stat().st_dev
            assert (backup/'old.txt').read_text() == 'retained-original'
        assert not (tmp_path/'state').exists()


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
