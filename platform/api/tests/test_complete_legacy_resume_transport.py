"""Admission/saved/resume -> actual compiler -> real CAD preparer, no inference."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.lib.protein_cad_settings import bind_new_cad_settings, replay_cad_settings, settings_projection
from services.nextflow import build_nextflow_command

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('backend,field,initial,changed,native', [
    ('disco', 'effort', 'fast', 'max', 'fast'),
    ('laproteina', 'num_steps', 400, 17, 23),
])
@pytest.mark.parametrize('native_key', [None, 'settings', 'pcad_settings_json'])
def test_admission_saved_resume_compiler_and_preparer(tmp_path, backend, field, initial, changed, native, native_key):
    flat = f'{backend}_{field}'
    params = {'backend': backend, 'design_task': 'unconditional', 'num_designs': 1,
              'target_lengths': '11', flat: initial,
              'laproteina_seed': 0, 'disco_use_deepspeed_evo_attention': False}
    saved = json.loads(json.dumps(bind_new_cad_settings('protein_cad_experimental', 'design', params)))
    before = deepcopy(saved)
    overrides = {flat: changed}
    if native_key:
        key = f'{backend}_settings' if native_key == 'settings' else f'pcad_{backend}_settings_json'
        overrides[key] = {field: native}
    overrides_before = deepcopy(overrides)
    resumed = replay_cad_settings('protein_cad_experimental', 'design', saved,
                                 {**saved, **overrides}, overrides=overrides)
    expected = native if native_key else changed
    key = f'pcad_{backend}_settings_json'
    assert settings_projection(resumed)[key][field] == expected
    # Real integrated compiler, not a reimplementation of its projection.
    cmd = build_nextflow_command('protein_cad_experimental', 'design', resumed,
                                str(tmp_path/'results'), job_id='resume-fixture')
    transported = json.loads(cmd[cmd.index('--'+key)+1])
    assert transported[field] == expected
    out = tmp_path/'prepared.json'
    subprocess.run([sys.executable, str(REPO/'scripts/prep_protein_cad_request.py'),
                    '--backend', backend, '--task', 'unconditional', '--num-designs', '1',
                    '--target-lengths', '11', '--output', str(out), '--input-dir', str(tmp_path/'inputs'),
                    '--'+backend+'-settings-json', json.dumps(transported)], check=True)
    prepared = json.loads(out.read_text())
    assert prepared[backend][field] == expected
    assert saved == before and overrides == overrides_before
    # No overrides: exact saved snapshot remains intact on ordinary replay.
    assert replay_cad_settings('protein_cad_experimental', 'design', saved, saved) == saved


@pytest.mark.parametrize('model,mode', [('protein_cad_experimental','design'),
                                      ('protein_modification_experimental','de_novo_design')])
def test_resume_explicit_aliases_and_history_remain_separate(model, mode):
    old = {'backend': 'disco', 'generator': 'disco', 'design_task': 'dna_conditioned',
           'disco_effort': 'fast', 'disco_seeds': '0,10', 'disco_use_deepspeed_evo_attention': False}
    assert replay_cad_settings(model, mode, old, old)['pcad_disco_settings_json']['effort'] == 'max'
    # An unrelated resume control must retain old effective conditional effort.
    delta = {'disco_seeds': '0'}
    replay = replay_cad_settings(model, mode, old, {**old, **delta}, overrides=delta)
    assert replay['pcad_disco_settings_json']['effort'] == 'max'
    assert replay['pcad_disco_settings_json']['seeds'] == [0]
    fresh = bind_new_cad_settings(model, mode, old)
    delta = {'pcad_disco_effort': 'max', 'disco_seeds': 0}
    result = replay_cad_settings(model, mode, fresh, {**fresh, **delta}, overrides=delta)
    assert settings_projection(result)['pcad_disco_settings_json']['effort'] == 'max'
    assert settings_projection(result)['pcad_disco_settings_json']['seeds'] == 0
    assert result['pcad_disco_settings_json']['use_deepspeed_evo_attention'] is False
    la = bind_new_cad_settings(model, mode, {'backend':'laproteina','generator':'laproteina',
                                          'laproteina_num_steps':400, 'laproteina_seed':0})
    delta = {'pcad_laproteina_num_steps':17, 'laproteina_self_cond':False,
             'laproteina_bb_ca_gt_clamp_val':None}
    result = replay_cad_settings(model, mode, la, {**la, **delta}, overrides=delta)
    projected = settings_projection(result)['pcad_laproteina_settings_json']
    assert projected['num_steps'] == 17 and projected['self_cond'] is False
    assert projected['bb_ca_gt_clamp_val'] is None and projected['seed'] == 0
