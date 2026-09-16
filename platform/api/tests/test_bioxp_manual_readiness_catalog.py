"""Robot-native emitted catalog -> strict BMS model -> actual legacy GET."""
import json
import os
from pathlib import Path
import pytest
from tests.test_bioxp_operator_controls import make_client
from services.bioxp.operator_models import OperatorControlCatalog


@pytest.mark.parametrize('fresh_motion', [True,False])
@pytest.mark.parametrize('phase',['initial','cached','recovered'])
def test_native_readiness_catalog_reaches_consumer(monkeypatch,fresh_motion,phase):
    evidence=os.environ.get('READINESS_ROBOT_EVIDENCE')
    if evidence:
        # Optional full native-producer replay during coordinated qualification.
        root=Path(evidence)
        raw=json.loads((root/f'candidate-contract-export-onset-calls.jsonl.catalog-{fresh_motion}.json').read_text())[phase]
    else:
        # Captured native test-route output, filtered to this action only.
        # Preserves producer fields/values; no workstation artifact required.
        path=Path(__file__).parent/'fixtures/bioxp_manual_readiness_catalog.json'
        raw=json.loads(path.read_text())[str(fresh_motion)][phase]
    # No invented producer field names or baseline fixture normalization.
    parsed=OperatorControlCatalog.model_validate(raw)
    expected=fresh_motion if phase=='cached' else True
    action=next(a for a in parsed.actions if a.informational_path=='/motion/gripper/open')
    assert action.enabled is expected
    client,runtime=make_client(monkeypatch,mutations=False)
    runtime.connection.client.responses['operator_control_catalog']=raw
    with client:
        response=client.get('/api/bioxp/operator-controls/catalog')
    assert response.status_code==200,response.text
    action=next(a for a in response.json()['actions'] if a['informational_path']=='/motion/gripper/open')
    assert action['enabled'] is expected
    assert action['available'] is expected
    assert action['snapshot_freshness']==raw['actions'][next(i for i,a in enumerate(raw['actions']) if a['informational_path']=='/motion/gripper/open')]['snapshot_freshness']
    assert all(call[0]=='operator_control_catalog' for call in runtime.connection.client.calls)
