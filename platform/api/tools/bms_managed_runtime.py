#!/usr/bin/env python3
"""Managed image/weight activation and readback, not scientific readiness.

Uses the independently hash-installed cache helper's descriptor-relative I/O.
No acquisition URLs, shell commands, model code execution or host callbacks.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import uuid
from typing import Any

MAX_JSON = 8 * 1024 * 1024

# Same capability contract as bootstrap_worker.sh and the native smoke checks.
# Tool/driver version output is evidence, not an exact-match release prerequisite.
CRITICAL_REQUIREMENTS = dict(policy='bms.native-worker.v1', system='Linux', machine='x86_64',
                             java='nextflow-executable', apptainer='cuda-container', driver='cuda-container')


def critical_compatible(requirements, observed):
    return (requirements == CRITICAL_REQUIREMENTS
            and all(observed.get(k) == requirements[k] for k in ('system', 'machine'))
            and all(bool(observed.get(k)) for k in ('java', 'apptainer', 'driver')))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def boot_id():
    return str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))


def release_path(root, manifest):
    # Relocation embeds final paths: bind the pre-relocation identity separately
    # from the digest of final bytes. A collision must never overwrite a manifest.
    return root / 'releases' / (manifest['critical']['installation_id'] if 'critical' in manifest
                                else hashlib.sha256(canonical(manifest)).hexdigest())


def observed_compatibility():
    import platform
    import subprocess
    def version(argv):
        result = subprocess.run(argv, capture_output=True, text=True, check=True, timeout=30)
        return (result.stdout + result.stderr).strip()
    return dict(system=platform.system(), machine=platform.machine(),
                java=version(['java', '-version']), apptainer=version(['apptainer', '--version']),
                driver=version(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader']))


def validate_manifest(value, cache):
    critical = value.get('critical')
    if set(value) != {'selection', 'source_revision', 'source_tree', 'artifacts'} | ({'critical'} if critical is not None else set()):
        raise ValueError('invalid_manifest')
    selection = value['selection']
    if (not isinstance(selection, dict) or set(selection) != {'kind', 'model_id'}
            or selection['kind'] not in ({'critical_runtime'} if critical is not None else {'model', 'image', 'workflow'})
            or not isinstance(selection['model_id'], str)
            or not re.fullmatch('[0-9a-f]{64}' if selection['kind'] == 'workflow' else '[a-z0-9_]{1,64}', selection['model_id'])):
        raise ValueError('invalid_selection')
    for key in ('source_revision', 'source_tree'):
        if not re.fullmatch('[0-9a-f]{40}', value[key]):
            raise ValueError('invalid_source')
    rows = value['artifacts']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100000:
        raise ValueError('invalid_artifacts')
    names = set()
    for row in rows:
        fields = {'name', 'sha256', 'size_bytes', 'mode'}
        if row.get('kind') == 'runtime_image':
            fields.add('kind')
        if set(row) != fields:
            raise ValueError('invalid_artifact')
        cache.artifact(row)
        name = row['name']
        path = PurePosixPath(name)
        if (str(path) != name or path.is_absolute() or '..' in path.parts
                or len(path.parts) < 2 or path.parts[0] not in ({'runner', 'nextflow', 'support-python'} if critical is not None else {'containers', 'weights'})
                or name in names or type(row['mode']) is not int or not 0 <= row['mode'] <= 0o777):
            raise ValueError('invalid_artifact_path')
        if (path.parts[0] == 'containers') != (row.get('kind') == 'runtime_image'):
            raise ValueError('legacy_or_invalid_image_storage')
        names.add(name)
    if any(str(parent) in names for name in names for parent in PurePosixPath(name).parents):
        raise ValueError('conflicting_artifact_paths')
    if critical is not None:
        if (selection != {'kind': 'critical_runtime', 'model_id': 'worker'}
                or set(critical) != {'schema', 'installation_id', 'nextflow_version', 'requirements', 'entrypoints'}
                or critical['schema'] != 'bms.critical-runtime.v1'
                or not re.fullmatch('[0-9a-f]{64}', critical['installation_id'])
                or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', critical['nextflow_version'])):
            raise ValueError('invalid_critical_identity')
        required = critical['requirements']
        if required != CRITICAL_REQUIREMENTS:
            raise ValueError('unsupported_critical_compatibility_policy')
        version = critical['nextflow_version']
        expected = dict(runner='runner/bms_remote_worker.py', nextflow='nextflow/nextflow',
                        jar=f'nextflow/home/framework/{version}/nextflow-{version}-one.jar',
                        python='support-python/venv/bin/python')
        if critical['entrypoints'] != expected or not set(expected.values()) <= names:
            raise ValueError('incomplete_critical_manifest')
        # The complete support release is enumerated by the host, not just venv's
        # interpreter shortcut. Require stdlib and site-packages declarations.
        if not any('/encodings/__init__.py' in n for n in names) or not any('/site-packages/' in n for n in names):
            raise ValueError('incomplete_support_python')
        if any(row['size_bytes'] == 0 for row in rows if row['name'] in expected.values()):
            raise ValueError('empty_critical_entrypoint')
        if any(not row['mode'] & 0o111 for row in rows if row['name'] in {expected['nextflow'], expected['python']}):
            raise ValueError('nonexecutable_critical_entrypoint')
    return hashlib.sha256(canonical(value)).hexdigest()


def read_bytes(path, cache, limit=MAX_JSON):
    with cache.directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        cache.regular(fd)
        if os.fstat(fd).st_size > limit:
            raise ValueError('oversized_record')
        data = bytearray()
        while chunk := os.read(fd, min(1024 * 1024, limit + 1 - len(data))):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('oversized_record')
        return bytes(data)
    finally:
        os.close(fd)


def publish(path, value, cache):
    data = canonical(value)
    with cache.directory(path.parent, create=True) as parent:
        name = '.publish-' + uuid.uuid4().hex
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), 0o400)
                os.fsync(stream.fileno())
            os.replace(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass


def image_storage(root, cache):
    # Reuse Cache's authoritative path/verification methods without its writing
    # constructor: inventory must not create absent cache directories.
    class Reader(cache.Cache):
        def __init__(self):
            self.root = PurePosixPath(root.parent.parent / 'cache/artifacts/v1')
    return Reader()


def retain_managed_image_releases(root, manifest, cache):
    """Pin complete retained generations, including pre-integration manifests.

    Only the existing managed releases directory is read. Partial installations
    without a committed manifest are not retained releases. Distinct releases
    share one verification per digest under the image lifecycle fence.
    """
    manifests = [manifest]
    try:
        with cache.directory(root / 'releases') as parent:
            for name in sorted(os.listdir(parent)):
                if not re.fullmatch('[0-9a-f]{64}', name):
                    raise ValueError('unknown_managed_release')
                try:
                    value = json.loads(read_bytes(root / 'releases' / name / 'manifest.json', cache))
                except FileNotFoundError:
                    continue
                validate_manifest(value, cache)
                if release_path(root, value).name != name:
                    raise ValueError('managed_generation_identity_collision')
                manifests.append(value)
    except FileNotFoundError:
        pass
    selections, sizes = {}, {}
    for value in manifests:
        digests = {r['sha256'] for r in value['artifacts'] if r.get('kind') == 'runtime_image'}
        for row in value['artifacts']:
            if row.get('kind') == 'runtime_image':
                if sizes.setdefault(row['sha256'], row['size_bytes']) != row['size_bytes']:
                    raise ValueError('managed_image_size_conflict')
        if digests:
            selections['managed-release:' + hashlib.sha256(canonical(value)).hexdigest()] = digests
    if not selections:
        return None
    storage = image_storage(root, cache)
    lifecycle = cache.runtime_lifecycle()
    with lifecycle.transaction(storage.image_store) as store:
        state = lifecycle.load_state(store)
        identities = {digest: cache.runtime_images().verify_image(lifecycle.object_path(store, digest), digest)
                      for digest in set().union(*selections.values())}
        if any(identity['size'] != sizes[digest] for digest, identity in identities.items()):
            raise ValueError('runtime_image_size_mismatch')
        current_owner = 'managed-release:' + hashlib.sha256(canonical(manifest)).hexdigest()
        current_reference = None
        for owner, digests in sorted(selections.items()):
            token, pinned = lifecycle._ensure_lease_locked(store, state, digests, owner,
                identities={digest: identities[digest] for digest in digests})
            if owner == current_owner:
                current_reference = dict(store_root=str(store), owner=owner, lease_token=token, identities=pinned)
        return current_reference


def retained_image_reference(root, manifest, cache) -> dict[str, Any] | None:
    """The manifest's durable image set is pinned in the existing lease authority.

    Old managed generations stay retained; activation never releases their lease.
    Observation is read-only and cannot silently repair an unpinned generation.
    """
    digests = {r['sha256'] for r in manifest['artifacts'] if r.get('kind') == 'runtime_image'}
    if not digests:
        return None
    storage = image_storage(root, cache)
    lifecycle = cache.runtime_lifecycle()
    owner = 'managed-release:' + hashlib.sha256(canonical(manifest)).hexdigest()
    state = lifecycle.load_state(storage.image_store)
    matches = [(token, row) for token, row in state['leases'].items() if row['owner'] == owner]
    if len(matches) != 1 or set(matches[0][1]['identities']) != digests:
        raise ValueError('managed_image_reference_missing_or_changed')
    token, row = matches[0]
    identities = row['identities']
    for digest, identity in identities.items():
        if cache.runtime_images().verify_image(lifecycle.object_path(storage.image_store, digest), digest) != identity:
            raise ValueError('managed_image_reference_identity_changed')
    return dict(store_root=str(storage.image_store), owner=owner, lease_token=token, identities=identities)


def observe(root, manifest, cache) -> dict[str, Any]:
    digest = validate_manifest(manifest, cache)
    release = release_path(root, manifest)
    selection = manifest['selection']
    active = root / 'active' / (selection['kind'] + '-' + selection['model_id'] + '.json')
    activated = False
    try:
        marker = {'release_sha256': digest}
        if 'critical' in manifest:
            marker['boot_id'] = boot_id()
        activated = (json.loads(read_bytes(active, cache)) == marker
                     and read_bytes(release / 'manifest.json', cache) == canonical(manifest))
    except (OSError, ValueError):
        pass
    # Reuse this observation's full hash/inode receipt, not a second image scan.
    image_reference = None
    if activated:
        try:
            image_reference = retained_image_reference(root, manifest, cache)
        except (OSError, ValueError, RuntimeError):
            activated = False
    rows = []
    for row in manifest['artifacts']:
        if row.get('kind') == 'runtime_image':
            try:
                if image_reference is None:
                    image_storage(root, cache).verify_runtime(row)
                elif image_reference['identities'][row['sha256']]['size'] != row['size_bytes']:
                    raise ValueError('runtime_image_size_mismatch')
                state = 'verified'
            except FileNotFoundError:
                state = 'missing'
            except (OSError, ValueError, RuntimeError):
                state = 'corrupt'
            rows.append({k: row[k] for k in ('name', 'sha256', 'size_bytes')} | {'state': state})
            continue
        path = release / row['name']
        state = 'missing'
        try:
            with cache.directory(path.parent) as parent:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                state = 'verified' if cache.verified(fd, row) else 'corrupt'
                if state == 'verified' and os.fstat(fd).st_mode & 0o777 != row['mode'] & 0o555:
                    state = 'incompatible'
            finally:
                os.close(fd)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            state = 'corrupt'
        rows.append({k: row[k] for k in ('name', 'sha256', 'size_bytes')} | {'state': state})
    states = {r['state'] for r in rows}
    state = ('corrupt' if 'corrupt' in states else 'incompatible' if 'incompatible' in states
             else 'missing' if states == {'missing'} else 'partial' if 'missing' in states
             else 'verified' if activated else 'unverified')
    compatibility = None
    if 'critical' in manifest:
        try:
            actual = observed_compatibility()
            compatible = critical_compatible(manifest['critical']['requirements'], actual)
        except Exception:
            actual, compatible = {}, False
        compatibility = dict(requirements=manifest['critical']['requirements'], observed=actual,
                             compatible=compatible)
        if not compatible:
            state = 'incompatible'
    return dict(**({'critical': compatibility} if compatibility is not None else {}),
                image_reference=image_reference,
                selection=selection, release_sha256=digest, source_revision=manifest['source_revision'],
                source_tree=manifest['source_tree'], state=state, artifacts=rows)


# Headroom is an admission floor, NOT allocated space or a reservation. Other
# writers, quotas, thin provisioning and filesystem metadata can still cause ENOSPC.
MIN_HEADROOM = 1024 * 1024 * 1024


@contextmanager
def admission_lock(root, cache):
    """Serialize this helper's managed writers, never hold an idle reservation.

    Fail rather than wait: a queued SSH request must not activate after its host
    operation lost authority. The lock inode is permanent (never unlink it).
    """
    with cache.directory(root, create=True) as parent:
        fd = os.open('.admission.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=parent)
        try:
            cache.regular(fd)
            if os.fstat(fd).st_nlink != 1:
                raise ValueError('unsafe_admission_lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity = os.fstat(parent)
            def fence():
                with cache.directory(root) as current:
                    now = os.fstat(current)
                    if (now.st_dev, now.st_ino) != (identity.st_dev, identity.st_ino):
                        raise ValueError('managed_root_changed')
                linked = os.stat('.admission.lock', dir_fd=parent, follow_symlinks=False)
                if (linked.st_dev, linked.st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                    raise ValueError('admission_lock_changed')
            fence()
            yield parent, fence
        finally:
            os.close(fd)


@contextmanager
def durable_directory(path, cache):
    """Fsync directory links, including new intermediate generation directories."""
    with cache.directory('/') as initial:
        fd = os.dup(initial)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.fsync(fd)
            os.close(fd)
            fd = child
        yield fd
        os.fsync(fd)
    finally:
        os.close(fd)


def check_space(parent, manifest, rows, *, metadata_allowance_bytes=None):
    fs = os.fstatvfs(parent)
    block = fs.f_frsize or fs.f_bsize
    rounded = lambda n: ((n + block - 1) // block) * block
    # Budget all absent/bad leaves concurrently: completed leaves are retained
    # on failure. Account duplicates by destination, not by source digest.
    payload = sum(rounded(row['size_bytes']) for row in rows)
    metadata = metadata_allowance_bytes
    if metadata is None:
        directories = {str(PurePosixPath(r['name']).parent) for r in manifest['artifacts']}
        metadata = rounded(len(canonical(manifest))) + block * (8 + sum(len(PurePosixPath(d).parts) for d in directories))
    required = payload + metadata + MIN_HEADROOM
    available = fs.f_bavail * block
    if available < required:
        raise OSError(errno.ENOSPC, 'managed_space_admission_failed')
    return {'additional_copy_bytes': payload, 'metadata_allowance_bytes': metadata,
            'headroom_bytes': MIN_HEADROOM, 'available_bytes': available,
            'required_available_bytes': required, 'reservation': False}


def admit(root, manifest, expected_boot, cache):
    validate_manifest(manifest, cache)
    with admission_lock(root, cache) as (parent, fence):
        if boot_id() != expected_boot:
            raise ValueError('worker_boot_changed')
        # The conservative peak assumes uploads and installed copies share a
        # filesystem. Reject split/mounted cache layouts rather than account on
        # the wrong device. This is still only a point-in-time preflight.
        cache_root = root.parent.parent / 'cache/artifacts/v1'
        paths = {cache_root / 'incoming', cache_root / 'locks'}
        paths.update(cache_root / 'objects/sha256' / r['sha256'][:2]
                     for r in manifest['artifacts'] if r.get('kind') != 'runtime_image')
        if any(r.get('kind') == 'runtime_image' for r in manifest['artifacts']):
            paths.add(image_storage(root, cache).image_store)
        for path in paths:
            with cache.directory(path, create=True) as current:
                if os.fstat(current).st_dev != os.fstat(parent).st_dev:
                    raise ValueError('managed_filesystem_changed')
        # Images peak at upload + immutable shared object, never a managed copy.
        peak = [r for r in manifest['artifacts']
                for _ in range(2 if r.get('kind') == 'runtime_image' else 3)]
        result = {'admission': check_space(parent, manifest, peak)}
        fence()
        return result


def install(root, manifest, expected_boot, cache):
    """Own admission, weight copies and activation of shared image references."""
    digest = validate_manifest(manifest, cache)
    with admission_lock(root, cache) as (parent, fence):
        if boot_id() != expected_boot:
            raise ValueError('worker_boot_changed')
        observed = observe(root, manifest, cache)
        if 'critical' in manifest:
            if not observed['critical']['compatible']:
                raise ValueError('critical_runtime_incompatible')
            try:
                prior_manifest = read_bytes(release_path(root, manifest) / 'manifest.json', cache)
            except FileNotFoundError:
                prior_manifest = None
            if prior_manifest is not None and prior_manifest != canonical(manifest):
                raise ValueError('critical_generation_identity_collision')
        missing = [r for r, seen in zip(manifest['artifacts'], observed['artifacts'])
                   if seen['state'] != 'verified']
        # Never repair an activated generation in place. It may have consumers.
        active = root / 'active' / (manifest['selection']['kind'] + '-' + manifest['selection']['model_id'] + '.json')
        try:
            current = json.loads(read_bytes(active, cache))
        except FileNotFoundError:
            current = None
        if missing and isinstance(current, dict) and current.get('release_sha256') == digest:
            raise ValueError('active_generation_damaged')
        if any(r.get('kind') == 'runtime_image' for r in missing):
            raise ValueError('incomplete_shared_image')
        budget = check_space(parent, manifest, missing)
        def copy_progress(event):
            if event.get('state') == 'transferring':
                # Check between chunks as well as before a whole-file copy.
                # At most one helper chunk can cross this floor before abort.
                fs = os.fstatvfs(parent)
                if fs.f_bavail * (fs.f_frsize or fs.f_bsize) < MIN_HEADROOM + budget['metadata_allowance_bytes']:
                    raise OSError(errno.ENOSPC, 'managed_space_floor_reached')
        storage = cache.Cache(root.parent.parent / 'cache/artifacts/v1', events=copy_progress)
        directories = {}
        def tree_fence():
            fence()
            for path, identity in directories.items():
                with cache.directory(path) as current:
                    now = os.fstat(current)
                    if (now.st_dev, now.st_ino) != identity:
                        raise ValueError('managed_directory_changed')
        for row in missing:
            fence()
            if boot_id() != expected_boot:
                raise ValueError('worker_boot_changed')
            destination = release_path(root, manifest) / row['name']
            with durable_directory(destination.parent, cache) as out:
                # A nested mount must not evade the filesystem admission budget.
                info = os.fstat(out)
                if info.st_dev != os.fstat(parent).st_dev:
                    raise ValueError('managed_filesystem_changed')
                directories[destination.parent] = (info.st_dev, info.st_ino)
                # Recheck actual remaining free space before each large copy.
                check_space(out, manifest, [row], metadata_allowance_bytes=budget["metadata_allowance_bytes"])
                with storage.locked(row), storage.objects(row) as objects:
                    source = os.open(row['sha256'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                     dir_fd=objects)
                    try:
                        if not cache.verified(source, row):
                            raise ValueError('corrupt_object')
                        # Copy through the already-open destination descriptor.
                        # The complete directory map is checked once before activation.
                        fence()
                        if boot_id() != expected_boot:
                            raise ValueError('worker_boot_changed')
                        # Use the SAME descriptor whose filesystem was admitted;
                        # do not reopen the destination through mutable pathnames.
                        storage._publish_copy(source, out, destination.name, row, row['mode'] & 0o555)
                    finally:
                        os.close(source)
        tree_fence()
        with durable_directory(root / 'active', cache) as active_parent:
            info = os.fstat(active_parent)
            if info.st_dev != os.fstat(parent).st_dev:
                raise ValueError('managed_filesystem_changed')
            directories[root / 'active'] = (info.st_dev, info.st_ino)
        result = activate(root, manifest, expected_boot, cache, fence=tree_fence)
        return {'release': result, 'admission': budget}


def verify_critical_execution(root, manifest):
    """Offline executable smoke checks are not scientific acceptance."""
    import subprocess
    release = release_path(root, manifest)
    critical = manifest['critical']
    env = {k: v for k, v in os.environ.items() if not k.startswith(('NXF_', 'PYTHON'))}
    env.update(NXF_OFFLINE='true', NXF_VER=critical['nextflow_version'],
               NXF_HOME=str(release / 'nextflow/home'), PYTHONNOUSERSITE='1')
    version = subprocess.run([str(release / critical['entrypoints']['nextflow']), '-version'],
                             env=env, capture_output=True, text=True, check=True, timeout=120)
    if f"version {critical['nextflow_version']}" not in version.stdout:
        raise ValueError('critical_nextflow_version_mismatch')
    subprocess.run([str(release / critical['entrypoints']['python']), '-I', '-c',
                    'import encodings,ssl,json,ctypes; print("BMS_SUPPORT_OK")'],
                   env=env, capture_output=True, text=True, check=True, timeout=120)


def activate(root, manifest, expected_boot, cache, fence=lambda: None):
    if boot_id() != expected_boot:
        raise ValueError('worker_boot_changed')
    digest = validate_manifest(manifest, cache)
    result = observe(root, manifest, cache)
    if 'critical' in manifest and not result['critical']['compatible']:
        raise ValueError('critical_runtime_incompatible')
    if any(row['state'] != 'verified' for row in result['artifacts']):
        raise ValueError('incomplete_release')
    if 'critical' in manifest:
        try:
            existing = read_bytes(release_path(root, manifest) / 'manifest.json', cache)
        except FileNotFoundError:
            existing = None
        if existing is not None and existing != canonical(manifest):
            raise ValueError('critical_generation_identity_collision')
        verify_critical_execution(root, manifest)
    # Weight copies and prepublished shared images are verified before activation.
    # The manifest records image identity, never another full managed SIF.
    # Prior content-addressed generations remain intact for explicit recovery.
    # Keep directory-entry durability ordered before active publication as well
    # as file fsyncs. No pre-activation failure may replace the prior identity.
    result['image_reference'] = (retain_managed_image_releases(root, manifest, cache)
        if any(row.get('kind') == 'runtime_image' for row in manifest['artifacts']) else None)
    with durable_directory(release_path(root, manifest), cache):
        pass
    publish(release_path(root, manifest) / 'manifest.json', manifest, cache)
    if boot_id() != expected_boot:
        raise ValueError('worker_boot_changed')
    fence()
    selection = manifest['selection']
    marker = {'release_sha256': digest}
    if 'critical' in manifest:
        marker['boot_id'] = expected_boot
    publish(root / 'active' / (selection['kind'] + '-' + selection['model_id'] + '.json'),
            marker, cache)
    # This process just verified the release and atomically published its marker.
    # Do not hash the entire Python release and spawn version probes again here.
    result['state'] = 'verified'
    return result


def bounded_native_check(release):
    """Project native-check coverage, never substitute installed bytes for a probe.

    Input is the existing observe/activation result, not a command descriptor.
    Native launch preflights are not yet bound to this sanitized manifest's
    selected immutable image and resources. In particular RFantibody's native
    check executes CUDA/DGL operations; it is not a file-presence check.
    """
    kind = release['selection']['kind']
    blockers = [] if release['state'] == 'verified' else ['managed_assets_not_verified']
    missing = ([] if kind == 'critical_runtime' else
               ['selected_image_native_preflight_binding'])
    if any(a.get('name') == 'containers/rfantibody.sif' for a in release.get('artifacts', [])):
        missing = ['scripts/check_rfantibody_runtime.py:operation_owned_gpu_probe_not_recorded']
    if kind == 'image':
        missing.append('complete_model_dependency_selection')
    if kind == 'workflow':
        missing.append('selected_native_component_probe_binding')
    return dict(state=('not_applicable' if kind == 'critical_runtime' else
                       'blocked' if blockers else 'unverified'),
                scope='native_runtime_preflight_only',
                authority='platform/api/tools/bms_managed_runtime.py:observe',
                missing_authorities=missing, blockers=blockers)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--cache-helper', required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('cache_helper', args.cache_helper)
    if spec is None or spec.loader is None:
        raise ValueError('invalid_cache_helper')
    cache = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cache)
    root = PurePosixPath(args.root)
    if not root.is_absolute() or '..' in root.parts or root == PurePosixPath('/'):
        raise ValueError('invalid_root')
    request = json.loads(sys.stdin.buffer.read(MAX_JSON + 1))
    before = boot_id()
    if request['action'] == 'boot':
        result = {}
    elif request['action'] == 'observe':
        result = {'releases': [observe(root, m, cache) for m in request['manifests']]}
    elif request['action'] == 'bounded_check':
        releases = [observe(root, m, cache) for m in request['manifests']]
        for release in releases:
            release['native_readiness'] = bounded_native_check(release)
        result = {'releases': releases}
    elif request['action'] == 'install':
        result = install(root, request['manifest'], request['boot_id'], cache)
    elif request['action'] == 'admit':
        # Incoming + cache object (+ managed copy for weights), even for hits.
        # This short conservative preflight reserves nothing.
        result = admit(root, request['manifest'], request['boot_id'], cache)
    elif request['action'] == 'activate':
        with admission_lock(root, cache) as (_, fence):
            result = {'release': activate(root, request['manifest'], request['boot_id'], cache, fence=fence)}
    else:
        raise ValueError('unsupported_action')
    if boot_id() != before:
        raise ValueError('worker_boot_changed')
    print(json.dumps(result | {'boot_id': before}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'state': 'failed', 'error': type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
