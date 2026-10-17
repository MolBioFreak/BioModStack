"""Exact original-confidence inventory through the existing download owner."""
import hashlib
import json
import os
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, get_session
from routers import designs, files
from services import protenix_scientific_consumer as consumer
from test_protenix_native_confidence import make_publication, store


def app_for(session):
    app = FastAPI()
    app.include_router(designs.router, prefix='/api/designs')
    app.include_router(files.router, prefix='/api/files')
    async def dependency():
        yield session
    app.dependency_overrides[get_session] = dependency
    app.dependency_overrides[files.get_governed_ngs_result_roots] = lambda: ()
    return app


async def check_inventory(session, design_id):
    row = await session.get(Design, design_id)
    selected = await consumer.verified_native_design(row, session)
    async with AsyncClient(transport=ASGITransport(app=app_for(session)), base_url='http://test') as client:
        response = await client.get(f'/api/designs/{design_id}/confidence-artifacts')
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload['status'] == 'ok', payload
        assert payload['producer'] == 'protenix'
        assert {a['role'] for a in payload['artifacts']} == {'structure', 'metrics', 'pae'}
        for artifact in payload['artifacts']:
            role = artifact['role']
            expected = selected['artifacts'][role]
            assert artifact['retained'] and artifact['filename'] == Path(expected['path']).name
            download = await client.get(artifact['download_url'])
            assert download.status_code == 200, download.text
            assert download.content == selected['snapshots'][role]
            assert hashlib.sha256(download.content).hexdigest() == artifact['artifact_sha256'] == expected['sha256']
            assert len(download.content) == artifact['size_bytes']
        assert not session.dirty and not session.new
        return payload


@pytest.mark.asyncio
@pytest.mark.parametrize('custody', ['local', 'remote', 'retained_remote'])
@pytest.mark.parametrize('fault', [None, 'missing', 'changed', 'foreign'])
async def test_exact_confidence_inventory(tmp_path, monkeypatch, custody, fault):
    import paths
    job, design, root = make_publication(tmp_path, custody=custody)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'bms_results': root})
    engine, factory = await store(tmp_path, job, design)
    try:
        async with factory() as session:
            row = await session.get(Design, design.id)
            selected = await consumer.verified_native_design(row, session)
            full = Path(selected['artifacts']['pae']['path'])
            # A different input with the same sample suffix is never a fallback.
            full.with_name('foreign_full_data_sample_0.json').write_bytes(full.read_bytes())
            if fault == 'missing':
                full.unlink()
            elif fault == 'changed':
                full.write_bytes(b'{}')
            elif fault == 'foreign':
                row.json_path = str(full.with_name('foreign_summary_confidence_sample_0.json'))
                await session.commit()
            if fault is None:
                await check_inventory(session, design.id)
            else:
                async with AsyncClient(transport=ASGITransport(app=app_for(session)), base_url='http://test') as client:
                    response = await client.get(f'/api/designs/{design.id}/confidence-artifacts')
                    assert response.status_code == 200
                    assert response.json()['status'] == 'unavailable'
                    assert response.json()['artifacts'] == []
                    # Optional inventory failure does not remove the structure.
                    structure = await client.get(f'/api/designs/{design.id}/pdb')
                    assert structure.status_code == 200
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retained_inventory_readonly(tmp_path, monkeypatch):
    """Opt-in real rows copied with Core; retained native files only read."""
    database = os.environ.get('BMS_PROTENIX_INVENTORY_DATABASE')
    if not database:
        pytest.skip('read-only retained Protenix database not selected')
    import paths
    ids = json.loads(os.environ['BMS_PROTENIX_INVENTORY_DESIGN_IDS'])
    assert isinstance(ids, list) and ids and all(isinstance(value, str) for value in ids)
    source = create_engine(f'sqlite:///file:{database}?mode=ro&uri=true')
    # Copy only exact referenced Parquet files before typed JSON resolution.
    import shutil
    from sqlalchemy import text
    copied_root = tmp_path / 'scientific_artifacts'
    monkeypatch.setenv('BMS_SCIENTIFIC_ARTIFACT_ROOT', str(copied_root))
    def copy_references(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return
        if isinstance(value, dict):
            if value.get('schema') == 'bms.scientific-artifact-row-reference.v1':
                from services.scientific_artifacts.contracts import require_row_reference
                relative = Path(require_row_reference(value)['relative_path'])
                assert not relative.is_absolute() and '..' not in relative.parts
                destination = copied_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(database).parent / 'scientific_artifacts' / relative, destination)
            for item in value.values():
                copy_references(item)
        elif isinstance(value, list):
            for item in value:
                copy_references(item)
    with source.connect() as conn:
        for design_id in ids:
            raw = conn.execute(text('SELECT * FROM designs WHERE id = :id'), {'id': design_id}).mappings().one()
            for value in raw.values():
                copy_references(value)
        design_rows = [dict(r) for r in conn.execute(select(Design.__table__).where(Design.id.in_(ids))).mappings()]
        jobs = {row['job_id'] for row in design_rows}
        job_rows = [dict(r) for r in conn.execute(select(Job.__table__).where(Job.id.in_(jobs))).mappings()]
    source.dispose()
    assert len(job_rows) == len(jobs) and len(design_rows) == len(ids)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'bms_results': Path(database).parent / 'bms_results'})
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "retained-copy.sqlite"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # No ORM flush/publication hooks and no live-store writes.
        await conn.execute(Job.__table__.insert(), job_rows)
        await conn.execute(Design.__table__.insert(), design_rows)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            reports = [await check_inventory(session, design_id) for design_id in ids]
        output = os.environ.get('BMS_PROTENIX_INVENTORY_REPORT')
        if output:
            Path(output).write_text(json.dumps(reports, indent=2))
    finally:
        await engine.dispose()
