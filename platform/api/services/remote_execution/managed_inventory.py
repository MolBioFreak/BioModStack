"""Managed asset releases: host-authorized identity, worker-observed bytes.

The inventory covers independently provisioned releases, not arbitrary worker
files or a certified critical/scientific runtime. GET is a read-only projection;
explicit refresh performs bounded SSH readback without requiring local weights.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal
import uuid

from pydantic import Field

from .contracts import (StrictModel, ProvisionSelection, CachedArtifactReceipt,
                        CriticalRuntimeSelection, CriticalRuntimeCompatibility, WorkflowRuntimeSelection)


class ManagedArtifact(CachedArtifactReceipt):
    state: Literal['verified', 'missing', 'corrupt', 'incompatible']


class ManagedImageIdentity(StrictModel):
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    size: int = Field(ge=0, strict=True)
    device: int = Field(ge=0, strict=True)
    inode: int = Field(ge=0, strict=True)
    mtime_ns: int = Field(strict=True)
    ctime_ns: int = Field(strict=True)


class ManagedImageReference(StrictModel):
    store_root: str = Field(pattern=r'^/[^\x00]*$')
    owner: str = Field(pattern=r'^managed-release:[0-9a-f]{64}$')
    lease_token: str = Field(pattern=r'^[0-9a-f]{32}$')
    identities: dict[str, ManagedImageIdentity] = Field(min_length=1)


class NativeProbeEvidence(StrictModel):
    authority: Literal['scripts/check_rfantibody_runtime.py:run_preflight']
    outcome: Literal['passed', 'failed']
    gpu_id: int | None = Field(default=None, ge=0)
    gpu_uuid: str | None = None
    observed_at: datetime | None = None
    release_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    image_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    script_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    source_tree: str = Field(pattern=r'^[0-9a-f]{40}$')
    boot_id: uuid.UUID


class NativeReadiness(StrictModel):
    # No "ready" state until a native probe has a selected-image execution
    # authority. Asset provisioning and inference acceptance remain independent.
    state: Literal['blocked', 'unverified', 'stale', 'not_applicable']
    scope: Literal['native_runtime_preflight_only'] = 'native_runtime_preflight_only'
    authority: str
    missing_authorities: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    probe: NativeProbeEvidence | None = None


class ManagedRelease(StrictModel):
    selection: ProvisionSelection | CriticalRuntimeSelection | WorkflowRuntimeSelection
    critical: CriticalRuntimeCompatibility | None = None
    image_reference: ManagedImageReference | None = None
    native_readiness: NativeReadiness | None = None
    release_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    source_tree: str = Field(pattern=r'^[0-9a-f]{40}$')
    state: Literal['verified', 'missing', 'partial', 'corrupt', 'incompatible', 'unverified']
    artifacts: list[ManagedArtifact]
    bounded_readiness: Literal['verified_assets_and_critical_runtime', 'blocked', 'stale'] = 'blocked'
    readiness_scope: Literal['asset_integrity_and_critical_compatibility_only'] = 'asset_integrity_and_critical_compatibility_only'


class ManagedInventory(StrictModel):
    observed_at: datetime
    boot_id: uuid.UUID
    state: Literal['current', 'stale'] = 'stale'
    scope: Literal['managed_independent_asset_releases'] = 'managed_independent_asset_releases'
    releases: list[ManagedRelease]
    scientific_ready: Literal[False] = False
    critical_runtime_ready: bool = False
    blockers: list[Literal['critical_release_not_verified', 'scientific_readiness_not_checked']] = Field(
        default_factory=lambda: ['critical_release_not_verified', 'scientific_readiness_not_checked'])


def endpoint_digest(target):
    return hashlib.sha256(json.dumps((target.host, target.port, target.username,
        target.remote_root, target.host_key_sha256)).encode()).hexdigest()


def saved_manifests(target):
    """Existing endpoint-bound manifests, including the attached critical release."""
    metadata = target.provider_metadata or {}
    endpoint = endpoint_digest(target)
    previous = metadata.get('managed_inventory') or {}
    manifests = list(previous.get('manifests', [])) if previous.get('endpoint_sha256') == endpoint else []
    critical = metadata.get('critical_runtime_manifest')
    if critical and metadata.get('critical_runtime_endpoint_sha256') == endpoint:
        manifests = [m for m in manifests if m['selection'] != critical['selection']] + [critical]
    return manifests


def manifest_for(selection, entries, source) -> dict[str, Any]:
    # Never transfer the scientific request, sequences, paths or generated inputs.
    wire_selection = selection.model_dump(mode="json")
    if selection.kind == "workflow":
        wire_selection = dict(kind="workflow", model_id=hashlib.sha256(json.dumps(
            wire_selection, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
    return dict(selection=wire_selection, source_revision=source[0], source_tree=source[1],
        artifacts=[dict(name=e.remote_destination, sha256=e.sha256, size_bytes=e.size_bytes,
                        mode=e.mode, **({'kind': 'runtime_image'} if e.role == 'image' else {}))
                   for e in entries])


def release_digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


async def helper_call(connection, request, check_fence):
    from . import cache
    cache_tool = await cache._install_helper(connection, check_fence)
    tool = await cache._install_helper(connection, check_fence, 'bms_managed_runtime.py')
    await check_fence()
    result = await cache.run_remote(connection, ['python3', tool, '--root',
        connection.remote_root + '/managed-assets/v1', '--cache-helper', cache_tool],
        input_bytes=json.dumps(request).encode(), timeout=3600)
    await check_fence()
    response = json.loads(result.stdout)
    uuid.UUID(response['boot_id'])
    return response


async def activate_release(connection, manifest, check_fence, progress, boot):
    await progress(dict(phase='verifying', artifact=None,
                        message='Checking free space and installing verified managed asset release'))
    # One process holds the worker admission lock through accounting, copying
    # and publication. Separate materialize_many SSH calls cannot do that.
    response = await helper_call(connection, dict(action='install', manifest=manifest, boot_id=boot), check_fence)
    observed = ManagedRelease.model_validate(response['release'])
    if (response['boot_id'] != boot or observed.state != 'verified'
            or observed.release_sha256 != release_digest(manifest)):
        raise ValueError('Managed release activation verification failed')
    validate_observation(ManagedInventory(observed_at=datetime.now(timezone.utc),
        boot_id=boot, releases=[observed]), [manifest])
    return observed


def validate_observation(result, manifests):
    if len(result.releases) != len(manifests):
        raise ValueError('Managed inventory identity mismatch')
    for observed, manifest in zip(result.releases, manifests, strict=True):
        # Old copied-image metadata cannot certify shared-image storage.
        if any((r['name'].startswith('containers/')) != (r.get('kind') == 'runtime_image')
               for r in manifest['artifacts']):
            raise ValueError('Managed image storage identity mismatch')
        expected_artifacts = [{k: r[k] for k in ('name', 'sha256', 'size_bytes')} for r in manifest['artifacts']]
        if (not expected_artifacts or observed.release_sha256 != release_digest(manifest)
                or observed.selection.model_dump() != manifest['selection']
                or observed.source_revision != manifest['source_revision']
                or observed.source_tree != manifest['source_tree']
                or [r.model_dump(exclude={'state'}) for r in observed.artifacts] != expected_artifacts):
            raise ValueError('Managed inventory identity mismatch')
        images = {r['sha256']: r['size_bytes'] for r in manifest['artifacts']
                  if r.get('kind') == 'runtime_image'}
        reference = observed.image_reference
        if reference is not None:
            if (not images or reference.owner != 'managed-release:' + release_digest(manifest)
                    or set(reference.identities) != set(images)
                    or any(identity.sha256 != digest or identity.size != images[digest]
                           for digest, identity in reference.identities.items())):
                raise ValueError('Managed image reference identity mismatch')
        elif images and observed.state == 'verified':
            raise ValueError('Managed image reference missing')
        if 'critical' in manifest:
            from tools.bms_managed_runtime import critical_compatible
            compatibility = observed.critical
            if (compatibility is None or compatibility.requirements != manifest['critical']['requirements']
                    or compatibility.compatible != critical_compatible(compatibility.requirements, compatibility.observed)):
                raise ValueError('Critical compatibility observation mismatch')
            if not compatibility.compatible:
                if observed.state != 'incompatible':
                    raise ValueError('Critical compatibility readiness mismatch')
                continue
        elif observed.critical is not None:
            raise ValueError('Unexpected critical compatibility')
        states = {r.state for r in observed.artifacts}
        expected = ('corrupt' if 'corrupt' in states else 'incompatible' if 'incompatible' in states
                    else 'missing' if states == {'missing'} else 'partial' if 'missing' in states else None)
        if observed.state not in ({expected} if expected else {'verified', 'unverified'}):
            raise ValueError('Managed inventory readiness mismatch')


async def observe_releases(connection, manifests, check_fence):
    response = await helper_call(connection, dict(action='bounded_check', manifests=manifests), check_fence)
    result = ManagedInventory(observed_at=datetime.now(timezone.utc), boot_id=response['boot_id'],
                              releases=response['releases'])
    validate_observation(result, manifests)
    result.critical_runtime_ready = any(r.selection.kind == 'critical_runtime' and r.state == 'verified'
                                       for r in result.releases)
    if result.critical_runtime_ready:
        result.blockers = ['scientific_readiness_not_checked']
    return result


async def run_native_readiness_check(connection, manifest, check_fence, *, gpu_id: int | None = None,
                                     observed=None, manifests=None):
    """Explicit operation-owned RFantibody preflight; never called by polling.

    The fixed command is the native modules/rfantibody.nf preflight, not its
    biological process. Stream the checked-out native script over existing SSH
    stdin; stage no input/output files and execute no inference or downloads.
    Caller owns the existing exclusive provisioning reservation/cancellation.
    An omitted GPU is selected only from the worker's reported index/UUID; no
    device-zero assumption or new job/worker scheduler is introduced.
    Returned evidence must be persisted with the enclosing managed observation.
    """
    from pathlib import PurePosixPath
    from services.nextflow import get_code_root
    from .bundle import current_source_identity
    from .transport import run_remote
    if not connection.provision_operation_id or (gpu_id is not None and (type(gpu_id) is not int or gpu_id < 0)):
        raise ValueError('Native check requires operation-owned GPU allocation')
    images = [a for a in manifest['artifacts']
              if a.get('kind') == 'runtime_image' and a['name'] == 'containers/rfantibody.sif']
    if len(images) != 1:
        raise ValueError('No bound RFantibody native check authority for this release')
    source = get_code_root()
    identity = (manifest['source_revision'], manifest['source_tree'])
    if current_source_identity(source) != identity:
        raise ValueError('Native check source identity changed')
    script = (source / 'scripts/check_rfantibody_runtime.py').read_bytes()
    manifests = manifests or [manifest]
    before = observed or await observe_releases(connection, manifests, check_fence)
    digest = release_digest(manifest)
    release = next(r for r in before.releases if r.release_sha256 == digest)
    if release.state != 'verified' or release.image_reference is None:
        raise ValueError('Native check image release is not verified')
    image = images[0]
    path = PurePosixPath(release.image_reference.store_root) / 'objects/sha256' / image['sha256'] / 'runtime.sif'
    await check_fence()
    gpu_uuid = None
    if gpu_id is None:
        import csv, io, re
        device_result = await run_remote(connection, ['nvidia-smi', '--query-gpu=index,uuid',
            '--format=csv,noheader,nounits'], timeout=30)
        await check_fence()
        devices = []
        for row in csv.reader(io.StringIO(device_result.stdout)):
            if (len(row) == 2 and row[0].strip().isdigit() and re.fullmatch(
                    r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', row[1].strip())):
                devices.append((int(row[0].strip()), row[1].strip()))
        if not devices:
            readiness = project_native_readiness(release, current=True, critical_ready=before.critical_runtime_ready)
            readiness.missing_authorities.append('native_probe_gpu_identity_not_reported')
            release.native_readiness = readiness
            return before
        gpu_id, gpu_uuid = sorted(devices)[0]
    result = await run_remote(connection, ['apptainer', 'exec', '--nv',
        '--env', 'CUDA_DEVICE_ORDER=PCI_BUS_ID', '--env', f'CUDA_VISIBLE_DEVICES={gpu_uuid or gpu_id}',
        '--writable-tmpfs', str(path), 'python3', '-'], input_bytes=script, timeout=120)
    await check_fence()
    outcome = 'passed' if result.returncode == 0 and '[RFA-PREFLIGHT] OK' in result.stdout.splitlines() else 'failed'
    after = await observe_releases(connection, manifests, check_fence)
    checked = next(r for r in after.releases if r.release_sha256 == digest)
    if (after.boot_id != before.boot_id or checked.state != 'verified'
            or checked.image_reference != release.image_reference
            or current_source_identity(source) != identity):
        raise ValueError('Native check observation identity changed')
    readiness = project_native_readiness(checked, current=True, critical_ready=after.critical_runtime_ready)
    readiness.probe = NativeProbeEvidence(authority='scripts/check_rfantibody_runtime.py:run_preflight',
        outcome=outcome, gpu_id=gpu_id, gpu_uuid=gpu_uuid, observed_at=after.observed_at,
        release_sha256=release.release_sha256, image_sha256=image['sha256'],
        script_sha256=hashlib.sha256(script).hexdigest(), source_revision=identity[0],
        source_tree=identity[1], boot_id=after.boot_id)
    # This probe checks CUDA/DGL, not all selected workflow components or weights.
    readiness.missing_authorities = [m for m in readiness.missing_authorities
                                    if m not in {'selected_image_native_preflight_binding',
                                          'scripts/check_rfantibody_runtime.py:operation_owned_gpu_probe_not_recorded'}]
    readiness.missing_authorities.append('complete_selected_model_native_probe_coverage')
    if outcome == 'failed':
        readiness.blockers.append('native_runtime_preflight_failed')
        readiness.state = 'blocked'
    checked.native_readiness = readiness
    return after


def project_native_readiness(release, *, current, critical_ready, boot=None):
    """Use shared dependency metadata; unknown probe coverage is not asset failure.

    Recompute this optional projection, including old observations with no native
    field. Never trust a cached native claim independently of the enclosing
    endpoint/boot/release observation, or introduce a second model catalog.
    """
    from tools.bms_managed_runtime import bounded_native_check
    result = NativeReadiness.model_validate(bounded_native_check(release.model_dump(mode='json')))
    if release.selection.kind == 'critical_runtime':
        return result
    if not critical_ready:
        result.blockers.append('critical_release_not_verified')
    if release.selection.kind == 'model':
        from model_registry import model_runtime_dependencies
        result.authority = 'platform/api/model_registry.py:model_runtime_dependencies'
        try:
            dependencies = model_runtime_dependencies(release.selection.model_id)
        except ValueError:
            result.missing_authorities.append('independent_model_dependency_closure')
        else:
            names = {a.name for a in release.artifacts if a.state == 'verified'}
            for dependency in dependencies:
                prefix = ('containers/' if dependency.kind == 'image' else 'weights/') + dependency.relative_path
                if not any(name == prefix if dependency.kind == 'image' else
                           name.startswith(prefix + '/') for name in names):
                    result.blockers.append('dependency_not_verified:' + prefix)
    result.state = ('stale' if not current else 'blocked' if result.blockers else 'unverified')
    probe = release.native_readiness.probe if release.native_readiness else None
    if (probe is not None and probe.release_sha256 == release.release_sha256
            and probe.source_revision == release.source_revision and probe.source_tree == release.source_tree
            and probe.boot_id == boot and any(a.sha256 == probe.image_sha256
                and a.name == 'containers/rfantibody.sif' and a.state == 'verified' for a in release.artifacts)):
        result.probe = probe
        if probe.outcome == 'failed':
            result.blockers.append('native_runtime_preflight_failed')
            result.state = 'blocked' if current else 'stale'
        result.missing_authorities = [m for m in result.missing_authorities
                                      if m not in {'selected_image_native_preflight_binding',
                                          'scripts/check_rfantibody_runtime.py:operation_owned_gpu_probe_not_recorded'}]
        result.missing_authorities.append('complete_selected_model_native_probe_coverage')
    return result


def project_inventory(target):
    from .targets import inventory_fresh, INVENTORY_MAX_AGE_SECONDS
    metadata = target.provider_metadata or {}
    raw = metadata.get('managed_inventory')
    if not isinstance(raw, dict):
        return None
    try:
        # Native readiness is an optional derived section. Malformed/old native
        # telemetry must not hide otherwise valid immutable asset inventory.
        payload = dict(raw['observation'])
        native = [r.get('native_readiness') for r in payload['releases']]
        payload['releases'] = [dict(r, native_readiness=None) for r in payload['releases']]
        observation = ManagedInventory.model_validate(payload)
        for release, value in zip(observation.releases, native, strict=True):
            try:
                release.native_readiness = NativeReadiness.model_validate(value) if value is not None else None
            except ValueError:
                pass
        validate_observation(observation, raw['manifests'])
        when = observation.observed_at
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - when).total_seconds()
        observation.state = 'stale'
        provider = metadata.get('inventory', {})
        if (0 <= age <= INVENTORY_MAX_AGE_SECONDS and inventory_fresh(target)
                and provider.get('present') is True and provider.get('running') is True
                and target.active and target.state == 'ready'
                and raw.get('endpoint_sha256') == endpoint_digest(target)
                and not raw.get('refresh_failed', False)
                and str(observation.boot_id) == metadata.get('managed_boot_id')):
            observation.state = 'current'
        observation.critical_runtime_ready = (observation.state == 'current' and any(
            r.selection.kind == 'critical_runtime' and r.state == 'verified' for r in observation.releases))
        observation.blockers = (['scientific_readiness_not_checked'] if observation.critical_runtime_ready
                                else ['critical_release_not_verified', 'scientific_readiness_not_checked'])
        for release in observation.releases:
            release.native_readiness = project_native_readiness(release,
                current=observation.state == 'current', critical_ready=observation.critical_runtime_ready,
                boot=observation.boot_id)
            release.bounded_readiness = (
                "stale" if observation.state != "current" else
                "verified_assets_and_critical_runtime" if observation.critical_runtime_ready
                and release.state == "verified" else "blocked")
        return observation
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
