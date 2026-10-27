"""Offline preview/compiler/portable-input parity; no model or service runs."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from schemas import JobCreate
from services import nextflow
from routers.jobs import _execution_plan_preview
from scripts.lib import portable_inputs


@pytest.fixture
def roots(tmp_path, monkeypatch):
    import paths

    data = tmp_path / 'data'
    inputs = data / 'inputs'
    results = data / 'results'
    inputs.mkdir(parents=True)
    results.mkdir()
    for module in (paths, nextflow):
        for name, value in [('get_data_root', data), ('get_inputs_dir', inputs),
                            ('get_results_dir', results)]:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, lambda value=value: value)
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    monkeypatch.delenv(portable_inputs.ENV, raising=False)
    return data, inputs, results


def request_for(model, components, form):
    if form != 'list':
        components = {form: components}
    return JobCreate(name='nested-input-parity', model_id=model, mode='predict', params={
        'model_variant': 'full', 'esmf_use_msa': False, 'run_frustrampnn': False,
        'complex_components': components,
    })


def compile_and_discover(request, roots):
    preview_invocation = nextflow.compile_workflow_provision_request(request)
    output = Path(preview_invocation.native_parameters['out_dir'])
    invocation = nextflow.compile_nextflow_invocation(
        request.model_id, request.mode, request.params, str(output))
    assert invocation.generated_inputs == preview_invocation.generated_inputs
    invocation.materialize_inputs(output)
    refs = portable_inputs.discover_native_input_references(
        invocation.model_id, invocation.mode, invocation.native_parameters,
        invocation.generated_inputs, output_dir=output, allowed_roots=roots)
    return output, refs


@pytest.mark.parametrize('model', ['esmfold2', 'esmfold2_experimental'])
@pytest.mark.parametrize('form', ['list', 'components', 'sequences'])
def test_preview_records_nested_msa_and_compiler_relocates(roots, tmp_path, monkeypatch, model, form):
    components = []
    expected = []
    # Same basenames and duplicate bytes must retain each selected chain slot.
    for index, sequence in enumerate(('ACDE', 'ACDE', 'FGHI')):
        path = roots[1] / str(index) / 'alignment.a3m'
        path.parent.mkdir()
        payload = f'>query\n{sequence}\n'.encode()
        path.write_bytes(payload)
        components.append({'type': 'protein', 'id': chr(65 + index),
                           'sequence': sequence, 'msa_path': str(path)})
        expected.append((str(path), hashlib.sha256(payload).hexdigest(), len(payload)))
    components.append({'type': 'protein', 'id': 'D', 'sequence': 'KLMN'})
    request = request_for(model, components, form)
    original = copy.deepcopy(request.model_dump())
    preview = _execution_plan_preview(request)
    refs = preview['input_identities']
    assert preview['admissible'] is True
    assert len(refs) == 3
    assert [(r['source_path'], r['sha256'], r['size_bytes']) for r in refs] == expected
    prefix = ['complex_components'] + ([] if form == 'list' else [form])
    assert [r['selector'] for r in refs] == [prefix + [i, 'msa_path'] for i in range(3)]
    assert all(r['role'] == 'msa' and r['owner'] == 'params' for r in refs)
    assert len({r['logical_id'] for r in refs}) == 3
    assert request.model_dump() == original
    assert not list(roots[2].iterdir()), 'preview must not materialize generated inputs'
    assert _execution_plan_preview(request) == preview

    source = Path(expected[0][0])
    original_bytes = source.read_bytes()
    source.write_bytes(original_bytes + b'\n')
    changed = _execution_plan_preview(request)
    assert changed['approval_digest'] != preview['approval_digest']
    assert changed['generated_inputs'] == preview['generated_inputs']
    source.write_bytes(original_bytes)

    output, packaged = compile_and_discover(request, roots)
    msa_refs = [r for r in packaged if r['role'] == 'msa']
    assert [(r['source_path'], r['sha256'], r['size_bytes']) for r in msa_refs] == expected
    document_path = output / 'esmfold2_complex_components.json'
    document_bytes = document_path.read_bytes()
    document = json.loads(document_bytes)
    assert document['components'] == components
    worker = tmp_path / 'worker'
    worker.mkdir()
    bindings = []
    for index, ref in enumerate(msa_refs):
        source = Path(ref['source_path'])
        destination = worker / f'{index}.a3m'
        destination.write_bytes(source.read_bytes())
        bindings.append({'reference': ref, 'path': str(destination)})
        source.unlink()
    binding_path = worker / 'bindings.json'
    binding_path.write_text(json.dumps({'schema': portable_inputs.SCHEMA,
        'roots': [str(worker)], 'bindings': bindings}))
    monkeypatch.setenv(portable_inputs.ENV, str(binding_path))
    rebound = portable_inputs.bind_native_document(document, 'boltz-complex')
    for index, component in enumerate(rebound['components'][:3]):
        assert component == {**components[index], 'msa_path': str(worker / f'{index}.a3m')}
        assert hashlib.sha256(Path(component['msa_path']).read_bytes()).hexdigest() == expected[index][1]
    assert rebound['components'][3] == components[3]
    assert document_path.read_bytes() == document_bytes
    assert request.model_dump() == original


@pytest.mark.parametrize('model', ['esmfold2', 'esmfold2_experimental'])
@pytest.mark.parametrize('kind', ['outside', 'symlink'])
def test_preview_and_packaging_share_existing_containment_diagnostic(roots, tmp_path, model, kind):
    outside = tmp_path / 'outside.a3m'
    outside.write_text('>query\nACDE\n')
    selected = outside
    if kind == 'symlink':
        selected = roots[1] / 'alias.a3m'
        selected.symlink_to(outside)
    request = request_for(model, [{'type': 'protein', 'id': 'A', 'sequence': 'ACDE',
                                  'msa_path': str(selected)}], 'list')
    # Existing containment resolves symlinks, then checks the same approved roots.
    message = 'outside approved roots'
    with pytest.raises(ValueError, match=message) as preview_error:
        _execution_plan_preview(request)
    assert not list(roots[2].iterdir())
    with pytest.raises(ValueError, match=message) as packaging_error:
        compile_and_discover(request, roots)
    assert str(preview_error.value) == str(packaging_error.value)


def test_absent_empty_and_provenance_paths_are_not_inputs(roots):
    components = [{'type': 'protein', 'id': 'A', 'sequence': 'ACDE'},
                  {'type': 'protein', 'id': 'B', 'sequence': 'FGHI', 'msa_path': ''}]
    params = {'complex_components': components,
              'requested_settings': {'complex_components': [{'msa_path': '/not/an/input.a3m'}]}}
    refs = portable_inputs.discover_native_input_references(
        'esmfold2', 'predict', params, (), output_dir=roots[2], allowed_roots=roots)
    assert refs == []
    components[1]['msa_path'] = 'empty'
    assert portable_inputs.discover_native_input_references(
        'esmfold2', 'predict', params, (), output_dir=roots[2], allowed_roots=roots) == []
