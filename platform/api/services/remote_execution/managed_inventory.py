"""Managed asset releases: host-authorized identity, worker-observed bytes.

The inventory covers independently provisioned releases, not arbitrary worker
files or a certified critical/scientific runtime. GET is a read-only projection;
explicit refresh performs bounded SSH readback without requiring local weights.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
import uuid

from pydantic import Field

from .contracts import StrictModel, ProvisionSelection, CachedArtifactReceipt


class ManagedArtifact(CachedArtifactReceipt):
    state: Literal['verified', 'missing', 'corrupt', 'incompatible']


class ManagedRelease(StrictModel):
    selection: ProvisionSelection
    release_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    source_tree: str = Field(pattern=r'^[0-9a-f]{40}$')
    state: Literal['verified', 'missing', 'partial', 'corrupt', 'incompatible', 'unverified']
    artifacts: list[ManagedArtifact]


class ManagedInventory(StrictModel):
    observed_at: datetime
    boot_id: uuid.UUID
    state: Literal['current', 'stale'] = 'stale'
    scope: Literal['managed_independent_asset_releases'] = 'managed_independent_asset_releases'
    releases: list[ManagedRelease]
    scientific_ready: Literal[False] = False
    critical_runtime_ready: Literal[False] = False
    blockers: list[Literal['critical_release_not_verified', 'scientific_readiness_not_checked']] = Field(
        default_factory=lambda: ['critical_release_not_verified', 'scientific_readiness_not_checked'])


def endpoint_digest(target):
    return hashlib.sha256(json.dumps((target.host, target.port, target.username,
        target.remote_root, target.host_key_sha256)).encode()).hexdigest()


def manifest_for(selection, entries, source):
    return dict(selection=selection.model_dump(), source_revision=source[0], source_tree=source[1],
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
        states = {r.state for r in observed.artifacts}
        expected = ('corrupt' if 'corrupt' in states else 'incompatible' if 'incompatible' in states
                    else 'missing' if states == {'missing'} else 'partial' if 'missing' in states else None)
        if observed.state not in ({expected} if expected else {'verified', 'unverified'}):
            raise ValueError('Managed inventory readiness mismatch')


async def observe_releases(connection, manifests, check_fence):
    response = await helper_call(connection, dict(action='observe', manifests=manifests), check_fence)
    result = ManagedInventory(observed_at=datetime.now(timezone.utc), boot_id=response['boot_id'],
                              releases=response['releases'])
    validate_observation(result, manifests)
    return result


def project_inventory(target):
    from .targets import inventory_fresh, INVENTORY_MAX_AGE_SECONDS
    metadata = target.provider_metadata or {}
    raw = metadata.get('managed_inventory')
    if not isinstance(raw, dict):
        return None
    try:
        observation = ManagedInventory.model_validate(raw['observation'])
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
        return observation
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
