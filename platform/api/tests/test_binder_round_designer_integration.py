"""Real admission/compiler/native-shell/ingestion, inert science only."""
from copy import deepcopy
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from database import Design, Job
from routers import jobs
from schemas import JobCreate
from services.nextflow import compile_job_nextflow_invocation
from services.result_ingester import ingest_job_results
from test_core_protein_scientific_admission import admission
from test_generic_sequence_launch_closeout import request, compile_request
from test_binder_continuation import selected
from test_project_workflow_setups import setup_store

ROOT = Path(__file__).resolve().parents[3]


def helper():
    spec = importlib.util.spec_from_file_location('binder_native_fixture', ROOT / 'tests/test_generic_sequence_native_transport.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def role_request(tmp_path, model='proteinmpnn', order='Z,T'):
    fixture = helper()
    typed = request(model, 'design' if model == 'proteinmpnn' else 'binder_design', tmp_path)
    Path(typed.params['input_pdb']).write_text(''.join(fixture.atom(i, c, n) for i, c, n in
        [(1, 'B', 91), (2, 'Z', 10), (3, 'Z', 30), (4, 'A', 10), (5, 'T', 7), (6, 'T', 18)]))
    typed.params.update(seqs_per_design=2, fixed_positions='Z:30')
    if model == 'proteinmpnn':
        typed.params.update(binder_chains=order, target_chains='B,A', mpnn_max_score=1.0)
    else:
        typed.params.update(design_chain=order, target_chain='B,A', fampnn_max_psce=1.0)
    return typed


def test_designer_process_descriptors_match_real_sections():
    from test_remote_workflow_closure import test_refinement_publication_descriptors_match_actual_process_sections
    for source in ('modules/caliby.nf', 'modules/proteinmpnn.nf'):
        test_refinement_publication_descriptors_match_actual_process_sections(source)


@pytest.mark.parametrize('model', ['proteinmpnn', 'fampnn'])
def test_roles_count_and_fixed_positions_survive_actual_normalize_compile_replay(model, tmp_path):
    typed = role_request(tmp_path, model)
    normalized, invocation = compile_request(typed, tmp_path / 'output')
    clone = jobs.normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert clone.model_dump() == normalized.model_dump()
    settings = json.loads(next(x.payload for x in invocation.generated_inputs if x.relative_path == '.sequence-design-settings.json'))
    keys = ['seqs_per_design', 'fixed_positions'] + (['binder_chains', 'target_chains'] if model == 'proteinmpnn' else ['design_chain', 'target_chain'])
    for key in keys:
        assert settings[key] == invocation.native_parameters[key] == normalized.params[key] == typed.params[key]
    if model == 'proteinmpnn':
        assert 'scripts/proteinmpnn_binder_roles.py' in {d.relative_path for d in invocation.execution_plan.dependencies}


@pytest.mark.parametrize('settings,message', [
    ({'target_chains': 'Z'}, 'overlap'),
    ({'target_chains': 'Q'}, 'absent'),
    ({'fixed_positions': 'Z:99'}, 'absent'),
])
def test_existing_native_role_checks_are_called_by_jobs(settings, message, tmp_path):
    typed = role_request(tmp_path)
    typed.params.update(settings)
    with pytest.raises(HTTPException) as error:
        jobs.normalize_job_request(typed)
    assert error.value.status_code == 422 and message in str(error.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize('model,order', [('proteinmpnn', 'Z,T'), ('proteinmpnn', 'T,Z'), ('fampnn', 'Z,T')])
async def test_role_request_real_compiled_native_transport_and_ingestion(admission, tmp_path, monkeypatch, model, order):
    fixture = helper()
    if model == 'proteinmpnn':
        monkeypatch.setenv('BMS_TEST_MPNN_ROLE_FIXTURE', str(fixture.native_role_fixture(tmp_path)))
    else:
        monkeypatch.setenv('BMS_TEST_FA_REAL_WRITER', '1')
    typed = role_request(tmp_path, model, order)
    response = await jobs._create_job(typed, BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    output = tmp_path / 'native-output'
    invocation = compile_job_nextflow_invocation(job, deepcopy(job.params), str(output))
    for generated in invocation.generated_inputs:
        generated.materialize(output)
    command = list(invocation.command)
    command[2] = str(ROOT / invocation.entrypoint)
    command[command.index('-w') + 1] = str(tmp_path / 'work')
    config = tmp_path / 'offline.config'
    config.write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="128 MB"\napptainer.enabled=false\nsingularity.enabled=false\ndocker.enabled=false\n')
    command = ['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'], '-C', str(ROOT / 'nextflow.config') + ',' + str(config), *command[1:]]
    result = subprocess.run(command, cwd=tmp_path, env=fixture.install_scientific_doubles(tmp_path), text=True, capture_output=True, timeout=150)
    (tmp_path / 'native-transport.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    index = json.loads((output / 'results/sequence_design_results.json').read_text())
    assert index['unfiltered_count'] == index['selected_count'] == 2
    job.status = 'completed'
    await admission.commit()
    assert await ingest_job_results(job.id, output, admission) == 2
    rows = list((await admission.execute(select(Design).where(Design.job_id == job.id))).scalars())
    assert len(rows) == 2
    for row in rows:
        native = json.loads(Path(row.json_path).read_text())
        assert row.confidence_metrics[model] == native
        # Protein's native featurizer sorts masked/visible chains; request order
        # remains unchanged in the persisted compiler settings tested above.
        assert native['binder_chains'] == (sorted(order.split(',')) if model == 'proteinmpnn' else ['B', 'D'])
        assert native['target_chains'] == (['A', 'B'] if model == 'proteinmpnn' else ['A', 'C'])
        assert all(native['chain_sequences'][chain] == 'A' for chain in native['target_chains'])
        assert set(native['designed_chain_sequences']) == ({'T', 'Z'} if model == 'proteinmpnn' else {'B', 'D'})
        assert native['source_structure_sha256'] == hashlib.sha256(Path(typed.params['input_pdb']).read_bytes()).hexdigest()
        from services.binder_round_inputs import _residue_correspondence
        correspondence = _residue_correspondence(native['source_residue_mapping'], reverse=True)
        assert len(correspondence) == 6
        assert {(row['chain_id'], row['auth_seq_id'], row['insertion_code']) for row in correspondence.values()} == {
            ('B', 91, ''), ('Z', 10, ''), ('Z', 30, ''), ('A', 10, ''), ('T', 7, ''), ('T', 18, '')}
        if model == 'proteinmpnn':
            assert native['native_output_tag'] == row.name
            assert native['source_input_tag'] == 'input'
            assert native['designed_chain_sequences'] in ({'T': 'GG', 'Z': 'GA'}, {'T': 'VV', 'Z': 'VA'})
        else:
            assert native['output_structure_name'] == Path(row.pdb_path).name
            assert native['input_binder_chains'] == order.split(',')
            assert native['chain_roles_namespace'] == 'output'
            assert native['designed_chain_sequences'] == {'B': 'GA', 'D': 'GG'}
    assert await ingest_job_results(job.id, output, admission) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['proteinmpnn', 'fampnn', 'caliby_binder'])
async def test_round_input_owner_reaches_actual_normalizer_and_compiler(selected, model):
    from services.binder_round_inputs import design_request, normalize_request
    _, session, root, _, tmp_path = selected
    design = await session.get(Design, 'd0')
    Path(design.pdb_path).write_text(helper().atom(1, 'Z', 17) + helper().atom(2, 'B', 31))
    envelope = normalize_request({
        'sequence_design': {'model_id': model, 'params': {}},
        'prediction': {'model_id': 'protenix', 'params': {'protenix_use_msa': False}},
    })
    typed = design_request(root, root, design, envelope, ['Z'], ['B'])
    normalized, invocation = compile_request(typed, tmp_path / 'compiled-round')
    binder_key = 'design_chain' if model == 'fampnn' else 'binder_chains'
    target_key = 'target_chain' if model == 'fampnn' else 'target_chains'
    assert invocation.native_parameters[binder_key] == normalized.params[binder_key] == 'Z'
    assert invocation.native_parameters[target_key] == normalized.params[target_key] == 'B'


@pytest.mark.asyncio
async def test_caliby_actual_compiler_shell_native_identity_and_ingestion(admission, tmp_path):
    """Real native CLI; only preflight, cleaning and sample are inert."""
    import sys
    import shutil
    fixture = helper()
    source = tmp_path / 'input.pdb'
    source.write_text(fixture.atom(1, 'Z', 17) + fixture.atom(2, 'B', 31))
    typed = JobCreate(name='caliby role fixture', model_id='caliby_binder', mode='design', params={
        'pdb_paths': str(source), 'binder_chains': 'Z', 'target_chains': 'B',
        'caliby_num_seqs_per_pdb': 2, 'caliby_temperature': 0.4,
    })
    response = await jobs._create_job(typed, BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    output = tmp_path / 'native-output'
    invocation = compile_job_nextflow_invocation(job, deepcopy(job.params), str(output))
    assert invocation.native_parameters['binder_chains'] == 'Z'
    assert invocation.native_parameters['target_chains'] == 'B'
    assert invocation.native_parameters['caliby_num_seqs_per_pdb'] == 2
    for generated in invocation.generated_inputs:
        generated.materialize(output)
    config = tmp_path / 'offline.config'
    config.write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="128 MB"\napptainer.enabled=false\nsingularity.enabled=false\ndocker.enabled=false\n')
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    wrapper = bin_dir / 'python3'
    wrapper.write_text(f'#!{sys.executable}\n' + 'ROOT = ' + repr(str(ROOT)) + '\n' + r'''
import os, sys, json
from pathlib import Path
from types import SimpleNamespace
if Path(sys.argv[1]).name != 'run_caliby_sequence_design.py':
    os.execv(sys.executable, [sys.executable] + sys.argv[1:])
sys.path.insert(0, str(Path(ROOT) / 'scripts'))
import gemmi
import run_caliby_sequence_design as runner
runner.preflight_caliby_runtime = lambda **kw: None
runner.maybe_clean_inputs = lambda **kw: kw['pdb_paths']
def sample(paths, **kw):
    folder = Path(kw['out_dir']); folder.mkdir(parents=True)
    results = dict(example_id=[], out_pdb=[], seq=[], input_seq=[], U=[])
    for index in range(kw['num_seqs_per_pdb']):
        native = folder / f'explicit-sample-{index}.cif'
        pdb = Path(paths[0]).read_text().replace('ALA Z', ('GLY' if index == 0 else 'VAL') + ' Z')
        gemmi.read_pdb_string(pdb).make_mmcif_document().write_file(str(native))
        for key, value in dict(example_id='input', out_pdb=str(native), seq=('GA' if index == 0 else 'VA'), input_seq='AA', U=float(index)).items():
            results[key].append(value)
    return results
runner.load_caliby_model = lambda name: SimpleNamespace(sample=sample)
sys.argv = sys.argv[1:]
runner.main()
''')
    wrapper.chmod(0o755)
    command = list(invocation.command)
    command[2] = str(ROOT / invocation.entrypoint)
    command[command.index('-w') + 1] = str(tmp_path / 'work')
    command = ['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'], '-C', str(ROOT / 'nextflow.config') + ',' + str(config), *command[1:]]
    result = subprocess.run(command, cwd=tmp_path, env=dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH']), text=True, capture_output=True, timeout=150)
    (tmp_path / 'caliby-transport.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    published = output / 'collected/binder_generation/caliby'
    assert len(list(published.glob('*.pdb'))) == 2
    assert len(list((published / 'native_outputs').glob('*.cif'))) == 2
    # Remote-style readback: remove the original publication.
    returned = tmp_path / 'returned'
    shutil.copytree(output, returned)
    shutil.rmtree(output)
    job.status = 'completed'
    await admission.commit()
    assert await ingest_job_results(job.id, returned, admission) == 2
    records = list((await admission.execute(select(Design).where(Design.job_id == job.id))).scalars())
    assert len(records) == 2
    for row in records:
        native = row.provenance['generator']
        assert native['binder_chains'] == ['Z'] and native['target_chains'] == ['B']
        assert native['designed_chain_sequences'] in ({'Z': 'G'}, {'Z': 'V'})
        assert native['input_sequence'] == 'AA'
        assert native['native_output_structure']['state'] == 'ready'
        assert Path(native['native_output_structure']['path']).is_relative_to(returned)
        assert native['source_structure_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
        from services.binder_round_inputs import _residue_correspondence
        correspondence = _residue_correspondence(native['source_residue_mapping'], reverse=True)
        assert len(correspondence) == 2
        assert {(row['chain_id'], row['auth_seq_id'], row['insertion_code']) for row in correspondence.values()} == {('Z', 17, ''), ('B', 31, '')}
        assert native['effective_settings']['num_seqs_per_pdb'] == 2
    assert await ingest_job_results(job.id, returned, admission) == 0
