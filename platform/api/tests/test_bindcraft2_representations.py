"""Three source-qualified representation extensions, no new launch policy."""
import copy
import json
import os
from pathlib import Path
import subprocess

import pytest
from services.bindcraft2_typed import compile_typed, schema, validate_request


@pytest.mark.parametrize('overrides', [
    {}, {'design_models': 2}, {'design_models': []},
    {'design_models': ['model_3_multimer_v3', 'model_1_multimer_v3']},
    {'design_models': [2, '0']}, {'parameter_sweep': False},
    {'parameter_sweep': True}, {'parameter_sweep': {}},
    {'parameter_sweep': {'max_arms': 3}},
    {'filters': {'Binder_RMSD': {'threshold': None, 'higher': False, 'mandatory': False}}},
    {'filters': {'Binder_RMSD': {'threshold': 0}}},
])
def test_request_compile_reopen(overrides, tmp_path):
    request = {'max_trajectories': 100, **overrides}
    original = copy.deepcopy(request)
    restored = json.loads(json.dumps(request))
    compiled = compile_typed(restored, tmp_path, copy.deepcopy, lambda _: ())
    assert compiled['requested_settings'] == original
    assert {k: v for k, v in compiled['native_request'].items() if k not in ('project_folder', 'resume')} == original
    assert restored == original


def test_common_discovery_and_mask_descriptor():
    data = schema()
    assert data['fields']['design_models']['observed_types'] == ['integer', 'array']
    assert data['fields']['parameter_sweep']['observed_types'] == ['object', 'boolean']
    nested = data['nested_control_schemas']
    assert nested['design_models']['items']['type'] == ['integer', 'string']
    assert nested['parameter_sweep']['type'] == ['object', 'boolean']
    assert nested['filters']['properties']['Binder_RMSD']['properties']['threshold']['type'] == ['number', 'null']
    mask = nested['losses']['properties']['induced_fit_interface']['properties']['params']['properties']['interface_mask']
    assert mask['type'] == ['array', 'null']
    assert mask['items'] == {'type': 'number'}
    assert mask['control'] == 'native-numeric-vector'


@pytest.mark.parametrize('bad', [
    {'design_models': [True]}, {'design_models': [[]]}, {'design_models': [1.5]},
    {'design_models': None}, {'design_models': False},
    {'parameter_sweep': None}, {'parameter_sweep': []}, {'parameter_sweep': 1},
    {'filters': {'Binder_RMSD': {'threshold': False}}},
    {'filters': {'Binder_RMSD': {'threshold': 'null'}}},
])
def test_no_unqualified_representations(bad):
    with pytest.raises(ValueError):
        validate_request({'max_trajectories': 100, **bad})


@pytest.mark.asyncio
@pytest.mark.parametrize('overrides', [
    {}, {'design_models': 2, 'parameter_sweep': False},
    {'design_models': [], 'parameter_sweep': {}},
    {'design_models': ['model_3_multimer_v3', 'model_1_multimer_v3'], 'parameter_sweep': True,
     'filters': {'Binder_RMSD': {'threshold': None, 'higher': False, 'mandatory': False}}},
])
async def test_saved_template_reopen_clone(overrides, tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from database import UserTemplate
    from routers.user_templates import UserTemplateCreate, create_user_template, get_user_template
    settings = {'max_trajectories': 100, **overrides}
    validate_request(settings)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'representations.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(UserTemplate.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            created = await create_user_template(UserTemplateCreate(
                name='representations', model_id='bindcraft2', mode='campaign',
                base_template_id='antibody_denovo', params={'bindcraft2_settings': settings}), session=session)
            identity = created.id
        async with sessions() as session:
            reopened = await get_user_template(identity, session=session)
            assert reopened.params['bindcraft2_settings'] == settings
            clone = await create_user_template(UserTemplateCreate(
                name='representation clone', model_id=reopened.model_id, mode=reopened.mode,
                base_template_id=reopened.base_template_id, params=reopened.params), session=session)
            clone_identity = clone.id
        async with sessions() as session:
            cloned = await get_user_template(clone_identity, session=session)
            assert cloned.params['bindcraft2_settings'] == settings
    finally:
        await engine.dispose()


def test_installed_native_cpu_representations(tmp_path):
    image = os.environ.get('BMS_TEST_BC2_IMAGE')
    upstream = os.environ.get('BMS_TEST_BC2_UPSTREAM')
    if not image or not upstream:
        pytest.skip('set BMS_TEST_BC2_IMAGE and BMS_TEST_BC2_UPSTREAM for pinned native CPU proof')
    root = Path(__file__).parents[3]
    fixture = Path(__file__).parent / 'fixtures/bindcraft2_representations_native.py'
    result = subprocess.run(['apptainer', 'exec', '--bind', f'{root}:{root}', '--bind', f'{tmp_path}:{tmp_path}',
                             '--bind', f'{upstream}:{upstream}:ro',
                             '--env', f'JAX_PLATFORMS=cpu,JAX_PLATFORM_NAME=cpu,JAX_COMPILATION_CACHE_DIR={tmp_path}/jax-cache',
                             image, 'python3', str(fixture), str(root), str(tmp_path), upstream],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'BC2_NATIVE_REPRESENTATIONS_CPU_OK 18' in result.stdout
    print(result.stdout)
