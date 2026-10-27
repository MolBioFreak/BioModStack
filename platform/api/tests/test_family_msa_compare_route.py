import json
import pytest
from bms_alignment import compare
from routers.family_msa_results import comparison_roster


@pytest.mark.asyncio
async def test_comparison_roster_maps_only_explicit_identity_and_positions():
    roster = {'molecule': 'dna', 'rows': [
        {'row_id': 'r', 'header': 'same', 'sequence': 'AC', 'source_member_id': 'member',
         'occurrence_id': 'occ', 'source_positions': [5, 6], 'transformation_provenance': 'declared'},
        {'row_id': 'unknown', 'header': 'same', 'sequence': 'AC'}]}
    entries = [{'id': 'a', 'role': 'alignment'}, {'id': 'receipt', 'role': 'receipt'}]
    blobs = {'a': b'>r\nA-C\n>unknown\nAC-\n', 'receipt': json.dumps({'requested': {'roster': roster}}).encode()}
    async def read(key):
        return blobs[key]
    aligned = await comparison_roster(entries, read, 'a')
    report = compare(aligned, aligned)
    assert report['rows'][0]['positions'] == [{'source': 5, 'left_column': 0, 'right_column': 0}, {'source': 6, 'left_column': 2, 'right_column': 2}]
    assert report['left_unknown_identity_rows'] == ['unknown']
    blobs['a'] = b'>r\nA-T\n>unknown\nAC-\n'
    changed = await comparison_roster(entries, read, 'a')
    report = compare(changed, changed)
    assert report['rows'][0]['mapping_unknown']
    assert report['rows'][0]['positions'] == []
