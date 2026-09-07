"""Native Nextflow with extracted production defaults; instrumented argv, no physics."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_openmm(tmp_path, monkeypatch, requested, *, marked=True, transport=True):
    jar = Path('/home/dalab/.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar')
    if not jar.is_file():
        pytest.skip('installed native Nextflow jar unavailable')
    pdb = tmp_path / 'input.pdb'
    pdb.write_text('NON_MODEL_FIXTURE_ONLY\n')
    fixture = tmp_path / 'fixture.nf'
    fixture.write_text(f'''nextflow.enable.dsl=2
include {{ AntibodyOpenMMRefinement }} from '{ROOT}/modules/antibody_openmm_refinement.nf'
workflow {{
    AntibodyOpenMMRefinement(Channel.of(tuple([id:'fixture'], file('{pdb}'))))
}}
''')
    # Exact production params definitions, without production executors/images/profiles.
    defaults = re.findall(r'^\s+openmm_\w+\s*=.*$', (ROOT / 'nextflow.config').read_text(), re.M)
    assert any('openmm_cdr_only = true' in line for line in defaults)
    config = tmp_path / 'nextflow.config'
    config.write_text('params {\n' + '\n'.join(defaults) + '\n}\nprocess.executor="local"\nprocess.cpus=1\nprocess.memory="256 MB"\ndocker.enabled=false\nsingularity.enabled=false\n' + f'process.ext.scripts_root="{ROOT}/scripts"\n')
    params = {'code_root': str(tmp_path), 'out_dir': str(tmp_path / 'out'), **requested}
    if marked:
        params['core_protein_scientific_contract'] = 1
    if marked and transport:
        sys.path.insert(0, str(ROOT / 'platform/api'))
        from services.nextflow import build_nextflow_command
        monkeypatch.setenv('BMS_NEXTFLOW_BIN', '/bin/true')
        # Normalized launch values and raw trusted request presence are distinct.
        normalized = {'openmm_cdr_only': True, 'openmm_force_field': 'amber14sb',
                      'openmm_antibody_chain': 'H', 'openmm_max_iterations': 500,
                      'openmm_energy_tolerance': 10.0, **params}
        command = build_nextflow_command('antibody_denovo', 'design', normalized,
            output_dir=str(tmp_path / 'out'), job_id='wp06', requested_params=requested)
        params['openmm_requested_settings_json'] = command[command.index('--openmm_requested_settings_json') + 1]
    request = tmp_path / 'params.json'
    request.write_text(json.dumps(params))
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    shim = bindir / 'python3'
    shim.write_text('#!/bin/sh\nif [ "$1" = "' + str(ROOT / 'scripts/relax_openmm.py') + '" ] || [ "$1" = "/scripts/relax_openmm.py" ]; then\nshift\nexec /usr/bin/python3 "' + str(ROOT / 'tests/fixtures/wp06_openmm_capture.py') + '" "$@"\nfi\nexec /usr/bin/python3 "$@"\n')
    shim.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir)+':'+os.environ['PATH'], NXF_OFFLINE='true',
        NXF_HOME=str(tmp_path / 'nxf-home'), NXF_ASSETS=str(tmp_path / 'assets'),
        NXF_TEMP=str(tmp_path / 'nxf-temp'), JAVA_TOOL_OPTIONS='-XX:ActiveProcessorCount=2', PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run(['java', '-jar', str(jar), '-C', str(config), 'run', str(fixture),
        '-params-file', str(request), '-work-dir', str(tmp_path / 'work')], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=90)
    captures = list((tmp_path / 'work').glob('*/*/relaxed/capture.json'))
    if marked and result.returncode == 0 and captures:
        from types import SimpleNamespace
        from services.core_protein_execution_settings import prepare_receipt
        for capture in captures:
            verified = prepare_receipt(SimpleNamespace(provenance={'core_protein_requested_params': requested}),
                tmp_path, capture.parent/'effective_settings.json')
            assert verified['receipt']['model'] == 'openmm'
    return result, captures


@pytest.mark.parametrize('value,expected,origin', [(False, False, 'request'), (True, True, 'request'), (None, True, 'workflow_default')])
def test_antibody_cdr_transport(tmp_path, monkeypatch, value, expected, origin):
    requested = {} if value is None else {'openmm_cdr_only': value}
    result, captures = run_openmm(tmp_path, monkeypatch, requested)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(captures) == 1
    captured = json.loads(captures[0].read_text())
    receipt = json.loads((captures[0].parent / 'effective_settings.json').read_text())
    assert ('--cdr_only' in captured['argv']) is expected
    assert receipt['settings']['cdr_only'] == {'requested': value, 'effective': expected, 'origin': origin, 'scope': 'antibody'}
    assert receipt['argv'] == captured['argv']
    published = list((tmp_path/'out/run/openmm/relaxation').rglob('*effective_settings.json'))
    assert len(published) == 1, 'actual execution receipt not published'
    assert published[0].read_bytes() == (captures[0].parent/'effective_settings.json').read_bytes()
    if value is False and os.environ.get('BMS_WP06_OPENMM_RECEIPT'):
        Path(os.environ['BMS_WP06_OPENMM_RECEIPT']).write_bytes(published[0].read_bytes())
    for key in ('force_field', 'antibody_chain', 'restraint_mode'):
        assert receipt['settings'][key]['requested'] is None
        assert receipt['settings'][key]['origin'] == 'workflow_default'
    for key in ('max_iterations', 'energy_tolerance'):
        assert receipt['settings'][key]['requested'] is None
        assert receipt['settings'][key]['origin'] == 'compute_tier'
    assert receipt['settings']['max_iterations']['effective'] == 100
    assert type(receipt['settings']['energy_tolerance']['effective']) is float


@pytest.mark.parametrize('key', ['compute_tier', 'restraint_mode', 'antibody_chain', 'force_field'])
@pytest.mark.parametrize('value', [False, 0, ''])
def test_invalid_falsey_wrapper_value_never_executes(tmp_path, monkeypatch, key, value):
    result, captures = run_openmm(tmp_path, monkeypatch, {'openmm_' + key: value})
    assert result.returncode != 0
    assert 'invalid openmm_' + key in result.stdout + result.stderr
    assert not captures


def test_explicit_settings_and_tier_derivation(tmp_path, monkeypatch):
    request = {'openmm_force_field': 'charmm36m', 'openmm_antibody_chain': 'L',
               'openmm_compute_tier': 'standard', 'openmm_max_iterations': 23,
               'openmm_energy_tolerance': 2.5}
    result, captures = run_openmm(tmp_path, monkeypatch, request)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((captures[0].parent / 'effective_settings.json').read_text())
    for key in ('force_field', 'antibody_chain'):
        assert receipt['settings'][key]['requested'] == request['openmm_' + key]
        assert receipt['settings'][key]['origin'] == 'request'
    for key, effective in [('max_iterations', 500), ('energy_tolerance', 10.0)]:
        assert receipt['settings'][key]['requested'] == request['openmm_' + key]
        assert receipt['settings'][key]['effective'] == effective
        assert receipt['settings'][key]['origin'] == 'compute_tier'
        assert receipt['settings'][key]['derived_from'] == {'requested': 'standard', 'effective': 'standard', 'origin': 'request'}


def test_null_settings_default_without_false_request_origin(tmp_path, monkeypatch):
    result, captures = run_openmm(tmp_path, monkeypatch, {'openmm_' + key: None
        for key in ('compute_tier', 'restraint_mode', 'antibody_chain', 'force_field', 'cdr_only')})
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((captures[0].parent / 'effective_settings.json').read_text())
    assert receipt['settings']['cdr_only']['effective'] is True
    for key in ('cdr_only', 'restraint_mode', 'antibody_chain', 'force_field'):
        assert receipt['settings'][key]['requested'] is None
        assert receipt['settings'][key]['origin'] == 'workflow_default'


def test_request_cannot_forge_internal_origin_transport(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'platform/api'))
    from services.nextflow import build_nextflow_command
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', '/bin/true')
    params = {'core_protein_scientific_contract': 1, 'openmm_requested_settings_json':
              '{"openmm_cdr_only":false}', 'openmm_cdr_only': True}
    command = build_nextflow_command('antibody_denovo', 'design', params,
        output_dir=str(tmp_path / 'out'), requested_params={})
    assert json.loads(command[command.index('--openmm_requested_settings_json') + 1]) == {}
    command = build_nextflow_command('antibody_denovo', 'design', params,
        output_dir=str(tmp_path / 'out'))
    assert '--openmm_requested_settings_json' not in command
    assert params['openmm_requested_settings_json'] == '{"openmm_cdr_only":false}'


def test_missing_trusted_presence_fails_closed(tmp_path, monkeypatch):
    result, captures = run_openmm(tmp_path, monkeypatch, {}, transport=False)
    assert result.returncode != 0
    assert 'missing OpenMM request provenance' in result.stdout + result.stderr
    assert not captures


def test_unmarked_falsey_legacy_unchanged(tmp_path, monkeypatch):
    result, captures = run_openmm(tmp_path, monkeypatch, {'openmm_compute_tier': False,
        'openmm_restraint_mode': '', 'openmm_antibody_chain': 0, 'openmm_force_field': False,
        'openmm_cdr_only': False}, marked=False)
    assert result.returncode == 0, result.stdout + result.stderr
    captured = json.loads(captures[0].read_text())
    assert '--cdr_only' in captured['argv']
    assert not (captures[0].parent / 'effective_settings.json').exists()
