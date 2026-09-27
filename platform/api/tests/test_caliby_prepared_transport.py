"""CPU-only retained Caliby input custody/bridge tests; no scientific execution."""
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from component_runtime import NativeInvocation
from services.caliby_native import prepare_for_job, read_prepared_request, science_params
from services.remote_execution import bundle
from scripts.lib.portable_inputs import discover_native_input_references, resolve_input_path
from test_remote_bundle_path_gaps import roots  # noqa: F401


@pytest.fixture(params=['ensemble_design', 'sidechain_pack'])
def case(request, roots):
    mode = request.param
    sources = []
    for name in ['one', 'two']:
        source = roots['inputs'] / name / 'same.cif'
        source.parent.mkdir()
        source.write_text('data_INERT_' + name + '\n')
        sources.append({'state_id': name, 'path': str(source)})
    params = {'num_workers': 0, 'scn_step_scale': 0}
    if mode == 'ensemble_design':
        params.update(ensembles=[{'ensemble_id': 'group', 'states': sources}],
                      omit_aas=[], verbose=False, use_primary_res_type=False)
    else:
        params['structures'] = sources
    output = roots['results'] / 'job'
    output.mkdir()
    return mode, science_params(mode, params), sources, output


def invocation(mode, params):
    return NativeInvocation.capture(model_id='caliby_experimental', mode=mode,
        command=['nextflow'], requested=params, effective=params, native_parameters=params,
        entrypoint='workflows/caliby_native.nf')


def assets(roots, mode, params, output, references=None):
    return bundle._input_assets(params, native_invocation=invocation(mode, params),
        repo_root=roots['repo'], runtime_paths=set(), output_dir=output, references=references)


def prepare(case, roots):
    mode, params, sources, output = case
    transport = prepare_for_job(mode, params, output / 'request', allowed_roots=[roots['inputs'], roots['results']])
    return {**params, **transport}, Path(transport['caliby_request_dir'])


def test_raw_discovery_and_exact_mode_scope(case, roots):
    mode, params, sources, output = case
    refs = discover_native_input_references('caliby_experimental', mode, params, (),
        output_dir=output, allowed_roots=[roots['inputs'], roots['results']])
    assert {r['source_path'] for r in refs} == {s['path'] for s in sources}
    assert {p for p, _ in assets(roots, mode, params, output)} == {Path(s['path']) for s in sources}
    assert discover_native_input_references('caliby_experimental', 'design', params, (),
        output_dir=output, allowed_roots=[roots['inputs'], roots['results']]) == []


def test_offline_retention_copy_and_relocation_hash_inventory(case, roots, tmp_path, monkeypatch):
    mode, original, sources, output = case
    params, root = prepare(case, roots)
    before = {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in root.rglob('*') if p.is_file()}
    for source in sources:
        Path(source['path']).unlink()
    retained = prepare_for_job(mode, params, output / 'unused', allowed_roots=[roots['inputs'], roots['results']],
                               retain_prepared=True)
    assert retained == {'caliby_request_dir': str(root)}
    assert not (output / 'unused').exists()
    assert before == {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in root.rglob('*') if p.is_file()}
    document = read_prepared_request(mode, params, root, allowed_roots=[roots['inputs'], roots['results']])
    assert document['requested'] == original
    assert len({s['path'] for s in document['sources']}) == len(sources)
    copied = prepare_for_job(mode, params, output / 'copied', allowed_roots=[roots['inputs'], roots['results']])
    assert read_prepared_request(mode, params, copied['caliby_request_dir']) == document

    refs = []
    selected = assets(roots, mode, params, output, refs)
    assert len(selected) == 1 and selected[0][0] == root
    assert {Path(ref['source_path']) for ref in refs} == {root / p for p in before}
    relative = selected[0][1]
    prefix = 'inputs/' + relative
    records = bundle._input_records(root, prefix, native_invocation=invocation(mode, params), output_dir=output)
    # Every approved member must actually occur in the final transfer hash inventory.
    inventory = {str(root / rec.relative_path[len(prefix):].lstrip('/')):
                 (rec.sha256, rec.size_bytes) for rec in records}
    assert set(inventory) == {ref['source_path'] for ref in refs}
    for ref in refs:
        assert inventory[ref['source_path']] == (ref['sha256'], ref['size_bytes'])
    job = SimpleNamespace(provenance={'execution_plan_approval': {
        'input_request': {'model_id': 'caliby_experimental', 'mode': mode,
                          'params': params, 'output_dir': str(output)}, 'input_identities': refs}})
    bundle.verify_approved_native_inputs(job, {}, inventory)
    attempt = tmp_path / 'worker'
    remote = attempt / 'bundle' / prefix
    shutil.copytree(root, remote)
    staging = tmp_path / 'staging'
    staging.mkdir()
    binding, _ = bundle._write_portable_bindings(staging_root=staging, remote_attempt=str(attempt),
        references=refs, input_transfers=[bundle.TransferPlan(root, str(remote))], input_records=records,
        remote_runtime=str(attempt / 'runtime'), remote_results=str(attempt / 'results'))
    monkeypatch.setenv('BMS_PORTABLE_INPUT_BINDINGS', str(binding.source))
    shutil.rmtree(root)
    assert resolve_input_path(root) == remote
    assert read_prepared_request(mode, params, remote, allowed_roots=[attempt]) == document
    for member, (data, _) in before.items():
        assert resolve_input_path(root / member).read_bytes() == data
    member = Path(document['sources'][0]['path'])
    (remote / member).write_bytes(b'changed')
    with pytest.raises(ValueError, match='digest/size mismatch'):
        resolve_input_path(root / member)


def test_requested_effective_and_coherent_byte_tampering(case, roots):
    mode, _, _, output = case
    params, root = prepare(case, roots)
    refs = []
    assets(roots, mode, params, output, refs)
    job = SimpleNamespace(provenance={'execution_plan_approval': {
        'input_request': {'model_id': 'caliby_experimental', 'mode': mode,
                          'params': params, 'output_dir': str(output)}, 'input_identities': refs}})
    request = root / 'request.json'
    original = request.read_bytes()
    doc = json.loads(original)
    doc['requested']['scn_step_scale'] = doc['effective']['scn_step_scale'] = 2
    request.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='selected settings'):
        read_prepared_request(mode, params, root)
    request.write_bytes(original)
    doc = json.loads(original)
    doc['effective']['num_workers'] = 1
    request.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='effective settings'):
        read_prepared_request(mode, params, root)
    request.write_bytes(original)
    doc = json.loads(original)
    source = root / doc['sources'][0]['path']
    source.write_bytes(b'data_CHANGED\n')
    with pytest.raises(ValueError, match='bytes changed'):
        read_prepared_request(mode, params, root)
    doc['sources'][0]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    request.write_text(json.dumps(doc))
    assert read_prepared_request(mode, params, root) == doc
    # Internal consistency is not saved approval; shared byte custody remains owner.
    with pytest.raises(bundle.RemoteBundleError, match='bytes or membership changed'):
        bundle.verify_approved_native_inputs(job, {})


def test_real_input_assets_containment_and_no_missing_tree_fallback(case, roots, tmp_path):
    mode, _, sources, output = case
    params, root = prepare(case, roots)
    (root / 'escape.cif').symlink_to(Path(sources[0]['path']))
    with pytest.raises(bundle.RemoteBundleError, match='symlink'):
        assets(roots, mode, params, output)
    (root / 'escape.cif').unlink()
    outside = tmp_path / 'unowned'
    shutil.copytree(root, outside)
    with pytest.raises(bundle.RemoteBundleError, match='outside BMS-managed storage'):
        assets(roots, mode, {**params, 'caliby_request_dir': str(outside)}, output)
    shutil.rmtree(root)
    with pytest.raises(bundle.RemoteBundleError, match='unavailable'):
        assets(roots, mode, params, output)
    with pytest.raises(FileNotFoundError):
        prepare_for_job(mode, params, output / 'replacement', allowed_roots=[roots['inputs'], roots['results']])
    assert not (output / 'replacement').exists()


def test_full_bundle_inventory_contains_every_retained_member(case, roots, tmp_path, monkeypatch):
    """Real bundle with a CPU transport projection, not release/compiler proof."""
    from dataclasses import replace
    from component_runtime import SourceIdentity
    from test_remote_cache_integration import cache_only_plan_fixture
    from test_remote_bundle_runtime_gaps import bundle_assignment_fixture

    mode, _, sources, output = case
    params, root = prepare(case, roots)
    refs = []
    assets(roots, mode, params, output, refs)
    for source in sources:
        Path(source['path']).unlink()
    repo = Path(__file__).resolve().parents[3]
    identity = SourceIdentity.from_checkout(repo)
    command = ['nextflow', 'run', str(repo / 'workflows/caliby_native.nf'),
               '--caliby_request_dir', str(root), '--out_dir', str(output)]
    native = cache_only_plan_fixture(replace(NativeInvocation.capture(
        model_id='caliby_experimental', mode=mode, command=command, requested=params,
        effective=params, native_parameters=params, entrypoint='workflows/caliby_native.nf'),
        source_identity=identity))
    # Use real committed archive bytes; candidate source/release binding and runtime
    # provisioning have separate owners. No fixture runtime is executed.
    monkeypatch.setattr(bundle, 'get_code_root', lambda: repo)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: (identity.revision, identity.tree))
    monkeypatch.setattr(bundle, '_runtime_assets', lambda *_, **__: [])
    approval = {'input_request': {'model_id': 'caliby_experimental', 'mode': mode,
                'params': params, 'output_dir': str(output)}, 'input_identities': refs}
    job = SimpleNamespace(id='job', model_id='caliby_experimental', mode=mode,
        params=params, output_dir=str(output), child_output_dir=None, lineage_root_job_id=None,
        parent_job_id=None, execution_source_revision=identity.revision,
        execution_source_tree=identity.tree, assigned_gpu=None,
        stage_family=None, stage_mode=mode, selected_input_artifact_class=None,
        provenance={**bundle_assignment_fixture(()), 'execution_plan_approval': approval})
    inventories = []
    verify = bundle.verify_approved_native_inputs
    def observe(job, runtime_references, input_hashes=None):
        if input_hashes is not None:
            inventories.append(dict(input_hashes))
        return verify(job, runtime_references, input_hashes)
    monkeypatch.setattr(bundle, 'verify_approved_native_inputs', observe)
    dispatch = bundle.prepare_remote_bundle(job=job,
        target=SimpleNamespace(id='target', remote_root=str(tmp_path / 'remote')),
        command=command, native_invocation=native)
    assert inventories
    files = {record.relative_path: record for record in dispatch.envelope.files}
    destination = dispatch.envelope.path_map[str(root)]
    prefix = destination.removeprefix(dispatch.remote_attempt_dir + '/bundle/')
    for reference in refs:
        expected = (reference['sha256'], reference['size_bytes'])
        assert inventories[-1][reference['source_path']] == expected
        relative = Path(reference['source_path']).relative_to(root).as_posix()
        record = files[prefix + '/' + relative]
        assert (record.sha256, record.size_bytes) == expected
    assert all(source['path'] not in dispatch.envelope.path_map for source in sources)


def test_source_containment_and_retained_path_escape(case, roots, tmp_path):
    mode, original, sources, output = case
    source = Path(sources[0]['path'])
    data = source.read_bytes()
    source.unlink()
    outside = tmp_path / 'outside.cif'
    outside.write_bytes(data)
    source.symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        prepare_for_job(mode, original, output / 'bad', allowed_roots=[roots['inputs'], roots['results']])
    source.unlink()
    source.write_bytes(data)
    params, root = prepare(case, roots)
    doc = json.loads((root / 'request.json').read_bytes())
    doc['sources'][0]['path'] = '../outside.cif'
    (root / 'request.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='binding changed'):
        read_prepared_request(mode, params, root)
