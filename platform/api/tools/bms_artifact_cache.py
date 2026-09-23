#!/usr/bin/env python3
"""Verified instance-local artifact storage. JSON stdin; no persistent capabilities.

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
import posixpath
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import time
import uuid

CHUNK = 1024 * 1024

# Declared budgets. The stdin request document must stay small: any larger payload
# is published as a worker-side file and passed by reference, never inlined.
MAX_REQUEST_BYTES = 8 * 1024 * 1024
# Declared budget for one referenced worker-side document (the bundle's runtime
# listing and the weight layout it carries). validate_manifest bounds a layout at
# 100,000 rows, so the declared closure is well inside this bound.
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
# Declared budgets for the packed shared weight tree. Its members are accepted
# only by matching a row of the authenticated weight layout, so the archive's own
# digest is the transport identity and the layout stays the content authority. A
# bounded number of small accompanying members (an index or a packer manifest)
# may ride along; they are counted and never published.
ARCHIVE_SCHEMA = 'bms.weight-archive.v1'
MAX_UNMATCHED_ARCHIVE_MEMBERS = 64
MAX_UNMATCHED_ARCHIVE_BYTES = 1024 * 1024

# Closed diagnostic code set for these budgets. Each code is a fixed safe string,
# never constructed from request contents, paths or external prose.
REQUEST_CODES = {
    'request_too_large': 'helper request exceeds the declared request byte budget',
    'document_too_large': 'referenced document exceeds the declared document byte budget',
    'invalid_reference': 'referenced document declaration is invalid',
    'document_unavailable': 'referenced document is unavailable',
    'document_identity_mismatch': 'referenced document identity does not match its digest',
    'invalid_weight_layout_document': 'referenced weight layout document is invalid',
}


class RequestBudgetError(ValueError):
    """Typed helper failure carrying one closed-set diagnostic code."""

    def __init__(self, code):
        if code not in REQUEST_CODES:
            raise ValueError('unknown_request_code')
        super().__init__(REQUEST_CODES[code])
        self.code = code


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


def remove_partial_weight_tree(path):
    # Only unpublished stages are disposable; leave CAS objects and published
    # generations alone, including hardlinks into a failed stage.
    path = Path(path)
    if path.is_symlink():
        raise ValueError('unsafe_partial_weight_tree')
    if path.exists():
        for current, _, _ in os.walk(path, followlinks=False):
            os.chmod(current, 0o700)
        shutil.rmtree(path)


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
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('not_regular_file')
    return info


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


def weight_layout(entries):
    """Named, immutable projection of existing CAS bytes, not another cache.

    Job IDs and source revisions are deliberately absent from its identity.
    Acquisition, locking and publication remain owned by Cache.
    """
    rows, names = [], set()
    if not isinstance(entries, list) or not entries:
        raise ValueError('invalid_weight_layout')
    for value in entries:
        row = dict(value)
        path = PurePosixPath(row['name'])
        if (set(row) != {'name', 'sha256', 'size_bytes', 'mode'} | ({'target'} if 'target' in row else set())
                or str(path) != row['name'] or path.is_absolute() or '..' in path.parts
                or not path.parts or path.parts[0].startswith('.') or row['name'] in names
                or type(row['mode']) is not int or not 0 <= row['mode'] <= 0o777):
            raise ValueError('invalid_weight_member')
        artifact({k: row[k] for k in ('sha256', 'size_bytes')})
        names.add(row['name'])
        if 'target' not in row:
            row['mode'] &= 0o555
        rows.append(row)
    directories = {str(p) for name in names for p in PurePosixPath(name).parents if str(p) != '.'}
    if names & directories:
        raise ValueError('conflicting_weight_members')
    for row in rows:
        if 'target' in row:
            target = row['target']
            dest = PurePosixPath(posixpath.normpath(str(PurePosixPath(row['name']).parent / target)))
            if (not isinstance(target, str) or not target or PurePosixPath(target).is_absolute()
                    or '..' in dest.parts or str(dest) not in names | directories
                    or PurePosixPath(row['name']).is_relative_to(dest)
                    or any(r['name'] == str(dest) and 'target' in r for r in rows)
                    or row['mode'] != 0o777 or row['size_bytes'] != len(target.encode())
                    or row['sha256'] != hashlib.sha256(target.encode()).hexdigest()):
                raise ValueError('invalid_weight_link')
    rows.sort(key=lambda row: row['name'])
    payload = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(payload).hexdigest(), rows, payload


def archive_rows(rows):
    """Layout rows that can be carried by a packed archive, keyed by their digest.

    Link rows project a target string rather than member content, so they are not
    archive members. One digest may only ever name one size.
    """
    expected = {}
    for row in rows:
        if 'target' in row:
            continue
        previous = expected.setdefault(row['sha256'], row)
        if previous['size_bytes'] != row['size_bytes']:
            raise ValueError('conflicting_weight_members')
    return expected


LAYOUT_DOCUMENT = '.bms-runtime-images.json'
LAYOUT_SCHEMA = 'bms.runtime-image-references.v1'


def read_document(path, limit=MAX_DOCUMENT_BYTES):
    """Read one declared, bounded worker-side document without following links."""
    path = PurePosixPath(str(path))
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        regular(fd)
        if os.fstat(fd).st_size > limit:
            raise RequestBudgetError('document_too_large')
        data = bytearray()
        while chunk := os.read(fd, min(CHUNK, limit + 1 - len(data))):
            data.extend(chunk)
            if len(data) > limit:
                raise RequestBudgetError('document_too_large')
        return bytes(data)
    finally:
        os.close(fd)


def weight_layout_reference(reference):
    """Resolve a by-reference weight layout from the worker's runtime listing.

    The listing is the document bundle.py already writes into the attempt runtime
    directory and records in the authenticated envelope, so the request carries a
    path and digest instead of every row. Document identity, schema, placement and
    row shape are re-verified here before the rows are used for the shared view.
    """
    if (not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}
            or not isinstance(reference['sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', reference['sha256'])):
        raise RequestBudgetError('invalid_reference')
    path = PurePosixPath(str(reference['path']))
    if not path.is_absolute() or '..' in path.parts or path.name != LAYOUT_DOCUMENT:
        raise RequestBudgetError('invalid_reference')
    try:
        payload = read_document(path)
    except FileNotFoundError:
        raise RequestBudgetError('document_unavailable') from None
    if hashlib.sha256(payload).hexdigest() != reference['sha256']:
        raise RequestBudgetError('document_identity_mismatch')
    try:
        document = json.loads(payload)
    except ValueError:
        raise RequestBudgetError('invalid_weight_layout_document') from None
    if (not isinstance(document, dict) or document.get('schema') != LAYOUT_SCHEMA
            or document.get('runtime_root') != str(path.parent)
            or not isinstance(document.get('weights'), list)):
        raise RequestBudgetError('invalid_weight_layout_document')
    return document['weights']


class Cache:
    def __init__(self, root, events=None):
        self.root = PurePosixPath(str(root))
        self.events = events or (lambda value: None)
        for name in ('objects/sha256', 'incoming', 'locks', 'weights', 'archives'):
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
        # A warm ingest needs one authoritative hash under the lifecycle lock,
        # not probe_runtime's full hash followed by ensure_lease's full hash.
        # Presence is determined by the digest directory, never by a missing
        # runtime.sif inside a damaged published generation.
        parent = self.image_path(item).parent
        with directory(parent.parent, create=True) as fd:
            try:
                os.stat(parent.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                present = False
            else:
                present = True
        if present:
            runtime_lifecycle().ensure_lease(self.image_store, [item['sha256']],
                owner='cache-artifact:' + item['sha256'],
                expected_sizes={item['sha256']: item['size_bytes']})
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
        runtime_lifecycle().publish_leased_image(Path(source), self.image_store, item['sha256'],
            owner='cache-artifact:' + item['sha256'])
        return {**item, 'state': 'ready', 'cache_hit': False}

    def runtime_alias(self, value, destination, runtime_root, *, check=False):
        item = artifact(value)
        root, destination = PurePosixPath(str(runtime_root)), PurePosixPath(str(destination))
        attempts = self.root.parent.parent.parent / 'attempts'
        relative = root.relative_to(attempts)
        if (not root.is_absolute() or '..' in root.parts or len(relative.parts) != 3
                or relative.parts[1:] != ('materialized', 'runtime')
                or str(uuid.UUID(relative.parts[0])) != relative.parts[0]
                or '..' in destination.parts or destination == root
                or not destination.is_relative_to(root)):
            raise ValueError('unsafe_runtime_alias')
        runtime_lifecycle().ensure_lease(self.image_store, [item['sha256']],
            owner='attempt:' + relative.parts[0] + ':image:' + item['sha256'],
            expected_sizes={item['sha256']: item['size_bytes']})
        target = self.image_path(item)
        with directory(destination.parent, create=not check) as parent:
            # Exact, controller-derived target only, not arbitrary external links.
            if check:
                if os.readlink(destination.name, dir_fd=parent) != str(target):
                    raise ValueError('runtime_alias_mismatch')
            else:
                try:
                    os.symlink(str(target), destination.name, dir_fd=parent)
                except FileExistsError:
                    # Recovery may repeat materialization, never retarget an alias.
                    if os.readlink(destination.name, dir_fd=parent) != str(target):
                        raise ValueError('runtime_alias_mismatch')
                os.fsync(parent)
        return {**item, 'state': 'ready'}

    def materialize_entry(self, row, destination_root):
        if row['artifact'].get('kind') == 'runtime_image':
            item = artifact(row['artifact'])
            if str(self.image_path(item)) != row['destination']:
                raise ValueError('runtime_image_destination_mismatch')
            if not row['aliases']:
                raise ValueError('missing_runtime_alias')
            for alias in row['aliases']:
                self.runtime_alias(item, alias, row['runtime_root'])
            return {**item, 'state': 'ready'}
        return self.materialize(row['artifact'], row['destination'], destination_root, row.get('mode', 0o644))

    def weights(self, entries, *, install=False, full=False):
        """Resolve an immutable named view of the existing content objects.

        The only durable bytes are still CAS objects. Read-only aliases are
        never exposed as writable task binds. A warm lookup reads metadata;
        execute_runtime verifies the selected bytes at the use boundary.
        """
        digest, rows, payload = weight_layout(entries)
        root = Path(self.root) / 'weights' / digest
        expected = {r['name']: r for r in rows}
        dirs = {str(p) for n in expected for p in PurePosixPath(n).parents if str(p) != '.'}

        def check(path, content=False):
            seen, before = set(), {}
            def walk(parent, prefix=''):
                info = os.fstat(parent)
                if stat.S_IMODE(info.st_mode) != 0o555:
                    raise ValueError('writable_weight_directory')
                before[prefix] = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
                for name in sorted(os.listdir(parent)):
                    rel = prefix + name
                    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if rel in dirs:
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        try:
                            walk(child, rel + '/')
                        finally:
                            os.close(child)
                        continue
                    if rel == '.bms-weights.json':
                        item = dict(sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload), mode=0o444)
                    else:
                        item = expected.get(rel)
                        if item is None:
                            raise ValueError('unexpected_weight_member')
                    seen.add(rel)
                    if 'target' in item:
                        if not stat.S_ISLNK(info.st_mode) or os.readlink(name, dir_fd=parent) != item['target']:
                            raise ValueError('weight_link_changed')
                    else:
                        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                        try:
                            first = regular(fd)
                            signature = (first.st_dev, first.st_ino, first.st_size, first.st_mode,
                                         first.st_mtime_ns, first.st_ctime_ns)
                            if first.st_size != item['size_bytes'] or stat.S_IMODE(first.st_mode) != item['mode']:
                                raise ValueError('weight_identity_changed')
                            if (content or rel == '.bms-weights.json') and not verified(fd, item):
                                raise ValueError('weight_hash_mismatch')
                            last = regular(fd)
                            if signature != (last.st_dev, last.st_ino, last.st_size, last.st_mode,
                                             last.st_mtime_ns, last.st_ctime_ns):
                                raise ValueError('weight_changed_during_read')
                            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                            if (current.st_dev, current.st_ino) != (first.st_dev, first.st_ino):
                                raise ValueError('weight_path_changed')
                        finally:
                            os.close(fd)
                after = os.fstat(parent)
                if before[prefix] != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError('weight_directory_changed')
            with directory(path) as fd:
                walk(fd)
                with directory(path) as current:
                    info = os.fstat(current)
                    if before[''] != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns):
                        raise ValueError('weight_root_changed')
            if seen != set(expected) | {'.bms-weights.json'}:
                raise ValueError('missing_weight_member')

        with self.locked(dict(sha256='weights-' + digest, size_bytes=0)):
            try:
                check(root, full)
            except FileNotFoundError:
                # A damaged published generation is never repaired in place.
                if root.exists() or root.is_symlink():
                    raise ValueError('damaged_weight_layout')
                if not install:
                    return dict(state='missing', root=str(root), sha256=digest)
                for stale in (Path(self.root) / 'weights').glob('.partial-' + digest + '-*'):
                    remove_partial_weight_tree(stale)
                stage = self.root / 'weights' / ('.partial-' + digest + '-' + uuid.uuid4().hex)
                try:
                    with directory(stage, create=True):
                        pass
                    for row in sorted(rows, key=lambda r: 'target' in r):
                        destination = stage / row['name']
                        if 'target' in row:
                            with directory(destination.parent, create=True) as out:
                                os.symlink(row['target'], destination.name, dir_fd=out)
                                os.fsync(out)
                            continue
                        with self.locked(row), self.objects(row) as objects, directory(destination.parent, create=True) as out:
                            source = os.open(row['sha256'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=objects)
                            try:
                                info = regular(source)
                                if stat.S_IMODE(info.st_mode) != 0o444 or not verified(source, row):
                                    raise ValueError('corrupt_weight_object')
                                if row['mode'] == 0o444:
                                    os.link(row['sha256'], destination.name, src_dir_fd=objects,
                                            dst_dir_fd=out, follow_symlinks=False)
                                    linked = os.stat(destination.name, dir_fd=out, follow_symlinks=False)
                                    if (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino):
                                        raise ValueError('weight_object_changed')
                                    os.fsync(out)
                                else:
                                    # An executable permission projection cannot chmod
                                    # other aliases of an immutable content object.
                                    self._publish_copy(source, out, destination.name, row, row['mode'])
                            finally:
                                os.close(source)
                    with directory(stage) as parent:
                        fd = os.open('.bms-weights.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                        with os.fdopen(fd, 'wb') as stream:
                            stream.write(payload)
                            stream.flush()
                            os.fchmod(stream.fileno(), 0o444)
                            os.fsync(stream.fileno())
                    for name in sorted(dirs, key=lambda n: len(PurePosixPath(n).parts), reverse=True):
                        with directory(stage / name) as fd:
                            os.fchmod(fd, 0o555)
                            os.fsync(fd)
                    with directory(stage) as fd:
                        os.fchmod(fd, 0o555)
                        os.fsync(fd)
                    check(stage)
                    with directory(root.parent) as parent:
                        os.rename(stage.name, root.name, src_dir_fd=parent, dst_dir_fd=parent)
                        os.fsync(parent)
                finally:
                    remove_partial_weight_tree(stage)
        return dict(state='ready', root=str(root), sha256=digest)

    def archive_root(self, archive):
        return self.root / 'archives' / archive['sha256']

    def archive_record(self, archive, layout_sha256):
        """The completion record for one archive pass, or None.

        Every object in the content store is verified on its own, but only this
        record is evidence that a pass finished over the whole declared layout.
        A record that does not name exactly this archive and this layout is not
        one, so a stale record can never certify a different pass.
        """
        try:
            payload = read_document(self.archive_root(archive) / 'complete.json')
        except (FileNotFoundError, RequestBudgetError, OSError):
            return None
        try:
            record = json.loads(payload)
        except ValueError:
            return None
        if (not isinstance(record, dict) or record.get('schema') != ARCHIVE_SCHEMA
                or record.get('archive') != {'sha256': archive['sha256'],
                                              'size_bytes': archive['size_bytes']}
                or record.get('layout_sha256') != layout_sha256
                or not isinstance(record.get('matched'), list)
                or not isinstance(record.get('missing'), list)
                or type(record.get('unmatched')) is not int or record['unmatched'] < 0
                or not all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
                           for value in record['matched'] + record['missing'])):
            return None
        return record

    def object_present(self, row):
        """Metadata-only presence, as the warm weight-view lookup already relies
        on; complete bytes are still verified by weights() before publication."""
        with self.objects(row) as parent:
            try:
                info = os.stat(row['sha256'], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
        return stat.S_ISREG(info.st_mode) and info.st_size == row['size_bytes']

    def absent_objects(self, digests, expected):
        return sorted(digest for digest in digests
                      if digest in expected and not self.object_present(expected[digest]))

    def archive_started(self, item):
        """Whether any pass for this archive identity ever created worker state."""
        with directory(self.root / 'archives') as parent:
            try:
                os.stat(item['sha256'], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
        return True

    def archive_state(self, archive, layout_reference):
        """Report the packed-archive pass for an already-authenticated layout.

        'ready' requires a record naming exactly this archive and layout, with
        every object it claims still present. 'partial' means a pass started and
        did not complete. 'missing' means nothing of this archive was unpacked.
        A partial pass is therefore never read as a complete weight tree, and no
        view exists until weights() publishes one from verified objects.
        """
        item = artifact(archive)
        layout_sha256, rows, _ = weight_layout(weight_layout_reference(layout_reference))
        expected = archive_rows(rows)
        with self.locked(dict(sha256='archive-' + item['sha256'], size_bytes=0)):
            record = self.archive_record(item, layout_sha256)
            if record is None:
                started = self.archive_started(item)
                return dict(state='partial' if started else 'missing', archive=item,
                            layout_sha256=layout_sha256, matched=[], missing=[], absent=[])
            absent = self.absent_objects(record['matched'], expected)
            return dict(state='partial' if absent else 'ready', archive=item,
                        layout_sha256=layout_sha256, matched=record['matched'],
                        missing=record['missing'], absent=absent)

    def unpack_weight_archive(self, archive, operation_id, batch_id, layout_reference):
        """Publish one packed archive's members into the content store.

        The archive's own bytes are authenticated against the declared object
        identity before a single member is read, and each member is accepted only
        by matching a row of the authenticated weight layout by digest, published
        under that digest. A truncated, foreign or extra-laden archive can only
        yield fewer verified objects, never wrong ones. The view is still
        published by weights() from verified objects, so an interrupted pass
        cannot present a partial weight tree as complete.
        """
        item = artifact(archive)
        layout_sha256, rows, _ = weight_layout(weight_layout_reference(layout_reference))
        expected = archive_rows(rows)
        try:
            source = self.incoming_batch(operation_id, batch_id) / item['sha256']
        except FileNotFoundError:
            raise ValueError('archive_source_unavailable') from None
        with self.locked(dict(sha256='archive-' + item['sha256'], size_bytes=0)):
            record = self.archive_record(item, layout_sha256)
            if record is not None and not self.absent_objects(record['matched'], expected):
                return dict(state='ready', archive=item, layout_sha256=layout_sha256,
                            matched=record['matched'], missing=record['missing'],
                            unmatched=record['unmatched'])
            try:
                with directory(source.parent) as incoming:
                    fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=incoming)
            except FileNotFoundError:
                raise ValueError('archive_source_unavailable') from None
            with os.fdopen(fd, 'rb') as stream:
                regular(stream.fileno())
                # Authenticate the transport bytes before reading any member.
                if not verified(stream.fileno(), item):
                    raise ValueError('archive_identity_mismatch')
                stream.seek(0)
                matched, unmatched = self._unpack_members(stream, expected, item)
            if not matched:
                raise ValueError('archive_carries_no_declared_member')
            missing = sorted(set(expected) - matched)
            self._write_archive_record(item, layout_sha256, sorted(matched), missing, unmatched)
            return dict(state='ready', archive=item, layout_sha256=layout_sha256,
                        matched=sorted(matched), missing=missing, unmatched=unmatched)

    def _unpack_members(self, stream, expected, item):
        import tarfile
        stage = self.archive_root(item) / ('stage-' + uuid.uuid4().hex)
        with directory(stage, create=True):
            pass
        # Member data can never exceed the declared size of the archive itself, so
        # any subset of the tree is acceptable and a larger stream is not.
        budget = item['size_bytes']
        extracted, unmatched_members, unmatched_bytes = 0, 0, 0
        matched = set()
        try:
            with tarfile.open(fileobj=stream, mode='r|*') as archive:
                for member in archive:
                    if member.isdir():
                        continue
                    if not member.isfile() or member.size < 0:
                        raise ValueError('unsupported_archive_member')
                    extracted += member.size
                    if extracted > budget:
                        raise ValueError('archive_payload_exceeds_layout')
                    digest, size, staged = self._stage_member(archive, member, stage)
                    row = expected.get(digest)
                    if row is not None and row['size_bytes'] != size:
                        raise ValueError('archive_member_size_mismatch')
                    if row is None:
                        self._discard(stage, staged)
                        unmatched_members += 1
                        unmatched_bytes += size
                        if (unmatched_members > MAX_UNMATCHED_ARCHIVE_MEMBERS
                                or unmatched_bytes > MAX_UNMATCHED_ARCHIVE_BYTES):
                            raise ValueError('unexpected_archive_member')
                        self.emit(dict(sha256=digest, size_bytes=size), 'ignored')
                        continue
                    self._publish_archive_object(row, staged, stage)
                    matched.add(digest)
                    self.emit(row, 'published', cache_hit=False)
        except tarfile.TarError:
            raise ValueError('archive_stream_invalid') from None
        finally:
            self._discard_stage(stage)
        return matched, unmatched_members

    def _stage_member(self, archive, member, stage):
        """Stream one member to private staging, hashing its bytes as they arrive."""
        source = archive.extractfile(member)
        if source is None:
            raise ValueError('unreadable_archive_member')
        name = 'member-' + uuid.uuid4().hex
        digest, done = hashlib.sha256(), 0
        with directory(stage) as parent:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                         dir_fd=parent)
            try:
                while chunk := source.read(CHUNK):
                    done += len(chunk)
                    if done > member.size:
                        raise ValueError('archive_member_size_mismatch')
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        count = os.write(fd, view)
                        if count <= 0:
                            raise ValueError('archive_write_failed')
                        view = view[count:]
                if done != member.size:
                    raise ValueError('archive_member_size_mismatch')
                # Content objects are immutable and read-only in the CAS.
                os.fchmod(fd, 0o444)
                os.fsync(fd)
            finally:
                os.close(fd)
        return digest.hexdigest(), done, name

    def _publish_archive_object(self, row, staged, stage):
        """Move hashed staging bytes to the digest that names them, atomically."""
        with self.objects(row) as parent:
            if self.object_present(row):
                # Identical content already published by an earlier pass.
                self._discard(stage, staged)
                return False
            with directory(stage) as staging:
                os.rename(staged, row['sha256'], src_dir_fd=staging, dst_dir_fd=parent)
                os.fsync(parent)
        return True

    def _discard(self, stage, name):
        try:
            with directory(stage) as parent:
                os.unlink(name, dir_fd=parent)
        except FileNotFoundError:
            pass

    def _discard_stage(self, stage):
        try:
            with directory(stage) as parent:
                for name in os.listdir(parent):
                    os.unlink(name, dir_fd=parent)
        except FileNotFoundError:
            return
        with directory(stage.parent) as parent:
            os.rmdir(stage.name, dir_fd=parent)

    def _write_archive_record(self, archive, layout_sha256, matched, missing, unmatched):
        payload = json.dumps(dict(schema=ARCHIVE_SCHEMA,
                                  archive={'sha256': archive['sha256'],
                                           'size_bytes': archive['size_bytes']},
                                  layout_sha256=layout_sha256, matched=matched,
                                  missing=missing, unmatched=unmatched),
                             sort_keys=True, separators=(',', ':')).encode()
        with directory(self.archive_root(archive), create=True) as parent:
            temporary = '.partial-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                         dir_fd=parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fchmod(stream.fileno(), 0o444)
                    os.fsync(stream.fileno())
                # Publication of the record is the only completeness boundary.
                os.replace(temporary, 'complete.json', src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                self._discard(self.archive_root(archive), temporary)

    def execute_runtime(self, manifest, command, expected_sha256):
        path = Path(manifest)
        with directory(path.parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as stream:
                regular(stream.fileno())
                payload = stream.read(MAX_DOCUMENT_BYTES + 1)
                if len(payload) > MAX_DOCUMENT_BYTES:
                    raise RequestBudgetError('document_too_large')
                if hashlib.sha256(payload).hexdigest() != expected_sha256:
                    raise ValueError('runtime_manifest_identity_mismatch')
                references = json.loads(payload)
        if references['schema'] != 'bms.runtime-image-references.v1':
            raise ValueError('invalid_runtime_manifest')
        if path != Path(references['runtime_root']) / '.bms-runtime-images.json':
            raise ValueError('invalid_runtime_manifest_path')
        for row in references['images']:
            if not row['aliases']:
                raise ValueError('missing_runtime_alias')
            for alias in row['aliases']:
                self.runtime_alias(row, alias, references['runtime_root'], check=True)
        if references.get('weights'):
            result = self.weights(references['weights'], full=True)
            if result['state'] != 'ready':
                raise ValueError('shared_weights_missing')
            os.environ['BMS_SHARED_WEIGHTS_ROOT'] = result['root']
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

    def incoming_batch(self, operation_id, batch_id, *, create=False):
        if str(uuid.UUID(operation_id)) != operation_id or uuid.UUID(batch_id).hex != batch_id:
            raise ValueError('invalid_incoming_identity')
        path = self.root / 'incoming' / operation_id / batch_id
        with directory(path.parent, create=create) as parent:
            if create:
                os.mkdir(path.name, 0o700, dir_fd=parent)
            with directory(path):
                pass
        return path

    def acquire_hf(self, value, operation_id, batch_id, source):
        # The installed helper imports its authenticated peer; source imports
        # use the same file through the API tools package.
        if __package__:
            from . import bms_hf_transfer as hf
        else:
            import bms_hf_transfer as hf
        item = None
        try:
            item = artifact(value)
            hf.validate_source(source)
            path = self.incoming_batch(operation_id, batch_id)
            with directory(path) as parent:
                fd = os.open(item['sha256'], os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
                             | os.O_NONBLOCK, 0o600, dir_fd=parent)
                try:
                    regular(fd)
                    info = os.fstat(fd)
                    if (info.st_nlink != 1 or info.st_uid != os.geteuid()
                            or stat.S_IMODE(info.st_mode) != 0o600):
                        raise hf.TransferError('hf_unsafe_incoming_file')
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise hf.TransferError('hf_acquisition_busy') from None
                    result = hf.download(fd, item, source)
                    current = os.stat(item['sha256'], dir_fd=parent, follow_symlinks=False)
                    if (current.st_dev, current.st_ino, current.st_nlink) != (info.st_dev, info.st_ino, 1):
                        raise hf.TransferError('hf_incoming_identity_changed')
                    os.fsync(parent)
                    return result
                finally:
                    os.close(fd)
        except hf.SourceExpired:
            return {**(item or {}), 'state': 'source_expired'}
        except hf.TransferError:
            raise
        except Exception:
            raise hf.TransferError('hf_acquisition_failed') from None

    def ingest_many(self, values, operation_id, batch_id):
        items = [artifact(value) for value in values]
        if (not items or len(items) > 2048
                or sum(item['size_bytes'] for item in items) > 256 * 1024 * 1024
                or any(item.get('kind') for item in items)
                or len({item['sha256'] for item in items}) != len(items)):
            raise ValueError('invalid_ingest_batch')
        source = self.incoming_batch(operation_id, batch_id)
        return {'artifacts': [self.ingest(item, source / item['sha256']) for item in items]}

    def remove_incoming(self, operation_id, batch_id):
        path = self.incoming_batch(operation_id, batch_id)
        with directory(path) as parent:
            for name in os.listdir(parent):
                # Only task-owned digest files, never recursively delete trees.
                if not re.fullmatch('[0-9a-f]{64}', name):
                    raise ValueError('unexpected_incoming_file')
                os.unlink(name, dir_fd=parent)
        with directory(path.parent) as parent:
            os.rmdir(path.name, dir_fd=parent)
        return {'state': 'ready'}

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
                # Autodetection also permits retained uncompressed attempts. The
                # digest above authenticates transport bytes before decompression.
                with tarfile.open(fileobj=source, mode='r:*') as archive:
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
                regular(fd)
                # _publish_copy hashes the exact bytes it copies before atomic
                # publication. A separate pre-read only hashes the same object
                # twice and does not strengthen the use-boundary check.
                self.emit(item, 'materializing')
                with directory(destination.parent, create=True) as parent:
                    self._publish_copy(fd, parent, destination.name, item, mode)
            finally:
                os.close(fd)
        self.emit(item, 'ready')
        return {**item, 'state': 'ready'}


def runtime_lifecycle():
    """Load the installed peer as a package so its relative imports stay exact."""
    import importlib
    import types
    peer = Path(__file__).with_name('runtime_image_lifecycle.py')
    if not peer.exists():
        peer = Path(__file__).resolve().parents[3] / 'scripts/lib/runtime_image_lifecycle.py'
    name = '_bms_image_authority_' + hashlib.sha256(str(peer.parent).encode()).hexdigest()[:16]
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(peer.parent)]
        sys.modules[name] = package
    return importlib.import_module(name + '.runtime_image_lifecycle')


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
    payload = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(payload) > MAX_REQUEST_BYTES:
        raise RequestBudgetError('request_too_large')
    request = json.loads(payload)
    cache = Cache(args.root, events)
    action = request['action']
    if action == 'init':
        result = {'state': 'ready', 'schema': 'bms.artifact-cache.v1'}
    elif action == 'probe':
        result = {'artifacts': [cache.probe(a) for a in request['artifacts']]}
    elif action == 'prepare_incoming':
        result = {'source': str(cache.incoming_batch(request['operation_id'], request['batch_id'], create=True))}
    elif action == 'acquire_hf':
        result = cache.acquire_hf(request['artifact'], request['operation_id'], request['batch_id'], request['source'])
    elif action == 'ingest_many':
        result = cache.ingest_many(request['artifacts'], request['operation_id'], request['batch_id'])
    elif action == 'remove_incoming':
        result = cache.remove_incoming(request['operation_id'], request['batch_id'])
    elif action == 'ingest':
        result = cache.ingest(request['artifact'], request['source'])
    elif action == 'extract_source':
        result = cache.extract_source(request['artifact'], request['destination'])
    elif action == 'materialize_links':
        result = {'artifacts': [cache.materialize_link(row['artifact'], row['destination'], request['destination_root'], row['target']) for row in request['entries']]}
    elif action in {'weights_probe', 'weights_install'}:
        # Exactly one declared layout form: inline rows, or a by-reference
        # listing whose digest is verified before its rows are used.
        declared = [key for key in ('entries', 'layout') if key in request]
        if len(declared) != 1:
            raise ValueError('invalid_weight_request')
        entries = (request['entries'] if declared[0] == 'entries'
                   else weight_layout_reference(request['layout']))
        result = cache.weights(entries, install=action == 'weights_install')
    elif action in {'weights_archive_probe', 'unpack_weights_archive'}:
        # One packed archive replaces the per-file relay of the shared weight
        # tree; the payload is one cache object, referenced by its declared
        # identity and checked against the same authenticated layout.
        if action == 'weights_archive_probe':
            result = cache.archive_state(request['archive'], request['layout'])
        else:
            result = cache.unpack_weight_archive(request['archive'], request['operation_id'],
                                                 request['batch_id'], request['layout'])
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
        # Declared-budget failures report their closed-set code, never a bare type.
        error = exc.code if isinstance(exc, RequestBudgetError) else type(exc).__name__
        print(json.dumps({'state': 'failed', 'error': error}), file=sys.stderr)
        raise SystemExit(1)
