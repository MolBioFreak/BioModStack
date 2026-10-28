"""Exercise the shipped transport bytes, not a mock of permission emulation."""
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROOT = ROOT / 'containers/transport/proot/proot-x86_64-4_8_0'
pytestmark = pytest.mark.skipif(sys.platform != 'linux' or platform.machine() != 'x86_64',
                              reason='the pinned worker transport is Linux x86_64')


def run_guest(code, *args):
    result = subprocess.run([str(PROOT), '-0', '-r', '/', sys.executable, '-B', '-c', code, *map(str, args)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def identity(path):
    s = path.stat()
    return s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def test_shipped_proot_pin():
    assert hashlib.sha256(PROOT.read_bytes()).hexdigest() == '55047d1f180eb3a93483ea3d68da6b2f87eeba0fd68ca50642e9697fa3bf3ec3'


@pytest.mark.parametrize('call', ['open', 'openat'])
def test_readonly_open_and_metadata_preserve_file_and_parent(tmp_path, call):
    parent = tmp_path / 'object'
    parent.mkdir()
    image = parent / 'runtime.sif'
    image.write_bytes(b'canonical bytes')
    image.chmod(0o400)
    parent.chmod(0o500)
    before = identity(image), identity(parent)
    try:
        # libc syscall bypasses Python's openat choice to cover both ABI owners.
        code = '''import ctypes,os,sys
assert os.getuid() == os.geteuid() == 0
libc=ctypes.CDLL(None,use_errno=True)
p=sys.argv[1].encode()
for _ in range(3):
 os.stat(p)
 if sys.argv[2]=='open': fd=libc.syscall(2,p,os.O_RDONLY)
 else: fd=libc.syscall(257,-100,p,os.O_RDONLY)
 assert fd>=0,ctypes.get_errno()
 assert os.read(fd,100)==b'canonical bytes'
 os.close(fd)
'''
        run_guest(code, image, call)
        assert before == (identity(image), identity(parent))
    finally:
        parent.chmod(0o700)


@pytest.mark.parametrize('call', ['open', 'openat'])
@pytest.mark.parametrize('mode', [0o400, 0o644])
@pytest.mark.parametrize('absolute', [False, True])
def test_readonly_symlink_resolution_preserves_target_identity(tmp_path, call, mode, absolute):
    blob = tmp_path / 'blob'
    blob.write_bytes(b'checkpoint')
    blob.chmod(mode)
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    alias = snapshot / 'model.safetensors'
    alias.symlink_to(blob if absolute else '../blob')
    before = identity(blob), identity(tmp_path), identity(snapshot)
    run_guest('''import ctypes,os,sys
libc=ctypes.CDLL(None,use_errno=True)
p=sys.argv[1].encode()
for _ in range(3):
 os.stat(p)
 if sys.argv[2]=='open': fd=libc.syscall(2,p,os.O_RDONLY)
 else: fd=libc.syscall(257,-100,p,os.O_RDONLY)
 assert fd>=0,ctypes.get_errno()
 assert os.read(fd,100)==b'checkpoint'
 os.close(fd)
''', alias, call)
    assert before == (identity(blob), identity(tmp_path), identity(snapshot))


@pytest.mark.parametrize('flags', ['os.O_WRONLY', 'os.O_RDWR', 'os.O_RDONLY|os.O_TRUNC'])
def test_fake_root_mutating_opens_still_work_and_restore_mode(tmp_path, flags):
    image = tmp_path / 'runtime.sif'
    image.write_bytes(b'canonical bytes')
    image.chmod(0o400)
    before = identity(image)
    run_guest(f'''import os,sys
assert os.getuid()==0
fd=os.open(sys.argv[1],{flags})
if not ({flags}) & os.O_TRUNC: os.write(fd,b'X')
os.close(fd)
''', image)
    assert image.stat().st_mode & 0o777 == 0o400
    assert identity(image) != before
    assert image.read_bytes() == (b'' if 'TRUNC' in flags else b'Xanonical bytes')


def test_fake_root_inaccessible_read_and_identity_emulation(tmp_path):
    image = tmp_path / 'inaccessible'
    image.write_bytes(b'x')
    image.chmod(0)
    try:
        run_guest('''import os,sys
assert os.getuid()==os.geteuid()==0
with open(sys.argv[1],'rb') as f: assert f.read()==b'x'
os.setuid(1234)
assert os.getuid()==os.geteuid()==1234
''', image)
        assert image.stat().st_mode & 0o777 == 0
    finally:
        image.chmod(0o600)


def test_real_canonical_attestation_and_same_bytes_mutation_rejection(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    from prepare_runtime_image_attestation import create_verified_image_reference
    source = tmp_path / 'source.sif'
    source.write_bytes(b'bounded inert image fixture')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = tmp_path / 'store'
    reference, receipt = tmp_path / 'reference.json', tmp_path / 'receipt.json'
    create_verified_image_reference(image=source, expected_sha256=digest, store_root=store,
                                    reference=reference, receipt=receipt)
    image = store / 'objects/sha256' / digest / 'runtime.sif'
    code = '''import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from prepare_runtime_image_attestation import resolve_verified_image_reference
p=resolve_verified_image_reference(reference=Path(sys.argv[2]),receipt=Path(sys.argv[3]),expected_sha256=sys.argv[4],store_root=Path(sys.argv[5]),executing_image=Path(sys.argv[6]))
assert str(p)==sys.argv[6]
'''
    args = ROOT / 'scripts', reference, receipt, digest, store, image
    before = identity(image), identity(image.parent)
    for _ in range(2):
        run_guest(code, *args)
        assert before == (identity(image), identity(image.parent))
    run_guest('import os,sys; os.chmod(sys.argv[1],0o600); os.chmod(sys.argv[1],0o400)', image)
    assert hashlib.sha256(image.read_bytes()).hexdigest() == digest
    result = subprocess.run([str(PROOT), '-0', '-r', '/', sys.executable, '-B', '-c', code, *map(str,args)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode != 0
    assert 'identity changed since preflight' in result.stderr
