"""Selected binder input/lineage adapter; execution stays with model owners."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job
from schemas import JobCreate
from typing import Optional, List, Dict, Any
from copy import deepcopy
import uuid
import os
import hashlib
import logging
import shutil
from datetime import datetime
from antibody_pipeline_contract import (
    ANTIBODY_REFINEMENT_PIPELINE,
    ANTIBODY_PIPELINE_CONTRACT_VERSION,
    BACKBONE_COMPLEX,
    SEQUENCE_DESIGNED_COMPLEX,
    infer_antibody_artifact_class_from_stage,
    infer_selected_input_artifact_class,
    is_antibody_pipeline_mode,
    normalize_antibody_artifact_class,
)
from paths import get_code_root, get_data_root, get_inputs_dir
from schemas import ExecutionPolicy
from services.job_requests import (
    _coerce_nonempty_text,
    _dedupe_preserve_order,
    _normalize_antibody_job_params,
    _normalize_selected_loop_scope,
    _normalize_stage_family,
    _normalize_structure_geometry_params,
    _selection_manifest_path,
    _to_bool,
)
from services.job_readback import _is_meaningful_param_value
from services.job_readback import SavedReviewFilterSet
from services.binder_source_materialization import _cif_selection_pdb

logger = logging.getLogger(__name__)


async def resolve_root(session: AsyncSession, source_job_id: str) -> tuple[Job, Job]:
    source = await session.get(Job, source_job_id)
    if source is None:
        raise HTTPException(404, 'Source job not found')
    current = source
    visited = set()
    while current.id not in visited:
        visited.add(current.id)
        params = current.params or {}
        step = (current.provenance or {}).get('binder_round_step') or {}
        root_id = (current.lineage_root_job_id or params.get('lineage_root_job_id')
                   or params.get('iteration_source_root_job_id') or step.get('root_job_id'))
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

    def digest(path):
        with Path(path).open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()

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
        directory = _materialize_protein_local_selection(root, source, inputs, 'selected')
    else:
        directory = _materialize_antibody_selection(root, source, inputs, 'selected', namespace='binder')
    manifest = json.loads((directory / 'selection_manifest.json').read_text())
    for selection in native_sources or []:
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
        from services.binder_source_materialization import materialize_source_bytes, structure_suffix
        from paths import resolve_allowed_path
        original = Path(item.get('native_source_structure_path') or item['selection_pdb_path'])
        derivative = directory / ('model-' + str(item.get('selection_id') or item['design_id']))
        derivative.mkdir()
        prepared = materialize_source_bytes(original.read_bytes(), structure_suffix(original), derivative,
                                             output_format=output_format, model_number=model)
        item['native_source_structure_path'] = str(original)
        item['native_source_structure_sha256'] = prepared['original_sha256']
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
    from services.job_requests import normalize_job_request
    request = JobCreate(name=f'{operation}-{source.name}', model_id=model_id, mode=mode,
                        params=effective, execution_target_id=execution_target_id)
    return normalize_job_request(request) if operation in {'refine', 'caliby'} else request


def individual_model_requests(base: JobCreate, operation: str, selection_dir: Path) -> list[JobCreate]:
    """Single-input native models get one ordinary Job per exact selected state."""
    if operation in {'refine', 'caliby'}:
        return [base]
    from Bio.PDB import PDBParser
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


# Project root for resolving code-relative paths
CODE_ROOT = get_code_root()


PPI_FLOW_STAGE_FLAG_KEYS = {
    "run_ppiflow_backbone_refine",
    "run_ppiflow_maturation",
    "run_maturation",
    "run_post_validation_maturation",
    "run_post_boltz_maturation",
}


ANTIBODY_ITERATION_ACTION_LABELS = {
    "validate_boltz2": "Boltz-2 validation",
    "validate_protenix": "Protenix validation",
    "ppiflow_backbone_refine": "PPIFlow backbone refinement",
    "ppiflow_maturation": "PPIFlow maturation",
    "fampnn_redesign": "FAMPNN redesign",
    "frustrampnn": "Frustration analysis",
    "ui_refinement": "refinement launch",
}


def _format_artifact_identity(artifact_class: Optional[str], stage_family: Optional[str], stage_mode: Optional[str]) -> str:
    parts: List[str] = []
    normalized_artifact_class = normalize_antibody_artifact_class(artifact_class)
    if normalized_artifact_class:
        parts.append(normalized_artifact_class)
    stage_identity = _format_stage_identity(stage_family, stage_mode)
    if stage_identity and stage_identity != "unknown":
        parts.append(stage_identity)
    return " from ".join(parts) if parts else "unknown"


def _build_selected_loop_scope(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(params, dict):
        return None

    scope: Dict[str, Any] = {}
    for key in (
        "interactive_gate_stage",
        "rfantibody_screen_reference_scope",
        "antibody_design_mode",
        "ppiflow_stage_mode",
        "ppiflow_region_mode",
        "ppiflow_backbone_region_mode",
        "ppiflow_maturation_region_mode",
        "iteration_action",
        "stage_family",
        "stage_mode",
        "selection_source_type",
    ):
        value = params.get(key)
        if value not in (None, "", [], {}, ()):
            scope[key] = value

    for key in (
        "ppiflow_selected_loops",
        "ppiflow_backbone_loop_scope",
        "ppiflow_maturation_loop_scope",
        "selected_loops",
        "loop_ids",
        "selected_residues",
        "antibody_design_loops",
    ):
        normalized = _normalize_selected_loop_scope(params.get(key))
        if normalized:
            scope[key] = normalized

    if params.get("cdr_positions_by_loop") not in (None, "", [], {}, ()):
        scope["cdr_positions_by_loop"] = params.get("cdr_positions_by_loop")

    return scope or None


def _derive_iteration_stage_metadata(action: str, params: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    action_normalized = (action or "").strip().lower()
    explicit_stage_family = _normalize_stage_family(params.get("stage_family"))
    explicit_stage_mode = _normalize_stage_family(params.get("stage_mode"))
    explicit_ppiflow_mode = _normalize_stage_family(params.get("ppiflow_stage_mode"))

    if explicit_stage_family or explicit_stage_mode:
        return explicit_stage_family, explicit_stage_mode
    if explicit_ppiflow_mode:
        return "ppiflow", explicit_ppiflow_mode

    if action_normalized == "ppiflow_backbone_refine":
        return "ppiflow", "backbone_refine"
    if action_normalized == "ppiflow_maturation":
        return "ppiflow", "maturation"
    if action_normalized == "fampnn_redesign":
        return "fampnn", "redesign"
    if action_normalized == "caliby_redesign":
        return "caliby", "redesign"
    if action_normalized in {"validate_boltz2", "validate_protenix"}:
        return "validation", action_normalized.replace("validate_", "")
    if action_normalized == "frustrampnn":
        return "frustrampnn", "screening"
    if action_normalized == "ui_refinement":
        gate_stage = _normalize_stage_family(params.get("interactive_gate_stage"))
        return "refinement", gate_stage or "interactive"
    if action_normalized in {"cdr_indel_round", "mutation_seeded_refinement", "mutation_seed_build", "manual_mutagenesis_round"}:
        return "mutation", action_normalized
    return _normalize_stage_family(params.get("child_stage")), action_normalized or None


def _format_stage_identity(stage_family: Optional[str], stage_mode: Optional[str]) -> str:
    family = _normalize_stage_family(stage_family)
    mode = _normalize_stage_family(stage_mode)
    if family and mode:
        return f"{family}/{mode}"
    if family:
        return family
    if mode:
        return mode
    return "unknown"


def _review_stage_to_canonical_stage(stage: Any) -> tuple[Optional[str], Optional[str]]:
    normalized = _normalize_stage_family(stage)
    if normalized == "post_rfantibody":
        return "rfantibody", "post_rfantibody"
    if normalized == "post_boltzgen":
        return "boltzgen", "post_boltzgen"
    if normalized == "post_ppiflow_generator":
        return "ppiflow", "generator_backbone_refine"
    if normalized == "post_fampnn":
        return "fampnn", "post_fampnn"
    if normalized == "post_caliby":
        return "caliby", "post_caliby"
    if normalized == "post_structure_validation":
        return "validation", "post_structure_validation"
    return None, None


def _awaiting_stage_to_resume_hint(stage: Any) -> Optional[str]:
    normalized = _normalize_stage_family(stage)
    if normalized == "post_rfantibody":
        return "rfantibody"
    if normalized == "post_boltzgen":
        return "boltzgen"
    if normalized == "post_ppiflow_generator":
        return "ppiflow"
    if normalized == "post_fampnn":
        return "fampnn"
    if normalized == "post_caliby":
        return "caliby"
    if normalized in {"post_structure_validation", "pre_protenix_msa"}:
        return "structure_validation"
    return None


def _resolve_design_stage_metadata(design: Any) -> tuple[Optional[str], Optional[str]]:
    family = (
        _normalize_stage_family(getattr(design, "stage_family", None))
        or _normalize_stage_family(getattr(design, "source_stage_family", None))
    )
    mode = (
        _normalize_stage_family(getattr(design, "stage_mode", None))
        or _normalize_stage_family(getattr(design, "source_stage_mode", None))
    )
    review_stage_family, review_stage_mode = _review_stage_to_canonical_stage(
        getattr(design, "source_stage", None)
    )
    return family or review_stage_family, mode or review_stage_mode


def _resolve_design_artifact_class(design: Any) -> Optional[str]:
    explicit = normalize_antibody_artifact_class(getattr(design, "artifact_class", None))
    if explicit:
        return explicit
    family, mode = _resolve_design_stage_metadata(design)
    return infer_antibody_artifact_class_from_stage(family, mode)


def _requires_immediate_ppiflow_backbone_refine(action: str, params: Dict[str, Any]) -> bool:
    action_normalized = (action or "").strip().lower()
    if action_normalized == "ppiflow_backbone_refine":
        return True
    if action_normalized != "ui_refinement":
        return False
    stage_mode = _normalize_stage_family(params.get("ppiflow_stage_mode"))
    if stage_mode == "post_ppiflow":
        return False
    return _to_bool(params.get("run_ppiflow_backbone_refine")) or stage_mode in {
        "post_rfantibody",
        "backbone_refine",
        "both",
    }


def _requires_direct_ppiflow_maturation(action: str, params: Dict[str, Any]) -> bool:
    action_normalized = (action or "").strip().lower()
    if action_normalized == "ppiflow_maturation":
        return True
    if action_normalized != "ui_refinement":
        return False
    stage_mode = _normalize_stage_family(params.get("ppiflow_stage_mode"))
    if not (
        _to_bool(params.get("run_ppiflow_maturation"))
        or _to_bool(params.get("run_maturation"))
        or stage_mode in {"post_fampnn", "maturation", "both"}
    ):
        return False
    return not (
        _to_bool(params.get("seq_design_fampnn"))
        or _to_bool(params.get("seq_design_antifold"))
        or _to_bool(params.get("seq_design_proteinmpnn"))
        or _to_bool(params.get("seq_design_caliby"))
    )


def _requires_post_ppiflow_backbone_reattempt(action: str, params: Dict[str, Any]) -> bool:
    action_normalized = (action or "").strip().lower()
    if action_normalized != "ui_refinement":
        return False
    stage_mode = _normalize_stage_family(params.get("ppiflow_stage_mode"))
    return stage_mode == "post_ppiflow"


def _validate_antibody_iteration_source_compatibility(action: str, params: Dict[str, Any]) -> None:
    source_stage_family = _normalize_stage_family(
        params.get("selected_input_stage_family") or params.get("source_stage_family")
    )
    source_stage_mode = _normalize_stage_family(
        params.get("selected_input_stage_mode") or params.get("source_stage_mode")
    )
    source_artifact_class = infer_selected_input_artifact_class(
        selected_input_artifact_class=params.get("selected_input_artifact_class"),
        selected_input_stage_family=source_stage_family,
        selected_input_stage_mode=source_stage_mode,
        rfantibody_input_pdbs=params.get("rfantibody_input_pdbs"),
        fampnn_collected_pdbs=params.get("fampnn_collected_pdbs"),
    )
    source_identity = _format_artifact_identity(source_artifact_class, source_stage_family, source_stage_mode)
    action_label = ANTIBODY_ITERATION_ACTION_LABELS.get((action or "").strip().lower(), action or "launch")

    if _requires_immediate_ppiflow_backbone_refine(action, params) and source_artifact_class not in {None, BACKBONE_COMPLEX}:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{action_label} only accepts backbone-complex inputs. "
                f"Selected input is {source_identity}. "
                "Use FAMPNN redesign first, then optionally run post-FA-MPNN PPIFlow maturation."
            ),
        )
    if _requires_immediate_ppiflow_backbone_refine(action, params) and source_stage_family == "ppiflow":
        raise HTTPException(
            status_code=422,
            detail=(
                f"{action_label} does not accept recursive PPIFlow backbone-refine inputs. "
                f"Selected input is {source_identity}. "
                "Use post-PPIFlow reattempt mode for loop-selective retries, or return to sequence design/maturation."
            ),
        )

    if _requires_post_ppiflow_backbone_reattempt(action, params) and (
        source_artifact_class != BACKBONE_COMPLEX or source_stage_family != "ppiflow"
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{action_label} in post-PPIFlow reattempt mode only accepts PPIFlow-derived backbone-complex inputs. "
                f"Selected input is {source_identity}. "
                "Choose RFantibody backbone refine for RF outputs, or switch this relaunch to FA-MPNN/post-FA-MPNN maturation."
            ),
        )

    if _requires_direct_ppiflow_maturation(action, params) and source_artifact_class != SEQUENCE_DESIGNED_COMPLEX:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{action_label} requires sequence-designed complex inputs when it runs directly on the selected structures. "
                f"Selected input is {source_identity}. "
                "If you are relaunching from PPIFlow backbone outputs, include sequence design first or use FAMPNN redesign."
            ),
        )


def _derive_job_stage_tags(model_id: str, mode: str, params: Dict[str, Any], child_stage: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(params, dict):
        params = {}

    model_normalized = (model_id or "").strip().lower()
    mode_normalized = (mode or "").strip().lower()
    trusted_iteration_identity = (
        model_normalized in {"rfantibody", "ppiflow", "fampnn_child", "maturation_child"}
        or mode_normalized in {"antibody_design", "maturation_child"}
    )
    action = _coerce_nonempty_text(params.get("iteration_action"))
    if action and trusted_iteration_identity:
        family, stage_mode = _derive_iteration_stage_metadata(action, params)
        if family or stage_mode:
            return family, stage_mode

    child_stage_normalized = _normalize_stage_family(child_stage)
    if child_stage_normalized and trusted_iteration_identity:
        return child_stage_normalized, child_stage_normalized

    if mode_normalized == "maturation_child":
        return "ppiflow", "maturation"
    if model_normalized == "boltzgen":
        boltzgen_mode = str(params.get("boltzgen_mode") or mode_normalized or "").strip().lower()
        return "boltzgen", boltzgen_mode or "generation"
    if model_normalized == "ppiflow":
        ppiflow_mode = str(
            params.get("stage_mode")
            or params.get("ppiflow_stage_mode")
            or mode_normalized
            or ""
        ).strip().lower()
        return "ppiflow", ppiflow_mode or "generator_backbone_refine"
    if model_normalized == "caliby_experimental":
        caliby_mode = str(
            params.get("stage_mode")
            or params.get("caliby_task")
            or mode_normalized
            or ""
        ).strip().lower()
        return "caliby", caliby_mode or "sequence_design"
    if "fampnn" in model_normalized:
        return "fampnn", mode_normalized or "sequence_design"
    if model_normalized in {"protenix", "boltz2", "rf3"} and mode_normalized in {"predict", "complex"}:
        return "validation", model_normalized
    if "antibody" in mode_normalized or "antibody" in model_normalized:
        return "antibody", mode_normalized or model_normalized
    return None, None


def _looks_like_antibody_job(job: Optional[Job]) -> bool:
    if job is None:
        return False
    model_id = (job.model_id or "").strip().lower()
    mode = (job.mode or "").strip().lower()
    params = job.params if isinstance(job.params, dict) else {}
    rfd_mode = str(params.get("rfd_mode") or "").strip().lower()
    boltzgen_mode = str(params.get("boltzgen_mode") or mode or "").strip().lower()
    framework_type = str(params.get("framework_type") or "").strip().lower()
    has_antibody_params = any(_is_meaningful_param_value(params.get(key)) for key in ("framework_type", "antibody_chains", "epitope_residues"))
    is_boltzgen_nanobody = (
        model_id == "boltzgen"
        and (
            boltzgen_mode in {"nanobody_binder", "antibody_binder"}
            or framework_type == "nanobody"
        )
    )
    is_caliby_antibody = model_id == "caliby_experimental" and has_antibody_params
    return (
        model_id in {"template_antibody_denovo", "antibody_denovo", "antibody_child"}
        or "antibody" in model_id
        or "antibody" in mode
        or is_antibody_pipeline_mode(rfd_mode)
        or has_antibody_params
        or is_boltzgen_nanobody
        or is_caliby_antibody
    )


def _should_spawn_antibody_refinement_on_resume(job: Optional[Job]) -> bool:
    if job is None or not getattr(job, "awaiting_input", False):
        return False
    awaiting_stage = str(getattr(job, "awaiting_stage", "") or "").strip().lower()
    model_id = str(getattr(job, "model_id", "") or "").strip().lower()
    if awaiting_stage == "post_boltzgen" and model_id == "boltzgen":
        return _looks_like_antibody_job(job)
    if awaiting_stage == "post_ppiflow_generator" and model_id == "ppiflow":
        return _looks_like_antibody_job(job)
    return False


async def _resolve_antibody_root_job(session: AsyncSession, source_job_id: str) -> tuple[Job, Job]:
    source_job = await session.get(Job, source_job_id)
    if source_job is None:
        raise HTTPException(status_code=404, detail=f"Source job '{source_job_id}' not found")

    # Viewer-launched refinement/iteration rounds are top-level jobs, so parent_job_id does not
    # capture scientific lineage. Prefer the explicit iteration root when it exists.
    iteration_root_id: Optional[str] = None
    if isinstance(source_job.params, dict):
        for key in ("iteration_source_root_job_id", "iteration_source_job_id"):
            value = source_job.params.get(key)
            if isinstance(value, str) and value.strip():
                iteration_root_id = value.strip()
                break

    if iteration_root_id:
        explicit_root = await session.get(Job, iteration_root_id)
        if explicit_root is not None and _looks_like_antibody_job(explicit_root):
            return source_job, explicit_root

    lineage: List[Job] = []
    visited: set[str] = set()
    current: Optional[Job] = source_job
    while current is not None and current.id not in visited:
        lineage.append(current)
        visited.add(current.id)
        if not current.parent_job_id:
            break
        current = await session.get(Job, current.parent_job_id)

    antibody_jobs = [job for job in lineage if _looks_like_antibody_job(job)]
    root_job = antibody_jobs[-1] if antibody_jobs else lineage[-1]
    if not _looks_like_antibody_job(root_job):
        raise HTTPException(
            status_code=422,
            detail="Selected job is not part of an antibody or nanobody refinement-compatible lineage.",
        )
    return source_job, root_job


async def _validate_selected_design_owners(
    session: AsyncSession,
    source_job: Job,
    root_job: Optional[Job],
    designs: List[Design],
    *,
    root_resolver=None,
) -> None:
    """Refuse a foreign Design before copying or linking any selected input."""
    resolve_root = root_resolver or _resolve_antibody_root_job
    owner_roots: Dict[str, str] = {}
    for design in designs:
        owner_id = str(getattr(design, "job_id", "") or "").strip()
        if not owner_id:
            raise HTTPException(status_code=422, detail="Selected design has no source job owner.")
        if root_job is not None:
            claimed_root = str(getattr(design, "lineage_root_job_id", "") or "").strip()
            if claimed_root and claimed_root != root_job.id:
                raise HTTPException(status_code=422, detail="Selected design belongs to another lineage root.")
        if owner_id in owner_roots:
            continue
        owner = await session.get(Job, owner_id)
        if owner is None:
            raise HTTPException(status_code=422, detail="Selected design source job is missing.")
        if owner_id == source_job.id or (root_job is not None and owner_id == root_job.id):
            if owner.model_id == 'bindcraft2':
                from services.bindcraft2_publication import verify_selected_native_designs
                await verify_selected_native_designs(owner, session, [d for d in designs if d.job_id == owner_id])
            owner_roots[owner_id] = root_job.id if root_job is not None else source_job.id
            continue
        if root_job is None:
            raise HTTPException(status_code=422, detail="Selected design belongs to another job.")
        try:
            _, owner_root = await resolve_root(session, owner_id)
        except HTTPException as exc:
            raise HTTPException(status_code=422, detail="Selected design has no compatible source lineage.") from exc
        if owner_root.id != root_job.id:
            raise HTTPException(status_code=422, detail="Selected design belongs to another lineage root.")
        if owner.model_id == 'bindcraft2':
            from services.bindcraft2_publication import verify_selected_native_designs
            await verify_selected_native_designs(owner, session, [d for d in designs if d.job_id == owner_id])
        owner_roots[owner_id] = owner_root.id


def _resolve_design_structure_path(raw_path: str) -> Path:
    if not raw_path:
        raise HTTPException(status_code=422, detail="Selected design is missing a structure path.")

    raw = Path(raw_path)
    candidates: List[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.extend([
            (get_data_root() / raw),
            (CODE_ROOT / raw),
            raw.resolve(),
        ])

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except FileNotFoundError:
            continue
        if resolved.exists():
            return resolved

    raise HTTPException(
        status_code=422,
        detail=f"Selected design structure path does not exist: {raw_path}",
    )


def _prune_iteration_params(base_params: Dict[str, Any]) -> Dict[str, Any]:
    pruned = deepcopy(base_params) if isinstance(base_params, dict) else {}
    for key in {
        "remote_result_policy",  # carried by typed ExecutionPolicy on follow-ons
        "execution_policy",
        "gpu_id",  # physical assignment belongs to the new attempt
        "pinned_gpus",
        "job_id",
        "run_id",
        "batch_name",
        "api_url",
        "out_dir",
        "output_dir",
        "interactive_gate_continue",
        "fampnn_collected_pdbs",
        "rfantibody_input_pdbs",
        "skip_rfantibody",
        "skip_fampnn",
        "iteration_source_job_id",
        "iteration_source_root_job_id",
        "iteration_source_design_ids",
        "iteration_action",
        "iteration_selection_dir",
        "selected_input_dir",
        "selected_input_manifest",
        "selected_input_artifact_class",
        "selected_input_schema_version",
        "selected_input_source_job_id",
        "selected_input_stage_family",
        "selected_input_stage_mode",
        "lineage_root_job_id",
        "stage_family",
        "stage_mode",
        "selection_source_type",
        "selection_source_job_id",
        "selection_dataset_name",
        "selected_loop_scope",
        "ppiflow_stage_mode",
        "ppiflow_tuning_profile",
        "ppiflow_stage_target",
        "ppiflow_selected_loops",
        "ppiflow_objective_mode",
        "ppiflow_objective_threshold",
        "manual_mutation_fixed_positions_json",
        "manual_mutation_mode",
        "manual_mutation_method",
        "mutation_seed_refinement_trigger",
        "mutation_variant",
        "resume_job_id",
        "resume_root_job_id",
        "resume_work_dir",
        "resume_source_dir",
        "resume_stage_work_dir",
        "resume_requested_stage",
        "resume_param_overrides",
        "resume_from_stage",
        "resume_name_suffix",
        "resume_lock_retry_attempts",
        *PPI_FLOW_STAGE_FLAG_KEYS,
    }:
        pruned.pop(key, None)
    return _normalize_antibody_job_params(_normalize_structure_geometry_params(pruned))


AA_CODES = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
}


def _extract_chain_records_from_pdb(pdb_path: Path) -> Dict[str, List[Dict[str, Any]]]:
    chain_records: Dict[str, List[Dict[str, Any]]] = {}
    seen_residues: Dict[str, set[tuple[int, str]]] = {}
    with pdb_path.open() as handle:
        for line in handle:
            if not line.startswith("ATOM") or len(line) < 27:
                continue
            if line[12:16].strip() != "CA":
                continue
            res_name = line[17:20].strip()
            chain_id = (line[21].strip() or "_").upper()
            aa = AA_CODES.get(res_name)
            if aa is None:
                continue
            try:
                resseq = int(line[22:26].strip())
            except ValueError:
                continue
            icode = line[26].strip()
            residue_key = (resseq, icode)
            if chain_id not in chain_records:
                chain_records[chain_id] = []
                seen_residues[chain_id] = set()
            if residue_key in seen_residues[chain_id]:
                continue
            seen_residues[chain_id].add(residue_key)
            chain_records[chain_id].append(
                {
                    "resseq": resseq,
                    "icode": icode,
                    "aa": aa,
                }
            )
    return chain_records


def _create_antibody_selection_dir(action: str) -> Path:
    selection_root = get_inputs_dir() / "design_selections" / "antibody"
    selection_root.mkdir(parents=True, exist_ok=True)
    selection_dir = selection_root / (
        f"{datetime.utcnow():%Y%m%d_%H%M%S}_{action}_{uuid.uuid4().hex[:8]}"
    )
    selection_dir.mkdir(parents=True, exist_ok=False)
    return selection_dir


def _create_generic_selection_dir(namespace: str, action: str) -> Path:
    selection_root = get_inputs_dir() / "design_selections" / namespace
    selection_root.mkdir(parents=True, exist_ok=True)
    selection_dir = selection_root / (
        f"{datetime.utcnow():%Y%m%d_%H%M%S}_{action}_{uuid.uuid4().hex[:8]}"
    )
    selection_dir.mkdir(parents=True, exist_ok=False)
    return selection_dir


def _link_selection_input(source_path: Path, dest_path: Path) -> str:
    try:
        rel_target = os.path.relpath(source_path, start=dest_path.parent)
        os.symlink(rel_target, dest_path)
        return "symlink"
    except OSError as symlink_error:
        try:
            os.link(source_path, dest_path)
            return "hardlink"
        except OSError as hardlink_error:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Failed to materialize a reference-only selection set without copying data. "
                    f"Symlink error: {symlink_error}. Hardlink error: {hardlink_error}."
                ),
            ) from hardlink_error


def _derive_source_stage_payload(
    source_job: Job,
    designs: List[Design],
    selection_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    source_job_ids = _dedupe_preserve_order([str(design.job_id) for design in designs if getattr(design, "job_id", None)])
    design_stage_metadata = [_resolve_design_stage_metadata(design) for design in designs]
    source_stage_families = _dedupe_preserve_order([
        family
        for family, _ in design_stage_metadata
        if family
    ])
    source_stage_modes = _dedupe_preserve_order([
        mode
        for _, mode in design_stage_metadata
        if mode
    ])

    job_stage_family = _normalize_stage_family(getattr(source_job, "stage_family", None))
    job_stage_mode = _normalize_stage_family(getattr(source_job, "stage_mode", None))
    if not job_stage_family and not job_stage_mode:
        job_stage_family, job_stage_mode = _derive_job_stage_tags(
            getattr(source_job, "model_id", None),
            getattr(source_job, "mode", None),
            source_job.params if isinstance(getattr(source_job, "params", None), dict) else {},
            getattr(source_job, "child_stage", None),
        )

    source_stage_job_id = source_job_ids[0] if len(source_job_ids) == 1 else source_job.id
    source_stage_family = source_stage_families[0] if len(source_stage_families) == 1 else job_stage_family
    source_stage_mode = source_stage_modes[0] if len(source_stage_modes) == 1 else job_stage_mode
    design_artifact_classes = _dedupe_preserve_order(
        [
            artifact_class
            for artifact_class in (_resolve_design_artifact_class(design) for design in designs)
            if artifact_class
        ]
    )
    selected_input_artifact_class = (
        design_artifact_classes[0]
        if len(design_artifact_classes) == 1
        else infer_antibody_artifact_class_from_stage(source_stage_family, source_stage_mode)
    )
    source_selection_manifest_path = str(_selection_manifest_path(selection_dir)) if selection_dir is not None else None

    return {
        "source_stage_job_id": source_stage_job_id,
        "source_stage_family": source_stage_family,
        "source_stage_mode": source_stage_mode,
        "source_selection_manifest_path": source_selection_manifest_path,
        "source_selection_count": len(designs),
        "selected_input_dir": str(selection_dir) if selection_dir is not None else None,
        "selected_input_manifest": source_selection_manifest_path,
        "selected_input_stage_family": source_stage_family,
        "selected_input_stage_mode": source_stage_mode,
        "selected_input_source_job_id": source_stage_job_id,
        "selected_input_artifact_class": selected_input_artifact_class,
        "selected_input_schema_version": (
            ANTIBODY_PIPELINE_CONTRACT_VERSION if selected_input_artifact_class else None
        ),
    }


def _build_selection_manifest_item(
    design: Design,
    *,
    source_path: Path,
    selection_path: Path,
    selection_entry_mode: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    design_stage_family, design_stage_mode = _resolve_design_stage_metadata(design)
    design_artifact_class = _resolve_design_artifact_class(design)
    item: Dict[str, Any] = {
        "design_id": design.id,
        "design_name": design.name,
        "design_job_id": design.job_id,
        "design_stage_family": design_stage_family,
        "design_stage_mode": design_stage_mode,
        "design_artifact_class": design_artifact_class,
        "design_artifact_schema_version": (
            getattr(design, "artifact_schema_version", None)
            or (ANTIBODY_PIPELINE_CONTRACT_VERSION if design_artifact_class else None)
        ),
        "lineage_root_job_id": design.lineage_root_job_id,
        "parent_design_id": design.parent_design_id,
        "origin_design_id": design.origin_design_id,
        "origin_backbone_design_id": design.origin_backbone_design_id,
        "origin_job_id": getattr(design, "origin_job_id", None),
        "source_design_provenance": getattr(design, "provenance", None),
        "source_review_role_map": getattr(design, "review_role_map", None),
        "source_review_artifact_manifest": getattr(design, "review_artifact_manifest", None),
        "source_design_name": design.name,
        "source_pdb_path": str(source_path),
        "selection_pdb_path": str(selection_path),
        "selected_loop_scope": design.selected_loop_scope,
    }
    if selection_entry_mode:
        item["selection_entry_mode"] = selection_entry_mode
    if extra:
        for key, value in extra.items():
            if value not in (None, "", [], {}, ()):
                item[key] = value
    return item


def _write_selection_manifest(
    selection_dir: Path,
    root_job: Job,
    source_job: Job,
    action: str,
    manifest_items: List[Dict[str, Any]],
    source_stage_payload: Optional[Dict[str, Any]] = None,
    fixed_positions_by_pdb: Optional[Dict[str, str]] = None,
    preserve_existing: bool = False,
) -> None:
    manifest = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "root_job_id": root_job.id,
        "source_job_id": source_job.id,
        "design_count": len(manifest_items),
        "source_stage_job_id": (source_stage_payload or {}).get("source_stage_job_id"),
        "source_stage_family": (source_stage_payload or {}).get("source_stage_family"),
        "source_stage_mode": (source_stage_payload or {}).get("source_stage_mode"),
        "selected_input_artifact_class": (source_stage_payload or {}).get("selected_input_artifact_class"),
        "selected_input_schema_version": (source_stage_payload or {}).get("selected_input_schema_version"),
        "source_selection_manifest_path": str(_selection_manifest_path(selection_dir)),
        "source_selection_count": len(manifest_items),
        "designs": manifest_items,
    }
    def write_json(path, value):
        if not preserve_existing:
            path.write_text(json.dumps(value, indent=2, sort_keys=True))
            return
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise ValueError('Unsafe retained seed metadata')
            previous = json.loads(path.read_bytes())
            # Creation time is presentation only; all native provenance stays exact.
            if {k: v for k, v in previous.items() if k != 'created_at'} != {
                    k: v for k, v in value.items() if k != 'created_at'}:
                raise ValueError('Retained seed metadata differs from reviewed derivation')
            return
        import tempfile
        with tempfile.NamedTemporaryFile(dir=selection_dir.parent, prefix='.seed-metadata-') as temporary:
            temporary.write(json.dumps(value, indent=2, sort_keys=True).encode())
            temporary.flush()
            os.fsync(temporary.fileno())
            os.link(temporary.name, path)  # publish once; never replace an existing file
    write_json(_selection_manifest_path(selection_dir), manifest)
    if fixed_positions_by_pdb is not None:
        write_json(selection_dir / "mutation_fixed_positions.json", fixed_positions_by_pdb)


def _write_seeded_refinement_metadata(
    selection_dir: Path,
    root_job: Job,
    source_job: Job,
    action: str,
    manifest_items: List[Dict[str, Any]],
    fixed_positions_by_pdb: Optional[Dict[str, str]] = None,
    preserve_existing: bool = False,
) -> None:
    _write_selection_manifest(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        source_stage_payload={
            "source_stage_job_id": source_job.id,
            "source_stage_family": _normalize_stage_family(getattr(source_job, "stage_family", None)),
            "source_stage_mode": _normalize_stage_family(getattr(source_job, "stage_mode", None)),
        },
        fixed_positions_by_pdb=fixed_positions_by_pdb,
        preserve_existing=preserve_existing,
    )


def _sequence_positions_from_mutation_meta(mutation_meta: Any) -> List[int]:
    if isinstance(mutation_meta, list):
        positions = []
        for mutation in mutation_meta:
            if not isinstance(mutation, dict):
                continue
            if str(mutation.get("type")).lower() == "substitution":
                try:
                    positions.append(int(mutation["position"]))
                except (TypeError, ValueError):
                    continue
        return sorted(set(positions))

    if not isinstance(mutation_meta, dict):
        return []

    mutation_type = str(mutation_meta.get("type") or "").strip().lower()
    try:
        position = int(mutation_meta.get("position"))
    except (TypeError, ValueError):
        return []

    if mutation_type == "substitution":
        return [position]
    if mutation_type == "insertion":
        inserted = str(mutation_meta.get("to") or "")
        if not inserted:
            return []
        start = position + 1
        return list(range(start, start + len(inserted)))
    return []


def _locked_spec_from_design_pdb(
    pdb_path: Path,
    binder_chain_id: str,
    mutation_meta: Any,
) -> str:
    chain_records = _extract_chain_records_from_pdb(pdb_path)
    binder_records = chain_records.get(str(binder_chain_id).strip().upper()) or []
    if not binder_records:
        return ""

    spec_tokens: List[str] = []
    for position in _sequence_positions_from_mutation_meta(mutation_meta):
        if 1 <= position <= len(binder_records):
            resseq = int(binder_records[position - 1]["resseq"])
            spec_tokens.append(f"{str(binder_chain_id).strip().upper()}{resseq}")
    return ",".join(sorted(set(spec_tokens)))


def _materialize_seed_selection_from_completed_designs(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    design_job_map: Dict[str, Job],
    action: str,
    selection_dir: Optional[Path] = None,
) -> tuple[Path, Path]:
    retained = selection_dir is not None
    if selection_dir is None:
        selection_dir = _create_antibody_selection_dir(action)
    elif selection_dir.exists():
        if selection_dir.is_symlink() or not selection_dir.is_dir():
            raise ValueError('Unsafe retained seed selection')
    else:
        selection_dir.mkdir(parents=True, exist_ok=False)
    expected_names = {f'{index:03d}_{design.id}.pdb' for index, design in enumerate(designs, 1)} | {
        'selection_manifest.json', 'mutation_fixed_positions.json'}
    if retained and any(path.name not in expected_names for path in selection_dir.iterdir()):
        raise ValueError('Foreign file in retained seed selection')
    manifest_items: List[Dict[str, Any]] = []
    fixed_positions_by_pdb: Dict[str, str] = {}

    for idx, design in enumerate(designs, start=1):
        source_path = _resolve_design_structure_path(design.pdb_path)
        dest_path = selection_dir / f"{idx:03d}_{design.id}.pdb"
        if source_path.suffix.lower() in {'.cif', '.mmcif'}:
            if dest_path.exists() or dest_path.is_symlink():
                if dest_path.is_symlink() or not dest_path.is_file():
                    raise ValueError('Unsafe retained CIF-derived seed')
                import tempfile
                with tempfile.TemporaryDirectory(dir=selection_dir.parent) as temporary:
                    comparison = _cif_selection_pdb(source_path, Path(temporary) / dest_path.name)
                    if dest_path.read_bytes() != comparison.read_bytes():
                        raise ValueError('Retained seed differs from native CIF conversion')
            else:
                _cif_selection_pdb(source_path, dest_path)
            link_mode = 'converted_verified_copy'
        elif root_job.execution_target_id:
            # Remote input authority requires a regular immutable snapshot,
            # not a mutable reference into an imported result generation.
            if dest_path.exists() or dest_path.is_symlink():
                if dest_path.is_symlink() or not dest_path.is_file() or dest_path.read_bytes() != source_path.read_bytes():
                    raise ValueError('Retained seed bytes differ from native source')
            else:
                # A killed copy must never publish a partial deterministic seed.
                # Keep the temporary inode outside the selected roster and
                # publish it without replacing any retained or foreign input.
                import tempfile
                with tempfile.NamedTemporaryFile(dir=selection_dir.parent,
                        prefix='.' + selection_dir.name + '-seed-input-') as temporary:
                    shutil.copyfile(source_path, temporary.name)
                    os.fsync(temporary.fileno())
                    os.link(temporary.name, dest_path)
            link_mode = 'copy'
        else:
            link_mode = _link_selection_input(source_path, dest_path)

        design_job = design_job_map.get(design.job_id)
        params = design_job.params if design_job and isinstance(design_job.params, dict) else {}
        variant_meta = params.get("mutation_variant") if isinstance(params.get("mutation_variant"), dict) else {}
        fixed_spec = ""
        if variant_meta:
            fixed_spec = _locked_spec_from_design_pdb(
                pdb_path=source_path,
                binder_chain_id=str(variant_meta.get("binder_chain_id") or ""),
                mutation_meta=variant_meta.get("mutation"),
            )

        manifest_items.append(_build_selection_manifest_item(
            design,
            source_path=source_path,
            selection_path=dest_path,
            selection_entry_mode=link_mode,
            extra={"mutation_variant": variant_meta},
        ))
        if fixed_spec:
            fixed_positions_by_pdb[dest_path.stem] = fixed_spec

    _write_seeded_refinement_metadata(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        fixed_positions_by_pdb=fixed_positions_by_pdb,
        preserve_existing=retained,
    )
    return selection_dir, selection_dir / "mutation_fixed_positions.json"


def _materialize_antibody_selection(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    action: str,
    *,
    namespace: str = "antibody",
) -> Path:
    selection_root = get_inputs_dir() / "design_selections" / namespace
    selection_root.mkdir(parents=True, exist_ok=True)

    selection_dir = selection_root / (
        f"{datetime.utcnow():%Y%m%d_%H%M%S}_{action}_{uuid.uuid4().hex[:8]}"
    )
    selection_dir.mkdir(parents=True, exist_ok=False)

    manifest_items: List[Dict[str, Any]] = []
    for idx, design in enumerate(designs, start=1):
        if not design.pdb_path:
            raise HTTPException(
                status_code=422,
                detail=f"Design '{design.name}' is missing a structure path.",
            )

        source_path = _resolve_design_structure_path(design.pdb_path)
        dest_path = selection_dir / f"{idx:03d}_{design.id}.pdb"
        native_snapshot = None
        if source_path.suffix.lower() in {'.cif', '.mmcif'}:
            native_dir = selection_dir / 'native'
            native_dir.mkdir(exist_ok=True)
            native_snapshot = native_dir / f"{idx:03d}_{design.id}{source_path.suffix.lower()}"
            shutil.copyfile(source_path, native_snapshot)
            _cif_selection_pdb(native_snapshot, dest_path)
            link_mode = 'converted_verified_copy'
        elif source_path.suffix.lower() != '.pdb':
            raise HTTPException(status_code=422, detail=f"Unsupported selected structure format: {source_path.suffix}")
        else:
            # Local and remote continuations both consume retained snapshots.
            # Linking a mutable publication would change a queued child's input.
            shutil.copyfile(source_path, dest_path)
            link_mode = "copy"

        manifest_items.append(_build_selection_manifest_item(
            design,
            source_path=dest_path if source_path.suffix.lower() in {'.cif', '.mmcif'} else source_path,
            selection_path=dest_path,
            selection_entry_mode=link_mode,
            extra={
                "source_structure_path": str(source_path),
                "selection_structure_sha256": hashlib.sha256(dest_path.read_bytes()).hexdigest(),
                **({"native_source_structure_path": str(native_snapshot),
                    "native_source_structure_sha256": hashlib.sha256(native_snapshot.read_bytes()).hexdigest(),
                    "native_source_format": "mmcif"} if native_snapshot else {}),
            },
        ))

    _write_selection_manifest(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        source_stage_payload=_derive_source_stage_payload(source_job, designs, selection_dir),
    )
    return selection_dir


def _candidate_sidecar_paths(design: Design, source_path: Path) -> List[Path]:
    candidates: List[Path] = []
    if design.json_path:
        candidates.append(_resolve_design_structure_path(design.json_path))
    else:
        sibling_json = source_path.with_suffix(".json")
        if sibling_json.exists():
            candidates.append(sibling_json.resolve())
    for suffix in (".cif", ".npz"):
        candidate = source_path.with_suffix(suffix)
        if candidate.exists():
            candidates.append(candidate.resolve())
    pae_candidate = source_path.with_suffix(".pae.npz")
    if pae_candidate.exists():
        candidates.append(pae_candidate.resolve())
    unique_paths: List[Path] = []
    # A selected CIF is the primary payload, not also its own .cif sidecar.
    # Compare concrete paths so staging aliases do not duplicate it either.
    seen: set[str] = {str(source_path.resolve())}
    for candidate in candidates:
        normalized = str(candidate.resolve())
        if normalized in seen or not candidate.exists():
            continue
        seen.add(normalized)
        unique_paths.append(candidate)
    return unique_paths


def _materialize_protein_local_selection(
    root_job: Job,
    source_job: Job,
    designs: List[Design],
    action: str,
) -> Path:
    selection_dir = _create_generic_selection_dir("protein_local_redesign", action)

    manifest_items: List[Dict[str, Any]] = []
    for idx, design in enumerate(designs, start=1):
        if not design.pdb_path:
            raise HTTPException(
                status_code=422,
                detail=f"Design '{design.name}' is missing a structure path.",
            )
        source_path = _resolve_design_structure_path(design.pdb_path)
        dest_name = source_path.name
        dest_path = selection_dir / dest_name
        if dest_path.exists():
            dest_path = selection_dir / f"{idx:03d}_{dest_name}"
        shutil.copyfile(source_path, dest_path)
        link_mode = "copy"

        for sidecar in _candidate_sidecar_paths(design, source_path):
            sidecar_dest = selection_dir / sidecar.name
            if sidecar_dest.exists():
                sidecar_dest = selection_dir / f"{idx:03d}_{sidecar.name}"
            shutil.copyfile(sidecar, sidecar_dest)

        manifest_items.append(_build_selection_manifest_item(
            design,
            source_path=source_path,
            selection_path=dest_path,
            selection_entry_mode=link_mode,
        ))

    _write_selection_manifest(
        selection_dir=selection_dir,
        root_job=root_job,
        source_job=source_job,
        action=action,
        manifest_items=manifest_items,
        source_stage_payload=_derive_source_stage_payload(source_job, designs, selection_dir),
    )
    return selection_dir


def _build_antibody_iteration_job(
    root_job: Job,
    source_job: Job,
    action: str,
    selection_dir: Path,
    design_ids: List[str],
    name_suffix: Optional[str],
    param_overrides: Dict[str, Any],
    saved_filter_set: Optional[SavedReviewFilterSet] = None,
    selected_designs: Optional[List[Design]] = None,
) -> JobCreate:
    action = action.strip().lower()
    action_map = {
        "validate_boltz2": {
            "suffix": "validate_boltz2",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": False,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": True,
                "structure_validator": "boltz2",
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "interactive_swa": True,
                "interactive_gating": True,
                "interactive_gate_stage": "post_structure_validation",
            },
        },
        "validate_protenix": {
            "suffix": "validate_protenix",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": True,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": True,
                "structure_validator": "protenix",
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "interactive_swa": True,
                "interactive_gating": True,
                "interactive_gate_stage": "post_structure_validation",
            },
        },
        "ppiflow_backbone_refine": {
            "suffix": "ppiflow_backbone_refine",
            "params": {
                "skip_rfantibody": True,
                "rfantibody_input_pdbs": str(selection_dir),
                "fampnn_collected_pdbs": None,
                "seq_design_fampnn": False,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": False,
                "run_ppiflow_backbone_refine": True,
                "run_ppiflow_maturation": False,
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "interactive_swa": False,
                "interactive_gating": False,
                "interactive_gate_stage": "post_fampnn",
                "ppiflow_stage_mode": "post_rfantibody",
                "ppiflow_tuning_profile": "stage_optimized",
                "ppiflow_stage_target": "post_rfantibody",
                "ppiflow_rotamer_enrichment_enabled": True,
                "ppiflow_require_anchors": True,
                "ppiflow_rotamer_shell_cutoff": 20.0,
                "maturation_anchor_distance_cutoff": 12.0,
            },
        },
        "ppiflow_maturation": {
            "suffix": "ppiflow_maturation",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": False,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": False,
                "run_ppiflow_backbone_refine": False,
                "run_ppiflow_maturation": True,
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": True,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "interactive_swa": False,
                "interactive_gating": False,
                "interactive_gate_stage": "post_structure_validation",
                "ppiflow_stage_mode": "post_fampnn",
                "ppiflow_tuning_profile": "stage_optimized",
                "ppiflow_stage_target": "post_fampnn",
                "ppiflow_rotamer_enrichment_enabled": True,
                "ppiflow_require_anchors": True,
                "ppiflow_rotamer_shell_cutoff": 20.0,
                "maturation_anchor_distance_cutoff": 12.0,
            },
        },
        "fampnn_redesign": {
            "suffix": "fampnn_redesign",
            "params": {
                "skip_rfantibody": True,
                "rfantibody_input_pdbs": str(selection_dir),
                "fampnn_collected_pdbs": None,
                "seq_design_fampnn": True,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": False,
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": False,
                "run_immunogenicity_scoring": False,
                "interactive_swa": True,
                "interactive_gating": True,
                "interactive_gate_stage": "post_fampnn",
            },
        },
        "frustrampnn": {
            "suffix": "frustrampnn",
            "params": {
                "skip_rfantibody": True,
                "fampnn_collected_pdbs": str(selection_dir),
                "rfantibody_input_pdbs": None,
                "seq_design_fampnn": True,
                "seq_design_antifold": False,
                "seq_design_proteinmpnn": False,
                "run_structure_validation": False,
                "run_post_validation_maturation": False,
                "run_post_boltz_maturation": False,
                "run_maturation": False,
                "run_frustrampnn": True,
                "run_immunogenicity_scoring": False,
                "interactive_swa": False,
                "interactive_gating": False,
                "interactive_gate_stage": "post_structure_validation",
            },
        },
        "ui_refinement": {
            "suffix": "refinement",
            "params": {},
        },
    }
    if action not in action_map:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported antibody iteration action '{action}'. "
                "Allowed: validate_boltz2, validate_protenix, ppiflow_backbone_refine, ppiflow_maturation, fampnn_redesign, frustrampnn, cdr_indel_round, mutation_seeded_refinement, ui_refinement."
            ),
        )

    launch_params = _prune_iteration_params(root_job.params if isinstance(root_job.params, dict) else {})
    source_stage_payload = _derive_source_stage_payload(source_job, selected_designs or [], selection_dir)
    launch_params.update({
        "iteration_source_job_id": source_job.id,
        "iteration_source_root_job_id": root_job.id,
        "iteration_source_design_ids": design_ids,
        "iteration_action": action,
        "iteration_selection_dir": str(selection_dir),
        "interactive_gate_continue": False,
        **source_stage_payload,
    })
    
    # Preserve epitope residue configurations for contact calculations during refinement
    if isinstance(root_job.params, dict):
        for key in ["epitope_residues", "selected_residues"]:
            if key in root_job.params:
                launch_params[key] = root_job.params[key]
                
    launch_params.update(action_map[action]["params"])

    stage_family, stage_mode = _derive_iteration_stage_metadata(action, launch_params)
    launch_params.update({
        "stage_family": stage_family,
        "stage_mode": stage_mode,
        "lineage_root_job_id": root_job.id,
        "selection_source_job_id": source_job.id,
        "selection_source_type": "saved_dataset" if saved_filter_set is not None else "selected_designs",
        "selection_dataset_name": saved_filter_set.name if saved_filter_set is not None else None,
        "selected_loop_scope": _build_selected_loop_scope(launch_params),
        "ppiflow_selected_loops": launch_params.get("ppiflow_selected_loops"),
        "source_stage_job_id": source_stage_payload.get("source_stage_job_id"),
        "source_stage_family": source_stage_payload.get("source_stage_family"),
        "source_stage_mode": source_stage_payload.get("source_stage_mode"),
        "source_selection_manifest_path": source_stage_payload.get("source_selection_manifest_path"),
        "source_selection_count": source_stage_payload.get("source_selection_count"),
        "selected_input_dir": source_stage_payload.get("selected_input_dir"),
        "selected_input_manifest": source_stage_payload.get("selected_input_manifest"),
        "selected_input_stage_family": source_stage_payload.get("selected_input_stage_family"),
        "selected_input_stage_mode": source_stage_payload.get("selected_input_stage_mode"),
        "selected_input_source_job_id": source_stage_payload.get("selected_input_source_job_id"),
        "selected_input_artifact_class": source_stage_payload.get("selected_input_artifact_class"),
        "selected_input_schema_version": source_stage_payload.get("selected_input_schema_version"),
    })

    for key in ["rfantibody_input_pdbs", "fampnn_collected_pdbs"]:
        if launch_params.get(key) is None:
            launch_params.pop(key, None)

    if param_overrides:
        launch_params.update(param_overrides)
        stage_family, stage_mode = _derive_iteration_stage_metadata(action, launch_params)
        launch_params.update({
            "stage_family": stage_family,
            "stage_mode": stage_mode,
            "selected_loop_scope": _build_selected_loop_scope(launch_params),
        })

    if action == "ui_refinement":
        refinement_screen_keys = {
            "enable_rfantibody_filter",
            "rfantibody_screen_reference_scope",
            "rfantibody_min_epitope_contacts",
            "rfantibody_max_epitope_distance",
            "rfantibody_min_target_contacts",
            "rfantibody_max_target_distance",
            "rfantibody_max_epitope_centroid_distance",
            "rfantibody_contact_distance_threshold",
            "rfantibody_target_contact_distance_threshold",
        }

        def _invalid_refinement_value(value: Any) -> bool:
            return value is None or (isinstance(value, str) and (not value.strip() or value.strip() == "refinement_mode"))

        def _has_explicit_refinement_screen_request(overrides: Dict[str, Any]) -> bool:
            if overrides.get("enable_rfantibody_filter") is True:
                return True
            for key in (
                "rfantibody_min_epitope_contacts",
                "rfantibody_max_epitope_distance",
                "rfantibody_min_target_contacts",
                "rfantibody_max_target_distance",
                "rfantibody_max_epitope_centroid_distance",
            ):
                if key in overrides and not _invalid_refinement_value(overrides.get(key)):
                    return True
            return False

        has_refinement_screen_override = _has_explicit_refinement_screen_request(param_overrides or {})

        def _pick_refinement_context(key: str) -> Any:
            for job in (root_job, source_job):
                params = job.params if isinstance(job.params, dict) else {}
                candidate = params.get(key)
                if not _invalid_refinement_value(candidate):
                    return candidate
            return None

        for key in ("target_pdb", "epitope_residues", "selected_residues", "antigen_chains"):
            if _invalid_refinement_value(launch_params.get(key)):
                fallback = _pick_refinement_context(key)
                if fallback is None:
                    launch_params.pop(key, None)
                else:
                    launch_params[key] = fallback

        launch_params["skip_rfantibody"] = True
        
        requested_ppiflow_stage_mode = _normalize_stage_family(launch_params.get("ppiflow_stage_mode"))
        requests_backbone_refine = (
            _to_bool(launch_params.get("run_ppiflow_backbone_refine"))
            or requested_ppiflow_stage_mode in {"post_rfantibody", "backbone_refine", "post_ppiflow", "both"}
        )

        # Sequence-design and sequence-free backbone-retry launches both start from backbone-style inputs.
        # Direct maturation/validation-only launches start from sequence-conditioned collected structures.
        if (
            launch_params.get("seq_design_fampnn")
            or launch_params.get("seq_design_antifold")
            or launch_params.get("seq_design_proteinmpnn")
            or launch_params.get("seq_design_caliby")
            or requests_backbone_refine
        ):
            launch_params["rfantibody_input_pdbs"] = str(selection_dir)
            launch_params["fampnn_collected_pdbs"] = None
        else:
            launch_params["fampnn_collected_pdbs"] = str(selection_dir)
            launch_params["rfantibody_input_pdbs"] = None

        # UI refinement starts from hand-selected structures. Do not reapply the
        # coarse RFantibody screen unless the caller explicitly overrides it.
        if not has_refinement_screen_override:
            launch_params["enable_rfantibody_filter"] = False
            for key in refinement_screen_keys - {"enable_rfantibody_filter"}:
                launch_params.pop(key, None)

    launch_params = _normalize_antibody_job_params(_normalize_structure_geometry_params(launch_params))
    _validate_antibody_iteration_source_compatibility(action, launch_params)
    suffix = name_suffix.strip() if isinstance(name_suffix, str) and name_suffix.strip() else action_map[action]["suffix"]
    job_name = f"{root_job.name}_{suffix}"

    launch_params["rfd_mode"] = ANTIBODY_REFINEMENT_PIPELINE

    return JobCreate(
        name=job_name,
        model_id="template_antibody_denovo",
        mode=ANTIBODY_REFINEMENT_PIPELINE,
        params=launch_params,
        execution_target_id=root_job.execution_target_id,
        execution_policy=ExecutionPolicy.from_params(root_job.params),
        pinned_gpu=None,
    )


def _is_protein_local_redesign_job(job: Job) -> bool:
    return job.model_id == "protein_local_redesign" or (
        job.model_id == "protein_modification_experimental" and job.mode == "region_redesign"
    )


def _mutagenesis_variant_job_params(params: Dict[str, Any], variant: Dict[str, Any], i: int) -> Dict[str, Any]:
    """Build the same generated settings for prevalidation and persistence."""
    # Override sequence with variant-specific sequence
    job_params = dict(params)
    job_params['sequence'] = variant.get('sequence')
    job_params['sequence_name'] = variant.get('name', f'var_{i+1}')
    job_params['mutation_variant'] = {
        'name': variant.get('name'),
        'source_design_id': variant.get('source_design_id'),
        'source_design_name': variant.get('source_design_name'),
        'binder_chain_id': variant.get('binder_chain_id'),
        'mutation': variant.get('mutation'),
        'loop_ids': variant.get('loop_ids'),
        'locked_positions_spec': variant.get('locked_positions_spec'),
    }

    # BATCH-STAGE-GATE: Remove per-variant FrustraMPNN
    # FrustraMPNN runs as a post-batch phase after ALL variants complete
    # This prevents GPU contention and enables single-model-load optimization
    job_params.pop('run_frustrampnn', None)

    variant_complex_components = variant.get('complex_components')
    # Construct complex_components for BoltzFromComplex if any non-protein components present
    # The ligands array contains ALL complex components: ligands, ions, DNA, RNA, peptides
    ligand_components = job_params.pop('ligands', [])

    if variant_complex_components:
        job_params['complex_components'] = variant_complex_components
        logger.info(
            f"[MUTAGENESIS] Using variant-specific complex_components with "
            f"{len(variant_complex_components)} entries for variant {variant.get('name')}"
        )
    # Check if any components need the complex workflow (DNA, RNA, ligands, ions, peptides)
    elif ligand_components:
        # Build complex_components array: protein + all other components
        complex_comps = [
            {'type': 'protein', 'id': 'A', 'sequence': variant.get('sequence')}
        ]
        # Add all components from ligands array (DNA, RNA, ligands, ions, peptides)
        for comp in ligand_components:
            comp_type = comp.get('type', 'ligand')
            comp_entry = {
                'type': comp_type,
                'id': comp.get('id', 'X'),
            }
            # Add sequence for nucleic acids and peptides
            if comp_type in ('dna', 'rna', 'peptide', 'protein') and comp.get('sequence'):
                comp_entry['sequence'] = comp.get('sequence')
            # Add CCD for standard ligands/ions
            if comp.get('ccd'):
                comp_entry['ccd'] = comp.get('ccd')
            # Add SMILES for custom ligands
            if comp.get('smiles'):
                comp_entry['smiles'] = comp.get('smiles')
            complex_comps.append(comp_entry)

        job_params['complex_components'] = complex_comps
        logger.info(f"[MUTAGENESIS] Built complex_components with {len(complex_comps)} entries for variant {variant.get('name')}")
    return job_params


def _native_checkpoint_domain_follow_on(parent, stage, selection_dir, references):
    """Adapt retained artifact identities to the ordinary native refinement policy.

    These are checkpoint-native identities, not imported database Design rows.
    No scientific field is inferred from filenames or selected automatically.
    """
    from types import SimpleNamespace
    from copy import deepcopy
    family, mode = _review_stage_to_canonical_stage(stage)
    source = SimpleNamespace(**{**deepcopy(parent), 'stage_family': family, 'stage_mode': mode,
        'awaiting_input': True, 'awaiting_stage': stage, 'awaiting_payload': {},
        'name': parent.get('name') or parent['id'], 'pinned_gpu': parent.get('pinned_gpu')})
    if not _should_spawn_antibody_refinement_on_resume(source):
        raise ValueError('Native domain follow-on policy does not admit this checkpoint')
    designs = []
    for reference in references:
        designs.append(SimpleNamespace(id=reference.relative_path, name=Path(reference.relative_path).stem,
            job_id=source.id, stage_family=family, stage_mode=mode, source_stage=stage,
            artifact_class=infer_antibody_artifact_class_from_stage(family, mode),
            lineage_root_job_id=source.id, parent_design_id=None, origin_design_id=None,
            origin_backbone_design_id=None, selected_loop_scope=_build_selected_loop_scope(source.params)))
    overrides, _ = _native_gate_resume_params(source, {})
    request = _build_antibody_iteration_job(source, source, 'ui_refinement', selection_dir,
        [design.id for design in designs], 'continued', overrides, selected_designs=designs)
    request.params.update(selection_source_type='review_gate', interactive_gate_continue=True)
    manifest = dict(action='continue_review', root_job_id=source.id, source_job_id=source.id,
        design_count=len(designs), source_selection_count=len(designs),
        **{key: value for key, value in _derive_source_stage_payload(source, designs, selection_dir).items()
           if key not in {'source_selection_count'}},
        designs=[_build_selection_manifest_item(design,
            source_path=Path(source.output_dir) / reference.relative_path,
            selection_path=selection_dir / Path(reference.relative_path).name,
            selection_entry_mode=None, extra={'checkpoint_artifact_sha256': reference.sha256})
            for design, reference in zip(designs, references)])
    return request, _selection_manifest_path(selection_dir), manifest


def _native_gate_resume_params(job, param_overrides: dict, effective_from_stage=None):
    """Existing native Resume behavior shared with checkpoint compilation."""
    if job.awaiting_input:
        awaiting_payload = dict(job.awaiting_payload or {})
        candidate_dir = awaiting_payload.get("candidate_dir")
        output_path = Path(job.output_dir)
        if not output_path.is_absolute():
            output_path = get_data_root() / output_path
        if candidate_dir and _is_protein_local_redesign_job(job):
            if job.awaiting_stage == "post_rfantibody":
                param_overrides.setdefault("plr_backbone_input_pdbs", candidate_dir)
                param_overrides.setdefault(
                    "plr_region_manifest",
                    str(output_path / "inputs" / "protein_local_redesign" / "region_manifest.json"),
                )
            elif job.awaiting_stage == "post_fampnn":
                param_overrides.setdefault("plr_sequence_input_pdbs", candidate_dir)
            elif job.awaiting_stage == "post_structure_validation":
                param_overrides.setdefault("plr_validation_input_pdbs", candidate_dir)
                param_overrides.setdefault("plr_final_candidate_dir", candidate_dir)
        else:
            if candidate_dir and job.awaiting_stage == "post_rfantibody":
                param_overrides.setdefault("rfantibody_input_pdbs", candidate_dir)
            if candidate_dir and job.awaiting_stage == "post_fampnn":
                param_overrides.setdefault("fampnn_collected_pdbs", candidate_dir)
            if candidate_dir and job.awaiting_stage == "post_caliby":
                param_overrides.setdefault("selected_input_dir", candidate_dir)
                param_overrides.setdefault("selected_input_stage_family", "caliby")
                param_overrides.setdefault("selected_input_stage_mode", "post_caliby")
                param_overrides.setdefault("selected_input_artifact_class", SEQUENCE_DESIGNED_COMPLEX)
                param_overrides.setdefault("selected_input_schema_version", ANTIBODY_PIPELINE_CONTRACT_VERSION)
        if job.awaiting_stage in {"post_rfantibody", "post_ppiflow_generator", "post_fampnn", "post_caliby", "post_structure_validation"}:
            param_overrides.setdefault("interactive_gate_continue", True)
            param_overrides.setdefault("interactive_swa", _to_bool((job.params or {}).get("interactive_swa")))
            param_overrides.setdefault("interactive_gating", _to_bool((job.params or {}).get("interactive_gating")))
        if not effective_from_stage:
            effective_from_stage = _awaiting_stage_to_resume_hint(job.awaiting_stage)

    return param_overrides, effective_from_stage
