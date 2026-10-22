"""Catalog and inert native-command seed transport; no inference."""
import os
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from boltz_cli_cpu_probe import render_command


def test_seed_catalog_keeps_optional_native_default_and_both_modes():
    catalog = yaml.safe_load((ROOT / 'platform/api/config/models/boltz2.yaml').read_text())
    seed = next(p for p in catalog['params'] if p['name'] == 'boltz_seed')
    assert seed['type'] == 'integer'
    assert seed['required'] is False
    assert seed['default'] is None
    assert seed['minimum'] == 0
    assert all('boltz_seed' in mode['params'] for mode in catalog['modes'])


@pytest.mark.parametrize('seed', [None, 0, 17])
def test_seed_uses_real_groovy_process_interpolation(seed):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('existing offline Nextflow JAR required for native Groovy interpolation')
    params = {'code_root': '/source', 'boltz_seed': seed,
              'boltz_recycling_steps': 0, 'boltz_sampling_steps': 7,
              'boltz_diffusion_samples': 3, 'boltz_max_parallel_samples': 2,
              'boltz_step_scale': 1.2}
    source = (ROOT / 'modules/structure_prediction.nf').read_text()
    owners = {owner: source.split('process ' + owner + ' {', 1)[1].split('\n}', 1)[0]
              for owner in ('BoltzFromSequenceTask', 'BoltzFromSequenceWithMSATask', 'BoltzFromComplex')}
    owners['RunBoltz'] = (ROOT / 'modules/boltz.nf').read_text()
    for owner, body in owners.items():
        argv = render_command(body, params, jar=jar)
        if seed is None:
            assert '--seed' not in argv, owner
        else:
            assert argv[argv.index('--seed') + 1] == str(seed), owner
