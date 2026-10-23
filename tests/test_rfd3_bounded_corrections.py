"""Bounded native-source/transport fixtures; no weights, GPU or live Job state."""
import ast
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.lib.rfd3_scientific_settings import batch_plan, task_dispatch
from scripts.rfd3_generation.contract import validate_request
from scripts.rfd3_generation.run_native import commands
from scripts.rfd3_generation.install_schedule_return import patch_source, PATCHED_SHA256
from scripts.rfd3_local_redesign.contract import build_request
from scripts.rfd3_local_redesign.source_mapping import materialize_source, lower_native
from scripts.shape_blueprint.install_rfd3_shape_extension import patch_sampler_source


def load_script(name, relative, contract):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get('contract')
    sys.modules['contract'] = contract
    try:
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop('contract', None)
        else:
            sys.modules['contract'] = previous
    return module


def generation(count=2):
    return {'schema': 'bms.rfd3.generation.request.v1', 'request_id': 'r', 'job_id': 'j',
            'generation': {'min_length': 1, 'max_length': 2, 'num_designs': count},
            'execution': {'seed': 0, 'dump_trajectories': False}}


@pytest.mark.parametrize('count', [200, 201, 512, 1001])
def test_positive_native_domain_has_no_product_cap(count):
    request = generation(count)
    request['generation']['max_length'] = 5000
    assert validate_request(request) == request


def test_batch_remainder_and_native_explicit_values():
    assert batch_plan(5, 2) == [{'diffusion_batch_size': 2, 'n_batches': 2}, {'diffusion_batch_size': 1, 'n_batches': 1}]
    request = generation(5)
    request['execution'].update(diffusion_batch_size=2, inference_sampler={'num_timesteps': 73, 'cfg_t_max': None}, low_memory_mode=True)
    calls = commands(validate_request(request), 'native.json', 'out')
    assert len(calls) == 2
    assert 'n_batches=2' in calls[0] and 'diffusion_batch_size=1' in calls[1]
    assert 'inference_sampler.num_timesteps=73' in calls[0]
    assert 'inference_sampler.cfg_t_max=null' in calls[0]
    assert 'low_memory_mode=true' in calls[0]
    assert 'global_prefix=bms_part0_' in calls[0]
    assert 'global_prefix=bms_part1_' in calls[1]
    assert 'seed=0' in calls[1]


def local(mode='minimal_insertion', **kwargs):
    return dict(input_structure='source.cif', redesign_mode=mode, design_chains=['A'],
                context_chains=['B', 'C'], insertion_anchor='A2', insertion_min_length=1,
                insertion_max_length=2, redesign_ranges='A2',
                source_residue_identities=[{'chain_id': c, 'residues': [
                    {'res_num': r, 'insertion_code': '', 'residue_name': 'ALA'} for r in values]}
                    for c, values in [('A', [2, 5]), ('B', [7, 9]), ('C', [4])]], **kwargs)


def test_context_boundaries_and_legacy_replay():
    params = local(select_hotspots=['B7', 'C4'])
    new = build_request(params)
    old = build_request(params, legacy_native=True)
    assert new['rfd3']['contig'] == 'A2,1-2,A5,/0,B7,B9,/0,C4'
    assert old['rfd3']['contig'] == 'A2,1-2,A5,/0,B7-9,C4'
    assert new['rfd3']['select_hotspots'] == 'B7,C4'
    assert old['rfd3']['select_hotspots'] == ['B7', 'C4']


@pytest.mark.parametrize('mode', ['partial_diffusion', 'minimal_insertion'])
@pytest.mark.parametrize('value', ['omitted', None, 0, 2])
def test_dispatch_preserves_contradictory_partial_intent(mode, value):
    params = local(mode)
    if value != 'omitted':
        params['partial_t'] = value
    request = build_request(params)
    dispatch = task_dispatch(mode, request['rfd3'])
    partial = mode == 'partial_diffusion' or value in (0, 2)
    assert dispatch['effective'] == ('partial_diffusion' if partial else 'contig')
    assert dispatch['conflicting_insertion_partial'] == (mode == 'minimal_insertion' and partial)


def test_native_recall_independent_sequence_and_coordinates():
    params = local('native_recall', contig='A2,A5,/0,B7,B9,/0,C4', sequence_policy='explicit_positions', select_unfixed_sequence='B7', select_fixed_atoms={'A2': []})
    request = build_request(params)
    assert request['rfd3']['select_unfixed_sequence'] == 'B7'
    assert request['rfd3']['select_fixed_atoms'] == {'A2': []}
    assert 'partial_t' not in request['rfd3']


def cif_source():
    tags = ['group_PDB', 'id', 'type_symbol', 'label_atom_id', 'label_alt_id', 'label_comp_id', 'label_asym_id', 'label_entity_id', 'label_seq_id', 'pdbx_PDB_ins_code', 'Cartn_x', 'Cartn_y', 'Cartn_z', 'occupancy', 'B_iso_or_equiv', 'auth_seq_id', 'auth_asym_id', 'auth_atom_id', 'pdbx_PDB_model_num']
    rows = []
    for i, (chain, residue, insertion, component, atom, group) in enumerate([
        ('A', '2', '?', 'ALA', 'CA', 'ATOM'), ('A', '2', 'A', 'GLY', 'CA', 'ATOM'),
        ('AA', '2', '?', 'ALA', 'CA', 'ATOM'), ('B', '9', '?', 'ZN', 'ZN', 'HETATM'),
        ('C', '4', '?', 'DA', 'P', 'ATOM')], 1):
        rows.append(f'{group} {i} C {atom} . {component} {chain} {i} {i} {insertion} {i}.0 0 0 1 0 {residue} {chain} {atom} 1')
    return 'data_source\nloop_\n' + '\n'.join('_atom_site.' + t for t in tags) + '\n' + '\n'.join(rows) + '\n#\n'


def test_injective_mapping_preserves_original_atoms_and_selected_chemistry(tmp_path):
    source = tmp_path / 'original.cif'
    source.write_text(cif_source() + "loop_\n_struct_conn.id\n_struct_conn.conn_type_id\n"
        "_struct_conn.ptnr1_auth_asym_id\n_struct_conn.ptnr1_auth_seq_id\n"
        "_struct_conn.ptnr2_auth_asym_id\n_struct_conn.ptnr2_auth_seq_id\n"
        "bond1 metalc B 9 A 2\n#\n")
    before = source.read_bytes()
    derived = tmp_path / 'native.cif'
    mapping = materialize_source(source, derived)
    identities = [(a['native']['chain'], a['native']['residue'], a['native']['atom']) for a in mapping['atoms']]
    assert len(set(identities)) == 5
    assert source.read_bytes() == before
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    derived_data = MMCIF2Dict(str(derived))
    assert derived_data['_struct_conn.ptnr1_auth_asym_id'] == ['C']
    assert derived_data['_struct_conn.ptnr1_auth_seq_id'] == ['1']
    assert derived_data['_struct_conn.conn_type_id'] == ['metalc']
    native = lower_native({'input': str(source), 'select_fixed_atoms': {'A2': ['CA'], 'A2A': [], 'AA2': ['ALL']}, 'ligand': 'B9,C4'}, mapping, derived.name)
    assert native['select_fixed_atoms'] == {'A1': ['CA'], 'A2': [], 'B1': ['ALL']}
    assert native['ligand'] == 'C1,D1'
    assert {a['source']['component'] for a in mapping['atoms']} == {'ALA', 'GLY', 'ZN', 'DA'}
    assert mapping['original_sha256'] == hashlib.sha256(before).hexdigest()


def test_partial_empty_unpaired_and_v1_readback(tmp_path):
    import scripts.rfd3_generation.contract as contract
    producer = load_script('generation_writer', 'scripts/rfd3_generation/build_result_manifest.py', contract)
    spec = importlib.util.spec_from_file_location('generation_api', ROOT / 'platform/api/services/rfd3_generation.py')
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    request = generation(2)
    request_file = tmp_path / 'request.json'
    request_file.write_text(json.dumps(request))
    output = tmp_path / 'run' / 'rfd3'
    output.mkdir(parents=True)
    structure = output / 'one.cif.gz'
    with gzip.open(structure, 'wt') as handle:
        handle.write(cif_source())
    metadata = output / 'one.json'
    metadata.write_text('{}')
    manifest = producer.build_result_manifest(request_path=request_file, structure_paths=[structure], metadata_paths=[metadata], accepted_candidate_ids=None, storage_prefix='run/rfd3', execution_status='failed')
    assert manifest['population']['missing'] == 1
    assert manifest['population']['failed'] is None
    assert manifest['execution_status'] == 'failed'
    api.validate_result_manifest(manifest, request=request, output_root=tmp_path, job_id='j')
    empty = producer.build_result_manifest(request_path=request_file, structure_paths=[], metadata_paths=[metadata], accepted_candidate_ids=None, storage_prefix='run/rfd3', execution_status='cancelled')
    assert empty['population']['missing'] == 2
    assert empty['aggregate']['length'] is None
    assert len(empty['population']['unpaired_artifacts']) == 1
    api.validate_result_manifest(empty, request=request, output_root=tmp_path, job_id='j')
    old = {k: v for k, v in manifest.items() if k not in {'population', 'execution_status'}}
    old['schema'] = contract.RESULT_MANIFEST_SCHEMA
    with pytest.raises(api.GenerationContractError, match='cardinality'):
        api.validate_result_manifest(old, request=request, output_root=tmp_path, job_id='j')
    request['generation']['num_designs'] = 1
    old['aggregate']['requested'] = 1
    old['request_sha256'] = api.request_sha256(request)
    api.validate_result_manifest(old, request=request, output_root=tmp_path, job_id='j')


def test_exact_retained_native_schedule_source_and_shape():
    source_path = os.environ.get('BMS_TEST_RFD3_SAMPLER_SOURCE')
    if not source_path:
        pytest.skip('set BMS_TEST_RFD3_SAMPLER_SOURCE to retained rc-foundry 0.1.9 source')
    source = Path(source_path).read_bytes()
    fixed = patch_source(source)
    assert hashlib.sha256(fixed).hexdigest() == PATCHED_SHA256
    assert patch_source(fixed) == fixed
    assert patch_sampler_source(source) == patch_sampler_source(fixed)
    assert patch_sampler_source(patch_sampler_source(source)) == patch_sampler_source(source)
    parsed = ast.parse(fixed)
    function = next(n for n in ast.walk(parsed) if isinstance(n, ast.FunctionDef) and n.name == '_construct_inference_noise_schedule')
    function.returns = None
    for arg in function.args.args:
        arg.annotation = None
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {'torch': SimpleNamespace(linspace=lambda start, end, n, device: np.linspace(start, end, n)),
                 'ranked_logger': SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)}
    exec(compile(module, '<retained-native-schedule>', 'exec'), namespace)
    cls = type('NativeSchedule', (), {'_construct_inference_noise_schedule': namespace[function.name]})
    sampler = cls()
    for key, value in dict(min_t=0, max_t=1, num_timesteps=200, sigma_data=16, s_min=4e-4, s_max=160, p=7).items():
        setattr(sampler, key, value)
    lengths = [len(sampler._construct_inference_noise_schedule('cpu', None if value is None else np.array([value]))) for value in [None, 0, 2, 10]]
    assert lengths == [200, 1, 48, 70]


def test_conditioned_generation_literal_transport(tmp_path):
    import scripts.rfd3_generation.contract as contract
    preparer = load_script('generation_prepare', 'scripts/rfd3_generation/prepare_native_input.py', contract)
    source = tmp_path / 'source.cif'
    source.write_text(cif_source())
    request = generation()
    request['source'] = {'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    request['conditioning'] = {'contig': 'A2,/0,C4,8-10', 'unindex': 'AA2',
        'select_fixed_atoms': {'C4': ['P']}, 'select_hotspots': ['A2'],
        'ori_token': [1, 2, 3], 'is_non_loopy': True, 'ligand': 'ZN'}
    native, receipt = preparer.prepare(request)
    assert native['generation_0']['select_hotspots'] == 'A2'
    assert native['generation_0']['unindex'] == 'AA2'
    assert native['generation_0']['select_fixed_atoms'] == {'C4': ['P']}
    assert native['generation_0']['input'] == str(source)
    assert receipt['request_sha256'] == contract.canonical_sha256(request)
    request['source_mapping'] = 'bms.rfd3.source-map.v1'
    mapped, receipt = preparer.prepare(request, source_destination=tmp_path / 'derived.cif')
    assert mapped['generation_0']['unindex'] == 'B1'
    assert mapped['generation_0']['input'] == 'derived.cif'
    assert receipt['source_mapping']['original_sha256'] == request['source']['sha256']


def test_population_reader_over_old_limit_and_truncation(tmp_path):
    from scripts.rfd3_generation.manifest_records import iter_manifest_records
    import tracemalloc
    path = tmp_path / 'large.json'
    count = 10000
    with path.open('w') as handle:
        handle.write('{"schema":"fixture","candidates":[')
        for i in range(count):
            if i:
                handle.write(',')
            json.dump({'candidate_id': str(i), 'payload': 'x' * 1024}, handle)
        handle.write('],"aggregate":{"generated":10000}}')
    assert path.stat().st_size > 8 * 1024 * 1024
    tracemalloc.start()
    observed = 0
    for key, record in iter_manifest_records(path):
        if key == 'candidate':
            assert record['candidate_id'] == str(observed)
            observed += 1
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert observed == count
    assert peak < 2 * 1024 * 1024
    for text in ['{"candidates":[{}', '{"candidates":[{},]}', '{"candidates":[]}junk', '{"candidates":[],"candidates":[]}']:
        path.write_text(text)
        with pytest.raises(ValueError):
            list(iter_manifest_records(path, chunk_size=3))


@pytest.mark.parametrize('staged', [False, True])
def test_local_partial_and_empty_producer_preserve_execution_state(tmp_path, staged):
    import subprocess
    import scripts.rfd3_local_redesign.contract as contract
    source = tmp_path / 'source.cif'
    source.write_text(cif_source())
    params = local('partial_diffusion', num_designs=2)
    params['input_structure'] = str(source)
    request = build_request(params, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    request_path = tmp_path / 'request.json'
    request_path.write_text(json.dumps(request))
    native_path, receipt_path = tmp_path / 'native.json', tmp_path / 'receipt.json'
    subprocess.run([sys.executable, str(ROOT / 'scripts/rfd3_local_redesign/prepare_native_input.py'),
        '--request', str(request_path), '--input-structure', str(source),
        '--output-native', str(native_path), '--output-receipt', str(receipt_path),
        *(['--source-dir', str(tmp_path / 'rfd3_sources')] if staged else [])], check=True)
    structure = tmp_path / 'one.cif.gz'
    with gzip.open(structure, 'wt') as handle:
        handle.write(cif_source())
    metadata = tmp_path / 'one.json'
    metadata.write_text('{}')
    log = tmp_path / 'run.log'
    log.write_text('fixture failure')
    index = tmp_path / 'index.jsonl'
    index.write_text('')
    output = tmp_path / 'manifest.json'
    command = [sys.executable, str(ROOT / 'scripts/rfd3_local_redesign/build_result_manifest.py'),
        '--request', str(request_path), '--native-input', str(native_path),
        '--native-input-storage-path', str(native_path), '--preparation-receipt', str(receipt_path),
        '--preparation-receipt-storage-path', str(receipt_path), '--log-file', str(log),
        '--metadata-jsonl', str(index), '--output', str(output), '--storage-root', str(tmp_path),
        '--request-storage-path', str(request_path), '--source-file', str(source),
        '--source-storage-path', str(source)]
    subprocess.run(command + ['--cif-file', str(structure), '--json-file', str(metadata), '--execution-status', 'failed'], check=True)
    manifest = json.loads(output.read_text())
    assert manifest['schema'].endswith('.v2')
    assert manifest['population']['generated'] == 1
    assert manifest['population']['missing'] == 1
    assert manifest['execution_status'] == 'failed'
    subprocess.run(command + ['--execution-status', 'cancelled'], check=True)
    empty = json.loads(output.read_text())
    assert empty['candidates'] == []
    assert empty['population']['missing'] == 2
    assert empty['execution_status'] == 'cancelled'


def test_historical_fraction_and_boolean_normalization_is_not_hardened():
    spec = importlib.util.spec_from_file_location('generation_compat_api', ROOT / 'platform/api/services/rfd3_generation.py')
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    _, request, _ = api.normalize_generation_params({'num_designs': 2.9}, job_name='fraction-compat')
    assert request['generation']['num_designs'] == 2
    with pytest.raises(api.GenerationContractError):
        api.normalize_generation_params({'num_designs': True}, job_name='bool')
    local_request = build_request(local('partial_diffusion', num_designs=2.9, dump_trajectories='true'))
    assert local_request['execution']['num_designs'] == 2
    assert local_request['execution']['dump_trajectories'] is True
    for value in [True, 2.9]:
        typed = generation(value)
        with pytest.raises(ValueError):
            validate_request(typed)
