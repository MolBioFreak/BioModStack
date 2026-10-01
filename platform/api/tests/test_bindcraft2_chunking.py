"""Approved no-chunking defaults apply to new campaigns, not stored/native replay."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from services import bindcraft2_launch as launch
from services.bindcraft2_native import _canonical
from services.bindcraft2_typed import (
    compile_typed, normalize_new_campaign_request, schema, validate_request,
)


def _compiler(request, campaign, *, resume=False):
    return compile_typed(request, campaign, lambda value: {
        **value, 'subbatch_size': value.get('subbatch_size', 'auto'),
    }, resume=resume)


def _sources(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, 'get_results_dir', lambda: tmp_path)
    monkeypatch.setattr(launch, 'get_allowed_roots', lambda: {'results': tmp_path})
    target = tmp_path / 'target.fasta'
    target.write_text('>synthetic fixture\n' + 'A' * 60 + '\n')
    return {'max_trajectories': 1, 'targets': [
        {'name': 'fixture', 'target_path': str(target)},
    ]}


def test_schema_distinguishes_native_auto_and_bms_no_chunking():
    data = schema()
    field = data['fields']['subbatch_size']
    assert field['observed_types'] == ['string', 'integer', 'null']
    assert field['native_default'] == 'auto'
    assert field['recommended_default'] is None
    assert data['recommended_defaults'] == {'subbatch_size': None}
    assert field['control'] == 'subbatch-size'
    assert 'VRAM' in field['recommended_default_reason']


@pytest.mark.parametrize('value', [None, 'auto', 1, 4, 16, 128])
def test_explicit_modes_roundtrip_without_rewriting(value, tmp_path):
    request = {'max_trajectories': 2, 'subbatch_size': value}
    original = copy.deepcopy(request)
    normalized = normalize_new_campaign_request(request)
    assert normalized == original
    compiled = _compiler(json.loads(json.dumps(normalized)), tmp_path / 'campaign')
    assert compiled['requested_settings'] == original
    assert compiled['native_request']['subbatch_size'] == value
    assert compiled['effective_settings']['subbatch_size'] == value
    assert compiled['request_sha256'] == hashlib.sha256(_canonical(original)).hexdigest()
    assert request == original


@pytest.mark.parametrize('value', [True, False, 0, -1, 2.5, '4', 'off', 'none', '', [], {}])
def test_rejects_only_unsupported_chunk_representations(value):
    with pytest.raises(ValueError, match='subbatch_size'):
        validate_request({'max_trajectories': 1, 'subbatch_size': value})


def test_new_default_is_explicit_and_does_not_mutate_saved_input():
    request = {'max_trajectories': 2, 'filters': {'Binder_RMSD': {'threshold': None}}}
    original = copy.deepcopy(request)
    normalized = normalize_new_campaign_request(request)
    assert normalized == {**original, 'subbatch_size': None}
    normalized['filters']['Binder_RMSD']['threshold'] = 0
    assert request == original
    assert validate_request(request) is request


@pytest.mark.parametrize('overrides,expected', [({}, None), ({'subbatch_size': None}, None),
                                              ({'subbatch_size': 'auto'}, 'auto'),
                                              ({'subbatch_size': 16}, 16)])
def test_preview_materialization_and_receipt_agree(overrides, expected, tmp_path, monkeypatch):
    request = {**_sources(tmp_path, monkeypatch), **overrides}
    original = copy.deepcopy(request)
    preview = launch.preview_campaign(request, compiler=_compiler)
    normalized = normalize_new_campaign_request(request)
    assert preview['requested_settings'] == normalized
    assert preview['effective_settings']['subbatch_size'] == expected
    assert launch.preview_campaign(normalized, compiler=_compiler)['preview_digest'] == preview['preview_digest']
    handoff = launch.materialize_campaign(request, tmp_path / 'job',
                                          preview_digest=preview['preview_digest'], compiler=_compiler)
    receipt = launch.read_campaign_receipt(tmp_path / 'job')
    assert handoff['bindcraft2_settings'] == normalized
    assert receipt['requested_settings'] == normalized
    assert receipt['effective_settings']['subbatch_size'] == expected
    assert request == original


def test_historical_omission_and_native_resume_keep_auto(tmp_path, monkeypatch):
    request = _sources(tmp_path, monkeypatch)
    destination = tmp_path / 'old/bindcraft2'
    # A pre-change receipt is created through the unchanged native compiler,
    # not the new-campaign normalizer. No stored request is rewritten.
    legacy = launch._compile(request, destination, copy=True, compiler=_compiler)['compiled']
    path = destination / 'compilation.json'
    path.write_bytes(_canonical(legacy) + b'\n')
    campaign = destination / 'campaign'
    campaign.mkdir()
    (campaign / '.campaign_state.json').write_text('{"trajectories":1}')
    before = path.read_bytes()
    receipt = launch.read_campaign_receipt(tmp_path / 'old')
    assert 'subbatch_size' not in receipt['requested_settings']
    assert receipt['effective_settings']['subbatch_size'] == 'auto'
    child = launch.materialize_native_action(tmp_path / 'old', tmp_path / 'resume',
        operation='resume', source_job_id='old', compiler=_compiler)
    assert 'subbatch_size' not in child['bindcraft2_settings']
    assert child['bc2_effective_settings']['subbatch_size'] == 'auto'
    assert path.read_bytes() == before


def test_installed_native_chunking_if_available(tmp_path):
    image = os.environ.get('BMS_TEST_BC2_IMAGE')
    if not image:
        pytest.skip('set BMS_TEST_BC2_IMAGE for installed native CPU transport proof')
    root = Path(__file__).parents[3]
    code = r'''import inspect,json,runpy
from pathlib import Path
runpy.run_path('/opt/bms/scripts/run_bindcraft2_campaign.py',run_name='bc2_module')
from services.bindcraft2_typed import compile_typed,normalize_new_campaign_request
from bindcraft.settings import load_settings
from bindcraft.af2 import resolve_subbatch_size
from bindcraft import design_workers
from bindcraft.af.alphafold.model import mapping
assert inspect.getsourcefile(mapping.sharded_apply).endswith('/bindcraft/af/alphafold/model/mapping.py')
# Simulated free-card memory qualifies policy semantics, not real VRAM usage.
design_workers.design_gpu_memory_gb=lambda: {'0': (40.0,40.0)}
rows=[]
for label,overrides,expected,af2_expected in [('new-default',{},None,None),
    ('off',{'subbatch_size':None},None,None),('auto',{'subbatch_size':'auto'},'auto',4),
    ('custom',{'subbatch_size':16},16,16)]:
    request=normalize_new_campaign_request({'max_trajectories':1,**overrides})
    compiled=compile_typed(request,Path('/work')/label,load_settings)
    assert request['subbatch_size']==expected
    assert compiled['native_request']['subbatch_size']==expected
    assert compiled['effective_settings']['subbatch_size']==expected
    policy=design_workers.campaign_subbatch_size(compiled['effective_settings'],800)
    actual=resolve_subbatch_size(737,policy)
    assert actual==af2_expected
    rows.append({'mode':label,'requested':expected,'native_af2_subbatch':actual})
legacy=compile_typed({'max_trajectories':1},Path('/work/legacy'),load_settings)
assert 'subbatch_size' not in legacy['requested_settings']
assert legacy['effective_settings']['subbatch_size']=='auto'
print(json.dumps(rows,allow_nan=False))
'''
    result = subprocess.run(['apptainer', 'exec', '--no-home', '--env', 'JAX_PLATFORMS=cpu',
        '--bind', f'{root}:/opt/bms:ro', '--bind', f'{tmp_path}:/work', image,
        'python3', '-c', code], text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row['native_af2_subbatch'] for row in json.loads(result.stdout)] == [None, None, 4, 16]
