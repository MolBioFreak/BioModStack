"""Review regressions: owned nested chemistry, execution assets and inline MSA."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.protenix_chemistry import validate_chemistry, parameter_schema
from scripts.prepare_protenix_msa import build_native_protenix_input
from scripts.run_protenix_inference import prepare_inline_msa_input
from test_protenix_chemistry_transport import science, compiler_environment

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('key', ['modfications', 'glycans', 'formal_charge', 'templatePath'])
def test_unknown_component_science_is_not_discarded(key):
    params = science()
    params['complex_components'][0][key] = []
    with pytest.raises(ValueError, match=key):
        validate_chemistry(params)
    from model_registry import get_registry
    errors = get_registry().validate_job_params('protenix', 'complex', params)
    assert any(key in error for error in errors)


def test_explicit_display_metadata_and_unowned_top_level_settings():
    params = science()
    params['complex_components'][0].update(name='Display label', id='editor-label')
    params['unrelated_existing_setting'] = {'opaque': True}
    validate_chemistry(params)
    assert parameter_schema()['$defs']['Component']['additionalProperties'] is False
    native = build_native_protenix_input(seeds=[42], complexes=[('display', {'components': params['complex_components']})])
    assert native[0]['sequences'][0]['proteinChain']['id'] == ['A', 'B']
    assert 'name' not in native[0]['sequences'][0]['proteinChain']


@pytest.mark.parametrize('section,index,key,value', [
    ('covalent_bonds', 0, 'left_entity', 6),
    ('covalent_bonds', 0, 'left_copy', 3),
    ('covalent_bonds', 0, 'right_position', 2),
    ('contact', 0, 'entity1', 6),
    ('contact', 0, 'copy2', 2),
    ('contact', 0, 'position2', 3),
    ('contact', 0, 'min_distance', 9.0),
])
def test_endpoint_crossrefs(section, index, key, value):
    params = science()
    records = params[section] if section == 'covalent_bonds' else params['constraint'][section]
    records[index][key] = value
    with pytest.raises(ValueError):
        validate_chemistry(params)


def test_native_copy_selection_cardinality_and_same_chain_conditions():
    params = science()
    del params['covalent_bonds'][0]['left_copy']
    with pytest.raises(ValueError, match='equal copy counts'):
        validate_chemistry(params)
    del params['covalent_bonds'][0]['right_copy']
    validate_chemistry(params)  # Both expanded entities have two copies.
    params['constraint']['contact'][0]['entity2'] = 1
    with pytest.raises(ValueError, match='same chain'):
        validate_chemistry(params)
    params = science()
    params['constraint']['pocket']['contact_residues'][0].update(entity=1, copy=2)
    with pytest.raises(ValueError, match='binder chain'):
        validate_chemistry(params)


@pytest.mark.parametrize('inline', ['', '>q\nAC\n>hit\nAS\n'])
@pytest.mark.parametrize('prepared_manifest', [False, True])
def test_inline_cli_and_manifest_keeps_chemistry(tmp_path, inline, prepared_manifest):
    from services.msa_preparation import export_protenix_inputs
    from biomodstack_msa_handoff import digest
    original = [{'name': 'inline', 'sequences': [{'proteinChain': {
        'sequence': 'AC', 'count': 2, 'id': ['A', 'B'], 'unpairedMsa': inline,
        'modifications': [{'ptmPosition': 1, 'ptmType': 'CCD_AIB'}]}}]}]
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(original))
    output = tmp_path / 'out.json'
    command = [sys.executable, str(ROOT / 'scripts/prepare_protenix_msa.py'), '--input_json', str(source),
        '--output_json', str(output), '--out_dir', str(tmp_path / 'execution'), '--backend', 'colabfold_api']
    if prepared_manifest:
        bundle = tmp_path / 'prepared'
        export_protenix_inputs(original, bundle, {}, {'backend': 'supplied'})
        command += ['--prepared-inputs', str(bundle), '--prepared-sha256', digest((bundle / 'msa-inputs.json').read_bytes())]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    native = json.loads(output.read_text())
    chain = native[0]['sequences'][0]['proteinChain']
    assert Path(chain.pop('unpairedMsaPath')).read_text() == (inline or '>query\nAC\n')
    assert native == original
    assert json.loads(source.read_text()) == original
    rebound = json.loads(Path(prepare_inline_msa_input(str(source), str(tmp_path / 'wrapper'))).read_text())
    rebound[0]['sequences'][0]['proteinChain'].pop('unpairedMsaPath')
    assert rebound == original


@pytest.mark.parametrize('use_template', [True, False])
def test_explicit_template_flag_and_native_hits_survive_conditioning(use_template, tmp_path, compiler_environment):
    from services.nextflow import compile_nextflow_invocation
    from scripts.prepare_protenix_msa import load_native_protenix_input
    hit_file = tmp_path / 'native.hhr'
    hit_file.write_text('native template hit fixture\n')
    params = {'complex_components': [{'type': 'protein', 'sequence': 'AC', 'templatesPath': str(hit_file)}],
              'protenix_use_msa': False, 'protenix_use_template': use_template}
    invocation = compile_nextflow_invocation('protenix', 'complex', params, str(tmp_path / 'out'))
    invocation.materialize_inputs(tmp_path / 'out')
    assert invocation.native_parameters['protenix_use_template'] is use_template
    native = load_native_protenix_input(invocation.native_parameters)
    source = tmp_path / 'native.json'
    source.write_text(json.dumps(native))
    conditioned = tmp_path / 'conditioned.json'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_protenix_constraints.py'),
        '--input_json', str(source), '--output_json', str(conditioned), '--auto-pocket-if-missing'],
        text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(conditioned.read_text()) == native
    assert native[0]['sequences'][0]['proteinChain']['templatesPath'] == str(hit_file)


@pytest.mark.parametrize('use_msa', [True, False])
def test_declared_native_execution_assets(use_msa, tmp_path, compiler_environment):
    from services.nextflow import compile_nextflow_invocation
    from native_components import PROCESS_CONTRACTS
    params = {'complex_components': [{'type': 'protein', 'sequence': 'AC', 'unpairedMsa': ''}],
              'protenix_use_msa': use_msa, 'msa_cache_only': True}
    invocation = compile_nextflow_invocation('protenix', 'complex', params, str(tmp_path))
    from model_registry import selected_execution_metadata
    from services.nextflow import MODEL_MODE_WORKFLOW_ENTRYPOINTS
    metadata = selected_execution_metadata('protenix', 'complex', invocation.native_parameters,
        MODEL_MODE_WORKFLOW_ENTRYPOINTS[('protenix', 'complex')])
    paths = {item.relative_path for item in metadata.dependencies}
    assert 'scripts/protenix_chemistry.py' in paths
    assert 'scripts/prepare_protenix_msa.py' in paths
    # Execute the converter from a detached source-only support tree. Helpers
    # come from the actual selected descriptors; scripts/lib is the existing
    # shared support package carried by the bms-source archive.
    import shutil
    support = tmp_path / 'source-only'
    for relative in paths:
        if relative and relative.startswith('scripts/') and (ROOT / relative).is_file():
            target = support / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
    shutil.copytree(ROOT / 'scripts/lib', support / 'scripts/lib', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(ROOT / 'scripts/local_msa_runtime.py', support / 'scripts/local_msa_runtime.py')
    program = """
import sys
sys.path.insert(0, sys.argv[1])
from prepare_protenix_msa import build_native_protenix_input
import protenix_chemistry
assert protenix_chemistry.__file__.startswith(sys.argv[1])
task = build_native_protenix_input(seeds=[42], complexes=[('detached', {'components': [
    {'type': 'protein', 'sequence': 'AC', 'modifications': [{'ptmPosition': 1, 'ptmType': 'CCD_AIB'}]}]})])
assert task[0]['sequences'][0]['proteinChain']['modifications'][0]['ptmType'] == 'CCD_AIB'
print('declared detached source converter: PASS')
"""
    result = subprocess.run([sys.executable, '-I', '-c', program, str(support / 'scripts')],
        cwd=support, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'declared detached source converter: PASS'
    for key, value in PROCESS_CONTRACTS.items():
        if 'scripts/prepare_protenix_msa.py' in value[3]:
            assert 'scripts/protenix_chemistry.py' in value[3], key
