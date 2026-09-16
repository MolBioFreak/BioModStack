"""F33/F37 SPEC counterexamples: mutations of immutable offline exports."""
import json
from pathlib import Path

import pytest
from services.bioxp.operator_models import PipetteApplicationPlanPostEnvelope
from test_bioxp_direct_liquid_recovery import client_for

FIXTURES = Path(__file__).parent / 'fixtures/f33_f37_combined'
ASSOCIATION_CASES = [
    ('plan-detect', 'fluid_class', 'TC'),
    ('plan-tip', 'tip_tray', 'different-tray'),
    ('plan-tip', 'tip_well', 'B2'),
    ('plan-tip', 'tip_type', 99),
    ('plan-tip', 'tip_location', 3),
    ('plan-tip', 'home_z_after', False),
    ('plan-up', 'direction', 'down'),
    ('plan-down', 'direction', 'up'),
]


@pytest.mark.parametrize('name,field,changed', ASSOCIATION_CASES)
@pytest.mark.parametrize('mutated', [False, True])
def test_direct_liquid_spec_request_association(name, field, changed, mutated):
    lookup = json.loads((FIXTURES / (name + '-get.json')).read_bytes())
    payload = json.loads((FIXTURES / (name + '-post.json')).read_bytes())
    body = dict(lookup['record']['requested_inputs'])
    if body['operation'] != 'load_tip':
        body.pop('home_z_after')
    assert payload['requested_inputs'][field] != changed
    if mutated:
        payload['requested_inputs'][field] = changed
    # Both positive and explicitly mutated negative remain strict valid envelopes.
    PipetteApplicationPlanPostEnvelope.model_validate(payload)
    client, requests = client_for(payload)
    response = client.post('/api/bioxp/operator-controls/pipettes/application/plan',
        params={'expected_connection_generation': 77}, json=body,
        headers={'Idempotency-Key': lookup['idempotency_key']})
    assert len(requests) == 1
    assert json.loads(requests[0].content) == body
    assert response.status_code == (502 if mutated else 200), response.text


# Exact scalar types emitted by plan_load_tip: str, int, int, bool.
# Integral floats are not the producer's integer JSON values (StrictInt input).
TYPED_CASES = [
    ('tip_type', 1, True), ('tip_type', 0, False),
    ('tip_location', 0, False),
    ('home_z_after', True, 1), ('home_z_after', False, 0),
    ('tip_type', 1, 1.0), ('tip_location', 0, 0.0),
    ('tip_type', 1, '1'), ('home_z_after', True, 'true'),
]


@pytest.mark.parametrize('field,original,changed', TYPED_CASES)
@pytest.mark.parametrize('mutated', [False, True])
def test_direct_liquid_r1_type_exact_association(field, original, changed, mutated):
    lookup = json.loads((FIXTURES / 'plan-tip-get.json').read_bytes())
    payload = json.loads((FIXTURES / 'plan-tip-post.json').read_bytes())
    body = dict(lookup['record']['requested_inputs'])
    body[field] = original
    payload['requested_inputs'][field] = changed if mutated else original
    # No fixture files or authority metadata rewritten; this is an explicitly
    # synthetic association counterexample accepted by the unchanged model.
    envelope = PipetteApplicationPlanPostEnvelope.model_validate(payload)
    assert type(envelope.requested_inputs[field]) is type(changed if mutated else original)
    client, requests = client_for(json.dumps(payload).encode())
    response = client.post('/api/bioxp/operator-controls/pipettes/application/plan',
        params={'expected_connection_generation': 77}, json=body,
        headers={'Idempotency-Key': lookup['idempotency_key']})
    assert len(requests) == 1
    assert json.dumps(json.loads(requests[0].content), sort_keys=True) == json.dumps(body, sort_keys=True)
    assert requests[0].headers['idempotency-key'] == lookup['idempotency_key']
    assert response.status_code == (502 if mutated else 200), response.text
    if mutated:
        assert response.json()['detail'] == 'BioXP robot returned a mismatched plan request'
