"""Pure compiler outputs for mounted workspace receiving; no native dispatch."""
import json
import os
from copy import deepcopy
from pathlib import Path
from bioxp_method_compiler import compile_method
from bioxp_method_simulation import simulate_method
from bioxp_method_custody import custody_suggestions
from bioxp_method_liquids import starter_entries


def test_emit_workspace_preview_contract():
    method = {'schema': 'bms.bioxp-method.v1', 'name': 'Synthetic workspace receiving', 'deck_plan': {
        'labware': [{'id': 'sample', 'station': 'LOC_MS', 'profile_id': 'plate'}, {'id': 'untouched', 'station': 'LOC_RC', 'profile_id': 'plate'}],
        'assignments': [{'labware_id': 'sample', 'well': 'A1', 'volume_ul': '20', 'material_id': 'sample'}, {'labware_id': 'untouched', 'well': 'A1', 'volume_ul': None, 'material_id': 'unknown'}]},
        'steps': [{'type': 'action', 'step_id': 'before', 'action': 'note', 'inputs': {'message': 'start'}},
                  {'type': 'action', 'step_id': 'carry', 'action': 'plate_move', 'inputs': {'plate_id': 'PL_POOL', 'labware_id': 'sample', 'target_location': 'LOC_TC'}},
                  {'type': 'action', 'step_id': 'move', 'action': 'move', 'inputs': {'location_id': 2, 'well': 'a1', 'position_flag': 0}},
                  {'type': 'action', 'step_id': 'cover', 'action': 'move_cover', 'inputs': {'cover_id': 'CV_OUTPUT', 'target_location': 'LOC_OCS'}},
                  {'type': 'action', 'step_id': 'park', 'action': 'park', 'inputs': {}}]}
    method['steps'].insert(3, {'type': 'action', 'step_id': 'transfer', 'action': 'transfer', 'inputs': {
        'source': {'labware_id': 'sample', 'station': 'LOC_TC', 'location_id': 2, 'wells': ['A1']},
        'destination': {'labware_id': 'untouched', 'station': 'LOC_RC', 'location_id': 3, 'wells': ['A1']},
        'channels': [0], 'volume_ul': '1.2500', 'aspirate_speed': '30', 'dispense_speed': '40',
        'source_position_flag': 1, 'destination_position_flag': 2, 'source_lift_height_steps': None, 'destination_lift_height_steps': 100}})
    request = {'method': method, 'dependencies': {'labware_profiles': [{'id': 'plate', 'native_addressing': {'reference_channel': 0, 'row_increment': 2, 'column_increment': 0}}]}, 'initial_state': {'vessels': {'sample:A1': {'volume_ul': '003.7500', 'materials': ['explicit']}}}}
    original = deepcopy(request)
    result = compile_method(request)
    assert result['document'], result['issues']
    assert request == original
    seed = result['simulation']['initial_state']
    assert seed['vessels']['sample:A1']['volume_ul'] == '003.7500'
    assert seed['vessels']['untouched:A1']['volume_ul'] is None
    occurrences = result['resolved']['occurrences']
    # Independent producer prefixes, not JS replay-derived expected values.
    prefixes = [simulate_method(occurrences[:i + 1], initial_state=seed)['state'] for i in range(len(occurrences))]
    output = {'starters': starter_entries(), 'custody': custody_suggestions(), 'request': request, 'result': result, 'expected_prefix_states': prefixes}
    path = os.environ.get('REPAIR_WORKSPACE_COMPILER_OUTPUT')
    assert path, 'Set REPAIR_WORKSPACE_COMPILER_OUTPUT to an evidence JSON path'
    Path(path).write_text(json.dumps(output, indent=2))
