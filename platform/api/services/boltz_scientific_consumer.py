"""Read marked Boltz evidence through its persisted owning Job, never caller claims."""
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from database import Design, Job
from services.boltz_scientific_persistence import _verified_publication, _revalidate
from services.core_protein_scientific_contract import revision_for_job
from services.frustrampnn.contracts import canonical_json_bytes


# Producer summaries, not a universal ranking or profile-derived means.
SCALAR_DESCRIPTORS = {
    'ptm': ('dimensionless', 'overall', 'higher_is_better'),
    'complex_plddt': ('fraction', 'complex', 'higher_is_better'),
    'confidence_score': ('dimensionless', 'native_weighted_complex_confidence', 'higher_is_better'),
    'iptm': ('dimensionless', 'native_interchain', 'higher_is_better'),
    'ligand_iptm': ('dimensionless', 'native_ligand_interface', 'higher_is_better'),
    'protein_iptm': ('dimensionless', 'native_protein_interface', 'higher_is_better'),
    'complex_iplddt': ('fraction', 'native_interface_weighted_complex', 'higher_is_better'),
    'complex_pde': ('angstrom', 'native_contact_weighted_pairs', 'lower_is_better'),
    'complex_ipde': ('angstrom', 'native_contact_weighted_interchain_pairs', 'lower_is_better'),
}


def scalar_records(confidence, source, producer_version):
    import math
    from services.core_protein_scientific_contract import validate_metric
    if not isinstance(confidence, dict):
        raise ValueError("native confidence must be an object")
    records = []
    for key, (unit, scope, direction) in SCALAR_DESCRIPTORS.items():
        value = confidence.get(key)
        state, reason = 'ok', None
        if value is None:
            state, reason = 'unavailable', 'not_reported'
        elif (type(value) not in (int, float) or not math.isfinite(value)
              or value < 0 or (unit != 'angstrom' and value > 1)):
            state, reason, value = 'invalid', 'outside_native_domain', None
        records.append(validate_metric(dict(metric_key=key, state=state, value=value,
            reason_code=reason, unit=unit, scope=scope, direction=direction,
            producer_version=producer_version, derivation_version='boltz-native-scalar-v1',
            source=source), expected_source=source))
    return records


async def verified_boltz_design(design, session) -> dict[str, Any]:
    """Return freshly verified native identity; no UUID allocation or DB writes.

    Verify selected bytes against launch/workflow authority and the committed
    receipt. Cohort admission is deliberately not repeated for a selected view.
    """
    from paths import get_data_root, resolve_runtime_data_path
    with session.no_autoflush:
        job = await session.scalar(select(Job).where(Job.id == design.job_id))
        if job is None or revision_for_job(job) != 1 or job.model_id not in ('boltz', 'boltz2'):
            raise ValueError('missing_producer_native_axis_ledger')
        if not isinstance(job.output_dir, str) or not job.output_dir:
            raise ValueError('missing_producer_publication_root')
        root = Path(job.output_dir)
        root = resolve_runtime_data_path(root) if root.is_absolute() else get_data_root() / root
        row = await session.scalar(select(Design).where(
            Design.id == design.id, Design.job_id == job.id, Design.source_stage.is_(None)))
        if row is None:
            raise ValueError('foreign selected design')
        prepared, receipt = _verified_publication(job, root, document=row.name)
        selected = prepared[row.name]
        prior = (job.provenance or {})['core_protein_candidate_publication']
        expected_receipt = dict(receipt,
            workflow_inventory=prior['workflow_inventory'],
            task_bindings={key: prior['task_bindings'][key] for key in receipt['task_bindings']},
            manifest=prior['candidates'][row.name]['manifest'],
            candidates={row.name: prior['candidates'][row.name]})
        if canonical_json_bytes(expected_receipt) != canonical_json_bytes(receipt):
            raise ValueError('persisted publication receipt mismatch')
        confidence = row.confidence_metrics or {}
        expected = dict(selected['block'], design_id=row.id)
        artifacts = selected['artifacts']
        if (canonical_json_bytes(confidence.get('core_protein_scientific')) != canonical_json_bytes(expected)
                or confidence.get('core_protein_candidate_artifacts') != artifacts
                or row.pdb_path != artifacts['structure']['path']
                or row.json_path != artifacts['metrics']['path']
                or row.aligned_error_path != artifacts['pae']['path']
                or row.aligned_error_format != 'boltz_pae_npz'
                or row.aligned_error_key != selected['native']['aligned_error']['matrix_key']):
            raise ValueError('persisted native design binding mismatch')
        # Enrich readback only after the historical compact block and every
        # source byte were verified. Do not rewrite rows or alter replay admission.
        import json
        selected['block'] = dict(selected['block'], metrics=scalar_records(
            json.loads(selected['snapshots']['metrics']),
            dict(artifact_sha256=artifacts['metrics']['sha256'],
                 candidate_id=selected['block']['candidate_id'], document_id=selected['block']['document_id']),
            selected['native']['provider_revision']))
        # All projections use the no-follow snapshots, not reopened paths.
        return dict(selected, design_id=row.id, publication_root=root, publication_receipt=receipt)


def retained_cp_paths(design, job):
    """Exact historical split-output transport; never scan basenames/other samples."""
    import re
    from paths import get_data_root, resolve_runtime_data_path
    if (job is None or job.id != design.job_id or job.model_id != 'boltz_cp_experimental'
            or revision_for_job(job) == 1 or not job.output_dir or not design.pdb_path):
        return None
    root = Path(job.output_dir)
    root = resolve_runtime_data_path(root) if root.is_absolute() else get_data_root() / root
    structure = Path(design.pdb_path)
    structure = resolve_runtime_data_path(structure) if structure.is_absolute() else get_data_root() / structure
    key = structure.relative_to(root)
    if (len(key.parts) != 5 or key.parts[:2] != ('cif_files', 'predictions')
            or not re.fullmatch(r'predictions_dp\d+_cp0', key.parts[2])):
        return None
    match = re.fullmatch(r'(.+)_model_(\d+)\.cif', key.name)
    if not match or match[1] != key.parts[3]:
        return None
    relative = Path(*key.parts[1:-1])
    return root, dict(structure=key.as_posix(),
        metrics=(Path('json_files') / relative / f'confidence_{structure.stem}.json').as_posix(),
        ledger=f'npz_files/predictions/processed/structures/{match[1]}.npz',
        **{role: (Path('npz_files') / relative / f'{role}_{structure.stem}.npz').as_posix()
           for role in ('pae', 'pde', 'plddt')}), dict(
        producer_method='boltz_cp_experimental', producer_sample=match[1],
        producer_rank=int(match[2]), producer_output_key=key.as_posix(),
        partition=key.parts[2], rank_scope='within_native_input_confidence_order')


def retained_cp_snapshot(design, job, roles=('structure', 'metrics', 'ledger', 'pae', 'plddt')) -> dict[str, Any]:
    from services.boltz_scientific_persistence import _snapshot
    found = retained_cp_paths(design, job)
    if found is None:
        raise ValueError('unsupported_retained_sample_layout')
    root, keys, producer = found
    artifacts, snapshots = {}, {}
    for role in roles:
        artifacts[role], snapshots[role] = _snapshot(root, keys[role])
    return dict(design_id=design.id, root=root, keys=keys, producer=producer,
                artifacts=artifacts, snapshots=snapshots)


def _cp_native_axis(selected):
    """Map the retained processed token order to exact CIF atom identities.

    Only the pinned standard-residue / nonstandard-atom tokenization is used.
    A mismatch leaves the optional metric unavailable, never guesses from B fields.
    """
    import io
    import numpy as np
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    from services.scientific_viewer_contract import NativeAxis
    with np.load(io.BytesIO(selected['snapshots']['ledger']), allow_pickle=False) as z:
        chains, residues, atoms, mask = (z[k] for k in ('chains', 'residues', 'atoms', 'mask'))
    cif = MMCIF2Dict(io.StringIO(selected['snapshots']['structure'].decode()))
    def column(key):
        return cif['_atom_site.' + key]
    def clean(v):
        return '' if v in ('.', '?') else v
    def integer(v):
        return int(v) if clean(v) else None
    atom_rows = {}
    for i, name in enumerate(column('label_atom_id')):
        chain = column('auth_asym_id')[i]
        seq = integer(column('auth_seq_id')[i])
        resname = column('label_comp_id')[i]
        identity = (chain, seq, resname, name)
        if identity in atom_rows:
            raise ValueError('ambiguous_written_atom_identity')
        atom_rows[identity] = dict(chain_id=chain, residue_name=resname,
            insertion_code=clean(column('pdbx_PDB_ins_code')[i]),
            selected_model=int(column('pdbx_PDB_model_num')[i]),
            selected_altloc=clean(column('label_alt_id')[i]),
            auth_asym_id=chain, auth_seq_id=seq,
            label_asym_id=column('label_asym_id')[i], label_seq_id=integer(column('label_seq_id')[i]),
            source_entity_id=column('label_entity_id')[i], entity_instance_id=column('label_asym_id')[i],
            label_atom_id=name, auth_atom_id=cif.get('_atom_site.auth_atom_id', column('label_atom_id'))[i],
            element=column('type_symbol')[i])
    tokens, chain_map, seen = [], [], set()
    if mask.dtype.kind != 'b' or mask.shape != (len(chains),):
        raise ValueError('invalid_native_chain_mask')
    for ci, chain in enumerate(chains):
        if not mask[ci]:
            continue
        chain_map.append(dict(native_asym_id=int(chain['asym_id']), source_chain_index=ci,
            output_asym_id=len(chain_map), chain_id=str(chain['name']),
            native_entity_id=int(chain['entity_id']), native_sym_id=int(chain['sym_id'])))
        start, count = int(chain['res_idx']), int(chain['res_num'])
        if start < 0 or count <= 0 or start + count > len(residues):
            raise ValueError('invalid_native_residue_span')
        chain_atom_indexes = []
        for res in residues[start:start + count]:
            ast, anum = int(res['atom_idx']), int(res['atom_num'])
            if ast < 0 or anum <= 0 or ast + anum > len(atoms):
                raise ValueError('invalid_native_atom_span')
            written = []
            for ai in range(ast, ast + anum):
                identity = (str(chain['name']), int(res['res_idx']) + 1, str(res['name']), str(atoms[ai]['name']))
                if identity in seen or identity not in atom_rows:
                    raise ValueError('native_ledger_written_atom_mismatch')
                seen.add(identity)
                chain_atom_indexes.append(ai)
                written.append(atom_rows[identity])
            if bool(res['is_standard']):
                row = {k:v for k,v in written[0].items() if k not in ('label_atom_id','auth_atom_id','element')}
                tokens.append(dict(row, index=len(tokens)))
            else:
                for row in written:
                    tokens.append(dict(row, index=len(tokens)))
        if chain_atom_indexes != list(range(int(chain['atom_idx']), int(chain['atom_idx']) + int(chain['atom_num']))):
            raise ValueError('native_chain_atom_span_mismatch')
    if seen != set(atom_rows):
        raise ValueError('native_ledger_written_atom_coverage_mismatch')
    binding = dict(candidate_id=selected['design_id'], document_id=selected['producer']['producer_output_key'])
    axis = NativeAxis.model_validate(dict(binding,
        source_sha256=selected['artifacts']['structure']['sha256'], residues=tokens)).model_dump()
    return axis, chain_map


def retained_cp_metric(design, job, metric, params=None):
    import io
    import json
    import numpy as np
    from services.scientific_viewer_contract import ScientificViewerMetric, ScientificResidueMetric, ScientificChainMetric
    from services.analysis_registry import unavailable_scientific_identity
    try:
        roles = ('structure', 'ledger') + ({'residue_plddt': ('plddt',),
            'chain_metrics': ('metrics',), 'pae': ('pae',)}[metric])
        selected = retained_cp_snapshot(design, job, roles=roles)
        axis, chain_map = _cp_native_axis(selected)
        n = len(axis['residues'])
        payload = dict(schema_name='core_protein_viewer_metric', schema_version=1, contract_revision=1,
            design_id=design.id, design_name=design.name, metric=metric, status='ok', reason=None,
            document=dict(documentId='primary', candidateId=design.id,
                contentSha256=selected['artifacts']['structure']['sha256'], sourceKind='mmcif'),
            producer_binding={k:axis[k] for k in ('candidate_id','document_id')})
        if metric == 'residue_plddt':
            with np.load(io.BytesIO(selected['snapshots']['plddt']), allow_pickle=False) as z:
                values = z['plddt']
            if values.shape != (n,) or not np.isfinite(values).all() or np.any(values < 0) or np.any(values > 1):
                raise ValueError('invalid_native_confidence_vector')
            return ScientificResidueMetric.model_validate(dict(payload,
                metric='token_plddt' if any('label_atom_id' in r for r in axis['residues']) else 'residue_plddt',
                axis=axis, native_positions=list(range(n)),
                artifact_sha256=selected['artifacts']['plddt']['sha256'], units='fraction', values=values.tolist()))
        if metric == 'chain_metrics':
            confidence = json.loads(selected['snapshots']['metrics'])
            return ScientificChainMetric.model_validate(dict(payload, axis=axis, native_positions=list(range(n)),
                artifact_sha256=selected['artifacts']['metrics']['sha256'], chain_index_map=chain_map,
                chains_ptm=confidence['chains_ptm'], pair_chains_iptm=confidence['pair_chains_iptm'],
                role_assignment=None, role_reason='missing_role_assignment'))
        with np.load(io.BytesIO(selected['snapshots']['pae']), allow_pickle=False) as z:
            matrix = z['pae']
        if matrix.shape != (n, n) or not np.isfinite(matrix).all() or np.any(matrix < 0):
            raise ValueError('invalid_native_pae')
        from services.analysis_registry import normalize_pae_matrix_params
        max_size = normalize_pae_matrix_params(params)['max_size']
        indexes = list(range(0, n, max(1, (n + max_size - 1) // max_size)))
        return ScientificViewerMetric.model_validate(dict(payload,
            artifact_sha256=selected['artifacts']['pae']['sha256'], row_axis=axis, column_axis=axis,
            native_row_positions=list(range(n)), native_column_positions=list(range(n)), native_shape=[n,n],
            sampled_row_indices=indexes, sampled_column_indices=indexes,
            pae_matrix=matrix[np.ix_(indexes, indexes)].tolist(), size=len(indexes)))
    except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
        # Retention is not request intent; missing mapping is not "not requested".
        return ScientificViewerMetric.model_validate(unavailable_scientific_identity(
            design, metric, 'retained_native_evidence_unpublished_or_mapping_unavailable'))


async def verified_native_design(design, session, *, structure_only=False):
    return await verified_boltz_design(design, session)


async def scientific_document(design, session):
    # Missing compact materialization is not an invitation to legacy inference.
    if not isinstance(getattr(design, 'confidence_metrics', None), dict) or not design.confidence_metrics.get('core_protein_scientific'):
        return None
    try:
        selected = await verified_boltz_design(design, session)
    except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
        return None
    from services.scientific_viewer_contract import ViewerDocument
    return ViewerDocument(documentId='primary', candidateId=design.id,
        contentSha256=selected['artifacts']['structure']['sha256'], sourceKind='pdb')


async def compute_persisted_native_metric(design, metric, session, *, selected=None):
    """Project only bytes retained by the independent publication verifier."""
    from io import BytesIO
    import json
    import numpy as np
    from services.scientific_viewer_contract import ScientificResidueMetric, ScientificChainMetric, ScientificViewerMetric
    from services.analysis_registry import unavailable_scientific_identity
    reason = ('missing_producer_residue_axis_ledger' if metric == 'residue_plddt'
              else 'missing_producer_chain_identity_ledger')
    if not isinstance(getattr(design, 'confidence_metrics', None), dict) or not design.confidence_metrics.get('core_protein_scientific'):
        return ScientificViewerMetric.model_validate(unavailable_scientific_identity(design, metric, reason))
    try:
        selected = selected or await verified_boltz_design(design, session)
        if selected['design_id'] != design.id:
            raise ValueError('foreign selected snapshot')
        native = selected['native']
        axis = native['vectors'][0]['axis']
        payload = dict(schema_name='core_protein_viewer_metric', schema_version=1,
            contract_revision=1, design_id=design.id, design_name=design.name,
            metric=metric, status='ok', reason=None,
            document=dict(documentId='primary', candidateId=design.id,
                contentSha256=selected['artifacts']['structure']['sha256'], sourceKind='pdb'),
            producer_binding={k:selected['block'][k] for k in ('candidate_id','document_id')},
            axis=axis, native_positions=[r['index'] for r in axis['residues']])
        if metric == 'residue_plddt':
            with np.load(BytesIO(selected['snapshots']['plddt']), allow_pickle=False) as data:
                values = data['plddt'].tolist()
            payload.update(artifact_sha256=native['vectors'][0]['artifact_sha256'], units='fraction', values=values)
            payload['metric'] = 'token_plddt' if any('label_atom_id' in r for r in payload['axis']['residues']) else 'residue_plddt'
            result = ScientificResidueMetric.model_validate(payload)
        elif metric == 'chain_metrics':
            confidence = json.loads(selected['snapshots']['metrics'])
            payload.update(artifact_sha256=native['confidence']['artifact_sha256'],
                chain_index_map=native['chain_index_map'], chains_ptm=confidence['chains_ptm'],
                pair_chains_iptm=confidence['pair_chains_iptm'], role_assignment=None,
                role_reason='missing_role_assignment')
            result = ScientificChainMetric.model_validate(payload)
        else:
            raise ValueError('unsupported native metric')
        return result
    except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
        return ScientificViewerMetric.model_validate(unavailable_scientific_identity(design, metric, reason))


async def compute_persisted_pae(design, params, session, *, selected=None):
    from services.analysis_subprocess import _compute_pae_matrix
    from services.analysis_registry import unavailable_scientific_identity
    if not isinstance(getattr(design, 'confidence_metrics', None), dict) or not design.confidence_metrics.get('core_protein_scientific'):
        result = unavailable_scientific_identity(design, 'pae', 'missing_producer_native_axis_ledger')
        return result, {'status':'unavailable', 'reason':result['reason']}, None
    try:
        selected = selected or await verified_boltz_design(design, session)
        if selected['design_id'] != design.id:
            raise ValueError('foreign selected snapshot')
        native = selected['native']
        # Narrow transport adaptation: paths are verified descriptors, not a
        # mutation of the ORM row or a basename-derived legacy fallback.
        adapted = SimpleNamespace(id=design.id, name=design.name,
            pdb_path=selected['artifacts']['structure']['path'],
            aligned_error_path=selected['artifacts']['pae']['path'],
            aligned_error_format='boltz_pae_npz', aligned_error_key=native['aligned_error']['matrix_key'])
        evidence = native['aligned_error']['identity_evidence']
        evidence = {key:evidence[key] for key in ('artifact_sha256', 'matrix_key', 'row_axis', 'column_axis')}
        result = _compute_pae_matrix(adapted, params, contract_revision=1,
            identity_evidence=evidence, producer_binding={key:selected['block'][key] for key in ('candidate_id','document_id')})
        # The strict loader reopens files. Do not commit a projection if any
        # source, ledger or publication generation changed during that read.
        _revalidate(selected['publication_root'], selected['publication_receipt'])
        return result
    except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
        result = unavailable_scientific_identity(design, 'pae', 'invalid_producer_native_evidence')
        return result, {'status':'unavailable', 'reason':result['reason']}, None
