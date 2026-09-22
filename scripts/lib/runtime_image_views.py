"""Derived rootfs and CoW-only execution views under the shared image authority.

Trusted extractors/engines only: this is source preservation, not a sandbox.
No SIF copies, writable shared hardlinks, byte-copy fallback or second ref store.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import stat
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from .shared_runtime_images import (
    SharedRuntimeImageError as Error, _absolute, _digest, _directory, _file,
    _check_file, _check_directory, _hash, _same, _identity, _member,
    _DIRECTORY_FLAGS, _FILE_FLAGS, verify_image,
)
from . import runtime_image_lifecycle as lifecycle


def parallel_workers():
    """Use 80% of this host's cores for the whole-image passes.

    Inventorying and hashing an extracted rootfs is a per-file pass over tens of
    thousands of entries and gigabytes of content; leaving it on one core makes
    every execution wait minutes for a machine that is otherwise idle. Extracting
    an image uses the same share.
    """
    return max(1, int((os.cpu_count() or 1) * 0.8))

FICLONE = 0x40049409


class CoWUnavailable(Error):
    """The selected source/workspace filesystem cannot provide reflinks."""


def derived_path(root, digest):
    return lifecycle.object_path(root, digest).parent.parent / (".rootfs-" + _digest(digest))


def _remove(parent, name):
    """Remove only a caller-owned tree, never traverse a symlink."""
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if stat.S_ISDIR(info.st_mode):
        # Engines may leave private directories mode000. Restore traversal
        # without following a substituted symlink, then pin the directory.
        os.chmod(name, 0o700, dir_fd=parent, follow_symlinks=False)
        fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        try:
            if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (info.st_dev, info.st_ino):
                raise Error('cleanup directory replaced')
            os.fchmod(fd, 0o700)
            for child in os.listdir(fd):
                _remove(fd, child)
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def _walk_tree(path):
    """Walk once using held no-follow ancestry; reject replacement/mutation.

    Yielded parent FDs are borrowed only until the next iteration. No regular
    bytes are read. A write-and-restore still changes ctime and fails recheck.
    """
    path = _absolute(path)
    def walk(parent, name, relative):
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        kind = stat.S_IFMT(before.st_mode)
        if kind not in {stat.S_IFDIR, stat.S_IFREG, stat.S_IFLNK}:
            raise Error('unsupported input file type')
        if kind == stat.S_IFDIR:
            fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
            try:
                if not _same(before, os.fstat(fd)):
                    raise Error('tree directory changed')
                yield parent, name, relative, before
                for child in sorted(os.listdir(fd)):
                    yield from walk(fd, child, relative + '/' + child)
                if not _same(before, os.fstat(fd)):
                    raise Error('tree directory membership changed')
            finally:
                os.close(fd)
        else:
            yield parent, name, relative, before
        if not _same(before, os.stat(name, dir_fd=parent, follow_symlinks=False)):
            raise Error('tree member changed')
    with _directory(path.parent) as parent:
        yield from walk(parent, path.name, '.')
        _check_directory(path.parent, parent)


def snapshot_tree(path):
    return {relative: _identity(info) + (
                os.readlink(name, dir_fd=parent) if stat.S_ISLNK(info.st_mode) else None,)
            for parent, name, relative, info in _walk_tree(path)}


def _inventory(path, *, freeze=False, identities=None):
    """No-follow content/mode inventory; optionally freeze extracted inodes.

    Original modes are retained separately and restored only on private views.
    Hardlinks are allowed only when every link is contained in this rootfs.
    """
    entries, links, original_modes, hashes = {}, {}, {}, {}
    stamps, pending = {}, {}

    def record(relative, info, target=None):
        if identities is not None:
            identities['.' if relative == '.' else './' + relative] = _identity(info) + (target,)

    def readable(parent, name, bits):
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        key = (info.st_dev, info.st_ino)
        original_modes.setdefault(key, stat.S_IMODE(info.st_mode))
        if freeze and stat.S_IMODE(info.st_mode) & bits != bits:
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise Error('unreadable extracted hardlink requires extractor normalization')
            os.chmod(name, stat.S_IMODE(info.st_mode) | bits,
                     dir_fd=parent, follow_symlinks=False)
        return original_modes[key]

    def walk(parent, name, prefix):
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise Error('derived rootfs directory is not a directory')
        mode = readable(parent, name, 0o500)
        fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
                raise Error('derived rootfs directory changed')
            record(prefix, before)
            names = sorted(os.listdir(fd))
            entries[prefix] = {"kind": "directory", "mode": mode}
            for child in names:
                rel = child if prefix == "." else prefix + "/" + child
                info = os.stat(child, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    walk(fd, child, rel)
                elif stat.S_ISLNK(info.st_mode):
                    target = os.readlink(child, dir_fd=fd)
                    entries[rel] = {"kind": "symlink", "target": target}
                    record(rel, info, target)
                    if not _same(info, os.stat(child, dir_fd=fd, follow_symlinks=False)):
                        raise Error("derived symlink changed")
                elif stat.S_ISREG(info.st_mode):
                    mode = readable(fd, child, 0o400)
                    with _member(fd, child) as (source, observed):
                        stamp = _identity(observed)
                        row = {"kind": "file", "mode": mode, "size": observed.st_size}
                        record(rel, observed)
                        key = (observed.st_dev, observed.st_ino)
                        links.setdefault(key, []).append((rel, observed.st_nlink))
                        entries[rel] = row
                        stamps[rel] = stamp
                        pending.setdefault(stamp, rel)
                else:
                    raise Error("unsupported special entry in extracted rootfs: " + rel)
            if not _same(before, os.fstat(fd)) or not _same(
                    before, os.stat(name, dir_fd=parent, follow_symlinks=False)):
                raise Error("derived directory membership changed")
        finally:
            os.close(fd)
    with _directory(path.parent) as parent:
        walk(parent, path.name, '.')
        _check_directory(path.parent, parent)
    if pending:
        # One hash per unique inode, in parallel, each re-checked against the
        # identity observed during the walk so a swap cannot be hashed as if it
        # were the frozen member.
        def digest_member(item):
            stamp, rel = item
            fd = os.open(path / rel, _FILE_FLAGS)
            try:
                before = os.fstat(fd)
                if _identity(before) != stamp:
                    raise Error('derived rootfs member changed before hashing')
                digest = _hash(fd)
                if not _same(before, os.fstat(fd)):
                    raise Error('derived rootfs member changed while hashing')
                return stamp, digest
            finally:
                os.close(fd)

        with ThreadPoolExecutor(max_workers=parallel_workers()) as pool:
            for stamp, digest in pool.map(digest_member, sorted(pending.items())):
                hashes[stamp] = digest
    for rel, stamp in stamps.items():
        entries[rel]['sha256'] = hashes[stamp]
    for group in links.values():
        if any(count != len(group) for _, count in group):
            raise Error("derived rootfs has an external hardlink")
        for rel, _ in group[1:]:
            entries[rel]["hardlink"] = group[0][0]
    if freeze:
        for rel, row in sorted(entries.items(), reverse=True):
            target = path if rel == "." else path / rel
            if row["kind"] == "file":
                with _file(target) as (fd, _, _):
                    os.fchmod(fd, (row["mode"] & ~0o222) | 0o400)
                    os.fsync(fd)
            elif row["kind"] == "directory":
                with _directory(target) as fd:
                    os.fchmod(fd, (row["mode"] & ~0o222) | 0o500)
                    os.fsync(fd)
    return entries


def verify_derivation(path, identity, *, verification=None):
    try:
        return _verify_derivation(path, identity, verification=verification)
    except (ValueError, TypeError, KeyError) as exc:
        raise Error('invalid derived rootfs metadata') from exc


def _verify_derivation(path, identity, *, verification=None):
    with _directory(path) as fd:
        envelope = _identity(os.fstat(fd))
        if stat.S_IMODE(os.fstat(fd).st_mode) != 0o500 or set(os.listdir(fd)) != {"rootfs", "manifest.json"}:
            raise Error("invalid derived rootfs envelope")
        with _file(path / "manifest.json") as (meta, parent, before):
            if stat.S_IMODE(before.st_mode) != 0o400 or before.st_nlink != 1:
                raise Error("invalid derived rootfs metadata")
            if verification:
                if (verification['path'] != str(path) or verification['source'] != identity
                        or verification['envelope'] != envelope
                        or verification['metadata'] != _identity(before)
                        or snapshot_tree(path / 'rootfs') != verification['tree']):
                    raise Error('derived rootfs integrity changed')
                _check_file(path / 'manifest.json', meta, parent, before)
                _check_directory(path, fd)
                if envelope != _identity(os.fstat(fd)):
                    raise Error('derived rootfs envelope changed')
                return verification['receipt'], verification['manifest']
            metadata_hash = _hash(meta)
            os.lseek(meta, 0, os.SEEK_SET)
            with os.fdopen(os.dup(meta)) as stream:
                manifest = json.load(stream, object_pairs_hook=lifecycle._unique_keys)
            _check_file(path / "manifest.json", meta, parent, before)
        if set(manifest) != {"schema_version", "source", "original", "frozen"} or manifest["schema_version"] != 1 or manifest["source"] != identity:
            raise Error("derived rootfs source identity mismatch")
        tree = {} if verification is not None else None
        if _inventory(path / "rootfs", identities=tree) != manifest["frozen"]:
            raise Error("derived rootfs integrity mismatch")
        # Original modes are the only differences permitted in the restoration map.
        original = manifest["original"]
        expected = {name: dict(row) for name, row in original.items()}
        for row in expected.values():
            if row["kind"] in {"file", "directory"}:
                row["mode"] = (row["mode"] & ~0o222) | (0o400 if row["kind"] == "file" else 0o500)
        if expected != manifest["frozen"]:
            raise Error("invalid derived mode restoration metadata")
        _check_directory(path, fd)
        if envelope != _identity(os.fstat(fd)):
            raise Error('derived rootfs envelope changed')
        receipt = {"metadata_sha256": metadata_hash, "device": os.fstat(fd).st_dev,
                   "inode": os.fstat(fd).st_ino}
        if verification is not None:
            verification.update(path=str(path), source=identity, envelope=envelope,
                                metadata=_identity(before), tree=tree,
                                receipt=receipt, manifest=manifest)
        return receipt, manifest


def _recover_stages(root, digest):
    objects = derived_path(root, digest).parent
    prefix = ".derive-" + digest + "-"
    with _directory(objects) as fd:
        for name in os.listdir(fd):
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix):]
            if len(suffix) != 32 or any(c not in "0123456789abcdef" for c in suffix):
                raise Error("unknown derivation staging name")
            with _directory(objects / name) as stage:
                if set(os.listdir(stage)) - {"rootfs", "manifest.json"}:
                    raise Error("unknown derivation staging contents")
            _remove(fd, name)
            os.fsync(fd)


def _derive(root, digest, image_fd, identity, extract, *, verification=None):
    path = derived_path(root, digest)
    _recover_stages(root, digest)
    with _directory(path.parent) as parent:
        if path.name in os.listdir(parent):
            return path, verify_derivation(path, identity, verification=verification)[1]
        name = ".derive-" + digest + "-" + uuid.uuid4().hex
        os.mkdir(name, 0o700, dir_fd=parent)
        stage = path.parent / name
        try:
            os.lseek(image_fd, 0, os.SEEK_SET)
            extract(image_fd, stage / "rootfs")
            original = _inventory(stage / "rootfs", freeze=True)
            frozen = _inventory(stage / "rootfs")
            if verify_image(lifecycle.object_path(root, digest), digest) != identity:
                raise Error("source image changed during extraction")
            manifest = {"schema_version": 1, "source": identity, "original": original, "frozen": frozen}
            lifecycle.atomic_write(stage / "manifest.json", json.dumps(manifest, sort_keys=True))
            with _file(stage / "manifest.json") as (fd, _, _):
                os.fchmod(fd, 0o400)
                os.fsync(fd)
            with _directory(stage) as fd:
                os.fchmod(fd, 0o500)
                os.fsync(fd)
            verify_derivation(stage, identity)
            _check_directory(path.parent, parent)
            if path.name in os.listdir(parent):
                raise Error("derivation appeared during publication")
            os.rename(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            if name in os.listdir(parent):
                _remove(parent, name)
        if verification is not None:
            verify_derivation(path, identity, verification=verification)
        return path, manifest


def _clone(source, destination, manifest):
    rows = manifest["original"]
    destination.mkdir(mode=0o700)
    for parent, name, relative, info in _walk_tree(source):
        if relative == '.':
            continue
        rel = relative[2:]
        row = rows.get(rel)
        if row is None:
            raise Error('derived rootfs membership changed during cloning')
        target = destination / rel
        if row["kind"] == "directory":
            target.mkdir(mode=0o700)
        elif row["kind"] == "symlink":
            target.symlink_to(row["target"])
        elif "hardlink" not in row:
            with _member(parent, name) as (src, before):
                if not _same(info, before):
                    raise Error('derived rootfs changed during cloning')
                out = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                try:
                    try:
                        fcntl.ioctl(out, FICLONE, src)
                    except OSError as exc:
                        if exc.errno in {errno.EXDEV, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL, errno.ENOSYS}:
                            raise CoWUnavailable("private image views require FICLONE; no byte-copy fallback") from exc
                        raise
                finally:
                    os.close(out)
    for rel, row in rows.items():
        if "hardlink" in row:
            os.link(destination / row["hardlink"], destination / rel, follow_symlinks=False)
    for rel, row in sorted(rows.items(), reverse=True):
        if row["kind"] != "symlink":
            os.chmod(destination if rel == "." else destination / rel, row["mode"], follow_symlinks=False)


@contextmanager
def private_image_view(store_root, digest, workspace_root, extract):
    """Yield {rootfs, image, image_fd, identity}; retain source FD and lease.

    The callback creates destination and must reap its children before returning.
    The engine must likewise exit/reap before leaving this context. SIGKILL leaves
    the ordinary durable lease pinned for explicit recovery, never time-based GC.
    """
    root, digest, workspace = _absolute(store_root), _digest(digest), _absolute(workspace_root)
    if workspace == root or workspace.is_relative_to(root):
        raise Error("private execution workspace must be outside the image store")
    image = lifecycle.object_path(root, digest)
    owner = "private-image-view:" + uuid.uuid4().hex
    token, identities = lifecycle.acquire_lease(root, [digest], owner=owner)
    identity = identities[digest]
    try:
        with _file(image) as (image_fd, image_parent, before):
            def check_source():
                _check_file(image, image_fd, image_parent, before)
                # acquire_lease already hashed this exact immutable generation.
                # ctime catches writes even if bytes/mtime are restored. Never
                # reuse this observation across launches or persist a new cache.
                if (stat.S_IMODE(before.st_mode) != 0o400 or before.st_nlink != 1
                        or stat.S_IMODE(os.fstat(image_parent).st_mode) != 0o500
                        or (before.st_dev, before.st_ino, before.st_size,
                            before.st_mtime_ns, before.st_ctime_ns) !=
                           (identity['device'], identity['inode'], identity['size'],
                            identity['mtime_ns'], identity['ctime_ns'])):
                    raise Error("executing image identity changed")
            check_source()
            verification = {}
            with lifecycle.transaction(root):
                derived, manifest = _derive(root, digest, image_fd, identity, extract,
                                            verification=verification)
            with _directory(workspace, create=True) as parent:
                name = ".image-view-" + uuid.uuid4().hex
                os.mkdir(name, 0o700, dir_fd=parent)
                private = workspace / name / "rootfs"
                try:
                    _clone(derived / "rootfs", private, manifest)
                    verify_derivation(derived, identity, verification=verification)
                    check_source()
                    _check_directory(workspace, parent)
                    os.lseek(image_fd, 0, os.SEEK_SET)
                    try:
                        yield {"rootfs": private, "image": image, "image_fd": image_fd, "identity": identity}
                    finally:
                        check_source()
                        verify_derivation(derived, identity, verification=verification)
                finally:
                    _remove(parent, name)
    finally:
        lifecycle.release_lease(root, token, owner=owner)
