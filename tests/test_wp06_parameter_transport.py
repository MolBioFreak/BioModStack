"""Route-free software contract fixtures. Never imports a scientific runtime."""
import importlib.util
import json
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wp06_runner', ROOT / 'scripts/run_esmfold2_inference.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def compile_request(params, staged=None):
    assert hasattr(runner, 'compile_workflow_request'), 'workflow argument compiler is missing'
    return runner.compile_workflow_request(params, staged or {})


def test_top_level_msa_false_zero_and_receipt(tmp_path):
    msa = tmp_path / 'selected.a3m'
    msa.write_text('>query\nACDE\n')
    argv, receipt = compile_request({'core_protein_scientific_contract': 1, 'sequence': 'ACDE',
        'msa_path': '/upload/input.a3m', 'msa_remove_insertions': False, 'seed': 0},
        {'/upload/input.a3m': str(msa)})
    args = runner.build_parser().parse_args(argv)
    assert args.msa_path == str(msa)
    assert args.msa_remove_insertions is False
    assert args.seed == 0
    assert receipt['settings']['msa_remove_insertions'] == {'requested': False, 'effective': False, 'origin': 'request', 'scope': 'primary'}
    assert receipt['sources'][0]['sha256'] == hashlib.sha256(msa.read_bytes()).hexdigest()
    assert receipt['argv'] == argv
    json.dumps(receipt, allow_nan=False)


@pytest.mark.parametrize('params', [
    {'msa_remove_insertions': 'false'}, {'seed': True}, {'seed': -1},
    {'msa_max_sequences': 0}, {'msa_max_sequences': 10001},
    {'msa_format': 'garbage'}, {'msa_path': 'missing'},
    {'msa_path': 'a', 'esmf_msa_path': 'b'},
    {'core_protein_scientific_contract': True}, {'core_protein_scientific_contract': 2},
    {'sequence': '', 'msa_path': 'a', 'complex_components': [{'type': 'protein', 'id': 'A', 'sequence': 'AC'}]},
])
def test_invalid_request_fails_before_command(params):
    with pytest.raises(ValueError):
        compile_request({'core_protein_scientific_contract': 1, **params})


def test_component_msa_keeps_own_scope(tmp_path):
    msa = tmp_path / 'component.a3m'
    msa.write_text('>x\nAC\n')
    argv, receipt = compile_request({'core_protein_scientific_contract': 1,
        'complex_components': [{'type': 'protein', 'id': 'B', 'sequence': 'AC', 'msa_path': 'source', 'msa_remove_insertions': False}]}, {'source': str(msa)})
    args = runner.build_parser().parse_args(argv)
    component = json.loads(args.complex_components_json)[0]
    assert component['msa_path'] == str(msa)
    assert component['msa_remove_insertions'] is False
    assert args.msa_path == ''
    assert receipt['sources'][0]['scope'] == 'component:B'


def test_msa_hash_and_parse_use_same_snapshot(tmp_path):
    msa = tmp_path / 'input.a3m'
    data = b'>q\nAC\n'
    msa.write_bytes(data)
    class MSA:
        @staticmethod
        def from_a3m(path, **kwargs):
            msa.write_text('>changed\nXX\n')
            assert Path(path).read_bytes() == data
            return type('Parsed', (), {'sequences': ['AC'], 'depth': 1})()
    assert 'expected_sha256' in __import__('inspect').signature(runner.load_msa_for_sequence).parameters
    _, metadata = runner.load_msa_for_sequence(MSA, path=str(msa), expected_sequence='AC',
        expected_sha256=hashlib.sha256(data).hexdigest(), snapshot_dir=tmp_path)
    assert metadata['sha256'] == hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError, match='hash'):
        runner.load_msa_for_sequence(MSA, path=str(msa), expected_sequence='AC',
            expected_sha256=hashlib.sha256(data).hexdigest(), snapshot_dir=tmp_path)


def test_api_marked_alias_conflict(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT / 'platform/api'))
    from services.nextflow import build_nextflow_command
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', '/bin/true')
    with pytest.raises(ValueError, match='conflicting'):
        build_nextflow_command('esmfold2', 'predict', {
            'core_protein_scientific_contract': 1,
            'msa_remove_insertions': False, 'esmf_msa_remove_insertions': True,
        }, output_dir=str(tmp_path / 'out'), job_id='wp06')


@pytest.mark.parametrize('component_mode', [False, True, 'multi'])
@pytest.mark.parametrize('remove', [False, True, None])
def test_real_workflow_non_model_capture(tmp_path, monkeypatch, component_mode, remove):
    import os
    import subprocess
    jar = Path('/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')
    if not jar.is_file():
        pytest.skip('installed native Nextflow jar unavailable; do not use production Docker wrapper')
    module = ROOT / 'modules/esmfold2_experimental.nf'
    msa = tmp_path / 'selected.a3m'
    msa.write_text('>query\nACDE\n')
    fixture = tmp_path / 'fixture.nf'
    fixture.write_text(f'''nextflow.enable.dsl=2
include {{ ESMFold2MSAPredict; esmfold2ContractInputs }} from '{module}'
workflow {{
    ESMFold2MSAPredict(esmfold2ContractInputs(Channel.of(tuple([id:'fixture'], 'ACDE', 'fixture')), params))
}}
''')
    config = tmp_path / 'nextflow.config'
    config.write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="256 MB"\ndocker.enabled=false\nsingularity.enabled=false\n' + f'process.ext.scripts_root="{ROOT}/scripts"\n')
    request = tmp_path / 'params.json'
    request.write_text(json.dumps({'code_root': str(ROOT), 'out_dir': str(tmp_path / 'out')}))
    import sys
    sys.path.insert(0, str(ROOT / 'platform/api'))
    from services.nextflow import build_nextflow_command
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', '/bin/true')
    msa_settings = {'msa_path': str(msa)}
    if remove is not None:
        msa_settings['msa_remove_insertions'] = remove
    input_settings = {'complex_components': [{'type': 'protein', 'id': 'B', 'sequence': 'ACDE', **msa_settings}]} if component_mode else {'sequence': 'ACDE', **msa_settings}
    if component_mode == 'multi':
        # Existing API contract: one MSA per component, no paired/unpaired
        # selector or dual-slot pairing semantics. Different formats are NOT
        # described as pairing. A primary MSA is forbidden on this API lane.
        msa2 = tmp_path / 'second.sto'
        msa2.write_text('# STOCKHOLM 1.0\nquery WQRS\n//\n')
        input_settings['complex_components'][0].update(msa_format='a3m', msa_max_sequences=7)
        input_settings['complex_components'].append({'type': 'protein', 'id': 'C',
            'sequence': 'WQRS', 'msa_path': str(msa2), 'msa_format': 'stockholm',
            'msa_max_sequences': 3, 'msa_remove_insertions': False})
    command = build_nextflow_command('esmfold2', 'predict', {
        'core_protein_scientific_contract': 1, 'seed': 0, **input_settings,
    }, output_dir=str(tmp_path / 'out'), job_id='wp06', requested_params={'seed': 0, **input_settings})
    # Keep only actual API-emitted request flags; never load production profiles.
    request_flags = []
    for index, flag in enumerate(command[:-1]):
        if flag.startswith('--esmf_') or flag == '--core_protein_scientific_contract':
            request_flags.extend([flag, command[index + 1]])
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    # The only fake is the scientific executable. The workflow and compiler execute.
    shim = bindir / 'python3'
    shim.write_text('#!/bin/sh\nif [ "$1" = "' + str(ROOT / 'scripts/run_esmfold2_inference.py') + '" ]; then\nshift\nexec /usr/bin/python3 "' + str(ROOT / 'tests/fixtures/wp06_capture.py') + '" "$@"\nfi\nexec /usr/bin/python3 "$@"\n')
    shim.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir)+':'+os.environ['PATH'], NXF_OFFLINE='true',
               NXF_HOME=str(tmp_path / 'nxf-home'), NXF_ASSETS=str(tmp_path / 'assets'),
               NXF_TEMP=str(tmp_path / 'nxf-temp'), JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2',
               PYTHONDONTWRITEBYTECODE='1', BMS_NVIDIA_SMI='/bin/false')
    result = subprocess.run(['java', '-jar', str(jar), '-C', str(config), 'run', str(fixture),
        '-params-file', str(request), '-work-dir', str(tmp_path / 'work')] + request_flags, cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    captures = list((tmp_path / 'work').glob('*/*/esmfold2_results/capture.json'))
    assert len(captures) == 1
    captured = json.loads(captures[0].read_text())
    receipt = json.loads((captures[0].parent / 'effective_settings.json').read_text())
    assert captured['argv'] == receipt['argv']
    assert captured['msa_sha256'] == hashlib.sha256(msa.read_bytes()).hexdigest()
    assert captured['msa_sha256'] == receipt['sources'][0]['sha256']
    components = json.loads(captured['parsed']['complex_components_json'] or '[]')
    used = components[0] if component_mode else captured['parsed']
    assert used['msa_remove_insertions'] is (True if remove is None else remove)
    assert captured['parsed']['seed'] == 0
    assert not Path(used['msa_path']).is_absolute()
    if component_mode:
        assert captured['parsed']['msa_path'] == ''
    assert receipt['settings']['msa_format']['origin'] == 'workflow_default'
    key = 'component:B.msa_remove_insertions' if component_mode else 'msa_remove_insertions'
    expected_origin = ('runner_default' if component_mode else 'workflow_default') if remove is None else 'request'
    assert receipt['settings'][key]['origin'] == expected_origin
    if component_mode == 'multi':
        assert captured['parsed']['sequence'] == ''
        assert captured['parsed']['msa_path'] == ''
        assert receipt['settings']['msa_path']['scope'] == 'primary'
        assert receipt['settings']['msa_path']['requested'] is None
        assert len(captured['parser_calls']) == len(captured['associations']) == len(receipt['sources']) == 2
        sources = {s['scope']: s for s in receipt['sources']}
        built = {c['id']: c for c in captured['associations']}
        manifests = {c['id']: c for c in captured['manifest_components']}
        for component, path, fmt, maximum in zip(components, [msa, msa2], ['a3m', 'stockholm'], [7, 3]):
            cid = component['id']
            source = sources['component:' + cid]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            assert source['requested_path'] == str(path)
            assert source['used_path'] == component['msa_path']
            assert not Path(source['used_path']).is_absolute()
            assert source['sha256'] == built[cid]['msa']['sha256'] == digest
            assert built[cid]['sequence'] == component['sequence']
            assert built[cid]['msa']['format'] == fmt
            assert built[cid]['msa']['kwargs']['max_sequences'] == maximum
            if fmt == 'a3m':
                assert built[cid]['msa']['kwargs']['remove_insertions'] is (True if remove is None else remove)
            else:
                assert built[cid]['msa']['kwargs']['remove_insertions'] is False
            assert manifests[cid]['msa']['path'] == source['used_path']
            assert manifests[cid]['msa']['sha256'] == digest
            assert receipt['settings']['component:' + cid + '.msa_max_sequences']['effective'] == maximum
        assert sources['component:B']['used_path'] != sources['component:C']['used_path']
        assert built['B']['msa']['sha256'] != built['C']['msa']['sha256']
    published = tmp_path / 'out/final/esmfold2/fixture/esmfold2_results/effective_settings.json'
    assert published.is_file(), 'sequence-scoped publication must not collide across candidates'


@pytest.mark.parametrize('params', [{'msa_remove_insertions': 'false'}, {'seed': True}, {'seed': -1}, {'msa_max_sequences': 0}, {'msa_path': 'missing'}, {'core_protein_scientific_contract': True}])
def test_api_invalid_marked_settings(tmp_path, monkeypatch, params):
    import sys
    sys.path.insert(0, str(ROOT / 'platform/api'))
    from services.nextflow import build_nextflow_command
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', '/bin/true')
    with pytest.raises(ValueError):
        build_nextflow_command('esmfold2', 'predict', {'core_protein_scientific_contract': 1, **params}, output_dir=str(tmp_path / 'out'), job_id='wp06')
