"""Bounded source/config/adapter tests. No GPU or invented scientific output."""
import copy
import json
import os
from pathlib import Path
import sys
import types
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'platform/api')]
from scripts.lib.rfd4_proteina_contract import normalize_request, parameter_schema, native_overlay, request_identity
from bms_rfd4.run import compose_native
from bms_rfd4.publication import NativeObserver, publish
import importlib.util
_spec=importlib.util.spec_from_file_location('rfd4_reader',ROOT/'platform/api/services/rfd4_proteina_results.py')
_reader=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reader)
page_records=_reader.page_records


def request():
    return {'input':{'kind':'unconditional','name':'sample','chains':[{'chain_id':'A','min_length':100,'max_length':100}]}}


def test_explicit_values_and_closed_contract():
    r=request(); r['settings']={'seed':0,'compile_lengths':None,'compress_outputs':False,
        'generation':{'args':{'save_trajectory_every':0,'partial_t_backbone':None,'guidance_w':0.0}}}
    n=normalize_request(r)
    assert n['settings']==r['settings']
    assert native_overlay(n)==r['settings']
    r['settings']['symmetry']='C3'
    with pytest.raises(ValueError):normalize_request(r)


def test_condition_order_is_identity():
    a=request();a['settings']={'seed':1,'batch_size':2}
    b=request();b['settings']={'batch_size':2,'seed':1}
    assert request_identity(a)!=request_identity(b)


def native_source():
    p=os.environ.get('BMS_TEST_RFD4_SOURCE')
    if not p:pytest.skip('Set BMS_TEST_RFD4_SOURCE to pinned native source for Hydra composition')
    return Path(p)


def test_official_hydra_default_and_explicit_precedence(tmp_path):
    source=native_source()
    cfg=compose_native(request(),source/'configs',inputs=tmp_path,output=tmp_path/'out',bundle=tmp_path/'pair')
    assert cfg.inference_backend=='native'
    assert cfg.precision=='bf16-mixed'
    assert cfg.generation.args.nsteps==60
    assert list(cfg.compile_lengths)==[128,160,192,224,256,288,320,352,384,448,512,640,768,896,1024]
    assert cfg.generation.n_recycle_schedule is not None
    r=request();r['settings']={'compile_lengths':None,'generation':{'args':{'nsteps':37,'partial_t_backbone':0.2}}}
    cfg=compose_native(r,source/'configs',inputs=tmp_path,output=tmp_path/'out',bundle=tmp_path/'pair')
    assert cfg.generation.args.nsteps==37 and cfg.generation.args.partial_t_backbone==0.2
    assert cfg.compile_lengths is None


def test_inverse_evaluate_search_and_guidance_compose(tmp_path):
    source=native_source()
    r=request();r['groups']={'generation':'inverse_folding'}
    r['settings']={'search':{'checkpoints':{'60':{'metrics':[],'rewards':{}}},'beam_width':2,'save_rollouts':False},'seed':0}
    cfg=compose_native(r,source/'configs',inputs=tmp_path,output=tmp_path/'out',bundle=tmp_path/'pair')
    assert cfg.generation.args.inverse_folding is True
    assert cfg.search.beam_width==2 and cfg.seed==0
    r={'mode':'evaluate','input':{'kind':'annotated','slots':['native']},'settings':{'metrics_tags':[]}}
    cfg=compose_native(r,source/'configs',inputs=tmp_path,output=tmp_path/'out')
    assert cfg.delete_failing_pre_oracle is False and list(cfg.metrics_tags)==[]
    r=request();r['guidance']={'metrics':['contract_ca'],'terms':{'contract_ca.sq_norm':{'bb_ca':{'kind':'window','w':0,'t_start':0,'t_end':0.1}}}}
    cfg=compose_native(r,source/'configs',inputs=tmp_path,output=tmp_path/'out',bundle=tmp_path/'pair')
    assert cfg.guidance.terms['contract_ca.sq_norm'].bb_ca._target_=='rfproteina.guidance.schedules.window'


def test_native_provider_constructor_and_trusted_extensions(tmp_path):
    source=native_source()
    r=request()
    r['providers']={'refolding_oracles':{'profile':'af2','overrides':{'oracle':{'seed':0,'initial_guess':False,'pct_seq_mask':0.0,
        'pre_oracle_transform':{'kind':'off_target','off_target_slots':{'other':'off'}}}}}}
    r['metric_extensions']=[{'name':'novelty','section':'inline_metrics','implementation':'rfproteina.metrics.foldseek_novelty.FoldseekNoveltyMetric',
        'kwargs':{'tm_threshold':0.5,'min_coverage':0.8,'threads':1,'timeout':300.0},'archive_slot':'archive'}]
    cfg=compose_native(r,source/'configs',inputs=tmp_path,output=tmp_path/'out',bundle=tmp_path/'pair',bindings={'off':'/selected/off.cif','archive':'/selected/archive'})
    assert cfg.metrics.refolding_oracles.oracle.seed==0
    assert cfg.metrics.refolding_oracles.oracle.pre_oracle_transform.off_target_paths.other=='/selected/off.cif'
    assert cfg.metrics.inline_metrics.novelty.archive_dir=='/selected/archive'
    clear=request();clear['providers']={'refolding_oracles':{'profile':'af3','overrides':{'oracle_ppi':{'template_query':None,'frac_under_thresholds':{}}}}}
    cleared=compose_native(clear,source/'configs',inputs=tmp_path,output=tmp_path/'out',bundle=tmp_path/'pair')
    assert cleared.metrics.refolding_oracles.oracle_ppi.template_query is None
    assert dict(cleared.metrics.refolding_oracles.oracle_ppi.frac_under_thresholds)=={}
    r['metric_extensions'][0]['implementation']='os.system'
    with pytest.raises(ValueError):normalize_request(r)
    r=request();r['settings']={'inference_backend':'bioir','generation':{'args':{'save_trajectory_every':2}}}
    cfg=compose_native(r,source/'configs',inputs=tmp_path,output=tmp_path/'out',bundle=tmp_path/'pair')
    # Native BioIR will report its exclusion; BMS never drops the trajectory request
    # or silently substitutes another backend to make it run.
    assert cfg.inference_backend=='bioir' and cfg.generation.args.save_trajectory_every==2


def test_exact_final_checkpoint_membership_not_filename(tmp_path):
    observer=NativeObserver()
    final={'example_id':'a_bsc2_1','reward':0.0}; rollout={'example_id':'a_bsc0_0'}
    frame=types.SimpleNamespace(f_code=types.SimpleNamespace(co_filename='/p/rfproteina/search/beam_search.py',co_name='generate'),
                                f_locals={'final_results':[final]})
    observer.observe(frame,'return',[rollout,final])
    assert observer.membership=={'a_bsc0_0':'completed_rollout','a_bsc2_1':'final_beam'}
    r=normalize_request(request())
    result=publish(tmp_path,r,{'execution_status':'completed'},observer)
    assert result['events'][-1]=={'event':'final_population','count':1,'observed':True}
    assert page_records(result)['total']==0  # no writer event != fabricated emitted structure
    assert (tmp_path/'manifest.json').is_file()


def test_metrics_off_failure_receipt_and_partial_reader(tmp_path):
    observer=NativeObserver();p=tmp_path/'native'/'x.cif';p.parent.mkdir();p.write_text('fixture, not scientific coordinates')
    observer.events=[{'event':'native_writer','example_id':'x','path':str(p),'population':'generated'},
                     {'event':'native_writer','example_id':'deleted','path':str(p.parent/'gone.cif'),'population':'completed_rollout'}]
    m=publish(tmp_path,normalize_request(request()),{'execution_status':'failed'},observer)
    assert m['execution_status']=='failed'
    assert [r['availability'] for r in m['records']]==['stored','not_retained']
    assert page_records(m,limit=1)['has_more'] is True
