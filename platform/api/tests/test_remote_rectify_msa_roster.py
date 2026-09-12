"""R03 PLR native producer/consumer boundary; no GPU or provider network.

Render the unmodified native Groovy script closures using the pinned Nextflow
JAR, then execute their actual producer/conditioning/MSA commands. Inference,
container setup and publication are outside this test's acceptance boundary.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from component_runtime import SourceIdentity, canonical_bytes
from services import nextflow
from services import model_msa_handoff as handoff

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location('r03_roster_fixtures', Path(__file__).with_name('test_remote_rectify_generated_msa.py'))
r03 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r03)
provider = r03.provider


def render(tmp_path, module, process, bindings, helpers=''):
    text = (ROOT / module).read_text().split('process ' + process + ' {', 1)[1]
    text = text.split('\nprocess ', 1)[0]
    body = text.split('    script:\n', 1)[1].rstrip()
    assert body.endswith('}')
    script = tmp_path / (process + '.groovy')
    script.write_text(helpers + '\n' + '\n'.join(
        f'def {key} = new groovy.json.JsonSlurper().parseText({json.dumps(json.dumps(value))})'
        for key, value in bindings.items()) + '\ndef render = {\n' + body[:-1] + '\n}\nprint render()\n')
    result = subprocess.run(['java', '-cp', os.environ['BMS_TEST_NEXTFLOW_JAR'],
        'groovy.ui.GroovyMain', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def native_command(script, name):
    # Select the original rendered shell command, including all native flags.
    lines = script.splitlines()
    index = next(i for i, line in enumerate(lines) if '/scripts/' + name in line)
    command = []
    while True:
        line = lines[index].strip()
        command.append(line.removesuffix('\\'))
        index += 1
        if not line.endswith('\\'):
            break
    return ' '.join(command)


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    # Native python3 resolves to the existing locked test environment, not host Python.
    monkeypatch.setenv('PATH', str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    return nextflow.compile_nextflow_invocation('protein_modification_experimental', 'region_redesign',
        dict(input_pdb=str(tmp_path / 'candidate_0007.pdb'), design_chains='H', context_chains='T',
             structure_validators=['protenix_v2'], msa_provider='colabfold_api',
             protenix_use_msa=True, protenix_seeds='7,9', colabfold_pairing_mode='unpaired',
             colabfold_use_env=False, run_frustrampnn=False), str(tmp_path / 'results'), 'root',
        source_identity=SourceIdentity('a' * 40, 'b' * 40),
        execution_context=nextflow.NativeCompilerExecutionContext(0, (0,), 'cpu'))


def test_plr_actual_compiler_producer_consumer(invocation, tmp_path, provider):
    plan = invocation.execution_plan
    params = json.loads(plan.native_parameters_json)
    assert params['plr_validator_suite_active'] is True
    assert invocation.entrypoint == 'workflows/protein_local_redesign.nf'
    assert invocation.command[invocation.command.index('--protenix_use_msa') + 1] == 'true'
    services = [s for s in plan.metadata.external_services if handoff.generated_msa_service_supported(s)]
    assert len(services) == 1
    assert 'modules/protenix.nf:ProtenixFromComplex' in services[0].authority
    assert any(d.relative_path == 'scripts/lib/component_adapter.py' for d in plan.metadata.dependencies)
    # Reuse only the labelled PDB fixture; PLR's own producer builds the roster.
    r03.native_roster(tmp_path)
    pdb = tmp_path / 'candidate_0007.pdb'
    module = (ROOT / 'workflows/protein_local_redesign.nf').read_text()
    helpers = module[module.index('def shellQuote('):module.index('def parseProteinLocalValidators')]
    params['code_root'] = str(ROOT)  # test-owned source relocation, not science
    producer = render(tmp_path, 'workflows/protein_local_redesign.nf', 'PrepareProteinLocalValidatorInput',
        dict(params=params, producer_meta={'candidate_id': pdb.stem}, source_pdb=str(pdb)), helpers)
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', producer], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / (pdb.stem + '.protenix.json')).read_text())
    assert payload[0]['modelSeeds'] == [7, 9]
    assert [c['proteinChain']['sequence'] for c in payload[0]['sequences']] == ['ACDE', 'FGHI']
    contract = json.loads((tmp_path / (pdb.stem + '.validator_contract.json')).read_text())
    assert [c['chain_id'] for c in contract['components']] == ['H', 'T']
    module = (ROOT / 'modules/protenix.nf').read_text()
    helpers = module[module.index('def normalizeGpuCsvValue'):module.index('// ─', module.index('def normalizeGpuCsvValue'))]
    helpers += module[module.index('def protenixComplexFinalizesGeometry'):module.index('process ProtenixFromComplex')]
    consumer = render(tmp_path, 'modules/protenix.nf', 'ProtenixFromComplex',
        dict(params=params, input_sample={'candidate_id': pdb.stem}, complex_json=pdb.stem + '.protenix.json',
             prepared_msa=[], task={'cpus': 2}), helpers)
    env = dict(os.environ, PROTENIX_INPUT_JSON=str(tmp_path / (pdb.stem + '.protenix.json')),
               RESOLVED_FIXED_TARGET_SOURCE_PATH='')
    conditioned = subprocess.run(['bash', '-euo', 'pipefail', '-c', native_command(consumer, 'prepare_protenix_constraints.py')],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert conditioned.returncode == 0, conditioned.stdout + conditioned.stderr
    original = (tmp_path / 'protenix_input_conditioned.json').read_bytes()
    payload = json.loads(original)
    output = tmp_path / 'worker/artifacts'
    output.mkdir(parents=True)
    value = dict(ledger_path=str(tmp_path / 'worker/ledger.sqlite'), artifact_root=str(output),
        attempt_id='attempt', root_job_id='root', target_id='remote-target', lease_id='lease',
        plan_sha256=plan.plan_sha256, source_identity=plan.to_dict()['source_identity'],
        execution_plan=plan.to_dict(), working_directory=str(ROOT))
    context_path = tmp_path / 'worker/context.json'
    context_path.write_text(json.dumps(value))
    owner = r03.runtime((value, context_path))
    env.update(BMS_COMPONENT_CONTEXT=str(context_path), PROTENIX_INPUT_JSON=str(tmp_path / 'protenix_input_conditioned.json'),
               PROTENIX_MSA_CACHE_DIR=str(tmp_path / 'empty-worker-cache'))
    command = native_command(consumer, 'prepare_protenix_msa.py')
    assert '--generated-service protenix:generated_msa' in command
    process = subprocess.Popen(['bash', '-euo', 'pipefail', '-c', command], cwd=tmp_path,
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        request = r03.pending(owner, process)
        assert request['native_input'] == payload
        package = tmp_path / 'controller/package'
        sha = handoff.prepare_generated_msa(request, package)
        r03.deliver(owner, request, package, sha)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0, out + err
        prepared = json.loads((tmp_path / 'prepared_input.json').read_text())
        for item in prepared[0]['sequences']:
            path = Path(item['proteinChain'].pop('unpairedMsaPath'))
            assert path.is_relative_to(output) and path.is_file()
        assert prepared == payload
        assert (tmp_path / 'protenix_input_conditioned.json').read_bytes() == original
        assert [call[0] for call in provider[0].calls] == ['POST', 'GET', 'GET']
        assert owner.pending_external_services() == ()
        # Completed request and controller cache replay preserve the exact roster.
        replay = subprocess.run(['bash', '-euo', 'pipefail', '-c', command], cwd=tmp_path,
            env=env, capture_output=True, text=True, timeout=15)
        assert replay.returncode == 0, replay.stdout + replay.stderr
        handoff.prepare_generated_msa(request, tmp_path / 'controller/cache-replay')
        assert len(provider[0].calls) == 3
        changed = copy.deepcopy(payload)
        changed[0]['name'] = 'different-native-candidate'
        key = owner.submit_external_service('protenix:generated_msa', changed)
        assert key != request['request_id']
        assert owner.external_service(key)['result'] is None
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize('workflow,params,supported', [
    ('protein_local_redesign', {'plr_structure_validators': ['protenix_v2'], 'plr_validator_suite_active': True}, True),
    ('protein_local_redesign', {'plr_structure_validators': ['protenix_v2']}, False),
    ('protein_local_redesign', {'plr_structure_validators': ['protenix_v2'], 'plr_validator_suite_active': True, 'protenix_prepared_msa_dir': '/supplied'}, False),
    ('protein_local_redesign', {'plr_structure_validators': ['protenix_v2'], 'plr_validator_suite_active': True, 'protenix_use_msa': False}, False),
    ('conformational_mapping', {'cm_request': {'backend': 'protenix_v2_ensemble'}}, False),
    ('confornets_experimental', {'cn_skip_msa': False}, False),
])
def test_canonical_service_roster(workflow, params, supported):
    params = dict(msa_provider='colabfold_api', **params)
    model_id, mode = {
        'protein_local_redesign': ('protein_modification_experimental', 'region_redesign'),
        'conformational_mapping': ('conformational_mapping', 'map'),
        'confornets_experimental': ('confornets_experimental', 'design'),
    }[workflow]
    plan = nextflow.build_selected_execution_plan(model_id=model_id, mode=mode,
        entrypoint='workflows/' + workflow + '.nf', requested=canonical_bytes(params),
        effective=canonical_bytes(params), native_parameters=canonical_bytes(params), source_identity=SourceIdentity('a'*40, 'b'*40))
    assert any(handoff.generated_msa_service_supported(s) for s in plan.metadata.external_services) == supported


@pytest.mark.parametrize('authority', ['', 'modules/conformational_mapping_protenix.nf:CanonicalProtenixEnsemble',
    'modules/confornets_experimental.nf:RunConforNets', 'modules/protenix.nf:ProtenixPredict',
    'modules/protenix.nf:ProtenixFromComplex:forged'])
def test_identifier_alone_never_grants_service(authority):
    assert not handoff.generated_msa_service_supported(dict(logical_id='protenix:generated_msa',
        state='planned_from_generated_candidates', provider='colabfold_api', authority=authority))


@pytest.mark.parametrize('active,prepared,expected', [
    (True, [], True), ('true', [], True), (False, [], False),
    (None, [], False), (True, '/controller/prepared', False),
])
def test_actual_native_flag_has_exact_caller_guard(invocation, tmp_path, active, prepared, expected):
    params = json.loads(invocation.execution_plan.native_parameters_json)
    params['plr_validator_suite_active'] = active
    module = (ROOT / 'modules/protenix.nf').read_text()
    helpers = module[module.index('def normalizeGpuCsvValue'):module.index('// ─', module.index('def normalizeGpuCsvValue'))]
    helpers += module[module.index('def protenixComplexFinalizesGeometry'):module.index('process ProtenixFromComplex')]
    script = render(tmp_path, 'modules/protenix.nf', 'ProtenixFromComplex',
        dict(params=params, input_sample={'candidate_id': 'candidate'}, complex_json='native.json',
             prepared_msa=prepared, task={'cpus': 2}), helpers)
    command = native_command(script, 'prepare_protenix_msa.py')
    assert ('--generated-service protenix:generated_msa' in command) == expected
    assert ('--prepared-inputs' in command) == bool(prepared)


@pytest.mark.parametrize('skip_msa', [False, True])
def test_confornets_actual_preprocessor_is_not_portable_msa_consumer(tmp_path, skip_msa):
    spec = importlib.util.spec_from_file_location('r03_confornets_native', ROOT / 'scripts/run_confornets_inference.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    repo = tmp_path / 'declared-confornets-repo'
    repo.mkdir()
    request = dict(benchmark='candidate-roster', params=dict(confornets_repo_path=str(repo), skip_msa=skip_msa))
    cwd, command = native._build_preprocess_command(request, tmp_path / 'assets')
    assert cwd == repo and command[1] == str(repo / 'preprocess.py')
    assert ('--skip-msa' in command) == skip_msa
    assert not {'--prepared-inputs', '--prepared-sha256', '--generated-service'}.intersection(command)


@pytest.mark.parametrize('case_index', [0, 8, -1])
def test_cm_native_producer_is_format_compatible_but_has_no_provider_authority(tmp_path, monkeypatch, case_index):
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    vectors = json.loads((ROOT / 'platform/api/tests/fixtures/conformational_mapping/phase_0_vectors/complex_cases.json').read_text())
    snapshot = copy.deepcopy([case for case in vectors['cases'] if case['kind'] == 'positive'][case_index])
    snapshot.update(target_id='native-target', target_order=0, unsupported_fields=[])
    snapshot.setdefault('admission', dict(token_count=1, atom_count=1, token_limit=4096, conversion_omissions=[]))
    request = dict(request_id='cm-roster-probe', request_sha256='a'*64,
        backend='protenix_v2_ensemble', targets=[{'target_id': snapshot['target_id']}], ordered_seeds=[7, 9],
        feature_policy=dict(mode='regenerate_mutated_protein_v1', protein_msa_enabled=True,
                            templates_enabled=False, rna_msa_enabled=False))
    managed = Path(os.environ['BMS_INPUTS']) / ('cm-roster-' + str(case_index))
    managed.mkdir()
    request_path, snapshots = managed / 'request.json', managed / 'snapshots.json'
    request_path.write_text(json.dumps(request))
    snapshots.write_text(json.dumps([snapshot]))
    original = request_path.read_bytes(), snapshots.read_bytes()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_protenix_conformational_mapping.py'),
        '--request', str(request_path), '--snapshots', str(snapshots), '--out', str(tmp_path / 'native')],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / 'native/protenix_input.json').read_text())
    assert handoff.generated_protenix_request(payload) == payload
    assert payload[0]['modelSeeds'] == [7, 9]
    assert original == (request_path.read_bytes(), snapshots.read_bytes())
    params = dict(cm_request_path=str(request_path), gpu_id=0, run_frustrampnn=True)
    invocation = nextflow.compile_nextflow_invocation('conformational_mapping', 'map', params,
        str(tmp_path / 'cm-results'), 'cm-job', source_identity=SourceIdentity('a'*40, 'b'*40))
    services = [s for s in invocation.execution_plan.metadata.external_services if s.logical_id == 'protenix:generated_msa']
    assert len(services) == 1
    assert services[0].provider is None
    assert json.loads(services[0].settings_json) == {}
    assert not handoff.generated_msa_service_supported(services[0])
    with pytest.raises(ValueError, match='canonical conformational-mapping launch parameters fail closed: .*msa_provider'):
        nextflow.compile_nextflow_invocation('conformational_mapping', 'map',
            dict(params, msa_provider='colabfold_api'), str(tmp_path / 'cm-results'), 'cm-job')
