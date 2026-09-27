"""General source adapter for the existing durable round and ordinary Jobs.

No target roles, prediction stage, sampling defaults or result parser live here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from pydantic import ConfigDict, create_model
from schemas import JobCreate, SequenceDesignRequest
from services.caliby_native import Conformer, EnsembleDesign

REQUEST = 'sequence_design_request'
MODES = {'proteinmpnn': 'design', 'fampnn': 'design', 'caliby_experimental': 'ensemble_design'}
BOUND_INPUTS = {'input_pdb', 'design_chain', 'target_chain', 'binder_chains', 'target_chains'}
# Project the native classes, rather than maintaining another numerical schema.
CalibySettings = create_model('CalibySequenceSettings', __config__=ConfigDict(extra='forbid'),
    **{key: (field.annotation, deepcopy(field)) for key, field in EnsembleDesign.model_fields.items()
       if key != 'ensembles'})
ConformerSettings = create_model('CalibyConformerSettings', __config__=ConfigDict(extra='forbid'),
    **{key: (field.annotation, deepcopy(field)) for key, field in Conformer.model_fields.items()
       if key not in {'state_id', 'path'}})


def normalize_request(request, registry=None):
    from model_registry import get_registry
    from services.sequence_designer_settings import normalize_historical_sequence_settings
    registry = registry or get_registry()
    result = SequenceDesignRequest.model_validate(request).model_copy(deep=True)
    if result.model_id == 'caliby_experimental':
        result.params = CalibySettings.model_validate(result.params).model_dump(mode='json')
        # Empty means native defaults; preserve explicitly supplied empty fields.
        result.input_settings = ConformerSettings.model_validate(result.input_settings).model_dump(
            mode='json', exclude_unset=True)
        return result
    if result.input_settings:
        raise ValueError('input_settings applies only to Caliby conformers')
    definition = registry.get_model(result.model_id)
    mode = next(item for item in definition.modes if item.id == MODES[result.model_id])
    fields = {field.name: field for field in definition.params if field.name in mode.params}
    supplied = normalize_historical_sequence_settings(result.model_id, result.params)
    unknown = set(supplied) - (fields.keys() - BOUND_INPUTS)
    if unknown:
        raise ValueError(f'Unknown or source-bound {result.model_id} settings: {sorted(unknown)}')
    defaults = {key: deepcopy(field.default) for key, field in fields.items()
                if field.default is not None and key not in BOUND_INPUTS}
    result.params = {**defaults, **supplied}
    errors = registry.validate_job_params(result.model_id, MODES[result.model_id], result.params)
    errors = [error for error in errors if error != 'Missing required parameter: input_pdb']
    if errors:
        raise ValueError(str(errors))
    return result


def design_request(root, owner, design, request):
    from services.binder_continuation import snapshot_selection, model_request, individual_model_requests
    from routers.jobs import _materialize_protein_local_selection
    params = deepcopy(request.params)
    if request.model_id == 'caliby_experimental':
        # Native Caliby accepts CIF directly; never force PDB conversion here.
        directory = _materialize_protein_local_selection(root, owner, [design], 'sequence_design')
        row = json.loads((directory / 'selection_manifest.json').read_text())['designs'][0]
        path = row['selection_pdb_path']
        params['ensembles'] = [{'ensemble_id': design.id, 'states': [
            {'state_id': design.id, 'path': path, **deepcopy(request.input_settings)}]}]
        child = JobCreate(name=f'sequence-{design.id}', model_id=request.model_id,
            mode=MODES[request.model_id], params=params, execution_target_id=root.execution_target_id)
    else:
        # Existing governed snapshot/converter retains native identity for PDB-only consumers.
        directory = snapshot_selection(owner, root, [design])
        row = json.loads((directory / 'selection_manifest.json').read_text())['designs'][0]
        path = row['selection_pdb_path']
        # Both native adapters require an explicit designed-chain selection for
        # complexes. Bind every source protein chain, not synthetic binder/target
        # roles; fixed-position constraints remain the model's own controls.
        if request.model_id == 'fampnn':
            from scripts.prep_fampnn_constraints_generic import pdb_domain
        else:
            from scripts.proteinmpnn_native_binding import pdb_domain
        params['design_chain'] = ','.join(dict.fromkeys(key[0] for key in pdb_domain(path)))
        params['target_chain'] = ''
        base = model_request(operation=request.model_id, params=params, source=owner, root=root,
            selection_dir=directory, execution_target_id=root.execution_target_id)
        child = individual_model_requests(base, request.model_id, directory)[0]
        child.mode = MODES[request.model_id]
        row = json.loads((directory / 'selection_manifest.json').read_text())['designs'][0]
        path = row['selection_pdb_path']
    native_path = row.get('native_source_structure_path') or path
    binding = {'source_binding': {'job_id': owner.id, 'design_id': design.id,
        'path': path, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        'native_path': native_path, 'native_sha256': hashlib.sha256(Path(native_path).read_bytes()).hexdigest(),
        'selection_manifest': str(directory / 'selection_manifest.json')},
        'lineage_root_job_id': root.id, 'kind': 'general_sequence_design'}
    return child, binding
