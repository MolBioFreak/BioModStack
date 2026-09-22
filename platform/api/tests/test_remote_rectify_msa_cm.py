"""CM canonical producer -> declared service -> actual native input boundary.

Only HTTP/transfer and scientific runtime imports are doubles. No GPU inference.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from component_runtime import SourceIdentity
from services import nextflow, model_msa_handoff as handoff
from services.conformational_mapping.contracts import validate_feature_policy
from services.conformational_mapping.request_builder import canonical_msa_params
from services.msa_policy import apply_msa_policy

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('cm_roster_fixture', Path(__file__).with_name('test_remote_rectify_msa_roster.py'))
roster = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roster)
provider = roster.provider


@pytest.fixture
def cm(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    cases = json.loads((ROOT / 'platform/api/tests/fixtures/conformational_mapping/phase_0_vectors/complex_cases.json').read_text())
    snapshot = copy.deepcopy([c for c in cases['cases'] if c['kind'] == 'positive'][-1])
    snapshot.update(target_id='cm-native-target', target_order=0, unsupported_fields=[])
    snapshot.setdefault('admission', dict(token_count=1, atom_count=1, token_limit=4096, conversion_omissions=[]))
    request = dict(request_id='cm-native-roster', request_sha256='a'*64,
        backend='protenix_v2_ensemble', targets=[{'target_id': snapshot['target_id']}],
        ordered_seeds=[7, 9], samples_per_seed=2, runtime_policy={'use_default_params': True},
        feature_policy=dict(mode='regenerate_mutated_protein_v1', protein_msa_enabled=True,
            templates_enabled=True, rna_msa_enabled=False,
            msa_settings={'msa_provider': 'colabfold_api', 'colabfold_use_env': False}))
    managed = Path(os.environ['BMS_INPUTS']) / tmp_path.name
    managed.mkdir()
    path = managed / 'cm_request_v1.json'
    path.write_text(json.dumps(request))
    snapshots = managed / 'cm_complex_snapshots_v1.json'
    snapshots.write_text(json.dumps([snapshot]))
    return request, path, snapshots


def compile_cm(tmp_path, path, **extra):
    return nextflow.compile_nextflow_invocation('conformational_mapping', 'map',
        dict(cm_request_path=str(path), gpu_id=0, run_frustrampnn=True, **extra),
        str(tmp_path / 'results'), 'cm-job', source_identity=SourceIdentity('a'*40, 'b'*40))


def test_cm_actual_native_portable_delivery(cm, tmp_path, monkeypatch, provider):
    request, path, snapshots = cm
    invocation = compile_cm(tmp_path, path)
    plan = invocation.execution_plan
    original = path.read_bytes(), snapshots.read_bytes()
    effective = json.loads(invocation.effective_json)
    expected = apply_msa_policy('protenix', request['feature_policy']['msa_settings'])
    assert all(effective[k] == v for k, v in expected.items())
    assert 'msa_provider' not in json.loads(invocation.requested_json)
    assert effective['protenix_use_template'] is True
    service = next(s for s in plan.metadata.external_services if s.logical_id == 'protenix:generated_msa')
    assert handoff.generated_msa_service_supported(service)
    assert any(d.relative_path == 'scripts/prepare_protenix_msa.py' for d in plan.metadata.dependencies)
    params = json.loads(plan.native_parameters_json)
    params.update(code_root=str(ROOT), api_python=sys.executable, container_dir=str(tmp_path / 'containers'))
    # Render the actual process closure; stop before workflow declaration only.
    source = (ROOT / 'modules/conformational_mapping_protenix.nf').read_text()
    body = source.split('process CanonicalProtenixEnsemble {', 1)[1].split('\nworkflow ', 1)[0]
    body = body.split('    script:\n', 1)[1].rstrip()
    groovy = tmp_path / 'cm.groovy'
    bindings = dict(params=params, preflight=str(tmp_path / 'preflight'), runtime_image='fixture-image')
    groovy.write_text('\n'.join(f'def {k} = new groovy.json.JsonSlurper().parseText({json.dumps(json.dumps(v))})' for k,v in bindings.items()) + '\ndef render = {\n' + body[:-1] + '\n}\nprint render()\n')
    rendered = subprocess.run(['java', '-cp', os.environ['BMS_TEST_NEXTFLOW_JAR'], 'groovy.ui.GroovyMain', str(groovy)], capture_output=True, text=True, timeout=30)
    assert rendered.returncode == 0, rendered.stderr
    script = rendered.stdout
    env = dict(os.environ, REQUEST=str(path), SNAPSHOTS=str(snapshots))
    produced = subprocess.run(['bash', '-euo', 'pipefail', '-c', roster.native_command(script, 'prepare_protenix_conformational_mapping.py')], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert produced.returncode == 0, produced.stderr
    native = tmp_path / 'prepared_protenix/protenix_input.json'
    original_native = native.read_bytes()
    payload = json.loads(original_native)
    from prepare_protenix_msa import iter_protein_chains
    sequences = list(dict.fromkeys(c['sequence'] for _, _, c in iter_protein_chains(payload)))
    provider[0].responses = provider[1].cf_success(sequences=sequences, use_env=False)
    output = tmp_path / 'worker/artifacts'
    output.mkdir(parents=True)
    value = dict(ledger_path=str(tmp_path / 'worker/ledger.sqlite'), artifact_root=str(output),
        attempt_id='attempt', root_job_id='cm-job', target_id='remote-target', lease_id='lease',
        plan_sha256=plan.plan_sha256, source_identity=plan.to_dict()['source_identity'],
        execution_plan=plan.to_dict(), working_directory=str(ROOT))
    context = tmp_path / 'worker/context.json'
    context.write_text(json.dumps(value))
    owner = roster.r03.runtime((value, context))
    # Execute the native conditional, hydration, and exact COMMAND_ARGS recorder.
    block = script[script.index('    PROTENIX_INPUT='):script.index('    STARTED_AT=')]
    env.update(BMS_COMPONENT_CONTEXT=str(context), USE_RNA_MSA='false', USE_MSA='true',
        USE_TEMPLATE='true', USE_DEFAULT='true', SEEDS='7,9', SAMPLES='2')
    process = subprocess.Popen(['bash', '-euo', 'pipefail', '-c', 'EXTRA=();\n' + block],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        pending = roster.r03.pending(owner, process)
        assert pending['native_input'] == payload
        package = tmp_path / 'controller/package'
        sha = handoff.prepare_generated_msa(pending, package)
        roster.r03.deliver(owner, pending, package, sha)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0, out + err
        argv = json.loads((tmp_path / 'native_protenix/runtime/command.json').read_text())
        consumed = tmp_path / argv[argv.index('--input') + 1]
        assert consumed == tmp_path / 'native_protenix/runtime/msa-input.json'
        hydrated = json.loads(consumed.read_text())
        for _, _, chain in iter_protein_chains(hydrated):
            alignment = Path(chain.pop('unpairedMsaPath'))
            assert alignment.is_relative_to(output) and alignment.is_file()
        assert hydrated == payload
        assert native.read_bytes() == original_native
        assert original == (path.read_bytes(), snapshots.read_bytes())
        assert argv[argv.index('--use_template') + 1] == 'true'
        assert argv[argv.index('--use_rna_msa') + 1] == 'false'
        assert argv[argv.index('--seeds') + 1] == '7,9'
        # Exercise actual wrapper up to upstream preprocess, with explicit
        # scientific-runtime doubles. Stop there, not a fabricated prediction.
        class ReachedPreprocess(Exception): pass
        observed = {}
        def preprocess(filename, **kwargs):
            observed.update(filename=filename, **kwargs)
            assert json.loads(Path(filename).read_text()) == json.loads(consumed.read_text())
            raise ReachedPreprocess
        runner = types.SimpleNamespace(configs=types.SimpleNamespace(sorted_by_ranking_score=False), init_dumper=lambda **kw: None)
        for name, fields in {
            'torch': {'cuda': types.SimpleNamespace(is_available=lambda: False)},
            'configs.configs_inference': {'inference_configs': {}},
            'runner.batch_inference': {'get_default_runner': lambda **kw: runner, 'preprocess_input': preprocess},
            'runner.inference': {'infer_predict': lambda *args: pytest.fail('inference not authorized')},
        }.items():
            mod = types.ModuleType(name)
            mod.__dict__.update(fields)
            monkeypatch.setitem(sys.modules, name, mod)
        spec = importlib.util.spec_from_file_location('cm_real_wrapper', ROOT / 'scripts/run_protenix_inference.py')
        wrapper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wrapper)
        # Existing ledger installation is separately qualified by workflow tests;
        # it needs the GPU runner's dumper. No argv flags are removed here.
        monkeypatch.setattr(wrapper, '_install_coordinate_ledger', lambda *args: None)
        monkeypatch.setattr(sys, 'argv', argv)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ReachedPreprocess):
            wrapper.main()
        assert observed['filename'] == str(consumed)
        assert observed['use_msa'] is True and observed['use_template'] is True
        assert observed['use_rna_msa'] is False
        replay = subprocess.run(['bash', '-euo', 'pipefail', '-c', 'EXTRA=();\n' + block], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
        assert replay.returncode == 0, replay.stderr
        handoff.prepare_generated_msa(pending, tmp_path / 'controller/replay')
        assert len(provider[0].calls) == 3
        disabled = subprocess.run(['bash', '-euo', 'pipefail', '-c', 'EXTRA=();\n' + block],
            cwd=tmp_path, env=dict(env, USE_MSA='false'), capture_output=True, text=True, timeout=15)
        assert disabled.returncode == 0, disabled.stderr
        disabled_argv = json.loads((tmp_path / 'native_protenix/runtime/command.json').read_text())
        assert disabled_argv[disabled_argv.index('--input') + 1] == 'prepared_protenix/protenix_input.json'
        assert disabled_argv[disabled_argv.index('--use_msa') + 1] == 'false'
        assert len(provider[0].calls) == 3
    finally:
        if process.poll() is None: process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize('settings', [
    None, {'msa_provider': 'local'}, {'unexpected': True}, {'colabfold_use_env': 'false'},
    {'msa_allow_empty_fallback': True}, {'msa_neurosnap_coverage_percent': -1},
])
def test_cm_settings_reuse_closed_global_validation(cm, settings):
    request, _, _ = cm
    policy = dict(request['feature_policy'], msa_settings=settings)
    with pytest.raises(ValueError): validate_feature_policy(policy)


def test_cm_canonical_materializer_preserves_requested_settings(tmp_path, monkeypatch):
    from services.conformational_mapping.request_builder import materialize_trusted_internal_request
    from services.conformational_mapping.contracts import validate_schema
    spec = importlib.util.spec_from_file_location('cm_canonical_request_fixture',
        Path(__file__).with_name('test_conformational_mapping_backend_complete.py'))
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    controls = fixture._recovery_request_params()
    controls['feature_policy'].update(rna_msa_enabled=False,
        msa_settings={'msa_provider': 'colabfold_api', 'colabfold_use_env': False})
    original = copy.deepcopy(controls)
    root = Path(os.environ['BMS_INPUTS']) / 'materialized-cm'
    materialized = materialize_trusted_internal_request(controls, output_dir=root,
        request_id='9d0a1104-01bf-4a12-8e2e-5d9a2baec3cb', principal_id='fixture-operator')
    request = json.loads(materialized.request_path.read_bytes())
    validate_schema('cm_request_v1', request)
    assert controls == original
    assert request['feature_policy'] == controls['feature_policy']
    original_bytes = materialized.request_path.read_bytes()
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    invocation = compile_cm(tmp_path, materialized.request_path)
    assert invocation.native_parameters['colabfold_use_env'] is False
    assert invocation.native_parameters['msa_provider'] == 'colabfold_api'
    assert invocation.native_parameters['run_frustrampnn'] is True
    assert materialized.request_path.read_bytes() == original_bytes
    assert json.loads(invocation.requested_json) == dict(materialized.launch_params, gpu_id=0)


@pytest.mark.parametrize('field,value', [('protein_msa_enabled', None), ('rna_msa_enabled', 'false'),
                                        ('templates_enabled', 0)])
def test_cm_missing_or_coerced_controls_never_disable_features(cm, tmp_path, field, value):
    request, path, _ = cm
    request['feature_policy'][field] = value
    path.write_text(json.dumps(request))
    with pytest.raises(ValueError, match='explicit protein-MSA|valid boolean'):
        compile_cm(tmp_path, path)


def test_cm_rna_and_disabled_and_selected_settings(cm, tmp_path):
    request, path, _ = cm
    request['feature_policy']['rna_msa_enabled'] = True
    path.write_text(json.dumps(request))
    with pytest.raises(ValueError, match='RNA-MSA is unsupported'):
        compile_cm(tmp_path, path)
    request['feature_policy'].update(protein_msa_enabled=False, rna_msa_enabled=False)
    path.write_text(json.dumps(request))
    invocation = compile_cm(tmp_path, path)
    assert not any(handoff.generated_msa_service_supported(s) for s in invocation.execution_plan.metadata.external_services)
    assert json.loads(invocation.effective_json)['protenix_use_template'] is True
    request['feature_policy'].update(protein_msa_enabled=True,
        msa_settings={'msa_provider': 'neurosnap_api', 'msa_neurosnap_coverage_percent': 40})
    path.write_text(json.dumps(request))
    selected = compile_cm(tmp_path, path)
    effective = json.loads(selected.effective_json)
    assert effective['msa_provider'] == 'neurosnap_api'
    assert effective['msa_neurosnap_coverage_percent'] == 40
    assert selected.execution_plan.plan_sha256 != invocation.execution_plan.plan_sha256
    assert canonical_msa_params(request)['protenix_use_template'] is True
