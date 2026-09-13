"""Guarded backend-selection and canonical-image handoff fault tests."""
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from tools import bms_managed_runtime as managed


@pytest.mark.parametrize('apptainer_ok', [True, False])
def test_selected_backend_executes_only_canonical_sif(tmp_path, monkeypatch, apptainer_ok):
    root = tmp_path / 'managed-assets/v1'
    release = root / 'releases/generation'
    image = tmp_path / 'cache/runtime-images/objects/sha256/digest/runtime.sif'
    calls = []
    installs = []
    monkeypatch.setattr(managed, 'canonical_probe_image', lambda *args: image)

    def run(argv, **kwargs):
        if argv[0] == 'bash':
            installs.append(argv)
            return SimpleNamespace(returncode=0)
        calls.append((argv, kwargs))
        ok = apptainer_ok or argv[0] == str(release / 'bin/bms-container')
        return SimpleNamespace(returncode=0 if ok else 1, stdout='BMS_CUDA_OK\n' if ok else '', stderr='')

    monkeypatch.setattr(subprocess, 'run', run)
    result = managed.qualify_container(root, release, {})
    assert result['backend'] == ('apptainer' if apptainer_ok else 'udocker')
    assert len(calls) == (1 if apptainer_ok else 2)
    assert len(installs) == (0 if apptainer_ok else 1)
    if installs:
        assert installs[0] == ['bash', str(release / 'lib/bootstrap_worker.sh'), 'udocker', str(tmp_path)]
    for argv, kwargs in calls:
        assert argv[1:4] == ['exec', '--nv', str(image)]
        assert not any(value.startswith('docker://') for value in argv)
    if not apptainer_ok:
        env = calls[-1][1]['env']
        assert env['BMS_UDOCKER'] == str(tmp_path / 'tools/udocker-1.3.17/bin/udocker')
        assert env['UDOCKER_BIN'] == str(tmp_path / 'tools/udocker-1.3.17/engines/bin')
        assert env['UDOCKER_LIB'] == str(tmp_path / 'tools/udocker-1.3.17/engines/lib')
        assert env['BMS_CONTAINER_WORK_ROOT'] == str(tmp_path / 'container-workspaces')
        assert env['BMS_RUNTIME_IMAGE_STORE'] == str(tmp_path / 'cache/runtime-images')


def test_no_backend_success_is_not_qualification(tmp_path, monkeypatch):
    monkeypatch.setattr(managed, 'canonical_probe_image', lambda *args: tmp_path / 'canonical.sif')
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=''))
    with pytest.raises(ValueError, match='CUDA container verification failed'):
        managed.qualify_container(tmp_path / 'managed-assets/v1', tmp_path / 'generation', {})


def test_bootstrap_does_not_gate_namespace_policy():
    source = (Path(__file__).parents[1] / 'services/remote_execution/bootstrap_worker.sh').read_text()
    assert 'unshare' not in source
    assert 'fd6589de0f3af7c1cd6a29554f2c00f2ef1fbd91da184ca30f8cea1eb42bcd2b' in source
    assert '2a4804ba82e087ca3e99305fce9887227925d2b56aaefcad6474c4cb86b7a157' in source
    embedded = source.split("<<'PY_INSTALL'\n", 1)[1].split('\nPY_INSTALL', 1)[0]
    compile(embedded, 'bootstrap-upstream-installer', 'exec')


def test_upstream_checksum_failure_publishes_nothing(tmp_path, monkeypatch):
    import io
    import sys
    import urllib.request
    source = (Path(__file__).parents[1] / 'services/remote_execution/bootstrap_worker.sh').read_text()
    embedded = source.split("<<'PY_INSTALL'\n", 1)[1].split('\nPY_INSTALL', 1)[0]
    monkeypatch.setattr(sys, 'argv', ['installer', str(tmp_path)])
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *args, **kwargs: io.BytesIO(b'wrong upstream bytes'))
    with pytest.raises(ValueError, match='upstream checksum mismatch'):
        exec(compile(embedded, 'bootstrap-upstream-installer', 'exec'), {})
    assert not (tmp_path / 'tools/udocker-1.3.17').exists()
    assert not list((tmp_path / 'tools').iterdir())


def test_exact_upstream_install_repeat_and_corruption(tmp_path, monkeypatch):
    """Use caller-supplied independently acquired pinned upstream artifacts."""
    import os
    import sys
    import urllib.request
    import json
    wheel = os.environ.get('BMS_TEST_UDOCKER_WHEEL')
    engines = os.environ.get('BMS_TEST_UDOCKER_ENGINES')
    if not wheel or not engines:
        pytest.skip('independently downloaded pinned upstream artifacts not supplied')
    source = (Path(__file__).parents[1] / 'services/remote_execution/bootstrap_worker.sh').read_text()
    embedded = source.split("<<'PY_INSTALL'\n", 1)[1].split('\nPY_INSTALL', 1)[0]
    monkeypatch.setattr(sys, 'argv', ['installer', str(tmp_path)])
    monkeypatch.setattr(urllib.request, 'urlopen', lambda url, **kwargs:
                        Path(wheel if url.endswith('.whl') else engines).open('rb'))
    for _ in range(2):
        exec(compile(embedded, 'bootstrap-upstream-installer', 'exec'), {})
    installed = tmp_path / 'tools/udocker-1.3.17'
    record = json.loads((installed / 'manifest.json').read_text())
    assert 'bin/udocker' in record['files']
    assert 'engines/bin/proot-x86_64' in record['files']
    output = subprocess.run([str(installed / 'bin/udocker'), '--allow-root', '--version'],
                            capture_output=True, text=True, timeout=30)
    assert output.returncode == 0, output.stdout + output.stderr
    assert '1.3.17' in output.stdout
    (installed / 'engines/bin/proot-x86_64').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='udocker installed byte mismatch'):
        exec(compile(embedded, 'bootstrap-upstream-installer', 'exec'), {})


def test_probe_publication_uses_canonical_lease_and_recovers_receipt(tmp_path, monkeypatch):
    import sys
    source_root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(source_root))
    root = tmp_path / 'managed-assets/v1'
    root.mkdir(parents=True)
    calls = []

    def pull(argv, **kwargs):
        calls.append(argv)
        assert argv[:3] == ['apptainer', 'pull', '--disable-cache']
        assert argv[-1] == managed.PROBE_OCI
        # Acquisition-boundary byte fixture, never executed as a container.
        Path(argv[3]).write_bytes(b'opaque pinned image acquisition fixture')
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, 'run', pull)
    release = root / 'releases/generation'
    image = managed.canonical_probe_image(root, release)
    assert image.is_relative_to(tmp_path / 'cache/runtime-images/objects/sha256')
    assert managed.canonical_probe_image(root, release) == image
    (root / 'probe-image.json').unlink()
    assert managed.canonical_probe_image(root, release) == image
    assert len(calls) == 1
    assert list(root.rglob('*.sif')) == []
    from scripts.lib.runtime_image_lifecycle import load_state
    assert len(load_state(tmp_path / 'cache/runtime-images')['leases']) == 1
