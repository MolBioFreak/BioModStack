#!/usr/bin/env python3
"""Explicit userspace execution of trusted, pinned BMS scientific images.

Upstream udocker P1 supplies path translation, not a namespace/security sandbox.
Canonical image bytes stay in the shared image authority. Private reflink rootfs
and input views preserve sources; a modified declared input invalidates the run.
The existing worker owns process quiescence, resources and result publication.
"""
from __future__ import annotations

import ctypes
import fcntl
import hashlib
import selectors
import uuid
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

_HERE = Path(__file__).resolve()
for _path in (_HERE.parent, _HERE.parent.parent / 'runner', _HERE.parent.parent / 'lib', _HERE.parents[3]):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from scripts.lib import runtime_image_views as views
from scripts.lib import runtime_image_lifecycle as lifecycle
from scripts.lib.shared_runtime_images import _directory, _file, _check_file
import bms_remote_worker as owner


@dataclass
class Invocation:
    image: str
    command: list[str]
    binds: list[tuple[str, str, str]] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    gpu: bool = False
    cleanenv: bool = False
    home: str | None = None
    no_home: bool = False
    cwd: str | None = None
    writable: bool = False


def parse_exec(args):
    binds, environment, flags = [], {}, {}
    i = 0
    valued = {'--bind', '-B', '--env', '--pwd', '--home'}
    boolean = {'--nv': 'gpu', '--cleanenv': 'cleanenv', '--no-home': 'no_home', '--writable-tmpfs': 'writable'}
    while i < len(args) and args[i].startswith('-'):
        word = args[i]
        if word == '--':
            i += 1
            break
        key, sep, value = word.partition('=')
        if key in boolean and not sep:
            flags[boolean[key]] = True
        elif key in valued:
            if not sep:
                i += 1
                if i >= len(args):
                    raise ValueError('missing container option value: ' + key)
                value = args[i]
            if key in {'--bind', '-B'}:
                for bind in value.split(','):
                    parts = bind.split(':')
                    if len(parts) > 3 or not parts[0]:
                        raise ValueError('invalid container bind')
                    source = os.path.abspath(os.path.expandvars(parts[0]))
                    target = os.path.expandvars(parts[1]) if len(parts) > 1 else source
                    mode = parts[2] if len(parts) > 2 else 'rw'
                    if not PurePosixPath(target).is_absolute() or '..' in PurePosixPath(target).parts or mode not in {'ro', 'rw'}:
                        raise ValueError('invalid container bind target or mode')
                    binds.append((source, target, mode))
            elif key == '--env':
                for assignment in re.split(r',(?=[A-Za-z_][A-Za-z_0-9]*=)', value):
                    name, equal, text = assignment.partition('=')
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name):
                        raise ValueError('invalid container environment key')
                    environment[name] = text if equal else os.environ.get(name, '')
            else:
                flags['cwd' if key == '--pwd' else 'home'] = value
        else:
            raise ValueError('unsupported userspace container option: ' + key)
        i += 1
    if len(args) - i < 2:
        raise ValueError('container exec requires an image and command')
    return Invocation(args[i], args[i + 1:], binds, environment, **flags)


def canonical_image(value, store):
    inherited = None
    if re.fullmatch(r'/proc/self/fd/[0-9]+', value):
        inherited = int(value.rsplit('/', 1)[1])
        path = Path(os.readlink(value))
    else:
        if '://' in value:
            raise ValueError('scientific exec requires a published local image, not an image download')
        path = Path(value).resolve(strict=True)
    relative = path.relative_to(store)
    if (len(relative.parts) != 4 or relative.parts[:2] != ('objects', 'sha256')
            or relative.name != 'runtime.sif' or not re.fullmatch('[0-9a-f]{64}', relative.parts[2])):
        raise ValueError('container image is not a canonical published SIF')
    with _file(path) as (fd, _, info):
        if inherited is not None:
            previous = os.fstat(inherited)
            if (previous.st_dev, previous.st_ino, previous.st_size, previous.st_ctime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_ctime_ns):
                raise ValueError('inherited image descriptor identity changed')
    return path, relative.parts[2]


def sif_partition_offset(fd, read=None):
    command = ['apptainer', 'sif', 'list', f'/proc/self/fd/{fd}']
    listing = (read(command) if read is not None else
               subprocess.check_output(command, text=True, pass_fds=(fd,)))
    rows = [line.split('|') for line in listing.splitlines() if 'FS (Squashfs/*System/amd64)' in line]
    if len(rows) != 1 or len(rows[0]) < 4:
        raise ValueError('image has no unique x86_64 Squashfs system partition')
    offset = rows[0][3].strip().split('-')[0].strip()
    if not offset.isdigit():
        raise ValueError('invalid SIF system partition offset')
    return offset


def extract_sif(fd, destination):
    source = f'/proc/self/fd/{fd}'
    offset = sif_partition_offset(fd)
    subprocess.run(['unsquashfs', '-no-progress', '-processors', '2', '-d', str(destination), '-o', offset, source],
                   check=True, pass_fds=(fd,), stdout=sys.stderr)


# Finite inspection policy, independent of image size and scientific settings.
INSPECT_MAX_FILES = 32
INSPECT_MAX_BYTES = 1024 * 1024 * 1024
INSPECT_TIMEOUT = 120
INSPECT_METADATA_BYTES = 1024 * 1024


def inspect_paths(paths):
    if not paths or len(paths) > INSPECT_MAX_FILES or len(set(paths)) != len(paths):
        raise ValueError('inspection requires a bounded unique file roster')
    for path in paths:
        if (not path.startswith('/') or len(path.encode()) > 4096
                or any(c in path for c in '*?[]\\')
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or any(p in {'', '.', '..'} or p.startswith('-') for p in path[1:].split('/'))):
            raise ValueError('inspection requires absolute normalized literal file paths')
    return tuple(paths)


def inspect_stream(command, fd, identity, deadline, consume, limit):
    """Bound both pipes; reap every descendant before returning to the lease owner."""
    process = subprocess.Popen(command, pass_fds=tuple(sorted(set(inherited_fds() + [fd]))),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            sizes = {'stdout': 0, 'stderr': 0}
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('container file inspection timed out')
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    sizes[key.data] += len(chunk)
                    bound = limit if key.data == 'stdout' else INSPECT_METADATA_BYTES
                    if sizes[key.data] > bound:
                        raise ValueError('container file inspection exceeded byte limit')
                    if key.data == 'stdout':
                        consume(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('container file inspection timed out')
            if process.wait(timeout=remaining):
                raise ValueError('container file inspection command failed')
    finally:
        # main establishes subreaper ownership and signal cancellation. Never
        # unpin an image while even a reparented inspection child is alive.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM, signal.SIGHUP})
        try:
            try:
                if not owner.quiesce_writers(identity, timeout_seconds=5):
                    os._exit(125)
            except BaseException:
                os._exit(125)  # Unknown quiescence must retain the durable lease.
            process.wait()
            # Reap adopted descendants too (main is the dedicated subreaper).
            while True:
                try:
                    if os.waitpid(-1, os.WNOHANG) == (0, 0):
                        break
                except ChildProcessError:
                    break
            process.stdout.close()
            process.stderr.close()
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def inspect_files(image_name, paths, identity):
    paths = inspect_paths(paths)
    store = Path(os.environ['BMS_RUNTIME_IMAGE_STORE'])
    if not store.is_absolute():
        raise ValueError('container store must be absolute')
    image, digest = canonical_image(image_name, store)
    lease_owner = 'inspect-files:' + uuid.uuid4().hex
    token, receipts = lifecycle.acquire_lease(store, [digest], owner=lease_owner)
    records = []
    try:
        with _file(image) as (fd, parent, before):
            def generation(info):
                return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            def check_source():
                _check_file(image, fd, parent, before)
                receipt = receipts[digest]
                if (stat.S_IMODE(before.st_mode) != 0o400 or before.st_nlink != 1
                        or stat.S_IMODE(os.fstat(parent).st_mode) != 0o500
                        or generation(before) != tuple(receipt[k] for k in
                            ('device', 'inode', 'size', 'mtime_ns', 'ctime_ns'))):
                    raise ValueError('inspected image generation changed')
                if re.fullmatch(r'/proc/self/fd/[0-9]+', image_name):
                    if generation(os.fstat(int(image_name.rsplit('/', 1)[1]))) != generation(before):
                        raise ValueError('inherited image descriptor identity changed')
            check_source()
            deadline = time.monotonic() + INSPECT_TIMEOUT
            def read_metadata(command):
                data = bytearray()
                inspect_stream(command, fd, identity, deadline, data.extend, INSPECT_METADATA_BYTES)
                return data.decode('utf-8')
            try:
                offset = sif_partition_offset(fd, read_metadata)
                for path in paths:
                    hashed = hashlib.sha256()
                    inspect_stream(['unsquashfs', '-no-progress', '-no-wildcards', '-o', offset,
                                    '-cat', f'/proc/self/fd/{fd}', path[1:]],
                                   fd, identity, deadline, hashed.update, INSPECT_MAX_BYTES)
                    records.append(f'{hashed.hexdigest()}  {path}\n')
            finally:
                check_source()
    finally:
        lifecycle.release_lease(store, token, owner=lease_owner)
    # No partial roster escapes on errors, replacement, cancellation or cleanup.
    sys.stdout.write(''.join(records))
    return 0


def snapshot(path):
    """Stable no-follow identities, without reopening ancestors per member."""
    return views.snapshot_tree(path)


def copy_input(source, target, guest, direct_images, *, writable=False):
    """Private CoW projection over pinned ancestry; no shared writable binds."""
    directories = []
    for parent, name, relative, info in views._walk_tree(source):
        destination = target if relative == '.' else target / relative
        if stat.S_ISLNK(info.st_mode):
            os.symlink(os.readlink(name, dir_fd=parent), destination)
        elif stat.S_ISDIR(info.st_mode):
            destination.mkdir(mode=0o700)
            directories.append((destination, stat.S_IMODE(info.st_mode) | (0o200 if writable else 0)))
        elif Path(name).suffix == '.sif':
            # SIFs remain original provenance capabilities, not copied storage.
            original = source if relative == '.' else source / relative
            canonical_image(str(original), Path(os.environ['BMS_RUNTIME_IMAGE_STORE']))
            destination.touch(mode=0o400)
            direct_images.append((str(original), str(PurePosixPath(guest) / relative)))
        else:
            with views._member(parent, name) as (fd, before):
                if not views._same(info, before):
                    raise ValueError('declared input changed during projection')
                out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    fcntl.ioctl(out, views.FICLONE, fd)
                    os.fchmod(out, stat.S_IMODE(before.st_mode) | (0o200 if writable else 0))
                finally:
                    os.close(out)
    for directory, mode in reversed(directories):
        directory.chmod(mode)


def inherited_fds():
    result = []
    for name in os.listdir('/proc/self/fd'):
        fd = int(name)
        try:
            if fd > 2 and not fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
                result.append(fd)
        except OSError:
            pass
    return result


def nvidia_libraries():
    result = {}
    for line in subprocess.check_output(['ldconfig', '-p'], text=True).splitlines():
        if '=>' not in line or 'x86-64' not in line:
            continue
        name = line.split()[0]
        if name.startswith(('libcuda.', 'libnvidia-')):
            result[name] = str(Path(line.split('=>', 1)[1].strip()).resolve(strict=True))
    if 'libcuda.so.1' not in result:
        raise ValueError('NVIDIA driver library unavailable')
    return result


def run_owned(command, environment, pass_fds, identity):
    process = subprocess.Popen(command, env=environment, pass_fds=tuple(sorted(set(pass_fds))))
    try:
        result = process.wait()
        if not owner.quiesce_writers(identity, timeout_seconds=5):
            os.write(2, b'Container writers remain active; retaining execution storage\n')
            os._exit(125)  # Do not release image lease/storage under a live writer.
        return result
    finally:
        if process.poll() is None:
            if not owner.quiesce_writers(identity, timeout_seconds=5):
                os._exit(125)
            process.wait()


def execute(invocation, identity):
    store = Path(os.environ['BMS_RUNTIME_IMAGE_STORE'])
    workspace = Path(os.environ['BMS_CONTAINER_WORK_ROOT'])
    if not store.is_absolute() or not workspace.is_absolute():
        raise ValueError('container stores must be absolute')
    image, digest = canonical_image(invocation.image, store)
    original_fds = inherited_fds()
    with views.private_image_view(store, digest, workspace, extract_sif) as view:
        rootfs = view['rootfs']
        private = rootfs.parent
        inputs = private / 'inputs'
        inputs.mkdir(mode=0o700)
        guarded, volumes, direct_images = [], [], []
        cwd = Path.cwd()
        volumes.append((str(cwd), str(cwd)))
        # Nextflow normally auto-mounts absolute targets of staged symlinks.
        for node in cwd.iterdir():
            if node.is_symlink():
                resolved = node.resolve(strict=True)
                mount = resolved if resolved.is_dir() else resolved.parent
                volumes.append((str(mount), str(mount)))
        volumes.extend((p, p) for p in ('/proc', '/sys', '/dev') if Path(p).exists())
        shared = os.environ.get('BMS_SHARED_WEIGHTS_ROOT')
        shared = Path(shared).absolute() if shared else None
        requested = [(source, target, 'rw') for source, target in volumes] + invocation.binds
        volumes = []
        for index, (source_name, target, mode) in enumerate(requested):
            source = Path(source_name).resolve(strict=True)
            protected = shared is not None and source.is_relative_to(shared)
            if shared is not None and shared != source and shared.is_relative_to(source):
                raise ValueError('broad bind exposes the shared weight authority')
            if mode == 'rw' and not protected:
                volumes.append((str(source), target))
                continue
            before = snapshot(source)
            projected = inputs / str(index)
            # PRoot can temporarily chmod frozen 0444/0555 inputs even for a
            # read. Normalize only the PRIVATE CoW view before its strict ro
            # baseline; shared originals and write-and-restore detection stay.
            if protected:
                copy_input(source, projected, target, direct_images, writable=True)
            else:
                copy_input(source, projected, target, direct_images)
            if snapshot(source) != before:
                raise ValueError('declared input changed during projection')
            guarded.append((source, before, projected, snapshot(projected) if mode == "ro" else None))
            volumes.append((str(projected), target))
        volumes.extend(direct_images)
        # Executing SIF identity stays truthful for native provenance consumers.
        volumes.append((str(image), str(image)))
        if invocation.home is not None:
            home_parts = invocation.home.split(':')
            if len(home_parts) > 2:
                raise ValueError('invalid home mapping')
            home_source = str(Path(home_parts[0]).resolve(strict=True))
            home_guest = home_parts[1] if len(home_parts) == 2 else '/root'
            volumes.append((home_source, home_guest))
        else:
            home_guest = '/root'
        extra = dict(invocation.environment)
        for prefix in ('SINGULARITYENV_', 'APPTAINERENV_'):
            for name, value in os.environ.items():
                if name.startswith(prefix):
                    extra.setdefault(name[len(prefix):], value)
        extra.update(BMS_EXECUTING_IMAGE=str(image), BMS_EXECUTING_IMAGE_SHA256=digest,
                     BMS_EXECUTING_IMAGE_FD=str(view['image_fd']), PYTHONDONTWRITEBYTECODE='1')
        if invocation.home is not None or invocation.no_home:
            extra['HOME'] = home_guest
        # SIF environment first, caller's explicit environment second, as in
        # Apptainer. External-rootfs udocker does not load SIF metadata itself.
        script = ['#!/bin/bash', 'set -e',
                  'for f in /.singularity.d/env/*.sh; do [ ! -f "$f" ] || source "$f"; done']
        script += ['export ' + key + '=' + shlex.quote(value) for key, value in extra.items()]
        if invocation.gpu:
            directory = rootfs / '.bms-nvidia'
            directory.mkdir(exist_ok=True)
            for name, source in nvidia_libraries().items():
                (directory / name).touch(exist_ok=True)
                volumes.append((source, '/.bms-nvidia/' + name))
            script.append('export LD_LIBRARY_PATH="/.bms-nvidia${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"')
        script.append('exec "$@"')
        entry = rootfs / '.bms-exec.sh'
        entry.write_text('\n'.join(script) + '\n')
        entry.chmod(0o700)
        environment = dict(os.environ)
        environment.update(UDOCKER_DIR=str(private / 'udocker'), UDOCKER_TMP=str(private / 'tmp'),
                           UDOCKER_DEFAULT_EXECUTION_MODE='P1', UDOCKER_LOGLEVEL='0',
                           UDOCKER_TARBALL='file:///nonexistent/no-runtime-downloads')
        Path(environment['UDOCKER_TMP']).mkdir(mode=0o700)
        launcher = Path(environment['BMS_UDOCKER'])
        if not launcher.is_absolute() or not launcher.is_file():
            raise ValueError('pinned udocker launcher unavailable')
        command = [str(launcher), '--allow-root', 'run', '--nobanner', '--nosysdirs', '--containerauth',
                   '--location=' + str(rootfs), '--workdir=' + (invocation.cwd or str(cwd))]
        if not invocation.cleanenv:
            command.append('--hostenv')
        else:
            command.extend('--env=' + k + '=' + v for k, v in environment.items() if k.startswith('BMS_'))
        # Preserve order: explicit nested mappings override their wider parents.
        command.extend('--volume=' + source + ':' + target for source, target in volumes)
        command += ['bms-pinned-image', '/bin/bash', '/.bms-exec.sh', *invocation.command]
        result = run_owned(command, environment, original_fds + [view['image_fd']], identity)
        for source, before, projected, original_view in guarded:
            if snapshot(source) != before or (original_view is not None and snapshot(projected) != original_view):
                raise ValueError('declared scientific input changed during execution')
        return result


class Cancelled(BaseException):
    def __init__(self, signum):
        self.signum = signum


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ['--version']:
        print('bms-container 1 (udocker P1; private-copy-on-write inputs)')
        return 0
    if args and args[0] == 'exec':
        invocation = parse_exec(args[1:])
    elif args and args[0] == 'inspect-files' and len(args) >= 3:
        inspect_paths(args[2:])
        invocation = None
    else:
        raise ValueError('expected exec or inspect-files IMAGE ABSOLUTE_PATH...')
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise RuntimeError('cannot establish container child ownership')
    identity = dict(boot_id=owner.boot_id(), supervisor_pid=os.getpid(),
                    supervisor_start_ticks=owner.process_start_ticks(os.getpid()))
    stopping = []
    def stop(signum, frame):
        if stopping:
            return
        stopping.append(signum)
        if not owner.quiesce_writers(identity, timeout_seconds=5):
            os._exit(125)
        raise Cancelled(signum)
    previous = {number: signal.signal(number, stop) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        if invocation is None:
            return inspect_files(args[1], args[2:], identity)
        return execute(invocation, identity)
    except Cancelled as exc:
        return 128 + exc.signum
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print('BMS container execution failed: ' + str(exc), file=sys.stderr)
        sys.exit(125)
