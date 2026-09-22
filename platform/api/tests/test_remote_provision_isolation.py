"""SQL regression tests for selected-worker admission and observation identity.

Uses a small SQLite table to isolate the production predicates. No provider,
SSH, asset transfer or scientific execution is involved.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, create_engine, update, func
from sqlalchemy.orm import declarative_base, Session
from services.remote_execution import preloading, progress

Base = declarative_base()


class Target(Base):
    __tablename__ = 'audit_execution_targets'
    id = Column(String, primary_key=True)
    active = Column(Boolean)
    state = Column(String)
    leased_job_id = Column(String, nullable=True)
    host = Column(String)
    port = Column(Integer)
    username = Column(String)
    remote_root = Column(String)
    host_key_sha256 = Column(String)
    activated_at = Column(DateTime)
    provider_metadata = Column(JSON)


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(preloading, 'ExecutionTarget', Target)
    monkeypatch.setattr(progress, 'ExecutionTarget', Target)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    now = datetime.utcnow()
    with Session(engine) as session:
        for name in ('a', 'b'):
            session.add(Target(id=name, active=True, state='ready', leased_job_id=None,
                host=name + '.invalid', port=22, username='root', remote_root='/worker',
                host_key_sha256='a' * 64, activated_at=now,
                provider_metadata={'inventory': {'status': 'complete', 'present': True,
                    'running': True, 'checked_at': now.isoformat()},
                    'preload': {'operation_id': 'prior', 'phase': 'source_download_ready'},
                    'managed_inventory': {'endpoint_sha256': 'fixture',
                        'observation': {'observed_at': now.isoformat()},
                        'refresh_failed': False}}))
        session.commit()
        yield session
    engine.dispose()


def snapshot(row):
    return SimpleNamespace(**{key: getattr(row, key) for key in (
        'id', 'host', 'port', 'username', 'remote_root', 'host_key_sha256')})


def admit(session):
    row = session.get(Target, 'a')
    return session.execute(update(Target).where(preloading.admission_clause(snapshot(row)))
        .values(state='ready')).rowcount


@pytest.mark.parametrize('other_state', ['ready', 'probing', 'unavailable'])
def test_unrelated_worker_never_vetoes_admission(store, other_state):
    store.get(Target, 'b').state = other_state
    store.commit()
    assert admit(store) == 1


@pytest.mark.parametrize('fault', ['lease', 'checking', 'cancelling', 'recovery_blocked', 'stale', 'probing'])
def test_own_worker_conflicts_still_refuse(store, fault):
    row = store.get(Target, 'a')
    metadata = deepcopy(row.provider_metadata)
    if fault == 'lease':
        row.leased_job_id = 'running-science'
    elif fault == 'probing':
        row.state = 'probing'
    elif fault == 'stale':
        metadata['inventory']['checked_at'] = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    else:
        metadata['preload']['phase'] = fault
    row.provider_metadata = metadata
    store.commit()
    assert admit(store) == 0


def observation_fence(session):
    row = session.get(Target, 'a')
    return preloading.inventory_observation_clause(snapshot(row),
        row.provider_metadata['preload']['operation_id'],
        deepcopy(row.provider_metadata.get('managed_inventory', {})), row.activated_at)


def invalidate(session, fence):
    result = session.execute(update(Target).where(fence).values(
        provider_metadata=func.json_set(Target.provider_metadata,
            '$.managed_inventory.refresh_failed', func.json('true'))))
    session.commit()
    session.expire_all()
    return result.rowcount


@pytest.mark.parametrize('later_event', ['other_probing', 'stale_provider', 'new_lease', 'inactive'])
def test_failed_observation_invalidates_even_when_admission_now_refuses(store, later_event):
    fence = observation_fence(store)
    row = store.get(Target, 'a')
    if later_event == 'other_probing':
        store.get(Target, 'b').state = 'probing'
    elif later_event == 'new_lease':
        row.leased_job_id = 'new-science'
    elif later_event == 'inactive':
        row.active = False
    else:
        metadata = deepcopy(row.provider_metadata)
        metadata['inventory']['checked_at'] = '2000-01-01T00:00:00'
        row.provider_metadata = metadata
    store.commit()
    assert invalidate(store, fence) == 1
    assert store.get(Target, 'a').provider_metadata['managed_inventory']['refresh_failed'] is True


@pytest.mark.parametrize('replacement', ['observation', 'endpoint', 'operation', 'attachment'])
def test_old_failure_cannot_invalidate_newer_authority(store, replacement):
    fence = observation_fence(store)
    row = store.get(Target, 'a')
    if replacement == 'endpoint':
        row.host = 'replacement.invalid'
    elif replacement == 'attachment':
        row.activated_at += timedelta(seconds=1)
    else:
        metadata = deepcopy(row.provider_metadata)
        if replacement == 'observation':
            metadata['managed_inventory']['observation']['observed_at'] = 'newer'
        else:
            metadata['preload']['operation_id'] = 'replacement'
        row.provider_metadata = metadata
    store.commit()
    assert invalidate(store, fence) == 0
    assert store.get(Target, 'a').provider_metadata['managed_inventory']['refresh_failed'] is False


def test_initial_absent_inventory_is_invalidated_without_fabricating_observation(store):
    row = store.get(Target, 'a')
    metadata = deepcopy(row.provider_metadata)
    metadata.pop('managed_inventory')
    row.provider_metadata = metadata
    store.commit()
    fence = observation_fence(store)
    assert invalidate(store, fence) == 1
    assert store.get(Target, 'a').provider_metadata['managed_inventory'] == {'refresh_failed': True}


def test_success_and_failure_share_same_observation_generation(store):
    fence = observation_fence(store)
    row = store.get(Target, 'a')
    newer = deepcopy(row.provider_metadata)
    newer['managed_inventory']['observation']['observed_at'] = 'newer-success'
    store.execute(update(Target).where(preloading.admission_clause(snapshot(row)), fence)
        .values(provider_metadata=newer))
    store.commit()
    assert invalidate(store, fence) == 0
