"""Lifespan-owned remote sampling. GETs and admission never initiate SSH."""
from __future__ import annotations

import asyncio
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
import uuid

from sqlalchemy import select
from database import ExecutionTarget
from .transport import RemoteConnection, run_remote

INTERVAL = 10.0
FRESH_SECONDS = 20.0
HISTORY_SECONDS = 3600
PROBE = Path(__file__).with_name('telemetry_probe.py').read_bytes()


def identity(target):
    return (target.id, target.host, target.port, target.username, target.remote_root,
            target.host_key_sha256, str(target.activated_at))


def derive(raw, previous):
    """Counter resets and first samples are unknown, never synthetic zero rates."""
    elapsed = raw['worker_time'] - previous['worker_time'] if previous else 0
    cpu = dict(raw.get('cpu', {}))
    cpu['utilization'] = None
    old_cpu = previous.get('cpu', {}) if previous else {}
    if elapsed > 0 and cpu.get('scope') == old_cpu.get('scope'):
        current, old = cpu.get('usage_seconds'), old_cpu.get('usage_seconds')
        cores = cpu.get('allocated_cores')
        if current is not None and old is not None and current >= old and cores:
            cpu['utilization'] = min(100, (current - old) / elapsed / cores * 100)
        elif cpu.get('host_total_ticks') is not None and old_cpu.get('host_total_ticks') is not None:
            total = cpu['host_total_ticks'] - old_cpu['host_total_ticks']
            idle = cpu['host_idle_ticks'] - old_cpu['host_idle_ticks']
            if total > 0 and 0 <= idle <= total:
                cpu['utilization'] = (total - idle) / total * 100
    network = []
    for name, counters in raw.get('network', {}).items():
        old = previous.get('network', {}).get(name, {}) if previous else {}
        rates = {}
        for direction in ('rx', 'tx'):
            key = direction + '_bytes'
            delta = counters[key] - old[key] if key in old else -1
            rates[direction + '_bytes_per_second'] = delta / elapsed if elapsed > 0 and delta >= 0 else None
        network.append({'interface': name, **rates})
    return {'cpu': cpu, 'ram': raw.get('ram', {}), 'disk': raw.get('disk', {}), 'network': network}


class RemoteTelemetry:
    def __init__(self):
        self.entries = {}
        self.epoch = uuid.uuid4().hex
        self.sequence = 0

    def read(self, target, since=None, *, include_history=True):
        from .targets import _target_response, target_eligible
        base = {'source': 'active_vast', 'available': False, 'target': None, 'gpus': [],
                'history': [], 'cursor': f'{self.epoch}:{self.sequence}', 'reset': since is None,
                'sample_interval_seconds': INTERVAL, 'retention_seconds': HISTORY_SECONDS}
        if target is None or not target_eligible(target):
            return base
        base['target'] = _target_response(target).model_dump(mode='json')
        entry = self.entries.get(identity(target))
        if not entry or not entry['history']:
            return {**base, 'error': 'Waiting for background worker sample'}
        stream = entry.get('epoch', self.epoch)
        base['cursor'] = f'{stream}:{self.sequence}'
        now = time.monotonic()
        self._prune(entry, now)
        if not entry['history']:
            return {**base, 'error': 'Remote telemetry has expired'}
        cursor = -1
        if since:
            try:
                epoch, value = since.split(':')
                if epoch == stream:
                    cursor = int(value)
            except (ValueError, TypeError):
                pass
        base['reset'] = cursor < 0 or cursor > self.sequence
        if base['reset']:
            cursor = -1
        latest = entry['history'][-1]
        base.update(deepcopy(latest[2]))
        base['history'] = [deepcopy(sample) for seq, _, sample in entry['history'] if seq > cursor] if include_history else []
        if now - latest[1] > FRESH_SECONDS:
            base.update(available=False, gpus=[], error='Remote telemetry is stale')
        return base

    @staticmethod
    def _prune(entry, now):
        while entry['history'] and now - entry['history'][0][1] > HISTORY_SECONDS:
            entry['history'].popleft()

    async def collect(self, target, entry):
        started = time.monotonic()
        sample = {'observed_at': datetime.now(timezone.utc).isoformat(), 'available': False, 'gpus': []}
        try:
            connection = RemoteConnection.from_target(target)
            response = await run_remote(connection, ['python3', '-', connection.remote_root],
                                        input_bytes=PROBE, timeout=6)
            if len(response.stdout.encode()) > 65536:
                raise ValueError('oversize telemetry')
            raw = json.loads(response.stdout)
            sample.update(derive(raw, entry.get('raw')))
            sample['gpus'] = [{**gpu, 'id': f'{target.id}:gpu:{gpu["index"]}',
                               'execution_target_id': target.id, 'controls': {'fan': False, 'power': False}}
                              for gpu in raw['gpus']]
            sample['available'] = bool(sample['gpus'])
            if not sample['available']:
                sample['error'] = 'Remote GPU readings unavailable'
            entry['raw'] = raw
            entry['failures'] = 0
            sample['payload_bytes'] = len(response.stdout.encode())
        except Exception:
            entry['raw'] = None
            entry['failures'] += 1
            sample['error'] = 'Remote collection failed or timed out'
        sample['collection_duration_ms'] = round((time.monotonic() - started) * 1000, 2)
        self.sequence += 1
        sample['sequence'] = self.sequence
        entry['history'].append((self.sequence, started, sample))
        self._prune(entry, time.monotonic())
        entry['due'] = started + min(60, INTERVAL * 2 ** min(entry['failures'], 3))

    async def run(self, session_factory, stop):
        from .targets import target_eligible
        try:
            while not stop.is_set():
                try:
                    async with session_factory() as session:
                        targets = list((await session.scalars(select(ExecutionTarget).where(
                            ExecutionTarget.active.is_(True), ExecutionTarget.state == 'ready'))).all())
                        targets = [target for target in targets if target_eligible(target)]
                        # Snapshot ORM fields before the DB session closes; no DB lock during SSH.
                        for target in targets:
                            session.expunge(target)
                    wanted = {identity(target) for target in targets}
                    for key in list(self.entries):
                        if key not in wanted:
                            entry = self.entries.pop(key)
                            if entry.get('task'):
                                entry['task'].cancel()
                                await asyncio.gather(entry['task'], return_exceptions=True)
                    for target in targets:
                        key = identity(target)
                        entry = self.entries.setdefault(key, {'history': deque(maxlen=361), 'failures': 0, 'due': 0, 'epoch': uuid.uuid4().hex})
                        task = entry.get('task')
                        if (task is None or task.done()) and time.monotonic() >= entry['due']:
                            entry['task'] = asyncio.create_task(self.collect(target, entry))
                except Exception:
                    logging.getLogger(__name__).warning('Remote telemetry target refresh unavailable')
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
        finally:
            tasks = [entry['task'] for entry in self.entries.values() if entry.get('task')]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.entries.clear()


remote_telemetry = RemoteTelemetry()
