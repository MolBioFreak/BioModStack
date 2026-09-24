"""Portable mask discovery/persistence and CPU native transport qualification."""
import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from services.bindcraft2_typed import compile_typed, schema, validate_request
from services.bindcraft2_runtime import prepare_campaign


@pytest.mark.parametrize('params', [{}, {'interface_mask': None}, {'interface_mask': []},
                                   {'interface_mask': [0, 0.25, -0.5, 2, 1]}])
def test_mask_discovery_and_request_receipt_roundtrip(params, tmp_path):
    descriptor = schema()['nested_control_schemas']['losses']['properties']['induced_fit_interface']['properties']['params']['properties']['interface_mask']
    assert descriptor['type'] == ['array', 'null']
    assert descriptor['items'] == {'type': 'number'}
    assert descriptor['default'] is None
    assert descriptor['control'] == 'native-numeric-vector'
    assert 'unresolved_reason' not in descriptor
    request = {'max_trajectories': 1, 'losses': {'induced_fit_interface': {'params': params}}}
    original = copy.deepcopy(request)
    assert validate_request(request) == original
    # Saved/cloned JSON and compiled receipts must not coerce None, [], or omission.
    restored = json.loads(json.dumps(request))
    compiled = compile_typed(restored, tmp_path / 'campaign', lambda value: copy.deepcopy(value))
    prepared = prepare_campaign(compiled, tmp_path, lambda value: copy.deepcopy(value), lambda _: (),
                                lambda path: json.loads(path.read_text()))
    receipt = json.loads(Path(prepared['receipt_path']).read_text())
    native = json.loads(Path(prepared['settings_path']).read_text())
    assert native['losses']['induced_fit_interface']['params'] == params
    for key in ('requested_settings', 'native_request', 'effective_settings'):
        assert receipt[key]['losses']['induced_fit_interface']['params'] == params
    assert request == original


@pytest.mark.parametrize('mask', [True, 1, '1,0', {}, [True], ['1'], [[1]], [None],
                                 [float('nan')], [float('inf')]])
def test_mask_accepts_only_portable_numeric_vector_or_null(mask):
    with pytest.raises(ValueError, match='interface_mask'):
        validate_request({'max_trajectories': 1,
                          'losses': {'induced_fit_interface': {'params': {'interface_mask': mask}}}})


@pytest.mark.asyncio
@pytest.mark.parametrize('params', [{}, {'interface_mask': None}, {'interface_mask': [0, .25, -1, 2]}])
async def test_saved_template_reopen_clone_preserves_native_mask(params, tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from database import UserTemplate
    from routers.user_templates import UserTemplateCreate, create_user_template, get_user_template
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mask-templates.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(UserTemplate.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        settings = {'max_trajectories': 1, 'losses': {'induced_fit_interface': {'params': params}}}
        async with sessions() as session:
            created = await create_user_template(UserTemplateCreate(
                name='mask fixture', model_id='bindcraft2', mode='campaign',
                base_template_id='antibody_denovo', params={'bindcraft2_settings': settings}), session=session)
            identity = created.id
        async with sessions() as session:
            reopened = await get_user_template(identity, session=session)
            assert reopened.params['bindcraft2_settings'] == settings
            clone = await create_user_template(UserTemplateCreate(
                name='mask clone', model_id=reopened.model_id, mode=reopened.mode,
                base_template_id=reopened.base_template_id, params=reopened.params), session=session)
            clone_identity = clone.id
        async with sessions() as session:
            cloned = await get_user_template(clone_identity, session=session)
            assert cloned.params['bindcraft2_settings'] == settings
    finally:
        await engine.dispose()


def test_installed_native_cpu_mask_transport(tmp_path):
    image = os.environ.get('BMS_TEST_BC2_IMAGE')
    if not image:
        pytest.skip('set BMS_TEST_BC2_IMAGE for installed native CPU qualification')
    root = Path(__file__).parents[3]
    fixture = Path(__file__).parent / 'fixtures/bindcraft2_interface_mask_native.py'
    result = subprocess.run(['apptainer', 'exec', '--bind', f'{root}:{root}', '--bind', f'{tmp_path}:{tmp_path}',
                             '--env', f'JAX_PLATFORMS=cpu,JAX_PLATFORM_NAME=cpu,JAX_COMPILATION_CACHE_DIR={tmp_path}/jax-cache', image, 'python3',
                             str(fixture), str(root), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'BC2_NATIVE_MASK_CPU_OK' in result.stdout
    print(result.stdout)
