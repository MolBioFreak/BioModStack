"""Shared artifact cache transport for bundle launch and explicit saved-Job prewarm."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import stat
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
from . import hf_assets


BATCH_COUNT = 2048
BATCH_BYTES = 256 * 1024 * 1024
HF_MIN_BYTES = 8 * 1024 * 1024


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
        views = lifecycle.with_name('runtime_image_views.py')
        payloads[views.name] = views.read_bytes()
        payloads['bms_hf_transfer.py'] = (Path(__file__).parents[2] / 'tools/bms_hf_transfer.py').read_bytes()
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
        await check_fence()
    return destination


async def _cache_artifacts(*, connection, artifacts, operation_id, progress, check_fence,
                           materialize=False, links=(), runtime_root=None, track_artifacts=False, helper=None):
    # Caller owns operation identity and destination authority; never use public paths.
    uuid.UUID(operation_id)
    artifacts = tuple(artifacts)
    unique = {}
    for entry in artifacts:
        previous = unique.setdefault((entry.role == 'image', entry.sha256), entry)
        if previous.size_bytes != entry.size_bytes:
            raise ValueError('Conflicting cache object sizes')
    tool = helper or await _install_helper(connection, check_fence)
    root = f'{connection.remote_root}/cache/artifacts/v1'
    async def call(request):
        await check_fence()
        result = await run_remote(connection, ['python3', tool, '--root', root],
                                  input_bytes=json.dumps(request).encode(), timeout=3600)
        await check_fence()
        return json.loads(result.stdout)
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
    objects = tuple(unique.values())
    for offset in range(0, len(objects), BATCH_COUNT):
        batch = objects[offset:offset + BATCH_COUNT]
        await report('checking', None, 'Verifying cached artifact batch')
        response = await call({'action': 'probe', 'artifacts': [identity(entry) for entry in batch]})
        states.update({(row.get('kind') == 'runtime_image', row['sha256']): row['state']
                       for row in response['artifacts']})
    # HF is a byte source, not a second registry. Verified worker hits remain
    # offline; selected bulk misses alone consult controller-owned cloud config.
    # Keep small support files batched rather than doing thousands of HTTP calls.
    bulk = {key(e) for e in objects if states[key(e)] != 'cache_hit'
            and e.role in {'image', 'runtime', 'source'} and e.size_bytes >= HF_MIN_BYTES}
    hf_enabled = bool(bulk) and hf_assets.configuration() is not None
    indices = {}
    for index, entry in enumerate(artifacts):
        indices.setdefault(key(entry), []).append(index)
    async def batch_progress(batch, state):
        for entry in batch:
            for index in indices[key(entry)]:
                activity[index]['state'] = state
        # One operation update carries every artifact's state. Thousands of
        # identical DB/fence updates would reintroduce per-file setup latency.
        await report(state, activity[indices[key(batch[0])][0]]['name'] if len(batch) == 1 else None,
                     'Transferring artifact batch' if state == 'transferring'
                     else 'Verifying and publishing artifact batch')

    async def transfer(batch, *, direct=False, use_hf=False):
        batch_id = uuid.uuid4().hex
        owner = {'operation_id': operation_id, 'batch_id': batch_id}
        incoming = f'{root}/incoming/{operation_id}/{batch_id}'
        await call({'action': 'prepare_incoming', **owner})
        await batch_progress(batch, 'transferring')
        if direct:
            # Images and oversized single objects never acquire task-local copies.
            entry = batch[0]
            source = incoming + '/' + entry.sha256
            await check_fence()
            if use_hf:
                name = activity[indices[key(entry)][0]]['name']
                acquired = {}
                for renewal in range(2):
                    await report('transferring', name, 'Preparing private Hugging Face asset delivery')
                    sources = await hf_assets.prepare_sources([entry], check_fence=check_fence)
                    await report('transferring', name, 'Downloading artifact from Hugging Face')
                    acquired = await call({'action': 'acquire_hf', 'artifact': identity(entry),
                                           **owner, 'source': sources[key(entry)]})
                    # A finite fresh-link retry is not a route fallback. Neither
                    # expiry nor cancellation publishes an unverified partial.
                    if acquired.get('state') != 'source_expired':
                        break
                if (acquired.get('state') != 'downloaded'
                        or acquired.get('sha256') != entry.sha256
                        or acquired.get('size_bytes') != entry.size_bytes):
                    raise ValueError('Hugging Face artifact acquisition did not verify')
            else:
                await rsync_to_remote(connection, entry.source, source, delete=False)
            await check_fence()
            await batch_progress(batch, 'verifying')
            await call({'action': 'ingest', 'artifact': identity(entry), 'source': source})
        else:
            with tempfile.TemporaryDirectory(prefix='bms-cache-batch-') as temporary:
                staging = Path(temporary)
                await check_fence()
                for entry in batch:
                    info = entry.source.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_size != entry.size_bytes:
                        raise ValueError('Cache source size or type changed')
                    destination = staging / entry.sha256
                    try:
                        os.link(entry.source, destination, follow_symlinks=False)
                    except OSError:
                        shutil.copyfile(entry.source, destination, follow_symlinks=False)
                    if destination.is_symlink() or not destination.is_file():
                        raise ValueError('Cache source is not a regular file')
                await check_fence()
                await rsync_to_remote(connection, staging, incoming + '/', delete=False)
                await check_fence()
            await batch_progress(batch, 'verifying')
            await call({'action': 'ingest_many', 'artifacts': [identity(e) for e in batch], **owner})
        # Never clean uncertain ingest/SSH/cancellation state; retry probes the CAS.
        await call({'action': 'remove_incoming', **owner})
        for entry in batch:
            states[key(entry)] = 'cache_hit'
            for index in indices[key(entry)]:
                activity[index]['state'] = 'verified'

    batch, size = [], 0
    for entry in objects:
        if states[key(entry)] == 'cache_hit':
            continue
        use_hf = hf_enabled and key(entry) in bulk
        direct = use_hf or entry.role == 'image' or entry.size_bytes > BATCH_BYTES
        if batch and (direct or len(batch) >= BATCH_COUNT or size + entry.size_bytes > BATCH_BYTES):
            await transfer(batch)
            batch, size = [], 0
        if direct:
            await transfer([entry], direct=True, use_hf=use_hf)
        else:
            batch.append(entry)
            size += entry.size_bytes
    if batch:
        await transfer(batch)
    receipts = []
    for index, entry in enumerate(artifacts):
        name = activity[index]['name']
        receipts.append({'name': name, 'sha256': entry.sha256, 'size_bytes': entry.size_bytes})
        activity[index]['state'] = 'verified'
    if track_artifacts:
        await report('verifying', None, 'Artifact cache identities verified')
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
    helper = await _install_helper(connection, check_fence)
    transferable = cache_transfer_artifacts(bundle)
    receipts = []
    if bundle.weight_layout:
        # The layout travels by reference to the runtime listing this bundle
        # already carries and the envelope already authenticates; its production
        # row count exceeds the helper's declared request budget. Stage and
        # materialize that one document first, then let the helper re-verify its
        # digest, schema and placement before it is used.
        listing_destination = bundle.remote_runtime_dir.rstrip('/') + '/.bms-runtime-images.json'
        listing = next((entry for entry in transferable
                        if entry.remote_destination == listing_destination), None)
        if listing is None:
            raise ValueError('Weight layout listing is unavailable for this bundle')
        receipts = await _cache_artifacts(connection=connection, artifacts=(listing,),
            operation_id=bundle.attempt_id, progress=progress, check_fence=check_fence,
            materialize=True, helper=helper, runtime_root=bundle.remote_runtime_dir)
        transferable = tuple(entry for entry in transferable
                             if entry.remote_destination != listing_destination)
        layout = {'path': listing_destination, 'sha256': listing.sha256}
        async def weights(action):
            await check_fence()
            result = await run_remote(connection, ['python3', helper, '--root',
                connection.remote_root + '/cache/artifacts/v1'],
                input_bytes=json.dumps(dict(action=action, layout=layout)).encode(), timeout=3600)
            await check_fence()
            response = json.loads(result.stdout)
            if response.get('root') != bundle.envelope.environment['BMS_WEIGHTS']:
                raise ValueError('Shared weight placement identity mismatch')
            return response
        await progress(dict(phase='checking', artifact=None, message='Resolving installed model weights'))
        observed = await weights('weights_probe')
        if observed['state'] == 'missing':
            receipts += await _cache_artifacts(connection=connection, artifacts=bundle.runtime_weights,
                operation_id=bundle.attempt_id, progress=progress, check_fence=check_fence, helper=helper)
            if (await weights('weights_install'))['state'] != 'ready':
                raise ValueError('Shared model weights were not installed')
        elif observed['state'] != 'ready':
            raise ValueError('Shared model weights are damaged')
        else:
            receipts += [dict(name=e.remote_destination.removeprefix(connection.remote_root + '/'),
                              sha256=e.sha256, size_bytes=e.size_bytes) for e in bundle.runtime_weights]
    receipts += await _cache_artifacts(connection=connection, artifacts=transferable,
                                      operation_id=bundle.attempt_id, progress=progress,
                                      check_fence=check_fence, materialize=True, helper=helper,
                                      links=links, runtime_root=bundle.remote_runtime_dir)
    return receipts


def _prewarm_plan(job, command, source_revision, source_tree, directory, *, native_invocation):
    repo = get_code_root().resolve()
    if current_source_identity(repo) != (source_revision, source_tree):
        raise ValueError('Prewarm source identity does not match current committed source')
    identity = native_invocation.source_identity
    if identity is None or (identity.revision, identity.tree) != (source_revision, source_tree):
        raise ValueError('Prewarm source identity does not match current committed source')
    _, effective = compile_remote_dependencies(str(job.model_id), str(job.mode), command,
                                                native_invocation=native_invocation)
    archive = directory / 'source.tar.gz'
    with archive.open('wb') as stream:
        subprocess.run(['git', 'archive', '--format=tar.gz', '-6', source_revision], cwd=repo,
                       stdout=stream, stderr=subprocess.PIPE, check=True, timeout=300)
    source = directory / 'source'
    _safe_extract(archive, source)
    archive.replace(source / '.bms-source.tar.gz')

    entries = []
    assets = [(source / '.bms-source.tar.gz', 'source/.bms-source.tar.gz')]
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
    from model_registry import model_runtime_dependencies, model_image_dependencies
    from paths import get_container_dir, get_weights_root
    entries = []
    refs = (model_image_dependencies(selection.model_id) if selection.kind == "image"
            else model_runtime_dependencies(selection.model_id))
    for ref in refs:
        if selection.kind == 'image' and ref.kind != 'image':
            continue
        root = (get_container_dir() if ref.kind == 'image' else get_weights_root()).resolve()
        path = (resolve_image(ref.relative_path, root) if ref.kind == 'image'
                else root / ref.relative_path)
        if (path.is_symlink() or (ref.kind != 'image' and not path.resolve().is_relative_to(root))):
            raise ValueError('Independent runtime asset is not a contained regular asset')
        prefix = ('containers/' if ref.kind == 'image' else 'weights/') + ref.relative_path
        for record in _records_for_source(path, prefix, 'runtime'):
            suffix = record.relative_path[len(prefix):].lstrip('/')
            entries.append(CacheTransferArtifact(path / suffix if suffix else path,
                record.relative_path, record.sha256, record.size_bytes, record.mode,
                'image' if ref.kind == 'image' else 'runtime', link_target=record.link_target))
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

    for path, prefix in _runtime_assets(plan.model_id, plan.mode,
            json.loads(plan.native_parameters_json), include_support=False, selected_plan=plan):
        for record in _records_for_source(path, prefix, 'runtime'):
            suffix = record.relative_path[len(prefix):].lstrip('/')
            local = path / suffix if suffix else path
            entries.append(CacheTransferArtifact(local, record.relative_path, record.sha256,
                record.size_bytes, record.mode, 'image' if _is_runtime_image(local, record.relative_path) else 'runtime',
                link_target=record.link_target))
    verify_selected_runtime_hashes(plan, {entry.remote_destination: entry.sha256 for entry in entries})
    verify_selected_preparation_inputs(plan)
    return entries, plan


def independent_preview(selection, target, *, compiled_plan=None):
    from .contracts import ProvisionPreview, CachedArtifactReceipt
    from model_registry import model_runtime_dependencies, model_image_dependencies
    from .bundle import RemoteBundleError
    plan = compiled_plan
    invocation = None
    dependencies, blockers = [], []
    if selection.kind == 'workflow':
        if plan is None:
            from schemas import JobCreate
            from services.nextflow import compile_workflow_provision_request
            if not isinstance(selection.workflow_request, JobCreate):
                raise ValueError('Native workflow requires fresh authenticated compilation')
            validate_workflow_provision_authority(selection.workflow_request.params)
            invocation = compile_workflow_provision_request(selection.workflow_request)
            plan = invocation.execution_plan
        prefixes = {'image': 'containers', 'weights': 'weights', 'database': 'data',
                    'reference_database': 'data', 'runtime_data': 'data'}
        dependencies = [dict(name=(prefixes[d.kind] + '/' + d.relative_path
                                  if d.kind in prefixes and d.relative_path else d.logical_id), kind=d.kind)
                        for d in plan.dependencies]
    else:
        try:
            refs = (model_image_dependencies(selection.model_id) if selection.kind == 'image'
                    else model_runtime_dependencies(selection.model_id))
            dependencies = [dict(name=('containers/' if r.kind == 'image' else 'weights/') + r.relative_path,
                                 kind=r.kind) for r in refs]
        except ValueError:
            blockers = ['binding_unavailable: Use the typed workflow form to prepare its selected dependencies; '
                        'this independent selection has no reviewed binding.']
    entries = []
    if not blockers:
        try:
            if selection.kind == 'workflow':
                entries, plan = workflow_plan(selection, compiled_plan=plan)
            else:
                entries = independent_plan(selection)
        except (RemoteBundleError, ValueError, OSError) as exc:
            # Project only known preparation failures, never raw exception paths,
            # biological inputs or arbitrary compiler diagnostics. Other failures
            # retain the existing error route and cannot grant start authority.
            message = str(exc)
            if isinstance(exc, FileNotFoundError) or message.startswith((
                    'Required package path is unavailable:', 'Required package directory is empty:',
                    'Required runtime asset is unavailable:', 'Required source dependency is unavailable:')):
                # Match only server-declared logical names; never reflect the
                # arbitrary absolute path carried by filesystem exceptions.
                unavailable = [d['name'] for d in dependencies if message.endswith('/' + d['name'].split('/', 1)[-1])]
                if plan is not None:
                    unavailable.extend(d.logical_id for d in plan.dependencies if message.endswith(': ' + d.logical_id))
                detail = (' Unavailable: ' + ', '.join(sorted(set(unavailable))) + '.') if unavailable else ''
                blockers = ['host_asset_unavailable:' + detail + ' Install the selected assets on the host using managed setup '
                            '(including any required license), then preview again. Worker/backend presence is separate.']
            elif message.startswith(('Workflow dependency closure is unresolved',
                    'Selected dependency closure is incomplete:', 'Selected runtime dependency has no managed binding:')):
                blockers = ['dependency_binding_unresolved: The selected workflow lacks complete managed dependency '
                            'bindings. Resolve its dependency metadata through managed setup, then preview again.']
            else:
                raise
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
    if any(e.link_target is not None for e in entries):
        from .managed_inventory import manifest_for
        from tools import bms_managed_runtime, bms_artifact_cache
        bms_managed_runtime.validate_manifest(manifest_for(selection, entries, source), bms_artifact_cache)
    if plan is not None and source != (plan.source_identity.revision, plan.source_identity.tree):
        raise ValueError('Workflow provision source identity changed')
    identity = dict(scope='managed_asset_activation.v1', selection=selection.model_dump(mode='json'),
        target=[target.id, target.host, target.port, target.username, target.remote_root, target.host_key_sha256],
        source=source, artifacts=artifacts, modes=[e.mode for e in entries],
        dependencies=dependencies, blockers=blockers)
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
    objects = {(e.role == 'image', e.sha256, e.size_bytes) for e in entries if e.link_target is None}
    missing = {(e.role == 'image', e.sha256, e.size_bytes) for e, row in zip(entries, states, strict=True)
               if row['state'] != 'verified' and e.link_target is None}
    return ProvisionPreview(selection=selection, preview_sha256=digest,
        dependencies=dependencies, blockers=blockers, estimates_complete=not blockers,
        artifacts=[CachedArtifactReceipt.model_validate(r) for r in artifacts],
        total_bytes=sum(e.size_bytes for e in entries),
        destination=dict(target_id=target.id, remote_root=target.remote_root),
        effective_params=json.loads(plan.effective_json) if plan else None,
        plan_sha256=plan.plan_sha256 if plan else None,
        asset_states=states, inventory_state=inventory_state,
        transfer_bytes=sum(size for _, _, size in missing),
        # Conservative space estimate: one immutable cache object plus each
        # non-image installed destination. Images are never copied into releases.
        storage_bytes=sum(size for _, _, size in objects) + sum(e.size_bytes for e in entries if e.role != 'image' and e.link_target is None)), entries


async def provision_cache(*, connection, entries, operation_id, progress, check_fence):
    # Alias identities are carried by the release manifest, not byte objects.
    # Its existing authenticated link publisher runs after verified leaves.
    entries = tuple(e for e in entries if e.link_target is None)
    receipts = await _cache_artifacts(connection=connection, artifacts=entries,
        operation_id=operation_id, progress=progress, check_fence=check_fence, track_artifacts=True)
    # Read back exact installed cache-object identities after all transfers. This
    # is download evidence, NOT a materialized runtime or scientific acceptance.
    tool = await _install_helper(connection, check_fence)
    objects = tuple({(e.role == 'image', e.sha256): e for e in entries}.values())
    for offset in range(0, len(objects), BATCH_COUNT):
        batch = objects[offset:offset + BATCH_COUNT]
        await check_fence()
        response = await run_remote(connection, ['python3', tool, '--root',
            f'{connection.remote_root}/cache/artifacts/v1'], input_bytes=json.dumps({
                'action': 'probe', 'artifacts': [dict(sha256=e.sha256, size_bytes=e.size_bytes,
                    **({'kind': 'runtime_image'} if e.role == 'image' else {})) for e in batch]
            }).encode(), timeout=3600)
        await check_fence()
        rows = json.loads(response.stdout)['artifacts']
        expected = {(e.role == 'image', e.sha256, e.size_bytes) for e in batch}
        observed = {(r.get('kind') == 'runtime_image', r['sha256'], r['size_bytes'])
                    for r in rows if r['state'] == 'cache_hit'}
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
