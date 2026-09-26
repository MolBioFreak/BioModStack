"""Measured live listener ownership is optional evidence, never an admission gate.

The source-identity fixture is extracted unchanged from live request
 ee73acdb-600f-4da7-8a44-170722f28da2 on the deployed robot. The relay case
explicitly composes that identity with the earlier captured readback body.
"""
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from services.bioxp.operator_models import OperatorReportListenerV1, PipetteDirectReleaseIdentity
from test_bioxp_direct_liquid_combined import client_for

FIXTURES = Path(__file__).with_name('fixtures')
SOURCE_IDENTITY = json.loads((FIXTURES / 'bioxp_live_readback_source_identity.json').read_text())
RELEASE = SOURCE_IDENTITY['release_identity']


def test_actual_measured_release_listeners_are_preserved():
    result = PipetteDirectReleaseIdentity.model_validate(RELEASE)
    assert result.binding.observed_listener is not None
    assert result.observation.listener is not None
    assert result.binding.observed_listener.model_dump() == RELEASE['binding']['observed_listener']
    assert result.observation.listener.model_dump() == RELEASE['observation']['listener']


def test_measured_listener_metadata_does_not_reject_readback_relay():
    payload = json.loads((FIXTURES / 'bioxp_live_readback_collection_source_post.json').read_text())
    payload['source_identity'] = copy.deepcopy(SOURCE_IDENTITY)
    client, requests = client_for(json.dumps(payload).encode(), {'http_status': 200, 'headers': {}})
    with client:
        response = client.post('/api/bioxp/operator-controls/pipettes/readback',
            params={'expected_connection_generation': 77}, json={'include_data': True},
            headers={'Idempotency-Key': 'offline:measured-listener'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['channels'] == payload['channels']
    assert result['collection_source'] == payload['collection_source']
    assert result['physical_effect_verified'] is False
    assert 'source_identity' not in result  # Existing private/public boundary remains.
    assert len(requests) == 1


@pytest.mark.parametrize('fields', [{}, {'owner_pid': None, 'socket_inode': None, 'owner_cgroup_sha256': None}])
def test_legacy_or_unknown_owner_remains_accepted(fields):
    row = {'host': '0.0.0.0', 'port': 8123, **fields}
    result = OperatorReportListenerV1.model_validate(row)
    assert result.model_dump(exclude_unset=True) == row


@pytest.mark.parametrize('field,value', [
    ('owner_pid', True), ('owner_pid', '89574'), ('socket_inode', True),
    ('socket_inode', '463982'), ('owner_cgroup_sha256', 123), ('unexpected', True),
])
def test_listener_remains_closed_and_does_not_coerce(field, value):
    row = copy.deepcopy(RELEASE['observation']['listener'])
    row[field] = value
    with pytest.raises(ValidationError):
        OperatorReportListenerV1.model_validate(row)
