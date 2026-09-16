"""Pinned ESMFold2 scalar materialization and read-only publication verification."""
import json
import math
from pathlib import Path

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
    No DB writes, artifact reparsing by consumers, or legacy authority inference.
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
