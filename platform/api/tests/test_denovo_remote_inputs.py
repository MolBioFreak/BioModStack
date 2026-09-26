"""Real CAD compiler -> portable input transport; inert bytes, no model runtime."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import paths
from component_runtime import SourceIdentity
from scripts.lib import portable_inputs as portable
from services import nextflow
from services.remote_execution import bundle


@pytest.fixture
def roots(tmp_path, monkeypatch):
    roots = {key: tmp_path / key for key in
             ('data', 'inputs', 'results', 'weights', 'containers', 'repo')}
    for root in roots.values():
        root.mkdir()
    for engine in ('disco', 'laproteina'):
        (roots['containers'] / f'{engine}.sif').write_bytes(b'inert runtime fixture')
        (roots['weights'] / engine).mkdir()
        (roots['weights'] / engine / 'checkpoint.bin').write_bytes(b'inert weight fixture')
    for getter, key in [('get_data_root', 'data'), ('get_inputs_dir', 'inputs'),
                        ('get_results_dir', 'results'), ('get_weights_root', 'weights'),
                        ('get_container_dir', 'containers')]:
        monkeypatch.setattr(paths, getter, lambda key=key: roots[key])
        monkeypatch.setattr(bundle, getter, lambda key=key: roots[key])
    monkeypatch.setattr(nextflow, 'get_work_dir', lambda: roots['data'] / 'work')
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.delenv(portable.ENV, raising=False)
    for name in ('BMS_WEIGHTS', 'BMS_RFD_MODELS', 'BMS_AF2_MODELS',
                 'BMS_BOLTZ_MODELS', 'BMS_COLABFOLD_DB'):
        monkeypatch.setenv(name, str(roots['weights']))
    return roots


def compile_cad(roots, params, public_model):
    model, mode = ('protein_modification_experimental', 'de_novo_design') if public_model else (
        'protein_cad_experimental', 'design')
    requested = dict(params)
    if public_model:
        requested['generator'] = requested['backend']
    return nextflow.compile_nextflow_invocation(model, mode, requested,
        str(roots['results'] / 'job'), job_id='portable-cad',
        source_identity=SourceIdentity('a' * 40, 'b' * 40))


def discover(roots, invocation, params):
    return portable.discover_native_input_references(invocation.model_id, invocation.mode,
        params, invocation.generated_inputs, output_dir=roots['results'] / 'job',
        allowed_roots=(roots['data'], roots['inputs'], roots['results']))


def place(roots, tmp_path, monkeypatch, invocation):
    # Exercise the actual input inventory, records and binding writer. Runtime
    # installation/source archive are intentionally outside this input-only test.
    refs = []
    # Use the real integrated selected plan, rather than the baseline lane's
    # input-only bypass. No model completeness or compilation is mocked.
    _, placed_params = bundle.compile_remote_dependencies(
        invocation.model_id, invocation.mode, list(invocation.command),
        native_invocation=invocation)
    runtime = bundle._runtime_assets(invocation.model_id, invocation.mode,
        placed_params, selected_plan=invocation.execution_plan,
        only_kinds=frozenset({'image', 'weights', 'runtime_data'}))
    assert runtime
    assets = bundle._input_assets(placed_params,
        native_invocation=invocation, repo_root=roots['repo'], runtime_paths={roots['weights']},
        output_dir=roots['results'] / 'job', references=refs)
    worker = tmp_path / 'worker'
    records, transfers = [], []
    for source, relative in assets:
        prefix = 'inputs/' + relative
        records.extend(bundle._input_records(source, prefix, native_invocation=invocation,
            output_dir=roots['results'] / 'job'))
        transfers.append(bundle.TransferPlan(source, str(worker / 'bundle' / prefix)))
    transfer, record = bundle._write_portable_bindings(staging_root=roots['data'],
        remote_attempt=str(worker), references=refs, input_transfers=transfers,
        input_records=records, remote_runtime=str(worker / 'runtime'),
        remote_results=str(worker / 'results'))
    for item in [*transfers, transfer]:
        target = Path(item.remote_destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, target)
    assert bundle._sha256_file(Path(transfer.remote_destination)) == record.sha256
    monkeypatch.setenv(portable.ENV, transfer.remote_destination)
    return refs, records, transfers


@pytest.mark.parametrize('public_model', [False, True])
def test_compiled_disco_nested_closure_exact_bytes_offline(roots, tmp_path, monkeypatch, public_model):
    relative = roots['inputs'] / 'relative.sdf'
    absolute = roots['inputs'] / 'absolute.sdf'
    relative.write_bytes(b'relative inert fixture\r\n\x00')
    absolute.write_bytes(b'absolute inert fixture\n')
    source = roots['inputs'] / 'native.json'
    document = [{'name': 'transport-only', 'sequences': [
        {'ligand': {'ligand': 'FILE_relative.sdf'}},
        {'ligand': {'ligand': 'FILE_' + str(absolute)}},
        {'ligand': {'ligand': 'CCD_NOT_A_FILE'}}]}]
    source.write_text(json.dumps(document, indent=3) + '\n')
    originals = {p: p.read_bytes() for p in (source, relative, absolute)}
    params = {'backend': 'disco', 'disco_input_json_path': str(source)}
    invocation = compile_cad(roots, params, public_model)
    assert invocation.native_parameters['pcad_disco_input_json_path'] == str(source)
    assert 'disco_input_json_path' not in invocation.native_parameters
    approved = discover(roots, invocation, params)
    compiled = discover(roots, invocation, invocation.native_parameters)
    assert {r['source_path'] for r in compiled} == {str(p) for p in originals}
    refs, records, transfers = place(roots, tmp_path, monkeypatch, invocation)
    assert {r['source_path']: (r['sha256'], r['size_bytes']) for r in approved} == {
        r['source_path']: (r['sha256'], r['size_bytes']) for r in refs}
    assert {t.source for t in transfers} == set(originals)
    assert {(r.sha256, r.size_bytes) for r in records} == {
        (hashlib.sha256(content).hexdigest(), len(content)) for content in originals.values()}
    assert all(p.read_bytes() == content for p, content in originals.items())
    shutil.rmtree(roots['inputs'])
    for path, content in originals.items():
        assert portable.resolve_input_path(path).read_bytes() == content
    relocated = portable.resolve_input_path(source)
    derived = portable.bind_native_document(json.loads(relocated.read_bytes()), 'disco-json', owner=relocated)
    for index, original in enumerate((relative, absolute)):
        bound = Path(derived[0]['sequences'][index]['ligand']['ligand'][5:])
        assert bound.read_bytes() == originals[original]
    assert relocated.read_bytes() == originals[source]
    # Existing worker-side byte verification stays authoritative.
    portable.resolve_input_path(absolute).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='digest/size mismatch'):
        portable.resolve_input_path(absolute)


@pytest.mark.parametrize('backend,key', [('disco', 'disco_ligand_sdf'),
                                         ('laproteina', 'laproteina_motif_pdb')])
@pytest.mark.parametrize('public_model', [False, True])
def test_compiled_scalar_input_alias_offline(roots, tmp_path, monkeypatch, backend, key, public_model):
    source = roots['inputs'] / ('fixture.sdf' if backend == 'disco' else 'fixture.pdb')
    content = b'inert scalar transport fixture\r\n'
    source.write_bytes(content)
    invocation = compile_cad(roots, {'backend': backend, key: str(source)}, public_model)
    assert invocation.native_parameters['pcad_' + key] == str(source)
    assert key not in invocation.native_parameters
    assert {r['source_path'] for r in discover(roots, invocation, invocation.native_parameters)} == {str(source)}
    refs, _, transfers = place(roots, tmp_path, monkeypatch, invocation)
    assert {r['source_path'] for r in refs} == {str(source)}
    assert [t.source for t in transfers] == [source]
    source.unlink()
    assert portable.resolve_input_path(source).read_bytes() == content


@pytest.mark.parametrize('backend', ['disco', 'laproteina'])
def test_absent_compiled_biological_inputs_are_not_acquired(roots, backend):
    invocation = compile_cad(roots, {'backend': backend}, True)
    assert discover(roots, invocation, invocation.native_parameters) == []


@pytest.mark.parametrize('violation', ['escape', 'symlink'])
def test_compiled_nested_inputs_retain_containment_checks(roots, tmp_path, violation):
    outside = tmp_path / 'outside.sdf'
    outside.write_bytes(b'outside approved roots')
    value = str(outside)
    if violation == 'symlink':
        link = roots['inputs'] / 'link.sdf'
        link.symlink_to(outside)
        value = str(link)
    source = roots['inputs'] / 'native.json'
    source.write_text(json.dumps([{'sequences': [{'ligand': {'ligand': 'FILE_' + value}}]}]))
    invocation = compile_cad(roots, {'backend': 'disco', 'disco_input_json_path': str(source)}, True)
    with pytest.raises(ValueError, match='outside approved roots|symlink'):
        discover(roots, invocation, invocation.native_parameters)


def test_approval_still_rejects_changed_source_bytes(roots):
    source = roots['inputs'] / 'native.json'
    source.write_text('[{"sequences": []}]')
    params = {'backend': 'disco', 'disco_input_json_path': str(source)}
    invocation = compile_cad(roots, params, True)
    approved = discover(roots, invocation, params)
    job = SimpleNamespace(provenance={'execution_plan_approval': {
        'input_request': {'model_id': invocation.model_id, 'mode': invocation.mode,
                          'params': params, 'output_dir': str(roots['results'] / 'job')},
        'input_identities': approved}})
    bundle.verify_approved_native_inputs(job, {})
    source.write_bytes(source.read_bytes() + b' ')
    with pytest.raises(bundle.RemoteBundleError, match='bytes or membership changed'):
        bundle.verify_approved_native_inputs(job, {})


@pytest.mark.parametrize('backend,key', [
    ('laproteina', 'laproteina_checkpoint_dir'),
    ('laproteina', 'laproteina_data_path'),
    ('disco', 'disco_checkpoint_path'),
    ('disco', 'disco_cutlass_path'),
])
def test_compiled_runtime_aliases_are_not_biological_inputs(roots, monkeypatch, backend, key):
    selected = roots['weights'] / 'selected'
    selected.mkdir()
    (selected / 'inert.bin').write_bytes(b'not biological input')
    invocation = compile_cad(roots, {'backend': backend, key: str(selected)}, True)
    assert invocation.native_parameters['pcad_' + key] == str(selected)
    assert key not in invocation.native_parameters
    monkeypatch.setattr(Path, 'rglob', lambda *_: pytest.fail('runtime tree must not be scanned'))
    assert discover(roots, invocation, invocation.native_parameters) == []
    assert bundle._input_assets(invocation.native_parameters, native_invocation=invocation,
        repo_root=roots['repo'], runtime_paths={roots['weights']},
        output_dir=roots['results'] / 'job') == []


def test_unselected_document_inputs_and_runtime_trees_stay_unselected(roots, monkeypatch):
    source = roots['inputs'] / 'cad.json'
    motif = roots['inputs'] / 'motif.pdb'
    motif.write_bytes(b'inert motif')
    checkpoint = roots['weights'] / 'selected'
    request = {'backend': 'laproteina', 'laproteina': {'motif_pdb': str(motif),
        'checkpoint_dir': str(checkpoint)}, 'disco': {
        'input_json_path': str(roots['inputs'] / 'unselected-missing.json'),
        'checkpoint_path': str(roots['weights'] / 'unselected-missing')}}
    source.write_text(json.dumps(request))
    monkeypatch.setattr(Path, 'rglob', lambda *_: pytest.fail('runtime tree must not be scanned'))
    refs = portable.discover_native_input_references('protein_cad_experimental', 'design',
        {'protein_cad_request': str(source)}, (), output_dir=roots['results'],
        allowed_roots=(roots['inputs'],), runtime_references={str(checkpoint): {
            'sha256': 'a' * 64, 'size_bytes': 42, 'format': 'runtime-directory',
            'path': '/worker/runtime/selected'}})
    assert {r['source_path'] for r in refs} == {str(source), str(motif), str(checkpoint)}
    assert [r['source_path'] for r in refs if r['role'] == 'runtime'] == [str(checkpoint)]
