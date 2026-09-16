import copy
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from services.bioxp.operator_models import OperatorControlCatalog
from bioxp_recorded_receipts import RecordedReceipts

FIXTURES = Path(__file__).parent / 'fixtures'


def test_live_software_abort_catalog_retains_nonphysical_scope():
    payload = json.loads((FIXTURES / 'bioxp_live_software_abort_catalog.json').read_text())
    value = OperatorControlCatalog.model_validate(payload)
    abort = next(a for a in value.actions if a.action_id == 'oem.abort_all')
    assert abort.physical_scope == 'none_software_flags_and_waiters'
    assert abort.aggregate_abort is True
    mutated = copy.deepcopy(payload)
    next(a for a in mutated['actions'] if a['action_id'] == 'oem.abort_all')['physical_scope'] = 'arbitrary_motion'
    with pytest.raises(ValidationError):
        OperatorControlCatalog.model_validate(mutated)


def test_live_history_accepts_authoritative_positive_sql_sequences():
    payload = json.loads((FIXTURES / 'bioxp_live_sql_sequence_history.json').read_text())
    value = RecordedReceipts.model_validate(payload["receipts"])
    assert [r.sequence for r in value.root] == [r['sequence'] for r in payload['receipts']]
    for invalid in [0, -1, True, '5']:
        altered = copy.deepcopy(payload)
        altered['receipts'][0]['sequence'] = invalid
        with pytest.raises(ValidationError):
            RecordedReceipts.model_validate(altered["receipts"])
