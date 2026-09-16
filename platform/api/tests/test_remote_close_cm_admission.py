"""Canonical CM root contract; runtime assets/source archive are offline doubles."""
import json
import pytest
from services.result_contracts import resolve_result_contract
from services.conformational_mapping.request_builder import materialize_trusted_internal_request
from tests.test_remote_rectify_inputs import offline_bundle, placement, compile_installed, pack
from tests.test_conformational_mapping_routing import _request_params


def test_cm_root_is_native_aggregate_not_candidate():
    root = resolve_result_contract(model_type='conformational_mapping', stage_mode='map')
    assert root.analysis_contract_id == 'conformational_mapping_ensemble_v1'
    assert root.required_artifacts == ['ensemble_manifest', 'native_manifest']
    for backend in ['protenix', 'confornets', 'import']:
        candidate = resolve_result_contract(model_type='conformational_mapping', stage_mode='map', result_set=f'cm_{backend}_ensemble')
        assert candidate.analysis_contract_id == f'conformational_mapping_{backend}_v1'
    for kw in [dict(model_type='conformational_mapping'), dict(model_type='conformational_mapping', stage_mode='predict'), dict(model_type='unknown', stage_mode='map'), dict(model_type='conformational_mapping', stage_mode='map', result_set='unknown'), dict(model_type='conformational_mapping', stage_mode='map', artifact_class='monomer_conformation')]:
        assert resolve_result_contract(**kw).analysis_contract_id is None


@pytest.mark.parametrize("backend", ["confornets", "protenix_v2_ensemble"])
def test_canonical_root_preview_and_bundle(placement, monkeypatch, backend):
    roots, job, target = placement
    controls = _request_params('confornets')
    checkpoint = roots['inputs'] / 'checkpoint.pt'
    checkpoint.write_bytes(b'explicit offline checkpoint transport double')
    import hashlib
    controls['confornets']['checkpoint'] = dict(path=str(checkpoint), sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest())
    controls['confornets']['backend_identity']['repo_path'] = str(roots['inputs'])
    controls['confornets']['skip_msa'] = False
    controls['confornets']['backend_identity']['backend_commit'] = '4df561a1fbd0fd2b9c7a230fa62957a837d9f72d'
    controls['feature_policy']['msa_settings'] = dict(msa_provider='colabfold_api', colabfold_use_env=False)
    if backend == 'protenix_v2_ensemble':
        controls = _request_params(backend)
        controls['feature_policy'].update(rna_msa_enabled=False, msa_settings=dict(msa_provider='colabfold_api', colabfold_use_env=False))
    materialized = materialize_trusted_internal_request(controls, output_dir=roots['results']/'canonical', request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='offline-operator')
    (materialized.request_path.parent / "cm_runtime_registry_v1.json").write_text(json.dumps({"schema_name":"cm_runtime_registry", "schema_version":1}))
    before = materialized.request_path.read_bytes()
    job.params = dict(materialized.launch_params, gpu_id=0, run_frustrampnn=True)
    invocation = compile_installed(placement, 'conformational_mapping', 'map')
    assert invocation.execution_plan.complete, invocation.execution_plan.blockers
    if backend == "confornets":
        installed = roots["weights"] / "openfold3" / "of3-p2-155k.pt"
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(checkpoint.read_bytes())
    from schemas import JobCreate
    from routers.jobs import _execution_plan_preview
    preview = _execution_plan_preview(JobCreate(name="CM admission", model_id="conformational_mapping", mode="map", params=dict(job.params)))
    assert preview["admissible"], preview["blockers"]
    assert materialized.request_path.read_bytes() == before
    # The scientific image is not installed in this route-free fixture. Use
    # its explicitly declared transport bytes, never a live runtime or digest.
    from services.remote_execution import bundle
    real_resolve = bundle.resolve_image
    monkeypatch.setattr(bundle, 'resolve_image', lambda name, root, params:
        root / name if name == 'frustrampnn.sif' else real_resolve(name, root, params))
    result = pack(placement, invocation)
    assert result.envelope is not None
    assert materialized.request_path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["confornets", "protenix_v2_ensemble"])
async def test_actual_cm_operator_route_stored_job_bundle(placement, monkeypatch, backend):
    from pathlib import Path
    from fastapi import Request, Response
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from database import Base, Job, ConformationalMappingSource
    from routers import conformational_mapping as cm
    from services.remote_execution import bundle
    from services.conformational_mapping.contracts import canonical_json_bytes
    from biomodstack_msa_handoff import digest
    roots, _, target = placement
    monkeypatch.setenv("BMS_CM_AUTHORIZATION_ENABLED", "0")
    monkeypatch.setattr(cm, "get_results_dir", lambda: roots['results'])
    checkpoint = roots['weights'] / 'openfold3/of3-p2-155k.pt'
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_bytes(b'explicit offline checkpoint transport fixture')
    # Runtime image registry is an explicit offline boundary double, not science.
    identity = _request_params('confornets')['confornets']['backend_identity']
    identity['backend_commit'] = '4df561a1fbd0fd2b9c7a230fa62957a837d9f72d'
    monkeypatch.setattr(cm, '_server_confornets_identity', lambda: dict(identity))
    monkeypatch.setattr(cm, '_runtime_registry', lambda _: {'schema_name':'cm_runtime_registry', 'schema_version':1, 'offline_fixture':True})
    real_resolve = bundle.resolve_image
    monkeypatch.setattr(bundle, 'resolve_image', lambda name, root, params: root / name if name == 'frustrampnn.sif' else real_resolve(name, root, params))
    controls = _request_params(backend)
    controls['feature_policy'].update(rna_msa_enabled=False, msa_settings=dict(msa_provider='colabfold_api', colabfold_use_env=False))
    if backend == 'protenix_v2_ensemble':
        fixture = json.loads((Path(__file__).parent / 'fixtures/conformational_mapping/schemas/positive/all_schemas.json').read_text())
        payload = canonical_json_bytes([fixture['cm_complex_snapshot_v1']])
        kind, metadata = 'complex_snapshot', {}
        fields = dict(registered_snapshot_id='source')
    else:
        sequence = controls['confornets']['sequence']
        payload = sequence.encode()
        kind, metadata = 'protein_sequence', {'sequence':sequence, 'target_id':'target-a'}
        forbidden = {'sequence','chain_id','test_case_id','benchmark_name','checkpoint','config','references','transfer_source','backend_identity'}
        settings = {k:v for k,v in controls['confornets'].items() if k not in forbidden}
        settings['skip_msa'] = False
        fields = dict(registered_sequence_id='source', registered_checkpoint_id='cm_src_server_confornets_checkpoint_'+digest(checkpoint.read_bytes())[:32], confornets=settings)
    source_path = roots['inputs'] / 'source.json'
    source_path.write_bytes(payload)
    engine = create_async_engine('sqlite+aiosqlite:///'+str(roots['data']/'cm-route.db'))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        session.add(ConformationalMappingSource(source_id='source', principal_id='local-personal-workflow', source_kind=kind, storage_root=str(roots['inputs']), relative_path=source_path.name, content_sha256=digest(payload), size_bytes=len(payload), metadata_json=metadata, immutable=True))
        await session.commit()
        body = cm.SubmitRequest(name='CM actual operator route', backend=backend, ordered_seeds=controls['ordered_seeds'], samples_per_seed=controls['samples_per_seed'], feature_policy=controls['feature_policy'], runtime_policy=controls['runtime_policy'], analysis_policy=controls['analysis_policy'], **fields)
        request = Request({'type':'http','method':'POST','scheme':'http','path':'/api/conformational-mapping/requests','headers':[], 'client':('testclient',50000), 'server':('testserver',80)})
        reply = await cm.submit_request(body, request, Response(), session)
        job = await session.get(Job, reply['job_id'])
        assert job.provenance['cm_request_sha256'] == reply['request_sha256']
        documents = {p:p.read_bytes() for p in Path(job.output_dir).rglob('*') if p.is_file()}
        # Apply the fixture's dispatcher-owned source/target binding; native
        # submission deliberately does not select execution source at creation.
        for key in ('assigned_gpu', 'execution_source_revision', 'execution_source_tree', 'execution_target_id', 'execution_target_kind', 'remote_attempt_id'):
            if hasattr(placement[1], key):
                setattr(job, key, getattr(placement[1], key))
        job.provenance = dict(job.provenance, remote_execution_assignment=dict(placement[1].provenance['remote_execution_assignment']))
        job.params = dict(job.params, gpu_id=0)
        invocation = compile_installed((roots,job,target), 'conformational_mapping','map')
        checkpoint.write_bytes(b'explicit offline checkpoint transport fixture')
        from routers.jobs import _execution_plan_preview, normalize_job_request
        from schemas import JobCreate
        typed = JobCreate(name=job.name,model_id=job.model_id,mode=job.mode,params={k:v for k,v in job.params.items() if k != 'remote_result_policy'}, execution_policy={'remote_result_policy':job.params['remote_result_policy']})
        normalized = normalize_job_request(typed)
        assert normalized.params == typed.params
        preview = _execution_plan_preview(typed)
        assert preview['admissible'], preview['blockers']
        result = pack((roots,job,target),invocation)
        assert result.envelope is not None
        assert all(p.read_bytes()==raw for p,raw in documents.items())
        if backend == 'confornets':
            checkpoint.write_bytes(b'changed selected runtime source')
            with pytest.raises(ValueError, match='selected managed runtime source'):
                _execution_plan_preview(typed)
            with pytest.raises(bundle.RemoteBundleError, match='selected dependency'):
                pack((roots,job,target),invocation)
            checkpoint.write_bytes(b'explicit offline checkpoint transport fixture')
            request_document = json.loads(Path(job.params['cm_request_path']).read_bytes())
            snapshot = Path(job.params['cm_request_path']).parent / request_document['confornets']['checkpoint']['path']
            snapshot.write_bytes(b'changed request-owned checkpoint')
            with pytest.raises(ValueError, match='selected managed runtime source'):
                _execution_plan_preview(typed)
            with pytest.raises(bundle.RemoteBundleError, match='declared checkpoint digest'):
                pack((roots,job,target),invocation)
    await engine.dispose()
