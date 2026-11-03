"""Audited native seams; CPU composition/CLI and immutable file fixtures only."""
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('drafts', [{}, {'inactive': {'kind': 'file', 'path': 'inputs/not-present'}}])
@pytest.mark.parametrize('parent', [False, True])
def test_source_free_complexa_expires_derived_closure(tmp_path, drafts, parent):
    from services.native_generation_sources import prepare_native_sources
    from services.complexa import bind_for_job
    params = {'complexa_request': {'task': 'protein_binder', 'task_name': 'new', 'seed': 17},
              'native_sources': copy.deepcopy(drafts), 'complexa_request_dir': str(tmp_path/'obsolete')}
    if parent:
        params['generator'] = 'complexa'
    job = SimpleNamespace(model_id='protein_modification_experimental' if parent else 'complexa',
                          mode='de_novo_design' if parent else 'generate', params=params)
    asyncio.run(prepare_native_sources(job, None))
    assert 'complexa_request_dir' not in job.params
    bound, files = bind_for_job('generate', job.params, tmp_path/'new')
    assert bound['complexa_request_dir'] == str(tmp_path/'new/complexa')
    assert bound['native_sources'] == drafts
    assert len(files) == 1
    # Also cover binding directly without the ordinary source preparer.
    rebound, files = bind_for_job('generate', params, tmp_path/'direct')
    assert rebound['complexa_request_dir'] == str(tmp_path/'direct/complexa')
    assert len(files) == 1


def prep(tmp_path, monkeypatch, backend, task, lengths, extra):
    import prep_protein_cad_request as owner
    request = tmp_path/'request.json'
    monkeypatch.setattr(sys, 'argv', ['prep', '--job-id', 'test', '--job-name', 'test',
        '--backend', backend, '--task', task, '--num-designs', '1',
        *(['--target-lengths', lengths] if lengths is not None else []),
        '--output', str(request), '--input-dir', str(tmp_path/'inputs'), *extra])
    owner.main()
    return json.loads(request.read_text())


@pytest.mark.parametrize('lengths', [None, '', 'not a number'])
@pytest.mark.parametrize('alternative', ['explicit', 'file'])
def test_disco_native_jobs_ignore_inactive_lengths(tmp_path, monkeypatch, lengths, alternative):
    source = tmp_path/'selected.json'; source.write_text('[]')
    extra = ['--disco-jobs-json', '[]'] if alternative == 'explicit' else ['--disco-input-json-path', str(source)]
    result = prep(tmp_path, monkeypatch, 'disco', 'custom_json', lengths, extra)
    assert result['target_lengths'] == []
    assert json.loads(Path(result['disco']['input_json_path']).read_text()) == []


def test_disco_shorthand_still_uses_authored_lengths(tmp_path, monkeypatch):
    result = prep(tmp_path, monkeypatch, 'disco', 'unconditional', '40,51', [])
    assert result['target_lengths'] == [40, 51]


def atom(serial, name, residue=10, insertion='A'):
    return f'ATOM  {serial:5d} {name:>4} ALA X{residue:4d}{insertion}   {serial:8.3f}{2.:8.3f}{3.:8.3f}{1.:6.2f}{20.:6.2f}           C  \n'


def test_la_atom_spec_without_contig_reaches_native_config(tmp_path, monkeypatch):
    from run_laproteina_inference import _configure_laproteina
    source = tmp_path/'motif.pdb'; source.write_text(atom(1, 'CA', insertion=' '))
    result = prep(tmp_path, monkeypatch, 'laproteina', 'motif_scaffolding', '40',
        ['--laproteina-preset', 'motif_idx_aa', '--laproteina-settings-json',
         json.dumps({'motif_pdb': str(source), 'motif_atom_spec': 'X10: [CA]'})])
    monkeypatch.setenv('LAPROTEINA_REPO', os.environ['LAPROTEINA_TEST_SOURCE'])
    invocation = _configure_laproteina(result, tmp_path/'native')
    motif = invocation['receipt']['effective_config']['generation']['dataset']['motif_dict_cfg']['BMS_CUSTOM']
    assert motif['motif_atom_spec'] == 'X10: [CA]'
    assert not motif.get('contig_string')
    # The generation config retains its dependency before original task cleanup.
    source.unlink()
    config = Path(invocation['receipt']['config_root'])/'inference_bms.yaml'
    sidecar = json.loads(config.with_name(config.name+'.motifs.json').read_text())
    assert (config.parent/sidecar['BMS_CUSTOM']['path']).read_text() == atom(1, 'CA', insertion=' ')


@pytest.mark.parametrize('task', ['monomer_motif_indexed', 'monomer_motif_unindexed'])
@pytest.mark.parametrize('contig', [None, 'X10'])
def test_complexa_evaluator_receives_native_ids_and_unchanged_mode(tmp_path, task, contig):
    from scripts.lib.complexa_contract import Request
    from scripts.bms_complexa.inputs import prepare_source
    from scripts.bms_complexa.config import compose_request
    source = tmp_path/'source.pdb'; source.write_text(atom(1, 'CA')+atom(2, 'CB'))
    req = Request(task=task, task_name='custom', source={'document_id':'motif',
        'contig_string': contig, 'atom_selection_mode': 'all_atom',
        'motif_atoms': [{'chain':'X', 'residue':10, 'insertion_code':'A', 'atoms':['CA','CB']}]})
    prepared = prepare_source(req.source, source, tmp_path/'source', ame=False)
    cfg = compose_request(req, Path(os.environ['COMPLEXA_TEST_SOURCE']), tmp_path/'cfg', tmp_path/'weights', prepared_source=prepared)
    target = cfg.dataset.motif_dict_cfg.custom
    assert target.contig_string == 'B1'
    assert target.atom_selection_mode == 'all_atom'
    assert cfg.generation.dataloader.dataset.conditional_features[0].motif_atom_spec == 'B1: [CA, CB]'
    assert [(r['native']['chain'], r['native']['residue'], r['native']['atom']) for r in prepared['atom_map']] == [('B',1,'CA'),('B',1,'CB')]


def test_la_numeric_descriptors_preserve_raw_csv(tmp_path):
    from run_laproteina_inference import publish_evaluation
    attempt=tmp_path/'evaluation'; attempt.mkdir()
    text='pdb_path,_res_scRMSD_ca_esmfold,_res_motif_seq_rec,_res_co_seq_rec_all,unknown\n001,0.25,0,"[0, 1]",123\n002,,,[],text\n'
    (attempt/'results.csv').write_text(text)
    publish_evaluation(tmp_path, attempt, {'execution_status':'completed'})
    manifest=json.loads((tmp_path/'manifest.json').read_text())
    record=manifest['records'][0]
    assert record['metrics']['_res_scRMSD_ca_esmfold'] == '0.25'
    assert record['native_record']['pdb_path'] == '001'
    descriptors=record.get('metric_descriptors', {})
    assert descriptors['_res_scRMSD_ca_esmfold']['value_type'] == 'number'
    assert descriptors['_res_motif_seq_rec']['unit'] == 'fraction'
    assert set(descriptors) == {'_res_scRMSD_ca_esmfold','_res_motif_seq_rec'}
    assert manifest['records'][1]['metrics']['_res_motif_seq_rec'] == ''
    assert (attempt/'results.csv').read_text() == text


def test_la_selected_motif_survives_closure_relocation(tmp_path):
    from services.laproteina_evaluation import prepare_for_job
    from run_laproteina_inference import evaluate_native
    from lib.portable_inputs import bind_native_document
    source=tmp_path/'original'; source.mkdir()
    samples=source/'samples'; samples.mkdir()
    (samples/'motif_info.csv').write_text('native,unchanged\n')
    motif=source/'motif.pdb'; motif.write_text(atom(1,'CA'))
    config=source/'native.yaml'
    config.write_text(yaml.safe_dump({'generation':{'dataset':{'motif_task_name':'custom',
        'motif_dict_cfg':{'custom':{'motif_pdb_path':str(motif), 'motif_atom_spec':'X10: [CA]'},
                          'inactive':{'motif_pdb_path':'/missing/inactive.pdb'}}},
        'metric':{'compute_designability':False}}}))
    original=config.read_bytes()
    params={'laproteina_evaluation_request':{'samples_slot':'samples','config_slot':'config','job_id':0,'ncpus':1}}
    result=prepare_for_job('evaluate',params,tmp_path/'prepared',resolved_sources={'samples':samples,'config':config},allowed_roots=[tmp_path],source_identities={})
    closure=Path(result['laproteina_evaluation_input_closure'])
    moved=tmp_path/'worker'; shutil.copytree(closure,moved)
    shutil.rmtree(source); shutil.rmtree(tmp_path/'prepared')
    request=bind_native_document(json.loads((moved/'request.json').read_text()),'laproteina-evaluation',owner=moved/'request.json')
    receipt=evaluate_native(request,tmp_path/'run',prepare_only=True)
    effective=receipt['effective_config']['generation']['dataset']['motif_dict_cfg']
    assert Path(effective['custom']['motif_pdb_path']).read_text() == atom(1,'CA')
    assert effective['inactive']['motif_pdb_path'] == '/missing/inactive.pdb'
    originals=list((tmp_path/'run').rglob('original_config.yaml'))
    assert originals[0].read_bytes() == original
    assert receipt['effective_config']['generation']['metric']['compute_designability'] is False


def test_la_shared_source_snapshot_keeps_motif_generations_distinct(tmp_path, monkeypatch):
    import paths
    from routers import files
    from services.native_generation_sources import prepare_native_sources
    def motif_references(config):
        bindings=json.loads(config.with_name(config.name+'.motifs.json').read_text())
        return {task:config.parent / binding['path'] for task,binding in bindings.items()}
    roots={'inputs':tmp_path/'inputs'}; roots['inputs'].mkdir()
    monkeypatch.setattr(paths,'get_allowed_roots',lambda: roots)
    monkeypatch.setattr(files,'get_allowed_roots',lambda: roots)
    async def no_governed_roots(session): return []
    monkeypatch.setattr(files,'get_governed_ngs_result_roots',no_governed_roots)
    samples=roots['inputs']/'samples'; samples.mkdir()
    motif=roots['inputs']/'motif.pdb'; motif.write_text('first reference')
    config=roots['inputs']/'native.yaml'
    config.write_text(yaml.safe_dump({'generation':{'dataset':{'motif_task_name':'custom',
        'motif_dict_cfg':{'custom':{'motif_pdb_path':str(motif)}}}}}))
    params={'laproteina_evaluation_request':{'samples_slot':'samples','config_slot':'config','job_id':0,'ncpus':1},
        'native_sources':{'samples':{'kind':'file','path':'inputs/samples'},'config':{'kind':'file','path':'inputs/native.yaml'}}}
    first=SimpleNamespace(model_id='laproteina',mode='evaluate',params=copy.deepcopy(params))
    asyncio.run(prepare_native_sources(first,None))
    motif.write_text('second reference')
    second=SimpleNamespace(model_id='laproteina',mode='evaluate',params=copy.deepcopy(params))
    asyncio.run(prepare_native_sources(second,None))
    assert first.params['native_sources']['config']['path'] != second.params['native_sources']['config']['path']
    first_config=paths.resolve_allowed_path(first.params['native_sources']['config']['path'])
    assert motif_references(first_config)['custom'].read_text() == 'first reference'
    previous=copy.deepcopy(first.params)
    config.unlink(); motif.unlink(); samples.rmdir()
    asyncio.run(prepare_native_sources(first,None))
    assert first.params == previous
    assert motif_references(first_config)['custom'].read_text() == 'first reference'


@pytest.mark.parametrize('native_exit', [0, 7])
def test_complexa_real_nextflow_collects_failure_without_success(tmp_path, native_exit):
    jar=os.environ['BMS_TEST_NEXTFLOW_JAR']
    closure=tmp_path/'closure'; closure.mkdir(); (closure/'request.json').write_text('{}')
    code=tmp_path/'code/scripts'; code.mkdir(parents=True)
    (code/'run_complexa_inference.py').write_text("import pathlib,sys\np=pathlib.Path('results');p.mkdir()\n(p/'manifest.json').write_text('partial fixture')\nsys.exit("+str(native_exit)+")\n")
    driver=tmp_path/'driver.nf'
    driver.write_text(f"include {{ RunComplexa }} from '{ROOT / 'modules/complexa.nf'}'\nworkflow {{ RunComplexa(file(params.closure)) }}\n")
    config=tmp_path/'offline.config'; config.write_text("process.executor='local'\nprocess.cpus=1\nprocess.memory='256 MB'\nexecutor.queueSize=1\n")
    params=tmp_path/'params.json'; params.write_text(json.dumps({'closure':str(closure),'code_root':str(code.parent),'out_dir':str(tmp_path/'published'),'complexa_weights_dir':'unused','complexa_autoguidance_checkpoint':None}))
    env={k:v for k,v in os.environ.items() if not k.startswith('NXF_')}
    env.update(NXF_OFFLINE='true',NXF_DISABLE_CHECK_LATEST='true',NXF_HOME=str(tmp_path/'nxf'),TMPDIR=str(tmp_path))
    command=['java', *['--add-opens=java.base/'+n+'=ALL-UNNAMED' for n in ('java.lang','java.util','java.nio')],'-Djava.io.tmpdir='+str(tmp_path),'-jar',jar,'-C',str(config),'run',str(driver),'-params-file',str(params),'-work-dir',str(tmp_path/'work'),'-ansi-log','false']
    run=subprocess.run(command,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=120)
    (tmp_path/'native-nextflow.log').write_text(run.stdout+run.stderr)
    assert bool(run.returncode) == bool(native_exit), run.stdout+run.stderr
    assert (tmp_path/'published/collected/complexa/manifest.json').read_text() == 'partial fixture'
    exit_files=list((tmp_path/'work').rglob('.exitcode'))
    assert len(exit_files) == 1 and int(exit_files[0].read_text()) == native_exit
