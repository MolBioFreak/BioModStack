import asyncio
import json
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select, func, event
from migrations.sqlite_sha256 import register_sqlite_sha256
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bms_alignment.bundle import make_bundle, inspect_project_bundle
from bms_alignment.http import AlignmentClient, BmsHttpError
from experiment_database import get_experiment_session
from experiment_migrations import run_all
from experiment_models import ExperimentArtifact, ExperimentExternalEntityReceipt
from experiment_services import create_project, add_audit_event
from routers.family_msa_results import router
from test_project_manager_adapters import _project_payload


def test_real_portable_import_export_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_EXPERIMENT_ARTIFACT_ROOT', str(tmp_path / 'blobs'))
    monkeypatch.setenv('BMS_CM_TRUSTED_PROXY_SECRET', 'scratch-secret')
    run_all(tmp_path / 'pm.db')
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pm.db'}")
    @event.listens_for(engine.sync_engine, 'connect')
    def configure(connection, _):
        register_sqlite_sha256(connection)
        connection.execute('PRAGMA foreign_keys=ON')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def setup():
        async with sessions() as session:
            project = await create_project(session, _project_payload())
            add_audit_event(session, workspace_id=project.id, resource_id=project.id,
                event_type='workspace_owner_bound', generation=project.head_generation,
                payload={'principal_id':'local-application-operator'})
            await session.commit()
            return project.id
    project = asyncio.run(setup())
    app = FastAPI(); app.include_router(router)
    async def dependency():
        async with sessions() as session:
            yield session
    app.dependency_overrides[get_experiment_session] = dependency
    class Response:
        def __init__(self, data): self.data = data
        def read(self): return self.data
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Opener:
        def open(self, request, timeout=None):
            import httpx
            from urllib.error import HTTPError
            async def call():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
                    content = request.data
                    if content is not None and not isinstance(content, bytes):
                        async def stream(source=content):
                            for chunk in source:
                                yield chunk
                        content = stream()
                    return await c.request(request.get_method(), request.full_url, headers=dict(request.header_items()), content=content)
            response = asyncio.run(call())
            if response.status_code >= 400:
                import io
                raise HTTPError(request.full_url, response.status_code, '', {}, io.BytesIO(response.content))
            return Response(response.content)
    try:
        client = AlignmentClient('http://test', headers={'X-BMS-CM-Proxy-Secret':'scratch-secret'})
        client._opener = Opener()
        source = tmp_path / 'source'; source.mkdir()
        values = {'alignment': b'>r1\nAC-G\n>r2\nACGG\n', 'tree': b'(r1,r2);\n',
                  'log': b'actual retained fixture log\n' * 1200000, 'receipt': json.dumps({'requested': {'roster': {'molecule':'dna', 'rows': [{'row_id':'r1','header':'one','sequence':'ACG'}, {'row_id':'r2','header':'two','sequence':'ACGG'}]}}, 'effective_settings': {'threads': 1}, 'runtime': {'version':'fixture'}}).encode(),
                  'row_map': b'{"r1":"r1","r2":"r2"}'}
        artifacts = []
        for role, data in values.items():
            (source / role).write_bytes(data)
            artifacts.append({'id': role, 'role': role, 'logical_path': role, 'path': role})
        state = {'state': {'marks': [{'id':'mark','label':'fixture'}]}}
        (source / 'state').write_text(json.dumps(state))
        inventory = {'schema':'bms.family-msa.project-export.v1', 'project_id':'fixture-source', 'receipt_id':'fixture-receipt', 'artifacts':artifacts, 'shared_states':[{'id':'original-state','path':'state'}]}
        (source / 'project-export.json').write_text(json.dumps(inventory))
        original = tmp_path / 'original'
        make_bundle(source, original, ['project-export.json', *values, 'state'])
        import tempfile
        import tracemalloc
        scratch = tmp_path / 'upload-scratch'; scratch.mkdir()
        monkeypatch.setattr(tempfile, 'tempdir', str(scratch))
        manifest = json.loads((original / 'manifest.json').read_text())
        metadata = json.dumps({'replay_key': 'malformed', 'manifest': manifest}).encode()
        async def bad_upload(body):
            import httpx
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
                return await c.post(f'/api/family-msa/projects/{project}/imports/stream',
                                    headers=client.headers, content=body)
        truncated = len(metadata).to_bytes(8, 'big') + metadata + b'x'
        assert asyncio.run(bad_upload(truncated)).status_code == 400
        assert list(scratch.iterdir()) == []
        tracemalloc.start()
        first = client.import_project_bundle_streaming(project, original, replay_key='first')
        async def browser_export():
            import httpx
            import io
            import zipfile
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers=client.headers) as c:
                response = await c.post(f'/api/family-msa/projects/{project}/receipts/{first.receipt_id}/bundle', json={'shared_state_ids': []})
                assert response.status_code == 200, response.text
                assert response.headers['content-type'] == 'application/zip'
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    archive.extractall(tmp_path / 'browser-export')
                assert inspect_project_bundle(tmp_path / 'browser-export')['inventory']['artifacts']
                missing = await c.post(f'/api/family-msa/projects/{project}/receipts/{first.receipt_id}/bundle', json={'shared_state_ids': ['missing']})
                assert missing.status_code == 404
        page = client._request('GET', f'/api/family-msa/projects/{project}/receipts')
        assert page['items'] == [{'receipt_id': first.receipt_id, 'entity_kind': 'family_msa_import', 'availability': 'available'}]
        assert page['next_cursor'] is None
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert peak < 16 * 1024 * 1024, peak
        asyncio.run(browser_export())
        assert list(scratch.iterdir()) == []
        assert client.import_project_bundle(project, original, replay_key='first').replayed
        assert not first.replayed and first.shared_state == 'retained_unmapped'
        exported = tmp_path / 'exported'
        client.export_project_bundle(project, first.receipt_id, exported)
        relocated = tmp_path / 'relocated'; shutil.move(exported, relocated)
        shutil.rmtree(original); shutil.rmtree(source)
        assert inspect_project_bundle(relocated)['inventory']['artifacts']
        imported = client.import_project_bundle(project, relocated, replay_key='second')
        replay = client.import_project_bundle_streaming(project, relocated, replay_key='second')
        assert replay.replayed and replay.receipt_id == imported.receipt_id
        url = f'/api/family-msa/projects/{project}/receipts/{imported.receipt_id}'
        readback = client._request('GET', url)
        assert readback['import_provenance']['shared_state'] == 'retained_unmapped'
        listing = readback['artifacts']
        for artifact in listing:
            from urllib.request import Request
            with client._opener.open(Request(client.base_url + url + '/artifacts/' + artifact['id'], headers=client.headers)) as response:
                data = response.read()
            role = artifact['role']
            if role == 'shared_state_snapshot': assert json.loads(data) == state
            else: assert data == values[role]
        align = next(x for x in listing if x['role'] == 'alignment')
        assert len(client._request('GET', url + '/documents/' + align['id'])['rows']) == 2
        again = tmp_path / 'again'; client.export_project_bundle(project, imported.receipt_id, again)
        assert inspect_project_bundle(again)['inventory']['artifacts']
        with pytest.raises(BmsHttpError) as unauthorized:
            anonymous = AlignmentClient(client.base_url); anonymous._opener = Opener()
            anonymous._request('GET', url)
        assert unauthorized.value.status == 401
        async def counts():
            async with sessions() as session:
                return (await session.scalar(select(func.count()).select_from(ExperimentExternalEntityReceipt)), await session.scalar(select(func.count()).select_from(ExperimentArtifact)))
        assert asyncio.run(counts()) == (2, 12)
        from services.family_msa import publication
        original_retain = publication.retain_artifact
        calls = 0
        async def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise publication.CustodyError('injected failure')
            return await original_retain(*args, **kwargs)
        monkeypatch.setattr(publication, 'retain_artifact', fail_second)
        with pytest.raises(BmsHttpError) as failed:
            client.import_project_bundle_streaming(project, again, replay_key='rollback')
        assert failed.value.status == 400 and calls == 2
        assert list(scratch.iterdir()) == []
        assert asyncio.run(counts()) == (2, 12)
        monkeypatch.setattr(publication, 'retain_artifact', original_retain)
        retry = client.import_project_bundle_streaming(project, again, replay_key='rollback')
        assert not retry.replayed
        assert client.import_project_bundle_streaming(project, again, replay_key='rollback').replayed
        assert asyncio.run(counts()) == (3, 18)
        inventory = json.loads((relocated / 'project-export.json').read_text())
        inventory['project_id'] = 'different-source'
        (relocated / 'project-export.json').write_text(json.dumps(inventory))
        mutated = tmp_path / 'mutated'
        manifest = json.loads((relocated / 'manifest.json').read_text())
        make_bundle(relocated, mutated, manifest['dependencies']['included'])
        with pytest.raises(BmsHttpError) as conflict:
            client.import_project_bundle(project, mutated, replay_key='second')
        assert conflict.value.status == 409
        assert asyncio.run(counts()) == (3, 18)
        imported = {}
        async def file_imports():
            import httpx
            samples = {
                'fasta': b'>same\r\nAC-G\r\n>same\r\nACGG\r\n',
                'stockholm': b'# STOCKHOLM 1.0\r\n#=GF ID test\r\n#=GS a DE annotated\r\na AC-G\r\nb ACGG\r\n#=GR a SS HHHH\r\n#=GC RF xxxx\r\n//\r\n',
                'clustal': b'CLUSTAL W\n\na AC-G\nb ACGG\n',
                'a3m': b'>a\nACx-G\n>b\nACGG\n',
            }
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers=client.headers) as c:
                for fmt, raw in samples.items():
                    path = f'/api/family-msa/projects/{project}/imports/alignment'
                    params = {'format': fmt, 'molecule': 'dna', 'replay_key': 'file-' + fmt}
                    response = await c.post(path, params=params, content=raw)
                    assert response.status_code == 200, response.text
                    receipt = response.json()['receipt_id']
                    imported[fmt] = (receipt, raw)
                    replay = await c.post(path, params=params, content=raw)
                    assert replay.json()['replayed'] and replay.json()['receipt_id'] == receipt
                    base = f'/api/family-msa/projects/{project}/receipts/{receipt}'
                    listing = (await c.get(base)).json()['artifacts']
                    source = next(item for item in listing if item['role'] == 'source')
                    assert (await c.get(base + '/artifacts/' + source['id'])).content == raw
                    native = next(item for item in listing if item['role'] == 'source_metadata')
                    from bms_alignment.models import SequenceSet
                    from bms_alignment.formats import export_bytes
                    roster = SequenceSet.model_validate_json((await c.get(base + '/artifacts/' + native['id'])).content)
                    assert export_bytes(roster, fmt, original=True)[0] == raw
                    if fmt == 'stockholm':
                        assert [a['kind'] for a in roster.stockholm_annotations] == ['GF', 'GS', 'GR', 'GC']
                    aligned = next(item for item in listing if item['role'] == 'alignment')
                    doc = await c.get(base + '/documents/' + aligned['id'])
                    assert doc.status_code == 200, doc.text
                    assert [r['aligned'] for r in doc.json()['rows']] == ['AC-G', 'ACGG']
        asyncio.run(file_imports())
        for fmt, (receipt, raw) in imported.items():
            destination = tmp_path / ('file-export-' + fmt)
            client.export_project_bundle(project, receipt, destination)
            from bms_alignment.bundle import verify_bundle
            from bms_alignment.models import SequenceSet
            from bms_alignment.formats import export_bytes
            assert verify_bundle(destination)['valid']
            inventory = json.loads((destination / 'project-export.json').read_text())
            native = next(x for x in inventory['artifacts'] if x['role'] == 'source_metadata')
            roster = SequenceSet.model_validate_json((destination / native['path']).read_bytes())
            assert export_bytes(roster, fmt, original=True)[0] == raw
    finally:
        asyncio.run(engine.dispose())
