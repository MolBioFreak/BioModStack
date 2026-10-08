"""Shared artifact cache transport for bundle launch and explicit saved-Job prewarm."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

from paths import get_code_root
from .bundle import (CacheTransferArtifact, cache_transfer_artifacts, current_source_identity,
                     compile_remote_dependencies, _runtime_assets, _records_for_source,
                     _safe_extract, _is_runtime_image, verify_selected_runtime_hashes, verify_selected_preparation_inputs)
from .transport import run_remote, rsync_to_remote
from .images import resolve_image


async def _noop(*args, **kwargs):
    pass


async def _install_helper(connection, check_fence, helper_name='bms_artifact_cache.py'):
    if helper_name not in {'bms_artifact_cache.py', 'bms_managed_runtime.py'}:
        raise ValueError('Unknown managed helper')
    await check_fence()
    payloads = {helper_name: (Path(__file__).parents[2] / 'tools' / helper_name).read_bytes()}
    if helper_name == 'bms_artifact_cache.py':
        payloads['shared_runtime_images.py'] = (Path(__file__).parents[4] / 'scripts/lib/shared_runtime_images.py').read_bytes()
        # Lifecycle retention is mandatory, not an optional worker capability.
        lifecycle = Path(__file__).parents[4] / 'scripts/lib/runtime_image_lifecycle.py'
        payloads[lifecycle.name] = lifecycle.read_bytes()
    generation = hashlib.sha256(b''.join(payloads.values())).hexdigest()
    destination = f'{connection.remote_root}/runner/cache-{generation}/{helper_name}'
    # Small source modules: stdin transfers, each verified before atomic publication.

    script = """import hashlib,os,pathlib,sys,tempfile
p=pathlib.Path(sys.argv[1]);expected=sys.argv[2];data=sys.stdin.buffer.read()
if hashlib.sha256(data).hexdigest()!=expected: raise RuntimeError('helper identity mismatch')
q=pathlib.Path('/')
for part in p.parent.parts[1:]:
 q=q/part
 if q.is_symlink(): raise RuntimeError('unsafe helper path')
 q.mkdir(mode=0o700,exist_ok=True)
fd,t=tempfile.mkstemp(prefix='.cache-helper-',dir=p.parent)
try:
 with os.fdopen(fd,'wb') as f: f.write(data);f.flush();os.fsync(f.fileno())
 os.chmod(t,0o500);os.replace(t,p)
finally:
 if os.path.exists(t): os.unlink(t)
"""
    for name, payload in payloads.items():
        await check_fence()
        path = str(Path(destination).with_name(name))
        await run_remote(connection, ['python3', '-c', script, path,
                                     hashlib.sha256(payload).hexdigest()], input_bytes=payload)
    return destination


async def _cache_artifacts(*, connection, artifacts, operation_id, progress, check_fence,
                           materialize=False, links=(), runtime_root=None, track_artifacts=False):
    # Caller owns operation identity and destination authority; never use public paths.
    uuid.UUID(operation_id)
    tool = await _install_helper(connection, check_fence)
    root = f'{connection.remote_root}/cache/artifacts/v1'
    async def call(request):
        await check_fence()
        result = await run_remote(connection, ['python3', tool, '--root', root],
                                  input_bytes=json.dumps(request).encode(), timeout=3600)
        return json.loads(result.stdout)
    artifacts = tuple(artifacts)
    states = {}
    activity = [dict(name=e.remote_destination.removeprefix(connection.remote_root.rstrip("/") + "/"),
                     sha256=e.sha256, size_bytes=e.size_bytes, state="pending") for e in artifacts]
    async def report(phase, artifact, message):
        await progress(dict(phase=phase, artifact=artifact, message=message,
                            **({"artifact_progress": [dict(row) for row in activity]} if track_artifacts else {})))
    def identity(entry):
        return {'sha256': entry.sha256, 'size_bytes': entry.size_bytes,
                **({'kind': 'runtime_image'} if entry.role == 'image' else {})}
    def key(entry):
        return (entry.role == 'image', entry.sha256)
    for offset in range(0, len(artifacts), 128):
        batch = artifacts[offset:offset + 128]
        await report('checking', None, 'Verifying cached artifact batch')
        response = await call({'action': 'probe', 'artifacts': [identity(entry) for entry in batch]})
        states.update({(row.get('kind') == 'runtime_image', row['sha256']): row['state']
                       for row in response['artifacts']})
    receipts = []
    for index, entry in enumerate(artifacts):
        item = identity(entry)
        # Names only from authoritative relative destinations, never source paths.
        name = entry.remote_destination.removeprefix(connection.remote_root.rstrip('/') + '/')
        if states[key(entry)] != 'cache_hit':
            incoming = f'{root}/incoming/{operation_id}/{uuid.uuid4().hex}'
            await check_fence()
            await run_remote(connection, ['mkdir', '-p', str(Path(incoming).parent)])
            activity[index]['state'] = 'transferring'
            await report('transferring', name, 'Transferring artifact')
            await check_fence()
            await rsync_to_remote(connection, entry.source, incoming, delete=False)
            activity[index]['state'] = 'verifying'
            await report('verifying', name, 'Verifying and publishing artifact')
            await call({'action': 'ingest', 'artifact': item, 'source': incoming})
            # Clean only a confirmed completed ingest, never race an uncertain
            # remote writer after cancellation or SSH loss.
            await check_fence()
            await run_remote(connection, ['rm', '-f', '--', incoming])
            await check_fence()
            states[key(entry)] = 'cache_hit'
        receipts.append({'name': name, 'sha256': entry.sha256, 'size_bytes': entry.size_bytes})
        activity[index]['state'] = 'verified'
        if track_artifacts:
            await report('verifying', name, 'Artifact cache identity verified')
    if materialize:
        for offset in range(0, len(artifacts), 128):
            batch = artifacts[offset:offset + 128]
            await progress({'phase': 'verifying', 'artifact': None, 'message': 'Materializing verified artifact batch'})
            await call({'action': 'materialize_many', 'destination_root': connection.remote_root,
                        'entries': [{'artifact': identity(entry),
                                     'destination': entry.remote_destination, 'mode': entry.mode,
                                     'aliases': list(entry.aliases), 'runtime_root': runtime_root} for entry in batch]})
        for offset in range(0, len(links), 128):
            await call({'action': 'materialize_links', 'destination_root': runtime_root,
                        'entries': links[offset:offset + 128]})
        for entry in artifacts:
            if entry.role == 'source':
                await call({'action': 'extract_source', 'artifact': {'sha256': entry.sha256, 'size_bytes': entry.size_bytes},
                            'destination': str(Path(entry.remote_destination).parent)})
    await check_fence()
    return receipts


async def stage_cached_bundle(*, connection, bundle, progress=_noop, check_fence=_noop):
    """Stage eligible source/runtime leaves; executor retains inputs/support-python.

    Do NOT run the old full source/runtime rsync after this call. Preserve the
    existing support-python transfer, attempt staging, symlinks and final verify.
    """
    # Claim one fresh generation before any upload/materialization. A failed or
    # replayed stage must use a new attempt, never merge into an existing tree.
    attempt_id = str(uuid.UUID(bundle.attempt_id))
    generation = f'{connection.remote_root.rstrip("/")}/attempts/{attempt_id}/materialized'
    if (bundle.attempt_id != attempt_id
            or bundle.remote_source_dir != generation + '/source'
            or bundle.remote_runtime_dir != generation + '/runtime'):
        raise ValueError('Cache materialization paths must belong to this attempt')
    script = """import pathlib,sys
p=pathlib.Path(sys.argv[1])
if not p.is_absolute() or '..' in p.parts: raise RuntimeError('unsafe generation path')
q=pathlib.Path('/')
for part in p.parent.parts[1:]:
 q=q/part
 if q.is_symlink(): raise RuntimeError('unsafe generation path')
 q.mkdir(mode=0o700,exist_ok=True)
p.mkdir(mode=0o700,exist_ok=False)
(p/'source').mkdir(mode=0o700)
(p/'runtime').mkdir(mode=0o700)
"""
    await check_fence()
    await run_remote(connection, ['python3', '-c', script, generation])
    links = [{'artifact': {'sha256': record.sha256, 'size_bytes': record.size_bytes},
              'destination': bundle.remote_runtime_dir.rstrip('/') + '/' + record.relative_path.removeprefix('runtime/'),
              'target': record.link_target}
             for record in bundle.envelope.files
             if record.role == 'runtime' and record.link_target is not None
             and record.relative_path.startswith('runtime/')
             and record.relative_path != 'runtime/support-python'
             and not record.relative_path.startswith('runtime/support-python/')]
    return await _cache_artifacts(connection=connection, artifacts=cache_transfer_artifacts(bundle),
                                  operation_id=bundle.attempt_id, progress=progress,
                                  check_fence=check_fence, materialize=True,
                                  links=links, runtime_root=bundle.remote_runtime_dir)


def _prewarm_plan(job, command, source_revision, source_tree, directory, *, native_invocation):
    repo = get_code_root().resolve()
    if current_source_identity(repo) != (source_revision, source_tree):
        raise ValueError('Prewarm source identity does not match current committed source')
    identity = native_invocation.source_identity
    if identity is None or (identity.revision, identity.tree) != (source_revision, source_tree):
        raise ValueError('Prewarm source identity does not match current committed source')
    _, effective = compile_remote_dependencies(str(job.model_id), str(job.mode), command,
                                                native_invocation=native_invocation)
    archive = directory / 'source.tar'
    with archive.open('wb') as stream:
        subprocess.run(['git', 'archive', '--format=tar', source_revision], cwd=repo,
                       stdout=stream, stderr=subprocess.PIPE, check=True, timeout=300)
    source = directory / 'source'
    _safe_extract(archive, source)
    archive.replace(source / '.bms-source.tar')

    entries = []
    assets = [(source / '.bms-source.tar', 'source/.bms-source.tar')]
    assets.extend((path, 'runtime/' + relative) for path, relative in
                  _runtime_assets(str(job.model_id), str(job.mode), effective,
                                  native_invocation=native_invocation)
                  if relative != 'support-python')
    for path, prefix in assets:
        if prefix.startswith('runtime/') and _is_runtime_image(path, prefix):
            path = path.resolve()
        for record in _records_for_source(path, prefix, 'source' if prefix.startswith('source/') else 'runtime'):
            if record.link_target is not None:
                continue
            suffix = record.relative_path[len(prefix):].lstrip('/')
            local = path / suffix if suffix else path
            entries.append(CacheTransferArtifact(local, record.relative_path, record.sha256,
                                                  record.size_bytes, record.mode,
                                                  'source' if prefix.startswith('source/') else
                                                  'image' if _is_runtime_image(local, record.relative_path) else 'runtime'))
    verify_selected_preparation_inputs(native_invocation.execution_plan)
    verify_selected_runtime_hashes(native_invocation.execution_plan,
        {entry.remote_destination.removeprefix('runtime/'): entry.sha256 for entry in entries})
    return entries


def independent_plan(selection):
    """Resolve only reviewed registry dependencies; no Job or biological inputs."""
    from model_registry import model_runtime_dependencies
    from paths import get_container_dir, get_weights_root
    entries = []
    for ref in model_runtime_dependencies(selection.model_id):
        if selection.kind == 'image' and ref.kind != 'image':
            continue
        root = (get_container_dir() if ref.kind == 'image' else get_weights_root()).resolve()
        path = (resolve_image(ref.relative_path, root) if ref.kind == 'image'
                else root / ref.relative_path)
        if (path.is_symlink() or (ref.kind != 'image' and not path.resolve().is_relative_to(root))):
            raise ValueError('Independent runtime asset is not a contained regular asset')
        prefix = ('containers/' if ref.kind == 'image' else 'weights/') + ref.relative_path
        for record in _records_for_source(path, prefix, 'runtime'):
            # Cache-only tree links are not installed; reject rather than claim
            # an incomplete model download. Launch's existing link path is unchanged.
            if record.link_target is not None:
                raise ValueError('Independent provisioning does not support runtime symlinks')
            suffix = record.relative_path[len(prefix):].lstrip('/')
            entries.append(CacheTransferArtifact(path / suffix if suffix else path,
                record.relative_path, record.sha256, record.size_bytes, record.mode,
                'image' if ref.kind == 'image' else 'runtime'))
    return entries


def validate_workflow_provision_authority(params):
    """Runtime acquisition is server-owned; typed biological references are not.

    The scientific compiler still owns unknown/model-specific scientific keys.
    Inspect key names, not path-looking values or input-reference sha256 fields.
    """
    from .images import IMAGE_SELECTORS
    forbidden = {flag for flag, _ in IMAGE_SELECTORS.values()} | {
        'command', 'argv', 'shell', 'environment', 'container', 'container_path',
        'container_dir', 'image_path', 'image_digest', 'image_sha256', 'runtime_sif',
        'runtime_image_store', 'runtime_assets', 'runtime_lock', 'runtime_root',
        'weights_path', 'weights_dir', 'weights_root', 'checkpoint_path', 'repo_path',
        'code_root', 'data_root', 'work_dir', 'out_dir', 'out', 'output_dir',
        'cm_api_runtime_dir', 'msa_cache_dir', 'acquisition_url', 'acquisition_command',
    }
    suffixes = ('_container_path', '_runtime_sif', '_repo_path', '_checkpoint_path',
                '_weights_path', '_weights_dir', '_runtime_lock', '_runtime_root',
                '_image_digest', '_image_sha256', '_acquisition_url')
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden or str(key).endswith(suffixes):
                    raise ValueError('Workflow provisioning runtime acquisition is server-owned')
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(params)


def workflow_plan(selection, *, compiled_plan=None):
    """Bind an authorized shared plan; inspect runtime leaves, never inputs."""
    from services.nextflow import compile_workflow_provision_request
    from schemas import JobCreate
    invocation = None
    if compiled_plan is None:
        if not isinstance(selection.workflow_request, JobCreate):
            raise ValueError('Native workflow requires fresh authenticated compilation')
        validate_workflow_provision_authority(selection.workflow_request.params)
        invocation = compile_workflow_provision_request(selection.workflow_request)
        plan = invocation.execution_plan
    else:
        plan = compiled_plan
    source = current_source_identity()
    identity = plan.source_identity if plan is not None else None
    if identity is None or (identity.revision, identity.tree) != source:
        raise ValueError('Workflow provision source identity changed')
    if plan is None or not plan.dependency_closure_complete:
        raise ValueError('Workflow dependency closure is unresolved')
    entries = []
    asset_kwargs = dict(include_support=False, native_invocation=invocation)
    if compiled_plan is not None:
        asset_kwargs['selected_plan'] = plan
    for path, prefix in _runtime_assets(plan.model_id, plan.mode,
            json.loads(plan.native_parameters_json), **asset_kwargs):
        for record in _records_for_source(path, prefix, 'runtime'):
            if record.link_target is not None:
                raise ValueError('Managed provisioning does not support runtime symlinks')
            suffix = record.relative_path[len(prefix):].lstrip('/')
            local = path / suffix if suffix else path
            entries.append(CacheTransferArtifact(local, record.relative_path, record.sha256,
                record.size_bytes, record.mode, 'image' if _is_runtime_image(local, record.relative_path) else 'runtime'))
    verify_selected_runtime_hashes(plan, {entry.remote_destination: entry.sha256 for entry in entries})
    verify_selected_preparation_inputs(plan)
    return entries, plan


def independent_preview(selection, target, *, compiled_plan=None):
    from .contracts import ProvisionPreview, CachedArtifactReceipt
    plan = None
    if selection.kind == 'workflow':
        entries, plan = workflow_plan(selection, compiled_plan=compiled_plan)
    else:
        entries = independent_plan(selection)
    # A shared dependency may occur in many components, but one destination has
    # exactly one immutable identity. Do not hide conflicting selected assets.
    unique = {}
    for entry in entries:
        previous = unique.setdefault(entry.remote_destination, entry)
        if (previous.sha256, previous.size_bytes, previous.mode, previous.role) != (
                entry.sha256, entry.size_bytes, entry.mode, entry.role):
            raise ValueError('Conflicting managed dependency destinations')
    entries = list(unique.values())
    artifacts = [dict(name=e.remote_destination, sha256=e.sha256, size_bytes=e.size_bytes) for e in entries]
    source = current_source_identity()
    if plan is not None and source != (plan.source_identity.revision, plan.source_identity.tree):
        raise ValueError('Workflow provision source identity changed')
    identity = dict(scope='managed_asset_activation.v1', selection=selection.model_dump(mode='json'),
        target=[target.id, target.host, target.port, target.username, target.remote_root, target.host_key_sha256],
        source=source, artifacts=artifacts, modes=[e.mode for e in entries])
    if plan is not None:
        identity['plan_sha256'] = plan.plan_sha256
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    observed = getattr(target, 'managed_inventory', None)
    inventory_state = observed.state if observed is not None else 'unobserved'
    known = {}
    if inventory_state == 'current':
        for release in observed.releases:
            for row in release.artifacts:
                key = (row.name, row.sha256, row.size_bytes)
                # Conflicting observations cannot certify readiness.
                if key in known and known[key] != row.state:
                    known[key] = 'unknown'
                else:
                    known[key] = row.state
    states = [dict(row, state=known.get((row['name'], row['sha256'], row['size_bytes']), 'unknown'))
              for row in artifacts]
    objects = {(e.role == 'image', e.sha256, e.size_bytes) for e in entries}
    missing = {(e.role == 'image', e.sha256, e.size_bytes) for e, row in zip(entries, states, strict=True)
               if row['state'] != 'verified'}
    return ProvisionPreview(selection=selection, preview_sha256=digest,
        artifacts=[CachedArtifactReceipt.model_validate(r) for r in artifacts],
        total_bytes=sum(e.size_bytes for e in entries),
        destination=dict(target_id=target.id, remote_root=target.remote_root),
        effective_params=json.loads(plan.effective_json) if plan else None,
        plan_sha256=plan.plan_sha256 if plan else None,
        asset_states=states, inventory_state=inventory_state,
        transfer_bytes=sum(size for _, _, size in missing),
        # Conservative space estimate: one immutable cache object plus each
        # non-image installed destination. Images are never copied into releases.
        storage_bytes=sum(size for _, _, size in objects) + sum(e.size_bytes for e in entries if e.role != 'image')), entries


async def provision_cache(*, connection, entries, operation_id, progress, check_fence):
    entries = tuple(entries)
    receipts = await _cache_artifacts(connection=connection, artifacts=entries,
        operation_id=operation_id, progress=progress, check_fence=check_fence, track_artifacts=True)
    # Read back exact installed cache-object identities after all transfers. This
    # is download evidence, NOT a materialized runtime or scientific acceptance.
    tool = await _install_helper(connection, check_fence)
    for offset in range(0, len(receipts), 128):
        batch = receipts[offset:offset + 128]
        await check_fence()
        response = await run_remote(connection, ['python3', tool, '--root',
            f'{connection.remote_root}/cache/artifacts/v1'], input_bytes=json.dumps({
                'action': 'probe', 'artifacts': [dict(sha256=r['sha256'], size_bytes=r['size_bytes'],
                    **({'kind': 'runtime_image'} if entry.role == 'image' else {}))
                    for r, entry in zip(batch, entries[offset:offset + 128], strict=True)]
            }).encode(), timeout=3600)
        rows = json.loads(response.stdout)['artifacts']
        expected = {(r['sha256'], r['size_bytes']) for r in batch}
        observed = {(r['sha256'], r['size_bytes']) for r in rows if r['state'] == 'cache_hit'}
        if expected != observed or any(r['state'] != 'cache_hit' for r in rows):
            raise ValueError('Cache source verification failed')
    await check_fence()
    return receipts


async def prewarm_cache(*, connection, job, command, source_revision, source_tree,
                        operation_id, progress, check_fence, native_invocation):
    """Only source/runtime: no input admission, envelope creation or scientific run."""
    await check_fence()
    with tempfile.TemporaryDirectory(prefix='bms-prewarm-') as temporary:
        plan_task = asyncio.create_task(asyncio.to_thread(_prewarm_plan, job, command, source_revision,
                                           source_tree, Path(temporary),
                                           native_invocation=native_invocation))
        try:
            entries = await asyncio.shield(plan_task)
        except asyncio.CancelledError:
            # The hashing/archive writer must stop before its directory closes.
            await plan_task
            raise
        receipts = await _cache_artifacts(connection=connection, artifacts=entries,
                                          operation_id=operation_id, progress=progress,
                                          check_fence=check_fence, track_artifacts=True)
    return {'source_revision': source_revision, 'source_tree': source_tree, 'artifacts': receipts,
            'excluded': [{'name': 'runtime/support-python', 'reason': 'destination-dependent relocation at launch'}]}
