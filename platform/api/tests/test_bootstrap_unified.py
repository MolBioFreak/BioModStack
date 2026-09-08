"""Combined preview/discovery entrypoint and shared path authority regressions."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_bootstrap_cli import isolated, snapshot
from test_bootstrap_review_regressions import archived
from biomodstack_runtime_profile import managed_runtime_storage_paths, resolve_runtime_paths


@pytest.mark.parametrize('entry', ['shell', 'python_action_first', 'python_option_first'])
@pytest.mark.parametrize('valid', [True, False])
def test_archived_preview_empty_home_fresh_prefix(isolated, archived, entry, valid):
    document = isolated / 'candidate.json'
    document.write_text(json.dumps({
        'schema_version': 'bms.install.v1',
        'profile': {} if valid else {'api_host_port': True},
        'ingress': {'mode': 'local-only'},
    }))
    env = dict(os.environ)
    env.pop('PYTHONDONTWRITEBYTECODE', None)
    env['PYTHONPYCACHEPREFIX'] = str(isolated / 'fresh-preview-cache')
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    options = ['--json', '--document', str(document)]
    if entry == 'shell':
        command = [str(archived / 'start_ui.sh'), 'configure-preview', *options]
    else:
        command = [sys.executable, '-B', str(archived / 'scripts/manage_desktop_services.py')]
        command += (options + ['configure-preview'] if entry.endswith('option_first')
                    else ['configure-preview', *options])
    before = snapshot(isolated)
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == (0 if valid else 2), result.stderr
    assert not result.stderr
    report = json.loads(result.stdout)
    assert report['valid'] is valid
    assert report['ready'] is report['apply_available'] is False
    assert snapshot(isolated) == before
    assert not (isolated / 'fresh-preview-cache').exists()


def test_lane_selector_preserves_authoritative_derived_analysis(isolated):
    resolved = resolve_runtime_paths(isolated / 'source', {
        'data_root': str(isolated / 'prod'), 'dev_data_root': str(isolated / 'dev'),
    }, environ={})
    assert resolved['dev_analysis_cache_dir'] == str(isolated / 'dev/analysis_cache')
    assert managed_runtime_storage_paths(resolved, 'dev')['dev_analysis_cache_dir'] == resolved['dev_analysis_cache_dir']
    # Selection must consume resolution, not independently recompute the path.
    resolved['dev_analysis_cache_dir'] = str(isolated / 'authoritative-derived-cache')
    assert managed_runtime_storage_paths(resolved, 'dev')['dev_analysis_cache_dir'] == resolved['dev_analysis_cache_dir']
