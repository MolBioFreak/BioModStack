"""Pinned PRoot writes through declared/automatic aliases, no container or inference."""
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from tools import bms_container as container


def test_private_reflink_views_shadow_broad_and_canonical_aliases(tmp_path):
    authority = tmp_path / 'worker/cache/artifacts/v1'
    shared = authority / 'weights/selected'
    shared.mkdir(parents=True)
    payload = b'original weight'
    digest = hashlib.sha256(payload).hexdigest()
    member = shared / 'weight.bin'
    member.write_bytes(payload)
    member.chmod(0o444)
    obj = authority / 'objects/sha256' / digest[:2] / digest
    obj.parent.mkdir(parents=True)
    os.link(member, obj)
    (shared / '.bms-weights.json').write_text(json.dumps([
        dict(name='weight.bin', sha256=digest, size_bytes=len(payload), mode=0o444)]))
    mutable = tmp_path / 'worker/output'
    mutable.mkdir()
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    requested = [(str(tmp_path / 'worker'), '/broad', 'rw'),
                 (str(shared), '/weights', 'ro'),
                 (str(shared), '/auto', 'rw')]
    try:
        volumes, guarded = container.project_bindings(requested, inputs, [], shared=shared)
    except OSError as error:
        if error.errno in {errno.EOPNOTSUPP, errno.EXDEV, errno.ENOTTY}:
            pytest.skip('Fixture filesystem lacks required FICLONE; no byte-copy fallback')
        raise
    mapped = dict((guest, Path(host)) for host, guest in volumes)
    view = mapped['/weights']
    assert view == mapped['/auto'] == mapped[str(shared)]
    assert mapped['/broad/cache/artifacts/v1/weights/selected'] == view
    assert mapped['/broad/cache/artifacts/v1/objects/sha256/' + digest[:2] + '/' + digest] == view / 'weight.bin'
    assert mapped[str(obj)] == view / 'weight.bin'
    assert mapped['/broad'] == tmp_path / 'worker'
    assert member.stat().st_ino != (view / 'weight.bin').stat().st_ino
    proot = Path(__file__).resolve().parents[3] / 'containers/transport/proot/proot-x86_64-4_8_0'
    command = [str(proot), '-0', '-r', '/']
    for host, guest in volumes:
        command.extend(['-b', host + ':' + guest])
    aliases = ['/weights/weight.bin', '/auto/weight.bin',
               '/broad/cache/artifacts/v1/weights/selected/weight.bin',
               str(member), str(obj),
               '/broad/cache/artifacts/v1/objects/sha256/' + digest[:2] + '/' + digest]
    script = '''import pathlib,sys
for name in sys.argv[1:]:
 p=pathlib.Path(name)
 p.write_bytes(b'private change')
pathlib.Path('/broad/output/result').write_bytes(b'native writable output')
'''
    result = subprocess.run([*command, sys.executable, '-B', '-c', script, *aliases],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert member.read_bytes() == obj.read_bytes() == payload
    assert (mutable / 'result').read_bytes() == b'native writable output'
    assert (view / 'weight.bin').read_bytes() == b'private change'
    assert container.snapshot(guarded[0][2]) != guarded[0][3]  # existing ro postcheck sees mutation


def test_pinned_proot_writable_cache_all_aliases_without_payload_clone(tmp_path):
    """Native directory-only bind routing; NOT a payload/FICLONE qualification."""
    source = tmp_path / 'weights'
    cache = source / 'native-cache'
    cache.mkdir(parents=True)
    (cache / 'existing').write_bytes(b'never cloned')
    inputs = tmp_path / 'views'
    inputs.mkdir()
    selected = [(str(source), '/weights', 'ro'), (str(source), str(source), 'ro')]
    writable = [(str(cache), '/weights/native-cache', 'rw')]
    requested = [(str(tmp_path), '/broad', 'rw'), (str(tmp_path), '/home/fixture', 'rw'),
                 (str(source), '/auto', 'rw'), *writable, *selected]
    volumes, guarded = container.project_bindings(requested, inputs, [], writable_overrides=writable)
    mapped = {guest: Path(host) for host, guest in volumes}
    assert mapped['/weights'] == mapped['/auto'] == mapped[str(source)]
    for alias in ('/weights/native-cache', '/auto/native-cache', '/broad/weights/native-cache',
                  '/home/fixture/weights/native-cache', str(cache)):
        assert mapped[alias] == cache
    assert not (mapped['/weights'] / 'native-cache/existing').exists()
    proot = Path(__file__).resolve().parents[3] / 'containers/transport/proot/proot-x86_64-4_8_0'
    command = [str(proot), '-0', '-r', '/']
    for host, guest in volumes:
        command.extend(['-b', host + ':' + guest])
    script = "import pathlib; pathlib.Path('/home/fixture/weights/native-cache/result').write_bytes(b'native cache')"
    result = subprocess.run([*command, sys.executable, '-B', '-c', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (cache / 'result').read_bytes() == b'native cache'
    for original, before, projected, original_view in guarded:
        assert container.snapshot(original, ['native-cache']) == before
        assert container.snapshot(projected, ['native-cache']) == original_view
