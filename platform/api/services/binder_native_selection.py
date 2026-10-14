"""Exact model-owned registered documents, independent of accepted Designs."""
from pathlib import Path
from fastapi import HTTPException
from database import JobArtifact


async def resolve_native_source(session, job_id: str, artifact_id: str) -> tuple[Path, dict]:
    from services.binder_continuation import resolve_root
    owner, root = await resolve_root(session, job_id)
    owners = {
        'bindcraft2': ('bindcraft2_native_publication', 'bindcraft2/native/'),
        'boltzgen': ('boltzgen_generation_publication', 'boltzgen/native/'),
        'ppiflow': ('ppiflow_generation_publication', 'ppiflow_generation/'),
    }
    publication_key, prefix = owners.get(owner.model_id, ('', ''))
    artifact = await session.get(JobArtifact, artifact_id)
    from services.binder_retained_publication import PUBLICATION_KEY, PREFIX, ROOTS
    if artifact and owner.model_id in ROOTS and artifact.logical_path.startswith(PREFIX):
        publication_key, prefix = PUBLICATION_KEY, PREFIX
    receipt = (owner.provenance or {}).get(publication_key) or {}
    name = artifact.logical_path.removeprefix(prefix) if artifact and prefix else ''
    entry = receipt.get('files', {}).get(name)
    if (artifact is None or not prefix or not artifact.logical_path.startswith(prefix) or entry is None
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
    if owner.model_id == 'bindcraft2' and expected.suffix.lower() in {'.cif', '.mmcif'} and expected.is_file():
        # Historical registered annotations predate trajectory/monomer role
        # reading. Enrich only from this exact selected document, not from the
        # campaign or a paired complex (whose binder chain can be different).
        import hashlib
        from services.bindcraft2_publication import _regular
        from services.bindcraft2_native_results import _cif_metadata
        _, raw = _regular(Path(receipt['root']) / receipt.get('campaign_root', '.'), name)
        if len(raw) != artifact.bytes or hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise ValueError('Selected native bytes changed')
        stamp = _cif_metadata(expected, raw)
        document = {**document, **{field: stamp[field] for field in ('binder_chains', 'target_chains') if field in stamp}}
    return Path(artifact.storage_path), {
        'artifact_id': artifact.id, 'artifact_sha256': artifact.sha256,
        'owner_job_id': owner.id, 'lineage_root_job_id': root.id,
        'source_parent_job_id': owner.parent_job_id,
        'publication_source': receipt.get('source'),
        'producer_model_id': document.get('producer_model_id', owner.model_id),
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
            media_type=media_type, source_format=source_format, producer_stage=identity.get('producer_model_id', 'bindcraft2') + '_native',
            producer_coordinates={'candidate_id': identity['artifact_id'],
                                  'selected_document': identity,
                                  'lineage_root_job_id': identity['lineage_root_job_id']}))
    return result
