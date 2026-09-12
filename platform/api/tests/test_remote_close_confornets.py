"""Offline generated service -> pinned native OF3 reader (no inference/weights).

Only HTTP and DataModule/tensor storage are doubles. Exact upstream AST bodies
perform JSON validation, A3M parsing/deletions and save_batches dispatch.
"""
import ast
import argparse
import shutil
import copy
from enum import IntEnum
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import string
import subprocess
import sys
from types import SimpleNamespace
from typing import Annotated, Any, NamedTuple, Sequence

import numpy as np
import pydantic
import pytest

from component_runtime import SourceIdentity
from services import nextflow, model_msa_handoff as handoff

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
import prepare_confornets_msa as adapter
import run_confornets_inference as driver

spec = importlib.util.spec_from_file_location('confor_service_fixtures', Path(__file__).with_name('test_remote_rectify_generated_msa.py'))
r03 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r03)
provider = r03.provider


def upstream_namespace():
    fixtures = ROOT / 'tests/fixtures/confornets_4df561a'
    provenance = json.loads((fixtures / 'provenance.json').read_text())
    ns: dict[str, Any] = dict(Any=Any, Annotated=Annotated, NamedTuple=NamedTuple, IntEnum=IntEnum,
              Sequence=Sequence, np=np, string=string, logging=logging, Path=Path,
              MsaArray=SimpleNamespace)
    ns.update({k: getattr(pydantic, k) for k in ('BaseModel', 'BeforeValidator', 'DirectoryPath', 'FilePath', 'field_serializer')})
    def load(filename, names):
        data = (fixtures / filename).read_bytes()
        assert hashlib.sha256(data).hexdigest() == provenance['files'][filename]['sha256']
        nodes = [n for n in ast.parse(data).body if getattr(n, 'name', None) in names]
        assert {n.name for n in nodes} == set(names)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), ns)
    load('openfold3__core__data__resources__residues.py.txt', ['MoleculeType'])
    load('openfold3__core__config__config_utils.py.txt', ['_ensure_list', '_cast_keys_to_int', '_convert_molecule_type'])
    load('openfold3__projects__of3_all_atom__config__inference_query_format.py.txt', ['Atom', 'Bond', 'Chain', 'Query', 'InferenceQuerySet'])
    load('openfold3__core__data__io__sequence__fasta.py.txt', ['parse_fasta'])
    load('openfold3__core__data__io__sequence__msa.py.txt', ['_msa_list_to_np', 'parse_a3m'])
    load('confornet_preprocess.py.txt', ['load_query_definitions', 'save_batches'])
    ns.update(argparse=argparse, json=json, shutil=shutil)
    load('preprocess.py.txt', ['main'])
    return ns


@pytest.fixture
def compiled(tmp_path, monkeypatch):
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    return nextflow.compile_nextflow_invocation('confornets_experimental', 'design',
        dict(sequence='ACDE', chain_id='B', task='diversity', msa_provider='colabfold_api',
             colabfold_use_env=False, colabfold_pairing_mode='unpaired', skip_msa=False),
        str(tmp_path / 'results'), 'root', source_identity=SourceIdentity('a'*40, 'b'*40),
        execution_context=nextflow.NativeCompilerExecutionContext(0, (0,), 'cpu'))


def staged(tmp_path):
    import prep_confornets_request as producer
    # Actual native producer CLI, no fake query document.
    command = [sys.executable, str(ROOT / 'scripts/prep_confornets_request.py'),
        '--sequence', 'ACDE', '--chain-id', 'B', '--checkpoint-path', '/runtime/checkpoint',
        '--confornets-repo-path', str(tmp_path), '--assets-dir', str(tmp_path/'assets'),
        '--output', str(tmp_path/'request.json')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads((tmp_path/'request.json').read_text())


def test_compiled_service_to_pinned_consumer(compiled, tmp_path, provider, monkeypatch):
    plan = compiled.execution_plan
    services = [s for s in plan.metadata.external_services if handoff.generated_msa_service_supported(s)]
    assert len(services) == 1
    assert 'modules/confornets_experimental.nf:RunConforNets' in services[0].authority
    assert json.loads(services[0].settings_json)['colabfold_use_env'] is False
    request = staged(tmp_path)
    original = (tmp_path/'request.json').read_bytes()
    # Actual HTTP protocol/cache/controller; replace the fixture roster with monomer.
    provider[0].responses = provider[1].cf_success(sequences=['ACDE'], use_env=False)
    output = tmp_path / 'worker/artifacts'
    output.mkdir(parents=True)
    value = dict(ledger_path=str(tmp_path/'worker/ledger.sqlite'), artifact_root=str(output),
        attempt_id='attempt', root_job_id='root', target_id='remote-target', lease_id='lease',
        plan_sha256=plan.plan_sha256, source_identity=plan.to_dict()['source_identity'],
        execution_plan=plan.to_dict(), working_directory=str(ROOT))
    ctx = tmp_path/'worker/context.json'
    ctx.write_text(json.dumps(value))
    owner = r03.runtime((value, ctx))
    process = subprocess.Popen([sys.executable, str(ROOT/'scripts/prepare_confornets_msa.py'),
        '--request', str(tmp_path/'request.json'), '--assets-dir', str(tmp_path/'assets'),
        '--output-dir', str(tmp_path/'prepared')], env=dict(os.environ, BMS_COMPONENT_CONTEXT=str(ctx)),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        pending = r03.pending(owner, process)
        assert pending['native_input'] == adapter.native_roster(request, tmp_path/'assets')[1]
        package = tmp_path/'controller/package'
        sha = handoff.prepare_generated_msa(pending, package)
        r03.deliver(owner, pending, package, sha)
        out, err = process.communicate(timeout=15)
        assert process.returncode == 0, out + err
        replay = subprocess.run(process.args, env=dict(os.environ, BMS_COMPONENT_CONTEXT=str(ctx)),
            capture_output=True, text=True, timeout=15)
        assert replay.returncode == 0, replay.stdout + replay.stderr
        ns = upstream_namespace()
        query_path = tmp_path/'prepared'/request['benchmark']/'query_msa.json'
        parsed = ns['InferenceQuerySet'].from_json(query_path)
        query = parsed.queries[request['query_id']]
        assert query.use_msas and query.use_main_msas and query.use_paired_msas
        assert query.chains[0].chain_ids == ['B']
        msa = ns['parse_a3m'](query.chains[0].main_msa_file_paths[0].read_text())
        assert ''.join(msa.msa[0]) == 'ACDE'
        assert len(msa.msa) > 1
        # Actual upstream batch function calls the real parser; scientific tensor
        # construction/storage are explicitly doubled, not claimed GPU inference.
        observed = []
        class DataModule:
            def __init__(self, config): self.config = config
            def setup(self): observed.append(self.config.datasets[0].config.query_set)
            def predict_dataloader(self): return [{'query_id': [request['query_id']]}]
        ns.update(DataModule=DataModule, DataModuleConfig=SimpleNamespace, InferenceDatasetSpec=SimpleNamespace,
            InferenceJobConfig=SimpleNamespace, MSASettings=SimpleNamespace, TemplateSettings=SimpleNamespace,
            TemplatePreprocessorSettings=SimpleNamespace, tqdm=lambda rows, **kw: rows,
            torch=SimpleNamespace(save=lambda batch, path: observed.append((batch, path))))
        _, native_argv = driver._build_preprocess_command(request, tmp_path/'prepared', prepared_msa=True)
        monkeypatch.setattr(sys, 'argv', native_argv[1:])
        ns['run_msa_batched'] = lambda **kw: pytest.fail('Native consumer attempted uncontrolled provider')
        ns['main']()
        assert observed[0].queries[request['query_id']].use_msas
        assert observed[1][1].name == request['query_id']+'.pt'
        _, command = driver._build_preprocess_command(request, tmp_path/'prepared', prepared_msa=True)
        assert command[-1] == '--skip-msa'  # skip duplicate upstream provider, not features
        assert request['params']['skip_msa'] is False
        assert (tmp_path/'request.json').read_bytes() == original
        assert owner.pending_external_services() == ()
        handoff.prepare_generated_msa(pending, tmp_path/'controller/replay')
        assert len(provider[0].calls) == 3
        changed = copy.deepcopy(pending['native_input']); changed[0]['name'] = 'changed'
        assert owner.submit_external_service('protenix:generated_msa', changed) != pending['request_id']
    finally:
        if process.poll() is None: process.kill()
        process.communicate(timeout=10)


def test_pinned_a3m_keeps_row_order_insertions_and_gaps():
    ns = upstream_namespace()
    parsed = ns['parse_a3m']('>query\nACDE\n>row2\nAaC-E\n>row1\nACdDE\n')
    assert parsed.metadata == ['query', 'row2', 'row1']
    assert [''.join(row) for row in parsed.msa] == ['ACDE', 'AC-E', 'ACDE']
    assert parsed.deletion_matrix.tolist() == [[0,0,0,0], [0,1,0,0], [0,0,1,0]]


@pytest.mark.parametrize('change', ['chain', 'sequence', 'multimer', 'supplied'])
def test_native_roster_rejects_mismatch(tmp_path, change):
    request = staged(tmp_path)
    path = tmp_path/'assets'/request['benchmark']/'test_cases'/request['test_case']/'query'/(request['query_id']+'.json')
    query = json.loads(path.read_text())
    if change == 'chain': query['chains'][0]['chain_ids'] = ['X']
    if change == 'sequence': query['chains'][0]['sequence'] = 'ACDF'
    if change == 'multimer': query['chains'].append(copy.deepcopy(query['chains'][0]))
    if change == 'supplied': query['chains'][0]['main_msa_file_paths'] = ['/foreign']
    path.write_text(json.dumps(query))
    with pytest.raises(ValueError, match='exact requested monomer'):
        adapter.native_roster(request, tmp_path/'assets')


def package_fixture(tmp_path, request, *, dot=False):
    from biomodstack_msa_handoff import package_alignments, digest
    from biomodstack_msa_policy import apply_msa_policy
    chains = []
    for role, row in [('unpaired', 'AaC-E'), ('paired', 'ACdDE')]:
        source = tmp_path / (role + '.a3m')
        source.write_text('>query\nACDE\n>' + role + '\n' + (row + '.' if dot else row) + '\n')
        chains.append(dict(chain_id='0:0', role=role, sequence='ACDE', source=source))
    package = tmp_path/'package'
    manifest = package_alignments(package, chains=chains, settings=apply_msa_policy('protenix', {'msa_provider': 'colabfold_api'}), provenance={'fixture': True})
    manifest['model_input'] = adapter.native_roster(request, tmp_path/'assets')[1]
    path = package/'msa-inputs.json'
    path.write_text(json.dumps(manifest))
    return package, digest(path.read_bytes())


def test_paired_and_main_native_paths_keep_bytes_and_deletions(tmp_path):
    request = staged(tmp_path)
    source, sha = package_fixture(tmp_path, request)
    output = adapter.prepare(request, tmp_path/'assets', tmp_path/'prepared', source=source, sha256=sha)
    ns = upstream_namespace()
    chain = ns['InferenceQuerySet'].from_json(output).queries[request['query_id']].chains[0]
    for native, role, expected in [(chain.main_msa_file_paths, 'unpaired', [0,1,0,0]),
                                   (chain.paired_msa_file_paths, 'paired', [0,0,1,0])]:
        assert native[0].read_bytes() == (tmp_path/(role+'.a3m')).read_bytes()
        assert ns['parse_a3m'](native[0].read_text()).deletion_matrix[1].tolist() == expected
    assert chain.main_msa_file_paths[0].parent == chain.paired_msa_file_paths[0].parent


@pytest.mark.parametrize('failure', ['digest', 'bytes', 'dots'])
def test_prepared_custody_corruption_and_native_incompatibility(tmp_path, failure):
    request = staged(tmp_path)
    source, sha = package_fixture(tmp_path, request, dot=failure == 'dots')
    if failure == 'digest': sha = '0'*64
    if failure == 'bytes':
        next((source/'alignments').iterdir()).write_text('>foreign\nZZZZ\n')
    with pytest.raises(ValueError):
        adapter.prepare(request, tmp_path/'assets', tmp_path/'prepared', source=source, sha256=sha)
    assert not (tmp_path/'prepared').exists()


@pytest.mark.parametrize('task', ['diversity', 'mse', 'transfer'])
def test_source_closure_preserves_legacy_and_requires_complete_adapter(tmp_path, task):
    import confornets_source_closure as closure
    repo = tmp_path/'upstream'
    repo.mkdir()
    for relative in closure.required_source_paths(task):
        if not relative.startswith('biomodstack/'):
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# source identity fixture, not executed science\n')
    run_script = {'diversity': '/source/run_diversity_bounded.py', 'mse': '/upstream/scripts/run_mse_training.py',
                  'transfer': '/upstream/scripts/run_transfer.py'}[task]
    commands = [[sys.executable, str(repo/'preprocess.py'), '--skip-msa'], [sys.executable, run_script]]
    for prepared in (False, True):
        records = driver._runtime_source_records(repo, task, prepared_msa=prepared)
        assert closure.validate_source_evidence(task, records, commands) == closure.required_source_paths(task, prepared_msa=prepared)
        if prepared:
            helper = next(r for r in records if r['relative_path'] == 'biomodstack/prepare_confornets_msa.py')
            assert helper['sha256'] == hashlib.sha256((ROOT/'scripts/prepare_confornets_msa.py').read_bytes()).hexdigest()
            with pytest.raises(ValueError): closure.validate_source_evidence(task, records[:-1], commands)
            with pytest.raises(ValueError): closure.validate_source_evidence(task, records, commands[::-1])


@pytest.mark.parametrize('module,process', [('confornets_experimental', 'RunConforNets'),
                                          ('conformational_mapping_confornets', 'RunCanonicalConforNets')])
def test_native_groovy_caller_uses_managed_source_and_service(compiled, tmp_path, module, process, monkeypatch):
    spec = importlib.util.spec_from_file_location('confor_groovy_fixtures', Path(__file__).with_name('test_remote_rectify_msa_roster.py'))
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    text = (ROOT/'modules'/ (module+'.nf')).read_text()
    start = text.index('def shellQuote(')
    end = text.index('\n}\n', start) + 3
    params = json.loads(compiled.execution_plan.native_parameters_json)
    params['code_root'] = str(ROOT)
    script = fixture.render(tmp_path, 'modules/'+module+'.nf', process,
        dict(params=params, request_json='request.json', assets_dir='assets'), text[start:end])
    command = fixture.native_command(script, 'run_confornets_inference.py')
    import shlex
    argv = shlex.split(command)
    assert argv[1] == str(ROOT/'scripts/run_confornets_inference.py')
    assert '--generated-msa-service' in argv
    assert '--request' in argv and '--assets-dir' in argv
    # Existing labels bind the SAME compiler source root (no SIF rebuild).
    config = (ROOT/'nextflow.config').read_text()
    label = 'ConforNetsCanonical' if 'Canonical' in process else 'ConforNets'
    block = config.split('withLabel: '+label+' {', 1)[1].split('\n    }', 1)[0]
    assert '--bind ${params.code_root}' in block
    request = staged(tmp_path)
    source, sha = package_fixture(tmp_path, request)
    import component_adapter
    seen = []
    def delivered(service, payload):
        assert service == 'protenix:generated_msa'
        assert payload == adapter.native_roster(request, tmp_path/'assets')[1]
        return source, sha
    monkeypatch.setattr(component_adapter, 'await_external_service', delivered)
    ns = upstream_namespace()
    class DataModule:
        def __init__(self, cfg): self.query_set = cfg.datasets[0].config.query_set
        def setup(self):
            query = self.query_set.queries[request['query_id']]
            assert query.use_msas and query.chains[0].chain_ids == ['B']
            seen.append(ns['parse_a3m'](query.chains[0].main_msa_file_paths[0].read_text()).msa.tolist())
        def predict_dataloader(self): return [{'query_id': [request['query_id']]}]
    ns.update(DataModule=DataModule, DataModuleConfig=SimpleNamespace, InferenceDatasetSpec=SimpleNamespace,
        InferenceJobConfig=SimpleNamespace, MSASettings=SimpleNamespace, TemplateSettings=SimpleNamespace,
        TemplatePreprocessorSettings=SimpleNamespace, tqdm=lambda rows, **kw: rows,
        torch=SimpleNamespace(save=lambda batch, path: seen.append(str(path))))
    ns['run_msa_batched'] = lambda **kw: pytest.fail('Native uncontrolled provider called')
    commands = []
    def native_process(cmd, cwd, log_path):
        commands.append(cmd)
        if cmd[1].endswith('/preprocess.py'):
            monkeypatch.setattr(sys, 'argv', cmd[1:])
            ns['main']()
        else:
            raise RuntimeError('STOP_BEFORE_GPU_INFERENCE')
    monkeypatch.setattr(driver, '_run', native_process)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', argv[1:argv.index('2>&1')] if '2>&1' in argv else argv[1:])
    # Rendered shell redirection is not part of Python argv.
    if '|' in sys.argv: monkeypatch.setattr(sys, 'argv', sys.argv[:sys.argv.index('|')])
    original_argv = list(sys.argv)
    for _ in range(2):
        monkeypatch.setattr(sys, 'argv', list(original_argv))
        with pytest.raises(RuntimeError, match='STOP_BEFORE_GPU_INFERENCE'):
            driver.main()
    assert len(commands) == 4 and commands[0][-1] == '--skip-msa'
    assert len(seen) == 4 and seen[0][0] == list('ACDE')
    effective = json.loads((tmp_path/'confornets_results/request.json').read_text())
    assert effective['params']['skip_msa'] is False


def test_interrupted_native_publication_retries_without_partial_input(tmp_path):
    import time
    request = staged(tmp_path)
    source, sha = package_fixture(tmp_path, request)
    output = tmp_path/'prepared'
    ready = tmp_path/'stage-ready'
    code = (
        'import sys,time; from pathlib import Path; '
        f'sys.path.insert(0,{str(ROOT / "scripts")!r}); '
        'import prepare_confornets_msa as a; '
        f'a._publish=lambda *args: (Path({str(ready)!r}).touch(),time.sleep(60)); '
        'a.main()'
    )
    arguments = ['--request', str(tmp_path/'request.json'), '--assets-dir', str(tmp_path/'assets'),
        '--output-dir', str(output), '--prepared-inputs', str(source), '--prepared-sha256', sha]
    process = subprocess.Popen([sys.executable, '-c', code, *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic()+10
        while not ready.exists() and time.monotonic()<deadline:
            if process.poll() is not None: pytest.fail(str(process.communicate()))
            time.sleep(.02)
        assert ready.exists() and not output.exists()
        process.kill(); process.communicate(timeout=5)
        assert list(tmp_path.glob('.prepared-*'))  # killed writer's task-owned staging
        result = subprocess.run([sys.executable, str(ROOT/'scripts/prepare_confornets_msa.py'), *arguments],
            capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        parsed = upstream_namespace()['InferenceQuerySet'].from_json(output/request['benchmark']/'query_msa.json')
        assert parsed.queries[request['query_id']].use_msas
    finally:
        if process.poll() is None: process.kill()
        process.communicate(timeout=5)


@pytest.mark.parametrize('foreign', ['empty', 'changed', 'symlink'])
def test_replay_never_replaces_foreign_output(tmp_path, foreign):
    request = staged(tmp_path)
    source, sha = package_fixture(tmp_path, request)
    output = tmp_path/'prepared'
    if foreign == 'empty': output.mkdir()
    elif foreign == 'symlink': output.symlink_to(tmp_path/'assets', target_is_directory=True)
    else:
        adapter.prepare(request, tmp_path/'assets', output, source=source, sha256=sha)
        (output/request['benchmark']/'query_msa.json').write_text('foreign')
    with pytest.raises(ValueError):
        adapter.prepare(request, tmp_path/'assets', output, source=source, sha256=sha)
    if foreign == 'empty': assert not list(output.iterdir())
    if foreign == 'symlink': assert output.is_symlink()
    if foreign == 'changed': assert (output/request['benchmark']/'query_msa.json').read_text() == 'foreign'
