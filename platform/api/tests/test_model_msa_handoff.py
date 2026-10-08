"""Offline handoff tests. Tiny alignments are transport fixtures, not science."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from biomodstack_msa_handoff import digest
from services.model_msa_handoff import native_alignments, prepare_launch_msa


def provider_fixture(monkeypatch, tmp_path):
    calls = []
    def prepare(*, sequences, params):
        calls.append((sequences, params))
        artifacts = []
        for index, sequence in enumerate(sequences):
            data = f'>query\n{sequence}\n>fixture-hit\n{sequence}\n'.encode()
            path = tmp_path / f'cache-{index}.a3m'
            path.write_bytes(data)
            artifacts.append(dict(chain_index=index, role='unpaired', path=str(path), sha256=digest(data)))
        return dict(provider=params['msa_provider'], request_digest='fixture-request',
                    artifacts=artifacts, provenance={'fixture': True}, cache_hit=True)
    monkeypatch.setitem(sys.modules, 'services.msa_preparation', SimpleNamespace(prepare_model_msa=prepare))
    return calls


@pytest.mark.parametrize('provider', ['colabfold_api', 'neurosnap_api'])
def test_cache_to_boltz_native_path(monkeypatch, tmp_path, provider):
    calls = provider_fixture(monkeypatch, tmp_path)
    params = dict(sequence='ACDE', boltz_use_msa=True, msa_provider=provider,
                  msa_neurosnap_max_sequences=25, msa_neurosnap_pad_sequences=False)
    output = prepare_launch_msa('boltz2', params, tmp_path / 'native')
    assert len(calls) == 1
    assert calls[0][1]['msa_neurosnap_max_sequences'] == 25
    assert calls[0][1]['msa_neurosnap_pad_sequences'] is False
    assert Path(output['msa_path']).read_bytes() == (tmp_path / 'cache-0.a3m').read_bytes()
    assert 'msa_path' not in params


def test_supplied_and_no_msa_never_call_provider(monkeypatch, tmp_path):
    calls = provider_fixture(monkeypatch, tmp_path)
    source = tmp_path / 'supplied.a3m'
    source.write_text('>query\nACDE\n')
    params = dict(sequence='ACDE', boltz_use_msa=True, msa_path=str(source))
    assert prepare_launch_msa('boltz2', params, tmp_path / 'native') == params
    params = dict(sequence='ACDE', boltz_use_msa=False)
    assert prepare_launch_msa('boltz2', params, tmp_path / 'native') == params
    assert not calls


def test_complex_chain_order_and_identity(monkeypatch, tmp_path):
    calls = provider_fixture(monkeypatch, tmp_path)
    params = dict(boltz_use_msa=True, msa_provider='neurosnap_api', complex_components=[
        {'type': 'protein', 'id': 'B', 'sequence': 'ACDE'},
        {'type': 'ligand', 'id': 'L', 'smiles': 'C'},
        {'type': 'protein', 'id': 'A', 'sequence': 'FGHI'}])
    output = prepare_launch_msa('boltz2', params, tmp_path / 'native')
    assert calls[0][0] == ['ACDE', 'FGHI']
    components = output['complex_components']
    assert [c['id'] for c in components] == ['B', 'L', 'A']
    assert 'ACDE' in Path(components[0]['msa_path']).read_text()
    assert 'FGHI' in Path(components[2]['msa_path']).read_text()
    assert 'msa_path' not in components[1]


def test_boltz_cp_bundle_survives_root_relocation(monkeypatch, tmp_path):
    calls = provider_fixture(monkeypatch, tmp_path)
    source = tmp_path / 'input.yaml'
    source.write_text(yaml.safe_dump({'version': 1, 'sequences': [
        {'protein': {'id': ['B', 'C'], 'sequence': 'ACDE'}},
        {'protein': {'id': 'A', 'sequence': 'FGHI'}}]}))
    output = prepare_launch_msa('boltz_cp_experimental', dict(
        boltz_use_msa=True, msa_provider='colabfold_api', bcp_input_path=str(source)), tmp_path / 'controller')
    assert len(calls) == 1
    worker = tmp_path / 'remote' / 'task' / 'bundle'
    shutil.copytree(output['bcp_input_path'], worker)
    shutil.rmtree(tmp_path / 'controller')
    spec = importlib.util.spec_from_file_location('resolve_bundle', ROOT / 'scripts/resolve_boltz_msa_bundle.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.resolve_yaml(worker / 'input.yaml')
    payload = yaml.safe_load((worker / 'input.yaml').read_text())
    for item in payload['sequences']:
        path = Path(item['protein']['msa'])
        assert path.is_relative_to(worker)
        assert item['protein']['sequence'] in path.read_text()
    assert payload['sequences'][0]['protein']['id'] == ['B', 'C']


@pytest.mark.parametrize('failure', ['digest', 'query', 'paired'])
def test_invalid_or_unrepresentable_provider_output_fails_closed(tmp_path, failure):
    source = tmp_path / 'cached.a3m'
    source.write_text('>query\nACDE\n>paired-hit\nACDE\n')
    artifact = dict(chain_index=0, role='paired' if failure == 'paired' else 'unpaired',
                    path=str(source), sha256='wrong' if failure == 'digest' else digest(source.read_bytes()))
    with pytest.raises(ValueError):
        native_alignments({'artifacts': [artifact]}, ['FGHI' if failure == 'query' else 'ACDE'], tmp_path / 'native')


@pytest.mark.parametrize('provider', ['colabfold_api', 'neurosnap_api'])
def test_protenix_cli_consumes_relocated_provider_inputs(tmp_path, provider):
    import subprocess
    from biomodstack_msa_handoff import package_alignments
    source = tmp_path / 'cached.a3m'
    source.write_text('>query\nACDE\n>fixture\nACDE\n')
    root = tmp_path / 'controller'
    payload = [{'name': 'fixture', 'sequences': [{'proteinChain': {'sequence': 'ACDE', 'count': 1}}]}]
    manifest = package_alignments(root, chains=[dict(chain_id='0:0', sequence='ACDE',
        role='unpaired', source=str(source))], settings={'msa_provider': provider}, provenance={'fixture': True})
    manifest['model_input'] = payload
    (root / 'msa-inputs.json').write_text(json.dumps(manifest))
    sha = digest((root / 'msa-inputs.json').read_bytes())
    worker = tmp_path / 'worker'
    shutil.copytree(root, worker)
    shutil.rmtree(root)
    input_path = tmp_path / 'input.json'
    input_path.write_text(json.dumps(payload))
    output = tmp_path / 'output.json'
    completed = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_protenix_msa.py'),
        '--input_json', str(input_path), '--output_json', str(output), '--out_dir', str(tmp_path / 'task'),
        '--backend', provider, '--prepared-inputs', str(worker), '--prepared-sha256', sha],
        capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    chain = json.loads(output.read_text())[0]['sequences'][0]['proteinChain']
    assert Path(chain['unpairedMsaPath']).is_relative_to(worker)
    assert Path(chain['unpairedMsaPath']).read_bytes() == source.read_bytes()


def test_rf3_missing_workflow_is_explicit(tmp_path):
    with pytest.raises(ValueError, match='no executable prediction workflow'):
        prepare_launch_msa('rf3', {'rf3_use_msa': True}, tmp_path)


def test_remote_alignment_is_a_mandatory_input_not_a_result(monkeypatch, tmp_path):
    from services.remote_execution import bundle
    calls = provider_fixture(monkeypatch, tmp_path)
    managed = tmp_path / 'managed'
    output = managed / 'results' / 'job'
    params = prepare_launch_msa('boltz2', dict(sequence='ACDE', boltz_use_msa=True,
        msa_provider='neurosnap_api'), output / 'prepared-msa')
    for name in ('get_data_root', 'get_inputs_dir', 'get_results_dir'):
        monkeypatch.setattr(bundle, name, lambda: managed)
    monkeypatch.setattr(bundle, 'get_weights_root', lambda: tmp_path / 'weights')
    monkeypatch.setattr(bundle, 'get_container_dir', lambda: tmp_path / 'containers')
    assets = bundle._input_assets({}, command=['nextflow', '--msa_path', params['msa_path']],
        repo_root=tmp_path / 'source', runtime_paths=set(), output_dir=output)
    assert len(assets) == 1 and len(calls) == 1
    path, relative = assets[0]
    remote = '/remote/attempt/bundle/inputs/' + relative
    assert bundle._rewrite(str(path), {str(path): remote}) == remote
    records = bundle._records_for_source(path, 'inputs/' + relative, 'input')
    assert records[0].sha256 == digest(path.read_bytes())


def paired_fixture(tmp_path, sequences=('ACDE', 'FGHI'), duplicate=False):
    """Synthetic transport rows, not biological homolog/taxonomy claims."""
    artifacts = []
    for index, sequence in enumerate(sequences):
        for role in ('paired', 'unpaired'):
            hits = [sequence if duplicate else 'A' + sequence[1:], '-' + sequence[1:]]
            if index == 0 and not duplicate:
                hits[0] = 'V' + sequence[1:]
            if role == 'unpaired':
                hits = ['W' + sequence[1:]]
            data = (f'>query\n{sequence}\n' + ''.join(
                f'>synthetic-{role}-{i}\n{row}\n' for i, row in enumerate(hits))).encode()
            path = tmp_path / f'fixture-{index}-{role}.a3m'
            path.write_bytes(data)
            artifacts.append(dict(chain_index=index, role=role, path=str(path), sha256=digest(data)))
    return {'artifacts': artifacts, 'provenance': {'fixture': True}}


@pytest.mark.parametrize('model', ['boltz2', 'boltz_cp_experimental'])
def test_paired_csv_group_identity_and_chain_order(tmp_path, model):
    import csv
    result = paired_fixture(tmp_path)
    paths = native_alignments(result, ['ACDE', 'FGHI'], tmp_path / 'native', model_id=model)
    tables = [list(csv.DictReader(path.open())) for path in paths]
    assert [table[0]['sequence'] for table in tables] == ['ACDE', 'FGHI']
    assert [[row['key'] for row in table] for table in tables] == [['-1', '1', '2', '-1']] * 2
    assert [row['sequence'] for row in tables[0]] == ['ACDE', 'VCDE', '-CDE', 'WCDE']
    groups = json.loads((tmp_path / 'native/pairing-groups.json').read_text())
    assert groups['paired_rows'] == 2
    assert groups['key_semantics'] == 'provider-paired-row-index-not-taxonomy'
    assert not any('TaxID=' in p.read_text() for p in paths)


def test_cp_exact_duplicate_parser_limitation_boltz2_preserves(tmp_path):
    result = paired_fixture(tmp_path, duplicate=True)
    with pytest.raises(ValueError, match='Boltz-CP parse_csv deduplicates paired sequences'):
        native_alignments(result, ['ACDE', 'FGHI'], tmp_path / 'cp', model_id='boltz_cp_experimental')
    paths = native_alignments(result, ['ACDE', 'FGHI'], tmp_path / 'boltz2')
    assert '1,ACDE' in paths[0].read_text()


@pytest.mark.parametrize('missing', [True, False])
def test_paired_depth_or_coverage_mismatch_rejected(tmp_path, missing):
    result = paired_fixture(tmp_path)
    artifact = next(a for a in result['artifacts'] if a['chain_index'] == 1 and a['role'] == 'paired')
    if missing:
        result['artifacts'].remove(artifact)
    else:
        source = Path(artifact['path'])
        source.write_text('>query\nFGHI\n')
        artifact['sha256'] = digest(source.read_bytes())
    with pytest.raises(ValueError, match='equal row counts'):
        native_alignments(result, ['ACDE', 'FGHI'], tmp_path / 'native')


def test_paired_cp_bundle_csv_survives_relocation(monkeypatch, tmp_path):
    result = paired_fixture(tmp_path)
    calls = []
    def prepare(**kwargs):
        calls.append(kwargs)
        return result
    monkeypatch.setitem(sys.modules, 'services.msa_preparation', SimpleNamespace(prepare_model_msa=prepare))
    source = tmp_path / 'input.yaml'
    source.write_text(yaml.safe_dump({'version': 1, 'sequences': [
        {'protein': {'id': ['B', 'C'], 'sequence': 'ACDE'}},
        {'protein': {'id': 'A', 'sequence': 'FGHI'}}]}))
    output = prepare_launch_msa('boltz_cp_experimental', dict(
        boltz_use_msa=True, msa_provider='colabfold_api', bcp_input_path=str(source)), tmp_path / 'controller')
    worker = tmp_path / 'worker'
    shutil.copytree(output['bcp_input_path'], worker)
    shutil.rmtree(tmp_path / 'controller')
    spec = importlib.util.spec_from_file_location('resolve_bundle', ROOT / 'scripts/resolve_boltz_msa_bundle.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.resolve_yaml(worker / 'input.yaml')
    payload = yaml.safe_load((worker / 'input.yaml').read_text())
    assert len(calls) == 1 and calls[0]['sequences'] == ['ACDE', 'FGHI']
    assert [p['protein']['id'] for p in payload['sequences']] == [['B', 'C'], 'A']
    for item in payload['sequences']:
        path = Path(item['protein']['msa'])
        assert path.suffix == '.csv' and path.is_relative_to(worker)
        assert path.read_text().startswith('key,sequence\n-1,')


def test_supplied_csv_never_searches(monkeypatch, tmp_path):
    calls = provider_fixture(monkeypatch, tmp_path)
    source = tmp_path / 'supplied.csv'
    source.write_text('key,sequence\n-1,ACDE\n12,VCDE\n')
    params = dict(sequence='ACDE', boltz_use_msa=True, msa_path=str(source))
    assert prepare_launch_msa('boltz2', params, tmp_path / 'native') == params
    assert not calls


@pytest.mark.parametrize('model', ['boltz2', 'boltz_cp_experimental'])
def test_paired_complex_launch_requests_once(monkeypatch, tmp_path, model):
    result = paired_fixture(tmp_path)
    calls = []
    def prepare(**kwargs):
        calls.append(kwargs)
        return result
    monkeypatch.setitem(sys.modules, 'services.msa_preparation', SimpleNamespace(prepare_model_msa=prepare))
    params = dict(boltz_use_msa=True, msa_provider='colabfold_api', complex_components=[
        {'type': 'protein', 'id': 'B', 'count': 2, 'sequence': 'ACDE'},
        {'type': 'ligand', 'id': 'L', 'smiles': 'C'},
        {'type': 'protein', 'id': 'A', 'count': 1, 'sequence': 'FGHI'}])
    out = prepare_launch_msa(model, params, tmp_path / 'native')
    assert len(calls) == 1 and calls[0]['sequences'] == ['ACDE', 'FGHI']
    assert [c['id'] for c in out['complex_components']] == ['B', 'L', 'A']
    for index in (0, 2):
        assert out['complex_components'][index]['count'] == params['complex_components'][index]['count']
        assert Path(out['complex_components'][index]['msa_path']).suffix == '.csv'
    assert 'msa_path' not in params['complex_components'][0]


@pytest.mark.parametrize('text', ['sequence,key\nFGHI,-1\n', 'key,sequence\n-1,ACDE,extra\n',
                                  'key,sequence\n-1,ACDE\nfoo,VCDE\n',
                                  'key,sequence\n-1,ACDE\n1,V.CDE\n'])
def test_invalid_native_csv_rejected(tmp_path, text):
    from biomodstack_boltz_msa import validate_boltz_msa
    source = tmp_path / 'bad.csv'
    source.write_text(text)
    with pytest.raises(ValueError):
        validate_boltz_msa(source, 'ACDE')


@pytest.mark.parametrize('parser_name', ['boltz2', 'boltz_cp'])
def test_real_native_csv_parser_contract(tmp_path, parser_name):
    """Execute upstream parse_csv verbatim, without installing GPU model deps.

    Only the MSA container/token alphabet are lightweight scaffolding. NumPy,
    pandas, native dtype widths and the full parser control flow are real.
    """
    import ast
    import numpy as np
    import pandas as pd
    from typing import Optional
    source = Path(__file__).parent / 'fixtures/boltz_native' / f'{parser_name}_csv.py'
    tree = ast.parse(source.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'parse_csv')
    letters = 'ARNDCQEGHILKMFPSTWYVX-'
    namespace = dict(Path=Path, Optional=Optional, np=np, pd=pd, MSA=SimpleNamespace,
        const=SimpleNamespace(prot_letter_to_token={c: c for c in letters},
                              token_ids={c: i for i, c in enumerate(letters)}),
        MSAResidue=[('res_type', 'i1')], MSADeletion=[('res_idx', 'i2'), ('deletion', 'i2')],
        MSASequence=[('seq_idx', 'i2'), ('taxonomy', 'i4'), ('res_start', 'i4'),
                     ('res_end', 'i4'), ('del_start', 'i4'), ('del_end', 'i4')])
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    parse_csv = namespace['parse_csv']
    paths = native_alignments(paired_fixture(tmp_path), ['ACDE', 'FGHI'], tmp_path / 'native')
    for path in paths:
        msa = parse_csv(path)
        assert msa.sequences['taxonomy'].tolist() == [-1, 1, 2, -1]
        assert msa.sequences['seq_idx'].tolist() == [0, 1, 2, 3]
        assert (msa.sequences['res_end'] - msa.sequences['res_start']).tolist() == [4] * 4
    # Exact parser difference motivating CP's fail-closed check.
    duplicate_paths = native_alignments(paired_fixture(tmp_path, duplicate=True),
                                        ['ACDE', 'FGHI'], tmp_path / 'duplicates')
    keys = parse_csv(duplicate_paths[0]).sequences['taxonomy'].tolist()
    assert keys == ([-1, 1, 2, -1] if parser_name == 'boltz2' else [-1, 2, -1])
    # Lowercase insertion bytes pass unchanged and become native deletions.
    insertion = tmp_path / 'insertion.csv'
    insertion.write_text('key,sequence\n-1,ACDE\n1,VkkCDE\n')
    parsed = parse_csv(insertion)
    assert parsed.deletions.tolist() == [(1, 2)]


def test_controller_prepares_before_all_launch_lanes():
    source = (ROOT / 'platform/api/services/nextflow.py').read_text()
    start = source.index('launch_params = await asyncio.to_thread(\n                prepare_launch_msa')
    assert start < source.index('if job.execution_target_id:', start)
    assert start < source.index('launch_via_workflow_adapter,', start)
    bundle = (ROOT / 'platform/api/services/remote_execution/bundle.py').read_text()
    assert 'prepare_remote_protenix_inputs(' not in bundle
    assert 'result_policy' not in bundle[bundle.index('    input_assets = _input_assets('):bundle.index('    source_records =')]
