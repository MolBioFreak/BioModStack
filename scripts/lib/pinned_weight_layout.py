"""Member-pinned directory publication, not extraction or runtime activation.

Trusted registry entries bind the layout to the existing weights dependency.
No archives are interpreted. Only explicitly named, individually pinned regular
files reference the shared acquisition store through declared immutable aliases. Store must be service-owned.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from .pinned_acquisition import AcquisitionError, Artifact, acquire
from .shared_runtime_images import (
    _directory, _file, _hash, _check_file, _check_directory, _lock, _same,
    _identity, image_identity,
)


def validate_members(dependency, entries, *, test_only=False):
    if (set(dependency) != {'kind', 'relative_path'} or dependency['kind'] != 'weights'
            or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', dependency['relative_path'])):
        raise AcquisitionError('invalid weights dependency binding')
    if not entries:
        raise AcquisitionError('empty weights layout')
    paths, ids = set(), set()
    for entry in entries:
        if set(entry) != {'member_path', 'manifest'}:
            raise AcquisitionError('invalid weight member entry')
        name = entry['member_path']
        if (not isinstance(name, str) or not name or
                any(not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', p)
                    for p in name.split('/'))):
            raise AcquisitionError('unsafe weight member path')
        artifact = Artifact(**entry['manifest'])
        artifact.validate(test_only=test_only)
        if artifact.kind != 'weights' or name in paths or artifact.artifact_id in ids:
            raise AcquisitionError('duplicate member or invalid weight kind')
        paths.add(name)
        ids.add(artifact.artifact_id)
    if any(str(parent) in paths for name in paths for parent in Path(name).parents
           if str(parent) != '.'):
        raise AcquisitionError('member file/directory collision')
    return paths


def _verify_tree(root, entries, *, frozen, weights_root=None, observed_files=None):
    root = Path(root)
    weight_store = Path(weights_root) if weights_root is not None else root.parents[2]
    expected = {e['member_path']: e['manifest'] for e in entries}
    directories = {str(p) for name in expected for p in Path(name).parents if str(p) != '.'}
    observed = {}

    def visit(path, prefix=''):
        with _directory(path) as parent:
            if frozen and stat.S_IMODE(os.fstat(parent).st_mode) != 0o500:
                raise AcquisitionError('layout directory is not readonly')
            for name in os.listdir(parent):
                relative = prefix + name
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode) and relative in directories:
                    visit(path / name, relative + '/')
                elif (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)) and relative in expected:
                    manifest = expected[relative]
                    member = path / name
                    if stat.S_ISLNK(info.st_mode):
                        # Only this manifest's existing acquisition object is an alias authority.
                        concrete = weight_store / 'objects' / 'sha256' / manifest['sha256'] / 'runtime.sif'
                        if member.resolve(strict=True) != concrete:
                            raise AcquisitionError('layout alias escapes selected acquisition object')
                        member = concrete
                    with _file(member) as (fd, owner, before):
                        if before.st_nlink != 1 or ((frozen or stat.S_ISLNK(info.st_mode)) and stat.S_IMODE(before.st_mode) != 0o400):
                            raise AcquisitionError('invalid layout member identity/mode')
                        stamp = _identity(before)
                        digest = (observed_files or {}).get(stamp)
                        if digest is None:
                            digest = _hash(fd)
                        if observed_files is not None:
                            observed_files[stamp] = digest
                        _check_file(member, fd, owner, before)
                        if stat.S_ISLNK(info.st_mode) and not _same(info, os.stat(name, dir_fd=parent, follow_symlinks=False)):
                            raise AcquisitionError('layout alias changed during verification')
                        if (digest, before.st_size) != (manifest['sha256'], manifest['size_bytes']):
                            raise AcquisitionError('layout member SHA-256/size mismatch')
                        observed[relative] = {'sha256': digest, 'size': before.st_size,
                                              'device': before.st_dev, 'inode': before.st_ino}
                else:
                    raise AcquisitionError('unexpected layout member or symlink')
            _check_directory(path, parent)
    visit(root)
    if set(observed) != set(expected):
        raise AcquisitionError('missing expected layout members')
    return observed


def materialize_weights(dependency, entries, store_root, *, accepted_licenses=(),
                        test_only=False, attempts=3, timeout=30, total_timeout=300, weights_root=None,
                        acquired=None):
    """Return immutable generation path; caller owns activation/transaction binding.

    entries = [{member_path: relative POSIX path, manifest: Artifact kwargs}].
    Validate all licenses before IO. Completed acquisitions and immutable aliases
    survive interruption; old uncommitted copy temps are discarded, never promoted.
    Corrupt completed members/publications block instead of being repaired.
    acquired accepts only receipts returned by this invocation's acquisition,
    never saved resume evidence. Its generations are rechecked without rereading
    bodies; standalone/resumed layout calls still authenticate the full bytes.
    """
    dependency, entries = copy.deepcopy((dependency, entries))
    validate_members(dependency, entries, test_only=test_only)
    licenses = frozenset(accepted_licenses)
    if any(e['manifest']['license_id'] not in licenses for e in entries):
        raise AcquisitionError('license acceptance required for all weight members')
    entries = sorted(entries, key=lambda e: e['member_path'])
    digest = hashlib.sha256(json.dumps({'dependency': dependency, 'members': entries},
                            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    root = Path(store_root).absolute()
    if test_only:
        root /= 'test-fixtures-not-scientific-assets'
    weight_store = Path(weights_root).absolute() if weights_root is not None else root / 'weights'
    if test_only and weights_root is not None:
        weight_store /= 'test-fixtures-not-scientific-assets'
    layouts = weight_store / 'layouts' / dependency['relative_path']
    destination = layouts / digest
    stage = layouts / ('.staging-' + digest)
    # Invocation-local observations only; explicit resume/audit still hashes.
    observed_files = {}
    acquired = acquired or {}
    for entry in entries:
        artifact = Artifact(**entry['manifest'])
        receipt = acquired.get(artifact.manifest_digest)
        if receipt is not None:
            concrete = weight_store / 'objects' / 'sha256' / artifact.sha256 / 'runtime.sif'
            if (receipt['path'] != str(concrete) or receipt['test_only'] != test_only
                    or image_identity(concrete, artifact.sha256) != receipt['verification']
                    or receipt['verification']['size'] != artifact.size_bytes):
                raise AcquisitionError('acquired member identity changed')
            with _file(concrete) as (fd, parent, before):
                observation = receipt['verification']
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        observation['device'], observation['inode'], observation['size'],
                        observation['mtime_ns'], observation['ctime_ns']):
                    raise AcquisitionError('acquired member identity changed')
                observed_files[_identity(before)] = artifact.sha256
                _check_file(concrete, fd, parent, before)
    with _lock(layouts, digest):
        if not os.path.lexists(destination):
            with _directory(stage, create=True):
                pass
            for entry in entries:
                artifact = Artifact(**entry['manifest'])
                receipt = acquired.get(artifact.manifest_digest) or acquire(artifact, store_root, accepted_licenses=licenses,
                                  test_only=test_only, attempts=attempts, timeout=timeout,
                                  total_timeout=total_timeout, weights_root=weights_root)
                target = stage / entry['member_path']
                with _directory(target.parent, create=True) as parent:
                    temp = '.' + target.name + '.copy'
                    try:
                        info = os.stat(temp, dir_fd=parent, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise AcquisitionError('unsafe interrupted member copy')
                        os.unlink(temp, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                    if os.path.lexists(target):
                        # Full tree verification below rejects corrupt completed files.
                        continue
                    # Native names are directory-only views, never another payload body.
                    # stage and destination have identical depth, so this relative alias
                    # remains valid after the atomic generation rename.
                    os.symlink(os.path.relpath(receipt['path'], target.parent),
                               target.name, dir_fd=parent)
                    _check_directory(target.parent, parent)
                    os.fsync(parent)
            _verify_tree(stage, entries, frozen=False, weights_root=weight_store, observed_files=observed_files)
            # Freeze children first; reruns can reuse a fully prepared frozen stage.
            dirs = {stage} | {stage / p for e in entries for p in Path(e['member_path']).parents
                              if str(p) != '.'}
            for directory in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
                with _directory(directory) as fd:
                    os.fchmod(fd, 0o500)
                    os.fsync(fd)
            _verify_tree(stage, entries, frozen=True, weights_root=weight_store, observed_files=observed_files)
            with _directory(layouts) as parent:
                os.rename(stage.name, destination.name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
        verification = _verify_tree(destination, entries, frozen=True, weights_root=weight_store, observed_files=observed_files)
        return {'dependency': dependency, 'path': str(destination),
                'layout_digest': digest, 'members': verification,
                'qualification': 'not_checked', 'test_only': test_only}
