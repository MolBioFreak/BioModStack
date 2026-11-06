"""GPU launcher retains optional host telemetry without changing admission."""
from contextlib import contextmanager
from pathlib import Path

import pytest
from tools import bms_container as driver


@pytest.mark.parametrize('gpu', [False, True])
@pytest.mark.parametrize('available', [False, True])
def test_actual_launcher_binds_optional_cli_after_image_environment(tmp_path, monkeypatch, gpu, available):
    rootfs = tmp_path / 'private/rootfs'
    rootfs.mkdir(parents=True)
    executable = tmp_path / 'host-nvidia-smi'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    alias = tmp_path / 'nvidia-smi'
    alias.symlink_to(executable)
    launcher = tmp_path / 'udocker'
    launcher.touch()
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'store'))
    monkeypatch.setenv('BMS_CONTAINER_WORK_ROOT', str(tmp_path / 'work'))
    monkeypatch.setenv('BMS_UDOCKER', str(launcher))
    monkeypatch.delenv('BMS_SHARED_WEIGHTS_ROOT', raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(driver, 'canonical_image', lambda *a: (tmp_path / 'image', 'a' * 64))
    @contextmanager
    def private(*args, **kwargs):
        yield dict(rootfs=rootfs, image_fd=0)
    monkeypatch.setattr(driver.views, 'private_image_view', private)
    monkeypatch.setattr(driver, 'host_system_bindings', lambda _: [])
    monkeypatch.setattr(driver, 'nvidia_libraries', lambda: {'libnvidia-ml.so.1': '/host/libnvml.so', 'libcuda.so.1': '/host/libcuda.so'})
    monkeypatch.setattr(driver.shutil, 'which', lambda name: str(alias) if available else None)
    def run(command, environment, pass_fds, identity):
        entry = (rootfs / '.bms-exec.sh').read_text()
        volume = '--volume=' + str(executable) + ':/.bms-nvidia/nvidia-smi'
        assert (volume in command) is (gpu and available)
        assert ('export PATH="/.bms-nvidia${PATH:+:$PATH}"' in entry) is gpu
        if gpu:
            assert '--volume=/host/libnvml.so:/.bms-nvidia/libnvidia-ml.so.1' in command
            assert entry.index('source "$f"') < entry.index('export PATH="/.bms-nvidia')
            assert entry.index('export PATH="/.bms-nvidia') < entry.index('exec "$@"')
        assert executable.read_text() == '#!/bin/sh\nexit 0\n'
        return 0
    monkeypatch.setattr(driver, 'run_owned', run)
    assert driver.execute(driver.Invocation('/image', ['true'], gpu=gpu), {}) == 0
