"""Offline actual-owner discovery memo and native integrity regressions."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingSource
from routers import conformational_mapping as cm


@pytest.mark.asyncio
async def test_md_cm02_discovery_memo_io_and_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "_confornets_discovery_digest", None)
    weights = tmp_path / "weights"
    checkpoint = weights / "openfold3/of3-p2-155k.pt"
    checkpoint.parent.mkdir(parents=True)
    first = b"A" * (1024 * 1024 + 17)
    second = b"B" * len(first)
    checkpoint.write_bytes(first)
    monkeypatch.setattr(cm, "get_weights_root", lambda: weights)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    counts = dict(hash_calls=0, checkpoint_bytes_read=0, insert=0, update=0, delete=0, commits=0)
    observations = {}
    original_open = Path.open
    original_hash = cm._sha256_path

    class Reader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            self.handle.__enter__()
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def read(self, *args):
            data = self.handle.read(*args)
            counts['checkpoint_bytes_read'] += len(data)
            return data
        def __getattr__(self, key):
            return getattr(self.handle, key)

    def counted_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        return Reader(handle) if path == checkpoint and args and args[0] == 'rb' else handle

    def counted_hash(path):
        if path == checkpoint:
            counts['hash_calls'] += 1
        return original_hash(path)

    monkeypatch.setattr(Path, 'open', counted_open)
    monkeypatch.setattr(cm, '_sha256_path', counted_hash)

    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def sql_count(conn, cursor, statement, parameters, context, executemany):
        kind = statement.strip().split()[0].lower()
        if kind in ('insert', 'update', 'delete'):
            counts[kind] += 1

    @event.listens_for(engine.sync_engine, 'commit')
    def commit_count(conn):
        counts['commits'] += 1

    request = Request(dict(type='http', method='GET', scheme='http',
        path='/api/conformational-mapping/sources', query_string=b'', headers=[],
        client=('127.0.0.1', 1), server=('127.0.0.1', 1)))
    request.state.authenticated_principal = dict(subject='fixture-principal', roles=['scientist'])

    def capture(label, before):
        observations[label] = {key: counts[key] - before[key] for key in counts}

    async def browse():
        async with factory() as session:
            response = await cm.list_sources(request, session)
            return next(row for row in response['sources'] if row['managed_checkpoint'])

    try:
        before = dict(counts)
        row = await browse()
        capture('cold_browse', before)
        expected = hashlib.sha256(first).hexdigest()
        assert row['sha256'] == expected
        assert row['source_id'] == 'cm_src_server_confornets_checkpoint_' + expected[:32]
        before = dict(counts)
        for _ in range(4):
            assert (await browse())['source_id'] == row['source_id']
        capture('four_unchanged_browses', before)
        assert observations['four_unchanged_browses'] == dict(hash_calls=0,
            checkpoint_bytes_read=0, insert=0, update=0, delete=0, commits=0)
        assert observations['cold_browse']['insert'] == 1
        assert observations['cold_browse']['commits'] == 1

        before = dict(counts)
        async with factory() as session:
            assert (await cm._ensure_managed_confornets_checkpoint(session)).source_id == row['source_id']
            assert (await cm._source(session, row['source_id'], cm._PERSONAL_WORKFLOW_PRINCIPAL, {'confornets_checkpoint'})).source_id == row['source_id']
        capture('warm_helper_and_selected_lookup', before)
        assert observations['warm_helper_and_selected_lookup'] == dict(hash_calls=0,
            checkpoint_bytes_read=0, insert=0, update=0, delete=0, commits=0)
        for label, read in (
            ('default_read_after_warm', lambda session: cm._read_managed_confornets_checkpoint(session)),
            ('submission_after_warm', lambda session: cm._managed_checkpoint_for_submission(session, row['source_id'])),
        ):
            before = dict(counts)
            async with factory() as session:
                selected = await read(session)
                assert selected is not None
                assert selected.source_id == row['source_id']
            capture(label, before)
            assert observations[label] == dict(hash_calls=1,
                checkpoint_bytes_read=len(first), insert=0, update=0, delete=0, commits=0)

        stamp = checkpoint.stat()
        checkpoint.write_bytes(second)
        os.utime(checkpoint, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        before = dict(counts)
        changed = await browse()
        capture('same_size_change_restored_mtime', before)
        assert changed['sha256'] == hashlib.sha256(second).hexdigest()
        assert changed['source_id'] != row['source_id']
        async with factory() as session:
            with pytest.raises(HTTPException) as old:
                await cm._managed_checkpoint_for_submission(session, row['source_id'])
            assert old.value.status_code == 422
            selected = await cm._managed_checkpoint_for_submission(session, changed['source_id'])
            assert selected.content_sha256 == changed['sha256']

        replacement = checkpoint.with_suffix('.replacement')
        replacement.write_bytes(first)
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        replacement.replace(checkpoint)
        before = dict(counts)
        assert (await browse())['source_id'] == row['source_id']
        capture('replacement_to_original_bytes', before)
        assert observations['replacement_to_original_bytes']['insert'] == 0
        async with factory() as session:
            registered = await session.get(ConformationalMappingSource, row['source_id'])
            registered.content_sha256 = '0' * 64
            await session.commit()
        before = dict(counts)
        with pytest.raises(HTTPException) as conflict:
            await browse()
        assert conflict.value.status_code == 503
        capture('warm_identity_conflict', before)
        assert observations['warm_identity_conflict'] == dict(hash_calls=0,
            checkpoint_bytes_read=0, insert=0, update=0, delete=0, commits=0)
        checkpoint.unlink()
        before = dict(counts)
        async with factory() as session:
            assert await cm._ensure_managed_confornets_checkpoint(session) is None
            with pytest.raises(HTTPException) as absent:
                await cm._managed_checkpoint_for_submission(session, row['source_id'])
            assert absent.value.status_code == 503
        capture('absent_checkpoint', before)
        target = checkpoint.with_suffix('.target')
        target.write_bytes(first)
        checkpoint.symlink_to(target)
        before = dict(counts)
        async with factory() as session:
            assert await cm._ensure_managed_confornets_checkpoint(session) is None
        capture('symlink_checkpoint', before)
        # A different pathname must not hit even when every inode field matches.
        checkpoint.unlink()
        checkpoint.write_bytes(second)
        await browse()
        original_weights, original_checkpoint = weights, checkpoint
        weights = tmp_path / 'other-weights'
        checkpoint = weights / 'openfold3/of3-p2-155k.pt'
        checkpoint.parent.mkdir(parents=True)
        os.link(original_checkpoint, checkpoint)
        before = dict(counts)
        with pytest.raises(HTTPException) as root_conflict:
            await browse()
        assert root_conflict.value.status_code == 503
        capture('root_change_same_inode_conflict', before)
        assert observations['root_change_same_inode_conflict']['hash_calls'] == 1
        checkpoint.unlink()
        third = b'C' * len(first)
        checkpoint.write_bytes(third)
        before = dict(counts)
        assert (await browse())['sha256'] == hashlib.sha256(third).hexdigest()
        capture('root_change_new_bytes', before)
        assert observations['root_change_new_bytes']['hash_calls'] == 1
        assert observations['root_change_new_bytes']['insert'] == 1
        observations['fixture_bytes'] = len(first)
        if os.environ.get('CM_DISCOVERY_METRICS'):
            Path(os.environ['CM_DISCOVERY_METRICS']).write_text(json.dumps(observations, indent=2) + '\n')
    finally:
        await engine.dispose()


@pytest.mark.parametrize("uncertainty", ["before_failure", "after_failure", "changed_during_hash", "missing_ns"])
def test_md_cm02_uncertain_metadata_single_read_not_reused(tmp_path, monkeypatch, uncertainty):
    checkpoint = tmp_path / 'checkpoint.pt'
    payload = b'offline-checkpoint'
    checkpoint.write_bytes(payload)
    monkeypatch.setattr(cm, '_confornets_discovery_digest', None)
    original_lstat = Path.lstat
    original_hash = cm._sha256_path
    calls = {'observations': 0, 'hashes': 0, 'bytes': 0}

    def observe(path, *args, **kwargs):
        observed = original_lstat(path, *args, **kwargs)
        if path != checkpoint:
            return observed
        calls['observations'] += 1
        if (uncertainty == 'before_failure' and calls['observations'] == 1
                or uncertainty == 'after_failure' and calls['observations'] == 2):
            raise OSError('inert metadata uncertainty')
        if uncertainty == 'missing_ns':
            from types import SimpleNamespace
            return SimpleNamespace(st_mode=observed.st_mode, st_dev=observed.st_dev,
                st_ino=observed.st_ino, st_size=observed.st_size)
        return observed

    def counted_hash(path):
        calls['hashes'] += 1
        result = original_hash(path)
        calls['bytes'] += len(payload)
        if uncertainty == 'changed_during_hash':
            observed = path.stat()
            os.utime(path, ns=(observed.st_atime_ns, observed.st_mtime_ns + 1))
        return result

    monkeypatch.setattr(Path, 'lstat', observe)
    monkeypatch.setattr(cm, '_sha256_path', counted_hash)
    assert cm._confornets_discovery_checksum(checkpoint) == (hashlib.sha256(payload).hexdigest(), len(payload))
    assert calls['hashes'] == 1
    assert cm._confornets_discovery_digest is None
    assert cm._confornets_discovery_checksum(checkpoint) == (hashlib.sha256(payload).hexdigest(), len(payload))
    assert calls['hashes'] == 2
    assert calls['bytes'] == 2 * len(payload)


@pytest.mark.parametrize('field', ['path', 'device', 'inode', 'size', 'mtime_ns', 'ctime_ns'])
def test_md_cm02_each_observation_field_invalidates(tmp_path, monkeypatch, field):
    checkpoint = tmp_path / 'checkpoint.pt'
    checkpoint.write_bytes(b'checkpoint')
    observation = cm._confornets_checkpoint_observation(checkpoint)
    assert observation is not None
    old = list(observation)
    index = ['path', 'device', 'inode', 'size', 'mtime_ns', 'ctime_ns'].index(field)
    old[index] = old[index] + 1 if index else str(tmp_path / 'other.pt')
    monkeypatch.setattr(cm, '_confornets_discovery_digest', (tuple(old), '0' * 64))
    calls = []
    original = cm._sha256_path
    monkeypatch.setattr(cm, '_sha256_path', lambda path: (calls.append(path), original(path))[1])
    assert cm._confornets_discovery_checksum(checkpoint) == (hashlib.sha256(b'checkpoint').hexdigest(), 10)
    assert calls == [checkpoint]
    assert cm._confornets_discovery_digest == (observation, hashlib.sha256(b'checkpoint').hexdigest())


def test_md_cm02_hash_failure_keeps_existing_exception_no_retry(tmp_path, monkeypatch):
    checkpoint = tmp_path / 'checkpoint.pt'
    checkpoint.write_bytes(b'checkpoint')
    monkeypatch.setattr(cm, '_confornets_discovery_digest', None)
    calls = []

    def unavailable(path):
        calls.append(path)
        raise OSError('inert unreadable checkpoint')

    monkeypatch.setattr(cm, '_sha256_path', unavailable)
    with pytest.raises(OSError, match='inert unreadable checkpoint'):
        cm._confornets_discovery_checksum(checkpoint)
    assert calls == [checkpoint]
    assert cm._confornets_discovery_digest is None
