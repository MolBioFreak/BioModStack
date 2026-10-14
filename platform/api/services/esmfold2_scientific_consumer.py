"""Pinned ESMFold2 scalar, CIF confidence and raw-token PAE readback."""
import json
import math
from pathlib import Path
from zipfile import BadZipFile

from sqlalchemy import select
from database import Design, Job
from services.core_protein_scientific_contract import revision_for_job, validate_metrics

SCALAR_DIALECT = {'name': 'biohub_esmfold2_token_scalar_v1',
    'esm_commit': 'c94ed8d763bbd7088b296949e5b401e8ea12073a',
    'transformers_commit': '3a8956fb4d4ea16b0ec8e71deef2c2909b6a5cbf'}
PRODUCER_VERSION = 'Biohub/esm@' + SCALAR_DIALECT['esm_commit'] + ';Biohub/transformers@' + SCALAR_DIALECT['transformers_commit']
DESCRIPTORS = [dict(metric_key=key, unit='fraction' if key == 'plddt' else 'dimensionless', direction='higher_is_better',
    scope='model_token_mean' if key == 'plddt' else 'model',
    producer_version=PRODUCER_VERSION, derivation_version=SCALAR_DIALECT['name'])
    for key in ('plddt', 'ptm', 'iptm')]


def scalar_block(payload, *, candidate_id, document_id, artifact_sha256):
    source = dict(candidate_id=candidate_id, document_id=document_id, artifact_sha256=artifact_sha256)
    records = []
    for descriptor in DESCRIPTORS:
        key = 'plddt_mean' if descriptor['metric_key'] == 'plddt' else descriptor['metric_key']
        value = payload.get(key)
        state, reason = 'ok', None
        if payload.get('scalar_dialect') != SCALAR_DIALECT:
            state, reason = 'unavailable', 'unverified_scalar_dialect'
        elif (payload.get('scalar_states') or {}).get(key) == 'invalid':
            state, reason = 'invalid', 'invalid_native_scalar'
        elif value is None:
            state, reason = 'unavailable', 'missing_native_scalar'
        elif type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            state, reason = 'invalid', 'invalid_native_scalar'
        records.append(dict(descriptor, state=state, value=value if state == 'ok' else None,
                            reason_code=reason, source=source))
    return dict(schema_version=1, producer='esmfold2', candidate_id=candidate_id,
        document_id=document_id, metrics=validate_metrics(records, DESCRIPTORS, expected_source=source))


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


async def verified_esmfold2_design(design, session):
    """Return selected {block, artifacts, payload, ...} after whole-set verification.

    block.metrics are native fractional scalars. Only display pLDDT uses x100.
    No DB writes or legacy authority inference; consumers use verified snapshots.
    """
    from paths import get_data_root, resolve_runtime_data_path
    from services.core_protein_result_contract import prepare_esmfold2_publication, revalidate_prepared_publication
    with session.no_autoflush:
        job = await session.scalar(select(Job).where(Job.id == design.job_id))
        if job is None or revision_for_job(job) != 1 or job.model_id not in ('esmfold2', 'esmfold2_experimental'):
            raise ValueError('missing_esmfold2_publication_authority')
        if not isinstance(job.output_dir, str) or not job.output_dir:
            raise ValueError('missing_producer_publication_root')
        root = Path(job.output_dir)
        root = resolve_runtime_data_path(root) if root.is_absolute() else get_data_root() / root
        from services.result_ingester import _resolve_esmfold2_final_root
        root = _resolve_esmfold2_final_root(root)
        if root is None:
            raise ValueError('missing_producer_publication_root')
        rows = list((await session.execute(select(Design).where(
            Design.job_id == job.id, Design.source_stage.is_(None)))).scalars())
        prepared, receipt = prepare_esmfold2_publication(job, root, rows)
        selected = next((prepared[row.name] for row in rows if row.id == design.id), None)
        if selected is None:
            raise ValueError('foreign selected design')
        revalidate_prepared_publication(root, receipt)
        return dict(selected, publication_root=root, publication_receipt=receipt)


def scalar_records_from_publication(design, job, read):
    """Verify scalar bytes and committed document binding, not coordinate bytes."""
    from services.core_protein_result_contract import _persisted_candidate_artifacts
    receipt = job.provenance['core_protein_candidate_publication']
    artifacts = _persisted_candidate_artifacts(receipt, design)
    manifest = read(receipt['manifest'])
    if manifest.get('schema_version') != 2 or manifest.get('workflow') != 'esmfold2':
        raise ValueError('foreign scalar manifest')
    entries = read(receipt['manifest'], index=('samples', 'sample_id'))
    entry = entries[design.name]
    root = Path(receipt['manifest']['path']).parent
    if (design.source_stage is not None
            or artifacts['structure']['path'] != str(root / entry['cif'])
            or artifacts['metrics']['path'] != str(root / entry['metrics'])):
        raise ValueError('foreign scalar document binding')
    payload = read(artifacts['metrics'])
    if payload.get('sample_id') != design.name or payload.get('cif') != entry['cif']:
        raise ValueError('foreign scalar companion')
    block = scalar_block(payload, candidate_id=design.name, document_id=entry['metrics'],
                         artifact_sha256=artifacts['metrics']['sha256'])
    if canonical_bytes(block) != canonical_bytes(design.confidence_metrics.get('core_protein_scientific')):
        raise ValueError('changed scalar publication')
    return block['metrics']


CONFIDENCE_DIALECT = {**SCALAR_DIALECT, 'name': 'biohub_esmfold2_native_confidence_v1'}
_ERRORS = (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError, BadZipFile, EOFError)


async def verified_native_design(design, session, *, structure_only=False):
    # Optional tensor parsing is deliberately NOT part of structure authority.
    selected = await verified_esmfold2_design(design, session)
    return dict(selected, design_id=design.id,
        source_kind='mmcif' if Path(selected['artifacts']['structure']['path']).suffix.lower() in ('.cif', '.mmcif') else 'pdb',
        native={'confidence_dialect': selected['payload'].get('confidence_dialect')})


def _binding(selected):
    return {k: selected['block'][k] for k in ('candidate_id', 'document_id')}


def _document(design, selected):
    from services.scientific_viewer_contract import ViewerDocument
    return ViewerDocument(documentId='primary', candidateId=design.id,
        contentSha256=selected['artifacts']['structure']['sha256'], sourceKind=selected['source_kind'])


async def scientific_document(design, session):
    try:
        return _document(design, await verified_native_design(design, session, structure_only=True))
    except _ERRORS:
        return None


def _unavailable(design, metric, reason):
    from services.analysis_registry import unavailable_scientific_identity
    from services.scientific_viewer_contract import ScientificViewerMetric
    return ScientificViewerMetric.model_validate(unavailable_scientific_identity(design, metric, reason))


def _cif_residue_confidence(selected):
    """Pinned writer expands collapsed-residue pLDDT ×100 to all its atoms.

    Read that identified channel, not guessed legacy B-factor meaning. Preserve
    author/label namespaces, heteromers, ligands, insertion codes and altlocs.
    The raw model-token mean remains independently published in scalar_block.
    """
    from io import StringIO
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    if selected['source_kind'] != 'mmcif' or selected['payload'].get('scalar_dialect') != SCALAR_DIALECT:
        raise ValueError('unverified_esmfold2_cif_confidence_dialect')
    table = MMCIF2Dict(StringIO(selected['snapshots']['structure'].decode('utf-8')))
    scores = table['_atom_site.B_iso_or_equiv']
    def cell(key, i, *, optional=False):
        column = table.get('_atom_site.' + key)
        if column is None and optional:
            return None
        if not isinstance(column, list) or len(column) != len(scores):
            raise ValueError('invalid_native_cif_column')
        return None if column[i] in ('.', '?', '') else column[i]
    def number(value):
        return int(value) if value is not None else None
    grouped = {}
    for i, raw in enumerate(scores):
        auth, label = cell('auth_asym_id', i, optional=True), cell('label_asym_id', i)
        auth_seq, label_seq = number(cell('auth_seq_id', i, optional=True)), number(cell('label_seq_id', i))
        if not (auth and auth_seq is not None or label and label_seq is not None):
            raise ValueError('missing_native_cif_residue_identity')
        residue = dict(chain_id=auth or label, residue_name=cell('label_comp_id', i),
            insertion_code=cell('pdbx_PDB_ins_code', i, optional=True) or '',
            selected_model=number(cell('pdbx_PDB_model_num', i)),
            selected_altloc=cell('label_alt_id', i) or '',
            auth_asym_id=auth, auth_seq_id=auth_seq, label_asym_id=label, label_seq_id=label_seq,
            source_entity_id=cell('label_entity_id', i), entity_instance_id=None)
        value = float(raw) / 100.0
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('invalid_native_cif_confidence')
        key = tuple(residue.values())
        if key in grouped and grouped[key][1] != value:
            raise ValueError('inconsistent_native_residue_confidence')
        grouped.setdefault(key, (residue, value))
    axis = dict(_binding(selected), source_sha256=selected['artifacts']['structure']['sha256'],
        residues=[dict(r, index=i) for i, (r, _) in enumerate(grouped.values())],
        producer_version=PRODUCER_VERSION, confidence_scope='collapsed_residue', stored_units='percent')
    return axis, [value for _, value in grouped.values()]


async def compute_persisted_native_metric(design, metric, session, *, selected=None):
    try:
        selected = selected or await verified_native_design(design, session)
        if selected['design_id'] != design.id:
            raise ValueError('foreign_selected_native_snapshot')
        if metric == 'chain_metrics':
            return _unavailable(design, metric, 'missing_native_chain_axis_mapping')
        if metric != 'residue_plddt':
            raise ValueError('unsupported_native_metric')
        axis, values = _cif_residue_confidence(selected)
        from services.scientific_viewer_contract import ScientificResidueMetric
        return ScientificResidueMetric(schema_name='core_protein_viewer_metric', schema_version=1,
            contract_revision=1, design_id=design.id, design_name=design.name, status='ok', reason=None,
            metric=metric, units='fraction', values=values, document=_document(design, selected),
            producer_binding=_binding(selected), artifact_sha256=selected['artifacts']['structure']['sha256'],
            axis=axis, native_positions=list(range(len(values))))
    except _ERRORS:
        return _unavailable(design, metric, 'missing_or_invalid_esmfold2_native_evidence')


def _native_pae(selected):
    import numpy as np
    from io import BytesIO
    if selected['payload'].get('confidence_dialect') != CONFIDENCE_DIALECT:
        raise ValueError('unverified_native_confidence_dialect')
    with np.load(BytesIO(selected['snapshots']['native_confidence']), allow_pickle=False) as arrays:
        if arrays['sample_id'].item() != selected['block']['candidate_id']:
            raise ValueError('foreign_native_confidence_sample')
        if 'pae' not in arrays:
            return None, None
        matrix = arrays['pae']
        if (matrix.dtype.kind not in 'fiu' or matrix.ndim != 2 or not matrix.size
                or matrix.shape[0] != matrix.shape[1] or not np.all(np.isfinite(matrix)) or np.any(matrix < 0)):
            raise ValueError('invalid_native_pae')
        n = matrix.shape[0]
        # Available producer metadata is advisory, not chain-instance identity.
        # A malformed metadata vector cannot invalidate otherwise usable PAE.
        metadata = {}
        for key in ('residue_index', 'entity_id'):
            values = arrays[key] if key in arrays else None
            metadata[key] = values.tolist() if values is not None and values.shape == (n,) and values.dtype.kind in 'iu' else [None] * n
        axis = dict(_binding(selected), source_sha256=selected['artifacts']['structure']['sha256'],
            axis_kind='model_token', producer_version=PRODUCER_VERSION,
            mapping_reason='native_token_to_structure_mapping_unavailable', orientation='native_output_order',
            tokens=[dict(index=i, **{k: v[i] for k, v in metadata.items()}) for i in range(n)])
        return axis, matrix


async def compute_persisted_pae(design, params, session, *, selected=None):
    try:
        selected = selected or await verified_native_design(design, session)
        if selected['design_id'] != design.id:
            raise ValueError('foreign_selected_native_snapshot')
        if 'native_confidence' not in selected['artifacts']:
            reason = ('native_pae_not_reported' if selected['payload'].get('confidence_dialect') == CONFIDENCE_DIALECT
                      else 'not_retained_by_producer')
            result = _unavailable(design, 'pae', reason)
        else:
            axis, matrix = _native_pae(selected)
            if matrix is None:
                result = _unavailable(design, 'pae', 'native_pae_not_reported')
            else:
                import numpy as np
                from services.scientific_viewer_contract import ScientificViewerMetric
                n = len(matrix)
                maximum = params.get('max_size', 300)
                if type(maximum) is not int or maximum < 1:
                    raise ValueError('invalid_pae_sample_size')
                indexes = np.linspace(0, n - 1, min(n, maximum), dtype=int).tolist()
                result = ScientificViewerMetric(schema_name='core_protein_viewer_metric', schema_version=1,
                    contract_revision=1, design_id=design.id, design_name=design.name, metric='pae', status='ok', reason=None,
                    document=_document(design, selected), producer_binding=_binding(selected),
                    artifact_sha256=selected['artifacts']['native_confidence']['sha256'], row_axis=axis, column_axis=axis,
                    native_row_positions=list(range(n)), native_column_positions=list(range(n)), native_shape=[n, n],
                    sampled_row_indices=indexes, sampled_column_indices=indexes,
                    pae_matrix=matrix[np.ix_(indexes, indexes)].tolist(), size=len(indexes))
    except _ERRORS:
        result = _unavailable(design, 'pae', 'missing_or_invalid_esmfold2_native_evidence')
    return result.model_dump(mode='json'), {'status': result.status, 'reason': result.reason}, None
