"""Single-computer MSA preparation boundary; not a worker rate limiter.

Disabled unless a deployment-owned config binds this computer and a qualified
fixed egress. Workers receive prepared artifacts, never this execution context.
An interrupted/failed operation blocks the queue for explicit reconciliation:
we do not resubmit ambiguous public-service tickets automatically.
"""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
import fcntl
import hashlib
import json
import os
import threading
import time

PUBLIC_HOST = 'https://api.colabfold.com'
BLOCKED = ('MSA search requires controller-only serialized preparation; workers must '
           'receive verified prepared alignments. No independently submitting workers are permitted.')
_active: ContextVar[tuple[int, int] | None] = ContextVar('bms_msa_controller_operation', default=None)
_state_path: ContextVar[Path | None] = ContextVar('bms_msa_controller_state', default=None)


def require_controller_submission(host: str = PUBLIC_HOST) -> None:
    identity = _active.get()
    if identity != (os.getpid(), threading.get_ident()):
        raise RuntimeError(BLOCKED)
    if host.rstrip('/') != PUBLIC_HOST:
        raise ValueError('MSA endpoint substitution is disabled')


def record_ticket(ticket_id: str, api_mode: str) -> None:
    require_controller_submission()
    path = _state_path.get()
    if path is None:
        raise RuntimeError(BLOCKED)
    state = json.loads(path.read_text())
    state.setdefault('tickets', []).append({'ticket_id': ticket_id, 'api_mode': api_mode})
    _write(path, state)


def _write(path: Path, state: dict) -> None:
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(state, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def prepare(config_path: Path, request: dict, operation):
    """Execute a whole preparation under one cross-process serial lock.

    Operation runs in this process only and must return a JSON-serializable
    receipt (normally a prepared input manifest). Request includes the complete
    effective settings and input digests. Completed identical requests replay
    the receipt; artifact callers must reverify files before delivery.
    This is a service integration primitive, not an exposed worker RPC/CLI.
    """
    config = json.loads(config_path.read_text())
    machine = Path('/etc/machine-id').read_text().strip()
    if (config.get('machine_id') != machine or config.get('qualified_single_egress') is not True
            or not config.get('egress_identity') or config.get('role') != 'msa_controller'):
        raise RuntimeError(BLOCKED)
    require_host = config.get('host', PUBLIC_HOST)
    if require_host != PUBLIC_HOST:
        raise ValueError('MSA endpoint substitution is disabled')
    root = Path(config['state_dir'])
    if not root.is_absolute():
        raise ValueError('Controller state_dir must be absolute')
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if _active.get() is not None:
        raise RuntimeError('Nested MSA controller preparation is forbidden')
    # One lock for the entire controller, not per sequence/cache/job/worker.
    with (root / 'submission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        active_path = root / 'active.json'
        if active_path.exists():
            raise RuntimeError('MSA controller requires reconciliation of interrupted/failed preparation')
        receipt_path = root / f'{key}.json'
        if receipt_path.exists():
            return json.loads(receipt_path.read_text())['result']
        state = {'request_sha256': key, 'status': 'preparing', 'started_at': time.time(),
                 'service': PUBLIC_HOST, 'egress_identity': config['egress_identity'],
                 'provider_database_version': None}
        _write(active_path, state)
        token = _active.set((os.getpid(), threading.get_ident()))
        state_token = _state_path.set(active_path)
        try:
            result = operation()
            state = json.loads(active_path.read_text())
            state.update(status='complete', retrieved_at=time.time(), result=result)
            _write(receipt_path, state)
            active_path.unlink()
            return result
        except BaseException as exc:
            state = json.loads(active_path.read_text())
            state.update(status='blocked_reconciliation', error_type=type(exc).__name__)
            _write(active_path, state)
            raise
        finally:
            _active.reset(token)
            _state_path.reset(state_token)
