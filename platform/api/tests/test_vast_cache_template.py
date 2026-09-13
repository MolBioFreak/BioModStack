"""Execute the VM startup helper; HTTP is an explicit offline transport double."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from services.remote_execution.cache_template import render_vm_cache_template_fields

TOOL = Path(__file__).parents[1] / 'tools/bms_artifact_cache.py'
URL = ('https://raw.githubusercontent.com/MolBioFreak/BioModStack/' + '1' * 40
       + '/platform/api/tools/bms_artifact_cache.py')


def fields(**kwargs):
    return render_vm_cache_template_fields(helper_bytes=TOOL.read_bytes(), helper_url=URL, **kwargs)


def transport(tmp_path, payload=None):
    """Only replace network reads, never helper execution or cache storage."""
    payload_path = tmp_path / 'http-body'
    payload_path.write_bytes(TOOL.read_bytes() if payload is None else payload)
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    (hooks / 'sitecustomize.py').write_text(
        'import io,pathlib,urllib.request\n'
        'class Offline:\n'
        ' def open(self,url,timeout):\n'
        f'  assert url=={URL!r}, "unexpected download"\n'
        f'  return io.BytesIO(pathlib.Path({str(payload_path)!r}).read_bytes())\n'
        'urllib.request.build_opener=lambda *args: Offline()\n'
    )
    return {'PATH': str(Path(sys.executable).parent) + ':' + os.environ['PATH'], 'PYTHONPATH': str(hooks)}


@pytest.mark.parametrize('from_environment_file', [False, True])
def test_bounded_template_initializes_verified_helper_offline(tmp_path, from_environment_file):
    root = tmp_path / 'worker'
    rendered = fields(worker_root=str(root))
    assert len(rendered['onstart']) <= 4048
    assert len(rendered['env']) <= 4096
    assert rendered['onstart'].startswith('#!/bin/bash\n')
    environment = transport(tmp_path)
    script = rendered['onstart']
    if from_environment_file:
        file = tmp_path / 'environment'
        file.write_text('\n'.join(f'{k}="{v}"' for k, v in rendered['environment'].items())
                        + '\nUNRELATED="$(touch never-executed)"\n')
        script = script.replace("pathlib.Path('/etc/environment')", f'pathlib.Path({str(file)!r})')
    else:
        environment.update(rendered['environment'])
    for _ in range(2):
        result = subprocess.run(['bash'], input=script, text=True, capture_output=True,
                                env=environment, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)['state'] == 'ready'
    payload = TOOL.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    assert (root / 'runner' / f'cache-{digest}.py').read_bytes() == payload
    assert (root / 'cache/artifacts/v1/objects/sha256').is_dir()
    assert not list((root / 'cache/artifacts/v1/objects/sha256').iterdir())
    assert not (tmp_path / 'never-executed').exists()


@pytest.mark.parametrize('kind', ['digest', 'body', 'oversize'])
def test_tampered_helper_never_executes(tmp_path, kind):
    root = tmp_path / 'worker'
    rendered = fields(worker_root=str(root))
    body = {'digest': None, 'body': b'raise RuntimeError("should not execute")',
            'oversize': b'x' * (2097152 + 1)}[kind]
    if kind == 'digest':
        rendered['environment']['BMS_CACHE_HELPER_SHA256'] = '0' * 64
    result = subprocess.run(['bash'], input=rendered['onstart'], text=True, capture_output=True,
                            env={**transport(tmp_path, body), **rendered['environment']})
    assert result.returncode != 0
    assert 'cache bootstrap hash mismatch' in result.stderr
    assert not root.exists()


def test_existing_template_env_is_preserved_and_bounded():
    assert fields(existing_env='-p 22:22 -e EXAMPLE=kept')['env'].startswith('-p 22:22 -e EXAMPLE=kept ')
    with pytest.raises(ValueError, match='env field'):
        fields(existing_env='-e LONG=' + 'x' * 4096)
    for key in ['BMS_CACHE_WORKER_ROOT', 'BMS_CACHE_HELPER_GZ_B64']:
        with pytest.raises(ValueError, match='reconciliation'):
            fields(existing_env=f'-e {key}=/other')


@pytest.mark.parametrize('root', ['/', '/tmp/../escape', '/tmp/x;command', 'relative'])
def test_invalid_template_root_rejected(root):
    with pytest.raises(ValueError, match='root'):
        fields(worker_root=root)


@pytest.mark.parametrize('url', [URL.replace('1' * 40, 'test'), URL.replace('https:', 'http:'),
                                  URL + '?download=1', URL.replace('raw.githubusercontent.com', 'example.com')])
def test_mutable_or_untrusted_helper_urls_rejected(url):
    with pytest.raises(ValueError, match='immutable'):
        render_vm_cache_template_fields(helper_bytes=TOOL.read_bytes(), helper_url=url)


def test_symlink_root_is_rejected_before_publication(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'worker'
    root.symlink_to(outside, target_is_directory=True)
    rendered = fields(worker_root=str(root))
    result = subprocess.run(['bash'], input=rendered['onstart'], text=True, capture_output=True,
                            env={**transport(tmp_path), **rendered['environment']})
    assert result.returncode != 0
    assert 'unsafe cache bootstrap path' in result.stderr
    assert not list(outside.iterdir())


def test_helper_growth_does_not_consume_provider_fields():
    small = render_vm_cache_template_fields(helper_bytes=b'x', helper_url=URL)
    large = render_vm_cache_template_fields(helper_bytes=b'x' * 2097152, helper_url=URL)
    assert len(small['onstart']) == len(large['onstart']) <= 4048
    assert len(small['env']) == len(large['env']) <= 4096
    with pytest.raises(ValueError, match='size'):
        render_vm_cache_template_fields(helper_bytes=b'x' * 2097153, helper_url=URL)
