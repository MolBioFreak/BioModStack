"""Exact model-owned registered documents, independent of accepted Designs."""
from pathlib import Path
from fastapi import HTTPException
from database import Job, JobArtifact


async def resolve_native_source(session, job_id: str, artifact_id: str) -> tuple[Path, dict]:
    owner = await session.get(Job, job_id)
    if owner is None:
        raise HTTPException(404, 'Source job not found')
    # Scientific custody is the producer JobArtifact, not a binder scheduler
    # root or a surviving ancestor Job. Lineage remains descriptive metadata.
    from services.binder_diagnostic_selection import root_id
    lineage_root = root_id(owner)
    from services.rfd4_proteina_results import has_native_publication, publication_model, PUBLICATIONS
    if has_native_publication(owner):
        producer = publication_model(owner)
        prefix = PUBLICATIONS[producer][0] + '/'
        # The native index/ingester registers output-relative JobArtifacts. Do
        # not interpret its observation manifest as a second custody authority.
        artifact = await session.get(JobArtifact, artifact_id)
        from services.binder_publication_roots import publication_root
        from services.binder_source_materialization import structure_suffix
        if (artifact is None or artifact.owner_job_id != owner.id
                or artifact.attempt != (owner.retry_count or 0)):
            raise HTTPException(404, 'Selected native structure not found')
        relative = Path(artifact.logical_path)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in artifact.logical_path
                or relative.as_posix() != artifact.logical_path
                or not artifact.logical_path.startswith(prefix)
                or structure_suffix(relative) not in {'.pdb', '.cif', '.mmcif', '.bcif',
                    '.cif.gz', '.mmcif.gz', '.cif.zst', '.mmcif.zst', '.bcif.gz', '.bcif.zst'}):
            raise HTTPException(404, 'Selected native structure not found')
        root = publication_root(owner)
        expected = root / relative
        if Path(artifact.storage_path) != expected or any(p.is_symlink() for p in (expected, *expected.parents)):
            raise ValueError('Selected native artifact path changed')
        document = (artifact.provenance or {}).get('native_document') or {}
        if document.get('sha256', artifact.sha256) != artifact.sha256:
            raise ValueError('Selected native document identity changed')
        return expected, {'artifact_id': artifact.id, 'artifact_sha256': artifact.sha256,
            'owner_job_id': owner.id, 'lineage_root_job_id': lineage_root,
            'source_parent_job_id': owner.parent_job_id, 'producer_model_id': producer,
            'target_state': document.get('target_state'), 'producer_document': document}
    owners = {
        'bindcraft2': ('bindcraft2_native_publication', 'bindcraft2/native/'),
        'boltzgen': ('boltzgen_generation_publication', 'boltzgen/native/'),
        'ppiflow': ('ppiflow_generation_publication', 'ppiflow_generation/'),
    }
    if owner.model_id in owners:
        # Keep legacy binder-root enumeration semantics; the new native
        # producer branch above does not acquire that scheduler dependency.
        from services.binder_continuation import resolve_root
        _, legacy_root = await resolve_root(session, job_id)
        lineage_root = legacy_root.id
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
    from services.binder_publication_roots import publication_root
    publication_root(owner, receipt['root'])
    if (Path(artifact.storage_path) != expected or Path(name).is_absolute() or '..' in Path(name).parts):
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
        'owner_job_id': owner.id, 'lineage_root_job_id': lineage_root,
        'source_parent_job_id': owner.parent_job_id,
        'publication_source': receipt.get('source'),
        'producer_model_id': document.get('producer_model_id', owner.model_id),
        'target_state': document.get('target_state'), 'producer_document': document}


async def read_native_sequence_candidates(session, owner, *, verify_bytes=False):
    """Read registered ProteinMPNN/FA-MPNN membership without fake Designs.

    Scalar pages use the registration snapshot; exact selection can verify the
    one manifest. No coordinate parsing, publication, or science writer runs.
    """
    from sqlalchemy import select
    from scripts.sequence_design_results import RESULT_PATH, MODES
    if owner.mode not in MODES.get(owner.model_id, set()):
        raise ValueError('No public sequence-index contract for this producer')
    artifact = await session.scalar(select(JobArtifact).where(
        JobArtifact.owner_job_id == owner.id, JobArtifact.attempt == (owner.retry_count or 0),
        JobArtifact.logical_path == RESULT_PATH))
    if artifact is None:
        raise HTTPException(404, 'Native sequence publication is not registered')
    provenance = artifact.provenance or {}
    if (provenance.get('model_id') != owner.model_id or provenance.get('mode') != owner.mode
            or provenance.get('remote_attempt_id') != owner.remote_attempt_id):
        raise ValueError('Native sequence publication owner changed')
    if verify_bytes:
        import hashlib
        from scripts.sequence_design_results import _published_bytes
        from services.binder_publication_roots import publication_root
        root = Path(artifact.storage_path).parent.parent
        publication_root(owner, root)
        if root / RESULT_PATH != Path(artifact.storage_path):
            raise ValueError('Native sequence publication path changed')
        raw = _published_bytes(root, RESULT_PATH)
        if len(raw) != artifact.bytes or hashlib.sha256(raw).hexdigest() != artifact.sha256:
            raise ValueError('Native sequence publication bytes changed')
    return artifact, provenance['native_sequences']


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
