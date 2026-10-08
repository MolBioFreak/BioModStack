"""Host projection of installed critical bytes into the existing managed actor.

Uses the existing bootstrap/native capability policy, not worker-version equality.
Installed artifact bytes remain hash-pinned. Support relocation delegates to the
bundle's existing authority; acquisition and activation keep their existing owners.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from tools.bms_managed_runtime import CRITICAL_REQUIREMENTS


class CriticalRuntimeBlocked(ValueError):
    pass


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _leaves(root):
    """Enumerate internal links as regular leaves, rejecting cycles/escapes."""
    root = root.resolve()
    def walk(path, relative, ancestors):
        real = path.resolve(strict=True)
        if not real.is_relative_to(root) or real in ancestors:
            raise CriticalRuntimeBlocked('Support Python link escapes or cycles')
        if real.is_dir():
            for child in sorted(real.iterdir()):
                if relative == 'venv' and child.name == '.venv':
                    continue  # Same exclusion as the existing relocation authority.
                yield from walk(child, '/'.join(filter(None, (relative, child.name))), ancestors | {real})
        elif real.is_file():
            yield relative, real
        else:
            raise CriticalRuntimeBlocked('Support Python contains nonregular members')
    yield from walk(root, '', set())


def project_runtime(remote_root, staging_root):
    """Return (manifest, existing CacheTransferArtifact tuple), without networking.

    Compatibility is the versioned existing native capability policy. It is not
    browser input and does not require a separate operator-supplied profile.
    """
    requirements = dict(CRITICAL_REQUIREMENTS)
    from services.nextflow import resolve_nextflow_executable, resolve_nextflow_version
    from .bundle import (CacheTransferArtifact, _relocate_python_runtime,
                         current_source_identity, get_code_root, get_data_root)
    version = resolve_nextflow_version()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise CriticalRuntimeBlocked('Pinned Nextflow version is invalid')
    launcher = Path(resolve_nextflow_executable()).resolve(strict=True)
    jar_name = f'nextflow/home/framework/{version}/nextflow-{version}-one.jar'
    nxf_home = Path(os.environ.get('NXF_HOME') or Path.home() / '.nextflow').expanduser()
    jar = nxf_home / 'framework' / version / f'nextflow-{version}-one.jar'
    if not jar.is_file():
        raise CriticalRuntimeBlocked('Pinned Nextflow framework JAR is unavailable')
    support = (Path(os.environ.get('BMS_CM_API_RUNTIME_DIR') or
                    get_data_root() / 'runtime/cm-api-python') / 'current').resolve(strict=True)
    runner = Path(__file__).resolve().parents[2] / 'tools/bms_remote_worker.py'
    sources = [('runner/bms_remote_worker.py', runner), ('nextflow/nextflow', launcher), (jar_name, jar)]
    support_leaves = list(_leaves(support))
    source = current_source_identity(get_code_root().resolve())
    identity = dict(source=source, remote_root=remote_root, requirements=requirements, version=version,
                    members=[(n, _digest(p), p.stat().st_size, p.stat().st_mode & 0o777)
                             for n, p in sources + [('support-python/' + n, p) for n, p in support_leaves]])
    installation_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    generation = f'{remote_root}/managed-assets/v1/releases/{installation_id}'
    staging_root = Path(staging_root)
    relocated = _relocate_python_runtime(support, staging_root / 'relocated', generation + '/support-python')
    # Managed helper's ordinary files use the SAME shared cache. Dereferencing
    # only validated internal links avoids a second symlink/installer protocol.
    for name, path in _leaves(relocated):
        destination = staging_root / 'support-python' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        destination.chmod(path.stat().st_mode & 0o777)
        sources.append(('support-python/' + name, destination))
    rows, transfers = [], []
    for name, path in sources:
        row = dict(name=name, sha256=_digest(path), size_bytes=path.stat().st_size,
                   mode=path.stat().st_mode & 0o777)
        rows.append(row)
        transfers.append(CacheTransferArtifact(source=path, remote_destination=name,
                         sha256=str(row['sha256']), size_bytes=int(row['size_bytes']), mode=int(row['mode']), role='runtime'))
    manifest = dict(selection=dict(kind='critical_runtime', model_id='worker'),
                    source_revision=source[0], source_tree=source[1], artifacts=rows,
                    critical=dict(schema='bms.critical-runtime.v1', installation_id=installation_id,
                        nextflow_version=version, requirements=dict(requirements),
                        entrypoints=dict(runner='runner/bms_remote_worker.py', nextflow='nextflow/nextflow',
                                         jar=jar_name, python='support-python/venv/bin/python')))
    return manifest, tuple(transfers)


def runtime_binding(remote_root, manifest):
    critical = manifest['critical']
    generation = f"{remote_root}/managed-assets/v1/releases/{critical['installation_id']}"
    paths = {key: generation + '/' + value for key, value in critical['entrypoints'].items()}
    rows = {r['name']: r for r in manifest['artifacts']}
    hashes = {key: rows[value]['sha256'] for key, value in critical['entrypoints'].items()}
    from .managed_inventory import release_digest
    return dict(paths=paths, sha256=hashes, release_sha256=release_digest(manifest),
                environment=dict(NXF_OFFLINE='true', NXF_VER=critical['nextflow_version'],
                NXF_HOME=generation + '/nextflow/home', PYTHONNOUSERSITE='1'))
