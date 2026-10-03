"""Preserve physical instance attribution and Project product selection."""
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import delete, select
from molbio_models import MolecularOperation, MolecularOperationInput, MolecularOperationOutput, MolecularRevision
from services.assembly.golden_gate_workflow import freeze_selection
from services.assembly.golden_gate_workflow_types import SaveDesignRequest, WorkflowResult
from services.global_experiments.adapters import MolBioOperationAdapter
from test_golden_gate_design_core import request, A, B, rc
from test_golden_gate_workflow_receiving import client_store


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['prepared', 'donor', 'pcr', 'synthesis'])
async def test_reverse_original_end_instances_keep_their_junction_and_role(tmp_path, kind):
    data = request(kind='pcr' if kind == 'pcr' else 'synthesis', reverse=kind in {'pcr', 'synthesis'}).model_dump(mode='json')
    if kind == 'prepared':
        data['sources'][1]['source'].update(sequence='CATT' + rc(B), features=[])
        data['parts'][1].update(orientation='reverse', role='backbone', preparation=dict(kind='prepared',
            left_end=dict(type='sticky_5', overhang='CATT', protruding_strand='top'),
            right_end=dict(type='sticky_5', overhang='GGAG', protruding_strand='bottom')))
        expected = {'left': ('CATT', '1'), 'right': ('GGAG', '0')}
        prefix, role = 'source:insert:insert:', 'backbone'
    elif kind == 'donor':
        data['sources'][0]['source']['sequence'] = rc(data['sources'][0]['source']['sequence'])
        data['parts'][0]['orientation'] = 'reverse'
        expected = {'left': ('CTCC', '0'), 'right': ('AATG', '1')}
        prefix, role = 'digest:backbone:', 'backbone'
    else:
        # PCR/synthesis owners have ALREADY oriented their new molecule.
        expected = {'left': ('GGAG', '0'), 'right': ('CATT', '1')}
        prefix, role = 'digest:insert:', 'insert'
    async with client_store(tmp_path) as (client, _):
        response = await client.post('/api/molbio/assembly/golden-gate/design', json=data)
        assert response.status_code == 200, response.text
        result = WorkflowResult.model_validate(response.json())
        assert result.selected_solution_id is not None
        assert result.solutions[0].design.solutions[0].sequence == 'AATG' + A + 'GGAG' + B
        inventory = [row for row in result.solutions[0].fidelity['inventory']
                     if row['instance_id'].startswith(prefix) and row['role'] != 'dropout']
        assert len(inventory) == 2
        for row in inventory:
            assert (row['sequence'], row['intended_junction_id']) == expected[row['instance_id'].rsplit(':', 1)[-1]]
            assert row['role'] == role
        body = SaveDesignRequest(selection=freeze_selection(result, result.selected_solution_id), name=kind, idempotency_key=kind)
        saved = await client.post('/api/molbio/assembly/golden-gate/design/save', json=body.model_dump(mode='json'))
        assert saved.status_code == 200, saved.text
        reopened = await client.get('/api/molbio/assembly/golden-gate/design/' + saved.json()['operation_id'])
        assert reopened.json() == saved.json()
        assert reopened.json()['result']['solutions'][0]['fidelity']['inventory'] == result.solutions[0].fidelity['inventory']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['product', 'historical_fallback', 'no_outputs'])
async def test_operation_open_chooses_product_with_historical_fallback(tmp_path, mode):
    async with client_store(tmp_path) as (client, sessions):
        response = await client.post('/api/molbio/assembly/golden-gate/design', json=request(kind='synthesis').model_dump(mode='json'))
        assert response.status_code == 200, response.text
        result = WorkflowResult.model_validate(response.json())
        assert result.selected_solution_id is not None
        body = SaveDesignRequest(selection=freeze_selection(result, result.selected_solution_id), name='Project source', idempotency_key=mode)
        response = await client.post('/api/molbio/assembly/golden-gate/design/save', json=body.model_dump(mode='json'))
        assert response.status_code == 200, response.text
        saved = response.json()
        async with sessions() as session:
            outputs = list((await session.scalars(select(MolecularOperationOutput)
                .where(MolecularOperationOutput.operation_id == saved['operation_id'])
                .order_by(MolecularOperationOutput.position))).all())
            assert outputs[0].revision_id != saved['product_revision_id']
            assert next(row for row in outputs if row.role == 'product').revision_id == saved['product_revision_id']
            first = await session.get(MolecularRevision, outputs[0].revision_id)
            if mode == 'historical_fallback':
                for row in outputs:
                    if row.role == 'product': row.role = 'legacy_output'
            elif mode == 'no_outputs':
                await session.execute(delete(MolecularOperationOutput).where(MolecularOperationOutput.operation_id == saved['operation_id']))
            await session.commit()
            receipt = await MolBioOperationAdapter(molbio_session_factory=sessions).verify(session, saved['operation_id'])
            operation = await session.get(MolecularOperation, saved['operation_id'])
            expected_edges = {}
            for key, model in [('inputs', MolecularOperationInput), ('outputs', MolecularOperationOutput)]:
                rows = (await session.scalars(select(model)
                    .where(model.operation_id == operation.id).order_by(model.position, model.id))).all()
                expected_edges[key] = [dict(revision_id=row.revision_id, role=row.role, ordinal=row.position)
                                       for row in rows]
            expected_detail = dict(operation_id=operation.id, operation_type=operation.operation_kind,
                status=operation.status, request_fingerprint_sha256=operation.request_fingerprint,
                **expected_edges)
        # Follow the real shared operation reader used by the browser parent, not just the URI.
        detail = await client.get('/api/molbio/operations/' + saved['operation_id'])
        assert detail.status_code == 200, detail.text
        assert detail.json() == expected_detail
        reopened = await client.get('/api/molbio/assembly/golden-gate/design/' + saved['operation_id'])
        assert reopened.status_code == 200, reopened.text
        assert reopened.json() == saved
        query = parse_qs(urlparse(receipt['reopen_uri']).query)
        assert query['operation_id'] == [saved['operation_id']]
        if mode == 'product':
            assert query['sequence_id'] == [saved['product_document_id']]
            assert query['revision_id'] == [saved['product_revision_id']]
        elif mode == 'historical_fallback':
            assert query['sequence_id'] == [first.document_id]
            assert query['revision_id'] == [first.id]
        else:
            assert 'sequence_id' not in query and 'revision_id' not in query


@pytest.mark.asyncio
async def test_missing_operation_read_remains_404(tmp_path):
    async with client_store(tmp_path) as (client, _):
        response = await client.get('/api/molbio/operations/does-not-exist')
        assert response.status_code == 404
        assert response.json() == {'detail': 'molecular operation not found'}
