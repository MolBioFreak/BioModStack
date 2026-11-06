"""Bounded catalogue of canonical history references for a Project export."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_models import (
    MolecularDocument, MolecularRevision, MolecularOperation,
    MolecularOperationInput, MolecularOperationOutput, PCRExperimentRevision,
)

MAX_HISTORY_ITEMS = 3000


async def project_export_history(session: AsyncSession, sequence_id: str, revision_id: str) -> dict:
    document = await session.get(MolecularDocument, sequence_id)
    selected = await session.get(MolecularRevision, revision_id)
    if document is None or document.deleted_at is not None or selected is None or selected.document_id != sequence_id:
        raise LookupError('The selected molecular revision does not belong to this saved sequence.')
    # Capture one read transaction. A concurrently created revision cannot move
    # the selected export boundary, and no scientific payload enters PM notes.
    revisions = list((await session.scalars(select(MolecularRevision).where(
        MolecularRevision.document_id == sequence_id,
        MolecularRevision.revision_number <= selected.revision_number,
    ).order_by(MolecularRevision.revision_number).limit(MAX_HISTORY_ITEMS + 1))).all())
    if len(revisions) > MAX_HISTORY_ITEMS:
        raise OverflowError('Plasmid history exceeds the complete-export limit; nothing was truncated.')
    ids = [row.id for row in revisions]
    input_operations = select(MolecularOperationInput.operation_id).where(MolecularOperationInput.revision_id.in_(ids))
    output_operations = select(MolecularOperationOutput.operation_id).where(MolecularOperationOutput.revision_id.in_(ids))
    pcr = list((await session.scalars(select(PCRExperimentRevision).where(or_(
        PCRExperimentRevision.template_revision_id.in_(ids), PCRExperimentRevision.product_revision_id.in_(ids),
    )).order_by(PCRExperimentRevision.created_at, PCRExperimentRevision.id).limit(MAX_HISTORY_ITEMS + 1))).all())
    operations = list((await session.scalars(select(MolecularOperation).where(or_(
        MolecularOperation.id.in_(input_operations), MolecularOperation.id.in_(output_operations),
        MolecularOperation.id.in_([row.operation_id for row in revisions if row.operation_id] + [row.operation_id for row in pcr]),
    )).order_by(MolecularOperation.created_at, MolecularOperation.id).limit(MAX_HISTORY_ITEMS + 1))).all())
    if len(revisions) + len(operations) + len(pcr) > MAX_HISTORY_ITEMS:
        raise OverflowError('Plasmid history exceeds the complete-export limit; nothing was truncated.')
    entries = [{
        'kind': 'revision', 'id': row.id, 'label': f'Sequence revision {row.revision_number}',
        'adapter_id': 'bms.molbio.revision-reference.adapter.v1',
        'entity_id': urlencode({'sequence_id': sequence_id, 'revision_id': row.id}),
        'content_sha256': row.content_sha256,
        'reopen_href': '/designer?' + urlencode({'molbio_sequence_id': sequence_id, 'molbio_revision_id': row.id}),
    } for row in revisions]
    entries.extend({
        'kind': 'operation', 'id': row.id, 'label': row.operation_kind.replace('_', ' '),
        'adapter_id': 'bms.molbio.operation-reference.adapter.v1', 'entity_id': row.id,
        'content_sha256': None,
        'reopen_href': '/designer?' + urlencode({'molbio_operation_id': row.id}),
    } for row in operations)
    entries.extend({
        'kind': 'pcr_review', 'id': row.id, 'label': f'PCR review revision {row.revision_number}',
        'adapter_id': None, 'entity_id': None, 'content_sha256': None,
        'reopen_href': '/designer?' + urlencode({'pcr_experiment_id': row.experiment_id, 'pcr_revision_id': row.id}),
    } for row in pcr)
    result = {'schema': 'bms.molbio.project-export-history.v1', 'sequence_id': sequence_id,
        'selected_revision_id': revision_id, 'name': str((selected.snapshot or {}).get('name') or document.name), 'complete': True,
        'entries': entries}
    raw = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if len(raw) > 512 * 1024:
        raise OverflowError('Plasmid history exceeds the complete-export size limit; nothing was truncated.')
    result['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
    return result
