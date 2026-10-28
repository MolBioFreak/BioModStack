"""Real pinned-source CPU evaluator, not selected-image/inference qualification.

Run in an isolated native-capable interpreter with BMS_TEST_RFD4_SOURCE and
BMS_TEST_RFD4_INSPECTION_CIF. Never replace unavailable native imports with doubles.
"""
import copy
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


@pytest.fixture
def native_sources(tmp_path):
    source = os.environ.get('BMS_TEST_RFD4_SOURCE')
    specimen = os.environ.get('BMS_TEST_RFD4_INSPECTION_CIF')
    if not source or not specimen:
        pytest.skip('Explicit pinned native source and structural specimen required')
    sys.path.insert(0, str(Path(source) / 'src'))
    import rfproteina.conditions
    from atomworks.io import parse
    from atomworks.io.utils.io_utils import to_cif_file
    from rfproteina.utils.parse_presets import ANNOTATED_CIF
    from atomworks.ml.conditions import C_SEQ
    array = parse(specimen, config=ANNOTATED_CIF)['asym_unit'][0]
    array = array[(array.res_id >= 1) & (array.res_id <= 12)]
    array.chain_id[:] = 'X'
    C_SEQ.set_annotation(array, C_SEQ.ground_truth_value(array))
    paths = {}
    for slot, chain in [('a', 'X'), ('b', 'Y'), ('collision', 'X')]:
        current = array.copy(); current.chain_id[:] = chain
        path = tmp_path / f'{slot}.cif'
        to_cif_file(current, path, save_standard_annotations=True)
        paths[slot] = str(path)
    return paths


def payload(paths, conditions, family, value, row_index=0, **example):
    row = conditions[family][value][row_index]
    return {'paths': paths, 'input': {'kind': 'condition_spec', 'global_args': {'input_as_annotated_cif': True},
            'examples': [{'name': 'current', 'sources': [{'slot': 'a'}], 'conditions': conditions, **example}]},
            'row': {'exampleIndex': 0, 'family': family, 'value': value, 'rowIndex': row_index, **row}}


def test_native_group_row_targets_are_not_final_masks_or_neighbor_rows(native_sources):
    from inspect_rfd4_condition import inspect_condition
    conditions = {'C_IDX': {'false': [{'select': 'res_id == 2 and atom_name == "CA"', 'group': 'g'},
                                     {'select': 'res_id == 4 and atom_name == "N"', 'group': 'g'}],
                            'true': [{'select': 'res_id == 2'}]},
                  'C_SEQ': {'false': [{'select': 'res_id == 5'}]}}
    request = payload(native_sources, conditions, 'C_IDX', 'false', 1)
    original = copy.deepcopy(request)
    result = inspect_condition(request)
    assert [(a['chain_id'], a['res_id'], a['atom_name']) for a in result['atoms']] == [('X', 4, 'N')]
    assert request == original
    result = inspect_condition(payload(native_sources, conditions, 'C_IDX', 'false', 0))
    assert [(a['res_id'], a['atom_name']) for a in result['atoms']] == [(2, 'CA')]
    result = inspect_condition(payload(native_sources, conditions, 'C_SEQ', 'false'))
    assert result['atoms'] and {a['res_id'] for a in result['atoms']} == {5}


def test_native_contig_uses_original_residue_numbering(native_sources):
    from inspect_rfd4_condition import inspect_condition
    result = inspect_condition(payload(native_sources, {'C_SEQ': {'true': [{'select': 'res_id == 10 and atom_name == "CA"'}]}},
                                        'C_SEQ', 'true', contig='X10-12,X2-3'))
    assert [(a['chain_id'], a['res_id'], a['atom_name']) for a in result['atoms']] == [('X', 10, 'CA')]


def test_native_multisource_preserves_exact_source_chains_and_rejects_collisions(native_sources):
    from inspect_rfd4_condition import inspect_condition
    conditions = {'C_SEQ': {'true': [{'select': 'chain_id == "Y" and res_id == 3 and atom_name == "CA"'}]}}
    request = payload(native_sources, conditions, 'C_SEQ', 'true', sources=[{'slot': 'a'}, {'slot': 'b'}])
    result = inspect_condition(request)
    assert [(a['chain_id'], a['res_id'], a['atom_name']) for a in result['atoms']] == [('Y', 3, 'CA')]
    request['input']['examples'][0]['sources'][1]['slot'] = 'collision'
    with pytest.raises(ValueError, match='chain_id.*appears in both'):
        inspect_condition(request)


@pytest.mark.parametrize('preserve', [True, False])
def test_native_inherited_and_explicit_fresh_parse_policy(native_sources, preserve):
    from inspect_rfd4_condition import inspect_condition
    from rfproteina.inference_utils.condition_spec import load_condition_spec_inputs
    from atomworks.ml.conditions import C_SEQ
    conditions = {'C_SEQ': {'true': [{'select': 'res_id == 2 and atom_name == "CA"'}]}}
    request = payload(native_sources, conditions, 'C_SEQ', 'true')
    if not preserve:
        request['input']['examples'][0]['sources'][0]['input_as_annotated_cif'] = False
    before = copy.deepcopy(request)
    result = inspect_condition(request)
    assert result['input_as_annotated_cif'] is preserve and request == before
    assert [(a['res_id'], a['atom_name']) for a in result['atoms']] == [(2, 'CA')]
    loaded = load_condition_spec_inputs([native_sources['a']], as_annotated_cif=preserve)
    assert bool(C_SEQ.mask(loaded, default='generate').any()) is preserve
