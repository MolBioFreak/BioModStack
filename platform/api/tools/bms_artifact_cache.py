#!/usr/bin/env python3
"""Verified instance-local artifact storage. JSON stdin protocol; no credentials/URLs.

Roots and destinations are trusted controller configuration, never operator input.
Every directory is pinned with O_NOFOLLOW; published bytes are never written in
place. Copies, rather than hardlinks, isolate writable execution materializations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time
import uuid

CHUNK = 1024 * 1024


@contextmanager
def directory(path, *, create=False):
    path = PurePosixPath(str(path))
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe_directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def artifact(value):
    digest, size = value.get('sha256'), value.get('size_bytes')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('invalid_digest')
    if type(size) is not int or size < 0:
        raise ValueError('invalid_size')
    kind = value.get('kind')
    if kind not in (None, 'runtime_image'):
        raise ValueError('invalid_artifact_kind')
    return {'sha256': digest, 'size_bytes': size, **({'kind': kind} if kind else {})}


def regular(fd):
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise ValueError('not_regular_file')


def verified(fd, item, progress=lambda **kw: None):
    regular(fd)
    if os.fstat(fd).st_size != item['size_bytes']:
        return False
    os.lseek(fd, 0, os.SEEK_SET)
    digest, done = hashlib.sha256(), 0
    while data := os.read(fd, CHUNK):
        digest.update(data)
        done += len(data)
        progress(state='verifying', verified_bytes=done)
    return done == item['size_bytes'] and digest.hexdigest() == item['sha256']


class Cache:
    def __init__(self, root, events=None):
        self.root = PurePosixPath(str(root))
        self.events = events or (lambda value: None)
        for name in ('objects/sha256', 'locks', 'incoming'):
            with directory(self.root / name, create=True):
                pass

    @property
    def image_store(self):
        # Same worker-local authority used by scientific runtime consumers.
        if self.root.parts[-3:] != ('cache', 'artifacts', 'v1'):
            raise ValueError('invalid_worker_cache_root')
        return Path(self.root.parent.parent / 'runtime-images')

    def image_path(self, item):
        return self.image_store / 'objects/sha256' / item['sha256'] / 'runtime.sif'

    def verify_runtime(self, value):
        item = artifact(value)
        info = runtime_images().verify_image(self.image_path(item), item['sha256'])
        if info['size'] != item['size_bytes']:
            raise ValueError('runtime_image_size_mismatch')
        return self.image_path(item)

    def probe_runtime(self, item):
        # Only absence of the digest DIRECTORY means missing. Incomplete/corrupt
        # published objects must fail, never trigger replacement of runnable bytes.
        parent = self.image_path(item).parent
        with directory(parent.parent, create=True) as fd:
            try:
                os.stat(parent.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return {**item, 'state': 'missing'}
        self.verify_runtime(item)
        return {**item, 'state': 'cache_hit'}

    def ingest_runtime(self, item, source):
        if self.probe_runtime(item)['state'] == 'cache_hit':
            return {**item, 'state': 'ready', 'cache_hit': True}
        # One private upload -> one independently copied immutable object. Never
        # retain another artifact-CAS SIF or adopt/hardlink a mutable incoming file.
        with directory(source.parent) as parent:
            fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                if not verified(fd, item):
                    raise ValueError('runtime_image_identity_mismatch')
            finally:
                os.close(fd)
        runtime_images().publish_image(Path(source), self.image_store, item['sha256'])
        self.verify_runtime(item)
        return {**item, 'state': 'ready', 'cache_hit': False}

    def runtime_alias(self, value, destination, runtime_root, *, check=False):
        item = artifact(value)
        target = self.verify_runtime(item)
        root, destination = PurePosixPath(str(runtime_root)), PurePosixPath(str(destination))
        attempts = self.root.parent.parent.parent / 'attempts'
        relative = root.relative_to(attempts)
        if (not root.is_absolute() or '..' in root.parts or len(relative.parts) != 3
                or relative.parts[1:] != ('materialized', 'runtime')
                or str(uuid.UUID(relative.parts[0])) != relative.parts[0]
                or '..' in destination.parts or destination == root
                or not destination.is_relative_to(root)):
            raise ValueError('unsafe_runtime_alias')
        with directory(destination.parent, create=not check) as parent:
            # Exact, controller-derived target only, not arbitrary external links.
            if check:
                if os.readlink(destination.name, dir_fd=parent) != str(target):
                    raise ValueError('runtime_alias_mismatch')
            else:
                os.symlink(str(target), destination.name, dir_fd=parent)
                os.fsync(parent)
        return {**item, 'state': 'ready'}

    def materialize_entry(self, row, destination_root):
        if row['artifact'].get('kind') == 'runtime_image':
            item = artifact(row['artifact'])
            if str(self.image_path(item)) != row['destination']:
                raise ValueError('runtime_image_destination_mismatch')
            self.verify_runtime(item)
            for alias in row['aliases']:
                self.runtime_alias(item, alias, row['runtime_root'])
            return {**item, 'state': 'ready'}
        return self.materialize(row['artifact'], row['destination'], destination_root, row.get('mode', 0o644))

    def execute_runtime(self, manifest, command, expected_sha256):
        path = Path(manifest)
        with directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as stream:
                regular(stream.fileno())
                payload = stream.read(8 * 1024 * 1024 + 1)
                if len(payload) > 8 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != expected_sha256:
                    raise ValueError('runtime_manifest_identity_mismatch')
                references = json.loads(payload)
        if references['schema'] != 'bms.runtime-image-references.v1':
            raise ValueError('invalid_runtime_manifest')
        if path != Path(references['runtime_root']) / '.bms-runtime-images.json':
            raise ValueError('invalid_runtime_manifest_path')
        for row in references['images']:
            self.verify_runtime(row)
            for alias in row['aliases']:
                self.runtime_alias(row, alias, references['runtime_root'], check=True)
        if not command:
            raise ValueError('missing_runtime_command')
        os.execvp(command[0], command)

    def emit(self, item, state, **kw):
        self.events({**item, 'state': state, 'timestamp': time.time(), **kw})

    @contextmanager
    def locked(self, item):
        with directory(self.root / 'locks') as parent:
            fd = os.open(item['sha256'], os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            regular(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.emit(item, 'waiting_for_lock')
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @contextmanager
    def objects(self, item):
        with directory(self.root / 'objects/sha256' / item['sha256'][:2], create=True) as fd:
            yield fd

    def state(self, parent, item):
        try:
            fd = os.open(item['sha256'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return 'missing'
        except OSError:
            return 'corrupt'
        try:
            return 'cache_hit' if verified(fd, item) else 'corrupt'
        except ValueError:
            return 'corrupt'
        finally:
            os.close(fd)

    def probe(self, value):
        item = artifact(value)
        if item.get('kind') == 'runtime_image':
            return self.probe_runtime(item)
        with self.locked(item), self.objects(item) as parent:
            state = self.state(parent, item)
        self.emit(item, state)
        return {**item, 'state': state}

    def _publish_copy(self, source_fd, parent, name, item, mode):
        temporary = '.partial-' + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            os.lseek(source_fd, 0, os.SEEK_SET)
            digest, done = hashlib.sha256(), 0
            while data := os.read(source_fd, CHUNK):
                done += len(data)
                if done > item['size_bytes']:
                    raise ValueError('size_mismatch')
                digest.update(data)
                view = memoryview(data)
                while view:
                    count = os.write(fd, view)
                    view = view[count:]
                self.emit(item, 'transferring', transferred_bytes=done)
            if done != item['size_bytes'] or digest.hexdigest() != item['sha256']:
                raise ValueError('hash_mismatch')
            os.fchmod(fd, mode)
            os.fsync(fd)
            self.emit(item, 'publishing')
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(fd)
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass

    def ingest(self, value, source):
        item = artifact(value)
        source = PurePosixPath(str(source))
        if '..' in source.parts or not source.is_relative_to(self.root / 'incoming'):
            raise ValueError('source_outside_incoming')
        if item.get('kind') == 'runtime_image':
            return self.ingest_runtime(item, source)
        with self.locked(item), self.objects(item) as parent:
            state = self.state(parent, item)
            self.emit(item, state)
            if state != 'cache_hit':
                with directory(source.parent) as incoming:
                    fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=incoming)
                try:
                    regular(fd)
                    self._publish_copy(fd, parent, item['sha256'], item, 0o444)
                finally:
                    os.close(fd)
        self.emit(item, 'ready', cache_hit=state == 'cache_hit')
        return {**item, 'state': 'ready', 'cache_hit': state == 'cache_hit'}

    def extract_source(self, value, destination):
        """Extract only a verified git archive; all members regular files/directories."""
        import tarfile
        item = artifact(value)
        destination = PurePosixPath(destination)
        if not destination.is_absolute() or '..' in destination.parts or destination.is_relative_to(self.root):
            raise ValueError('unsafe_source_destination')
        with self.locked(item), self.objects(item) as parent:
            fd = os.open(item['sha256'], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(fd, 'rb') as source:
                if not verified(source.fileno(), item):
                    raise ValueError('corrupt_source_archive')
                source.seek(0)
                with tarfile.open(fileobj=source, mode='r:') as archive:
                    members = archive.getmembers()
                    for member in members:
                        path = PurePosixPath(member.name)
                        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                            raise ValueError('unsafe_archive_member')
                    for member in members:
                        target = destination / member.name
                        if member.isdir():
                            with directory(target, create=True):
                                pass
                        else:
                            stream = archive.extractfile(member)
                            # Keep memory bounded, and publish each source leaf atomically.
                            with directory(target.parent, create=True) as out:
                                name = '.source-' + uuid.uuid4().hex
                                output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=out)
                                try:
                                    with os.fdopen(output, 'wb') as handle:
                                        while data := stream.read(CHUNK):
                                            handle.write(data)
                                        handle.flush()
                                        os.fchmod(handle.fileno(), member.mode & 0o777)
                                        os.fsync(handle.fileno())
                                    os.replace(name, target.name, src_dir_fd=out, dst_dir_fd=out)
                                finally:
                                    try: os.unlink(name, dir_fd=out)
                                    except FileNotFoundError: pass
        return {**item, 'state': 'ready'}

    def materialize_link(self, value, destination, destination_root, target):
        """Publish authenticated relative link metadata, never cache link referents."""
        item = artifact(value)
        root = PurePosixPath(str(destination_root))
        destination = PurePosixPath(str(destination))
        if (not root.is_absolute() or '..' in root.parts or '..' in destination.parts
                or not destination.is_relative_to(root) or destination == root
                or root.is_relative_to(self.root) or self.root.is_relative_to(root)):
            raise ValueError('unsafe_link_destination')
        if not isinstance(target, str) or not target or PurePosixPath(target).is_absolute():
            raise ValueError('unsafe_link_target')
        payload = target.encode('utf-8')
        if len(payload) != item['size_bytes'] or hashlib.sha256(payload).hexdigest() != item['sha256']:
            raise ValueError('link_identity_mismatch')
        resolved = PurePosixPath(os.path.realpath(destination.parent / target))
        lexical = PurePosixPath(os.path.abspath(destination.parent / target))
        if not resolved.is_relative_to(root) or not lexical.is_relative_to(root) or lexical == destination:
            raise ValueError('unsafe_link_target')
        with directory(destination.parent, create=True) as parent:
            temporary = '.link-' + uuid.uuid4().hex
            try:
                os.symlink(target, temporary, dir_fd=parent)
                os.replace(temporary, destination.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
        return {**item, 'state': 'ready'}

    def materialize(self, value, destination, destination_root, mode=0o644):
        item = artifact(value)
        if item.get('kind') == 'runtime_image':
            raise ValueError('runtime_images_require_references')
        destination, root = PurePosixPath(str(destination)), PurePosixPath(str(destination_root))
        if (not root.is_absolute() or '..' in root.parts or '..' in destination.parts
                or not destination.is_relative_to(root) or destination == root
                or destination.is_relative_to(self.root) or self.root.is_relative_to(destination)):
            raise ValueError('unsafe_destination')
        if type(mode) is not int or mode < 0 or mode > 0o777:
            raise ValueError('invalid_mode')
        with self.locked(item), self.objects(item) as objects:
            fd = os.open(item['sha256'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=objects)
            try:
                if not verified(fd, item, lambda **kw: self.emit(item, **kw)):
                    raise ValueError('corrupt_object')
                self.emit(item, 'materializing')
                with directory(destination.parent, create=True) as parent:
                    self._publish_copy(fd, parent, destination.name, item, mode)
            finally:
                os.close(fd)
        self.emit(item, 'ready')
        return {**item, 'state': 'ready'}


def runtime_images():
    """Load the exact shared stdlib helper: installed peer or committed source."""
    import importlib.util
    peer = Path(__file__).with_name('shared_runtime_images.py')
    if not peer.exists():
        peer = Path(__file__).resolve().parents[3] / 'scripts/lib/shared_runtime_images.py'
    # Shared publisher dependencies (including lifecycle) live beside that module.
    # The peer directory is either the authenticated helper generation or source archive.
    if str(peer.parent) not in sys.path:
        sys.path.insert(0, str(peer.parent))
    spec = importlib.util.spec_from_file_location('_bms_shared_runtime_images', peer)
    if spec is None or spec.loader is None:
        raise RuntimeError("shared_runtime_image_helper_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--events-jsonl')
    parser.add_argument('--execute-runtime')
    parser.add_argument('--manifest-sha256')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.execute_runtime:
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        Cache(args.root).execute_runtime(args.execute_runtime, command, args.manifest_sha256)
        return
    def events(value):
        if args.events_jsonl:
            path = PurePosixPath(args.events_jsonl)
            with directory(path.parent) as parent:
                fd = os.open(path.name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
            try:
                regular(fd)
                os.write(fd, (json.dumps(value, sort_keys=True) + '\n').encode())
            finally:
                os.close(fd)
    request = json.loads(sys.stdin.buffer.read(8 * 1024 * 1024 + 1))
    cache = Cache(args.root, events)
    action = request['action']
    if action == 'init':
        result = {'state': 'ready', 'schema': 'bms.artifact-cache.v1'}
    elif action == 'probe':
        result = {'artifacts': [cache.probe(a) for a in request['artifacts']]}
    elif action == 'ingest':
        result = cache.ingest(request['artifact'], request['source'])
    elif action == 'extract_source':
        result = cache.extract_source(request['artifact'], request['destination'])
    elif action == 'materialize_links':
        result = {'artifacts': [cache.materialize_link(row['artifact'], row['destination'], request['destination_root'], row['target']) for row in request['entries']]}
    elif action == 'materialize_many':
        result = {'artifacts': [cache.materialize_entry(row, request['destination_root']) for row in request['entries']]}
    elif action == 'materialize':
        result = cache.materialize(request['artifact'], request['destination'], request['destination_root'], request.get('mode', 0o644))
    else:
        raise ValueError('unsupported_action')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Paths, request contents and transport URLs never enter diagnostic output.
        print(json.dumps({'state': 'failed', 'error': type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
