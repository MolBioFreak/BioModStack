"""SDK saved-view serialization preserves the browser's complete renderer state."""
import pytest
from pydantic import ValidationError

from bms_alignment.http import RendererState, View


def test_full_renderer_state_round_trip():
    renderer: RendererState = {'scrollX': 0, 'scrollY': 23, 'columnWidth': 12,
                               'rowHeight': 18, 'colorScheme': 'clustal',
                               'hiddenColumns': [0, 3], 'hideGaps': False,
                               'allowedGappyness': 0, 'zoomLevel': 2}
    view = View(id='view', name='Full view', artifact_id='artifact',
                selected_column=0, renderer_state=renderer)
    assert View.model_validate_json(view.model_dump_json()).renderer_state == renderer
    assert view.model_dump(mode='json')['selected_column'] == 0


@pytest.mark.parametrize('renderer', [None, {}, {'hideGaps': False}, {'scrollX': 0}])
def test_partial_and_legacy_renderer_state(renderer):
    view = View(id='view', name='View', artifact_id='artifact', renderer_state=renderer)
    assert view.model_dump(mode='json')['renderer_state'] == renderer


def test_unknown_renderer_field_matches_server_closed_contract():
    with pytest.raises(ValidationError):
        View.model_validate(dict(id='view', name='View', artifact_id='artifact', renderer_state={'unknown': 1}))
