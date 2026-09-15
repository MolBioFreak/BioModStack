"""Permission/isolation unit tests; real CoW is qualified separately."""
import importlib.util
import os
from pathlib import Path
import stat
import sys

import pytest

spec = importlib.util.spec_from_file_location('shared_weight_mount_driver', Path(__file__).parents[1] / 'platform/api/tools/bms_container.py')
driver = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = driver
spec.loader.exec_module(driver)


@pytest.mark.parametrize('single_file', [False, True])
def test_private_writable_weight_view_never_thaws_shared_bytes(tmp_path, monkeypatch, single_file):
    installed = tmp_path / 'installed'
    installed.mkdir()
    source = installed / 'model.pt'
    source.write_bytes(b'generic immutable input fixture')
    source.chmod(0o444)
    installed.chmod(0o555)
    original = driver.snapshot(installed)
    # Only a deterministic unit double. It is NOT evidence of physical CoW.
    def clone(destination, operation, fd):
        assert operation == driver.views.FICLONE
        os.write(destination, os.pread(fd, os.fstat(fd).st_size, 0))
    monkeypatch.setattr(driver.fcntl, 'ioctl', clone)
    target = tmp_path / 'private'
    driver.copy_input(source if single_file else installed, target, '/weights', [], writable=True)
    private_file = target if single_file else target / 'model.pt'
    assert stat.S_IMODE(private_file.stat().st_mode) == 0o644
    private_file.write_bytes(b'task-only mutation')
    if not single_file:
        assert stat.S_IMODE(target.stat().st_mode) == 0o755
        (target / 'jit-cache').mkdir()
        (target / 'jit-cache/new').write_bytes(b'task-only cache')
    assert source.read_bytes() == b'generic immutable input fixture'
    assert driver.snapshot(installed) == original
    assert stat.S_IMODE(source.stat().st_mode) == 0o444
    installed.chmod(0o755)
