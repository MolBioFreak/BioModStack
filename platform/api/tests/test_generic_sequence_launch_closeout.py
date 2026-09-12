"""Public sequence requests through shared compilation; isolated, no inference."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from component_runtime import SourceIdentity
from model_registry import get_registry
from routers.jobs import normalize_job_request
from schemas import JobCreate
from services.nextflow import compile_job_nextflow_invocation, compile_nextflow_invocation
from services.remote_execution.bundle import compile_remote_dependencies

ROOT = Path(__file__).resolve().parents[3]
ROWS = [('fampnn', mode) for mode in ('design', 'fixed_backbone', 'binder_design')] + [('proteinmpnn', 'design')]


def request(model, mode, tmp_path, target=None, policy='manual'):
    model_definition = get_registry().get_model(model)
    settings = {p.name: deepcopy(p.default) for p in model_definition.params if p.default is not None}
    source = tmp_path / 'input.pdb'
    source.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\n'
                      'ATOM      2  CA  GLY A   2       3.000   0.000   0.000  1.00 20.00           C\n'
                      'ATOM      3  CA  SER B   1       0.000   3.000   0.000  1.00 20.00           C\nEND\n')
    settings['input_pdb'] = str(source)
    if model == 'fampnn':
        settings.update(fampnn_temperature=0.27, fampnn_repack_last=False,
                        fampnn_psce_threshold=0.0, fampnn_mutation_top_n=0)
        settings.update({'design': {'design_chain': 'A'},
                         'fixed_backbone': {'fixed_positions': 'A:1'},
                         'binder_design': {'design_chain': 'A', 'target_chain': 'B',
                                           'fampnn_fix_target_sidechains': True}}[mode])
    else:
        source.write_text(''.join(line for line in source.read_text().splitlines(keepends=True)
                                  if not line.startswith('ATOM') or line[21] == 'A'))
        settings.update(mpnn_temperature=0.27, mpnn_omitAAs='',
                        mpnn_backbone_noise=0.0, mpnn_checkpoint_type='vanilla',
                        mpnn_relax_max_cycles=0)
    return JobCreate(name='isolated public sequence request', model_id=model, mode=mode,
                     params=settings, execution_target_id=target,
                     execution_policy={'remote_result_policy': policy})


def compile_request(typed, output):
    normalized = normalize_job_request(typed)
    job = SimpleNamespace(id='generic-sequence-fixture', model_id=normalized.model_id,
                          mode=normalized.mode, params=normalized.params, provenance={})
    return normalized, compile_job_nextflow_invocation(job, deepcopy(normalized.params), str(output))


@pytest.mark.parametrize('model,mode', ROWS)
@pytest.mark.parametrize('target,policy', [(None, 'manual'), ('worker-one', 'manual'), ('worker-two', 'automatic')])
def test_public_sequence_request_reaches_only_selected_native_model(model, mode, target, policy, tmp_path):
    typed = request(model, mode, tmp_path, target, policy)
    before = typed.model_dump(mode='json')
    normalized, invocation = compile_request(typed, tmp_path / 'not-created')
    assert typed.model_dump(mode='json') == before
    assert normalized.execution_target_id == target
    assert normalized.execution_policy.remote_result_policy == policy
    clone = normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert clone.model_dump() == normalized.model_dump()
    assert invocation.entrypoint == 'workflows/protein_sequence_design.nf'
    assert invocation.command[invocation.command.index('-profile') + 1] == 'protein_sequence_design,workstation_ryzen7960x'
    plan = invocation.execution_plan
    assert plan.complete, plan.blockers
    stages = {node.component_key for node in plan.metadata.static_components}
    expected = ({'PrepFAMPNN', 'RunFAMPNN', 'FilterFAMPNN'} if model == 'fampnn'
                else {'PrepMPNN', 'RunMPNN', 'FilterMPNN'}) | {'PublishSequenceDesign'}
    assert expected == stages
    native_run = next(node for node in plan.metadata.static_components
                      if node.component_key == ('RunFAMPNN' if model == 'fampnn' else 'RunMPNN'))
    assert json.loads(native_run.resources_json)['gpu']['count'] == 1
    assert not stages & {'RunRFD3', 'RunAF2', 'RunBoltz', 'RFANTIBODY',
                         'RunMPNN' if model == 'fampnn' else 'RunFAMPNN'}
    assert not plan.metadata.dynamic_templates
    assert not plan.metadata.external_services
    image_names = {d.relative_path for d in plan.metadata.dependencies if d.kind == 'image'}
    assert image_names == {'pyrosetta_tools.sif', 'fampnn.sif' if model == 'fampnn' else 'dl_binder_design.sif'}
    contract = json.loads(plan.metadata.result_contract_json)
    assert contract['analysis_contract_id'] == 'sequence_design_v1'
    for key, value in typed.params.items():
        assert invocation.native_parameters[key] == value, key
    generated = next(g for g in invocation.generated_inputs if g.relative_path == '.sequence-design-settings.json')
    settings = json.loads(generated.payload)
    assert settings['sequence_design_engine'] == model
    assert settings['sequence_design_mode'] == mode
    assert 'input_pdb' not in settings
    assert not {'code_root', 'weights_root', 'container_dir'} & settings.keys()
    for key, value in typed.params.items():
        if key != 'input_pdb':
            assert settings[key] == value
    command, remote = compile_remote_dependencies(model, mode, list(invocation.command), native_invocation=invocation)
    assert command == list(invocation.command)
    assert remote == invocation.native_parameters
    assert not {'rfd_models', 'af2_models', 'alphafold_params', 'msa_local_db', 'boltz_models'} & remote.keys()
    assert not (tmp_path / 'not-created').exists()


@pytest.mark.parametrize('model,mode', ROWS)
def test_malformed_numeric_and_unknown_mode_remain_rejected(model, mode, tmp_path):
    typed = request(model, mode, tmp_path)
    key = 'fampnn_temperature' if model == 'fampnn' else 'mpnn_temperature'
    typed.params[key] = True
    with pytest.raises(HTTPException) as error:
        normalize_job_request(typed)
    assert error.value.status_code == 422
    typed = request(model, mode, tmp_path)
    typed.mode = 'not_a_supported_mode'
    with pytest.raises(HTTPException) as error:
        normalize_job_request(typed)
    assert error.value.status_code == 422


@pytest.mark.parametrize('model,mode', ROWS)
def test_internal_engine_selection_cannot_change_public_model(model, mode, tmp_path):
    typed = request(model, mode, tmp_path)
    typed.params['sequence_design_engine'] = 'proteinmpnn' if model == 'fampnn' else 'fampnn'
    with pytest.raises(ValueError, match='selection conflicts'):
        compile_nextflow_invocation(model, mode, typed.params, str(tmp_path / 'out'))


@pytest.mark.parametrize('model,mode', ROWS)
def test_generated_science_document_and_pdb_use_shared_input_materialization(model, mode, tmp_path, monkeypatch):
    from services.remote_execution import bundle
    data = tmp_path / 'data'; data.mkdir()
    inputs = data / 'inputs'; inputs.mkdir()
    results = data / 'results'; results.mkdir()
    for name, value in [('get_data_root', data), ('get_inputs_dir', inputs),
                        ('get_results_dir', results), ('get_weights_root', data / 'weights'),
                        ('get_container_dir', data / 'containers')]:
        monkeypatch.setattr(bundle, name, lambda value=value: value)
    typed = request(model, mode, inputs)
    output = results / 'job'
    _, invocation = compile_request(typed, output)
    for generated in invocation.generated_inputs:
        generated.materialize(output)
    assets = bundle._input_assets(invocation.native_parameters, native_invocation=invocation,
        repo_root=ROOT, runtime_paths=set(), output_dir=output)
    assert {source for source, destination in assets} == {
        Path(typed.params['input_pdb']), output / '.sequence-design-settings.json'}
    for source, destination in assets:
        assert not Path(destination).is_absolute()
        assert '..' not in Path(destination).parts
    original = next(g.payload for g in invocation.generated_inputs if g.relative_path.endswith('settings.json'))
    assert (output / '.sequence-design-settings.json').read_bytes() == original


def test_real_nextflow_params_document_preserves_empty_false_zero(tmp_path):
    import os
    import subprocess
    typed = request('proteinmpnn', 'design', tmp_path)
    _, invocation = compile_request(typed, tmp_path / 'compiled')
    for generated in invocation.generated_inputs:
        generated.materialize(tmp_path / 'compiled')
    harness = tmp_path / 'read-params.nf'
    harness.write_text("nextflow.enable.dsl=2\nworkflow { println('PARAMS=' + groovy.json.JsonOutput.toJson([omit:params.mpnn_omitAAs,noise:params.mpnn_backbone_noise,cycles:params.mpnn_relax_max_cycles,engine:params.sequence_design_engine])) }\n")
    config = tmp_path / 'minimal.config'; config.write_text('process.executor="local"\n')
    result = subprocess.run(['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'], '-C', str(config),
        'run', str(harness), '-params-file', str(tmp_path / 'compiled/.sequence-design-settings.json'),
        '-w', str(tmp_path / 'work')], cwd=tmp_path, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(line for line in result.stdout.splitlines() if line.startswith('PARAMS='))
    assert json.loads(line.partition('=')[2]) == {'omit': '', 'noise': 0.0, 'cycles': 0, 'engine': 'proteinmpnn'}


from test_core_protein_scientific_admission import admission


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode', ROWS)
async def test_real_job_creation_preserves_sequence_request_without_launch(admission, model, mode, tmp_path):
    from fastapi import BackgroundTasks
    from database import Job
    from routers import jobs
    typed = request(model, mode, tmp_path)
    before = deepcopy(typed.params)
    tasks = BackgroundTasks()
    response = await jobs._create_job(typed, tasks, admission)
    admission.expire_all()
    stored = await admission.get(Job, response.id)
    assert (stored.model_id, stored.mode) == (model, mode)
    assert stored.execution_target_id is None
    assert stored.params['remote_result_policy'] == 'manual'
    for key, value in before.items():
        assert stored.params[key] == value, key
    # Never run queued tasks: this is actual typed normalization/SQLite custody,
    # not permission for scheduler, provider, model or worker execution.


@pytest.mark.parametrize('model,mode', ROWS)
def test_browser_pdb_alias_uses_existing_allowed_path_resolution(model, mode, tmp_path, monkeypatch):
    from routers import files, jobs
    typed = request(model, mode, tmp_path)
    original = typed.params['input_pdb']
    monkeypatch.setattr(files, 'get_allowed_roots', lambda: {'downloads': tmp_path})
    monkeypatch.setattr(jobs, 'resolve_allowed_path', files._allowed_lexical_path)
    typed.params['input_pdb'] = 'downloads/input.pdb'
    normalized = normalize_job_request(typed)
    assert normalized.params['input_pdb'] == original
    assert typed.params['input_pdb'] == 'downloads/input.pdb'


@pytest.mark.parametrize('key', ['sequence_design_engine', 'sequence_design_mode', 'sequence_design_settings_path'])
def test_public_requests_cannot_supply_native_transport_authority(key, tmp_path):
    typed = request('fampnn', 'design', tmp_path)
    typed.params[key] = 'operator-supplied'
    with pytest.raises(HTTPException) as error:
        normalize_job_request(typed)
    assert error.value.status_code == 422
    assert 'Server-owned' in str(error.value.detail)


@pytest.mark.parametrize('spec', ['A:10-12', 'A:10,A:10', 'A:1-1000000000,A:10'])
def test_native_fixed_ranges_match_existing_typed_admission(spec):
    from scripts.prep_fampnn_constraints_generic import fixed_residues, selected_chains
    from services.fampnn_policy_admission import _fixed_positions
    domain = {('A', 10): 'ALA', ('A', 20): 'ALA', ('B', 21): 'ALA'}
    admitted = _fixed_positions(spec, [f'{chain}:{number}:' for chain, number in domain])
    native = fixed_residues(spec, domain)
    assert {f'{chain}:{number}:' for chain, number in native} == admitted
    assert selected_chains('A,A', domain, 'design_chain') == {'A'}


@pytest.mark.asyncio
async def test_unimplemented_multichain_mpnn_is_rejected_before_job_or_queue(admission, tmp_path):
    from fastapi import BackgroundTasks
    from sqlalchemy import select, func
    from database import Job
    from routers import jobs
    typed = request('proteinmpnn', 'design', tmp_path)
    source = Path(typed.params['input_pdb'])
    text = source.read_text()
    atom = text.splitlines(keepends=True)[0]
    source.write_text(text.replace('END\n', atom[:21] + 'B' + atom[22:] + 'END\n'))
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        await jobs._create_job(typed, tasks, admission)
    assert error.value.status_code == 422 and 'multi-chain' in str(error.value.detail)
    assert await admission.scalar(select(func.count()).select_from(Job)) == 0
    assert not tasks.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode', ROWS)
async def test_actual_compiled_command_native_wrapper_and_sqlite_consumer(admission, model, mode, tmp_path, monkeypatch):
    """Software-chain acceptance with explicit inference/geometry doubles only."""
    import importlib.util
    import os
    import subprocess
    from fastapi import BackgroundTasks
    from sqlalchemy import select
    from database import Design, Job
    from routers import jobs
    from services.result_ingester import ingest_job_results
    spec = importlib.util.spec_from_file_location('sequence_native_transport_fixture',
        ROOT / 'tests/test_generic_sequence_native_transport.py')
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    typed = request(model, mode, tmp_path)
    # An explicit all-alanine inference fixture matches the native recorder's
    # fabricated output alphabet. These are not measured/scientific results.
    source = Path(typed.params['input_pdb'])
    source.write_text(source.read_text().replace(' GLY ', ' ALA ').replace(' SER ', ' ALA '))
    captured_path = os.environ.get('BMS_GENERIC_SEQUENCE_REQUEST_FIXTURE')
    if captured_path:
        from routers import files
        captured = json.loads(Path(captured_path).read_text())
        matches = [value for value in captured if value.get('name') == f'generic-native-{model}-{mode}']
        assert len(matches) == 1, 'actual mounted request missing or duplicated'
        typed = JobCreate.model_validate(matches[0])
        assert typed.execution_target_id is None  # Actual explicit-Local UI case.
        source_alias = tmp_path / 'generic-sequence-fixture.pdb'
        source_alias.write_bytes(source.read_bytes())
        monkeypatch.setattr(files, 'get_allowed_roots', lambda: {'inputs': tmp_path})
        monkeypatch.setattr(jobs, 'resolve_allowed_path', files._allowed_lexical_path)
        # No scientific request field is replaced. Current nextflow.config owns
        # the unsupplied seqs_per_design default of eight.
        expected_count = 8
    else:
        typed.params['seqs_per_design'] = 2
        typed.params['fampnn_max_psce' if model == 'fampnn' else 'mpnn_max_score'] = 1.0
        expected_count = 2
    response = await jobs._create_job(typed, BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    assert job.execution_target_id == typed.execution_target_id
    assert job.params['remote_result_policy'] == typed.execution_policy.remote_result_policy
    for key, value in typed.params.items():
        if key != 'input_pdb':
            assert job.params[key] == value
    output = tmp_path / 'native-output'
    invocation = compile_job_nextflow_invocation(job, deepcopy(job.params), str(output))
    for generated in invocation.generated_inputs:
        generated.materialize(output)
    command = list(invocation.command)
    assert command[1] == 'run'
    command[2] = str(ROOT / invocation.entrypoint)
    # Only execution infrastructure is overridden: the real source config,
    # compiler-produced science document/flags and native helper inputs remain.
    config = tmp_path / 'offline.config'
    config.write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="128 MB"\n'
                      'apptainer.enabled=false\nsingularity.enabled=false\ndocker.enabled=false\n')
    command = ['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'],
               '-C', str(ROOT / 'nextflow.config') + ',' + str(config), *command[1:],
               '-with-trace', str(tmp_path / 'native-trace.tsv')]
    result = subprocess.run(command, cwd=ROOT, env=helper.install_scientific_doubles(tmp_path),
                            text=True, capture_output=True, timeout=150)
    (tmp_path / 'compiled-command.json').write_text(json.dumps(command))
    (tmp_path / 'nextflow.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    index = json.loads((output / 'results/sequence_design_results.json').read_text())
    assert index['engine'] == model and index['mode'] == mode
    assert index['unfiltered_count'] == index['selected_count'] == expected_count
    assert index['settings'].get('seqs_per_design', 8) == expected_count
    job.status = 'completed'; await admission.commit()
    assert await ingest_job_results(response.id, output, admission) == expected_count
    admission.expire_all()
    records = list((await admission.execute(select(Design).where(Design.job_id == response.id))).scalars())
    assert len(records) == expected_count
    for record in records:
        assert record.stage_family == model and record.stage_mode == mode
        assert record.review_profile_id == 'sequence_design_v1'
        assert record.plddt_overall is None and record.pae_overall is None
        assert record.provenance['stage_settings'] == index['settings']
        assert record.confidence_metrics[model] == json.loads(Path(record.json_path).read_text())
    assert await ingest_job_results(response.id, output, admission) == 0
