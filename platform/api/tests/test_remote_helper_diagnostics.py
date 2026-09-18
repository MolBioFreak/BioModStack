"""Known helper codes survive transport and preload; untrusted prose does not."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from services.remote_execution import transport as codes
from services.remote_execution import preloading, transport


@pytest.mark.parametrize('code', sorted(codes.HELPER_FAILURE_MESSAGES))
def test_known_code_has_safe_actionable_message(code):
    envelope = json.dumps({'state': 'failed', 'error': code})
    assert codes.helper_failure_code(envelope) == code
    error = transport.RemoteHelperError(code)
    assert isinstance(error, transport.RemoteTransportError)
    assert error.code == code
    message = preloading.failure_message(error, 'verifying')
    assert code in message
    assert 'Preload failed during' not in message


@pytest.mark.parametrize('value', [
    '', 'SECRET worker log /private/file', 'null', '[]',
    '{"state":"failed","error":"not_a_known_code"}',
    '{"state":"success","error":"request_too_large"}',
    '{"state":"failed","error":"request_too_large","path":"SECRET"}',
    '{"state":"failed","error":["request_too_large"]}',
    '{"state":"failed","error":"bad","error":"request_too_large"}',
    '{"state":"failed","error":"request_too_large"}\nUNTRUSTED',
    '{"state":"failed","error":"request_too_large"}' + ' ' * 600 + 'x',
])
def test_only_bounded_exact_final_envelope_is_accepted(value):
    assert codes.helper_failure_code(value) is None


@pytest.mark.asyncio
async def test_transport_to_persisted_message_discards_stderr_secrets(monkeypatch):
    async def run(*args, **kwargs):
        return transport.CommandResult(1, '', 'SECRET/path?token=private\n' + json.dumps(
            {'state': 'failed', 'error': 'document_identity_mismatch'}))
    monkeypatch.setattr(transport, '_run', run)
    monkeypatch.setattr(transport, '_ssh_base', lambda _: ['ssh', 'fixture'])
    connection = SimpleNamespace(provision_operation_id=None)
    with pytest.raises(transport.RemoteHelperError) as caught:
        await transport.run_remote(connection, ['python3', 'fixture-helper'])
    assert caught.value.code == 'document_identity_mismatch'
    assert 'SECRET' not in str(caught.value)
    assert 'SECRET' not in preloading.failure_message(caught.value, 'verifying')


@pytest.mark.asyncio
async def test_authentication_failure_wins_over_a_fake_helper_envelope(monkeypatch):
    async def run(*args, **kwargs):
        return transport.CommandResult(255, '', json.dumps({'state': 'failed', 'error': 'request_too_large'}))
    monkeypatch.setattr(transport, '_run', run)
    monkeypatch.setattr(transport, '_ssh_base', lambda _: ['ssh', 'fixture'])
    with pytest.raises(transport.RemoteConnectionError):
        await transport.run_remote(SimpleNamespace(provision_operation_id=None), ['true'])


def test_cancellation_and_unknown_errors_keep_existing_behavior():
    assert preloading.failure_message(asyncio.CancelledError(), 'verifying') == 'Preload interrupted; explicitly retry'
    message = preloading.failure_message(transport.RemoteTransportError('SECRET/path'), 'verifying')
    assert message == 'Preload failed during verifying; verify worker and recipe, then explicitly retry'
    assert 'SECRET' not in message
    with pytest.raises(ValueError):
        transport.RemoteHelperError('unknown')
