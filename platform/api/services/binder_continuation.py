"""Selected binder input/lineage adapter; execution stays with model owners."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job
from schemas import JobCreate


async def resolve_root(session: AsyncSession, source_job_id: str) -> tuple[Job, Job]:
    source = await session.get(Job, source_job_id)
    if source is None:
        raise HTTPException(404, 'Source job not found')
    current = source
    visited = set()
    while current.id not in visited:
        visited.add(current.id)
        params = current.params or {}
        root_id = (current.lineage_root_job_id or params.get('lineage_root_job_id')
                   or params.get('iteration_source_root_job_id'))
        source_id = (current.selection_source_job_id or current.source_stage_job_id
                     or params.get('selection_source_job_id') or params.get('source_stage_job_id')
                     or params.get('iteration_source_job_id'))
        next_id = root_id or source_id or current.parent_job_id
        if not next_id or next_id == current.id:
            return source, current
        parent = await session.get(Job, next_id)
        if parent is None:
            raise HTTPException(422, 'Selected source lineage job is missing')
        current = parent
    raise HTTPException(422, 'Selected source lineage contains a cycle')


async def resolve_selection(session: AsyncSession, source_job_id: str, design_ids: list[str]):
    from routers.jobs import _validate_selected_design_owners
    source, root = await resolve_root(session, source_job_id)
    if len(set(design_ids)) != len(design_ids):
        raise HTTPException(422, 'Duplicate Design IDs')
    rows = (await session.scalars(select(Design).where(Design.id.in_(design_ids)))).all()
    by_id = {row.id: row for row in rows}
    if set(by_id) != set(design_ids):
        raise HTTPException(404, 'Selected Design not found')
    designs = [by_id[item] for item in design_ids]
    await _validate_selected_design_owners(session, source, root, designs, root_resolver=resolve_root)
    return source, root, designs


async def resolve_candidate_documents(session, designs, candidate_documents):
    """Use each persisted producer, including same-root sibling owners."""
    from services.binder_diagnostic_selection import selected_document
    if set(candidate_documents) - {design.id for design in designs}:
        raise ValueError('Candidate documents must be keyed by selected Design IDs')
    resolved = {}
    for design in designs:
        selector = candidate_documents.get(design.id)
        if selector and (selector.artifact_id is not None or selector.target_state is not None):
            owner = await session.get(Job, design.job_id)
            resolved[design.id] = await selected_document(owner, design, selector, session)
    return resolved


def retained_selection(source, root, designs, *, kind, create, extra=None) -> Path:
    """Reuse this owner's retained input bytes, not a writable producer inode.

    The namespace binds exact source bytes and ordered producer/selection IDs.
    Emitted metadata revisions reuse those bodies. Missing or changed retained
    files mean a fresh snapshot, not a new admission gate.
    A directory flock serializes preparation without allocating a lock per retry.
    """
    import fcntl
    import hashlib
    import os
    from routers.jobs import get_inputs_dir, _resolve_design_structure_path, _candidate_sidecar_paths

    def digest(path):
        with Path(path).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

    from routers.jobs import _build_selection_manifest_item, _derive_source_stage_payload
    rows, metadata = [], []
    for design in designs:
        path = _resolve_design_structure_path(design.pdb_path)
        members = [path, *(_candidate_sidecar_paths(design, path) if kind == 'native' else [])]
        rows.append({'design_id': design.id, 'job_id': design.job_id,
                     'members': {str(p.resolve()): digest(p) for p in members}})
        metadata.append(_build_selection_manifest_item(design, source_path=path,
                        selection_path=path, selection_entry_mode=None))
    identity = {'kind': kind, 'source_job_id': source.id, 'root_job_id': root.id,
                'rows': rows, 'extra': extra}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
    # Only metadata actually emitted by this owner participates. Scores, Job
    # progress, arbitrary parent params/provenance do not allocate input bodies.
    metadata_key = hashlib.sha256(json.dumps(
        [metadata, _derive_source_stage_payload(source, designs)],
        sort_keys=True, default=str).encode()).hexdigest()
    selection_root = get_inputs_dir() / 'design_selections' / (
        'protein_local_redesign' if kind == 'native' else 'binder')
    selection_root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(selection_root, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        # Two deterministic lookups in the existing retained namespace, never a
        # scan of unrelated selections. Metadata revisions share retained bodies
        # but get their own immutable manifest/source-identity documents.
        canonical = selection_root / key
        revision = selection_root / (key + '-' + metadata_key)
        donor = None
        for directory in (revision, canonical):
            try:
                manifest = json.loads((directory / 'selection_manifest.json').read_text())
                retained = manifest.pop('retained_input_binding', {})
                manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
                if (retained.get('identity_sha256') == key
                        and manifest_hash == retained.get('manifest_sha256') and retained.get('files')
                        and all(digest(directory / name) == value for name, value in retained['files'].items())):
                    if retained.get('metadata_sha256') == metadata_key:
                        return directory
                    donor = directory
            except (OSError, ValueError, KeyError):
                pass
        target = revision if canonical.exists() else canonical
        if target.exists():
            # Damaged historical input is retained, not overwritten underneath
            # another consumer. Native preparation still has its old fallback.
            import uuid
            target = selection_root / (key + '-' + uuid.uuid4().hex)
        if donor is None:
            directory = create()
        else:
            import errno
            import shutil
            import uuid
            directory = selection_root / ('.selection-' + uuid.uuid4().hex)
            directory.mkdir()
            donor_manifest = json.loads((donor / 'selection_manifest.json').read_text())
            for name in donor_manifest['retained_input_binding']['files']:
                if name == 'source_identity.json':
                    continue
                src, dst = donor / name, directory / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(src, dst)
                except OSError as exc:
                    if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.EMLINK):
                        raise
                    shutil.copyfile(src, dst)
            manifest = donor_manifest
            manifest.pop('retained_input_binding', None)
            by_id = {design.id: design for design in designs}
            for item in manifest['designs']:
                design = by_id.get(item['design_id'])
                if design is not None:
                    item.update(_build_selection_manifest_item(design,
                        source_path=Path(item['source_pdb_path']),
                        selection_path=Path(item['selection_pdb_path']),
                        selection_entry_mode=item.get('selection_entry_mode')))
            stage = _derive_source_stage_payload(source, designs)
            for name in ('source_stage_job_id', 'source_stage_family', 'source_stage_mode',
                         'selected_input_artifact_class', 'selected_input_schema_version'):
                manifest[name] = stage[name]
            _rebind_selection_paths(manifest, donor, directory)
            (directory / 'selection_manifest.json').write_text(json.dumps(manifest))
        # Rebind only owner-authored path fields, never arbitrary provenance.
        manifest = json.loads((directory / 'selection_manifest.json').read_text())
        _rebind_selection_paths(manifest, directory, target)
        (directory / 'selection_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True))
        if kind == 'pdb' or (directory / 'source_identity.json').exists():
            _write_source_identity(directory, root.id)
        directory.rename(target)
        directory = target
        receipt = directory / 'selection_manifest.json'
        manifest = json.loads(receipt.read_text())
        manifest['retained_input_binding'] = {
            'identity_sha256': key,
            'metadata_sha256': metadata_key,
            'manifest_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
            'files': {p.relative_to(directory).as_posix(): digest(p)
                      for p in directory.rglob('*') if p.is_file() and p != receipt}}
        partial = receipt.with_suffix('.json.partial')
        partial.write_text(json.dumps(manifest, indent=2, sort_keys=True))
        partial.replace(receipt)
        return directory
    finally:
        os.close(descriptor)


def _rebind_selection_paths(manifest: dict, previous: Path, destination: Path) -> None:
    def rebind(row, name):
        value = row.get(name)
        if value and Path(value).is_relative_to(previous):
            row[name] = str(destination / Path(value).relative_to(previous))
    rebind(manifest, 'source_selection_manifest_path')
    for item in manifest['designs']:
        for name in ('selection_pdb_path', 'native_source_structure_path', 'source_pdb_path'):
            rebind(item, name)


def snapshot_selection(source: Job, root: Job, designs: list[Design], *,
                       candidate_documents: dict | None = None, native_sources: list | None = None,
                       output_format: str = 'pdb') -> Path:
    import hashlib
    from types import SimpleNamespace
    resolved = candidate_documents or {}
    inputs = [SimpleNamespace(**{column.key: getattr(design, column.key)
                                 for column in Design.__table__.columns}) for design in designs]
    for design in inputs:
        if design.id in resolved:
            design.pdb_path = str(resolved[design.id][0])
    for path, identity in native_sources or []:
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != identity['artifact_sha256']:
            raise ValueError('Selected native bytes changed')
    extra = {'output_format': output_format, 'documents': {key: (str(value[0]), value[1]) for key, value in resolved.items()},
             'native': [(str(path), identity, identity['artifact_sha256'])
                        for path, identity in native_sources or []]}
    return retained_selection(source, root, inputs, kind=output_format, extra=extra,
        create=lambda: _snapshot_selection(source, root, designs,
            candidate_documents=candidate_documents, native_sources=native_sources, output_format=output_format))


def _snapshot_selection(source: Job, root: Job, designs: list[Design], *,
                       candidate_documents: dict | None = None, native_sources: list | None = None,
                       output_format: str = 'pdb') -> Path:
    from routers.jobs import _materialize_antibody_selection
    from types import SimpleNamespace
    import hashlib
    import shutil
    resolved = candidate_documents or {}
    # Detached values only: never assign a selected alternate onto an ORM Design.
    inputs = [SimpleNamespace(**{column.key: getattr(design, column.key)
                                for column in Design.__table__.columns})
              if design.id in resolved else design for design in designs]
    for design in inputs:
        if design.id in resolved:
            design.pdb_path = str(resolved[design.id][0])
    if output_format == 'native':
        from routers.jobs import _materialize_protein_local_selection
        directory = _materialize_protein_local_selection(root, source, inputs, 'selected')
    else:
        directory = _materialize_antibody_selection(root, source, inputs, 'selected', namespace='binder')
    manifest = json.loads((directory / 'selection_manifest.json').read_text())
    for selection in native_sources or []:
        from routers.jobs import _cif_selection_pdb
        path, identity = selection
        key = identity['artifact_id']
        destination = directory / f'native_{key}.pdb'
        native_path = directory / f'native_{key}{path.suffix}' if path.suffix.lower() not in {'.pdb', '.ent'} else destination
        from services.frustrampnn.jobs import _read_owned_structure
        native_path.write_bytes(_read_owned_structure(path, label='Selected native structure'))
        if hashlib.sha256(native_path.read_bytes()).hexdigest() != identity['artifact_sha256']:
            shutil.rmtree(directory)
            raise ValueError('Selected native snapshot differs from its owned artifact')
        if output_format == 'native':
            destination = native_path
        elif native_path != destination:
            _cif_selection_pdb(native_path, destination)
        manifest['designs'].append({'design_id': None, 'selection_id': key,
            'design_job_id': identity['owner_job_id'], 'selection_pdb_path': str(destination),
            'native_source_structure_path': str(native_path),
            'native_source_structure_sha256': identity['artifact_sha256'],
            'source_structure_path': str(path), 'selected_document': identity,
            'native_source': {'job_id': identity['owner_job_id'], 'artifact_id': key},
            'source_design_provenance': {},
            'selection_structure_sha256': hashlib.sha256(destination.read_bytes()).hexdigest()})
    manifest['native_source_count'] = len(native_sources or [])
    manifest['selection_count'] = len(manifest['designs'])
    for item in manifest['designs']:
        if item['design_id'] in resolved:
            _, identity = resolved[item['design_id']]
            retained = Path(item.get('native_source_structure_path') or item['selection_pdb_path'])
            if hashlib.sha256(retained.read_bytes()).hexdigest() != identity['artifact_sha256']:
                shutil.rmtree(directory)
                raise ValueError('Selected document snapshot differs from its owned artifact')
            item['selected_document'] = identity
    for item in manifest['designs']:
        item.setdefault('selection_structure_sha256',
            hashlib.sha256(Path(item['selection_pdb_path']).read_bytes()).hexdigest())
    # The exact document remains retained; a selected MODEL is a separately
    # recorded derivative, prepared by the existing source representation owner.
    for item in manifest['designs']:
        model = (item.get('selected_document') or {}).get('model')
        if model is None:
            continue
        from services.binder_source_materialization import materialize_source_bytes
        from paths import resolve_allowed_path
        original = Path(item.get('native_source_structure_path') or item['selection_pdb_path'])
        derivative = directory / ('model-' + str(item.get('selection_id') or item['design_id']))
        derivative.mkdir()
        prepared = materialize_source_bytes(original.read_bytes(), original.suffix, derivative,
                                             output_format=output_format, model_number=model)
        item['native_source_structure_path'] = str(original)
        item['native_source_structure_sha256'] = hashlib.sha256(original.read_bytes()).hexdigest()
        item['selection_pdb_path'] = str(resolve_allowed_path(prepared['path']))
        item['selection_structure_sha256'] = prepared['sha256']
        item['selected_model'] = model
    (directory / 'selection_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True))
    _write_source_identity(directory, root.id)
    return directory


def _write_source_identity(directory: Path, root_id: str) -> None:
    manifest = json.loads((directory / 'selection_manifest.json').read_text())
    rows = []
    for item in manifest['designs']:
        provenance = item.get('source_design_provenance') or {}
        rows.append({'staged_name': Path(item['selection_pdb_path']).name,
                     'source_path': item['selection_pdb_path'],
                     'source_meta': {**provenance, **item,
                                     'id': item['design_id'],
                                     'structure_state': (item['selected_document'].get('target_state')
                                                         if 'selected_document' in item else
                                                         (provenance.get('structure_state')
                                                          or provenance.get('target_state')
                                                          or provenance.get('primary_target_state'))),
                                     'parent_design_id': item['design_id'],
                                     'source_job_id': item['design_job_id'],
                                     'lineage_root_job_id': root_id}})
    (directory / 'source_identity.json').write_text(json.dumps(rows, indent=2, sort_keys=True))


MODEL_OPERATIONS = {
    'refine': ('binder_refinement', 'refine'),
    'caliby': ('caliby_binder', 'design'),
    'fampnn': ('fampnn', 'binder_design'),
    'proteinmpnn': ('proteinmpnn', 'design'),
    'protonpottsmpnn': ('protonpottsmpnn', 'redesign'),
    'predict_boltz2': ('boltz2', 'complex'),
    'predict_protenix': ('protenix', 'complex'),
}


def model_request(*, operation: str, params: dict, source: Job, root: Job,
                  selection_dir: Path, execution_target_id: str | None) -> JobCreate:
    model_id, mode = MODEL_OPERATIONS[operation]
    manifest_path = selection_dir / 'selection_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    # The operation owns only its requested model settings. Input/lineage comes
    # from persisted selected Designs, never a browser path or inherited recipe.
    effective = dict(params)
    if operation in {'proteinmpnn', 'fampnn'}:
        # Selected roles are exact author IDs, not antibody/CDR annotations.
        # Consume the selection aliases before the model's closed normalization.
        for selected_key, native_key in (('binder_chains', 'design_chain'),
                                         ('target_chains', 'target_chain')):
            if selected_key in effective:
                role = effective.pop(selected_key)
                effective.setdefault(native_key, role)
    effective.update({
        'pdb_paths': ','.join(item['selection_pdb_path'] for item in manifest['designs']),
        'source_identity_json': str(selection_dir / 'source_identity.json'),
        'selected_input_dir': str(selection_dir),
        'selected_input_manifest': str(manifest_path),
        'source_selection_manifest_path': str(manifest_path),
        'selection_source_type': 'selected_native_artifacts' if any(item.get('native_source') for item in manifest['designs']) else 'selected_designs',
        'selection_source_job_id': source.id,
        'selected_input_source_job_id': source.id,
        'lineage_root_job_id': root.id,
        'iteration_source_root_job_id': root.id,
        'iteration_source_job_id': source.id,
        'iteration_source_design_ids': [item['design_id'] for item in manifest['designs'] if item['design_id']],
        'native_sources': [item['native_source'] for item in manifest['designs'] if item.get('native_source')],
        'source_selection_count': len(manifest['designs']),
    })
    from routers.jobs import normalize_job_request
    request = JobCreate(name=f'{operation}-{source.name}', model_id=model_id, mode=mode,
                        params=effective, execution_target_id=execution_target_id)
    return normalize_job_request(request) if operation in {'refine', 'caliby'} else request


def individual_model_requests(base: JobCreate, operation: str, selection_dir: Path) -> list[JobCreate]:
    """Single-input native models get one ordinary Job per exact selected state."""
    if operation in {'refine', 'caliby'}:
        return [base]
    from Bio.PDB import PDBParser
    from routers.jobs import AA_CODES
    manifest = json.loads((selection_dir / 'selection_manifest.json').read_text())
    requests = []
    for item in manifest['designs']:
        params = dict(base.params)
        path = item['selection_pdb_path']
        params.update(iteration_source_design_ids=[item['design_id']] if item['design_id'] else [], source_selection_count=1,
                      source_design_id=item['design_id'], source_pdb_path=path,
                      source_stage_job_id=item['design_job_id'])
        params['native_sources'] = [item['native_source']] if item.get('native_source') else []
        params['selection_source_type'] = 'selected_native_artifacts' if item.get('native_source') else 'selected_designs'
        if operation == 'protonpottsmpnn':
            params['target_pdb'] = path
            params['pdb_paths'] = path
        elif operation in {'fampnn', 'proteinmpnn'}:
            params['input_pdb'] = path
            # The single-input child must not retain the batch CSV as a path;
            # shared portable discovery treats native file values literally.
            params['pdb_paths'] = path
        else:
            # Protein prediction is explicitly sequence-conditioned, not an
            # inherited ligand/pose constraint. Preserve exact chain labels.
            structure = PDBParser(QUIET=True).get_structure('selected', path)
            components = []
            for chain in structure[0]:
                residues = [residue for residue in chain if residue.id[0] == ' ']
                if not residues:
                    continue
                try:
                    sequence = ''.join(AA_CODES[residue.resname] for residue in residues)
                except KeyError as exc:
                    raise HTTPException(422, 'Protein prediction requires model-supported amino acid residues') from exc
                components.append({'id': chain.id, 'type': 'protein', 'sequence': sequence})
            if not components:
                raise HTTPException(422, 'Selected structure contains no protein sequence')
            params['complex_components'] = components
            params['sequence'] = ':'.join(component['sequence'] for component in components)
            params['sequence_name'] = item.get('selection_id') or item['design_id']
        requests.append(base.model_copy(update={'name': f'{base.name}-{item.get("selection_id") or item["design_id"]}', 'params': params}))
    return requests
