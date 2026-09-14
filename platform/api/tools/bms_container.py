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


def extract_sif(fd, destination):
    source = f'/proc/self/fd/{fd}'
    listing = subprocess.check_output(['apptainer', 'sif', 'list', source], text=True, pass_fds=(fd,))
    rows = [line.split('|') for line in listing.splitlines() if 'FS (Squashfs/*System/amd64)' in line]
    if len(rows) != 1:
        raise ValueError('image has no unique x86_64 Squashfs system partition')
    offset = rows[0][3].strip().split('-')[0].strip()
    if not offset.isdigit():
        raise ValueError('invalid SIF system partition offset')
    subprocess.run(['unsquashfs', '-no-progress', '-processors', '2', '-d', str(destination), '-o', offset, source],
                   check=True, pass_fds=(fd,), stdout=sys.stderr)


def snapshot(path):
    """Stable no-follow identities: byte writes necessarily change inode ctime."""
    result = {}
    def walk(node, relative):
        with _directory(node.parent) as parent:
            info = os.stat(node.name, dir_fd=parent, follow_symlinks=False)
            kind = stat.S_IFMT(info.st_mode)
            if kind not in {stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK}:
                raise ValueError('unsupported input file type')
            result[relative] = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                                info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                                os.readlink(node.name, dir_fd=parent) if kind == stat.S_IFLNK else None)
        if kind == stat.S_IFDIR:
            with _directory(node) as directory:
                names = sorted(os.listdir(directory))
                for name in names:
                    walk(node / name, relative + '/' + name)
                if names != sorted(os.listdir(directory)):
                    raise ValueError('input membership changed')
    walk(path, '.')
    return result


def copy_input(source, target, guest, direct_images):
    """Private CoW input projection, never a copied SIF or shared hardlink."""
    with _directory(source.parent) as parent:
        info = os.stat(source.name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            os.symlink(os.readlink(source.name, dir_fd=parent), target)
        elif stat.S_ISDIR(info.st_mode):
            target.mkdir(mode=0o700)
            with _directory(source) as directory:
                for name in sorted(os.listdir(directory)):
                    copy_input(source / name, target / name, str(PurePosixPath(guest) / name), direct_images)
            target.chmod(stat.S_IMODE(info.st_mode))
        elif stat.S_ISREG(info.st_mode):
            if source.suffix == '.sif':
                # The archive is a pinned provenance capability, not writable
                # execution storage. Retain original identity; never copy it.
                canonical_image(str(source), Path(os.environ['BMS_RUNTIME_IMAGE_STORE']))
                target.touch(mode=0o400)
                direct_images.append((str(source), guest))
            else:
                with _file(source) as (fd, source_parent, before):
                    out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    try:
                        fcntl.ioctl(out, views.FICLONE, fd)
                        os.fchmod(out, stat.S_IMODE(before.st_mode))
                    finally:
                        os.close(out)
                    _check_file(source, fd, source_parent, before)
        else:
            raise ValueError('unsupported input file type')


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
        for index, (source_name, target, mode) in enumerate(invocation.binds):
            source = Path(source_name).resolve(strict=True)
            if mode == 'rw':
                volumes.append((str(source), target))
                continue
            before = snapshot(source)
            projected = inputs / str(index)
            copy_input(source, projected, target, direct_images)
            if snapshot(source) != before:
                raise ValueError('declared input changed during projection')
            guarded.append((source, before, projected, snapshot(projected)))
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
            if snapshot(source) != before or snapshot(projected) != original_view:
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
    else:
        raise ValueError('expected exec')
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
