#!/usr/bin/env python3
"""One-time controller publication adoption from a retained verified preview.

Run as the installation owner, with its normal paths/archive configuration.
No worker, broker or asset-body reads. The supplied preview is publication input,
not an untrusted API request. It must describe the verified installed selection.
Missing/old sidecars retain legacy planning; this is never run by preview/start.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'platform/api'))


def adopt(preview: dict, manifests: list[dict]) -> Path:
    from paths import get_container_dir
    from services.remote_execution import hf_assets
    from services.remote_execution.cache import _independent_dependencies
    from services.remote_execution.contracts import ProvisionSelection, WorkflowPackSelection
    from services.remote_execution.images import image_reference
    from tools import bms_artifact_cache, bms_managed_runtime

    selection = (WorkflowPackSelection if preview['selection']['kind'] == 'workflow_pack'
                 else ProvisionSelection).model_validate(preview['selection'])
    if selection.kind == 'workflow':
        raise ValueError('Adopt named registry roots using a model/image/workflow-pack preview')
    expected_dependencies = sorted({('containers/' if r.kind == 'image' else 'weights/') + r.relative_path
                                   for r in _independent_dependencies(selection)})
    if expected_dependencies != sorted({r['name'] for r in preview['dependencies']}):
        raise ValueError('Retained preview dependency selection differs from registry')
    archive = hf_assets.weights_archive()
    path = hf_assets.publication_index_path()
    if archive is None or path is None:
        raise ValueError('Configure the existing published weight archive before adoption')
    digest, size = archive
    images = get_container_dir().resolve()
    candidates = [m for m in manifests if 'critical' not in m and
        {(r['name'], r['sha256'], r['size_bytes']) for r in m['artifacts']} ==
        {(r['name'], r['sha256'], r['size_bytes']) for r in preview['artifacts']}]
    if len(candidates) != 1:
        raise ValueError('Retained manifest must exactly match the verified preview')
    manifest = candidates[0]
    bms_managed_runtime.validate_manifest(manifest, bms_artifact_cache)
    rows = [dict(r) for r in manifest['artifacts']]
    if len(rows) != len(preview['artifacts']):
        raise ValueError('Duplicate or missing retained publication rows')
    for row in rows:
        name = row['name']
        if not any(name == root or name.startswith(root + '/') for root in expected_dependencies):
            raise ValueError('Retained artifact is outside declared selection')
        if name.startswith('containers/'):
            source, approved = image_reference(name.removeprefix('containers/'), images)
            if approved is not None and approved != row['sha256']:
                raise ValueError('Retained image differs from installation-approved reference')
            row['source'] = str(source.resolve())
    for root in expected_dependencies:
        if not any(r['name'] == root or r['name'].startswith(root + '/') for r in rows):
            raise ValueError('Retained preview does not cover a declared dependency')
    # Preserve old advisory fields and prior independently adopted named scopes.
    index = {}
    if path.exists():
        with os.fdopen(hf_assets._open_regular(path), 'rb') as stream:
            index = json.load(stream)
        if index.get('archive') != dict(sha256=digest, size_bytes=size):
            raise ValueError('Existing archive sidecar identity differs')
    named = index.get('schema') == hf_assets.NAMED_INDEX_SCHEMA
    previous = {r['name']: r for r in index.get('artifacts', [])} if named else {}
    # Replacing a dependency's publication removes stale former members.
    previous = {name: row for name, row in previous.items() if not any(
        name == root or name.startswith(root + '/') for root in expected_dependencies)}
    previous.update({r['name']: r for r in rows})
    index.update(schema=hf_assets.NAMED_INDEX_SCHEMA, archive=dict(sha256=digest, size_bytes=size),
        dependencies=sorted(set(index.get('dependencies', []) if named else []) | set(expected_dependencies)),
        artifacts=[previous[name] for name in sorted(previous)])
    index.setdefault('digest_sizes', {}).update({r['sha256']: r['size_bytes'] for r in rows
                                              if r['name'].startswith('weights/') and 'target' not in r})
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Pin the publication directory through no-follow traversal. Atomic replacement
    # is only within this owner-controlled directory; no asset bytes are rewritten.
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_fd
        info = os.fstat(directory)
        if info.st_uid != os.getuid():
            raise ValueError('Archive publication directory belongs to another owner')
        # Adoption is the explicit publication write: secure its existing
        # directory here, rather than making legacy metadata modes a new gate.
        os.fchmod(directory, info.st_mode & 0o777 & ~0o022)
        temporary = '.index-' + os.urandom(12).hex()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(index, stream, sort_keys=True, separators=(',', ':'))
                stream.flush()
                os.fchmod(stream.fileno(), 0o444)
                os.fsync(stream.fileno())
            os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    finally:
        os.close(directory)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verified-preview', type=Path, required=True)
    parser.add_argument('--verified-manifests', type=Path, required=True)
    args = parser.parse_args()
    path = adopt(json.loads(args.verified_preview.read_bytes()),
                 json.loads(args.verified_manifests.read_bytes()))
    print(path)


if __name__ == '__main__':
    main()
