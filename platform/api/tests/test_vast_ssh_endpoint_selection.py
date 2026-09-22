"""Bounded provider-route selection through real refresh/attachment owners."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.remote_execution import targets, transport, vast
from services.remote_execution.contracts import ExecutionTargetActivateRequest, ExecutionTargetInventoryResponse
from test_vast_inventory_reconciliation import store

DIRECT = ('203.0.113.10', 22)
PROXY = ('ssh.example.test', 15938)


@pytest.fixture(autouse=True)
def private_known_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_REMOTE_KNOWN_HOSTS', str(tmp_path / 'known_hosts'))


def provider(monkeypatch, *, proxy=True):
    async def read():
        raw = dict(id='49674511', actual_status='running', public_ipaddr=DIRECT[0],
                   ports={'22/tcp': [{'HostPort': str(DIRECT[1])}]})
        if proxy:
            raw.update(ssh_host=PROXY[0], ssh_port=PROXY[1])
        return ExecutionTargetInventoryResponse(provider='vast', available=True, credential_configured=True,
            message='fixture', instances=[vast._normalize(raw)])
    monkeypatch.setattr(targets, 'list_owned_instances', read)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['unreachable', 'both_unreachable', 'retained_pin', 'malformed', 'authentication', 'known_host', 'pinned', 'lease', 'absent', 'replaced'])
async def test_initial_selection_and_refresh_are_bounded_and_fenced(store, monkeypatch, failure):
    session, factory = store
    provider(monkeypatch)
    scans, pins, authenticated = [], [], []
    if failure == 'retained_pin':
        transport.known_hosts_path().write_text(DIRECT[0] + ' ssh-ed25519 YQ==\n')
    async def scan(host, port):
        scans.append((host, port))
        if failure in {'unreachable', 'retained_pin', 'both_unreachable', 'pinned', 'lease', 'absent', 'replaced'}:
            if (host, port) == DIRECT or failure == 'both_unreachable':
                if failure in {'lease', 'absent', 'replaced'}:
                    async with factory() as other:
                        row = await targets.get_target(other, 'vast:49674511')
                        if failure == 'lease':
                            row.leased_job_id = 'racing-owner'
                        else:
                            metadata = deepcopy(row.provider_metadata)
                            if failure == 'absent': metadata['inventory']['present'] = False
                            else: metadata['setup']['started_at'] = 'replacement'
                            row.provider_metadata = metadata
                        await other.commit()
                raise transport.RemoteHostKeyUnavailable('Unable to read the remote SSH host key')
        if failure == 'malformed':
            raise transport.RemoteTransportError('Remote SSH host key is malformed')
        return 'fixture key', 'a' * 64
    async def pin(*args):
        pins.append(args)
        if failure == 'known_host':
            raise transport.RemoteTransportError('Remote SSH host key changed')
    async def run(connection, argv, **kwargs):
        if argv[0] == 'sh':
            authenticated.append((connection.host, connection.port))
            if failure == 'authentication':
                raise transport.RemoteConnectionError('Remote SSH connection or authentication failed')
            return SimpleNamespace(stdout='BMS_ATTACHED\nBMS_TELEMETRY\n')
        raise transport.RemoteTransportError('Mount namespaces unavailable; use a compatible VM')
    monkeypatch.setattr(targets, 'capture_host_key', scan)
    monkeypatch.setattr(targets, 'persist_host_key', pin)
    monkeypatch.setattr(targets, 'run_remote', run)
    if failure == 'pinned':
        row = await targets.get_target(session, 'vast:49674511')
        row.host_key_sha256 = 'a' * 64
        await session.commit()
    with pytest.raises(targets.ExecutionTargetError):
        await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
    row = await targets.get_target(session, 'vast:49674511')
    assert scans == ([DIRECT, PROXY] if failure in {'unreachable', 'both_unreachable'} else [DIRECT])
    if failure == 'unreachable':
        assert authenticated == [PROXY] and len(pins) == 1
        assert row.active and row.host_key_sha256 == 'a' * 64
        generation = deepcopy(row.provider_metadata['attachment'])
        for _ in range(3):
            inventory = await targets.refresh_vast_targets(session)
            await session.refresh(row)
            assert row.active and (row.host, row.port) == PROXY
            assert row.provider_metadata['attachment'] == generation
            assert (inventory.instances[0].host, inventory.instances[0].port) == PROXY
        # Reattachment uses the selected route, not a recurring direct timeout.
        with pytest.raises(targets.ExecutionTargetError):
            await targets.activate_target(session, ExecutionTargetActivateRequest(provider_instance_id='49674511'))
        assert scans == [DIRECT, PROXY, PROXY]
        provider(monkeypatch, proxy=False)
        await targets.refresh_vast_targets(session)
        await session.refresh(row)
        assert not row.active and (row.host, row.port) == DIRECT
        assert row.host_key_sha256 == 'a' * 64  # no identity reset
    else:
        assert not row.active
        assert 'attachment' not in row.provider_metadata
        if failure in {'both_unreachable', 'retained_pin', 'malformed', 'pinned', 'lease', 'absent', 'replaced'}:
            assert not pins and not authenticated


@pytest.mark.asyncio
@pytest.mark.parametrize('result', ['empty', 'timeout', 'malformed', 'partial', 'valid'])
async def test_keyscan_only_no_key_is_retryable(monkeypatch, result):
    async def run(*args, **kwargs):
        assert kwargs['timeout'] == 15
        if result == 'timeout':
            raise transport.RemoteTransportError('Remote transport timed out')
        return transport.CommandResult(1 if result in {'empty', 'partial'} else 0,
            '' if result == 'empty' else 'host ssh-ed25519 ' + ('YQ==' if result in {'valid', 'partial'} else '!'), '')
    monkeypatch.setattr(transport, '_run', run)
    if result == 'valid':
        assert len((await transport.capture_host_key(*DIRECT))[1]) == 64
    else:
        with pytest.raises(transport.RemoteTransportError) as caught:
            await transport.capture_host_key(*DIRECT)
        assert isinstance(caught.value, transport.RemoteHostKeyUnavailable) == (result in {'empty', 'timeout'})


def test_normalization_candidates_are_typed_bounded_and_deduplicated():
    instance = vast._normalize(dict(id=1, public_ipaddr=DIRECT[0], ports={'22/tcp': [{'HostPort': 22}, {'HostPort': 23}]},
                                    ssh_host=PROXY[0], ssh_port=PROXY[1]))
    assert [(e.host, e.port) for e in instance.ssh_endpoints] == [DIRECT, PROXY]
    assert instance.raw == {}
    same = vast._normalize(dict(id=1, public_ipaddr=DIRECT[0], direct_port_start=22, ssh_host=DIRECT[0], ssh_port=22))
    assert len(same.ssh_endpoints) == 1
