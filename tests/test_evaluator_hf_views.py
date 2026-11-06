"""Real immutable HF snapshot receiving; wrapper lifecycle only, no inference."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))


@pytest.fixture
def source():
    value = os.environ.get('BMS_TEST_HF_CACHE')
    assert value, 'Run inside a native image with BMS_TEST_HF_CACHE bound to acquired immutable snapshots'
    return Path(value)


def verify_cache(view, source, repositories):
    from huggingface_hub import hf_hub_download
    from transformers import AutoConfig, AutoTokenizer
    entries = json.loads((ROOT / 'scripts/bms_complexa/assets.json').read_text())['selected_oracle_sources']['native_hf']['hf']
    for entry in entries:
        if entry['repo'] not in repositories:
            continue
        folder = 'models--' + entry['repo'].replace('/', '--')
        assert (view / folder / 'refs/main').read_text() == entry['revision']
        config = AutoConfig.from_pretrained(entry['repo'], cache_dir=str(view), revision='main', local_files_only=True)
        tokenizer = AutoTokenizer.from_pretrained(entry['repo'], cache_dir=str(view), revision='main', local_files_only=True)
        assert config.model_type == 'esm' and len(tokenizer) > 0
        for item in entry['files']:
            resolved = Path(hf_hub_download(entry['repo'], item['name'], cache_dir=str(view), revision='main', local_files_only=True))
            original = source / folder / 'snapshots' / entry['revision'] / item['name']
            assert resolved.samefile(original), item['name']
            assert resolved.stat().st_size == item['size_bytes']


def test_native_refs_and_repreparation(tmp_path, source):
    from scripts.bms_complexa.hf_cache import prepare_hf_view
    repos = ['facebook/esmfold_v1', 'facebook/esm2_t33_650M_UR50D']
    before = {p: p.read_bytes() for p in source.glob('*/refs/*') if p.is_file()}
    view = tmp_path / 'view'
    assert prepare_hf_view(view, str(source), repos) == view
    verify_cache(view, source, repos)
    prepare_hf_view(view, str(source), repos[1:])
    assert not (view / 'models--facebook--esmfold_v1').exists()
    verify_cache(view, source, repos[1:])
    assert {p: p.read_bytes() for p in source.glob('*/refs/*') if p.is_file()} == before
    assert prepare_hf_view(view, str(source), []) is None
    assert not view.exists()


def test_missing_optional_binding_is_not_a_gate(tmp_path):
    from scripts.bms_complexa.hf_cache import prepare_hf_view
    assert prepare_hf_view(tmp_path/'none', None, ['facebook/esmfold_v1']) is None
    # No source-stat validation/refusal is introduced. Native loading owns absence.
    view = prepare_hf_view(tmp_path/'missing', str(tmp_path/'absent'), ['facebook/esmfold_v1'])
    assert (view/'models--facebook--esmfold_v1/refs/main').is_file()
    assert next((view/'models--facebook--esmfold_v1/snapshots').iterdir()).is_symlink()


@pytest.mark.parametrize('returncode', [0, 1])
def test_complexa_hf_environment_and_cleanup(tmp_path, monkeypatch, source, returncode):
    from omegaconf import OmegaConf
    from scripts.lib.complexa_contract import Request
    runner = importlib.import_module('scripts.bms_complexa.run')
    monkeypatch.setenv('CACHE_DIR', str(source))
    monkeypatch.setattr(runner, 'compose_request', lambda *a, **k: OmegaConf.create({}))
    monkeypatch.setattr(runner, 'selected_dependencies', lambda *a: ['esmfold', 'esm'])
    output = tmp_path/'result'
    private = output/'native/.cache/hf-evaluators'
    calls=[]
    def execute(command, **kwargs):
        env=kwargs['env']; calls.append(env)
        assert Path(env['CACHE_DIR']) == private
        assert env['HF_HUB_CACHE'] == env['TRANSFORMERS_CACHE'] == str(private)
        verify_cache(private, source, ['facebook/esmfold_v1','facebook/esm2_t33_650M_UR50D'])
        return subprocess.CompletedProcess(command, returncode)
    def publish(*args):
        assert not private.exists()
        assert not (output/'native/community_models').exists()
    monkeypatch.setattr(runner.subprocess, 'run', execute)
    monkeypatch.setattr(runner, 'publish', publish)
    args=dict(upstream=Path('/opt/complexa'),weights=tmp_path,output=output)
    request=Request(task='protein_binder',task_name='hf-receiving',operation='evaluate')
    if returncode:
        with pytest.raises(subprocess.CalledProcessError): runner.run(request, **args)
    else:
        assert runner.run(request, **args)['execution']=='completed'
    assert len(calls)==1 and os.environ['CACHE_DIR']==str(source)


@pytest.mark.parametrize('fail', [False, True])
def test_la_hf_environment_restore_and_cleanup(tmp_path, monkeypatch, source, fail):
    from omegaconf import OmegaConf
    import pandas as pd
    import proteinfoundation
    runner=importlib.import_module('run_laproteina_inference')
    monkeypatch.setenv('CACHE_DIR',str(source))
    monkeypatch.setenv('HF_HUB_CACHE','unchanged-hub')
    monkeypatch.delenv('TRANSFORMERS_CACHE',raising=False)
    samples=tmp_path/'samples';samples.mkdir()
    config=tmp_path/'original.yaml'
    OmegaConf.save(OmegaConf.create({'generation': {'dataset': {}, 'metric': {
        'compute_designability':False,'compute_codesignability':True,
        'compute_co_sequence_recovery':False,'codesignability_folding_models':['esmfold']}}}),config)
    observed=[]
    def compute(*args):
        view=Path(os.environ['CACHE_DIR']);observed.append(view)
        assert view != source and view.name=='hf-evaluators'
        verify_cache(view,source,['facebook/esmfold_v1'])
        assert not (view/'models--facebook--esm2_t33_650M_UR50D').exists()
        if fail: raise RuntimeError('bounded native failure')
        return pd.DataFrame()
    native=types.ModuleType('proteinfoundation.evaluate')
    native.split_by_job=lambda *a,**k: []
    native.compute_traditional_metrics=compute
    monkeypatch.setattr(proteinfoundation,'evaluate',native,raising=False)
    monkeypatch.setitem(sys.modules,'proteinfoundation.evaluate',native)
    def publish(*args):
        assert observed and not observed[0].exists()
        assert os.environ['CACHE_DIR']==str(source)
        assert os.environ['HF_HUB_CACHE']=='unchanged-hub'
        assert 'TRANSFORMERS_CACHE' not in os.environ
    monkeypatch.setattr(runner,'publish_evaluation',publish)
    request={'source_directory':str(samples),'config_path':str(config),'job_id':0,'ncpus':1}
    if fail:
        with pytest.raises(RuntimeError,match='bounded native failure'):
            runner.evaluate_native(request,tmp_path/'result')
    else:
        assert runner.evaluate_native(request,tmp_path/'result')['execution_status']=='completed'
