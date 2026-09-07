"""Exercise the real VM cache bootstrap within Vast's documented field limit."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from services.remote_execution.cache_template import render_vm_cache_template_fields

TOOL = Path(__file__).parents[1] / 'tools/bms_artifact_cache.py'


@pytest.mark.parametrize('from_environment_file', [False, True])
def test_bounded_template_initializes_verified_helper_offline(tmp_path, from_environment_file):
    root = tmp_path / 'worker'
    payload = TOOL.read_bytes()
    fields = render_vm_cache_template_fields(helper_bytes=payload, worker_root=str(root))
    assert len(fields['onstart']) <= 4048
    assert len(fields['env']) <= 4096
    assert fields['onstart'].startswith('#!/bin/bash\n')
    environment = {'PATH': os.environ['PATH']}
    script = fields['onstart']
    if from_environment_file:
        file = tmp_path / 'environment'
        file.write_text('\n'.join(f'{k}="{v}"' for k, v in fields['environment'].items()) + '\nUNRELATED="$(touch never-executed)"\n')
        script = script.replace("pathlib.Path('/etc/environment')", f'pathlib.Path({str(file)!r})')
    else:
        environment.update(fields['environment'])
    for _ in range(2):
        result = subprocess.run(['bash'], input=script, text=True, capture_output=True, env=environment, check=True)
        assert json.loads(result.stdout)['state'] == 'ready'
    digest = hashlib.sha256(payload).hexdigest()
    assert (root / 'runner' / f'cache-{digest}.py').read_bytes() == payload
    assert (root / 'cache/artifacts/v1/objects/sha256').is_dir()
    assert not list((root / 'cache/artifacts/v1/objects/sha256').iterdir())


def test_tampered_helper_never_executes(tmp_path):
    root = tmp_path / 'worker'
    fields = render_vm_cache_template_fields(helper_bytes=TOOL.read_bytes(), worker_root=str(root))
    fields['environment']['BMS_CACHE_HELPER_SHA256'] = '0' * 64
    result = subprocess.run(['bash'], input=fields['onstart'], text=True, capture_output=True,
                            env={'PATH': os.environ['PATH'], **fields['environment']})
    assert result.returncode != 0
    assert 'cache bootstrap hash mismatch' in result.stderr
    assert not root.exists()


def test_existing_template_env_is_preserved_and_bounded():
    fields = render_vm_cache_template_fields(helper_bytes=TOOL.read_bytes(), existing_env='-p 22:22 -e EXAMPLE=kept')
    assert fields['env'].startswith('-p 22:22 -e EXAMPLE=kept ')
    with pytest.raises(ValueError, match='env field'):
        render_vm_cache_template_fields(helper_bytes=TOOL.read_bytes(), existing_env='-e LONG=' + 'x' * 4096)
    with pytest.raises(ValueError, match='reconciliation'):
        render_vm_cache_template_fields(helper_bytes=TOOL.read_bytes(), existing_env='-e BMS_CACHE_WORKER_ROOT=/other')


@pytest.mark.parametrize('root', ['/', '/tmp/../escape', '/tmp/x;command', 'relative'])
def test_invalid_template_root_rejected(root):
    with pytest.raises(ValueError):
        render_vm_cache_template_fields(helper_bytes=b'helper', worker_root=root)
