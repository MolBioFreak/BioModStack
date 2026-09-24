"""Real SQLite Job creation -> BC2 native CPU compiler/materializer -> argv.

No native design/inference is run. The installed pinned image resolves settings
on CPU; scheduler dispatch is not started by these direct API calls.
"""
import json
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job
from routers import jobs
from schemas import JobCreate
from services import bindcraft2_launch as launch
from services.bindcraft2_runtime import NATIVE_ACTIONS, native_command
from test_bindcraft2_lifecycle import setup


@pytest.mark.asyncio
async def test_actual_lifecycle_creation_materializes_and_compiles_every_mode(tmp_path, monkeypatch):
    if not Path('/mnt/BioModStack/apptainer/bindcraft2.sif').is_file():
        pytest.skip('installed pinned BC2 settings compiler image unavailable')
    setup(tmp_path, monkeypatch, launch._native_compile)
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: tmp_path)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "actions.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            source = Job(id='native-source', name='source', model_id='bindcraft2', mode='campaign',
                         status='completed', params={}, output_dir=str(tmp_path / 'parent'),
                         lineage_root_job_id='native-root')
            session.add(source)
            await session.commit()
            parent_bytes = (tmp_path / 'parent/bindcraft2/compilation.json').read_bytes()
            outputs = set()
            for operation in NATIVE_ACTIONS:
                options = {'structure_relative_path': 'candidate.cif'} if operation == 'score' else {}
                request = JobCreate(name='same-name-child', model_id='bindcraft2', mode=operation,
                    parent_job_id=source.id, params={'bc2_source_job_id': source.id, 'bc2_action_options': options})
                response = await jobs.create_job(request, BackgroundTasks(), session)
                child = await session.get(Job, response.id)
                assert child.output_dir == str(tmp_path / child.id)
                assert child.output_dir not in outputs
                outputs.add(child.output_dir)
                assert child.mode == operation
                assert child.parent_job_id == source.id
                assert child.lineage_root_job_id == 'native-root'
                assert child.selection_source_job_id == source.id
                assert (child.vram_estimate_mb > 0) == (operation == 'resume')
                if operation != 'resume':
                    assert child.pinned_gpu is None
                compilation = json.loads(Path(child.params['bc2_compilation']).read_text())
                assert compilation['native_action'] == {'operation': operation, 'options': options, 'source_job_id': source.id}
                assert compilation['native_request']['resume'] is (operation == 'resume')
                campaign = Path(child.params['bc2_campaign_dir']) / 'campaign'
                assert (campaign / '.campaign_state.json').is_file()
                command = native_command(compilation, campaign, campaign.parent / 'native_settings.json')
                assert command[1] == ('design' if operation == 'resume' else operation)
                assert (tmp_path / 'parent/bindcraft2/compilation.json').read_bytes() == parent_bytes
    finally:
        await engine.dispose()


def test_lifecycle_native_options_fail_at_existing_request_boundary():
    with pytest.raises(HTTPException) as failure:
        jobs.normalize_job_request(JobCreate(name='bad native options', model_id='bindcraft2', mode='rank',
            params={'bc2_source_job_id': 'source', 'bc2_action_options': {'not_native': True}}))
    assert failure.value.status_code == 422
