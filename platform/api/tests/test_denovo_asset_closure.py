"""Full-registry native selection and real small transport leaves, no inference."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import paths
from component_runtime import SourceIdentity
from model_registry import RuntimeDependencyRef, workflow_pack_dependencies
from services import nextflow
from services.remote_execution import bundle, cache, preloading
from services.remote_execution.bundle import resolve_job_result_contract as native_result_contract
from test_denovo_native_core import rfd4
from test_remote_bundle_runtime_gaps import package


def test_pack_runtime_data_uses_data_root_not_weights(tmp_path, monkeypatch):
    refs = workflow_pack_dependencies('de_novo_design')
    selected = [r for r in refs if r.kind == 'runtime_data']
    assert selected == [RuntimeDependencyRef(kind='runtime_data', relative_path='md-preparation/env-v1-explicit.txt')]
    data, weights, containers = [tmp_path / n for n in ('data', 'weights', 'containers')]
    for owner in (paths, bundle):
        monkeypatch.setattr(owner, 'get_data_root', lambda: data)
        monkeypatch.setattr(owner, 'get_weights_root', lambda: weights)
        monkeypatch.setattr(owner, 'get_container_dir', lambda: containers)
    leaf = data / selected[0].relative_path
    leaf.parent.mkdir(parents=True)
    leaf.write_bytes(b'@EXPLICIT\n# inert transport fixture\n')
    entries = cache.independent_plan(SimpleNamespace(kind='workflow_pack'), dependencies=selected, publication={})
    assert [(e.source, e.remote_destination, e.sha256) for e in entries] == [
        (leaf, 'data/' + selected[0].relative_path, hashlib.sha256(leaf.read_bytes()).hexdigest())]
    assert not weights.exists()


@pytest.mark.parametrize('model', ['rfd4_proteina', 'complexa'])
def test_saved_job_uses_exact_native_request_not_pack(tmp_path, monkeypatch, model):
    identity = SourceIdentity('a' * 40, 'b' * 40)
    monkeypatch.setattr(SourceIdentity, 'from_checkout', classmethod(lambda cls, root: identity))
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    params = ({'rfd4_request': rfd4()} if model == 'rfd4_proteina' else
              {'complexa_request': {'task': 'AME', 'task_name': 'fixture', 'reward': None, 'seed': 0}})
    if model == 'rfd4_proteina':
        from scripts.lib.rfd4_proteina_contract import normalize_request
        params['rfd4_request'] = normalize_request(params['rfd4_request'])
    job = SimpleNamespace(id='recipe', model_id=model, mode='generate', params=params,
        child_output_dir=None, output_dir=str(tmp_path / 'uncreated'), provenance={},
        execution_source_revision=identity.revision, execution_source_tree=identity.tree)
    invocations = []
    preloading.compile_recipe(job, native_invocations=invocations)
    invocation, = invocations
    key = 'rfd4_request' if model == 'rfd4_proteina' else 'complexa_request'
    assert invocation.native_parameters[key] == params[key]
    payload = json.loads(invocation.generated_inputs[0].payload)
    assert (payload['request'] if model == 'rfd4_proteina' else payload) == params[key]
    weights = {d.relative_path for d in invocation.execution_plan.dependencies if d.kind == 'weights'}
    assert weights == ({'rfd4-proteina'} if model == 'rfd4_proteina' else
        {'complexa/complexa_ame.ckpt', 'complexa/complexa_ame_ae.ckpt'})
    assert not Path(job.output_dir).exists()


def test_complexa_autoguidance_is_selected_with_emitted_argument(tmp_path, monkeypatch):
    weights = tmp_path / 'weights'
    containers = tmp_path / 'containers'
    weights.mkdir(); containers.mkdir()
    checkpoint = weights / 'ag.ckpt'
    checkpoint.write_bytes(b'custom autoguidance transport fixture')
    (containers / 'complexa.sif').write_bytes(b'inert image')
    (weights / 'complexa').mkdir()
    for name in ('complexa_ame.ckpt', 'complexa_ame_ae.ckpt'):
        (weights / 'complexa' / name).write_bytes(b'paired transport fixture')
    for owner in (paths, bundle):
        monkeypatch.setattr(owner, 'get_weights_root', lambda: weights)
        monkeypatch.setattr(owner, 'get_container_dir', lambda: containers)
        monkeypatch.setattr(owner, 'get_data_root', lambda: tmp_path)
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'image-store'))
    params = {'complexa_request': {'task': 'AME', 'task_name': 'fixture', 'reward': None},
              'complexa_autoguidance_checkpoint': str(checkpoint), 'weights_root': str(weights),
              'container_dir': str(containers)}
    invocation = nextflow.compile_nextflow_invocation('complexa', 'generate', params,
        str(tmp_path / 'out'), job_id='fixture', source_identity=SourceIdentity('a' * 40, 'b' * 40))
    position = invocation.command.index('--complexa_autoguidance_checkpoint')
    assert invocation.command[position + 1] == str(checkpoint)
    dep, = [d for d in invocation.execution_plan.dependencies if d.selector == 'complexa_autoguidance_checkpoint']
    assert dep.kind == 'weights' and dep.relative_path is None
    assert invocation.native_parameters[dep.selector] == str(checkpoint)
    assets = bundle._runtime_assets('complexa', 'generate', invocation.native_parameters,
        include_support=False, selected_plan=invocation.execution_plan, publication={})
    assert (checkpoint, 'weights/ag.ckpt') in assets
    rows = bundle._runtime_records(checkpoint, 'weights/ag.ckpt', publication={})
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert rows[0].sha256 == digest
    checkpoint.write_bytes(b'changed custom checkpoint')
    assert bundle._runtime_records(checkpoint, 'weights/ag.ckpt', publication={})[0].sha256 != digest


@pytest.mark.parametrize('metadata', [{}, {'source_path': '/controller/observation'}])
def test_historical_md_provenance_does_not_gain_required_fields(metadata):
    assert json.loads(bundle._rewrite_md_source_provenance(json.dumps(metadata), {})) == metadata


def test_md_compiler_emits_typed_collector_provenance(tmp_path):
    provenance = {'snapshot': {'structure': str(tmp_path / 'selected.pdb'), 'model': 2},
        'original_snapshot': {'path': str(tmp_path / 'original.pdb'), 'sha256': 'a' * 64},
        'source_path': '/observational/source', 'source': {'document_id': 'document', 'model': 2}}
    invocation = nextflow.compile_nextflow_invocation('molecular_dynamics', 'analyze',
        {'md_source_provenance': provenance}, str(tmp_path / 'out'), job_id='fixture',
        source_identity=SourceIdentity('a' * 40, 'b' * 40))
    wire = invocation.command[invocation.command.index('--md_source_provenance_json') + 1]
    assert json.loads(wire) == invocation.native_parameters['md_source_provenance'] == provenance
    rebound = json.loads(bundle._rewrite_md_source_provenance(wire, {str(tmp_path): '/worker/inputs'}))
    assert rebound['snapshot']['structure'] == '/worker/inputs/selected.pdb'
    assert rebound['original_snapshot']['path'] == '/worker/inputs/original.pdb'
    assert rebound['source_path'] == provenance['source_path']
    assert rebound['source'] == provenance['source']


def test_md_provenance_in_actual_worker_command(package, tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_FEATURE_MOLECULAR_DYNAMICS', '1')
    monkeypatch.setattr(bundle, 'resolve_job_result_contract', native_result_contract)
    roots, _, job, target, _ = package
    original = roots['inputs'] / 'original.pdb'
    derivative = roots['inputs'] / 'selected.pdb'
    original.write_bytes(b'MODEL 1\nMODEL 2\n')
    derivative.write_bytes(b'MODEL 2\n')
    provenance = {'snapshot': {'structure': str(derivative), 'model': 2},
        'original_snapshot': {'path': str(original), 'sha256': hashlib.sha256(original.read_bytes()).hexdigest()},
        'source_path': '/observation/only', 'source': {'document_id': 'document', 'model': 2}}
    job.model_id, job.mode = 'molecular_dynamics', 'simulate'
    job.stage_family = job.stage_mode = job.selected_input_artifact_class = None
    job.params = {'md_source_provenance': provenance, 'md_job_spec': {
        'schema': 'bms.md.job.v2', 'engine': 'gromacs',
        'input': {'structure': str(derivative)},
        'chemistry': {'profile_id': 'gmx_amber99sb_ildn_tip3p_smoke_v1'}}}
    invocation = nextflow.compile_nextflow_invocation(job.model_id, job.mode, job.params,
        job.output_dir, job_id=job.id, source_identity=SourceIdentity('a' * 40, 'b' * 40))
    assert invocation.execution_plan.complete, invocation.execution_plan.metadata.blockers
    source = Path(__file__).resolve().parents[3]
    import shutil
    for dependency in invocation.execution_plan.dependencies:
        if dependency.kind == 'support_tool' and dependency.relative_path:
            path = roots['repo'] / dependency.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / dependency.relative_path, path)
        if dependency.kind == 'image':
            (roots['containers'] / dependency.relative_path).write_bytes(b'inert MD transport image')
    prepared = bundle.prepare_remote_bundle(job=job, target=target,
        command=list(invocation.command), native_invocation=invocation)
    context_file, = [t.source for t in prepared.input_transfers if t.source.name == 'component-context.json']
    context = json.loads(context_file.read_text())
    command = context['root_command']
    wire = command[command.index('--md_source_provenance_json') + 1]
    received = json.loads(wire)
    assert context['parent']['params']['md_source_provenance'] == received
    assert context['parent']['params']['md_job_spec']['input']['structure'] == received['snapshot']['structure']
    assert invocation.native_parameters['md_source_provenance'] == provenance
    assert received['snapshot']['structure'] != str(derivative)
    assert received['original_snapshot']['path'] != str(original)
    assert received['source'] == provenance['source']
    assert received['source_path'] == provenance['source_path']
    for field, expected in ((received['snapshot']['structure'], b'MODEL 2\n'),
                            (received['original_snapshot']['path'], b'MODEL 1\nMODEL 2\n')):
        transfer, = [t for t in prepared.input_transfers if t.remote_destination == field]
        destination = Path(field)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(transfer.source, destination)
        assert destination.read_bytes() == expected
    shutil.rmtree(roots['inputs'])
    assert Path(received['snapshot']['structure']).read_bytes() == b'MODEL 2\n'
    assert Path(received['original_snapshot']['path']).read_bytes() == b'MODEL 1\nMODEL 2\n'
