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
         'settings': {'seed': 0, 'global_prefix': '', 'generation': {'args': {'nsteps': 37}, 'n_recycle_schedule': None}}}
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
    assert effective['global_prefix'] == ''
    assert effective['generation']['args']['inverse_folding'] == (generation == 'inverse_folding')
    assert effective['out_dir'] is None


def test_installed_metric_tuple_defaults_and_explicit_symmetry():
    image = os.environ.get('BMS_TEST_RFD4_IMAGE')
    if not image:
        pytest.skip('Selected RFD4 image required')
    r = {'input': {'kind':'unconditional','name':'metric','chains':[{'chain_id':'A','min_length':40,'max_length':40}]},
         'metric_extensions': [
             {'name':'rmsd_exact','section':'inline_metrics','implementation':'rfproteina.metrics.gt_structure_comparison.StructureComparisonMetric','kwargs':{'resolve_symmetry':[]}},
             {'name':'lddt_atom','section':'inline_metrics','implementation':'rfproteina.metrics.lddt_metric.AllAtomLDDT','kwargs':{'resolve_symmetry':['atom']}}]}
    done = subprocess.run(['apptainer','exec','--cleanenv','--bind',f'{ROOT}:{ROOT}:ro',image,'python',str(ROOT/'scripts/preview_rfd4_settings.py')], input=json.dumps(r), text=True, capture_output=True)
    assert done.returncode == 0, done.stderr
    metrics = json.loads(done.stdout)['effective_config']['metrics']['inline_metrics']
    assert metrics['rmsd_exact']['resolve_symmetry'] == []
    assert metrics['lddt_atom']['resolve_symmetry'] == ['atom']
