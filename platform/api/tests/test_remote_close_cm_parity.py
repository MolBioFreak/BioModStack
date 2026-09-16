"""Closed CM discovery and immutable browser-shaped settings round trips.

No provider calls/GPU operations. Native delivery is separately exercised by
 test_remote_rectify_msa_cm.py using actual native commands and explicit doubles.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path

import pytest
from services.conformational_mapping.contracts import (
    FeaturePolicy, validate_schema, canonical_json_bytes, canonical_sha256,
)
from services.conformational_mapping.request_builder import materialize_trusted_internal_request, canonical_msa_params
from services.workflow_request_types import SubmitRequest
from services.msa_policy import POLICY, apply_msa_policy
from services import nextflow
from component_runtime import SourceIdentity


def settings(provider):
    return {'msa_provider': provider, **{k: v['default'] for group in ('colabfold_settings', 'neurosnap_settings') for k,v in POLICY[group].items()},
        'colabfold_use_env': False, 'colabfold_use_filter': False,
        'colabfold_pairing_strategy': 'complete', 'msa_neurosnap_coverage_percent': 45.5,
        'msa_neurosnap_identity_percent': 65.5, 'msa_neurosnap_max_sequences': 321}


def test_actual_openapi_closed_inventory_discovery():
    from main import app
    document = app.openapi()
    route = document['paths']['/api/conformational-mapping/requests']['post']
    ref = route['requestBody']['content']['application/json']['schema']['$ref']
    schema = document['components']['schemas'][ref.rsplit('/', 1)[-1]]['properties']['feature_policy']
    assert schema['additionalProperties'] is False
    msa = schema['properties']['msa_settings']
    assert msa['type'] == 'object' and 'default' not in msa  # Optional does not mean explicit null is valid.
    assert msa['additionalProperties'] is False
    assert msa['$id'] == 'urn:bms:hosted-msa-settings:v2'
    assert set(msa['properties']) == {'msa_provider', *POLICY['colabfold_settings'], *POLICY['neurosnap_settings']}
    for group in ('colabfold_settings', 'neurosnap_settings'):
        for key, field in POLICY[group].items():
            assert msa['properties'][key] == field


@pytest.mark.parametrize('provider', ['colabfold_api', 'neurosnap_api'])
def test_typed_json_persistence_clone_retry_materializer_compiler(tmp_path, monkeypatch, provider):
    spec = importlib.util.spec_from_file_location('cm_complete_fixture', Path(__file__).with_name('test_conformational_mapping_backend_complete.py'))
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    controls = fixture._recovery_request_params()
    controls['feature_policy'].update(rna_msa_enabled=False, msa_settings=settings(provider))
    wire = SubmitRequest(name='CM parity', backend='protenix_v2_ensemble', ordered_seeds=controls['ordered_seeds'],
        samples_per_seed=controls['samples_per_seed'], feature_policy=controls['feature_policy'],
        runtime_policy=controls['runtime_policy'], analysis_policy=controls['analysis_policy'])
    readback = SubmitRequest.model_validate_json(wire.model_dump_json(exclude={"frustrampnn_settings"}))
    assert readback.feature_policy == wire.feature_policy == controls['feature_policy']
    root = Path(os.environ['BMS_INPUTS']) / tmp_path.name
    materialized = materialize_trusted_internal_request(controls, output_dir=root, request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture-operator')
    original = materialized.request_path.read_bytes()
    request = json.loads(original)
    validate_schema('cm_request_v1', request)
    assert request['feature_policy'] == readback.feature_policy
    retry = materialize_trusted_internal_request(copy.deepcopy(controls), output_dir=root, request_id=request['request_id'], principal_id='fixture-operator')
    assert retry.request_path.read_bytes() == original
    clone = materialize_trusted_internal_request(copy.deepcopy(controls), output_dir=root / 'clone', request_id='6d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture-operator')
    assert json.loads(clone.request_path.read_bytes())['feature_policy'] == readback.feature_policy
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    invocation = nextflow.compile_nextflow_invocation('conformational_mapping', 'map', dict(materialized.launch_params, gpu_id=0), str(tmp_path/'out'), 'cm-parity', source_identity=SourceIdentity('a'*40,'b'*40))
    expected = apply_msa_policy('protenix', settings(provider))
    assert all(invocation.native_parameters[k] == v for k,v in expected.items())
    assert original == materialized.request_path.read_bytes()
    for invalid in ({'unknown': True}, {'msa_neurosnap_max_sequences': 2}, {'colabfold_use_env': 'false'}, {'msa_provider':'local'}):
        bad = copy.deepcopy(request); bad['feature_policy']['msa_settings'] = invalid
        with pytest.raises(ValueError): FeaturePolicy.model_validate(bad['feature_policy'])
        with pytest.raises(ValueError): validate_schema('cm_request_v1', bad)


def test_confor_projection_keeps_native_flags_and_selected_provider():
    request = dict(backend='confornets', feature_policy=dict(mode='regenerate_mutated_protein_v1', msa_settings=settings('neurosnap_api')), confornets={'skip_msa':False})
    before = canonical_json_bytes(request)
    result = canonical_msa_params(request)
    assert result == apply_msa_policy('protenix', settings('neurosnap_api'))
    assert 'protenix_use_msa' not in result and 'protenix_use_template' not in result
    assert canonical_json_bytes(request) == before
    assert canonical_sha256(request) == canonical_sha256(json.loads(before))


@pytest.mark.parametrize('provider', ['colabfold_api', 'neurosnap_api'])
def test_confor_materializer_to_actual_compiler(tmp_path, monkeypatch, provider):
    spec = importlib.util.spec_from_file_location('cm_routing_fixture', Path(__file__).with_name('test_conformational_mapping_routing.py'))
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    controls = fixture._request_params('confornets')
    controls['feature_policy']['msa_settings'] = settings(provider)
    root = Path(os.environ['BMS_INPUTS']) / tmp_path.name
    materialized = materialize_trusted_internal_request(controls, output_dir=root,
        request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture-operator')
    original = materialized.request_path.read_bytes()
    assert json.loads(original)['feature_policy']['msa_settings'] == settings(provider)
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    invocation = nextflow.compile_nextflow_invocation('conformational_mapping', 'map', dict(materialized.launch_params, gpu_id=0), str(tmp_path/'out'), 'cm-confor-parity', source_identity=SourceIdentity('a'*40,'b'*40))
    assert all(invocation.native_parameters[k] == v for k,v in apply_msa_policy('protenix', settings(provider)).items())
    assert original == materialized.request_path.read_bytes()
    assert json.loads(Path(invocation.native_parameters['cm_request_path']).read_bytes())['confornets']['skip_msa'] == controls['confornets']['skip_msa']


@pytest.mark.parametrize('backend', ['protenix_v2_ensemble', 'confornets'])
@pytest.mark.parametrize('field', ['msa_neurosnap_pad_sequences', 'msa_neurosnap_force_uppercase'])
def test_inactive_consumer_preserves_saved_settings_but_selected_science_rejects(tmp_path, monkeypatch, backend, field):
    from services.conformational_mapping.request_builder import validate_request_params
    spec = importlib.util.spec_from_file_location('cm_inactive_fixture', Path(__file__).with_name('test_conformational_mapping_routing.py'))
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    controls = fixture._request_params(backend)
    controls['feature_policy']['msa_settings'] = {**settings('neurosnap_api'), field: True}
    if backend == 'protenix_v2_ensemble':
        controls['feature_policy'].update(protein_msa_enabled=False, templates_enabled=False, rna_msa_enabled=False, mode='features_disabled_control_v1')
    else:
        controls['confornets']['skip_msa'] = True
    original = copy.deepcopy(controls)
    validate_request_params(controls)
    materialized = materialize_trusted_internal_request(controls, output_dir=Path(os.environ['BMS_INPUTS']) / tmp_path.name,
        request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture-operator')
    request = json.loads(materialized.request_path.read_bytes())
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    invocation = nextflow.compile_nextflow_invocation('conformational_mapping', 'map', dict(materialized.launch_params, gpu_id=0), str(tmp_path/'out'), 'cm-inactive-parity', source_identity=SourceIdentity('a'*40,'b'*40))
    assert invocation.native_parameters[field] is True
    assert controls == original and request['feature_policy'] == original['feature_policy']
    if backend == 'protenix_v2_ensemble':
        assert invocation.native_parameters['protenix_use_msa'] is False
        controls['feature_policy'].update(protein_msa_enabled=True, mode='regenerate_mutated_protein_v1')
    else:
        controls['confornets']['skip_msa'] = False
    with pytest.raises(ValueError, match='unsupported for Protenix A3M consumption'):
        validate_request_params(controls)
    with pytest.raises(ValueError, match='unsupported for Protenix A3M consumption'):
        canonical_msa_params(controls)


@pytest.mark.parametrize('backend', ['protenix_v2_ensemble', 'confornets'])
@pytest.mark.parametrize('invalid,message', [
    ({'colabfold_use_templates': True}, 'template retrieval is not supported'),
    ({'colabfold_pairing_mode': 'paired', 'colabfold_use_filter': False}, 'unfiltered paired search'),
])
def test_selected_provider_capability_is_pure_prequeue_not_deferred_to_worker(tmp_path, monkeypatch, backend, invalid, message):
    import biomodstack_msa_api as msa_api
    from services.conformational_mapping.request_builder import validate_request_params
    original_validate = msa_api.validate_settings
    calls = []
    def observed(provider, values):
        calls.append((provider, values))
        return original_validate(provider, values)
    monkeypatch.setattr(msa_api, 'validate_settings', observed)
    spec = importlib.util.spec_from_file_location('cm_capability_fixture', Path(__file__).with_name('test_conformational_mapping_routing.py'))
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    controls = fixture._request_params(backend)
    controls['feature_policy']['msa_settings'] = {**settings('colabfold_api'), **invalid}
    if backend == 'protenix_v2_ensemble':
        controls['feature_policy'].update(protein_msa_enabled=True, rna_msa_enabled=False)
    else:
        controls['confornets']['skip_msa'] = False
    before = copy.deepcopy(controls)
    with pytest.raises(ValueError, match=message):
        validate_request_params(controls)
    with pytest.raises(ValueError, match=message):
        canonical_msa_params(controls)
    assert controls == before and calls
    # The same unsupported inactive provider choice must not overwrite or block
    # an explicitly selected different provider.
    controls['feature_policy']['msa_settings']['msa_provider'] = 'neurosnap_api'
    validate_request_params(controls)
    assert canonical_msa_params(controls)['msa_provider'] == 'neurosnap_api'
    assert controls['feature_policy']['msa_settings']['colabfold_use_filter'] is False


@pytest.mark.asyncio
async def test_actual_cm_sqlite_fresh_session_and_clean_retry_keep_settings(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import Base, Job
    from services.conformational_mapping.persistence import register_prepared_request, get_request
    from routers import conformational_mapping as router
    spec = importlib.util.spec_from_file_location('cm_persistence_fixture', Path(__file__).with_name('test_conformational_mapping_backend_complete.py'))
    assert spec and spec.loader
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    controls = fixture._recovery_request_params()
    controls['feature_policy'].update(rna_msa_enabled=False, msa_settings=settings('neurosnap_api'))
    root = tmp_path / 'request'
    materialized = materialize_trusted_internal_request(controls, output_dir=root,
        request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture-operator')
    request = json.loads(materialized.request_path.read_bytes())
    plan = json.loads(materialized.coordinate_plan_path.read_bytes())
    # Real immutable recovery boundary requires its actual owned sidecars.
    (root / 'cm_runtime_registry_v1.json').write_text('{"schema_name":"cm_runtime_registry","schema_version":1}')
    snapshots = json.loads((Path(__file__).parent / 'fixtures/conformational_mapping/schemas/positive/all_schemas.json').read_text())
    (root / 'cm_complex_snapshots_v1.json').write_text(json.dumps([snapshots['cm_complex_snapshot_v1']]))
    authority = router._build_retry_authority(root)
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "parity.db"}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            job = Job(id=request['request_id'], name='CM parity persistence', model_id='conformational_mapping', mode='map', params=materialized.launch_params, status='queued', output_dir=str(root))
            record = await register_prepared_request(session, job=job, principal_id='fixture-operator', request=request,
                coordinate_plan=plan, resume_key='0'*64, capability_sha256='a'*64)
            await session.commit()
            assert record.request_json['feature_policy'] == controls['feature_policy']
        # Dispose ensures a new physical database connection, not ORM read cache.
        await engine.dispose()
        async with sessions() as session:
            record = await get_request(session, request['request_id'])
            assert record is not None
            assert record.request_json == request
            assert record.coordinate_plan_json == plan
            attempt = tmp_path / 'retry'
            router._copy_clean_retry_authority(source_root=root, attempt_root=attempt,
                request_payload=record.request_json, coordinate_plan=record.coordinate_plan_json, persisted_authority=authority)
            assert json.loads((attempt / 'cm_request_v1.json').read_bytes()) == request
            assert json.loads((attempt / 'cm_request_v1.json').read_bytes())['feature_policy']['msa_settings'] == settings('neurosnap_api')
    finally:
        await engine.dispose()
