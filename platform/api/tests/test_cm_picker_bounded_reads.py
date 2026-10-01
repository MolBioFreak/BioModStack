"""Isolated source-owner pagination and exact-detail regression evidence."""
import json
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import ConformationalMappingSource, get_session
from routers import conformational_mapping as cm


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [1, 100, 1000])
async def test_picker_summary_and_exact_detail(tmp_path, monkeypatch, count):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path}/picker.sqlite')
    async with engine.begin() as connection:
        await connection.run_sync(ConformationalMappingSource.__table__.create)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    kinds = ['complex_snapshot', 'structure_upload', 'structure_artifact', 'protein_sequence',
             'confornets_checkpoint', 'confornets_config', 'confornets_state']
    async with factory() as session:
        session.add_all([ConformationalMappingSource(
            source_id=f'source-{i:04}', principal_id='alice', source_kind=kinds[i % 7],
            storage_root=str(tmp_path), relative_path='content.cif', content_sha256='a' * 64,
            size_bytes=123, immutable=True, created_at=datetime(2026, 1, 1),
            metadata_json={'scientific': 'x' * 8192, 'explicit_false': False, 'zero': 0,
                           'empty': [], 'null': None, 'rcsb_entry': {'accession': '4HHB'}},
        ) for i in range(count)])
        session.add(ConformationalMappingSource(
            source_id='hidden', principal_id='bob', source_kind='complex_snapshot',
            storage_root=str(tmp_path), relative_path='content.json', content_sha256='b' * 64,
            size_bytes=5, immutable=True, metadata_json={},
        ))
        session.add(ConformationalMappingSource(
            source_id='zz-managed', principal_id='system', source_kind='confornets_checkpoint',
            storage_root=str(tmp_path), relative_path='content.pt', content_sha256='c' * 64,
            size_bytes=5, immutable=True, metadata_json={'managed': 'exact'},
        ))
        await session.commit()
    ensure_calls = []
    async def ensure(session):
        ensure_calls.append(1)
        return await session.get(ConformationalMappingSource, 'zz-managed')
    monkeypatch.setattr(cm, '_ensure_managed_confornets_checkpoint', ensure)
    monkeypatch.setattr(cm, '_principal', lambda request: 'alice')
    authority_reads = []
    def authority(row):
        authority_reads.append(row.source_id)
        return {'source_id': row.source_id, 'payload': {'scientific': 'y' * 8192}}
    monkeypatch.setattr(cm, '_read_source_authority', authority)
    statements = []
    parameters_seen = []
    event.listen(engine.sync_engine, 'before_cursor_execute',
                 lambda conn, cursor, statement, parameters, context, many: (statements.append(statement), parameters_seen.append(parameters)))
    app = FastAPI()
    app.include_router(cm.router)
    async def sessions():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
        path = '/api/conformational-mapping/sources'
        first = await client.get(path, params={'summary': True, 'limit': 50})
        assert first.status_code == 200
        page = first.json()
        assert len(page['sources']) <= 50
        assert page['managed_source']['source_id'] == 'zz-managed'
        assert authority_reads == []
        # List SQL projects only the RCSB scalar, not the complete metadata value.
        list_sql = [s for s in statements if 'LIMIT' in s]
        assert list_sql and 'JSON_EXTRACT' in list_sql[0]
        actual_limit = parameters_seen[statements.index(list_sql[0])][-2]
        assert actual_limit == 51
        assert 'metadata_json' not in list_sql[0].split('JSON_EXTRACT', 1)[0]
        ids = []
        while True:
            ids.extend(row['source_id'] for row in page['sources'])
            assert all(row['metadata'] == {} and 'authority_receipt' not in row for row in page['sources'])
            if not page['next_cursor']:
                break
            page = (await client.get(path, params={'summary': True, 'limit': 50, 'after': page['next_cursor']})).json()
        assert ids == [f'source-{i:04}' for i in range(count)] + ['zz-managed']
        assert len(ids) == len(set(ids))
        assert authority_reads == []
        detail = await client.get(f'{path}/source-{count - 1:04}')
        assert detail.status_code == 200
        exact = detail.json()
        assert exact['metadata']['scientific'] == 'x' * 8192
        assert exact['metadata']['explicit_false'] is False
        assert exact['metadata']['zero'] == 0
        assert exact['metadata']['null'] is None
        assert exact['authority_receipt']['payload']['scientific'] == 'y' * 8192
        assert authority_reads == [f'source-{count - 1:04}']
        assert (await client.get(f'{path}/hidden')).status_code == 404
        assert (await client.get(f'{path}/removed')).status_code == 404
        managed = (await client.get(f'{path}/zz-managed')).json()
        assert managed['managed_checkpoint'] and managed['metadata'] == {'managed': 'exact'}
        matched = (await client.get(path, params={'summary': True, 'search': 'source-0000', 'source_kind': 'complex_snapshot'})).json()
        assert [x['source_id'] for x in matched['sources']] == ['source-0000']
        for kind in kinds:
            filtered = (await client.get(path, params={'summary': True, 'source_kind': kind})).json()
            assert all(x['source_kind'] == kind for x in filtered['sources'])
        assert (await client.get(path, params={'summary': True, 'limit': 101})).status_code == 422
        before_legacy_reads = len(authority_reads)
        legacy_response = await client.get(path)
        legacy = legacy_response.json()
        assert len(legacy['sources']) == count + 1
        assert next(row for row in legacy['sources'] if row['source_id'] == exact['source_id']) == exact
        print('CM_PICKER_METRIC ' + json.dumps({
            'fixture_sources': count, 'legacy_bytes': len(legacy_response.content),
            'summary_bytes': len(first.content), 'summary_rows': len(first.json()['sources']),
            'list_authority_reads': 0, 'exact_detail_authority_reads': 1,
            'legacy_authority_reads': len(authority_reads) - before_legacy_reads,
            'list_projection_sql_limit': actual_limit, 'exact_detail_bytes': len(detail.content),
            'traversed_rows': len(ids), 'ensure_calls': len(ensure_calls),
        }))
    await engine.dispose()
