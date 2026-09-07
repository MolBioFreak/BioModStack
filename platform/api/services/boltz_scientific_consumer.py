"""Read marked Boltz evidence through its persisted owning Job, never caller claims."""
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select
from database import Design, Job
from services.boltz_scientific_persistence import _verified_publication, _revalidate
from services.core_protein_result_contract import validate_persisted_publication
from services.core_protein_scientific_contract import revision_for_job
from services.frustrampnn.contracts import canonical_json_bytes


async def verified_boltz_design(design, session):
    """Return freshly verified native identity; no UUID allocation or DB writes.

    Rebuild the publication from launch/workflow authority and exact file bytes,
    then compare its receipt and compact blocks to committed ownership records.
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
        prepared, receipt = _verified_publication(job, root)
        if canonical_json_bytes((job.provenance or {}).get('core_protein_candidate_publication')) != canonical_json_bytes(receipt):
            raise ValueError('persisted publication receipt mismatch')
        rows = list((await session.execute(select(Design).where(
            Design.job_id == job.id, Design.source_stage.is_(None)))).scalars())
        validate_persisted_publication(job, rows, root)
        selected = None
        for row in rows:
            candidate = prepared[row.name]
            expected = dict(candidate['block'], design_id=row.id)
            if canonical_json_bytes((row.confidence_metrics or {}).get('core_protein_scientific')) != canonical_json_bytes(expected):
                raise ValueError('persisted native design binding mismatch')
            if row.id == design.id:
                selected = candidate
        if selected is None:
            raise ValueError('foreign selected design')
        _revalidate(root, receipt)
        return dict(selected, publication_root=root, publication_receipt=receipt)


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


async def compute_persisted_native_metric(design, metric, session):
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
        selected = await verified_boltz_design(design, session)
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
        _revalidate(selected['publication_root'], selected['publication_receipt'])
        return result
    except (ValueError, TypeError, KeyError, IndexError, OSError, RuntimeError):
        return ScientificViewerMetric.model_validate(unavailable_scientific_identity(design, metric, reason))


async def compute_persisted_pae(design, params, session):
    from services.analysis_subprocess import _compute_pae_matrix
    from services.analysis_registry import unavailable_scientific_identity
    if not isinstance(getattr(design, 'confidence_metrics', None), dict) or not design.confidence_metrics.get('core_protein_scientific'):
        result = unavailable_scientific_identity(design, 'pae', 'missing_producer_native_axis_ledger')
        return result, {'status':'unavailable', 'reason':result['reason']}, None
    try:
        selected = await verified_boltz_design(design, session)
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
