"""Private stdlib SSH/rsync provisioning envelope (not an attempt controller).

Sent verbatim by transport in python -c; stdin/stdout remain payload channels.
Durable cancellation and launch share one flock. A gated session leader remains
alive until group cleanup, retaining the process identity needed for signals.
Ambiguous starts/identity loss never yield a quiescence receipt. Only reviewed
provisioning helpers, which do not daemonize/escape their session, belong here.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import sys
import time
import uuid

SCHEMA = 'bms.provision-transport.v1'


def boot_id():
    return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))


def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return None
    return dict(pid=pid, start=fields[19], group=int(fields[2]), session=int(fields[3]), state=fields[0])


def same_process(actual, expected):
    return actual is not None and all(actual[k] == expected[k] for k in ('pid', 'start', 'group', 'session'))


def operation_directory(root, operation):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', operation):
        raise ValueError('invalid provisioning operation')
    path = Path(root)
    if not path.is_absolute() or '..' in path.parts or path == Path('/'):
        raise ValueError('invalid provisioning root')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (*path.parts[1:], 'managed-assets', 'provision-operations', operation):
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.fsync(fd)
            os.close(fd)
            fd = child
        os.fchdir(fd)
    finally:
        os.close(fd)


def publish(name, value):
    temporary = '.receipt-' + uuid.uuid4().hex
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, name)
    fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read(name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('invalid provisioning record')
        value = json.load(stream)
    if value.get('schema') != SCHEMA:
        raise ValueError('unknown provisioning record')
    return value


@contextmanager
def locked():
    fd = os.open('lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('invalid provisioning lock')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def live_group(group):
    for item in Path('/proc').iterdir():
        if item.name.isdigit():
            process = identity(int(item.name))
            if process and process['group'] == group and process['state'] not in ('Z', 'X'):
                return True
    return False


def stop(row):
    if (row.get('phase') not in {'starting', 'running', 'stopped'}
            or not isinstance(row.get('boot_id'), str)):
        return False
    try:
        uuid.UUID(row['boot_id'])
    except ValueError:
        return False
    if row.get('phase') == 'stopped':
        return True
    if row.get('boot_id') != boot_id():
        # A real worker reboot ends the old process epoch without signalling.
        row['phase'] = 'stopped'
        return True
    leader = row.get('leader')
    if not leader or leader['pid'] != leader['group'] or leader['pid'] != leader['session']:
        return False
    # Missing/reused leader without a receipt is UNKNOWN, even if no matching
    # group is visible. Never signal an unrelated or unproven process group.
    if not same_process(identity(leader['pid']), leader):
        return False
    os.killpg(leader['pid'], signal.SIGTERM)
    time.sleep(0.15)
    if not same_process(identity(leader['pid']), leader):
        return False
    os.killpg(leader['pid'], signal.SIGKILL)
    deadline = time.monotonic() + 3
    while live_group(leader['pid']):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.03)
    row['phase'] = 'stopped'
    return True


def group_child(ready, gate, result, argv, cwd):
    os.setsid()
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(ready, b'1')
    os.close(ready)
    if os.read(gate, 1) != b'1':
        os._exit(125)
    os.close(gate)
    pid = os.fork()
    if pid == 0:
        os.close(result)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.chdir(cwd)
        try:
            os.execvp(argv[0], argv)
        except BaseException:
            os._exit(127)
    _, status = os.waitpid(pid, 0)
    try:
        os.write(result, str(os.waitstatus_to_exitcode(status)).encode())
    except BrokenPipeError:
        # Controller/SSH loss closes the status reader. It must not destroy
        # the anchor needed by durable cancellation to prove group ownership.
        pass
    finally:
        os.close(result)
    # Retain this session identity until controller cleanup; never exit early
    # when the payload leader exits but descendants may still mutate files.
    while True:
        signal.pause()


def run(argv, cwd):
    if not argv:
        raise ValueError('empty provisioning command')
    command = uuid.uuid4().hex + '.json'
    row: dict = dict(schema=SCHEMA, boot_id=boot_id(), phase='starting')
    ready_r, ready_w = os.pipe()
    gate_r, gate_w = os.pipe()
    result_r, result_w = os.pipe()
    with locked():
        if Path('cancelled.json').exists():
            raise ValueError('provisioning operation cancelled')
        publish(command, row)
        pid = os.fork()
        if pid == 0:
            # flock must not be inherited into a long-lived child.
            for entry in list(Path('/proc/self/fd').iterdir()):
                fd = int(entry.name)
                if fd > 2 and fd not in (ready_w, gate_r, result_w):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            try:
                group_child(ready_w, gate_r, result_w, argv, cwd)
            finally:
                os._exit(125)
        os.close(ready_w)
        os.close(gate_r)
        os.close(result_w)
        if not select.select([ready_r], [], [], 5)[0] or os.read(ready_r, 1) != b'1':
            os.close(gate_w)
            raise ValueError('unknown provisioning start')
        os.close(ready_r)
        row['leader'] = identity(pid)
        if not row['leader'] or row['leader']['group'] != pid:
            os.close(gate_w)
            raise ValueError('unknown provisioning identity')
        row['phase'] = 'running'
        publish(command, row)
        os.write(gate_w, b'1')
        os.close(gate_w)
    code = 125
    while True:
        if select.select([result_r], [], [], 0.1)[0]:
            data = os.read(result_r, 64)
            if data:
                code = int(data)
            break
        with locked():
            if Path('cancelled.json').exists():
                break
    os.close(result_r)
    with locked():
        row = read(command)
        if not stop(row):
            raise ValueError('provisioning quiescence unknown')
        publish(command, row)
        if Path('cancelled.json').exists():
            code = 125
    os.waitpid(pid, 0)
    return code if code >= 0 else 128 - code


def quiesce(operation):
    with locked():
        publish('cancelled.json', dict(schema=SCHEMA, operation_id=operation))
        quiet = True
        for path in Path('.').glob('*.json'):
            if path.name == 'cancelled.json':
                continue
            row = read(path.name)
            stopped = stop(row)
            if stopped:
                publish(path.name, row)
            quiet = stopped and quiet
    print(json.dumps(dict(schema=SCHEMA, operation_id=operation, quiescent=quiet)))
    return 0


def main():
    mode, root, operation, *argv = sys.argv[1:]
    cwd = os.getcwd()
    # SSH disconnect must not kill the witness authority before group cleanup.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    operation_directory(root, operation)
    if mode == 'quiesce':
        return quiesce(operation)
    if mode != 'run':
        raise ValueError('invalid provisioning action')
    return run(argv, cwd)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('provisioning transport ownership unavailable', file=sys.stderr)
        sys.exit(125)
