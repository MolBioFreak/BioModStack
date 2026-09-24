"""Preparation/transport tests only; no native model or science job is run."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.prep_boltzgen import preparation_parser, build_design_config
from scripts.lib.boltzgen_inputs import snapshot, input_identity


def pdb(path, chain='a'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(
        f'ATOM  {i:5d}  CA  ALA {chain}{number:4d}    {float(i):8.3f}{0.:8.3f}{0.:8.3f}  1.00 10.00           C\n'
        for i, number in enumerate([10, 11, 13], 1)) + 'END\n')
    return path


@pytest.mark.parametrize('mode,protocol,binder_id', [
    ('protein_binder', 'protein-anything', 'B'),
    ('peptide_binder', 'peptide-anything', 'B'),
    ('nanobody_binder', 'nanobody-anything', 'H'),
])
def test_public_modes_keep_distinct_target_and_protein_binder(tmp_path, mode, protocol, binder_id):
    target = pdb(tmp_path / 'target.pdb')
    args = preparation_parser().parse_args([
        '--generation_mode', mode, '--protocol', protocol, '--target_pdb', str(target),
        '--target_chains', 'a', '--binding_site_residues', 'a10,a13',
        '--output_yaml', str(tmp_path / 'boltzgen_input.yaml')])
    result = build_design_config(args)
    assert result['entities'][0] == {'file': {'path': str(target),
        'include': [{'chain': {'id': 'a'}}],
        'binding_types': [{'chain': {'id': 'a', 'binding': '1,3'}}]}}
    assert result['entities'][1]['protein']['id'] == binder_id
    assert not any('peptide' in entity for entity in result['entities'])


@pytest.mark.parametrize('protocol', ['protein-anything', 'peptide-anything', 'auto'])
def test_target_no_longer_omitted_without_public_mode_marker(tmp_path, protocol):
    args = preparation_parser().parse_args(['--protocol', protocol, '--target_pdb', str(pdb(tmp_path / 'target.pdb')),
        '--output_yaml', str(tmp_path / 'input.yaml'), '--secondary_structure', 'helix:1-2'])
    result = build_design_config(args)
    target, binder = result['entities']
    assert target['protein']['id'] == 'T'
    assert binder['protein']['id'] == 'A'
    assert 'secondary_structure' not in target['protein']
    assert binder['protein']['secondary_structure'] == {'helix': '1..2'}


def test_native_cif_context_and_explicit_scaffold_masks_are_not_reinterpreted(tmp_path):
    target = tmp_path / 'target.cif'
    target.write_text('data_transport_fixture\n')  # copied only, never parsed as scientific evidence
    args = preparation_parser().parse_args(['--generation_mode', 'protein_binder',
        '--target_pdb', str(target), '--target_chains', 'a1,B2',
        '--target_binding_positions', 'a1:2-3,B2:8',
        '--scaffold_path', 'scaffold.cif', '--scaffold_chain', 'h', '--scaffold_design_ranges', '4..6,9',
        '--output_yaml', str(tmp_path / 'input.yaml')])
    target_spec, scaffold = build_design_config(args)['entities']
    assert target_spec['file']['include'] == [{'chain': {'id': 'a1'}}, {'chain': {'id': 'B2'}}]
    assert target_spec['file']['binding_types'] == [
        {'chain': {'id': 'a1', 'binding': '2..3'}}, {'chain': {'id': 'B2', 'binding': '8'}}]
    assert scaffold == {'file': {'path': 'scaffold.cif', 'include': [{'chain': {'id': 'h'}}],
        'design': [{'chain': {'id': 'h', 'res_index': '4..6,9'}}]}}


def test_nested_scaffold_and_ligand_sources_are_in_the_snapshot(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    (source / 'nested').mkdir()
    pdb(source / 'nested/framework.pdb', 'H')
    pdb(source / 'target.pdb')
    (source / 'ligand.pdb').write_text('HETATM fixture\n')
    (source / 'nested/scaffold.yaml').write_text(yaml.safe_dump({'path': 'framework.pdb'}))
    config = source / 'boltzgen_input.yaml'
    config.write_text(yaml.safe_dump({'entities': [{'file': {'path': 'target.pdb'}},
        {'file': {'path': ['nested/scaffold.yaml']}}, {'ligand': {'path': 'ligand.pdb'}}]}))
    expected = {'boltzgen_input.yaml', 'target.pdb', 'nested/scaffold.yaml', 'nested/framework.pdb', 'ligand.pdb'}
    identity = snapshot(config, tmp_path / 'transport')
    assert set(identity) == expected
    assert input_identity(tmp_path / 'transport') == identity
    (source / 'nested/scaffold.yaml').write_text(yaml.safe_dump({'path': '../target.pdb'}))
    with pytest.raises(ValueError, match='contained relative'):
        input_identity(config)


def test_literal_nextflow_preparation_handles_matching_source_names(tmp_path):
    target = pdb(tmp_path / 'target/source.pdb')
    scaffold = pdb(tmp_path / 'scaffold/source.pdb', 'h')
    harness = tmp_path / 'prepare.nf'
    harness.write_text(f"include {{ PrepBoltzGenInput }} from '{ROOT}/modules/boltzgen.nf'\n"
        "workflow { PrepBoltzGenInput('', '', '80-120', 1, '', false, '', '', '', '', 'protein-anything', '', '', '', '', '', "
        "file(params.scaffold), file(params.ligand), file(params.dna), file(params.target)) }\n")
    params = {'code_root': str(ROOT), 'scaffold': str(scaffold), 'target': str(target),
        'ligand': str(ROOT / 'lib/NO_LIGAND_PDB'), 'dna': str(ROOT / 'lib/NO_DNA_STRUCT'),
        'boltzgen_generation_mode': 'protein_binder', 'boltzgen_scaffold_path': str(scaffold),
        'boltzgen_scaffold_chain': 'h', 'boltzgen_scaffold_design_ranges': '1..2',
        'boltzgen_target_chains': 'a'}
    params_file = tmp_path / 'params.json'; params_file.write_text(json.dumps(params))
    config = tmp_path / 'local.config'
    config.write_text("process.executor = 'local'\nprocess.shell = ['/bin/bash', '-euo', 'pipefail']\n")
    env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
        NXF_HOME=str(tmp_path / 'nxf'), PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    result = subprocess.run(['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'], 'run', str(harness),
        '-c', str(config), '-params-file', str(params_file), '-work-dir', str(tmp_path / 'work'), '-ansi-log', 'false'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    prepared = list((tmp_path / 'work').glob('*/*/boltzgen_prepared'))
    assert len(prepared) == 1
    assert (prepared[0] / 'target/source.pdb').read_bytes() == target.read_bytes()
    assert (prepared[0] / 'backbone/source.pdb').read_bytes() == scaffold.read_bytes()
    spec = yaml.safe_load((prepared[0] / 'boltzgen_input.yaml').read_text())
    assert spec['entities'][0]['file']['path'] == 'target/source.pdb'
    assert spec['entities'][1]['file']['path'] == 'backbone/source.pdb'

@pytest.mark.parametrize('mode', ['protein_binder', 'peptide_binder', 'nanobody_binder'])
def test_materializer_keeps_sources_once_and_transports_native_specs(tmp_path, mode, monkeypatch):
    from scripts.lib import boltzgen_inputs as transport
    target = pdb(tmp_path / 'sources/target.pdb')
    scaffold = pdb(tmp_path / 'sources/scaffold.pdb', 'H')
    params = {'boltzgen_generation_mode': mode, 'boltzgen_target_pdb_path': str(target),
        'boltzgen_protocol': 'auto'}
    if mode == 'nanobody_binder':
        params['boltzgen_nanobody_scaffold_specs'] = json.dumps([
            {'name': 'one', 'spec': {'path': str(scaffold), 'include': [{'chain': {'id': 'H'}}],
                'design': [{'chain': {'id': 'H', 'res_index': '1..2'}}]}},
            {'name': 'two', 'spec': {'path': str(scaffold), 'include': [{'chain': {'id': 'H'}}],
                'design': [{'chain': {'id': 'H', 'res_index': '2..3'}}]}}])
    else:
        params.update(boltzgen_scaffold_path=str(scaffold), boltzgen_scaffold_chain='H', boltzgen_scaffold_design_ranges='1..2')
    copied = []
    original_copy = transport.shutil.copyfile
    def counted(source, destination):
        copied.append(source)
        return original_copy(source, destination)
    monkeypatch.setattr(transport.shutil, 'copyfile', counted)
    output = tmp_path / 'prepared'
    result = transport.materialize_generation_input(params, output, allowed_input_roots=[tmp_path / 'sources'])
    assert copied == [target, scaffold]
    assert result['boltzgen_yaml_config'] == str(output)
    assert result['boltzgen_prepared_sha256'] == transport.identity_digest(transport.input_identity(output))
    payload = yaml.safe_load((output / 'boltzgen_input.yaml').read_text())
    target_name = payload['entities'][0]['file']['path']
    assert (output / target_name).read_bytes() == target.read_bytes()
    before = transport.input_identity(output)
    target.unlink(); scaffold.unlink()
    transport.snapshot(output / 'boltzgen_input.yaml', tmp_path / 'returned')
    assert transport.input_identity(tmp_path / 'returned') == before
    if mode == 'nanobody_binder':
        assert payload['entities'][1]['file']['path'] == ['one_1.yaml', 'two_2.yaml']
        assert yaml.safe_load((output / 'one_1.yaml').read_text())['design'][0]['chain']['res_index'] == '1..2'


@pytest.mark.parametrize('prepared', [False, True])
def test_public_workflow_zero_yield_graph_with_explicit_non_science_runner(tmp_path, prepared):
    # Copy only for process-fixture substitution. The production public workflow
    # runs unchanged except its include location; real Prep is still executed.
    module = (ROOT / 'modules/boltzgen.nf').read_text()
    first = module.index('process RunBoltzGen {')
    last = module.index('process FilterBoltzGen {', first)
    stub = """process RunBoltzGen {
 input:
 path yaml_configs
 output:
 path 'output/designs/*.pdb', emit: pdbs, optional: true
 path 'output/designs/*.json', emit: jsons, optional: true
 path 'fixture.log'
 script:
 'mkdir -p output/designs; touch fixture.log'
}
"""
    (tmp_path / 'module.nf').write_text(module[:first] + stub + module[last:])
    workflow = (ROOT / 'workflows/boltzgen_generation.nf').read_text().replace("'../modules/boltzgen.nf'", "'./module.nf'")
    (tmp_path / 'public.nf').write_text(workflow)
    target = pdb(tmp_path / 'target.pdb')
    params = {'code_root': str(ROOT), 'out_dir': str(tmp_path / 'output'),
        'boltzgen_generation_mode': 'protein_binder', 'boltzgen_protocol': 'protein-anything',
        'boltzgen_target_pdb_path': str(target)}
    if prepared:
        from scripts.lib.boltzgen_inputs import materialize_generation_input
        params.update(materialize_generation_input(params, tmp_path / 'prepared', allowed_input_roots=[tmp_path]))
    (tmp_path / 'params.json').write_text(json.dumps(params))
    (tmp_path / 'local.config').write_text("process.executor = 'local'\nprocess.shell = ['/bin/bash', '-euo', 'pipefail']\n")
    env = dict(os.environ, NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', NXF_HOME=str(tmp_path / 'nxf'),
        PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    result = subprocess.run(['java', '-jar', os.environ['BMS_TEST_NEXTFLOW_JAR'], 'run', str(tmp_path / 'public.nf'),
        '-c', str(tmp_path / 'local.config'), '-params-file', str(tmp_path / 'params.json'),
        '-work-dir', str(tmp_path / 'work'), '-with-trace', str(tmp_path / 'trace.txt'), '-ansi-log', 'false'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    trace = (tmp_path / 'trace.txt').read_text()
    assert 'RunBoltzGen' in trace
    assert ('PrepBoltzGenInput' in trace) is not prepared
    assert 'FilterBoltzGen' not in trace


def test_generation_materializer_keeps_existing_source_containment(tmp_path):
    from scripts.lib.boltzgen_inputs import materialize_generation_input
    target = pdb(tmp_path / 'outside/target.pdb')
    approved = tmp_path / 'approved'; approved.mkdir()
    request = {'boltzgen_generation_mode': 'protein_binder', 'boltzgen_target_pdb_path': str(target)}
    with pytest.raises(ValueError, match='outside approved'):
        materialize_generation_input(request, tmp_path / 'refused', allowed_input_roots=[approved])
    link = approved / 'link.pdb'; link.symlink_to(target)
    request['boltzgen_target_pdb_path'] = str(link)
    with pytest.raises(ValueError, match='symlink'):
        materialize_generation_input(request, tmp_path / 'refused-link', allowed_input_roots=[approved])


@pytest.mark.parametrize('kind', ['ligand', 'ntp', 'fixed_ligand', 'backbone', 'dna'])
def test_legacy_preparation_meanings_are_not_reclassified(tmp_path, kind):
    extra = {
        'ligand': ['--ligand_smiles', 'O'],
        'ntp': ['--ntp_type', 'ATP'],
        'fixed_ligand': ['--ligand_pdb', str(pdb(tmp_path / 'ligand.pdb'))],
        'backbone': ['--input_pdb', str(pdb(tmp_path / 'backbone.pdb')), '--ntp_type', 'ATP'],
        'dna': ['--protein_sequence', 'AAA', '--dna_template_seq', 'AAA'],
    }[kind]
    args = preparation_parser().parse_args(extra + ['--output_yaml', str(tmp_path / 'input.yaml')])
    result = build_design_config(args)
    if kind in ('ligand', 'ntp'):
        assert 'smiles' in result['entities'][1]['ligand']
    elif kind == 'fixed_ligand':
        assert result['entities'][1]['ligand']['path'].endswith('ligand.pdb')
    elif kind == 'backbone':
        # Existing omission is recorded for the parent, not silently converted
        # into a different docking protocol in this generation implementation.
        assert len(result['entities']) == 1
        assert result['entities'][0]['protein']['sequence'] == 'AAA'
    else:
        assert result['entities'] == [{'protein': {'id': 'A', 'sequence': 'AAA'}}, {'dna': {'id': 'B', 'sequence': 'AAA'}}]
