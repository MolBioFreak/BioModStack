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
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def validate_controller_config(config_path: Path) -> dict:
    """Validate deployment qualification without submitting or creating state.

    This checks the existing operator assertion, not live egress connectivity.
    The returned configuration is internal and must not be a public receipt.
    """
    config = json.loads(Path(config_path).read_text())
    if not isinstance(config, dict):
        raise RuntimeError(BLOCKED)
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
    return config


def default_controller_config_path() -> Path:
    """Where the controller declaration lives unless BMS_MSA_CONTROLLER_CONFIG names another file."""
    from biomodstack_runtime_profile import get_biomodstack_config_dir
    return get_biomodstack_config_dir() / 'msa-controller.json'


def default_controller_state_dir() -> Path:
    home = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local' / 'state')
    return Path(home).expanduser().resolve() / 'biomodstack' / 'msa-controller'


def enable_on_this_computer(config_path: Path) -> dict:
    """Operator action: declare this computer the one that submits public ColabFold searches.

    Writes (or rebinds) the declaration for the current /etc/machine-id, so a
    reinstalled or moved installation is re-enabled by repeating the action. An
    existing file keeps its state directory and egress label. Nothing is submitted.
    """
    import socket
    import tempfile
    config_path = Path(config_path)
    try:
        existing = json.loads(config_path.read_text())
    except (OSError, ValueError):
        existing = {}
    if not isinstance(existing, dict):
        existing = {}
    state_dir = existing.get('state_dir')
    if not isinstance(state_dir, str) or not Path(state_dir).is_absolute():
        state_dir = str(default_controller_state_dir())
    config = {
        'role': 'msa_controller',
        'machine_id': Path('/etc/machine-id').read_text().strip(),
        'qualified_single_egress': True,
        'egress_identity': existing.get('egress_identity') or f'controller:{socket.gethostname()}',
        'host': PUBLIC_HOST,
        'state_dir': state_dir,
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{config_path.name}.', dir=config_path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(config, indent=2, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config_path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return validate_controller_config(config_path)


def disable_on_this_computer(config_path: Path) -> bool:
    """Remove the declaration. Cached alignments keep replaying; new public searches stop."""
    try:
        Path(config_path).unlink()
    except FileNotFoundError:
        return False
    return True


def prepare(config_path: Path, request: dict, operation, *, resume_same_request=False):
    """Serialize controller work; opt-in is ONLY for the durable shared client.

    Legacy callbacks remain non-resumable. The opt-in must be present on the
    original attempt and its retry, with the identical request (including cache
    identity). Shared-client replays execute its verified cache read, not a stale
    controller receipt. This internal primitive is not a worker RPC.
    """
    from biomodstack_msa_api import PendingMSA, ReconciliationRequired, _check_preparation_stop

    if type(resume_same_request) is not bool:
        raise ValueError('resume_same_request must be boolean')
    config = validate_controller_config(config_path)
    root = Path(config['state_dir'])
    root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if _active.get() is not None:
        raise RuntimeError('Nested MSA controller preparation is forbidden')
    # One lock for the entire controller, not per sequence/cache/job/worker.
    with (root / 'submission.lock').open('a') as lock:
        # A queued caller must join on stop without waiting for another job's
        # provider operation, or touching that owner's active ticket/receipt.
        while True:
            _check_preparation_stop()
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.1)
        _check_preparation_stop()
        active_path = root / 'active.json'
        state = None
        if active_path.exists():
            state = json.loads(active_path.read_text())
            if (not resume_same_request or not isinstance(state, dict)
                    or state.get('resume_same_request') is not True
                    or state.get('request_sha256') != key
                    or state.get('service') != PUBLIC_HOST
                    or state.get('egress_identity') != config['egress_identity']):
                raise ReconciliationRequired('MSA controller requires reconciliation of interrupted/failed preparation')
        receipt_path = root / f'{key}.json'
        if state is None and receipt_path.exists() and not resume_same_request:
            return json.loads(receipt_path.read_text())['result']
        if state is None:
            state = {'request_sha256': key, 'started_at': time.time(),
                     'service': PUBLIC_HOST, 'egress_identity': config['egress_identity'],
                     'provider_database_version': None,
                     'resume_same_request': resume_same_request}
        state.update(status='preparing')
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
            status = 'pending' if resume_same_request and isinstance(exc, PendingMSA) else 'blocked_reconciliation'
            state.update(status=status, error_type=type(exc).__name__)
            _write(active_path, state)
            raise
        finally:
            _active.reset(token)
            _state_path.reset(state_token)
