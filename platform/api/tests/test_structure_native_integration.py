"""Real CPU compiler/materialization boundaries, without providers or inference."""
import json
from pathlib import Path

import pytest
from schemas import JobCreate
from services import nextflow
from test_protenix_chemistry_transport import compiler_environment


@pytest.mark.parametrize('mode', ['predict', 'complex'])
@pytest.mark.parametrize('seed', [None, 0, 17])
def test_boltz_seed_catalog_and_compiler_preserve_native_default(mode, seed, tmp_path, compiler_environment):
    from model_registry import get_registry
    registry = get_registry()
    definition = registry.get_model('boltz2')
    assert definition is not None
    field = next(p for p in definition.params if p.name == 'boltz_seed')
    assert field.default is None and field.required is False
    assert 'boltz_seed' in next(m for m in definition.modes if m.id == mode).params
    params = {'sequence': 'ACG', 'boltz_use_msa': False, 'run_frustrampnn': False,
              'boltz_seed': seed}
    assert registry.validate_job_params('boltz2', mode, params) == []
    invocation = nextflow.compile_nextflow_invocation('boltz2', mode, params, str(tmp_path))
    assert invocation.native_parameters.get('boltz_seed') == seed


def test_boltz_native_copies_and_msa_intent():
    from scripts.lib.boltz_fidelity import component_copies
    from services.model_msa_handoff import _boltz_task_proteins
    components = [
        {'type': 'peptide', 'id': 'row', 'copy_ids': ['Z', 'Y'], 'count': 3, 'sequence': 'ACG', 'msa': ''},
        {'type': 'protein', 'id': 'A', 'sequence': 'ACG', 'msa': 'empty'},
        {'type': 'peptide', 'id': 'L', 'sequence': 'ACG', 'msa': 'supplied.a3m', 'msa_path': 'ignored.a3m'},
    ]
    copies = component_copies(components)
    assert copies[0]['id'] == ['Z', 'Y', 'B']
    proteins = _boltz_task_proteins({'components': components}, {})
    assert [p['id'] for p in proteins] == [c['id'] for c in copies]
    assert [p['msa'] for p in proteins] == ['', 'empty', 'supplied.a3m']
    components[0]['count'] = 1
    assert component_copies(components)[0]['id'] == ['Z', 'Y']


@pytest.mark.parametrize('batch', [False, True])
def test_esm_native_task_envelope(batch, tmp_path, compiler_environment):
    from scripts.run_esmfold2_inference import compile_workflow_request
    params = {'esmf_use_msa': False, 'seed': 0, 'model_variant': 'full',
        'complex_components': [{'type': 'protein', 'id': ['Z', 'Y'], 'count': 2,
                                'sequence': 'ACG', 'msa': None, 'modifications': [{'position': 1, 'ccd': 'CSO'}]}],
        'esmf_covalent_bonds': [{'chain_id1': 'Z', 'res_idx1': 1, 'atom_idx1': 5,
                                'chain_id2': 'Y', 'res_idx2': 1, 'atom_idx2': 5}],
        'esmf_distogram_conditioning': [{'chain_id': 'Z', 'distogram': [[0, 2, 3], [2, 0, 4], [3, 4, 0]]}]}
    if batch:
        params.update(sequence_batch_component_id='Z', sequence_batch_entries=[{'name': 'one', 'sequence': 'ACG'}])
    invocation = nextflow.compile_nextflow_invocation('esmfold2', 'predict', params, str(tmp_path))
    invocation.materialize_inputs(tmp_path)
    document = json.loads((tmp_path / 'esmfold2_complex_components.json').read_text())
    assert document['covalent_bonds'] == params['esmf_covalent_bonds']
    assert document['distogram_conditioning'] == params['esmf_distogram_conditioning']
    argv, receipt = compile_workflow_request({'core_protein_scientific_contract': 1,
        'complex_components': document, 'seed': 0}, {})
    assert receipt['settings']['seed']['effective'] == 0
    native = json.loads(argv[argv.index('--complex-components-json') + 1])
    assert native['covalent_bonds'] == document['covalent_bonds']
    assert native['components'][0]['msa'] is None
    if batch:
        variant = json.loads(next((tmp_path / 'complex_batch_inputs').glob('*.json')).read_text())
        assert variant['covalent_bonds'] == document['covalent_bonds']


@pytest.mark.parametrize('method,batch', [('boltz', False), ('boltz_protenix', False), ('boltz', True)])
def test_boltz_native_materialization(method, batch, tmp_path, compiler_environment):
    import importlib.util
    spec = importlib.util.spec_from_file_location('boltz_native_probe', Path(__file__).resolve().parents[3] / 'tests/test_boltz_fidelity.py')
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    params = {'pred_method': method, 'boltz_use_msa': False, 'protenix_use_msa': False, 'boltz_seed': 0,
              'complex_components': [{'type': 'protein', 'id': 'row', 'copy_ids': ['Z', 'Y'], 'count': 3,
                                      'sequence': 'ACG', 'msa': 'empty', 'cyclic': True,
                                      'modifications': [{'position': 2, 'ccd': 'CSO'}]},
                                     {'type': 'ligand', 'id': 'L', 'ccd': 'ATP'}],
              'constraints': [{'bond': {'atom1': ['Z', 2, 'SG'], 'atom2': ['Y', 2, 'SG']}}],
              'properties': [{'affinity': {'binder': 'L'}}]}
    if batch:
        params.update(sequence_batch_component_id='Z', sequence_batch_entries=[{'name': 'one', 'sequence': 'ACG'}])
    request = JobCreate(name='native-positive', model_id='boltz2', mode='complex', params=params)
    invocation = nextflow.compile_nextflow_invocation(request.model_id, request.mode, request.params, str(tmp_path))
    invocation.materialize_inputs(tmp_path)
    document_path = next((tmp_path / 'complex_batch_inputs').glob('*.json')) if batch else tmp_path / 'complex_definition.json'
    document = json.loads(document_path.read_text())
    native = probe.convert(document, tmp_path / 'native')
    assert native['sequences'][0]['protein']['id'] == ['Z', 'Y', 'A']
    assert native['sequences'][0]['protein']['cyclic'] is True
    assert native['constraints'] == params['constraints']
    assert native['properties'] == params['properties']
    assert str(invocation.native_parameters['boltz_seed']) == '0'
    import os
    if os.environ.get('BMS_NATIVE_WIRE_EVIDENCE'):
        target = Path(os.environ['BMS_NATIVE_WIRE_EVIDENCE']) / 'integration/backend-followup' / f'positive-{method}-{batch}'
        target.mkdir(parents=True, exist_ok=True)
        for name, value in [('request', request.model_dump(mode='json')), ('compiled', invocation.native_parameters), ('document', document), ('native', native)]:
            (target / (name + '.json')).write_text(json.dumps(value, indent=2, default=str))


@pytest.mark.parametrize('inline', [None, {'sequences': []}])
def test_esm_inline_msa_never_searches(inline, tmp_path, monkeypatch):
    from services.model_msa_handoff import prepare_esmfold2_msa
    def forbidden(**kwargs):
        raise AssertionError('No provider search permitted')
    monkeypatch.setattr('services.msa_preparation.prepare_model_msa', forbidden)
    params = {'core_protein_scientific_contract': 1, 'model_variant': 'full', 'esmf_use_msa': True,
              'msa_provider': 'colabfold_api', 'colabfold_pairing_mode': 'unpaired',
              'complex_components': {'components': [{'type': 'protein', 'id': ['Z', 'Y'], 'sequence': 'ACG', 'msa': inline}],
                                     'covalent_bonds': [], 'distogram_conditioning': []}}
    result = prepare_esmfold2_msa(params, tmp_path)
    assert result['complex_components'] == params['complex_components']


def test_model_owned_discovery_dialects():
    from routers.models import _native_parameter_schema
    from jsonschema import Draft202012Validator
    for model, position in [('boltz2', 1), ('esmfold2', 0)]:
        schema = _native_parameter_schema(model, 'predict')
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate({'complex_components': [
            {'type': 'protein', 'id': ['Z', 'Y'], 'count': 2, 'sequence': 'ACG',
             'modifications': [{'position': position, 'ccd': 'CSO'}]}]})
    assert 'constraints' not in _native_parameter_schema('esmfold2', 'predict')['properties']


def test_captured_frontend_requests(tmp_path, compiler_environment):
    """Opt-in external captures, retaining exact errors rather than blessing invalid examples."""
    import os
    evidence = os.environ.get('BMS_NATIVE_WIRE_EVIDENCE')
    if not evidence:
        pytest.skip('external frontend capture directory not selected')
    from jsonschema import Draft202012Validator
    from routers.models import _native_parameter_schema
    root = Path(evidence)
    output = root / 'integration/backend-followup'
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for capture in sorted((root / 'frontend/wire').glob('*.json')):
        data = json.loads(capture.read_text())
        for index, payload in enumerate(data.get('posts', [data] if 'model_id' in data else [])):
            name = capture.stem + '-' + str(index)
            target = output / name
            target.mkdir(exist_ok=True)
            (target / 'request.json').write_text(json.dumps(payload, indent=2))
            record = {'capture': capture.name, 'index': index}
            try:
                request = JobCreate.model_validate(payload)
            except Exception as exc:
                record['request_envelope_error'] = str(exc)
                results.append(record)
                continue
            record['request_envelope'] = 'accepted'
            schema = _native_parameter_schema(request.model_id, request.mode)
            if request.model_id in {'boltz2', 'esmfold2'}:
                record['native_schema_errors'] = [e.message for e in Draft202012Validator(schema).iter_errors(request.params)]
            try:
                invocation = nextflow.compile_nextflow_invocation(request.model_id, request.mode, request.params, str(target / 'inputs'))
                invocation.materialize_inputs(target / 'inputs')
                (target / 'compiled.json').write_text(json.dumps(invocation.native_parameters, indent=2, default=str))
                record['compiler'] = 'materialized'
                record['generated_inputs'] = [i.relative_path for i in invocation.generated_inputs]
                if request.model_id == 'boltz2' and not record.get('native_schema_errors'):
                    import importlib.util
                    spec = importlib.util.spec_from_file_location('boltz_wire_probe', Path(__file__).resolve().parents[3] / 'tests/test_boltz_fidelity.py')
                    probe = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(probe)
                    document = json.loads((target / 'inputs/complex_definition.json').read_text())
                    native = probe.convert(document, target / 'native-prep')
                    (target / 'native.json').write_text(json.dumps(native, indent=2))
                    record['native_boundary'] = 'actual PrepareComplexWithMSA Python block; no parser/inference'
                elif request.model_id == 'esmfold2':
                    from scripts.run_esmfold2_inference import compile_workflow_request
                    for item in invocation.generated_inputs:
                        if item.relative_path.startswith('complex_batch_inputs/'):
                            document = json.loads(item.payload)
                            _, receipt = compile_workflow_request({'core_protein_scientific_contract': 1,
                                'complex_components': document, 'seed': request.params.get('seed')}, {})
                            (target / (Path(item.relative_path).stem + '-native.json')).write_text(json.dumps(receipt, indent=2))
                    record['native_boundary'] = 'actual ESM runner argv compiler for each materialized batch member'
            except Exception as exc:
                record['compiler_error'] = str(exc)
            results.append(record)
    (output / 'captured-results.json').write_text(json.dumps(results, indent=2))
    assert results
