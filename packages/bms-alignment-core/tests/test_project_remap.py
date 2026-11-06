from bms_alignment.project_remap import ProjectInventory, remap_state


def test_typed_inventory_and_explicit_remapping():
    inv = ProjectInventory.model_validate({'schema': 'bms.family-msa.project-export.v1',
        'project_id': 'p', 'receipt_id': 'r', 'artifacts': [
            {'id': 'old', 'role': 'alignment', 'logical_path': 'out/a', 'path': 'artifacts/0'}],
        'shared_states': [{'id': 's', 'path': 'shared-state/0.json'}]})
    assert inv.artifacts[0].id == 'old'
    state = {'marks': [
        {'id': 'm', 'label': 'm', 'coordinate': {'scope': 'artifact_columns', 'artifact_id': 'old', 'start': 0, 'end': 1}},
        {'id': 'n', 'label': 'n', 'coordinate': {'scope': 'source_positions', 'source_member_id': 'old', 'occurrence_id': 'old', 'start': 0, 'end': 1}}],
        'views': [{'id': 'v', 'name': 'v', 'artifact_id': 'old'}],
        'comparisons': [{'id': 'c', 'name': 'c', 'left_artifact_id': 'old', 'right_artifact_id': 'missing',
                         'method': 'explicit', 'report': {'artifact_id': 'old'}}]}
    mapped = remap_state(state, {'old': 'new'})
    assert mapped.state['marks'][0]['coordinate']['artifact_id'] == 'new'
    assert mapped.state['marks'][1]['coordinate']['source_member_id'] == 'old'
    assert mapped.state['views'][0]['artifact_id'] == 'new'
    assert mapped.state['comparisons'][0]['left_artifact_id'] == 'new'
    assert mapped.state['comparisons'][0]['right_artifact_id'] == 'missing'
    assert mapped.state['comparisons'][0]['report']['artifact_id'] == 'old'
    assert [(x.location, x.source_artifact_id) for x in mapped.unresolved] == [('comparisons/0/right_artifact_id', 'missing')]
    assert state['views'][0]['artifact_id'] == 'old'
