"""Actual parent mounted wire -> generation_0 -> pinned published parser, CPU only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import pytest
from test_rfd3_selection_receiving import native_parser, atom_array

ROOT = Path(__file__).resolve().parents[1]


def test_parent_nested_selection_reaches_native_parser(tmp_path, native_parser):
    path = os.environ.get('BMS_PARENT_SELECTION_WIRE')
    if not path:
        pytest.skip('explicit parent mounted wire required')
    wire = json.loads(Path(path).read_text())
    params = wire['params']
    original = Path(params['input_structure'])
    from scripts.rfd3_generation import contract
    spec = importlib.util.spec_from_file_location('parent_prepare', ROOT / 'scripts/rfd3_generation/prepare_native_input.py')
    assert spec is not None and spec.loader is not None
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
    request = {'schema': contract.REQUEST_SCHEMA, 'request_id': 'parent', 'job_id': 'inert',
        'generation': {'min_length': params['min_length'], 'max_length': params['max_length'], 'num_designs': 1},
        'execution': {'seed': 0, 'dump_trajectories': False},
        'source': {'path': str(original), 'sha256': hashlib.sha256(original.read_bytes()).hexdigest()},
        'source_mapping': params['source_mapping'], 'source_model': params['source_model'],
        'conditioning': params['conditioning']}
    derived = tmp_path / 'native.cif'
    prepared, receipt = module.prepare(request, source_destination=derived)
    native = prepared['generation_0']
    assert native['select_fixed_atoms'] == {'A1': ['CA', 'N']}
    assert native['select_unfixed_sequence'] is False
    assert native['select_hotspots'] is None
    assert receipt['source_mapping']['model'] == '2'
    aa = atom_array(derived)
    select, _ = native_parser
    assert select.from_any(native['select_fixed_atoms'], aa).mask.tolist() == [True, True, False, False, False]
    # Derivative viewer document identity remains distinct from original custody.
    assert wire['receipt']['path'] != params['input_structure']
    historical, _ = module.prepare({k: v for k, v in request.items() if k != 'source_mapping'})
    assert historical['generation_0']['select_fixed_atoms'] == params['conditioning']['select_fixed_atoms']
