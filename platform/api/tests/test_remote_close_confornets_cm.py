"""Real canonical request/compiler/service -> pinned native consumer.

Only selected runtime asset bytes, source archive, HTTP/transfer, and heavy
DataModule/Torch storage are doubles. No GPU/provider/live runtime is invoked.
"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest
from services import nextflow, model_msa_handoff as handoff
from services.conformational_mapping.request_builder import materialize_trusted_internal_request
from tests.test_remote_rectify_inputs import offline_bundle, placement, compile_installed
from tests.test_remote_close_confornets import upstream_namespace, adapter, r03, provider, ROOT


def test_canonical_selected_service_and_native_features(placement, tmp_path, monkeypatch, provider):
    spec = importlib.util.spec_from_file_location('confor_cm_fixture', Path(__file__).with_name('test_conformational_mapping_routing.py'))
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    roots, job, target = placement
    controls = fixture._request_params('confornets')
    controls['confornets']['skip_msa'] = False
    controls['confornets']['backend_identity']['backend_commit'] = '4df561a1fbd0fd2b9c7a230fa62957a837d9f72d'
    controls['feature_policy']['msa_settings'] = dict(msa_provider='colabfold_api', colabfold_use_env=False)
    root = roots['results']/'canonical'; root.mkdir()
    checkpoint = root/'checkpoint.pt'; checkpoint.write_bytes(b'explicit offline checkpoint transport double')
    controls['confornets']['checkpoint'] = dict(path='checkpoint.pt', sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest())
    monkeypatch.setenv('BMS_RESULTS_ROOT', str(roots['results']))
    materialized = materialize_trusted_internal_request(controls, output_dir=root,
        request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='offline-operator')
    original = materialized.request_path.read_bytes()
    job.params = dict(materialized.launch_params, gpu_id=0, run_frustrampnn=True)
    invocation = compile_installed(placement, 'conformational_mapping', 'map')
    plan = invocation.execution_plan
    service = next(s for s in plan.metadata.external_services if s.logical_id == 'protenix:generated_msa')
    assert handoff.generated_msa_service_supported(service)
    assert service.provider == 'colabfold_api' and json.loads(service.settings_json)['colabfold_use_env'] is False
    # Shared CM result-contract recognition blocks whole-root bundle admission
    # in the base revision (see task evidence run07); never forge a complete plan.
    # Exercise the actual selected MSA service without weakening that guard.
    assert plan.dependency_closure_complete
    # Real canonical producer consumes the materialized immutable coordinate plan.
    native_request, assets = tmp_path/'native-request.json', tmp_path/'native-assets'
    spec = importlib.util.spec_from_file_location('confor_cm_prep', ROOT/'scripts/prep_canonical_confornets_request.py')
    prep = importlib.util.module_from_spec(spec); spec.loader.exec_module(prep)
    # Explicit installed-image presence double; all request ownership, file/hash,
    # coordinate-plan and runtime-registry checks remain real.
    monkeypatch.setattr(prep, '_instrumented_confornets_runtime_available', lambda: True)
    (root/'cm_runtime_registry_v1.json').write_text(json.dumps(controls['confornets']['backend_identity']))
    prep.prepare(materialized.request_path, materialized.coordinate_plan_path, assets, native_request)
    request = json.loads(native_request.read_text())
    assert request['canonical_binding'] and request['params']['skip_msa'] is False
    assert request['params']['num_samples'] == controls['confornets']['samples']
    assert request['params']['num_runs'] == controls['confornets']['runs']
    original_native = native_request.read_bytes()
    provider[0].responses = provider[1].cf_success(sequences=[request['sequence']], use_env=False)
    artifact_root = tmp_path/'worker/artifacts'; artifact_root.mkdir(parents=True)
    value = dict(ledger_path=str(tmp_path/'worker/ledger.sqlite'), artifact_root=str(artifact_root),
        attempt_id='attempt', root_job_id=str(job.id), target_id='remote-target', lease_id='lease',
        plan_sha256=plan.plan_sha256, source_identity=plan.to_dict()['source_identity'],
        execution_plan=plan.to_dict(), working_directory=str(ROOT))
    context = tmp_path/'worker/context.json'; context.write_text(json.dumps(value))
    owner = r03.runtime((value, context))
    out_assets = tmp_path/'prepared-assets'
    argv = [sys.executable, str(ROOT/'scripts/prepare_confornets_msa.py'), '--request', str(native_request),
        '--assets-dir', str(assets), '--output-dir', str(out_assets)]
    env = dict(os.environ, BMS_COMPONENT_CONTEXT=str(context))
    process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        pending = r03.pending(owner, process)
        assert pending['native_input'] == adapter.native_roster(request, assets)[1]
        package = tmp_path/'controller/package'
        sha = handoff.prepare_generated_msa(pending, package)
        r03.deliver(owner, pending, package, sha)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0, out+err
        ns = upstream_namespace(); observed = []
        class DataModule:
            def __init__(self, config): self.config = config
            def setup(self):
                queries = self.config.datasets[0].config.query_set.queries
                for query in queries.values():
                    assert query.use_msas
                    chain = query.chains[0]
                    alignment = ns['parse_a3m'](chain.main_msa_file_paths[0].read_text())
                    assert ''.join(alignment.msa[0]) == request['sequence']
                    observed.append(alignment)
            def predict_dataloader(self): return [{'query_id': [request['query_id']]}]
        ns.update(DataModule=DataModule, DataModuleConfig=types.SimpleNamespace,
            InferenceDatasetSpec=types.SimpleNamespace, InferenceJobConfig=types.SimpleNamespace,
            MSASettings=types.SimpleNamespace, TemplateSettings=types.SimpleNamespace,
            TemplatePreprocessorSettings=types.SimpleNamespace, tqdm=lambda rows, **kw: rows,
            torch=types.SimpleNamespace(save=lambda batch,path: Path(path).write_text('explicit storage double')))
        ns['run_msa_batched'] = lambda **kw: pytest.fail('uncontrolled provider')
        from tests.test_remote_close_confornets import driver
        relocated = json.loads(json.dumps(request))
        relocated['params']['confornets_repo_path'] = str(tmp_path)  # runtime filesystem double
        _, command = driver._build_preprocess_command(relocated, out_assets, prepared_msa=True)
        monkeypatch.setattr(sys, 'argv', command[1:])
        ns['main']()
        assert observed and native_request.read_bytes() == original_native
        assert materialized.request_path.read_bytes() == original
        assert json.loads((out_assets/request['benchmark']/'query.json').read_text())['seeds'] == [request['params'].get('seed',42)]
        replay = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=15)
        assert replay.returncode == 0, replay.stderr
    finally:
        if process.poll() is None: process.kill()
        process.communicate(timeout=5)
