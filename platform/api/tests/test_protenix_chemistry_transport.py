from copy import deepcopy
import json
from pathlib import Path

import pytest

from schemas import JobCreate
from services import nextflow
from scripts.protenix_chemistry import validate_chemistry
from scripts.prepare_protenix_msa import build_native_protenix_input, load_native_protenix_input


@pytest.fixture
def compiler_environment(monkeypatch):
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})


def science():
    return {
        'complex_components': [
            {'type': 'peptide', 'id': 'peptide', 'count': 2, 'copy_ids': ['A', 'B'],
             'sequence': 'YAQGTFTSDYSILLDKKAQAAFIEYLLEGG',
             'modifications': [{'ptmPosition': p, 'ptmType': c} for p, c in [(2, 'CCD_AIB'), (13, 'CCD_2ML'), (20, 'CCD_AIB')]],
             'unpairedMsa': '', 'pairedMsa': '', 'templatesPath': '/managed/hits.hhr'},
            {'type': 'dna', 'id': 'dna', 'sequence': 'AC', 'modifications': [{'basePosition': 1, 'modificationType': 'CCD_5CM'}]},
            {'type': 'rna', 'id': 'rna', 'sequence': 'AC', 'modifications': [{'basePosition': 2, 'modificationType': 'CCD_PSU'}], 'unpairedMsa': ''},
            {'type': 'ligand', 'id': 'ligand', 'smiles': '[CH3:1][OH:2]', 'count': 2},
            {'type': 'ion', 'id': 'ion', 'element': 'Zn'},
        ],
        'covalent_bonds': [{'left_entity': 1, 'right_entity': 4, 'left_position': 1, 'right_position': 1,
                            'left_atom': 'N', 'right_atom': 1, 'left_copy': 2, 'right_copy': 2}],
        'constraint': {'contact': [{'entity1': 1, 'entity2': 2, 'copy1': 1, 'copy2': 1,
                                    'position1': 1, 'position2': 1, 'max_distance': 8.0}],
                       'pocket': {'binder_chain': {'entity': 1, 'copy': 2},
                                  'contact_residues': [{'entity': 2, 'copy': 1, 'position': 1}], 'max_distance': 6.0}},
    }


def assert_native(native, params):
    assert native['covalent_bonds'] == params['covalent_bonds']
    assert native['constraint'] == params['constraint']
    assert len(native['sequences']) == len(params['complex_components'])
    for comp, wrapper in zip(params['complex_components'], native['sequences']):
        chain = next(iter(wrapper.values()))
        for key in ('modifications', 'unpairedMsa', 'pairedMsa', 'templatesPath'):
            if key in comp:
                assert chain[key] == comp[key]
    assert native['sequences'][0]['proteinChain']['id'] == ['A', 'B']
    assert 'id' not in native['sequences'][3]['ligand']


@pytest.mark.parametrize('batch', [False, True])
def test_real_compiler_materialization_load(batch, tmp_path, compiler_environment):
    params = science()
    params['protenix_use_msa'] = False
    if batch:
        params.update(sequence_batch_entries=[{'name': 'one', 'sequence': 'ACD'}, {'name': 'two', 'sequence': 'ACE'}])
    output = tmp_path / 'out'
    invocation = nextflow.compile_nextflow_invocation('protenix', 'complex', params, str(output))
    assert not output.exists()
    invocation.materialize_inputs(output)
    native = load_native_protenix_input(invocation.native_parameters)
    assert len(native) == (2 if batch else 1)
    for task in native:
        expected = deepcopy(params)
        if batch:  # Existing policy adds a binder; the original entity ordinals do not move.
            expected['complex_components'].append({'type': 'protein'})
        assert_native(task, expected)
    assert '--constraint' not in invocation.command


def test_request_reopen_clone_and_native_deep_copy():
    original = science()
    request = JobCreate(name='chemistry', model_id='protenix', mode='complex', params=original)
    for name in ('reopened', 'clone', 'resubmit-envelope'):
        data = json.loads(request.model_dump_json())
        data['name'] = name
        request = JobCreate.model_validate(data)
        assert request.params == original
    task = build_native_protenix_input(seeds=[42], complexes=[('chemistry', {'components': original['complex_components'], **{k: original[k] for k in ('covalent_bonds', 'constraint')}})])[0]
    assert_native(task, original)
    native = build_native_protenix_input(seeds=[99], native_payload=[task])
    native[0]['covalent_bonds'][0]['left_copy'] = 1
    assert task['covalent_bonds'][0]['left_copy'] == 2
    assert task['modelSeeds'] == [42]


@pytest.mark.parametrize('change', [
    {'count': 0}, {'count': '2'}, {'copy_ids': ['A']},
    {'modifications': [{'basePosition': 2, 'modificationType': 'CCD_AIB'}]},
    {'modifications': [{'ptmPosition': '2', 'ptmType': 'CCD_AIB'}]},
])
def test_scientific_errors_not_coercions(change):
    params = science()
    params['complex_components'][0].update(change)
    with pytest.raises(ValueError):
        JobCreate(name='invalid', model_id='protenix', mode='complex', params=params)
    # Not a new global unknown-settings gate for unrelated models.
    JobCreate(name='other', model_id='boltz2', mode='complex', params=params)


def test_inline_and_paths_msa_handoff_preserves_chemistry(tmp_path):
    from services.msa_preparation import export_protenix_inputs, materialize_protenix_inputs
    from biomodstack_msa_handoff import hydrate_prepared_protenix_task, digest
    params = science()
    native = build_native_protenix_input(seeds=[42], complexes=[('chemistry', {'components': params['complex_components'], **{k: params[k] for k in ('covalent_bonds', 'constraint')}})])
    path = tmp_path / 'supplied.a3m'
    path.write_text('>query\n' + params['complex_components'][0]['sequence'] + '\n')
    native[0]['sequences'][0]['proteinChain']['unpairedMsaPath'] = str(path)
    bundle = tmp_path / 'msa'
    export_protenix_inputs(native, bundle, {}, {'backend': 'supplied'})
    out = materialize_protenix_inputs(bundle, tmp_path / 'out.json', {})
    received = json.loads(out.read_text())
    rebound = received[0]['sequences'][0]['proteinChain'].pop('unpairedMsaPath')
    assert Path(rebound).read_bytes() == path.read_bytes()
    expected = deepcopy(native)
    expected[0]['sequences'][0]['proteinChain'].pop('unpairedMsaPath')
    assert received == expected
    hydrated = hydrate_prepared_protenix_task(native, bundle, digest((bundle / 'msa-inputs.json').read_bytes()))
    assert Path(hydrated[0]['sequences'][0]['proteinChain'].pop('unpairedMsaPath')).read_bytes() == path.read_bytes()
    assert hydrated == expected


def test_managed_file_is_compiler_artifact(tmp_path, compiler_environment, monkeypatch):
    import paths
    managed = tmp_path / 'inputs'
    managed.mkdir()
    (managed / 'ligand.sdf').write_text('owned ligand fixture\n')
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'inputs': managed})
    params = {'complex_components': [{'type': 'ligand', 'ligand_file': 'inputs/ligand.sdf'}], 'protenix_use_msa': False}
    request = JobCreate(name='file', model_id='protenix', mode='complex', params=params)
    output = tmp_path / 'out'
    invocation = nextflow.compile_nextflow_invocation('protenix', 'complex', request.params, str(output))
    invocation.materialize_inputs(output)
    native = load_native_protenix_input(invocation.native_parameters)
    ligand = native[0]['sequences'][0]['ligand']['ligand']
    assert ligand == 'FILE_' + str(output / 'protenix_ligands/1.sdf')
    assert Path(ligand[5:]).read_text() == 'owned ligand fixture\n'
    from scripts.lib.portable_inputs import discover_native_input_references
    references = discover_native_input_references('protenix', 'complex', invocation.native_parameters,
        invocation.generated_inputs, output_dir=output, allowed_roots=[tmp_path])
    assert any(item['role'] == 'ligand' and item['selector'] == ['components', 0, 'ligand_file']
               for item in references)
    # Rebind the exact discovered ligand on a worker; controller staged bytes
    # become unavailable, so accidental fallback to the controller cannot pass.
    from scripts.lib.portable_inputs import ENV, SCHEMA
    worker = tmp_path / 'worker'
    worker.mkdir()
    source = Path(ligand[5:])
    destination = worker / source.name
    destination.write_bytes(source.read_bytes())
    reference = next(item for item in references if item['role'] == 'ligand')
    binding = worker / 'bindings.json'
    binding.write_text(json.dumps({'schema': SCHEMA, 'roots': [str(worker)],
        'bindings': [{'reference': reference, 'path': str(destination)}]}))
    source.unlink()
    monkeypatch.setenv(ENV, str(binding))
    rebound = load_native_protenix_input(invocation.native_parameters)
    assert rebound[0]['sequences'][0]['ligand']['ligand'] == 'FILE_' + str(destination)
    assert destination.read_text() == 'owned ligand fixture\n'
    params['complex_components'][0]['ligand_file'] = '/etc/passwd'
    with pytest.raises(ValueError, match='managed file selection'):
        JobCreate(name='bad', model_id='protenix', mode='complex', params=params)


def test_model_discovery_nested_contract():
    from routers.models import _native_parameter_schema
    schema = _native_parameter_schema('protenix', 'complex')
    assert schema['schema_version'] == 1
    assert set(schema['properties']) == {'complex_components', 'covalent_bonds', 'constraint'}
    assert 'ligand_file' in schema['$defs']['Component']['properties']
    assert 'ptmPosition' in schema['$defs']['ProteinModification']['properties']
