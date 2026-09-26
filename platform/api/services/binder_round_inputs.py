"""Initial-round adapters. Native models own settings, sampling and output identity."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import sys

from schemas import BinderRoundRequest, JobCreate

MODES = {'proteinmpnn': 'design', 'fampnn': 'binder_design', 'caliby_binder': 'design',
         'protenix': 'complex', 'boltz2': 'complex', 'esmfold2': 'complex'}


BLIND_FIXED_PARAMETERS = {'protenix_use_template', 'colabfold_use_templates',
    'protenix_anchor_target', 'protenix_anchor_strict', 'boltz_anchor_target', 'boltz_anchor_strict'}
BOUND_INPUTS = {'input_pdb', 'pdb_paths', 'selected_input_dir', 'selected_input_manifest',
    'source_identity_json', 'binder_chains', 'target_chains', 'design_chain', 'target_chain', 'sequence', 'sequence_name',
    'chain_id', 'pdb_sequence_path', 'pdb_chain_ids', 'dna_sequence', 'dna_chain_id',
    'rna_sequence', 'rna_chain_id', 'ligand_smiles', 'ligand_ccd', 'ligand_chain_id',
    'complex_components', 'complex_components_json'}


def normalize_request(request, registry=None):
    from model_registry import get_registry
    registry = registry or get_registry()
    result = BinderRoundRequest.model_validate(request).model_copy(deep=True)
    for stage in (result.sequence_design, result.prediction):
        definition = registry.get_internal_model_definition(stage.model_id)
        if definition is None or not any(m.id == MODES[stage.model_id] for m in definition.modes):
            raise ValueError(f'{stage.model_id} does not support ordinary {MODES[stage.model_id]} execution')
        known = {field.name for field in definition.params}
        unknown = set(stage.params) - known
        if unknown:
            raise ValueError(f'Unknown {stage.model_id} settings: {sorted(unknown)}')
        if any(stage.params.get(key) for key in BOUND_INPUTS):
            raise ValueError('Round source inputs are bound from the selected candidate and independent target')
        if stage is result.prediction and any(stage.params.get(key) for key in BLIND_FIXED_PARAMETERS):
            raise ValueError('Blind prediction does not accept pose conditioning')
        defaults = {field.name: deepcopy(field.default) for field in definition.params
                    if field.default is not None and field.name not in BOUND_INPUTS}
        if stage is result.prediction:
            defaults.update({key: False for key in BLIND_FIXED_PARAMETERS if key in known})
        stage.params = {**defaults, **stage.params}
        errors = registry.validate_job_params(stage.model_id, MODES[stage.model_id], stage.params)
        # Input requirements are fulfilled from producer-owned sources at the
        # ordinary create boundary, not from fabricated placeholders here.
        errors = [error for error in errors
                  if error not in {f'Missing required parameter: {key}' for key in BOUND_INPUTS}]
        if errors:
            raise ValueError(str(errors))
    return result


def chains(value):
    if isinstance(value, str):
        return [x.strip() for x in value.split(',') if x.strip()]
    return list(value or [])


def candidate_roles(owner, design, request):
    from services.binder_diagnostic_selection import documents
    provenance = design.provenance or {}
    rows = documents(owner, design)
    primary = next((row for row in rows if row.get('primary')), {})
    roles = provenance.get('chain_roles') or design.review_role_map or {}
    named_roles = roles.get('chain_roles') or {}
    declared_binder = [chain for chain, role in named_roles.items()
                       if role in {'binder', 'antibody_heavy', 'antibody_light'}]
    declared_target = [chain for chain, role in named_roles.items() if role == 'target']
    params = owner.params or {}
    binder = chains(request.binder_chains or primary.get('binder_chains') or provenance.get('binder_chains')
                    or roles.get('binder') or roles.get('binder_chains') or declared_binder
                    or params.get('binder_chains') or params.get('binder_chain'))
    if not binder:
        binder = [params[key] for key in ('heavy_chain', 'light_chain') if params.get(key)]
    target = chains(request.target_chains or primary.get('target_chains') or provenance.get('target_chains')
                    or roles.get('target') or roles.get('target_chains') or declared_target
                    or params.get('target_chains') or params.get('target_chain')
                    or params.get('antigen_chains') or params.get('antigen_chain'))
    if not binder:
        raise ValueError('Candidate has no declared binder chain roles; set binder_round.binder_chains')
    return binder, target


def requires_design(owner, design):
    if (owner.provenance or {}).get('binder_round_step', {}).get('stage') == 'sequence_design':
        return False
    # Explicit producer artifact semantics, never composition/name inference.
    artifact = design.artifact_class or (design.provenance or {}).get('artifact_class')
    if artifact in {'binder_backbone', 'backbone_complex', 'antibody_backbone', 'backbone', 'generated_backbone'}:
        return True
    if artifact in {'binder_complex', 'binder_sequence', 'designed_sequence', 'designed_structure',
                    'antibody_complex', 'sequence_design', 'sequence_designed_complex',
                    'validated_complex', 'post_validation_refined_complex'}:
        return False
    # These publication owners explicitly emit designed sequence-bearing candidates.
    if owner.model_id in {'bindcraft2', 'boltzgen'}:
        return False
    raise ValueError(f'Candidate sequence semantics unavailable for artifact class {artifact!r}')


def source_components(path, chain_ids, role):
    from paths import get_code_root
    scripts = str(get_code_root() / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from run_binder_blind_pose import _source
    path = Path(path)
    components = _source(path, chain_ids, role=role)
    # Retain author identity in the exact sequence extraction order, solely for
    # later comparison. Neither these rows nor coordinates enter prediction.
    rows = {chain: {} for chain in chain_ids}
    if path.suffix.lower() in {'.cif', '.mmcif'}:
        from Bio.PDB.MMCIF2Dict import MMCIF2Dict
        cif = MMCIF2Dict(str(path))
        count = len(cif.get('_atom_site.id', []))
        columns = {name: cif.get('_atom_site.' + name, [''] * count) for name in (
            'auth_asym_id', 'group_PDB', 'label_asym_id', 'label_seq_id', 'pdbx_PDB_ins_code', 'auth_seq_id')}
        def col(name):
            return columns[name]
        for i in range(count):
            chain = col('auth_asym_id')[i]
            if chain not in rows or col('group_PDB')[i] != 'ATOM':
                continue
            key = (col('label_asym_id')[i], col('label_seq_id')[i])
            insertion = col('pdbx_PDB_ins_code')[i]
            rows[chain].setdefault(key, {'chain_id': chain, 'residue_number': int(col('auth_seq_id')[i]),
                'insertion_code': '' if insertion in {'.', '?'} else insertion})
    else:
        from run_esmfold2_inference import _map_residue_to_letter
        for line in path.read_text().splitlines():
            if not line.startswith(('ATOM  ', 'HETATM')):
                continue
            chain, name = line[21:22].strip() or '_', line[17:20].strip().upper()
            if chain not in rows or not _map_residue_to_letter(name, 'protein'):
                continue
            key = (line[22:27].strip(), name)
            rows[chain].setdefault(key, {'chain_id': chain, 'residue_number': int(line[22:26]),
                                       'insertion_code': line[26:27].strip()})
    for component in components:
        residues = list(rows[component['id']].values())
        component['source_residues'] = residues if len(residues) == len(component['sequence']) else None
    return components


def design_request(root, owner, design, request, binder, target):
    from services.binder_continuation import snapshot_selection, model_request, individual_model_requests
    directory = snapshot_selection(owner, root, [design])
    model = request.sequence_design.model_id
    operation = 'caliby' if model == 'caliby_binder' else model
    params = deepcopy(request.sequence_design.params)
    params.update(binder_chains=','.join(binder), target_chains=','.join(target))
    if model in {'proteinmpnn', 'fampnn'}:
        params['design_chain'] = ','.join(binder)
    if model == 'fampnn':
        params['target_chain'] = ','.join(target)
    base = model_request(operation=operation, params=params, source=owner, root=root,
                         selection_dir=directory, execution_target_id=root.execution_target_id)
    return individual_model_requests(base, operation, directory)[0]


def _residue_correspondence(entries, *, reverse=False):
    """Decode a producer's explicit bijection, not an alignment or identity guess."""
    from services.binder_pose_comparison import _selector_key
    if not isinstance(entries, list):
        return {}
    mapping, used = {}, set()
    try:
        for row in entries:
            source, output = row['source'], row['output']
            first, second = (output, source) if reverse else (source, output)
            key, value_key = _selector_key(first), _selector_key(second)
            if key in mapping or value_key in used:
                return {}
            mapping[key] = dict(second)
            used.add(value_key)
    except (KeyError, TypeError, ValueError):
        return {}
    return mapping


def comparison_reference(owner, design, path):
    """Read immutable source bindings; missing comparison data never gates prediction."""
    import json
    from paths import resolve_runtime_data_path
    prior = (owner.provenance or {}).get('binder_round_step') or {}
    if prior.get('stage') != 'sequence_design':
        return path, None, (design.provenance or {}).get('target_residue_mapping')
    try:
        manifest = json.loads(resolve_runtime_data_path(owner.params['selected_input_manifest']).read_text())
        rows = [row for row in manifest['designs'] if row['design_id'] == prior['backbone_design_id']]
        if len(rows) != 1:
            return None, {}, None
        row = rows[0]
        staged = resolve_runtime_data_path(row['selection_pdb_path'])
        source_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
        if source_hash != row['selection_structure_sha256']:
            return None, {}, None
        reference = resolve_runtime_data_path(row.get('native_source_structure_path') or str(staged))
        expected = row.get('native_source_structure_sha256') or source_hash
        if hashlib.sha256(reference.read_bytes()).hexdigest() != expected:
            return None, {}, None
        target_mapping = (row.get('source_design_provenance') or {}).get('target_residue_mapping')
        # The retained native-to-PDB converter preserves author identities. The
        # designer binds its actual original PDB input by bytes, not worker paths.
        mapping = {}
        try:
            record = json.loads(resolve_runtime_data_path(design.json_path).read_text()) if design.json_path else {}
            if record.get('source_structure_sha256') == source_hash:
                mapping = _residue_correspondence(record.get('source_residue_mapping'), reverse=True)
        except (OSError, ValueError, TypeError):
            pass
        return reference, mapping, target_mapping
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, {}, None


def _reference_residues(component, *, bound, mapping=None):
    residues = component.get('source_residues')
    if not bound or not isinstance(residues, list) or len(residues) != len(component['sequence']):
        return [None] * len(component['sequence'])
    return [dict(chain_id=row['chain_id'], auth_seq_id=row['residue_number'],
                 insertion_code=row['insertion_code']) if mapping is None else
            mapping.get((row['chain_id'], row['residue_number'], row['insertion_code']))
            for row in residues]


def _target_correspondence(value, source_hash):
    candidates = value if isinstance(value, list) else [value]
    matches = [row for row in candidates if isinstance(row, dict) and source_hash
               and row.get('source_sha256') == source_hash]
    return _residue_correspondence(matches[0].get('residues')) if len(matches) == 1 else {}


def prediction_request(root, owner, design, request, binder, target, target_source):
    from paths import resolve_runtime_data_path
    path = resolve_runtime_data_path(design.pdb_path)
    reference, binder_mapping, target_mapping = comparison_reference(owner, design, path)
    if binder_mapping:
        # Native designers may rename chains. Project the retained input roles
        # through their actual output map before extracting designed sequences.
        output_binder = list(dict.fromkeys(key[0] for key, source in binder_mapping.items()
                                           if source['chain_id'] in binder))
        output_target = list(dict.fromkeys(key[0] for key, source in binder_mapping.items()
                                           if source['chain_id'] in target))
        if output_binder and not set(output_binder).intersection(output_target):
            binder, target = output_binder, output_target
    binder_components = source_components(path, binder, 'binder')
    target_path = target_source.get('target_path')
    # A generated-complex role override is not an independent source-document
    # chain label. Use that source owner's explicit chain selection first.
    target_ids = chains(target_source.get('chains') or request.target_chains or target)
    if target_source.get('components'):
        targets = deepcopy(target_source['components'])
        if target_ids:
            targets = [c for c in targets if c['id'] in target_ids]
        target_ids = [c['id'] for c in targets]
        target_binding = deepcopy(target_source)
    else:
        target_path = resolve_runtime_data_path(target_path)
        targets = source_components(target_path, target_ids, 'target')
        target_binding = {**target_source, 'sha256': hashlib.sha256(target_path.read_bytes()).hexdigest()}
    if not targets:
        raise ValueError('No independently declared target protein sequence')
    # Input labels may collide across independent documents. Emit unique native
    # labels and record exact source roles/order; output labels are model-owned.
    reference_hash = hashlib.sha256(reference.read_bytes()).hexdigest() if reference else None
    # Ordinary separate-target inputs use producer-emitted residue joins. Exact
    # document identity remains valid without requiring a redundant mapping.
    target_same_document = bool(not target_source.get('components') and reference_hash
                                and target_binding.get('sha256') == reference_hash)
    target_correspondence = None if target_same_document else _target_correspondence(
        target_mapping, target_binding.get('sha256'))
    components, mapping = [], []
    for role, group in [('binder', binder_components), ('target', targets)]:
        for component in group:
            index = len(components)
            label = chr(65 + index) if index < 26 else f'C{index}'
            components.append({'id': label, 'type': 'protein', 'sequence': component['sequence']})
            mapping.append({'index': index, 'id': label, 'source_chain': component['id'],
                            'role': role, 'sequence': component['sequence'],
                            'source_residues': component.get('source_residues'),
                            'reference_residues': _reference_residues(component,
                                bound=reference is not None,
                                mapping=binder_mapping if role == 'binder' else target_correspondence)})
    params = deepcopy(request.prediction.params)
    params.update(complex_components=components, sequence=':'.join(c['sequence'] for c in components),
                  sequence_name=design.id, lineage_root_job_id=root.id,
                  iteration_source_root_job_id=root.id, iteration_source_job_id=owner.id,
                  iteration_source_design_ids=[design.id], source_design_id=design.id,
                  source_stage_job_id=owner.id, selection_source_job_id=owner.id,
                  selection_source_type='selected_designs', source_selection_count=1)
    child = JobCreate(name=f'predict-{design.id}', model_id=request.prediction.model_id,
                      mode=MODES[request.prediction.model_id], params=params,
                      execution_target_id=root.execution_target_id)
    binding = {'source_binding': {'job_id': owner.id, 'design_id': design.id,
                                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
               'target_binding': target_binding, 'input_components': mapping,
               'binder_chains': binder, 'target_chains': target_ids,
               'pose_comparison': {'reference_sha256': reference_hash}}
    return child, binding
