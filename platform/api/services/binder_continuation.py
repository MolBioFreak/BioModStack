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
        next_id = root_id if root_id and root_id != current.id else current.parent_job_id
        if not next_id:
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


def snapshot_selection(source: Job, root: Job, designs: list[Design], *,
                       candidate_documents: dict | None = None) -> Path:
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
    directory = _materialize_antibody_selection(root, source, inputs, 'selected', namespace='binder')
    manifest = json.loads((directory / 'selection_manifest.json').read_text())
    rows = []
    for item in manifest['designs']:
        if item['design_id'] in resolved:
            _, identity = resolved[item['design_id']]
            retained = Path(item.get('native_source_structure_path') or item['selection_pdb_path'])
            if hashlib.sha256(retained.read_bytes()).hexdigest() != identity['artifact_sha256']:
                shutil.rmtree(directory)
                raise ValueError('Selected document snapshot differs from its owned artifact')
            item['selected_document'] = identity
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
                                     'lineage_root_job_id': root.id}})
    (directory / 'selection_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True))
    (directory / 'source_identity.json').write_text(json.dumps(rows, indent=2, sort_keys=True))
    return directory


MODEL_OPERATIONS = {
    'refine': ('binder_refinement', 'refine'),
    'caliby': ('caliby_binder', 'design'),
    'fampnn': ('fampnn', 'binder_design'),
    'proteinmpnn': ('proteinmpnn', 'design'),
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
    effective.update({
        'pdb_paths': ','.join(item['selection_pdb_path'] for item in manifest['designs']),
        'source_identity_json': str(selection_dir / 'source_identity.json'),
        'selected_input_dir': str(selection_dir),
        'selected_input_manifest': str(manifest_path),
        'source_selection_manifest_path': str(manifest_path),
        'selection_source_type': 'selected_designs',
        'selection_source_job_id': source.id,
        'selected_input_source_job_id': source.id,
        'lineage_root_job_id': root.id,
        'iteration_source_root_job_id': root.id,
        'iteration_source_job_id': source.id,
        'iteration_source_design_ids': [item['design_id'] for item in manifest['designs']],
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
        params.update(iteration_source_design_ids=[item['design_id']], source_selection_count=1,
                      source_design_id=item['design_id'], source_pdb_path=path,
                      source_stage_job_id=item['design_job_id'])
        if operation in {'fampnn', 'proteinmpnn'}:
            params['input_pdb'] = path
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
            params['sequence_name'] = item['design_id']
        requests.append(base.model_copy(update={'name': f'{base.name}-{item["design_id"]}', 'params': params}))
    return requests
