"""Exact BC2 registered-document selections, independent of accepted Designs."""
from pathlib import Path
from fastapi import HTTPException
from database import JobArtifact


async def resolve_native_source(session, job_id: str, artifact_id: str) -> tuple[Path, dict]:
    from services.binder_continuation import resolve_root
    owner, root = await resolve_root(session, job_id)
    receipt = (owner.provenance or {}).get('bindcraft2_native_publication') or {}
    artifact = await session.get(JobArtifact, artifact_id)
    name = artifact.logical_path.removeprefix('bindcraft2/native/') if artifact else ''
    entry = receipt.get('files', {}).get(name)
    if (artifact is None or owner.model_id != 'bindcraft2' or entry is None
            or artifact.owner_job_id != owner.id or artifact.attempt != receipt.get('attempt')
            or receipt.get('attempt') != (owner.retry_count or 0)
            or receipt.get('remote_attempt_id') != owner.remote_attempt_id
            or artifact.sha256 != entry['sha256'] or artifact.bytes != entry['bytes']
            or artifact.media_type not in {'chemical/x-pdb', 'chemical/x-mmcif'}):
        raise HTTPException(404, 'Selected native structure not found')
    expected = Path(receipt['root']) / receipt.get('campaign_root', '.') / name
    if (Path(artifact.storage_path) != expected or Path(name).is_absolute() or '..' in Path(name).parts
            or not owner.output_dir or Path(owner.output_dir).absolute() != Path(receipt['root'])):
        raise ValueError('Selected native artifact path changed')
    document = (artifact.provenance or {}).get('native_document')
    if document is None:
        # Legacy identity remains selectable offline; absent annotations remain
        # unknown rather than requiring unrelated campaign diagnostics to exist.
        document = next((structure for binding in receipt.get('candidates', [])
                         for structure in binding['structures']
                         if structure['artifact_id'] == artifact_id),
                        {'path': name, 'sha256': artifact.sha256, 'native_rows': []})
    if document is None or document['sha256'] != artifact.sha256:
        raise ValueError('Selected native document identity changed')
    return Path(artifact.storage_path), {
        'artifact_id': artifact.id, 'artifact_sha256': artifact.sha256,
        'owner_job_id': owner.id, 'lineage_root_job_id': root.id,
        'source_parent_job_id': owner.parent_job_id,
        'publication_source': receipt.get('source'),
        'target_state': document.get('target_state'), 'producer_document': document}


async def resolve_native_sources(session, root, sources):
    result, seen = [], set()
    for source in sources:
        key = (source.job_id, source.artifact_id)
        if key in seen:
            raise ValueError('Duplicate native sources')
        seen.add(key)
        selected = await resolve_native_source(session, *key)
        if selected[1]['lineage_root_job_id'] != root.id:
            raise ValueError('Selected native source crosses the source-root boundary')
        result.append(selected)
    return result


def frustra_native_selections(selections):
    from services.frustrampnn.jobs import SourceSelection, _format_for_name, _read_owned_structure
    import hashlib
    result = []
    for path, identity in selections:
        payload = _read_owned_structure(path, label='Selected native structure')
        digest = hashlib.sha256(payload).hexdigest()
        if digest != identity['artifact_sha256']:
            raise ValueError('Selected native bytes changed')
        source_format, media_type, _ = _format_for_name(str(path))
        result.append(SourceSelection(design_id=None, source_job_id=identity['owner_job_id'],
            source_path=str(path), source_bytes=payload, source_sha256=digest,
            media_type=media_type, source_format=source_format, producer_stage='bindcraft2_native',
            producer_coordinates={'candidate_id': identity['artifact_id'],
                                  'selected_document': identity,
                                  'lineage_root_job_id': identity['lineage_root_job_id']}))
    return result
