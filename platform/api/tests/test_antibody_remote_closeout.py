"""Offline supported-parent admission -> real shared native compiler regressions."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import HTTPException

from component_runtime import SourceIdentity
from model_registry import get_registry
from routers.jobs import normalize_job_request
from schemas import JobCreate
from services.nextflow import compile_nextflow_invocation, resolve_nextflow_entrypoint


CASES = [
    ('nanobody_binder', 'protein_design', 'RunBoltzGen', {
        'diffusion_method': 'boltzgen', 'run_boltzgen_only': True,
        'boltzgen_target_pdb_path': '/fixture/target.pdb',
        'boltzgen_mode': 'nanobody_binder', 'boltzgen_protocol': 'nanobody-anything',
        'boltzgen_num_designs': 17, 'boltzgen_batch_size': 3,
        'boltzgen_scaffold_length': '95-110', 'boltzgen_cdr_h1_length': '6-8',
        'boltzgen_cdr_h2_length': '7-9', 'boltzgen_cdr_h3_length': '12-17',
        'boltzgen_noise_scale': 0.83, 'boltzgen_step_scale': 1.31,
        'boltzgen_checkpoint_mode': 'both', 'boltzgen_inverse_fold_num_sequences': 3,
        'boltzgen_inverse_fold_avoid': 'C', 'boltzgen_alpha': 0.13,
        'boltzgen_binding_site_residues': 'T12,T15',
        'boltzgen_filter_biased': False, 'boltzgen_parallel_mode': False,
        'stage_family': 'boltzgen', 'stage_mode': 'nanobody_binder',
        'run_docking': False, 'run_diffdock': False, 'run_unidock': False,
        'run_frustrampnn': False, 'interactive_gating': False,
    }),
    ('generator_backbone_refine', 'ppiflow_generator_design', 'RunPartialFlow', {
        'ppiflow_seed_complex_path': '/fixture/seed.pdb',
        'ppiflow_mode': 'backbone_refine', 'ppiflow_stage_mode': 'generator_backbone_refine',
        'stage_family': 'ppiflow', 'stage_mode': 'generator_backbone_refine',
        'ppiflow_samples_per_target': 7, 'ppiflow_start_t': 0.61,
        'ppiflow_retry_limit': 3, 'ppiflow_checkpoint': 'seeded.ckpt',
        'ppiflow_require_anchors': False, 'ppiflow_rotamer_enrichment_enabled': True,
        'ppiflow_rotamer_shell_cutoff': 7.5, 'ppiflow_region_mode': 'selected_cdrs',
        'ppiflow_selected_loops': 'H1,H3', 'ppiflow_objective_mode': 'loop_epitope',
        'ppiflow_objective_threshold': -0.25, 'maturation_redesign_enabled': False,
        'antibody_chains': 'H', 'antigen_chains': 'T', 'interactive_gating': False,
    }),
]


@pytest.mark.parametrize('mode,workflow,stage,settings', CASES)
@pytest.mark.parametrize('target,policy', [(None, 'manual'), ('worker-one', 'manual'), ('worker-two', 'automatic')])
def test_supported_generator_normalization_and_native_custody(mode, workflow, stage, settings, target, policy, tmp_path):
    original = deepcopy(settings)
    request = JobCreate(name='antibody native settings', model_id='antibody_denovo', mode=mode,
                        params=settings, execution_target_id=target,
                        execution_policy={'remote_result_policy': policy})
    normalized = normalize_job_request(request)
    assert normalized.execution_target_id == target
    assert normalized.execution_policy.remote_result_policy == policy
    assert request.params == original
    for key, value in original.items():
        assert normalized.params[key] == value, key
    # Clone/replay uses the exact persisted typed request, not a new-job policy.
    clone = normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert clone.model_dump() == normalized.model_dump()
    invocation = compile_nextflow_invocation('antibody_denovo', mode, normalized.params,
        str(tmp_path / 'not-materialized'), job_id='offline-antibody', requested_params=original,
        source_identity=SourceIdentity.from_checkout(Path(__file__).resolve().parents[3]))
    assert invocation.entrypoint == f'workflows/{workflow}.nf'
    assert json.loads(invocation.requested_json) == original
    for key, value in original.items():
        assert invocation.native_parameters[key] == value, key
    plan = invocation.execution_plan
    assert plan is not None
    assert plan.model_id == 'antibody_denovo'
    assert plan.mode == mode
    selected = {component.component_key for component in plan.metadata.static_components}
    assert stage in selected
    assert 'RFANTIBODY' not in selected
    assert 'RunMaturationFAMPNN' not in selected
    assert plan.complete, plan.metadata.blockers
    from services.remote_execution.bundle import compile_remote_dependencies
    command, remote_params = compile_remote_dependencies('antibody_denovo', mode,
        list(invocation.command), native_invocation=invocation)
    assert command == list(invocation.command)
    assert remote_params == invocation.native_parameters
    assert not (tmp_path / 'not-materialized').exists()


@pytest.mark.parametrize('model,mode', [('boltzgen', 'nanobody_binder'), ('ppiflow', 'generator_backbone_refine')])
def test_standalone_engines_still_rejected(model, mode):
    with pytest.raises(HTTPException):
        normalize_job_request(JobCreate(name='unsupported', model_id=model, mode=mode, params={}))
    with pytest.raises(ValueError, match='internal de-novo engine'):
        resolve_nextflow_entrypoint(effective_profile=model, model_id=model, mode=mode)


@pytest.mark.parametrize('mode,params', [
    ('nanobody_binder', {}), ('generator_backbone_refine', {}),
    ('nanobody_binder', {'boltzgen_target_pdb_path': '/fixture/target.pdb', 'diffusion_method': 'rfd3'}),
    ('generator_backbone_refine', {'ppiflow_seed_complex_path': '/fixture/seed.pdb', 'ppiflow_mode': 'maturation'}),
])
def test_parent_mode_keeps_native_input_and_generator_admission(mode, params):
    assert get_registry().validate_job_params('antibody_denovo', mode, params)



def test_actual_frontend_requests_reach_remote_compiler(tmp_path):
    import os
    from pathlib import Path
    from services.nextflow import compile_workflow_provision_request
    from services.remote_execution.bundle import compile_remote_dependencies
    fixture = os.environ.get('BMS_ANTIBODY_REQUEST_FIXTURE')
    if not fixture:
        pytest.skip('Run the paired offline frontend consumer before this integration case')
    payloads = json.loads(Path(fixture).read_text())
    assert len(payloads) == 8
    assert {p['mode'] for p in payloads} == {'nanobody_binder', 'generator_backbone_refine'}
    for payload in payloads:
        typed = JobCreate.model_validate(payload)
        normalized = normalize_job_request(typed)
        assert normalized.execution_target_id == payload['execution_target_id']
        assert normalized.execution_policy.model_dump() == payload['execution_policy']
        invocation = compile_workflow_provision_request(payload)
        assert invocation.requested_json == invocation.execution_plan.requested_json
        assert json.loads(invocation.requested_json) == payload['params']
        assert invocation.execution_plan.complete, invocation.execution_plan.blockers
        command, remote = compile_remote_dependencies(typed.model_id, typed.mode,
            list(invocation.command), native_invocation=invocation)
        assert command == list(invocation.command)
        for key, value in payload['params'].items():
            if key == 'cdr_positions_by_loop' and value == {}:
                # The UI selects native annotation, not explicit CDR positions.
                # IdentifyAnchorResidues uses [:] when absent; persistence still
                # carries the exact empty selection. No populated map is waived.
                assert json.loads(invocation.effective_json)[key] == {}
                continue
            if value is None:
                assert remote.get(key) is None
                assert json.loads(invocation.effective_json)[key] is None
            else:
                assert remote[key] == value, (typed.mode, key)
        contract = json.loads(invocation.execution_plan.metadata.result_contract_json)
        assert contract['analysis_contract_id'] == (
            'de_novo_generation_v1' if typed.mode == 'nanobody_binder' else 'ppiflow_maturation_v1')



@pytest.mark.parametrize('count,per_child', [(3, 100), (17, 5)])
def test_boltzgen_parallel_selection_matches_native_even_for_one_child(count, per_child, tmp_path):
    settings = {**CASES[0][3], 'boltzgen_parallel_mode': True,
                'boltzgen_num_designs': count, 'boltzgen_designs_per_job': per_child}
    invocation = compile_nextflow_invocation('antibody_denovo', 'nanobody_binder', settings,
        str(tmp_path / 'out'), source_identity=SourceIdentity.from_checkout(Path(__file__).resolve().parents[3]))
    templates = invocation.execution_plan.metadata.dynamic_templates
    expansions = [json.loads(row.expansion_json) for row in templates if row.expansion_json]
    selected = next(row for row in expansions if row.get('child_model') == 'boltzgen_child')
    assert selected['grouping'] == {'num_designs': count, 'designs_per_job': per_child}
    assert selected['child_mode'] == 'nanobody_binder'
    assert invocation.native_parameters['boltzgen_parallel_mode'] is True
    # This proves selection only. Actual prepared-YAML child custody is reported
    # as an unresolved cross-owner consumer in the handback, not science success.



from test_core_protein_scientific_admission import admission


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,workflow,stage,settings', CASES)
async def test_real_job_creation_persists_supported_parent_without_science_launch(admission, mode, workflow, stage, settings):
    from fastapi import BackgroundTasks
    from database import Job
    from routers import jobs
    tasks = BackgroundTasks()
    response = await jobs._create_job(JobCreate(name='offline generator', model_id='antibody_denovo',
        mode=mode, params=deepcopy(settings), execution_target_id=None,
        execution_policy={'remote_result_policy': 'manual'}), tasks, admission)
    admission.expire_all()
    stored = await admission.get(Job, response.id)
    assert stored.model_id == 'antibody_denovo'
    assert stored.mode == mode
    assert stored.execution_target_id is None
    for key, value in settings.items():
        assert stored.params[key] == value, key
    assert stored.params['remote_result_policy'] == 'manual'
    # Do not invoke background tasks: this is actual request persistence, not
    # authorization to start scheduler, provider, native inference or workers.



def test_selected_parallel_child_consumes_native_generator_request(tmp_path, monkeypatch):
    """Retained real spawner/compiler regression; no irrelevant AF2 dependency."""
    from dataclasses import asdict
    from component_runtime import ComponentRequest
    from services.nextflow import compile_component_nextflow_invocation
    import importlib
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'scripts'))
    spawner = importlib.import_module('spawn_boltzgen_children')
    monkeypatch.setattr(spawner, 'component_runtime_enabled', lambda: True)
    monkeypatch.setattr(spawner, 'check_existing_children', lambda *a, **kw: (False, [], {}))
    captured = []
    def submit(payload, **identity):
        captured.append(ComponentRequest.capture(payload=payload, **identity))
        return captured[-1].component_id
    monkeypatch.setattr(spawner, 'submit_child_job', submit)
    monkeypatch.setattr(spawner.requests, 'post', lambda *a, **kw: pytest.fail('host HTTP attempted'))
    yaml_path = tmp_path / 'selected.yaml'
    yaml_path.write_text('entities: []\n')  # transport fixture, not scientific inference input
    target = tmp_path / 'target.pdb'
    target.write_text('END\n')
    settings = {**CASES[0][3], 'boltzgen_parallel_mode': True,
                'boltzgen_num_designs': 3, 'boltzgen_designs_per_job': 100}
    source = SourceIdentity.from_checkout(Path(__file__).resolve().parents[3])
    root = compile_nextflow_invocation('antibody_denovo', 'nanobody_binder', settings,
        str(tmp_path / 'root'), job_id='parent', source_identity=source)
    spawner.spawn_boltzgen_jobs(parent_job_id='parent', total_designs=3, designs_per_job=100,
        yaml_config_path=str(yaml_path), target_pdb_path=str(target), mode='nanobody_binder',
        batch_name='fixture', params_json=None)
    assert len(captured) == 1
    context = {'parent': {'id': 'parent', 'model_id': 'antibody_denovo', 'mode': 'nanobody_binder',
                         'params': settings, 'provenance': {}},
               'source_identity': asdict(source), 'execution_plan': root.execution_plan.to_dict(),
               'plan_sha256': root.execution_plan.plan_sha256, 'artifact_root': str(tmp_path),
               'native_runtime': {'anarcii_execution_mode': 'cpu'},
               'resources': {'gpu_id': 0, 'gpu_ids': [0]}}
    child = compile_component_nextflow_invocation(captured[0], context)
    assert any(row.authority.endswith(':RunBoltzGen') for row in child.execution_plan.metadata.static_components)
    assert child.native_parameters['boltzgen_yaml_config'] == str(yaml_path)
    assert child.native_parameters['boltzgen_num_designs'] == 3
    for key in ('boltzgen_noise_scale', 'boltzgen_step_scale', 'boltzgen_inverse_fold_num_sequences'):
        assert child.native_parameters[key] == settings[key]
    assert 'af2.sif' not in repr(child.execution_plan.to_dict())
    assert not child.execution_plan.metadata.dynamic_templates
    assert {row.component_key for row in child.execution_plan.metadata.static_components} == {'RunBoltzGen'}
    assert json.loads(child.effective_json)['boltzgen_prepared_identity'] == captured[0].payload['params']['boltzgen_prepared_identity']
    for mutation, message in [({'boltzgen_noise_scale': 0.17}, 'conflicts with parent'),
                              ({'num_designs': 2}, 'roster conflicts'),
                              ({'job_index': 1}, 'roster conflicts'),
                              ({'gpu_id': 9}, 'outside the parent reservation'),
                              ({'boltzgen_prepared_sha256': '0'*64}, 'digest conflicts')]:
        payload = deepcopy(captured[0].payload)
        payload['params'].update(mutation)
        altered = ComponentRequest.capture(payload=payload, parent_job_id='parent',
                                           stage='boltzgen', child_key='0')
        with pytest.raises(ValueError, match=message):
            compile_component_nextflow_invocation(altered, context)
    with pytest.raises(ValueError, match='source/plan binding'):
        compile_component_nextflow_invocation(captured[0], {**context, 'plan_sha256':'0'*64})
    with pytest.raises(HTTPException):
        normalize_job_request(JobCreate.model_validate(captured[0].payload))
    yaml_path.write_text('entities: [{protein: {id: H, sequence: CHANGED}}]\n')
    with pytest.raises(ValueError, match='prepared input identity changed'):
        compile_component_nextflow_invocation(captured[0], context)


def test_prepared_boltzgen_package_keeps_yaml_and_scaffolds(tmp_path):
    from scripts.lib.boltzgen_inputs import snapshot, input_identity
    prep = tmp_path/'prep'
    prep.mkdir()
    target = tmp_path/'bound-target.pdb'
    target.write_bytes(b'fixture-native-input')
    (prep/'target.pdb').symlink_to(target)
    (prep/'scaffold.yaml').write_text('entities: [{protein: {id: H, sequence: "A6..8C"}}]\n')
    yaml_path = prep/'boltzgen_input.yaml'
    yaml_path.write_text('entities: [{file: {path: target.pdb}}, {file: {path: scaffold.yaml}}]\n')
    identity = snapshot(yaml_path, tmp_path/'package')
    assert set(identity) == {'boltzgen_input.yaml','scaffold.yaml','target.pdb'}
    assert not (tmp_path/'package/target.pdb').is_symlink()
    target.unlink()
    assert input_identity(tmp_path/'package') == identity
    assert (tmp_path/'package/boltzgen_input.yaml').read_bytes() == yaml_path.read_bytes()
    yaml_path.write_text('entities: [{file: {path: ../escape.pdb}}]\n')
    with pytest.raises(ValueError, match='contained relative'):
        snapshot(yaml_path, tmp_path/'invalid')


def test_parent_rejects_unavailable_native_plddt_before_spawn(tmp_path):
    for value in (70, 0, ''):
        settings = {**CASES[0][3], 'boltzgen_min_plddt':value}
        with pytest.raises(HTTPException, match='pLDDT is unavailable'):
            normalize_job_request(JobCreate(name='native-policy-check', model_id='antibody_denovo', mode='nanobody_binder', params=settings))
        with pytest.raises(ValueError, match='pLDDT is unavailable'):
            compile_nextflow_invocation('antibody_denovo', 'nanobody_binder', settings, str(tmp_path/'out'))
    normalized = normalize_job_request(JobCreate(name='native-policy-check', model_id='antibody_denovo', mode='nanobody_binder',
                                               params={**CASES[0][3], 'boltzgen_min_plddt':None}))
    assert normalized.params['boltzgen_min_plddt'] is None


def test_native_wrapper_preserves_explicit_zero_controls(tmp_path, monkeypatch):
    import sys
    import run_boltzgen_wrapper as wrapper
    config = tmp_path/'input.yaml'
    config.write_text('entities: []\n')
    calls = []
    monkeypatch.setattr(wrapper, 'report_stage', lambda *a, **k: None)
    monkeypatch.setattr(wrapper.os, 'system', lambda command: calls.append(command) or 0)
    monkeypatch.setattr(sys, 'argv', ['wrapper', '--config', str(config), '--out_dir', str(tmp_path/'out'),
                                    '--num_designs', '1', '--noise_scale', '0', '--step_scale', '0'])
    wrapper.main()
    assert len(calls) == 1
    assert '--noise_scale 0.0' in calls[0]
    assert '--step_scale 0.0' in calls[0]


@pytest.mark.parametrize('full_parent', [False, True])
def test_actual_native_spawn_child_and_global_collection(tmp_path, monkeypatch, full_parent):
    """Real offline Nextflow/SQLite; model and PyRosetta calls are named stubs.

    Runs the actual parent Spawn process and actual child entrypoint, then native
    collector/filter/aggregation processes. All scientific args come from the
    shared compiler; no product runner or Nextflow source is replaced.
    """
    import os
    import shutil
    import subprocess
    import sys
    from dataclasses import asdict
    from component_runtime import ComponentRequest
    from scripts.lib.component_adapter import runtime_from_environment
    from services.nextflow import compile_component_nextflow_invocation
    root = Path(__file__).resolve().parents[3]
    jar = Path.home() / '.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    assert jar.is_file(), 'runner must supply the existing offline jar'
    target = tmp_path / 'target.pdb'
    target.write_text('ATOM      1  CA  GLY T   1       0.000   0.000   0.000  1.00 90.00           C  \nEND\n')
    # Real preparation creates this package below, including native scaffold YAML.
    # Consume the real public-builder science projection when the frozen UI
    # runner supplies it. Only synthetic biological inputs/cohort size change.
    browser_params = {}
    if os.environ.get('BMS_ANTIBODY_REQUEST_FIXTURE'):
        browser = next(row for row in json.loads(Path(os.environ['BMS_ANTIBODY_REQUEST_FIXTURE']).read_text())
                       if row['mode'] == 'nanobody_binder')
        browser_params = {key:value for key,value in browser['params'].items() if key.startswith('boltzgen_')}
    settings = {**CASES[0][3], **browser_params, 'boltzgen_target_pdb_path': str(target),
                'boltzgen_parallel_mode': True, 'boltzgen_num_designs': 5,
                'boltzgen_designs_per_job': 2, 'boltzgen_budget': 2,
                'boltzgen_nanobody_framework': 'A' * 120,
                'boltzgen_binding_site_residues': '', 'boltzgen_filter_biased': False}
    source = SourceIdentity.from_checkout(root)
    invocation = compile_nextflow_invocation('antibody_denovo', 'nanobody_binder', settings,
        str(tmp_path/'parent-out'), job_id='00000000-0000-0000-0000-000000000001', source_identity=source)
    assert invocation.execution_plan.complete, invocation.execution_plan.blockers
    components = {row.component_key: row for row in invocation.execution_plan.metadata.static_components}
    assert components['FilterBoltzGen'].depends_on == ('CollectBoltzGenOutputs',)
    assert json.loads(components['WaitForBoltzGenChildren'].resources_json)['execution_role'] == 'coordinator'
    context = dict(ledger_path=str(tmp_path/'ledger.sqlite'), artifact_root=str(tmp_path),
        attempt_id='00000000-0000-0000-0000-000000000002', root_job_id='00000000-0000-0000-0000-000000000001', target_id='00000000-0000-0000-0000-000000000003', lease_id='00000000-0000-0000-0000-000000000004',
        parent=dict(id='00000000-0000-0000-0000-000000000001', model_id='antibody_denovo', mode='nanobody_binder', params=settings, provenance={}, output_dir=str(tmp_path/'parent-out')),
        source_identity=asdict(source), execution_plan=invocation.execution_plan.to_dict(),
        plan_sha256=invocation.execution_plan.plan_sha256,
        resources={'gpu_id':0, 'gpu_ids':[0]}, native_runtime={'anarcii_execution_mode':'cpu'})
    context_path = tmp_path/'context.json'
    context_path.write_text(json.dumps(context))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT', str(context_path))
    runtime = runtime_from_environment()
    # Intercept only the scientific wrapper executable. Preparation, spawner,
    # ledger, compiler, input verification, collector and filter are real source.
    shim = tmp_path/'bin'
    shim.mkdir()
    python = shim/'python3'
    python.write_text('#!' + sys.executable + '\n' + '''import json, os, pathlib, sys
if len(sys.argv)>1 and sys.argv[1]=='-u':
    sys.argv.pop(1)
if len(sys.argv)>1 and sys.argv[1]=='/scripts/run_boltzgen_wrapper.py':
    args=sys.argv[2:]
    def value(key): return args[args.index(key)+1]
    config=pathlib.Path(value('--config'))
    assert config.is_file()
    output=pathlib.Path(value('--out_dir'))/'designs'
    output.mkdir(parents=True)
    count=int(value('--num_designs'))
    for i in range(count):
        name=f'design_{i}'
        (output/(name+'.pdb')).write_text('ATOM      1  CA  GLY H   1       0.000   0.000   0.000  1.00 90.00           C  \\nEND\\n')
        (output/('confidence_'+name+'.json')).write_text(json.dumps({'design_id':name,'design_ptm':0.95,'affinity_probability':0.95,'filter_rmsd':0.5,'designed_sequence':'ACDEFGHIKLMNPQRSTVWY','source':'boltzgen','metrics_source':'csv'}))
    (pathlib.Path(value('--out_dir'))/'science-stub.json').write_text(json.dumps({'argv':args,'yaml':config.read_text(),'cwd':os.getcwd()}))
elif len(sys.argv)>1 and sys.argv[1]=='/scripts/analyse_best_designs.py':
    # Named scientific analysis stub: no fabricated scores; native lineage binds.
    pathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_text(json.dumps({'fixture_origin':'offline-no-pyrosetta'})+'\\n')
else:
    if len(sys.argv)>1 and sys.argv[1].startswith('/scripts/'):
        sys.argv[1]=os.environ['BMS_HOME']+sys.argv[1]
    os.execv(sys.executable,[sys.executable,*sys.argv[1:]])
''')
    python.chmod(0o755)
    (shim/'python').symlink_to(python)
    deny = tmp_path/'deny'
    deny.mkdir()
    (deny/'sitecustomize.py').write_text("import socket\ndef denied(*a,**k): raise RuntimeError('OUTBOUND_DENIED')\nsocket.socket.connect=denied\nsocket.socket.connect_ex=denied\nsocket.create_connection=denied\n")
    env = {**os.environ, 'PATH':str(shim)+':'+os.environ['PATH'], 'BMS_API_PYTHON':sys.executable,
           'BMS_COMPONENT_JOB_ID':'00000000-0000-0000-0000-000000000001', 'BMS_COMPONENT_OUTPUT_DIR':str(tmp_path/'parent-out'),
           'PYTHONPATH':str(deny)+':'+os.environ.get('PYTHONPATH',''),
           'JAVA_TOOL_OPTIONS':'--add-opens=java.base/java.util=ALL-UNNAMED --add-opens=java.base/java.lang=ALL-UNNAMED',
           'NXF_HOME':str(tmp_path/'nxf-home'), 'NXF_OFFLINE':'true', 'NXF_DISABLE_CHECK_LATEST':'true'}
    config = tmp_path/'offline.config'
    # Reuse the production default parameter block, not handwritten test defaults.
    # Runtime/profile process declarations are excluded so no image is launched.
    defaults = (root/'nextflow.config').read_text().split('\nprofiles {', 1)[0]
    config.write_text(defaults + "\nprocess.executor='local'\nprocess.cpus=2\nprocess.memory='1 GB'\nprocess.shell=['/bin/bash','-euo','pipefail']\n")
    def run(entrypoint, params, name):
        directory = tmp_path/name
        directory.mkdir()
        parameter_file = directory/'params.json'
        parameter_file.write_text(json.dumps(params))
        command = ['java','-jar',str(jar),'-C',str(config),'run',str(entrypoint),
                   '-params-file',str(parameter_file),'-ansi-log','false']
        result = subprocess.run(command,cwd=directory,env=env,capture_output=True,text=True,timeout=100)
        (directory/'stdout.log').write_text(result.stdout+result.stderr)
        assert result.returncode == 0, result.stdout+result.stderr
        return directory
    # Production process inputs/order, using the actual prepared YAML producer.
    driver = tmp_path/'spawn.nf'
    driver.write_text("include { PrepBoltzGenInput; SpawnBoltzGenJobs } from '"+str(root/'modules/boltzgen.nf')+"'\n" + '''workflow {
    PrepBoltzGenInput('', '', params.boltzgen_scaffold_length, params.boltzgen_num_designs,
        '', false, '', '', '', '', params.boltzgen_protocol, '', params.boltzgen_nanobody_framework,
        params.boltzgen_cdr_h1_length, params.boltzgen_cdr_h2_length, params.boltzgen_cdr_h3_length,
        file(params.code_root+'/lib/NO_INPUT_PDB'), file(params.code_root+'/lib/NO_LIGAND_PDB'),
        file(params.code_root+'/lib/NO_DNA_STRUCT'), file(params.boltzgen_target_pdb_path))
    SpawnBoltzGenJobs(params.job_id, params.boltzgen_num_designs, params.boltzgen_designs_per_job,
        PrepBoltzGenInput.out.yaml, file(params.boltzgen_target_pdb_path), params.boltzgen_mode, 'offline-native')
}
''')
    parent_future = None
    if full_parent:
        import concurrent.futures
        import time
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        parent_future = pool.submit(run, root/invocation.entrypoint, invocation.native_parameters, 'spawn-run')
        parent_run = tmp_path/'spawn-run'
        deadline = time.monotonic() + 50
        while len(runtime.children(parent_job_id='00000000-0000-0000-0000-000000000001',stage='boltzgen')) != 3:
            if parent_future.done():
                parent_future.result()
                pytest.fail('native parent ended before spawning children')
            assert time.monotonic() < deadline, 'native parent failed to submit its declared roster'
            time.sleep(0.2)
    else:
        parent_run = run(driver, invocation.native_parameters, 'spawn-run')
    rows = runtime.children(parent_job_id='00000000-0000-0000-0000-000000000001', stage='boltzgen')
    assert len(rows) == 3
    counts = []
    original_inputs = None
    for row in rows:
        request = runtime.request(row['job_id'])
        child = compile_component_nextflow_invocation(request, context)
        assert child.entrypoint == 'workflows/boltzgen_child.nf'
        assert {r.component_key for r in child.execution_plan.metadata.static_components} == {'RunBoltzGen'}
        assert not child.execution_plan.metadata.dynamic_templates
        assert not any('af2' in dep.logical_id for dep in child.execution_plan.metadata.dependencies)
        for key in ('boltzgen_noise_scale','boltzgen_step_scale','boltzgen_inverse_fold_num_sequences','boltzgen_budget'):
            assert child.native_parameters[key] == settings[key]
        prepared = Path(child.native_parameters['boltzgen_yaml_config'])
        identity = request.payload['params']['boltzgen_prepared_identity']
        original_inputs = identity if original_inputs is None else original_inputs
        assert identity == original_inputs
        counts.append(child.native_parameters['boltzgen_num_designs'])
        runtime.claim(row['job_id'], owner_id='offline-pump', boot_id='offline-boot')
        child_run = run(root/child.entrypoint, child.native_parameters, 'child-'+str(len(counts)))
        observed = list((child_run/'work').rglob('science-stub.json'))
        assert len(observed) == 1
        record = json.loads(observed[0].read_text())
        assert record['yaml'] == (prepared/'boltzgen_input.yaml').read_text()
        for key, value in [('noise_scale',0.83),('step_scale',1.31),('inverse_fold_num_sequences',3)]:
            assert record['argv'][record['argv'].index('--'+key)+1] == str(value)
        runtime.execution_finished(row['job_id'], owner_id='offline-pump', boot_id='offline-boot',
                                   output_dir=child.native_parameters['out_dir'], exit_code=0)
    assert sorted(counts) == [1,2,2]
    collector = tmp_path/'collect.nf'
    collector.write_text("include { WaitForBoltzGenChildren; CollectBoltzGenOutputs; FilterBoltzGen; AggregateBoltzGenResults } from '"+str(root/'modules/boltzgen.nf')+"'\n" + '''workflow {
    WaitForBoltzGenChildren(params.job_id, file(params.spawn_result), 'offline-native')
    CollectBoltzGenOutputs(WaitForBoltzGenChildren.out.result)
    FilterBoltzGen(CollectBoltzGenOutputs.out.pdbs.collect(), CollectBoltzGenOutputs.out.jsons.collect())
    AggregateBoltzGenResults(params.job_id, FilterBoltzGen.out.pdbs.collect(), FilterBoltzGen.out.jsons.collect(), CollectBoltzGenOutputs.out.manifest)
}
''')
    spawn_result = next((parent_run/'work').rglob('spawn_boltzgen_result.json'))
    if parent_future is not None:
        collected = parent_future.result(timeout=100)
        pool.shutdown()
    else:
        collected = run(collector, {**invocation.native_parameters,'spawn_result':str(spawn_result)}, 'collect-run')
    filters = [path for path in (collected/'work').rglob('filter_summary.json') if path.parent.name == 'filtered']
    assert len(filters) == 1
    summary = json.loads(filters[0].read_text())
    assert summary['input_count'] == 5
    assert summary['final_count'] == 2
    if full_parent:
        import csv
        final = tmp_path/'parent-out/results'
        with (final/'all_designs.csv').open() as handle:
            published = list(csv.DictReader(handle))
        assert len(published) == 2
        assert len({row['candidate_id'] for row in published}) == 2
        assert (final/'success_metrics.json').is_file()
        assert (final/'best_designs.tar.gz').is_file()
        # Exercise the actual native consumer's byte guard on copied test data.
        publication_work = next((collected/'work').rglob('best_designs.tar.gz')).parent
        tampered = tmp_path/'tampered-terminal'
        tampered.mkdir()
        for row in published:
            name = 'candidate_'+row['candidate_id']+'.pdb'
            (tampered/name).write_bytes((publication_work/name).read_bytes())
        first = next(tampered.glob('*.pdb'))
        first.write_bytes(first.read_bytes()+b'changed-fixture-bytes')
        rejected = subprocess.run([sys.executable, str(root/'scripts/filter_best_designs.py'),
            '--csv', str(final/'all_designs.csv'), '--pdb-dir', str(tampered),
            '--output-csv', str(tmp_path/'rejected.csv'), '--output-dir', str(tmp_path/'rejected')],
            env=env, capture_output=True, text=True, timeout=20)
        (tmp_path/'tamper.log').write_text(rejected.stdout+rejected.stderr)
        assert rejected.returncode != 0 and 'projected producer bytes' in rejected.stderr
        assert not list((tmp_path/'rejected').iterdir())
    assert all(row['status'] == 'completed' for row in runtime.children(parent_job_id='00000000-0000-0000-0000-000000000001',stage='boltzgen'))
    assert len(runtime.join_children([row['job_id'] for row in rows])) == 3
    report = json.loads(next((collected/'work').rglob('aggregation_report.json')).read_text())
    assert report['ingestion_triggered'] is False
