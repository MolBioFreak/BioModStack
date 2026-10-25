"""Indexed RFD4 readback; never reparse coordinates for scalar pages."""
from __future__ import annotations
import copy
import json
from pathlib import Path

# One registration/readback seam; native leaves keep their distinct schemas.
PUBLICATIONS = {
    'rfd4_proteina': ('collected/rfd4_proteina', 'manifest.json', 'rfd4_proteina_v1'),
    'complexa': ('collected/complexa', 'complexa-results.json', 'complexa_v1'),
    'laproteina': ('collected/protein_cad_native', 'design_manifest.json', 'laproteina_native_v1'),
    'disco': ('collected/protein_cad_native', 'design_manifest.json', 'disco_native_v1'),
}
PUBLICATION_KEY = 'native_generation_publication'
RESULT_CONTRACT='rfd4_proteina_v1'
MANIFEST_RELATIVE_PATH='collected/rfd4_proteina/manifest.json'


def read_manifest(path):
    from services.ligandmpnn_interface_publication import regular_bytes
    document=json.loads(regular_bytes(Path(path)))
    if document.get('contract')!=RESULT_CONTRACT:raise ValueError('Not an RFD4 native publication')
    return document


def page_records(manifest,*,offset=0,limit=100,population=None):
    if offset<0 or limit<1:raise ValueError('Invalid result page')
    records=[r for r in manifest['records'] if population is None or r['population']==population]
    return {'contract':RESULT_CONTRACT,'records':copy.deepcopy(records[offset:offset+limit]),
            'offset':offset,'limit':limit,'total':len(records),'has_more':offset+limit<len(records),
            'population':population,'population_completeness':manifest['population_completeness'],
            'execution_status':manifest['execution_status'],'observation_errors':manifest.get('observation_errors',[])}


def artifact_members(manifest):
    """Existing ingester registers these exact relative paths with JobArtifact once."""
    for member in manifest['artifacts']:
        path=Path(member['path'])
        if path.is_absolute() or '..' in path.parts:raise ValueError('Publication member escapes native root')
        yield copy.deepcopy(member)


def exact_document_record(manifest,example_id,document):
    matches=[r for r in manifest['records'] if r['example_id']==example_id and r['document']==document]
    if len(matches)!=1:raise ValueError('Native example/document does not identify exactly one record')
    return copy.deepcopy(matches[0])


def _relative(value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or '\\' in value or path.as_posix() != value:
        raise ValueError('Invalid native publication member')
    return path


def publication_model(job):
    """Resolve producer identity independently of the parent workflow identity."""
    params = job.params or {}
    if job.model_id == 'protein_modification_experimental' and job.mode == 'de_novo_design':
        return params.get('generator_model_id') or params.get('generator') or job.model_id
    if job.model_id == 'protein_cad_experimental':
        return params.get('backend') or params.get('pcad_backend') or 'disco'
    return job.model_id


def has_native_publication(job, output_path=None):
    model = publication_model(job)
    if model not in PUBLICATIONS:
        return False
    if model in {'laproteina', 'disco'}:
        # Old Jobs retain their historical PDB/Design receiving path. Presence
        # of the canonical producer index, not a version guess, selects migration.
        return bool((job.provenance or {}).get(PUBLICATION_KEY)) or (
            output_path is not None and (Path(output_path) / PUBLICATIONS[model][0] / PUBLICATIONS[model][1]).is_file())
    return True


def _cad_manifest(native, raw, model):
    """Adapt native CAD rows without projecting CIF/sequence outputs to PDBs."""
    from services.bindcraft2_publication import _regular
    rows = json.loads(raw)
    status_name = f'{model}_publication.json'
    _, status_raw = _regular(native, status_name)
    status = json.loads(status_raw)
    if status.get('schema') != PUBLICATIONS[model][2]:
        raise ValueError('Native CAD publication producer disagrees')
    records = []
    for row in rows:
        metadata = {}
        if row.get('metadata_path'):
            _, metadata_raw = _regular(native, _relative(row['metadata_path']).as_posix())
            metadata = json.loads(metadata_raw)
        records.append({**{k: copy.deepcopy(v) for k, v in row.items() if k != 'design_id'},
            'native_output_id': row.get('design_id'), 'document': row.get('structure_path'),
            'population': 'sample', 'native_record': metadata,
            'metrics': metadata.get('native_summary_confidence') or {},
            'sequence': row.get('sequence') or metadata.get('designed_sequence')})
    # The canonical directory is the native retained tree, including observations,
    # trajectories and partial/unpaired documents; never scan the historical PDB projection.
    artifacts = [{'path': p.relative_to(native).as_posix()} for p in sorted(native.rglob('*'))
                 if (p.is_file() or p.is_symlink()) and p != native / 'design_manifest.json']
    return {'contract': PUBLICATIONS[model][2], 'model_id': model, 'records': records,
            'artifacts': artifacts, 'receipt': status,
            'execution_status': 'failed' if status.get('execution_failed') else 'completed',
            'population_completeness': 'partial' if status.get('execution_failed') else None,
            'observation_errors': status.get('diagnostics', []),
            'emitted_sample_count': status.get('emitted_sample_count')}


def _records(manifest, model):
    """Iterate immutable snapshot rows; copy only at the returned-page boundary."""
    if model in {'laproteina', 'disco'}:
        yield from manifest['records']
    elif model == 'rfd4_proteina':
        yield from manifest['records']
        for event in manifest.get('events', []):
            if event.get('event') != 'native_writer':
                yield {**event, 'population': event.get('population', 'event')}
    else:
        for row in manifest['documents']:
            yield dict(row, document=row['path'], population=row['role'])
        for row in manifest['observations']:
            yield dict(row, population='observation', metrics=row['values'])


async def publish_native_generation(session, job, output_path):
    """Register sealed native inventory once, without Design creation or writers.

    The registration snapshot supports scalar paging without reopening any
    coordinate. Selected acquisition still verifies the exact native bytes.
    """
    import hashlib
    import uuid
    from sqlalchemy import select
    from database import JobArtifact
    from services.bindcraft2_publication import _regular
    from services.binder_publication_roots import publication_root
    from services.binder_source_materialization import structure_suffix

    model = publication_model(job)
    prefix, name, contract = PUBLICATIONS[model]
    root = publication_root(job, output_path)
    native = root / prefix
    _, raw = _regular(native, name)
    manifest = (_cad_manifest(native, raw, model) if model in {'laproteina', 'disco'} else json.loads(raw))
    if manifest.get('contract') != contract or manifest.get('model_id') != model:
        raise ValueError('Native publication producer disagrees')
    attempt = job.retry_count or 0
    inventory = {}
    for member in [*manifest['artifacts'], {'path': name}]:
        relative = _relative(member['path']).as_posix()
        if relative in inventory:
            raise ValueError('Duplicate native publication member')
        _, data = _regular(native, relative) if relative != name else (None, raw)
        digest = hashlib.sha256(data).hexdigest()
        if member.get('sha256', digest) != digest or member.get('size', member.get('size_bytes', len(data))) != len(data):
            raise ValueError('Native publication member changed')
        inventory[relative] = {'sha256': digest, 'bytes': len(data)}
    receipt = {'schema': 'bms.native-generation-publication.v1', 'model_id': job.model_id,
               'contract': contract, 'attempt': attempt, 'remote_attempt_id': job.remote_attempt_id,
               'manifest_sha256': inventory[name]['sha256'], 'files': inventory}
    previous = (job.provenance or {}).get(PUBLICATION_KEY)
    if previous is not None and previous != receipt:
        raise ValueError('Native generation replay changed')
    existing = list((await session.scalars(select(JobArtifact).where(
        JobArtifact.owner_job_id == job.id, JobArtifact.attempt == attempt,
        JobArtifact.logical_path.startswith(prefix + '/')))).all())
    owned = {a.logical_path.removeprefix(prefix + '/'): a for a in existing}
    if previous is not None and set(owned) != set(inventory):
        raise ValueError('Native publication artifact inventory changed')
    artifacts = {}
    records_by_document = {}
    for row in _records(manifest, model):
        records_by_document.setdefault(row.get('document'), []).append(row)
    for relative, info in inventory.items():
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, f'bms:native:{job.id}:{attempt}:{prefix}/{relative}'))
        matching_records = records_by_document.get(relative, [])
        declared_roles = {key: matching_records[0][key] for key in ('binder_chains', 'target_chains', 'target_state')
                          if len(matching_records) == 1 and key in matching_records[0]}
        provenance = {'model_id': job.model_id, 'native_document': {
            **declared_roles, 'path': relative, 'sha256': info['sha256'], 'producer_model_id': model}}
        if relative == name:
            provenance['native_generation_manifest'] = manifest
            provenance['publication'] = receipt
        suffix = structure_suffix(Path(relative))
        media_type = 'chemical/x-pdb' if suffix == '.pdb' else 'chemical/x-mmcif' if '.cif' in suffix or '.mmcif' in suffix else 'application/octet-stream'
        expected = (str(native / relative), info['sha256'], info['bytes'], provenance)
        artifact = owned.get(relative)
        if artifact is not None:
            if (artifact.storage_path, artifact.sha256, artifact.bytes, artifact.provenance) != expected:
                raise ValueError('Registered native artifact changed')
        else:
            artifact = JobArtifact(id=identity, owner_job_id=job.id, attempt=attempt,
                logical_path=f'{prefix}/{relative}', storage_path=expected[0], sha256=info['sha256'],
                bytes=info['bytes'], media_type=media_type, provenance=provenance)
            session.add(artifact)
        artifacts[relative] = artifact
    job.provenance = {**(job.provenance or {}), PUBLICATION_KEY: receipt}
    await session.flush()
    return receipt


async def read_published_generation_page(job, session, *, offset=0, limit=100):
    """Read registered scalar snapshots; missing publication is not zero yield."""
    from sqlalchemy import select
    from database import JobArtifact
    if offset < 0 or not 1 <= limit <= 1000:
        raise ValueError('Invalid native page')
    model = publication_model(job)
    prefix, name, contract = PUBLICATIONS[model]
    owner = (
        JobArtifact.owner_job_id == job.id, JobArtifact.attempt == (job.retry_count or 0),
        JobArtifact.logical_path.startswith(prefix + '/'))
    index = await session.scalar(select(JobArtifact).where(*owner, JobArtifact.logical_path == prefix + '/' + name))
    receipt = (job.provenance or {}).get(PUBLICATION_KEY)
    if index is None or receipt is None:
        raise ValueError('Native publication is not registered')
    if (receipt != (index.provenance or {}).get('publication')
            or receipt['remote_attempt_id'] != job.remote_attempt_id
            or receipt['attempt'] != (job.retry_count or 0)
            or receipt['manifest_sha256'] != index.sha256):
        raise ValueError('Native publication identity changed')
    manifest = index.provenance['native_generation_manifest']
    records = []
    total = 0
    populations = {}
    observations = 0
    for position, raw in enumerate(_records(manifest, model)):
        total += 1
        population = raw.get('population', 'unreported')
        populations[population] = populations.get(population, 0) + 1
        if not raw.get('document') and not raw.get('sequence') and not raw.get('sequences'):
            observations += 1
        if offset <= position < offset + limit:
            records.append((position, raw))
    needed = {row.get('document') for _, row in records}
    needed.update(relation.get('document') for _, row in records
                  for relation in row.get('relations', []))
    from urllib.parse import quote
    from paths import to_allowed_relative
    downloads = {}
    owned = {}
    artifacts = []
    member_metadata = {member['path']: member for member in manifest.get('artifacts', [])}
    seen = set()
    # Verify the full custody inventory, but do not instantiate/copy every
    # JobArtifact (each index can contain large model-owned metadata). Stream
    # scalar columns in bounded DB batches; retain objects only for this page.
    inventory = await session.stream(select(JobArtifact.id, JobArtifact.logical_path,
        JobArtifact.storage_path, JobArtifact.sha256, JobArtifact.bytes).where(*owner).execution_options(yield_per=500))
    async for artifact in inventory:
        relative = artifact.logical_path.removeprefix(prefix + '/')
        info = receipt['files'].get(relative)
        if relative in seen or info is None or (artifact.sha256, artifact.bytes) != (info['sha256'], info['bytes']):
            raise ValueError('Native artifact identity changed')
        seen.add(relative)
        try:
            download = '/api/files/download/' + quote(to_allowed_relative(Path(artifact.storage_path)), safe='/')
        except ValueError:
            download = None
        artifacts.append({'path': artifact.logical_path, 'bytes': artifact.bytes, 'download_url': download,
                          'artifact_id': artifact.id, 'sha256': artifact.sha256,
                          'source_path': to_allowed_relative(Path(artifact.storage_path)) if download else None,
                          'role': member_metadata.get(relative, {}).get('role'),
                          'relations': copy.deepcopy(member_metadata.get(relative, {}).get('relations', []))})
        if relative in needed:
            owned[relative] = artifact
            downloads[artifact.id] = download
    if seen != set(receipt['files']):
        raise ValueError('Native publication identity changed')
    result = []
    for position, raw in records:
        row = copy.deepcopy(raw)
        # The event ordinal is scoped to the immutable publication, never used
        # to pair a metric with a neighboring document or candidate.
        key = f'{index.id}:{position}'
        document = owned.get(row.get('document'))
        structures = []
        if document is not None:
            structures = [{'artifact_id': document.id, 'logical_path': document.logical_path,
                'sha256': document.sha256, 'primary': True,
                'download_url': downloads[document.id]}]
            ref = {'kind': 'native_document', 'job_id': job.id, 'artifact_id': document.id}
        elif isinstance(row.get('sequence'), str) and row['sequence']:
            ref = {'kind': 'native_sequence', 'job_id': job.id,
                   'publication_ref': index.id, 'native_output_id': key}
        else:
            ref = {'kind': 'native_observation', 'job_id': job.id,
                   'publication_ref': index.id, 'native_record_id': key}
        for relation in row.get('relations', []):
            related = owned.get(relation.get('document'))
            if related is not None:
                relation.update(artifact_id=related.id, sha256=related.sha256,
                                download_url=downloads[related.id])
                from services.binder_source_materialization import structure_suffix
                if related.id not in {item['artifact_id'] for item in structures} and relation['role'] in {'input', 'refold', 'trajectory'} and structure_suffix(Path(related.logical_path)) in {'.pdb', '.cif', '.mmcif', '.bcif', '.cif.gz', '.mmcif.gz', '.cif.zst', '.mmcif.zst'}:
                    structures.append({'artifact_id': related.id, 'logical_path': related.logical_path,
                        'sha256': related.sha256, 'download_url': downloads[related.id],
                        'primary': False, 'relation_role': relation['role'],
                        'producer_field': relation['producer_field']})
        row.update(candidate_key=key, candidate_ref=ref, structures=structures)
        row['record_kind'] = 'document' if row.get('document') else 'sequence' if row.get('sequence') or row.get('sequences') else 'observation'
        result.append(row)
    return {'receipt': copy.deepcopy(manifest.get('receipt', {})), 'publication': copy.deepcopy(receipt),
            'model_id': model, 'owner_model_id': job.model_id, 'execution_status': job.status,
            'native_execution_status': manifest.get('execution_status'),
            'population_completeness': manifest.get('population_completeness'),
            'observation_errors': manifest.get('observation_errors', []),
            'missingness': manifest.get('missingness'), 'records': result,
            'offset': offset, 'limit': limit, 'total': total,
            'record_counts': {'total': total, 'observations': observations,
                              'molecular_records': total - observations, 'populations': populations},
            'native_request': copy.deepcopy(manifest.get('request')),
            'artifacts': artifacts}


async def read_publication_record(job, session, ref):
    """Exact body-less native selection for existing continuation receivers."""
    key = ref.get('native_output_id') or ref.get('native_record_id')
    try:
        publication, ordinal = key.rsplit(':', 1)
        offset = int(ordinal)
    except (AttributeError, ValueError) as exc:
        raise ValueError('Invalid native record identity') from exc
    if publication != ref.get('publication_ref') or ref.get('job_id') != job.id:
        raise ValueError('Native record publication owner differs')
    page = await read_published_generation_page(job, session, offset=offset, limit=1)
    if not page['records'] or page['records'][0]['candidate_ref'] != ref:
        raise ValueError('Native record identity differs from registered publication')
    return page['records'][0], page['publication']
