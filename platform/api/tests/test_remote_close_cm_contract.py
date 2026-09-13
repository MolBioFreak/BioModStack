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


def test_canonical_confornets_root_compiles(placement):
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
    materialized = materialize_trusted_internal_request(controls, output_dir=roots['inputs']/'canonical', request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='offline-operator')
    before = materialized.request_path.read_bytes()
    job.params = dict(materialized.launch_params, gpu_id=0, run_frustrampnn=True)
    invocation = compile_installed(placement, 'conformational_mapping', 'map')
    assert invocation.execution_plan.complete, invocation.execution_plan.blockers
    assert materialized.request_path.read_bytes() == before
