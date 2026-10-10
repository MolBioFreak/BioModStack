"""Real MD compiler and portable receiving owners; no scientific execution."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from component_runtime import SourceIdentity
from services import nextflow
from services.remote_execution import bundle
from services.remote_execution.targets import selected_plan_target_resources
from scripts.bms_md.contract import normalize_job_config, prepare_verified_worker_inputs
from scripts.lib import portable_inputs
from test_remote_bundle_path_gaps import roots


@pytest.mark.parametrize('prepared', [False, True], ids=['protein', 'prepared'])
def test_md_selected_compile_and_offline_native_receiving(roots, tmp_path, monkeypatch, prepared):
    import model_registry
    monkeypatch.setattr(model_registry, 'molecular_dynamics_feature_enabled', lambda: True)
    source = roots['inputs'] / 'system'
    source.mkdir()
    field = 'coordinates' if prepared else 'structure'
    coordinate = source / ('system.gro' if prepared else 'system.pdb')
    coordinate.write_bytes(b'inert coordinate transport fixture\n')
    inp = {field: str(coordinate), field + '_sha256': bundle._sha256_file(coordinate),
           field + '_bytes': coordinate.stat().st_size}
    expected = {coordinate.name: coordinate.read_bytes()}
    if prepared:
        topology = source / 'system.top'
        topology.write_text('#include "nested/molecule.itp"\n')
        include = source / 'nested/molecule.itp'
        include.parent.mkdir()
        include.write_text('[ moleculetype ]\nFIXTURE 3\n')
        inp.update(topology=str(topology), topology_sha256=bundle._sha256_file(topology),
                   topology_bytes=topology.stat().st_size,
                   topology_closure={'root': str(source), 'files': [
                       {'path': 'nested/molecule.itp', 'sha256': bundle._sha256_file(include),
                        'bytes': include.stat().st_size}]})
        expected.update(topology=topology.read_bytes(), include=include.read_bytes())
    cfg = normalize_job_config({'schema': 'bms.md.job.v1', 'job_id': 'md-bridge',
                                'engine': 'gromacs', 'replicas': 2, 'input': inp})
    config = source / 'config.json'
    config.write_text(json.dumps(cfg))
    params = {'md_job_config': str(config), 'md_job_spec': cfg}
    output = roots['results'] / 'md-bridge'
    invocation = nextflow.compile_nextflow_invocation('molecular_dynamics', 'simulate', params,
        str(output), job_id='md-bridge', source_identity=SourceIdentity('a'*40, 'b'*40),
        execution_context=nextflow.NativeCompilerExecutionContext(gpu_id=0, gpu_ids=(0,),
                                                                 anarcii_execution_mode='cpu'))
    invocation.materialize_inputs(output)
    plan = invocation.execution_plan
    assert plan is not None
    assert plan.complete, plan.to_dict()['blockers']
    dependencies = [row.relative_path for row in plan.dependencies]
    assert any('gromacs' in str(path) for path in dependencies)
    assert not any('openmm' in str(path) for path in dependencies)
    resources = selected_plan_target_resources(SimpleNamespace(id='vast:fixture'), plan,
                                               gpu_ids=[0], scratch_bytes=0)
    assert resources['gpu_ids'] == [0]
    assert max(row['gpu_count'] for row in resources['components']) == 1
    assert resources['coordinator_overlap']['cpus'] > 0
    _, effective_params = bundle.compile_remote_dependencies('molecular_dynamics', 'simulate',
        list(invocation.command), native_invocation=invocation)
    assets = bundle._input_assets(effective_params, native_invocation=invocation,
        repo_root=nextflow.PROJECT_ROOT, runtime_paths=set(), output_dir=output)
    refs = portable_inputs.discover_native_input_references('molecular_dynamics', 'simulate',
        invocation.native_parameters, invocation.generated_inputs, output_dir=output,
        allowed_roots=list(roots.values()))
    assert all(any(Path(ref['source_path']) == path or Path(ref['source_path']).is_relative_to(path)
                   for path, _ in assets) for ref in refs)
    worker = tmp_path / 'worker'
    worker.mkdir()
    bindings = []
    transferred = {}
    for ref in refs:
        source_path = ref['source_path']
        if source_path not in transferred:
            target = worker / f'{len(transferred)}-{Path(source_path).name}'
            shutil.copyfile(source_path, target)
            transferred[source_path] = target
        bindings.append({'reference': ref, 'path': str(transferred[source_path])})
    binding_path = worker / 'bindings.json'
    binding_path.write_text(json.dumps({'schema': portable_inputs.SCHEMA,
                                      'roots': [str(worker)], 'bindings': bindings}))
    monkeypatch.setenv(portable_inputs.ENV, str(binding_path))
    archived = portable_inputs.resolve_input_path(invocation.native_parameters['md_job_config'])
    archived_bytes = archived.read_bytes()
    shutil.rmtree(source)
    shutil.rmtree(output)
    observed = prepare_verified_worker_inputs(archived, worker / 'private')
    assert Path(observed['input'][field]).read_bytes() == expected[coordinate.name]
    assert observed['execution']['gpu_id'] == '0'
    if prepared:
        assert Path(observed['input']['topology']).read_bytes() == expected['topology']
        closure = observed['input']['topology_closure']
        assert (Path(closure['root']) / 'nested/molecule.itp').read_bytes() == expected['include']
        assert closure['files'][0]['path'] == 'nested/molecule.itp'
    assert archived.read_bytes() == archived_bytes
    assert params['md_job_spec'] == cfg


@pytest.mark.parametrize('field', ['tpr', 'checkpoint', 'index', 'restraint_reference'])
def test_native_md_v3_declared_file_references(tmp_path, monkeypatch, field):
    source = tmp_path / 'controller'
    source.mkdir()
    native = source / (field + '.dat')
    native.write_bytes(b'inert native file')
    mdp = source / 'production.mdp'
    mdp.write_text('integrator = md\nnsteps = 20\n')
    document = {'schema': 'bms.md.job.v3', 'input': {field: str(native)},
                'stages': [{'name': 'production', 'mdp_file': str(mdp)}],
                'provenance': {'unrelated_path': '/not/an/input'},
                'execution': {'output': '/not/an/input'}}
    config = source / 'config.json'
    config.write_text(json.dumps(document))
    refs = portable_inputs.discover_native_input_references('molecular_dynamics', 'simulate',
        {'md_job_config': str(config)}, (), output_dir=source, allowed_roots=[source])
    assert {r['source_path'] for r in refs} == {str(config), str(native), str(mdp)}
    worker = tmp_path / 'worker'
    worker.mkdir()
    bindings = []
    for ref in refs:
        target = worker / Path(ref['source_path']).name
        shutil.copyfile(ref['source_path'], target)
        bindings.append({'reference': ref, 'path': str(target)})
    binding = worker / 'bindings.json'
    binding.write_text(json.dumps({'schema': portable_inputs.SCHEMA,
                                  'roots': [str(worker)], 'bindings': bindings}))
    monkeypatch.setenv(portable_inputs.ENV, str(binding))
    shutil.rmtree(source)
    bound = portable_inputs.bind_native_document(document, 'md-job', owner=worker/'config.json')
    assert Path(bound['input'][field]).read_bytes() == b'inert native file'
    assert Path(bound['stages'][0]['mdp_file']).read_text() == 'integrator = md\nnsteps = 20\n'
    assert document['input'][field] == str(native)
    assert bound['provenance'] == document['provenance']
    assert bound['execution'] == document['execution']


@pytest.mark.parametrize('threads', [2, 32])
def test_native_md_v3_cpu_reservation_follows_execution(roots, tmp_path, monkeypatch, threads):
    import model_registry
    from scripts.lib.component_adapter import native_resource_config
    monkeypatch.setattr(model_registry, 'molecular_dynamics_feature_enabled', lambda: True)
    config = {'schema': 'bms.md.job.v3', 'engine': 'gromacs', 'replicas': 1,
              'input': {'tpr': '/managed/system.tpr'}, 'stages': [],
              'execution': {'ntmpi': 1, 'ntomp': threads}}
    invocation = nextflow.compile_nextflow_invocation('molecular_dynamics', 'simulate',
        {'md_job_spec': config}, str(tmp_path), job_id='md-resource',
        source_identity=SourceIdentity('a'*40, 'b'*40),
        execution_context=nextflow.NativeCompilerExecutionContext(gpu_id=0, gpu_ids=(0,),
                                                                 anarcii_execution_mode='cpu'))
    plan = invocation.execution_plan
    assert plan is not None and plan.complete
    component = next(c for c in plan.metadata.dynamic_templates
                     if c.authority.endswith(':MD_GROMACS_REPLICA'))
    assert json.loads(component.resources_json)['cpus']['value'] == threads
    resources = selected_plan_target_resources(SimpleNamespace(id='vast:fixture'), plan,
                                               gpu_ids=[0], scratch_bytes=0)
    config_path = roots['results'] / 'normalized_config.json'
    config_path.write_text(json.dumps(config))
    child = nextflow.compile_nextflow_invocation('molecular_dynamics', 'replica',
        {'md_job_config': str(config_path), 'md_engine': 'gromacs'}, str(roots['results']/'child'),
        job_id='md-replica', source_identity=SourceIdentity('a'*40, 'b'*40),
        execution_context=nextflow.NativeCompilerExecutionContext(gpu_id=0, gpu_ids=(0,),
                                                                 anarcii_execution_mode='cpu'))
    rendered = native_resource_config(child.execution_plan.to_dict(), resources,
                                      str(tmp_path/'compute.lock'), nextflow.PROJECT_ROOT)
    block = rendered.split("withName: 'MD_GROMACS_REPLICA'", 1)[1].split('withName:', 1)[0]
    child_component = next(c for c in child.execution_plan.metadata.static_components
                           if c.authority.endswith(':MD_GROMACS_REPLICA'))
    assert json.loads(child_component.resources_json)['cpus']['value'] == threads
    assert f"executor.cpus = {resources['required']['cpus']}" in rendered
    assert 'flock -x 198' in block
    assert resources['gpu_ids'] == [0]

