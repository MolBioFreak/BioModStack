#!/usr/bin/env python3
"""Verified instance-local artifact storage. JSON stdin; no persistent capabilities.

Roots and destinations are trusted controller configuration, never operator input.
Every directory is pinned with O_NOFOLLOW; published bytes are never written in
place. Copies, rather than hardlinks, isolate writable execution materializations.

Verification happens once, where bytes are published: the publisher hashes what
it writes, the layout walk hashes what it installs, and each result is recorded
as a durable receipt (digest, size, helper generation, verification epoch and
the published file's device/inode/size/mode/mtime/ctime). Warm read paths - a
probe, a materialization, a source extraction, a weight layout at the use
boundary - consume that receipt instead of re-reading the same bytes, which
removes the duplicated multi-gigabyte hashing of a staging run. A receipt is
never an authority: an absent, stale, foreign or mismatched one falls back to
full verification of the bytes, and `action=sweep` re-derives every receipt from
the bytes on demand. The trade is deliberate and bounded: a receipt proves the
bytes are the verified publication as long as the file's inode, times and size
are untouched (a rewrite always moves ctime), while silent media corruption that
preserves all of that is caught by the explicit sweep rather than by every read.
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


# Durable verification receipts. One is written whenever bytes are verified at
# publication, and warm read paths consume it instead of re-reading the same
# bytes. A receipt never replaces the verification a publication performs, and
# the explicit integrity sweep re-derives every receipt from bytes on demand.
RECEIPT_SCHEMA = 'bms.artifact-cache-receipt.v1'
LAYOUT_RECEIPT_SCHEMA = 'bms.artifact-cache-layout-receipt.v1'
MAX_RECEIPT_BYTES = 64 * 1024 * 1024

_HELPER_IDENTITY: list = []


def helper_identity():
    """Identity of the running helper generation: the digest of its own source.

    Receipts are honoured only by the exact generation that wrote them, so a
    helper change re-verifies every object once instead of trusting a receipt
    written under different verification semantics. An unidentifiable helper
    writes no receipt at all, which keeps every warm path on full verification.
    """
    if not _HELPER_IDENTITY:
        try:
            source = Path(__file__)
            raw = source.read_bytes() if source.is_file() else b''
        except OSError:
            raw = b''
        _HELPER_IDENTITY.append(hashlib.sha256(raw).hexdigest() if raw else None)
    return _HELPER_IDENTITY[0]


def signature(info):
    """Compact published-file identity: device, inode, size, mode, mtime, ctime.

    Any in-place rewrite or replacement of the bytes moves at least one of these
    (ctime is not settable by an unprivileged writer), so a matching signature
    means the file still holds exactly the publication that was verified.
    """
    return [info.st_dev, info.st_ino, info.st_size, stat.S_IMODE(info.st_mode),
            info.st_mtime_ns, info.st_ctime_ns]


def read_receipt(path):
    """Best-effort durable receipt read.

    An absent, unreadable, oversized or malformed document is simply "no
    receipt": every caller then verifies the bytes instead of trusting one.
    """
    try:
        payload = read_document(path, MAX_RECEIPT_BYTES)
    except (OSError, ValueError, RequestBudgetError):
        return None
    try:
        document = json.loads(payload)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def write_receipt(path, document):
    """Atomically publish one immutable receipt.

    A receipt is an optimisation, never an authority: a failed write is reported
    to the caller and costs one future verification, so it is never fatal.
    """
    path = PurePosixPath(str(path))
    payload = json.dumps(document, sort_keys=True, separators=(',', ':')).encode()
    if not payload or len(payload) > MAX_RECEIPT_BYTES:
        return False
    try:
        with directory(path.parent, create=True) as parent:
            temporary = '.partial-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(fd, view):]
                os.fchmod(fd, 0o444)
                os.fsync(fd)
                os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(fd)
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
    except OSError:
        return False
    return True


def receipted_member(receipt, name, info):
    """True when one verified layout generation still covers this member."""
    return receipt is not None and receipt['members'].get(name) == signature(info)


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
        for name in ('objects/sha256', 'incoming', 'locks', 'weights', 'receipts/objects', 'receipts/weights'):
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
            runtime_lifecycle().ensure_lease(self.image_store, [item['sha256']],
                owner='cache-artifact:' + item['sha256'])
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
        _, identities = runtime_lifecycle().ensure_lease(self.image_store, [item['sha256']],
            owner='attempt:' + relative.parts[0] + ':image:' + item['sha256'])
        if identities[item['sha256']]['size'] != item['size_bytes']:
            raise ValueError('runtime_image_size_mismatch')
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

    def weights(self, entries, *, install=False, full=False, refresh=False):
        """Resolve an immutable named view of the existing content objects.

        The only durable bytes are still CAS objects. Read-only aliases are
        never exposed as writable task binds. A warm lookup reads metadata, and
        the use boundary consumes the durable receipt the publisher wrote rather
        than re-reading the same bytes. `install=True` republishes from verified
        CAS objects and `refresh=True` re-reads every member and rewrites the
        receipt, which is the explicit integrity sweep for one generation.
        """
        digest, rows, payload = weight_layout(entries)
        root = Path(self.root) / 'weights' / digest
        expected = {r['name']: r for r in rows}
        dirs = {str(p) for n in expected for p in PurePosixPath(n).parents if str(p) != '.'}
        # A publication always re-verifies; a warm read may consume the receipt
        # the last publication wrote for exactly this generation.
        receipt = None if (install or refresh) else self.layout_receipt(digest)

        def check(path, content=False, record=None):
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
                        # The layout digest is the same digest weight_layout()
                        # just derived; never re-hash the 12 MB payload here.
                        item = dict(sha256=digest, size_bytes=len(payload), mode=0o444)
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
                            if first.st_size != item['size_bytes'] or stat.S_IMODE(first.st_mode) != item['mode']:
                                raise ValueError('weight_identity_changed')
                            # Member bytes are re-read only when no durable
                            # receipt covers exactly this published file, or when
                            # a publication/sweep asked for them to be read.
                            if ((content or rel == '.bms-weights.json')
                                    and not receipted_member(receipt, rel, first)
                                    and not verified(fd, item)):
                                raise ValueError('weight_hash_mismatch')
                            last = regular(fd)
                            if signature(first) != signature(last):
                                raise ValueError('weight_changed_during_read')
                            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                            if (current.st_dev, current.st_ino) != (first.st_dev, first.st_ino):
                                raise ValueError('weight_path_changed')
                            if record is not None:
                                record[rel] = signature(first)
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
            members = {}
            try:
                check(root, full, record=members)
            except FileNotFoundError:
                # A damaged published generation is never repaired in place.
                if root.exists() or root.is_symlink():
                    raise ValueError('damaged_weight_layout')
                if not install:
                    return dict(state='missing', root=str(root), sha256=digest)
                stage = self.root / 'weights' / ('.partial-' + uuid.uuid4().hex)
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
                check(stage, record=members)
                with directory(root.parent) as parent:
                    os.rename(stage.name, root.name, src_dir_fd=parent, dst_dir_fd=parent)
                    os.fsync(parent)
                # Member signatures are path-independent, so the receipt the
                # staged tree just proved is the receipt for the published
                # generation: warm lookups no longer re-read these bytes.
                self.record_layout_receipt(digest, members)
            else:
                if refresh or (full and receipt is None):
                    # Every member above was read and verified in this pass, so
                    # the receipt is re-derived from bytes: a sweep refreshes it,
                    # and a store whose receipt is missing (or was written by an
                    # older helper) heals in one pass instead of re-reading every
                    # layout at every use boundary from then on.
                    self.record_layout_receipt(digest, members)
        return dict(state='ready', root=str(root), sha256=digest)

    def sweep(self, include=('objects',)):
        """Explicit integrity sweep: re-read every published byte in the store.

        Warm paths consume durable receipts; this re-derives them from the bytes
        instead, so an operator can prove a store without trusting a receipt.
        Published bytes are never repaired, replaced or deleted - a damaged
        identity is reported by name - only derived receipts are rewritten, and
        a receipt that a read disproved is dropped so it cannot authorise a
        later warm path.
        """
        report = {'state': 'ready', 'objects': 0, 'verified': 0,
                  'layouts': 0, 'layouts_verified': 0, 'damaged': []}
        if 'objects' in include:
            prefixes = self.root / 'objects/sha256'
            with directory(prefixes) as root_fd:
                names = sorted(name for name in os.listdir(root_fd) if re.fullmatch('[0-9a-f]{2}', name))
            for prefix in names:
                with directory(prefixes / prefix) as parent:
                    for name in sorted(os.listdir(parent)):
                        label = 'objects/' + prefix + '/' + name
                        if not re.fullmatch('[0-9a-f]{64}', name):
                            report['damaged'].append(label)
                            continue
                        item = dict(sha256=name, size_bytes=0)
                        with self.locked(item):
                            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                            try:
                                item['size_bytes'] = regular(fd).st_size
                                report['objects'] += 1
                                if self.confirm(fd, item):
                                    report['verified'] += 1
                                else:
                                    # The read disproved any receipt covering
                                    # these bytes; it must not survive to bless
                                    # them on a warm path.
                                    self.discard_receipt(item)
                                    report['damaged'].append(label)
                            except (OSError, ValueError):
                                self.discard_receipt(item)
                                report['damaged'].append(label)
                            finally:
                                os.close(fd)
        if 'weights' in include:
            swept = self.sweep_weights()
            report['layouts'] = swept['layouts']
            report['layouts_verified'] = swept['layouts_verified']
            report['damaged'] += swept['damaged']
        report['damaged'] = sorted(set(report['damaged']))
        return report

    def sweep_weights(self):
        """Re-read and re-receipt every published weight-layout generation."""
        result = {'layouts': 0, 'layouts_verified': 0, 'damaged': []}
        directory_path = self.root / 'weights'
        with directory(directory_path) as parent:
            names = sorted(os.listdir(parent))
        for name in names:
            if not re.fullmatch('[0-9a-f]{64}', name):
                result['damaged'].append('weights/' + name)
                continue
            result['layouts'] += 1
            listing = directory_path / name / '.bms-weights.json'
            try:
                # The stored listing is the canonical row document itself: its
                # own bytes must re-derive the generation digest under it.
                rows = json.loads(read_document(listing))
                if weight_layout(rows)[0] != name:
                    raise ValueError('damaged_weight_layout')
                if self.weights(rows, full=True, refresh=True)['state'] != 'ready':
                    raise ValueError('damaged_weight_layout')
            except (OSError, ValueError, RequestBudgetError):
                result['damaged'].append('weights/' + name)
                continue
            result['layouts_verified'] += 1
        return result

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

    def object_receipt_path(self, item):
        digest = item['sha256']
        return self.root / 'receipts/objects' / digest[:2] / (digest + '.json')

    def layout_receipt_path(self, digest):
        return self.root / 'receipts/weights' / (digest + '.json')

    def receipted(self, fd, item):
        """True when a durable receipt already covers exactly these bytes.

        The receipt must be this helper's own generation and describe this
        digest and size, and the object's device, inode, size, mode, mtime and
        ctime must be unchanged since it was written. Anything else - absent,
        unreadable, stale, foreign or mismatched - returns False, and the caller
        reads and verifies the bytes instead.
        """
        identity = helper_identity()
        if identity is None:
            return False
        info = regular(fd)
        document = read_receipt(self.object_receipt_path(item))
        return bool(document is not None
                    and document.get('schema') == RECEIPT_SCHEMA
                    and document.get('verifier') == identity
                    and document.get('sha256') == item['sha256']
                    and document.get('size_bytes') == item['size_bytes']
                    and document.get('object') == signature(info))

    def record_receipt(self, item, info):
        """Publish the durable receipt for bytes that were just verified."""
        identity = helper_identity()
        if identity is None or info.st_size != item['size_bytes']:
            return False
        return write_receipt(self.object_receipt_path(item),
            {'schema': RECEIPT_SCHEMA, 'sha256': item['sha256'], 'size_bytes': item['size_bytes'],
             'verifier': identity, 'verified_ns': time.time_ns(), 'object': signature(info)})

    def discard_receipt(self, item):
        """Drop a receipt for bytes that failed verification.

        A receipt is derived data, never evidence: once a read disproves one it
        must not survive to authorise a warm path. The damaged bytes themselves
        are left exactly as found and reported by the caller.
        """
        try:
            with directory(self.object_receipt_path(item).parent) as parent:
                os.unlink(self.object_receipt_path(item).name, dir_fd=parent)
        except (OSError, ValueError):
            return False
        return True

    def layout_receipt(self, digest):
        """Durable receipt for one published weight-layout generation.

        Returns None unless the document is this helper's own generation and
        describes this exact layout digest; the walk then matches each member's
        name and recorded signature. Anything else re-reads the member bytes.
        """
        identity = helper_identity()
        if identity is None:
            return None
        document = read_receipt(self.layout_receipt_path(digest))
        if (document is None or document.get('schema') != LAYOUT_RECEIPT_SCHEMA
                or document.get('verifier') != identity or document.get('sha256') != digest
                or not isinstance(document.get('members'), dict)):
            return None
        return document

    def record_layout_receipt(self, digest, members):
        """Publish the durable receipt for a layout whose members were verified."""
        identity = helper_identity()
        if identity is None or not members:
            return False
        return write_receipt(self.layout_receipt_path(digest),
            {'schema': LAYOUT_RECEIPT_SCHEMA, 'sha256': digest, 'verifier': identity,
             'verified_ns': time.time_ns(), 'members': members})

    def confirm(self, fd, item, *, progress=None):
        """Read the bytes once, verify them, and record the receipt for them.

        The single verification site for objects that have no usable receipt.
        A size or digest mismatch returns False and publishes nothing, so a
        damaged object is never silently promoted by a stale receipt.
        """
        before = regular(fd)
        if before.st_size != item['size_bytes']:
            return False
        if not verified(fd, item, progress or (lambda **kw: None)):
            return False
        after = regular(fd)
        if signature(before) != signature(after):
            return False
        self.record_receipt(item, after)
        return True

    def state(self, parent, item):
        try:
            fd = os.open(item['sha256'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except FileNotFoundError:
            return 'missing'
        except OSError:
            return 'corrupt'
        try:
            if self.receipted(fd, item):
                return 'cache_hit'
            return 'cache_hit' if self.confirm(fd, item) else 'corrupt'
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
                # The publisher hashed the bytes it wrote; record that verified
                # publication so warm paths do not re-read them.
                self.record_receipt(item, os.stat(item['sha256'], dir_fd=parent, follow_symlinks=False))
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
                # The archive bytes are the verified publication, so a receipt
                # covers them here exactly as it does on the other warm paths.
                if not self.receipted(source.fileno(), item) and not self.confirm(source.fileno(), item):
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
                # A receipt covers a publication that was already verified; the
                # copy still hashes every byte it writes, so the destination is
                # never published unverified.
                if not self.receipted(fd, item) and not self.confirm(
                        fd, item, progress=lambda **kw: self.emit(item, **kw)):
                    raise ValueError('corrupt_object')
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
    elif action == 'materialize_many':
        result = {'artifacts': [cache.materialize_entry(row, request['destination_root']) for row in request['entries']]}
    elif action == 'materialize':
        result = cache.materialize(request['artifact'], request['destination'], request['destination_root'], request.get('mode', 0o644))
    elif action == 'sweep':
        # Explicit integrity sweep of the published store: re-reads bytes and
        # re-derives receipts instead of trusting them. Operator action only;
        # the controller never sends it.
        include = request.get('include') or ['objects']
        if (not isinstance(include, list) or not all(isinstance(name, str) for name in include)
                or set(include) - {'objects', 'weights'}):
            raise ValueError('invalid_sweep_request')
        result = cache.sweep(tuple(include))
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
