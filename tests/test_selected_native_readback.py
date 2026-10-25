"""Selected runtime CPU composition and literal field transport (no inference)."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]


def test_rfd3_sparse_skip_and_native_symmetry():
    from scripts.lib.rfd3_scientific_settings import execution_settings, native_overrides, conditioning_settings, scientific_inventory
    assert 'skip_existing' not in execution_settings({})
    assert 'skip_existing=false' in native_overrides({'skip_existing': False})
    assert 'skip_existing=true' in native_overrides({'skip_existing': True})
    s = {'id': 'C3', 'is_symmetric_motif': False, 'is_unsym_motif': 'HEM', 'native_extra': 0}
    assert conditioning_settings({'symmetry': s}) == {'symmetry': s}
    assert set(scientific_inventory()['conditioning']['symmetry']['properties']) == {'id', 'is_unsym_motif', 'is_symmetric_motif'}


def test_disco_native_enums_preserve_null_and_zero():
    from typing import get_type_hints, get_args
    from scripts.lib.disco_contract import NoisyGuidance, SequenceSampling, native_overrides
    assert get_args(get_type_hints(NoisyGuidance)['omega_schedule']) == ('constant', 'linear_decay')
    planners = get_args(get_args(get_type_hints(SequenceSampling)['planner_name'])[0])
    assert len(planners) == 12 and 'evoflow_650M_Base' in planners
    assert '++sequence_sampling_strategy.planner_name=null' in native_overrides({'sequence_sampling': {'planner_name': None}})
    assert '++sample_diffusion.noisy_guidance.omega_struct=0' in native_overrides({'noisy_guidance': {'omega_struct': 0}})


@pytest.mark.parametrize('generation,recycle', [('inference_base','thinking_medium'), ('inverse_folding','thinking_low'), ('inference_base','high/front_loaded')])
def test_installed_selected_profiles(generation, recycle):
    image = os.environ.get('BMS_TEST_RFD4_IMAGE')
    if not image:
        pytest.skip('Selected installed image required')
    r = {'input': {'kind': 'unconditional', 'name': 'preview', 'chains': [{'chain_id': 'A', 'min_length': 40, 'max_length': 40}]},
         'groups': {'generation': generation, 'recycle_strategy': recycle},
         'settings': {'seed': 0, 'generation': {'args': {'nsteps': 37}, 'n_recycle_schedule': None}}}
    original = copy.deepcopy(r)
    done = subprocess.run(['apptainer','exec','--cleanenv','--bind', f'{ROOT}:{ROOT}:ro', image,
                           'python',str(ROOT/'scripts/preview_rfd4_settings.py')], input=json.dumps(r), text=True, capture_output=True)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert r == original
    assert result['request']['settings'] == r['settings']
    effective = result['effective_config']
    assert effective['generation']['args']['nsteps'] == 37
    assert effective['generation']['n_recycle_schedule'] is None
    assert effective['seed'] == 0
    assert effective['generation']['args']['inverse_folding'] == (generation == 'inverse_folding')
    assert effective['out_dir'] is None
