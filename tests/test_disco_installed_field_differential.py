"""Installed DISCO pure-consumer differential; no chemistry or model creation."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import get_args, get_type_hints
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]


def native_root():
    p = Path('/opt/disco/disco')
    if not p.exists():
        pytest.skip('Selected installed DISCO required')
    return p


def test_planner_registry_equals_contract():
    from lib.disco_contract import SequenceSampling
    tree = ast.parse((native_root()/'model/modules/plm.py').read_text())
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MODEL_REGISTRY' for t in n.targets))
    registry = ast.literal_eval(assignment.value)
    annotation = get_type_hints(SequenceSampling)['planner_name']
    assert set(get_args(get_args(annotation)[0])) == set(registry)


def test_guidance_branch_enums_equal_contract():
    from lib.disco_contract import NoisyGuidance
    tree = ast.parse((native_root()/'model/cogen_inference/cogen_inference_loop_body_impl.py').read_text())
    modes, schedules = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.ops[0], ast.Eq):
            continue
        if len(node.comparators) != 1 or not isinstance(node.comparators[0], ast.Constant):
            continue
        value = node.comparators[0].value
        if isinstance(node.left, ast.Name) and node.left.id == 'time_mode':
            modes.add(value)
        if isinstance(node.left, ast.Attribute) and node.left.attr == 'omega_schedule':
            schedules.add(value)
    hints = get_type_hints(NoisyGuidance)
    assert set(get_args(hints['omega_schedule'])) == schedules
    assert set(get_args(hints['uncond_seq_time_mode'])) == modes
    assert set(get_args(hints['uncond_struct_time_mode'])) == modes


def test_integer_endpoint_native_lookup_failure_is_not_repaired():
    # Execute the installed method itself in an inert namespace. The bug occurs
    # before any atom access: dict.values() is neither the entity dict nor its
    # atom map. No ligand/conformer is synthesized for this qualification.
    path = native_root()/'data/json_to_feature.py'
    tree = ast.parse(path.read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'add_bonds_between_entities')
    ns = {'AtomArray': object}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), ns)
    subject = SimpleNamespace(input_dict={
        'sequences': [{'ligand': {'atom_map_to_atom_name': {1: 'C1'}}}],
        'covalent_bonds': [{'left_entity': 1, 'left_position': 1, 'left_atom': 1,
                            'right_entity': 1, 'right_position': 1, 'right_atom': 'C2'}]})
    with pytest.raises(AssertionError):
        ns['add_bonds_between_entities'](subject, None)
    assert subject.input_dict['covalent_bonds'][0]['left_atom'] == 1
