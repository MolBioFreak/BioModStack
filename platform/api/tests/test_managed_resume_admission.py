"""Resume path admission through real ASGI routes; no job/worker writes."""
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from routers import jobs


@pytest.fixture
def resume_paths(tmp_path, monkeypatch):
    root = tmp_path / 'results'
    root.mkdir()
    retained = root / 'retained'
    retained.mkdir()
    (retained / 'result.txt').write_text('retained evidence')
    monkeypatch.setattr(jobs, 'get_results_dir', lambda: root)
    return root, retained


def test_managed_resume_path_is_read_only_and_optional(resume_paths):
    root, retained = resume_paths
    assert jobs._managed_resume_output_dir(None) is None
    assert jobs._managed_resume_output_dir(str(retained)) == str(retained)
    assert (retained / 'result.txt').read_text() == 'retained evidence'
    assert sorted(p.name for p in root.iterdir()) == ['retained']


def invalid_path(case, root, retained):
    if case == 'foreign':
        return str(root.parent / 'unmanaged' / 'old-job')
    if case == 'root':
        return str(root)
    if case == 'prefix':
        return str(root.with_name('results-foreign') / 'old-job')
    if case == 'missing':
        return str(root / 'missing-job')
    if case == 'file':
        return str(retained / 'result.txt')
    if case == 'symlink':
        link = root / 'link'
        link.symlink_to(retained, target_is_directory=True)
        return str(link)
    if case == 'traversal':
        return str(root / 'unused' / '..' / 'retained')
    return {'relative': 'results/old', 'empty': '', 'wrong_type': 42, 'nul': 'x\x00y'}[case]


@pytest.mark.parametrize('endpoint', ['/api/jobs', '/api/jobs/execution-plan/preview'])
@pytest.mark.parametrize('case,code', [
    ('foreign', 'RESUME_SOURCE_OUTSIDE_MANAGED_STORAGE'),
    ('root', 'RESUME_SOURCE_OUTSIDE_MANAGED_STORAGE'),
    ('prefix', 'RESUME_SOURCE_OUTSIDE_MANAGED_STORAGE'),
    ('missing', 'RESUME_SOURCE_UNAVAILABLE'), ('file', 'RESUME_SOURCE_UNAVAILABLE'),
    ('symlink', 'RESUME_SOURCE_SYMLINK'), ('traversal', 'RESUME_SOURCE_INVALID'),
    ('relative', 'RESUME_SOURCE_INVALID'), ('empty', 'RESUME_SOURCE_INVALID'),
    ('wrong_type', 'RESUME_SOURCE_INVALID'), ('nul', 'RESUME_SOURCE_INVALID'),
])
@pytest.mark.asyncio
async def test_invalid_resume_is_named_422_before_admission(resume_paths, monkeypatch, endpoint, case, code):
    root, retained = resume_paths
    path = invalid_path(case, root, retained)
    app = FastAPI()
    app.include_router(jobs.router, prefix='/api/jobs')
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError('Invalid resume reached database: ' + name)
    async def session():
        yield NoDatabase()
    app.dependency_overrides[jobs.get_session] = session
    app.dependency_overrides[jobs.get_experiment_session] = session
    def no_launch(*args, **kwargs):
        raise AssertionError('Invalid resume reached workflow launch admission')
    monkeypatch.setattr(jobs, '_raise_if_workflow_launches_disabled', no_launch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        result = await client.post(endpoint, json={'name': 'resume refusal',
            'model_id': 'boltz_cp_experimental', 'mode': 'predict',
            'params': {'resume_source_dir': path}})
    assert result.status_code == 422, result.text
    assert result.json()['detail']['code'] == code
    assert str(root.parent) not in result.text
    assert (retained / 'result.txt').read_text() == 'retained evidence'
    assert not (root / 'missing-job').exists()
