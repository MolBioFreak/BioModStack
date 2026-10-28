"""Published rc-foundry 0.1.9 CPU parsing, not installed-image/inference evidence.

The parser and its scientific component functions are unchanged AST nodes. Only
package startup and distributed logging are omitted; no masks are substituted.
The mounted wire is separately supplied so a browser assertion is not mistaken
for native selector execution.
"""
import ast
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.rfd3_local_redesign.source_mapping import materialize_source, lower_native

HASHES = {
    'rfd3/constants.py': 'c0bbc3ceb4e1a4eacaf13df0185350786ae10173894337bc977bffee98c874c3',
    'foundry/constants.py': 'd27d7004a0af36ec3741a41e8526e6b0762421d6869f7b4b791148b41add7876',
    'rfd3/inference/parsing.py': '92d00c52e644dcf7b86ca0238d5df38ea7040e61a5319f9ca2d214849b044100',
    'foundry/utils/components.py': '3e2c364df405dbabaec42de15c6de2bffe2b82e7759553be72fe8dfa9d391036',
    'foundry/common.py': '02eafc987f82366714a92b30ed581a090496e6c1f506a7fc0daf758f3c4f5756',
    'rfd3/inference/input_parsing.py': 'a6872a9e7844dfeb337816c22fd832f44de464948ffc21875dde85151c5fbf47',
}


@pytest.fixture
def native_parser(monkeypatch):
    source = os.environ.get('BMS_TEST_RFD3_PUBLISHED')
    if not source:
        pytest.skip('Explicit published rc-foundry 0.1.9 source required (CPU parser, not installed image)')
    source = Path(source)
    trees = {}
    for name, digest in HASHES.items():
        raw = (source / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        trees[name] = ast.parse(raw)
    # Namespace packages avoid the unrelated Foundry environment/ML startup.
    for name in ('foundry', 'foundry.utils', 'rfd3', 'rfd3.inference'):
        module = types.ModuleType(name)
        module.__path__ = [str(source / name.replace('.', '/'))]
        monkeypatch.setitem(sys.modules, name, module)
    common = types.ModuleType('foundry.common')
    from typing import Any
    common.__dict__['Any'] = Any
    exists = next(n for n in trees['foundry/common.py'].body if isinstance(n, ast.FunctionDef) and n.name == 'exists')
    exec(compile(ast.Module(body=[exists], type_ignores=[]), '<pinned exists>', 'exec'), common.__dict__)
    monkeypatch.setitem(sys.modules, 'foundry.common', common)
    components = types.ModuleType('foundry.utils.components')
    components.__dict__['global_logger'] = logging.getLogger('native-parser-test')
    body: list[ast.stmt] = [n for n in trees['foundry/utils/components.py'].body
            if not (isinstance(n, ast.ImportFrom) and n.module == 'foundry.utils.ddp')
            and not (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'global_logger' for t in n.targets))]
    exec(compile(ast.Module(body=body, type_ignores=[]), '<pinned components>', 'exec'), components.__dict__)
    monkeypatch.setitem(sys.modules, 'foundry.utils.components', components)
    parser = types.ModuleType('rfd3.inference.parsing')
    monkeypatch.setitem(sys.modules, parser.__name__, parser)
    exec(compile(trees['rfd3/inference/parsing.py'], '<pinned InputSelection>', 'exec'), parser.__dict__)
    # Exercise the exact source pre-validator too, without constructing the ML model.
    cls = next(n for n in trees['rfd3/inference/input_parsing.py'].body if isinstance(n, ast.ClassDef) and n.name == 'DesignInputSpecification')
    load = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'load_input')
    load.decorator_list = []
    ns = {'InputSelection': parser.InputSelection, 'exists': common.exists, 'validator_context': lambda *a: contextlib.nullcontext()}
    exec(compile(ast.Module(body=[load], type_ignores=[]), '<pinned load_input>', 'exec'), ns)
    # Imported constant modules are real published source and isolated from other tests.
    yield parser.InputSelection, ns['load_input']
    for name in ('rfd3.constants', 'foundry.constants'):
        sys.modules.pop(name, None)


def atom_array(native_path):
    from biotite.structure.io.pdbx import CIFFile, get_structure
    return get_structure(CIFFile.read(native_path), model=1, use_author_fields=False, altloc='all')


def test_actual_mounted_author_atom_wire_reaches_native_mask(tmp_path, native_parser):
    wire_path = os.environ.get('BMS_TEST_RFD3_SELECTION_WIRE')
    if not wire_path:
        pytest.skip('Run the mounted receiving suite with BMS_TEST_RFD3_SELECTION_WIRE first')
    wire = json.loads(Path(wire_path).read_text())
    derived = tmp_path / 'native.cif'
    original = Path(wire['sourcePath'])
    before = original.read_bytes()
    mapping = materialize_source(original, derived, model_serial=2)
    lowered = lower_native(wire['draft'], mapping, derived.name)
    assert lowered == wire['native']
    assert original.read_bytes() == before
    select, load = native_parser
    aa = atom_array(derived)
    assert select.from_any(lowered['select_fixed_atoms'], aa).mask.tolist() == [True, False, False, False, False]
    # Independent sequence mask remains explicit False (not missing/defaulted).
    assert not select.from_any(lowered['select_unfixed_sequence'], aa).mask.any()
    for field in ('select_hbond_donor', 'select_hbond_acceptor', 'select_buried', 'select_exposed', 'select_partially_buried', 'select_hotspots'):
        parsed = load(None, {'input': None, 'atom_array_input': aa, field: lowered['select_fixed_atoms']})
        assert parsed[field].mask.tolist() == [True, False, False, False, False]
    assert mapping['model'] == '2'
    # Replay the exact mounted condition object through the actual generation_0 owner.
    import importlib.util
    from scripts.rfd3_generation import contract
    spec = importlib.util.spec_from_file_location('receiving_prepare', ROOT / 'scripts/rfd3_generation/prepare_native_input.py')
    assert spec is not None and spec.loader is not None
    prepare_module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get('contract')
    sys.modules['contract'] = contract
    try:
        spec.loader.exec_module(prepare_module)
    finally:
        if previous is None:
            sys.modules.pop('contract', None)
        else:
            sys.modules['contract'] = previous
    request = {'schema': contract.REQUEST_SCHEMA, 'request_id': 'mounted', 'job_id': 'inert',
               'generation': {'min_length': 20, 'max_length': 30, 'num_designs': 1},
               'execution': {'seed': 0, 'dump_trajectories': False},
               'source': {'path': str(original), 'sha256': hashlib.sha256(before).hexdigest()},
               'source_mapping': 'bms.rfd3.source-map.v1', 'source_model': 2,
               'conditioning': wire['draft']}
    prepared, receipt = prepare_module.prepare(request, source_destination=tmp_path / 'generation-source.cif')
    assert prepared['generation_0'] == {**wire['native'], 'input': 'generation-source.cif', 'dialect': 2, 'length': '20-30'}
    assert receipt['source_mapping']['model'] == '2'
    # Historical mapping-off values remain untouched by the same owner.
    historical, _ = prepare_module.prepare({k: v for k, v in request.items() if k != 'source_mapping'})
    assert historical['generation_0']['select_fixed_atoms'] == wire['draft']['select_fixed_atoms']
    assert historical['generation_0']['input'] == str(original)


@pytest.mark.parametrize('value', [True, False, {'A1': ['CA']}])
def test_native_contig_prevalidator_rejects_new_union_forms_but_hbond_accepts_them(tmp_path, native_parser, value):
    select, load = native_parser
    derived = tmp_path / 'native.cif'
    materialize_source(ROOT / 'platform/frontend/tests/fixtures/rfd3-selection-identity.cif', derived, model_serial=2)
    aa = atom_array(derived)
    with pytest.raises(ValueError, match='selection must be a string or None'):
        load(None, {'input': None, 'atom_array_input': aa, 'contig': value})
    assert isinstance(load(None, {'input': None, 'atom_array_input': aa, 'select_hbond_donor': value})['select_hbond_donor'], select)
    assert load(None, {'input': None, 'atom_array_input': aa, 'contig': None})['contig'] is None
    assert load(None, {'input': None, 'atom_array_input': aa, 'contig': 'A1'})['contig'].mask.sum() == 2
