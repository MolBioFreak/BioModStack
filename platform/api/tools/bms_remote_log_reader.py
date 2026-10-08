"""Bounded, generation-fenced native diagnostics over governed SSH transport."""
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any
import stat
import sys

MAX_LOG_BYTES = 1_048_576
IDENTITY_FIELDS = ('job_id', 'attempt_id', 'generation', 'boot_id',
                   'continuation_lease_id', 'plan_sha256', 'native_output_directory',
                   'workflow_pid', 'workflow_start_ticks')


def _open_directory(path):
    path = PurePosixPath(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Invalid attempt log directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(info):
    return info.st_dev, info.st_ino


def _owned_environment(current):
    if 'process_matches' not in globals():
        from tools.bms_remote_worker import process_matches, boot_id
    else:
        process_matches, boot_id = globals()['process_matches'], globals()['boot_id']
    pid, ticks = current.get('workflow_pid'), current.get('workflow_start_ticks')
    def check():
        if current.get('boot_id') != boot_id() or not process_matches(pid, ticks):
            raise ValueError('Execution environment owner unavailable')
    check()
    directory = _open_directory(f'/proc/{pid}')
    try:
        fd = os.open('environ', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, 'rb') as handle:
            raw = handle.read(MAX_LOG_BYTES + 1)
        if len(raw) > MAX_LOG_BYTES:
            raise ValueError('Execution environment exceeds bounded size')
        environment = {}
        for item in raw.split(b'\0'):
            if item:
                key, value = item.split(b'=', 1)
                environment[os.fsdecode(key)] = os.fsdecode(value)
        if not environment:
            raise ValueError('Execution environment owner unavailable')
        check()
        return environment, check
    finally:
        os.close(directory)


def read_logs(attempt_dir, expected, tail):
    # The transport embeds the canonical filter; direct callers import it.
    if 'redact_text' not in globals():
        from scripts.lib.native_diagnostics import redact_text
    else:
        redact_text = globals()['redact_text']
    directories = []
    members = []
    def pin(path):
        fd = _open_directory(path)
        directories.append((path, fd, _identity(os.fstat(fd))))
        return fd
    directory = pin(attempt_dir)
    try:
        def read(name, limit, *, parent=directory, offset=None, unchanged_mtime=None, optional=False):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                if optional:
                    return None
                raise
            with os.fdopen(fd, 'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError('Attempt log reader requires regular files')
                # Status is atomically published by its existing owner; its logical
                # identity is checked separately. All other members must survive.
                if name != 'status.json':
                    members.append((parent, name, _identity(info), info.st_size,
                                    (info.st_ctime_ns, info.st_size, info.st_mode) if name == 'execution-envelope.json' else None))
                if offset is not None:
                    if type(offset) is not int or offset < 0 or info.st_size < offset:
                        raise ValueError('Diagnostic generation offset conflicts')
                    if unchanged_mtime == info.st_mtime_ns:
                        return None
                    handle.seek(max(offset, info.st_size - limit))
                elif info.st_size > limit:
                    raise ValueError('Attempt metadata exceeds bounded size')
                return handle.read(limit)

        def check():
            current = json.loads(read('status.json', 4 * MAX_LOG_BYTES))
            identity = {key: current.get(key, 0 if key == 'generation' else None)
                        for key in IDENTITY_FIELDS}
            if identity != expected or current.get('state') not in {'prepared', 'running', 'cancelling', 'awaiting_input'}:
                raise ValueError('Active attempt log identity changed')
            return current

        current = check()
        envelope = json.loads(read('execution-envelope.json', 4 * MAX_LOG_BYTES))
        if any(envelope.get(key) != expected[key] for key in ('job_id', 'attempt_id')):
            raise ValueError('Attempt envelope identity changed')
        native_path = PurePosixPath(envelope['working_directory'])
        if not native_path.is_relative_to(PurePosixPath(attempt_dir)) or '..' in native_path.parts:
            raise ValueError('Native diagnostic directory escapes attempt custody')
        envelope_plan = envelope.get('plan_sha256')
        if envelope_plan is not None and envelope_plan != expected.get('plan_sha256'):
            raise ValueError('Envelope plan identity changed')
        environment, check_process = _owned_environment(current)
        offsets = current.get('diagnostic_offsets')
        # Before supervisor observation, never expose a predecessor generation.
        if offsets is None and expected['generation']:
            return {'nextflow_log': None, 'command_log': None}
        offsets = offsets or {}
        sources = [('nextflow_log', directory, 'nextflow.log', 'nextflow.log'),
                   ('command_log', directory, 'supervisor.log', 'supervisor.log')]
        if current.get('diagnostic_offsets') is not None:
            native = pin(envelope['working_directory'])
            sources += [('nextflow_log', native, '.nextflow.log', 'nextflow-internal.log'),
                        ('command_log', native, 'component-root.log', 'component-root.log')]
        result = {'nextflow_log': None, 'command_log': None}
        for field, parent, name, key in sources:
            raw = read(name, MAX_LOG_BYTES, parent=parent, offset=offsets.get(key, 0),
                       unchanged_mtime=offsets.get('internal_mtime_ns') if key == 'nextflow-internal.log' else None,
                       optional=True)
            if raw:
                text = redact_text(raw.decode('utf-8', errors='replace'), environment=environment)
                result[field] = '\n'.join(filter(None, [result[field], text]))
        for field, text in result.items():
            if text is not None:
                result[field] = '\n'.join(text.splitlines()[-max(1, min(int(tail), 5000)):])[-MAX_LOG_BYTES:]
        if check().get('diagnostic_offsets') != current.get('diagnostic_offsets'):
            raise ValueError('Diagnostic generation observation changed')
        for parent, name, identity, size, immutable in members:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or _identity(info) != identity or info.st_size < size
                    or immutable is not None and immutable != (info.st_ctime_ns, info.st_size, info.st_mode)):
                raise ValueError('Diagnostic member replaced during read')
        for path, fd, identity in directories:
            reachable = _open_directory(path)
            try:
                if _identity(os.fstat(reachable)) != identity:
                    raise ValueError('Diagnostic directory replaced during read')
            finally:
                os.close(reachable)
        check_process()
        return result
    finally:
        for _, fd, _ in directories:
            os.close(fd)


if __name__ == '__main__':
    print(json.dumps(read_logs(sys.argv[1], json.loads(sys.argv[2]), int(sys.argv[3]))))
