"""Real native import -> PM receipt custody, isolated from live databases."""
from pathlib import Path
import copy

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job
from routers import external_imports
from schemas import ExternalImportCreateRequest, ExternalImportPreviewRequest
from services.external_imports.service import process_external_import
from services.global_experiments.adapters import CoreProteinResultAdapter, TypedCoreJobResultAdapter, AdapterError
from test_protenix_external_import import source
from test_lineage_native_authority import attach_and_reverify


@pytest.mark.asyncio
async def test_import_receipts_and_retained_containment(source, tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_DATA', str(tmp_path / 'data'))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path / 'data/results'))
    monkeypatch.setenv('BMS_BUILD_SHA', 'native-import-pm-test')
    monkeypatch.setattr(external_imports, 'resolve_allowed_path', lambda _: source)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql('PRAGMA foreign_keys=ON')
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            preview = await external_imports.preview_external_import(ExternalImportPreviewRequest(
                source_path='inputs/native', provider_hint='protenix_native'))
            queued = await external_imports.create_external_import(ExternalImportCreateRequest(
                source_path='inputs/native', provider='protenix_native',
                preview_fingerprint=preview.source_fingerprint, dataset_name='Native result'), session)
            imported = await process_external_import(session, import_id=queued.id, data_root=tmp_path / 'data')
            assert imported.state == 'completed'
            job = await session.get(Job, imported.bms_job_id)
            design = (await session.scalars(select(Design).where(Design.job_id == job.id))).one()
            before = copy.deepcopy(job.provenance)
            adapter = TypedCoreJobResultAdapter('protenix')
            receipt = await adapter.verify(session, job.id)
            individual = await CoreProteinResultAdapter().verify(session, design.id)
            assert receipt['metadata']['artifact_count'] == 1
            assert receipt['metadata']['artifact_authority'] == 'verified_external_import_design_receipts'
            assert individual['reopen_uri'].startswith(f'/designs/{job.id}?design_id=')
            await attach_and_reverify(tmp_path, session, adapter, job.id, receipt['content_digest'])
            assert job.provenance == before
            assert 'core_protein_candidate_publication' not in job.provenance
            assert 'remote_execution_receipt' not in job.provenance
            assert job.started_at is None
            assert not session.dirty
            # The ordinary results resolver remains narrow even for this Job.
            from services.global_experiments.adapters import resolve_persisted_job_result_root
            with pytest.raises(ValueError):
                resolve_persisted_job_result_root(job)
            original_root = job.output_dir
            job.output_dir = str(tmp_path / 'outside')
            with pytest.raises(AdapterError, match='root differs'):
                await adapter.verify(session, job.id)
            job.output_dir = original_root
            original_mode = job.mode
            job.mode = 'prediction'
            with pytest.raises(AdapterError):
                await adapter.verify(session, job.id)
            job.mode = original_mode
            manifest = Path(job.source_selection_manifest_path)
            data = manifest.read_bytes()
            manifest.write_bytes(data + b' ')
            with pytest.raises(AdapterError, match='changed native import manifest'):
                await adapter.verify(session, job.id)
            manifest.write_bytes(data)
            original_structure = Path(design.pdb_path).read_bytes()
            Path(design.pdb_path).write_bytes(original_structure + b'\n')
            with pytest.raises(AdapterError, match='changed native import artifact'):
                await adapter.verify(session, job.id)
            Path(design.pdb_path).write_bytes(original_structure)
            outside = tmp_path / 'outside.cif'
            outside.write_bytes(original_structure)
            Path(design.pdb_path).unlink()
            Path(design.pdb_path).symlink_to(outside)
            with pytest.raises(AdapterError):
                await adapter.verify(session, job.id)
    finally:
        await engine.dispose()
