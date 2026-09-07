import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from scientific_viewer_fixture import native_fixture
from services.scientific_viewer_contract import ScientificViewerMetric


def test_real_strict_loader_api_serialization_equals_mounted_consumer_fixture(tmp_path):
    result, evidence = native_fixture(tmp_path)
    fixture = Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures/scientificPaeNative.json'
    assert json.loads(result.model_dump_json()) == json.loads(fixture.read_text())
    assert result.design_id == 'candidate'
    assert result.row_axis.candidate_id == 'native-sample-7'
    assert result.row_axis.document_id == 'native/sample-7.cif'
    assert result.row_axis.model_dump() == evidence['row_axis']
    assert [r.auth_asym_id for r in result.row_axis.residues] == ['T','H','H','H','L']
    assert [r.insertion_code for r in result.row_axis.residues[1:4]] == ['', 'A', 'B']


def test_real_strict_loader_downsample_retains_native_axes(tmp_path):
    result, evidence = native_fixture(tmp_path, max_size=3)
    assert result.native_shape == [5,5]
    assert result.sampled_row_indices == [0,2,4]
    assert result.sampled_column_indices == [0,2,4]
    assert result.row_axis.model_dump() == evidence['row_axis']
    assert result.pae_matrix == [[0,2,4],[10,12,14],[20,22,24]]


@pytest.mark.parametrize('damage', ['bool', 'null', 'inf', 'extra', 'axes', 'sample', 'version_bool'])
def test_api_wire_rejects_invalid_native_projection(tmp_path, damage):
    result, _ = native_fixture(tmp_path)
    raw = result.model_dump()
    if damage == 'bool': raw['pae_matrix'][0][0] = True
    if damage == 'null': raw['pae_matrix'][0][0] = None
    if damage == 'inf': raw['pae_matrix'][0][0] = float('inf')
    if damage == 'extra': raw['row_axis']['surprise'] = 1
    if damage == 'axes': raw['column_axis']['residues'][0]['auth_seq_id'] = 101
    if damage == 'sample': raw['sampled_row_indices'] = None
    if damage == 'version_bool': raw['schema_version'] = True
    with pytest.raises(ValidationError):
        ScientificViewerMetric.model_validate(raw)
