"""Native generator request identity and antibody iteration source boundaries."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from database import Job
from routers.user_templates import UserTemplateCreate, create_user_template, get_user_template
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database import UserTemplate
from model_registry import get_registry
from routers.jobs import normalize_job_request
from schemas import JobCreate
from routers.jobs import _resolve_antibody_root_job, _validate_selected_design_owners


class Session:
    def __init__(self, *jobs):
        self.jobs = {job.id: job for job in jobs}

    async def get(self, model, id):
        assert model is Job
        return self.jobs.get(id)


def job(id, model, *, params=None, parent=None):
    return SimpleNamespace(id=id, name=id, model_id=model, mode='campaign',
                           params=params or {}, parent_job_id=parent,
                           execution_target_id=None)


@pytest.mark.asyncio
async def test_bc2_campaign_does_not_become_an_antibody_refinement_root():
    root = job('root', 'bindcraft2', params={'bc2_native_only': {'objective': 'native'}})
    source = job('round-1', 'template_antibody_denovo',
                 params={'iteration_source_root_job_id': 'root', 'iteration_source_job_id': 'root'})
    session = Session(root, source)
    resolved_source, resolved_root = await _resolve_antibody_root_job(session, source.id)
    assert (resolved_source.id, resolved_root.id) == ('round-1', 'round-1')
    with pytest.raises(HTTPException) as exc:
        await _resolve_antibody_root_job(session, root.id)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_foreign_root_and_unrelated_design_refused():
    root = job('root', 'template_antibody_denovo')
    source = job('source', 'template_antibody_denovo',
                 params={'iteration_source_root_job_id': root.id})
    foreign = job('foreign', 'bindcraft2')
    session = Session(root, source, foreign)
    for design in (
        SimpleNamespace(id='other', job_id=foreign.id, lineage_root_job_id=foreign.id),
        SimpleNamespace(id='other', job_id=foreign.id, lineage_root_job_id=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await _validate_selected_design_owners(session, source, root, [design])
        assert exc.value.status_code == 422


@pytest.mark.parametrize('mode,required,wrong', [
    ('nanobody_binder', {'boltzgen_target_pdb_path': '/target.pdb'}, 'ppiflow'),
    ('generator_backbone_refine', {'ppiflow_seed_complex_path': '/seed.pdb'}, 'boltzgen'),
])
def test_generator_mode_rejects_conflicting_saved_selector(mode, required, wrong):
    registry = get_registry()
    for selector in ('denovo_generator', 'generator'):
        assert any('conflicts' in error for error in registry.validate_job_params(
            'antibody_denovo', mode, {**required, selector: wrong}))
        with pytest.raises(HTTPException) as exc:
            normalize_job_request(JobCreate(name='conflicting saved generator',
                model_id='antibody_denovo', mode=mode, params={**required, selector: wrong}))
        assert exc.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,params', [
    ('nanobody_binder', {'denovo_generator': 'boltzgen', 'boltzgen_mode': 'nanobody_binder',
                         'boltzgen_target_pdb_path': '/target.pdb', 'boltzgen_num_designs': 7}),
    ('generator_backbone_refine', {'denovo_generator': 'ppiflow',
                                   'ppiflow_seed_complex_path': '/seed.pdb',
                                   'ppiflow_mode': 'backbone_refine', 'ppiflow_samples_per_target': 3}),
])
async def test_saved_generator_template_reopens_exact_mode_and_params(tmp_path, mode, params):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'saved.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(UserTemplate.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            saved = await create_user_template(UserTemplateCreate(name=mode,
                model_id='antibody_denovo', base_template_id='antibody_denovo',
                mode=mode, params=params), session=session)
            reopened = await get_user_template(saved.id, session=session)
            assert reopened.mode == mode
            assert reopened.params == params
    finally:
        await engine.dispose()


@pytest.mark.parametrize('mode,settings', [
    ('nanobody_binder', {'denovo_generator': 'boltzgen', 'diffusion_method': 'boltzgen',
                         'boltzgen_target_pdb_path': '/target.pdb', 'boltzgen_mode': 'nanobody_binder',
                         'boltzgen_num_designs': 7}),
    ('generator_backbone_refine', {'denovo_generator': 'ppiflow',
                                   'ppiflow_seed_complex_path': '/seed.pdb',
                                   'ppiflow_mode': 'backbone_refine',
                                   'ppiflow_samples_per_target': 3}),
])
def test_saved_generator_request_keeps_native_mode_and_science(mode, settings):
    request = JobCreate(name='saved generator', model_id='antibody_denovo', mode=mode,
                        params=settings)
    normalized = normalize_job_request(request)
    reopened = normalize_job_request(JobCreate.model_validate(normalized.model_dump(mode='json')))
    assert reopened.model_dump() == normalized.model_dump()
    assert reopened.mode == mode
    for key, value in settings.items():
        assert reopened.params[key] == value
    if mode == 'generator_backbone_refine':
        assert not reopened.params.get('ppiflow_checkpoint')
