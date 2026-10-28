"""S13: real preparation/bundle/bindings, hidden controller; no native inference."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from schemas import JobCreate
from services.native_generation_sources import prepare_native_sources
from scripts.lib import portable_inputs as portable
from test_binder_source_materialization import cif
from test_project_generation_bound_jobs import isolated_core
from test_denovo_remote_retained import _input_inventory, _place_and_hide
from test_denovo_remote_inputs import roots, compile_cad, place
from test_remote_cache_integration import local_transport


def sha(body):
    return hashlib.sha256(body).hexdigest()


def source_roots(tmp_path, monkeypatch):
    import paths
    from routers import files
    root = tmp_path / 'owned'
    roots = {'inputs': root / 'inputs', 'bms_results': root / 'results'}
    for module in (paths, files):
        monkeypatch.setattr(module, 'get_allowed_roots', lambda: roots)
    monkeypatch.delenv(portable.ENV, raising=False)
    return root, roots


def relocate(tmp_path, monkeypatch, root, job):
    refs, assets = _input_inventory(tmp_path, monkeypatch, root, job.params, job.model_id, job.mode)
    assert refs
    _place_and_hide(tmp_path, monkeypatch, root, refs, assets)
    assert not root.exists()
    for ref in refs:
        body = portable.resolve_input_path(ref['source_path']).read_bytes()
        assert (sha(body), len(body)) == (ref['sha256'], ref['size_bytes'])
    return refs


@pytest.mark.asyncio
@pytest.mark.parametrize('parent', [False, True])
async def test_typed_rfd4_frozen_cif_state_companions_order_and_inactive_sources(
        isolated_core, tmp_path, monkeypatch, parent):
    root, roots = source_roots(tmp_path, monkeypatch)
    # A genuine retained Studio export (including internal-coordinate and
    # scalar atom conditions), not a CIF-shaped placeholder for annotations.
    raw = (Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures/rfd4-saved-conditions.cif').read_bytes()
    source = roots['inputs'] / 'annotated_structures' / (sha(raw) + '.mmcif')
    source.parent.mkdir()
    source.write_bytes(raw)
    state = roots['inputs'] / 'state'
    state.mkdir()
    bodies = {'state.pt': b'opaque state\x00',
              'companion.json': b'{"components":[{"ligand_file":"/missing/not-consumed"}],"zero":0,"off":false,"auto":null}',
              'native.yaml': b'!!native opaque state\n'}
    for name, body in bodies.items():
        (state / name).write_bytes(body)
    adapter = roots['inputs'] / 'adapter' / 'companion.json'
    adapter.parent.mkdir()
    adapter.write_bytes(b'{"source":"/missing/observational-path"}')
    native = {'input': {'kind': 'annotated', 'slots': ['B', 'A']},
              'resume_slot': 'state', 'adapter_slot': 'adapter',
              'settings': {'seed': 0, 'metrics_tags': [], 'search': {'resume_seed': 0}}}
    selected = {'path': 'inputs/annotated_structures/' + source.name,
                'expected_sha256': sha(raw), 'model_number': 1}
    params = {'rfd4_request': native, 'native_sources': {
        'B': selected, 'A': selected,
        'state': {'kind': 'file', 'path': 'inputs/state'},
        'adapter': {'kind': 'file', 'path': 'inputs/adapter/companion.json',
                    'expected_sha256': sha(adapter.read_bytes())},
        'inactive': {'kind': 'file', 'path': '/missing/inactive-authoring-file'}}}
    model, mode = ('protein_modification_experimental', 'de_novo_design') if parent else ('rfd4_proteina', 'generate')
    if parent:
        params['generator'] = 'rfd4_proteina'
    job = JobCreate(name='frozen CIF', model_id=model, mode=mode, params=params)
    async with isolated_core() as session:
        await prepare_native_sources(job, session)
        retained = copy.deepcopy(job.params)
        # Replay uses durable custody after the ephemeral editor export is gone.
        source.unlink()
        await prepare_native_sources(job, session)
        assert job.params == retained
        import paths
        assert paths.resolve_allowed_path(job.params['native_sources']['B']['path']).read_bytes() == raw
    closure = Path(job.params['rfd4_input_closure'])
    before = {p.relative_to(closure).as_posix(): p.read_bytes() for p in closure.rglob('*') if p.is_file()}
    refs = relocate(tmp_path, monkeypatch, root, job)
    assert all(ref['role'] == 'native-snapshot' for ref in refs)
    assert {ref['source_path'] for ref in refs} == {str(closure / name) for name in before}
    received = portable.resolve_input_path(closure)
    assert {p.relative_to(received).as_posix(): p.read_bytes() for p in received.rglob('*') if p.is_file()} == before
    bindings = json.loads((received / 'bindings.json').read_bytes())
    assert list(bindings) == ['B', 'A', 'state', 'adapter']
    assert Path(bindings['B']).name.startswith('0000_')
    assert Path(bindings['A']).name.startswith('0001_')
    consumed = (received / bindings['B']).read_bytes()
    assert consumed == (received / bindings['A']).read_bytes()
    # Explicit MODEL selection is a declared derivative, not byte-identical
    # original custody; the bundle preserves each of their respective digests.
    assert b'_atom_site.condition_hbond_1_atom' in consumed
    assert b'_atom_site.annotation_src-component_1_atom' in consumed
    assert b'_condition_InternalCoordinate_1_atom.val2' in consumed
    identity = json.loads((received / 'request.json').read_bytes())['runtime_identity']['sources']['B']
    assert identity['original_sha256'] == sha(raw)
    assert identity['consumed_sha256'] == sha(consumed)
    assert identity['model_number'] == 1
    for name, body in bodies.items():
        assert (received / bindings['state'] / name).read_bytes() == body
    request = json.loads((received / 'request.json').read_bytes())
    assert request['request'] == job.params['rfd4_request']
    assert request['request']['settings'] == native['settings']
    assert set(request['runtime_identity']['sources']) == {'B', 'A', 'state', 'adapter'}
    assert job.params['native_sources']['inactive'] == params['native_sources']['inactive']


@pytest.mark.asyncio
@pytest.mark.parametrize('parent', [False, True])
async def test_complexa_checked_cif_closure_ignores_inactive_authoring_bindings(
        isolated_core, tmp_path, monkeypatch, parent):
    root, roots = source_roots(tmp_path, monkeypatch)
    raw = cif(models=(3, 7)).encode()
    source = roots['inputs'] / 'annotated.cif'
    source.write_bytes(raw)
    params = {'complexa_request': {'task': 'protein_binder', 'task_name': 'frozen',
              'source': {'document_id': 'target'}},
              'native_sources': {'target': {'path': 'inputs/annotated.cif',
                  'expected_sha256': sha(raw), 'model_number': 7},
                  'inactive': {'kind': 'file', 'path': '/missing/inactive-authoring-file'}}}
    model, mode = ('protein_modification_experimental', 'de_novo_design') if parent else ('complexa', 'generate')
    if parent:
        params['generator'] = 'complexa'
    job = JobCreate(name='checked derivative', model_id=model, mode=mode, params=params)
    async with isolated_core() as session:
        await prepare_native_sources(job, session)
    closure = Path(job.params['complexa_request_dir'])
    expected = (closure / 'source.pdb').read_bytes()
    assert b'ATOM' in expected and b'data_' not in expected
    identities = json.loads((closure / 'sources.json').read_bytes())
    assert set(identities) == {'target'}
    assert identities['target']['original_sha256'] == sha(raw)
    assert identities['target']['model_number'] == 7
    refs = relocate(tmp_path, monkeypatch, root, job)
    assert {ref['source_path'] for ref in refs} == {str(p) for p in (
        closure / 'request.json', closure / 'source.pdb', closure / 'source-path.txt',
        closure / 'sources.json', closure / 'source-bindings.json')}
    assert all(ref['role'] == 'native-snapshot' for ref in refs)
    received = portable.resolve_input_path(closure)
    assert (received / 'source.pdb').read_bytes() == expected
    assert json.loads((received / 'request.json').read_bytes()) == job.params['complexa_request']
    assert json.loads((received / 'sources.json').read_bytes()) == identities


@pytest.mark.parametrize('backend', ['disco', 'laproteina'])
@pytest.mark.parametrize('source_free', [False, True])
def test_real_cad_preparer_receives_cold_portable_inputs(roots, tmp_path, monkeypatch, backend, source_free):
    import os
    import shutil
    import subprocess
    import sys
    params = {'backend': backend, 'target_lengths': '40', 'num_designs': 1}
    if backend == 'laproteina':
        settings = {'seed': 0, 'self_cond': False, 'bb_ca_gt_clamp_val': None}
        params['laproteina_settings'] = settings
        if not source_free:
            motif = roots['inputs'] / 'motif.pdb'
            motif.write_bytes(b'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n')
            params.update(design_task='motif_scaffolding', laproteina_preset='motif_idx_aa',
                          laproteina_motif_pdb=str(motif), laproteina_contig_string='A1-1')
    else:
        settings = {'seeds': [0], 'use_deepspeed_evo_attention': False}
        params['disco_settings'] = settings
        if not source_free:
            companion = roots['inputs'] / 'ligand.sdf'
            companion.write_bytes(b'opaque SDF transport body\r\n')
            source = roots['inputs'] / 'jobs.json'
            source.write_text(json.dumps([{'name': 'cold', 'sequences': [
                {'ligand': {'ligand': 'FILE_ligand.sdf', 'count': 1}}]}]))
            params['disco_input_json_path'] = str(source)
    invocation = compile_cad(roots, params, True)
    refs, _, _ = place(roots, tmp_path, monkeypatch, invocation)
    assert bool(refs) is not source_free
    shutil.rmtree(roots['inputs'])
    output = tmp_path / 'worker/prepared.json'
    command = [sys.executable, str(Path(__file__).resolve().parents[3] / 'scripts/prep_protein_cad_request.py'),
               '--backend', backend, '--task', params.get('design_task', 'unconditional'),
               '--num-designs', '1', '--target-lengths', '40', '--output', str(output),
               '--input-dir', str(tmp_path / 'worker/prepared'),
               '--' + backend + '-settings-json', json.dumps(settings)]
    for key in ('laproteina_preset', 'laproteina_motif_pdb', 'laproteina_contig_string', 'disco_input_json_path'):
        if key in params:
            command.extend(['--' + key.replace('_', '-'), params[key]])
    done = subprocess.run(command, capture_output=True, text=True, env=os.environ.copy())
    assert done.returncode == 0, done.stderr
    prepared = json.loads(output.read_bytes())
    for key, value in settings.items():
        assert prepared[backend][key] == value
    if not source_free:
        if backend == 'laproteina':
            assert Path(prepared[backend]['motif_pdb']).read_bytes().startswith(b'ATOM')
            assert prepared[backend]['contig_string'] == 'A1-1'
        else:
            jobs = json.loads(Path(prepared[backend]['input_json_path']).read_bytes())
            assert Path(jobs[0]['sequences'][0]['ligand']['ligand'][5:]).read_bytes() == b'opaque SDF transport body\r\n'


@pytest.mark.parametrize('with_source', [False, True])
def test_rfd3_canonical_request_typed_source_portability(roots, tmp_path, monkeypatch, with_source):
    from services.rfd3_generation import normalize_generation_params, materialize_generation_request
    from services.nextflow import compile_nextflow_invocation
    from component_runtime import SourceIdentity
    import paths
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'inputs': roots['inputs'], 'bms_results': roots['results']})
    params = {'generator': 'rfd3', 'min_length': 40, 'max_length': 40,
              'num_designs': 1, 'seed': 0, 'dump_trajectories': False,
              'conditioning': {'contig': None}}
    raw = cif(models=(7,)).encode()
    source = roots['inputs'] / 'annotated.cif'
    if with_source:
        source.write_bytes(raw)
        params.update(input_structure=str(source), source_mapping='bms.rfd3.source-map.v1', source_model=7)
    normalized, _, _ = normalize_generation_params(params, job_name='cold-rfd3')
    output = roots['inputs'] / 'job'
    output.mkdir()
    bound, document = materialize_generation_request(normalized, output_dir=output, job_id='cold-rfd3')
    invocation = compile_nextflow_invocation('protein_modification_experimental', 'de_novo_design', bound,
        str(output), job_id='cold-rfd3', source_identity=SourceIdentity('a' * 40, 'b' * 40))
    for item in invocation.generated_inputs:
        item.materialize(output)
    expected = document.read_bytes()
    # Use the ordinary bundle owner, not a caller-authored placement envelope.
    refs, assets = _input_inventory(tmp_path, monkeypatch, roots['inputs'], bound,
        invocation.model_id, invocation.mode)
    assert {r['source_path'] for r in refs} == {str(document), *([str(source)] if with_source else [])}
    _place_and_hide(tmp_path, monkeypatch, roots['inputs'], refs, assets)
    received = portable.resolve_input_path(document)
    assert received.read_bytes() == expected
    retained = json.loads(received.read_bytes())
    assert retained['execution']['seed'] == 0 and retained['execution']['dump_trajectories'] is False
    assert retained['conditioning']['contig'] is None
    if with_source:
        assert retained['source_model'] == 7
        assert retained['source_mapping'] == 'bms.rfd3.source-map.v1'
        assert portable.resolve_input_path(retained['source']['path']).read_bytes() == raw
        assert retained['source']['sha256'] == sha(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', ['rfd4_proteina', 'rfd3', 'complexa', 'disco', 'laproteina'])
async def test_selected_existing_runtime_cold_warm_cache_not_family_pack(roots, tmp_path, monkeypatch, local_transport, engine):
    import uuid
    from types import SimpleNamespace
    from component_runtime import SourceIdentity
    from services.nextflow import compile_nextflow_invocation
    from services.remote_execution import bundle, cache, hf_assets
    from test_denovo_native_core import rfd4
    monkeypatch.setattr(hf_assets, 'configuration', lambda: None)
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'unused-image-store'))
    monkeypatch.setattr(cache, 'get_data_root', lambda: roots['data'])
    monkeypatch.setattr(cache, 'get_code_root', lambda: roots['repo'])
    import subprocess
    (roots['repo'] / 'main.nf').write_bytes(b'// inert cache source fixture\n')
    subprocess.run(['git', 'init', '-q', str(roots['repo'])], check=True)
    subprocess.run(['git', '-C', str(roots['repo']), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(roots['repo']), '-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    if engine == 'rfd4_proteina':
        model, mode, params = engine, 'generate', {'rfd4_request': rfd4()}
    elif engine == 'complexa':
        model, mode, params = engine, 'generate', {'complexa_request': {
            'task': 'AME', 'task_name': 'selected', 'reward': None, 'seed': 0}}
    else:
        model, mode = 'protein_modification_experimental', 'de_novo_design'
        params = {'generator': engine, 'backend': engine, 'min_length': 40, 'max_length': 40,
                  'num_designs': 1, 'seed': 0, 'dump_trajectories': False}
        if engine != 'rfd3':
            params = {'generator': engine, 'backend': engine, 'target_lengths': '40', 'num_designs': 1}
        else:
            params.pop('backend')
    invocation = compile_nextflow_invocation(model, mode, params, str(roots['results'] / 'runtime-job'),
        job_id='selected-runtime', source_identity=SourceIdentity('a' * 40, 'b' * 40))
    deps = [dep for dep in invocation.execution_plan.dependencies if dep.kind in {'image', 'weights', 'runtime_data'}]
    expected_images = {'rfd4_proteina': {'rfd4_proteina.sif'}, 'complexa': {'complexa.sif'},
        'rfd3': {'foundry.sif', 'pyrosetta_tools.sif'}, 'disco': {'disco.sif'}, 'laproteina': {'laproteina.sif'}}
    assert {d.relative_path for d in deps if d.kind == 'image'} == expected_images[engine]
    # Create inert bytes for exactly the compiler-selected declarations only.
    for dep in deps:
        base = roots['containers' if dep.kind == 'image' else 'weights']
        path = base / dep.relative_path
        if dep.kind == 'weights' and '.' not in path.name:
            path = path / 'selected.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('inert selected ' + engine + dep.relative_path).encode())
    entries = cache.independent_plan(SimpleNamespace(kind='model', model_id=model), dependencies=deps, publication={})
    assert entries
    assert not any(e.role == 'input' for e in entries)
    connection = SimpleNamespace(remote_root=str(tmp_path / 'worker-cache'))
    _, uploads = local_transport
    await cache.provision_cache(connection=connection, entries=entries, operation_id=str(uuid.uuid4()),
        progress=cache._noop, check_fence=cache._noop)
    cold_uploads = list(uploads)
    assert cold_uploads
    await cache.provision_cache(connection=connection, entries=entries, operation_id=str(uuid.uuid4()),
        progress=cache._noop, check_fence=cache._noop)
    assert uploads == cold_uploads
    for entry in entries:
        if entry.role == 'image':
            obj = Path(connection.remote_root) / 'cache/runtime-images/objects/sha256' / entry.sha256 / 'runtime.sif'
        else:
            obj = Path(connection.remote_root) / 'cache/artifacts/v1/objects/sha256' / entry.sha256[:2] / entry.sha256
        assert sha(obj.read_bytes()) == entry.sha256
