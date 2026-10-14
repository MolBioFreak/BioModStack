"""Non-reflink protected bind allocation at the actual container driver."""
import errno
import os
import stat
import pytest
from test_userspace_container_driver import driver


@pytest.mark.parametrize('reason', [errno.EXDEV, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL, errno.ENOSYS])
def test_private_input_fallback_preserves_donor_aliases_and_modes(tmp_path, monkeypatch, reason):
    source, output = tmp_path/'source', tmp_path/'output'
    source.mkdir()
    leaf = source/'payload'
    leaf.write_bytes(b'original input' * 100000)
    leaf.chmod(0o444)
    os.link(leaf, source/'alias')
    (source/'link').symlink_to('payload')
    before = driver.snapshot(source)
    def unsupported(out, operation, fd):
        os.write(out, b'partial ioctl data')
        raise OSError(reason, 'unsupported')
    monkeypatch.setattr(driver.views.fcntl, 'ioctl', unsupported)
    assert driver.copy_input(source, output, '/input', [], writable=True) == before
    assert driver.snapshot(source) == before
    private = output/'payload'
    assert private.read_bytes() == leaf.read_bytes()
    assert private.stat().st_ino != leaf.stat().st_ino
    assert private.stat().st_ino == (output/'alias').stat().st_ino
    assert stat.S_IMODE(private.stat().st_mode) == 0o644
    assert os.readlink(output/'link') == 'payload'
    private.write_bytes(b'private edit')
    assert leaf.read_bytes().startswith(b'original input')


@pytest.mark.parametrize('reason', [errno.EACCES, errno.EPERM, errno.EIO, errno.ENOSPC, errno.ENOMEM, errno.EDQUOT])
def test_private_input_operational_fault_is_not_feature_fallback(tmp_path, monkeypatch, reason):
    leaf = tmp_path/'source'
    leaf.write_bytes(b'unchanged')
    def fault(*args):
        raise OSError(reason, 'operational fault')
    monkeypatch.setattr(driver.views.fcntl, 'ioctl', fault)
    monkeypatch.setattr(driver.views.os, 'pread', lambda *args: pytest.fail('fault masked by copy'))
    with pytest.raises(OSError) as error:
        driver.copy_input(leaf, tmp_path/'output', '/input', [])
    assert error.value.errno == reason
    assert leaf.read_bytes() == b'unchanged'


def test_private_input_fallback_rejects_symlink_destination(tmp_path, monkeypatch):
    leaf, donor = tmp_path/'source', tmp_path/'donor'
    leaf.write_bytes(b'input')
    donor.write_bytes(b'sentinel')
    (tmp_path/'output').symlink_to(donor)
    with pytest.raises(FileExistsError):
        driver.copy_input(leaf, tmp_path/'output', '/input', [])
    assert donor.read_bytes() == b'sentinel'
