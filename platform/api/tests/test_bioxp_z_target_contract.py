"""Real offline robot scalar fixtures; no hardware/network acceptance implied."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.bioxp.operator_models import OperatorZMoveEvidence, OperatorZTargetPreview


FIXTURES = json.loads((Path(__file__).parents[2] / 'frontend/tests/fixtures/bioxp_z_target_producer.json').read_text())


@pytest.mark.parametrize('row', FIXTURES)
def test_source_owned_z_target_scalar_contract(row):
    preview = OperatorZTargetPreview.model_validate(row['provider']['target_preview'])
    receipt = OperatorZMoveEvidence.model_validate(row['z_move'])
    assert preview.requested_position_steps == receipt.requested_position_steps == row['requested']
    assert preview.effective_position_steps == receipt.effective_position_steps
    assert receipt.before_position_steps == row['start']
    assert receipt.after_position_steps is None
    assert receipt.controller_command_acknowledged is True
    assert receipt.controller_terminal_state_verified is False
    assert receipt.physical_effect_verified is False
    assert receipt.model_dump()['target_clamped'] == (row['requested'] < row['minimum'])


def test_z_targets_reject_diagnostic_omission_in_place_of_critical_scalar():
    row = FIXTURES[0]['z_move']
    with pytest.raises(ValidationError):
        OperatorZMoveEvidence.model_validate({**row, 'effective_position_steps': {'omitted': 'item_limit'}})
    assert OperatorZTargetPreview(requested_position_steps=0, effective_position_steps=None).effective_position_steps is None
