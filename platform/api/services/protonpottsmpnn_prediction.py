"""Selected native pH-redesign sequences use the ordinary prediction owners.

The retained complex supplies sequence context only. No redesigned sequence is
represented as a refolded structure, and no Potts metric becomes an admission
criterion for prediction.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from database import Job
from schemas import JobCreate


class PredictionSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    design_ids: list[str] = Field(min_length=1)
    model_id: Literal['protenix', 'boltz2']
    params: dict[str, Any] = Field(default_factory=dict)
    execution_target_id: str | None = None
    launch_context_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


def source_components(path: Path) -> list[dict]:
    """Reuse the existing protein sequence-source parsers, never pose templates."""
    from services.binder_round_inputs import source_components as selected_components
    from scripts.run_esmfold2_inference import parse_pdb_polymer_components

    if path.suffix.lower() == '.pdb':
        return [{key: row[key] for key in ('id', 'type', 'sequence')}
                for row in parse_pdb_polymer_components(path, include_dna_rna=False)]
    from Bio.PDB import MMCIFParser
    structure = MMCIFParser(QUIET=True).get_structure('retained', str(path))
    chains = [chain.id for chain in structure[0]
              if any(residue.id[0] == ' ' for residue in chain)]
    return [{key: row[key] for key in ('id', 'type', 'sequence')}
            for row in selected_components(path, chains, 'retained context')]


def prediction_requests(source: Job, root: Job, result: dict,
                        components: list[dict], request: PredictionSelection) -> list[JobCreate]:
    """Bind explicitly selected native IDs and sequences into normal JobCreate."""
    from routers.jobs import normalize_job_request
    from services.binder_round_inputs import BOUND_INPUTS

    if len(set(request.design_ids)) != len(request.design_ids):
        raise ValueError('Duplicate ProtonPottsMPNN design IDs')
    rows = {row['design_id']: row for row in result['designs']}
    missing = set(request.design_ids) - rows.keys()
    if missing:
        raise ValueError(f'Selected ProtonPottsMPNN design not found: {sorted(missing)}')
    replaced = [key for key in BOUND_INPUTS if request.params.get(key)]
    if replaced:
        raise ValueError(f'Prediction sequence inputs are bound from the selected redesign: {sorted(replaced)}')
    children = []
    for identity in request.design_ids:
        native = rows[identity]['native']
        chain = native['binder_chain']
        sequence = native['canonical_sequence']
        if sum(row['id'] == chain for row in components) != 1:
            raise ValueError('Redesigned binder chain is not present in its retained source')
        selected = deepcopy(components)
        for row in selected:
            if row['id'] == chain:
                row['sequence'] = sequence
        params = deepcopy(request.params)
        params.update(
            complex_components=selected,
            sequence=':'.join(row['sequence'] for row in selected),
            sequence_name=identity,
            source_stage_job_id=source.id,
            selection_source_job_id=source.id,
            selection_source_type='selected_native_sequences',
            source_selection_count=1,
            lineage_root_job_id=root.id,
            iteration_source_root_job_id=root.id,
            iteration_source_job_id=source.id,
            iteration_source_design_ids=deepcopy((source.params or {}).get('iteration_source_design_ids', [])),
            iteration_action=f'validate_{request.model_id}',
        )
        child = JobCreate(name=f'{request.model_id}-{identity}'[:255],
                          model_id=request.model_id, mode='complex', params=params,
                          execution_target_id=request.execution_target_id)
        children.append(normalize_job_request(child))
    return children


async def launch_prediction(job_id: str, request: PredictionSelection,
                            background_tasks: BackgroundTasks, session, experiment_session):
    from services.binder_continuation import resolve_root
    from services.protonpottsmpnn_design import prepared_source_path, read_result
    from routers.jobs import submit_selected_child_jobs

    source, root = await resolve_root(session, job_id)
    if source.model_id != 'protonpottsmpnn' or source.mode != 'redesign':
        raise HTTPException(422, 'Select a ProtonPottsMPNN redesign Job')
    try:
        result = await read_result(session, source.id)
        components = source_components(Path(prepared_source_path(source)))
        children = prediction_requests(source, root, result, components, request)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    context = {'source_job_id': source.id, 'root_job_id': root.id,
               'operation': f'predict_{request.model_id}',
               'design_ids': list(request.design_ids),
               'selected_design_count': len(request.design_ids)}
    launched = await submit_selected_child_jobs(
        children, background_tasks, session, experiment_session,
        destination_launch_context_id=request.launch_context_id,
        idempotency_key=request.idempotency_key or f'{job_id}:{request.model_id}:{",".join(request.design_ids)}',
        response_context=context,
    )
    return {**context, 'launched_jobs': launched}
