"""Replay the retained live data sweep; no hardware queries or resubmission.

POST is the parent's unmodified captured receipt envelope for request
16650780-76db-460d-baf6-762ea3ab34de. GET is the actual robot project_lookup_row
output from that exact retained SQLite row after its typed source-field repair,
not a hand-built compatible lookup. Only transport and connection are replaced.
Unknown/null/invalid variants below are explicitly synthetic model controls.
"""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from services.bioxp.operator_models import (
    PipetteReadbackPostEnvelope, PipetteReadbackResponse,
    PipetteDirectRequestLookupResponse,
)
from test_bioxp_direct_liquid_combined import client_for

FIXTURES = Path(__file__).with_name('fixtures')
POST = json.loads((FIXTURES / 'bioxp_live_readback_collection_source_post.json').read_text())
GET = json.loads((FIXTURES / 'bioxp_live_readback_collection_source_get.json').read_text())
FLAGS = ('delivery_verified', 'controller_acknowledged', 'completion_verified',
         'hardware_postcondition_verified', 'physical_effect_verified')


@pytest.mark.parametrize('lookup', [False, True], ids=['exact-post', 'retained-lookup'])
def test_actual_readback_source_survives_model_and_relay(lookup):
    payload = GET if lookup else POST
    model = PipetteDirectRequestLookupResponse if lookup else PipetteReadbackPostEnvelope
    validated = model.model_validate(payload)
    observed = validated
    if isinstance(validated, PipetteDirectRequestLookupResponse):
        assert validated.record is not None
        observed = validated.record.result
    assert isinstance(observed, PipetteReadbackResponse)
    source = observed.collection_source
    assert source is not None
    assert source.model_dump(mode='json') == POST['collection_source']
    assert GET['record']['result']['collection_source'] == POST['collection_source']
    client, requests = client_for(json.dumps(payload).encode(), {'http_status': 200, 'headers': {}})
    with client:
        if lookup:
            response = client.get('/api/bioxp/operator-controls/pipettes/requests',
                params={'request_kind': 'readback', 'expected_connection_generation': 77},
                headers={'Idempotency-Key': GET['idempotency_key']})
        else:
            response = client.post('/api/bioxp/operator-controls/pipettes/readback',
                params={'expected_connection_generation': 77}, json={'include_data': True},
                headers={'Idempotency-Key': GET['idempotency_key']})
    assert response.status_code == 200, response.text
    result = response.json()['record']['result'] if lookup else response.json()
    expected = GET['record']['result']
    assert PipetteReadbackResponse.model_validate(result) == PipetteReadbackResponse.model_validate(expected)
    assert result['collection_source'] == POST['collection_source']
    assert [channel['semantic_ok'] for channel in result['channels']] == [True] * 4
    assert all(result[flag] is False for flag in FLAGS)
    assert len(requests) == 1
    assert requests[0].method == ('GET' if lookup else 'POST')
    assert requests[0].headers['idempotency-key'] == GET['idempotency_key']
    if lookup:
        assert response.json()['lookup_state'] == 'resolved'
        assert response.json()['record']['command_status'] == 'observed'
        assert response.json()['retry_forbidden'] is True
        assert response.json()['live_query_performed'] is False
        assert requests[0].content == b''
    else:
        assert json.loads(requests[0].content) == {'include_data': True}


@pytest.mark.parametrize('variant', ['omitted', 'null', 'unknown'])
@pytest.mark.parametrize('lookup', [False, True])
def test_source_missing_or_unknown_is_evidence_not_admission(variant, lookup):
    payload = copy.deepcopy(GET if lookup else POST)
    result = payload['record']['result'] if lookup else payload
    if variant == 'omitted':
        del result['collection_source']
    elif variant == 'null':
        result['collection_source'] = None
    else:
        for stamp in result['collection_source']['identity']['channels']:
            stamp.update(actor=None, revision=None, reader_generation=None)
        for observation in result['collection_source']['channels']:
            observation.update(tip_loaded=None, verified=False)
    model = PipetteDirectRequestLookupResponse if lookup else PipetteReadbackPostEnvelope
    value = model.model_validate(payload)
    observed = value
    if isinstance(value, PipetteDirectRequestLookupResponse):
        assert value.record is not None
        observed = value.record.result
    assert isinstance(observed, PipetteReadbackResponse)
    if variant != 'unknown':
        assert observed.collection_source is None
    else:
        assert observed.collection_source is not None
        assert observed.collection_source.model_dump() == result['collection_source']
    assert all(getattr(observed, flag) is False for flag in FLAGS)


@pytest.mark.parametrize('location,field,value', [
    ('source', 'extra', True), ('identity', 'extra', True),
    ('stamp', 'extra', True), ('observation', 'extra', True),
    ('identity', 'interrupt_epoch', True), ('stamp', 'reader', '123'),
    ('stamp', 'actor', 123), ('stamp', 'revision', True),
    ('stamp', 'reader_generation', '1'), ('observation', 'tip_loaded', 1),
    ('observation', 'verified', 'true'),
])
def test_source_remains_closed_and_strict(location, field, value):
    payload = copy.deepcopy(POST)
    source = payload['collection_source']
    target = {'source': source, 'identity': source['identity'],
              'stamp': source['identity']['channels'][0], 'observation': source['channels'][0]}[location]
    target[field] = value
    with pytest.raises(ValidationError):
        PipetteReadbackPostEnvelope.model_validate(payload)
