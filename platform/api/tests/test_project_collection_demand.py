"""Demand projects pages through the existing owner; catalog traversal stays real."""
import json
import os
import time
from collections import Counter
from pathlib import Path

import httpx
import pytest
from sqlalchemy import event, select

from services.global_experiments import read_models as owner
from test_project_manager_read_models import (
    read_model_store, _hierarchy, _add_attachment_note_activity_rows,
    _read_fixture_head, _app, _add_workflow_runs,
)
from experiment_models import ExperimentResearchRecord, ExperimentResource, ExperimentExternalEntityReceipt

FAMILIES = ['results', 'lineage', 'datasets', 'notes', 'decisions', 'activity']
CURSORS = dict(zip(FAMILIES, ['result_cursor', 'lineage_cursor', 'dataset_cursor', 'note_cursor', 'decision_cursor', 'activity_cursor']))


@pytest.mark.asyncio
async def test_project_collection_demand_complete_pages_shared_views_and_offpage_selection(read_model_store, monkeypatch):
    factory = read_model_store
    async with factory() as session:
        project, glob, domain = await _hierarchy(session)
        ids = project.id, glob.id, domain.id
        await _add_attachment_note_activity_rows(session, project_id=ids[0], domain_id=ids[2])
        await _add_workflow_runs(session, project_id=ids[0], domain_experiment_id=ids[2])
        for index in range(105):
            await _read_fixture_head(session, identity=f'dataset-{index:03}', kind='dataset',
                                     project_id=ids[0], parent_id=ids[2], payload={'name': f'Dataset {index}'})
            stamp = f'2026-08-09T12:{index // 60:02}:{index % 60:02}Z'
            session.add(ExperimentResource(id=f'decision-{index}', kind='research_record', workspace_id=ids[0], lifecycle_owner_id=ids[0], created_at=stamp))
            await session.flush()
            session.add(ExperimentResearchRecord(resource_id=f'decision-{index}', workspace_id=ids[0], subject_resource_id=ids[2], record_kind='decision', body=f'Decision {index}', author='operator', source_receipt_ids_json='[]', created_at=stamp))
        await session.commit()
    calls = Counter()
    for name in ['_attachment_page', '_record_page', '_dataset_page', '_activity_page', '_run_page']:
        original = getattr(owner, name)
        async def counted(*args, _name=name, _original=original, **kwargs):
            calls[kwargs.get('family', _name)] += 1
            return await _original(*args, **kwargs)
        monkeypatch.setattr(owner, name, counted)
    statements = []
    async with factory() as session:
        engine = session.bind.sync_engine
    def sql(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', sql)
    async def read(**kwargs):
        async with factory() as session:
            return await owner.build_project_manager_read_model(session, project_id=ids[0], focus_id=ids[1], **kwargs)
    metrics = {}
    full = {}
    payload = b""
    async with factory() as session:
        persisted_receipt_ids = set((await session.scalars(select(ExperimentExternalEntityReceipt.id).where(ExperimentExternalEntityReceipt.workspace_id == ids[0]))).all())
    try:
        for label, families in [('full', None), ('shared_only', []), ('notes_only', ['notes'])]:
            statements.clear(); calls.clear()
            value = await read(collection_families=families)
            start = time.process_time()
            for _ in range(20):
                payload = json.dumps(value, sort_keys=True).encode()
            metrics[label] = {'sql_selects': sum(s.lstrip().upper().startswith('SELECT') for s in statements), 'reader_calls': dict(calls), 'decoded_json_bytes': len(payload), 'serialization_cpu_seconds_20': time.process_time() - start}
            if label == 'full':
                full = value
            if label == 'shared_only':
                for field in ['tree', 'map', 'runs', 'tasks', 'selection', 'counts', 'source_receipt_ids', 'source_digest_set_sha256', 'reconciliation']:
                    assert value[field] == full[field], field
                assert value['loaded_collection_families'] == []
                assert all(value['pagination'][family]['items'] == [] for family in FAMILIES)
                assert calls == Counter({'map': 1, '_run_page': 1})
        for family in FAMILIES:
            cursor = None
            all_items = []
            while True:
                options = {CURSORS[family]: cursor} if cursor else {}
                page = await read(collection_families=[family], **options)
                control = await read(**options)
                assert page['pagination'][family] == control['pagination'][family]
                all_items += page['pagination'][family]['items']
                cursor = page['pagination'][family]['next_cursor']
                if not cursor:
                    break
            assert len(all_items) > 100, family
            assert len({json.dumps(item, sort_keys=True) for item in all_items}) == len(all_items)
        # Shared map pages still discover the complete attached catalog, not fixture totals.
        cursor = None
        discovered = set()
        while True:
            page = await read(collection_families=[], map_cursor=cursor, map_limit=7)
            control = await read(map_cursor=cursor, map_limit=7)
            assert page['map'] == control['map']
            discovered.update(item['receipt_id'] for item in page['pagination']['map']['items'])
            cursor = page['map']['next_cursor']
            if not cursor:
                break
        assert discovered == persisted_receipt_ids
        run_id = full['runs']['items'][-1]['run_id']
        for key in ['research_record:record-000', 'external_entity_receipt:receipt-000', f'workflow_run:{run_id}']:
            control = await read(selected_node_key=key, run_limit=1)
            demand = await read(selected_node_key=key, run_limit=1, collection_families=[])
            assert demand['selection'] == control['selection']
        dataset = (await read(collection_families=['datasets']))['pagination']['datasets']['items'][-1]
        key = dataset["node_key"]
        assert (await read(selected_node_key=key, collection_families=[]))['selection'] == (await read(selected_node_key=key))['selection']
        # Folder selection itself discovers demand even with an empty caller list.
        selected = await read(selected_node_key=f'virtual_folder:{ids[2]}:notes', collection_families=[])
        assert selected['loaded_collection_families'] == ['notes']
        assert selected['pagination']['notes']['items']
        app = _app(factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            default = await client.get(f'/api/projects/{ids[0]}/summary', params={'focus_id': ids[1]})
            demand = await client.get(f'/api/projects/{ids[0]}/summary', params={'focus_id': ids[1], 'collection_families': 'notes'})
            empty = await client.get(f'/api/projects/{ids[0]}/summary', params={'focus_id': ids[1], 'collection_families': ''})
            assert default.status_code == demand.status_code == empty.status_code == 200
            assert 'loaded_collection_families' not in default.json()
            assert default.json()['pagination']['notes']['items']
            assert demand.json()['pagination']['notes'] == default.json()['pagination']['notes']
            assert empty.json()['loaded_collection_families'] == []
            assert default.json()['source_receipt_ids'] == empty.json()['source_receipt_ids']
    finally:
        event.remove(engine, 'before_cursor_execute', sql)
    if os.getenv('BMS_PROJECT_COLLECTION_METRICS'):
        Path(os.environ['BMS_PROJECT_COLLECTION_METRICS']).write_text(json.dumps(metrics, indent=2))
