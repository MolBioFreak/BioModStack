"""Repair acceptance at the real CAD preparer/portable/native composition boundaries."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts'))
from lib.portable_inputs import discover_native_input_references
from lib.laproteina_contract import MOTIF_CONTROLS
from run_laproteina_inference import _configure_laproteina


def prepare(tmp_path, backend='disco', task='custom_json', **args):
    output = tmp_path / 'request.json'
    cmd = [sys.executable, str(ROOT / 'scripts/prep_protein_cad_request.py'), '--backend', backend,
           '--task', task, '--num-designs', '1', '--target-lengths', '40', '--output', str(output),
           '--input-dir', str(tmp_path / 'prepared')]
    for key, value in args.items():
        cmd += ['--' + key.replace('_', '-'), json.dumps(value) if key.endswith('_json') else str(value)]
    result = subprocess.run(cmd, text=True, capture_output=True)
    return result, json.loads(output.read_text()) if result.returncode == 0 else None


@pytest.mark.parametrize('jobs', [[], [{'name': 'one', 'sequences': []}]])
@pytest.mark.parametrize('fallback', ['missing.json', 'invalid.json'])
def test_active_jobs_ignore_fallback_and_shorthand(tmp_path, jobs, fallback):
    (tmp_path / 'invalid.json').write_text('not JSON')
    params = dict(pcad_backend='disco', pcad_disco_jobs_json=jobs,
                  pcad_disco_input_json_path=str(tmp_path / fallback),
                  pcad_disco_ligand_sdf=str(tmp_path / 'missing.sdf'))
    result, request = prepare(tmp_path, disco_jobs_json=jobs,
        disco_input_json_path=params['pcad_disco_input_json_path'], disco_ligand_sdf=params['pcad_disco_ligand_sdf'])
    assert result.returncode == 0, result.stderr
    assert json.loads(Path(request['disco']['input_json_path']).read_text()) == jobs
    assert discover_native_input_references('protein_cad_experimental', 'generate', params, [],
        output_dir=tmp_path, allowed_roots=[tmp_path]) == []


def test_import_owner_not_fallback_and_only_edited_dependencies(tmp_path):
    a = tmp_path / 'A'; a.mkdir()
    b = tmp_path / 'B'; b.mkdir()
    owner = a / 'jobs.json'
    owner.write_text('[{"name":"original","sequences":[{"ligand":{"ligand":"FILE_removed.sdf","count":1}}]}]')
    ligand = a / 'active.sdf'; ligand.write_bytes(b'owner A molecule\n')
    (b / 'active.sdf').write_bytes(b'wrong molecule')
    fallback = b / 'bad.json'; fallback.write_text('invalid')
    jobs = [{'name': 'edited', 'sequences': [{'ligand': {'ligand': 'FILE_active.sdf', 'count': 1}}]}]
    params = dict(pcad_backend='disco', pcad_disco_jobs_json=jobs,
                  pcad_disco_jobs_source_path=str(owner), pcad_disco_input_json_path=str(fallback))
    refs = discover_native_input_references('protein_cad_experimental', 'generate', params, [],
        output_dir=tmp_path, allowed_roots=[tmp_path])
    assert {r['source_path'] for r in refs} == {str(owner), str(ligand)}
    result, request = prepare(tmp_path, disco_jobs_json=jobs, disco_jobs_source_path=owner, disco_input_json_path=fallback)
    assert result.returncode == 0, result.stderr
    bound = json.loads(Path(request['disco']['input_json_path']).read_text())
    assert Path(bound[0]['sequences'][0]['ligand']['ligand'][5:]).read_bytes() == ligand.read_bytes()
    assert (tmp_path / 'prepared/original_disco_jobs/jobs.json').read_bytes() == owner.read_bytes()
    # Removing the override intentionally reactivates the selected invalid file.
    result, _ = prepare(tmp_path, disco_input_json_path=fallback)
    assert result.returncode != 0 and 'JSONDecodeError' in result.stderr


def test_selected_file_precedes_inactive_shorthand(tmp_path):
    source = tmp_path / 'jobs.json'; source.write_text('[]')
    result, request = prepare(tmp_path, task='ligand_conditioned', disco_input_json_path=source,
                              disco_ligand_sdf=tmp_path / 'missing.sdf')
    assert result.returncode == 0, result.stderr
    assert json.loads(Path(request['disco']['input_json_path']).read_text()) == []
    assert 'compiled_input_json' not in request['disco']


@pytest.mark.parametrize('primary,override,expected', [('', {'contig_string':'A1-2'}, 'A1-2'),
    ('A3-4', {'contig_string':'A1-2'}, 'A1-2'), ('A1-2', {}, 'A1-2'), ('A1-2', {'contig_string':''}, None)])
def test_effective_la_prerequisite(tmp_path, primary, override, expected):
    source = tmp_path / 'source.pdb'
    source.write_text('ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 20.00           C\nEND\n')
    result, request = prepare(tmp_path, 'laproteina', 'motif_scaffolding', laproteina_preset='motif_idx_aa',
        laproteina_motif_pdb=source, laproteina_contig_string=primary, laproteina_settings_json=override)
    if expected is None:
        assert result.returncode != 0 and 'requires either' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert request['laproteina']['contig_string'] == expected
        assert request['requested_primary']['laproteina']['contig_string'] == primary
        assert request['native_settings_overrides']['laproteina'] == override


@pytest.fixture
def native_repo():
    value = os.environ.get('LAPROTEINA_REPO')
    if not value:
        pytest.skip('Set LAPROTEINA_REPO to adopted native source for real CPU composition')
    pytest.importorskip('hydra')
    return Path(value)


def config_hashes(repo):
    return {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (repo / 'configs').rglob('*') if p.is_file()}


@pytest.mark.parametrize('override', [{}, {'contig_string': 'A1-2'}, {'segment_order':'B'},
    {'atom_selection_mode':'ca'}, {'motif_atom_spec':'A1: [CA]'}, {'motif_atom_spec':None},
    {'motif_min_length':42}, {'motif_min_length':None}, {'motif_max_length':60}, {'motif_max_length':None}])
def test_named_native_composition_preserves_omission_and_applies_authored(tmp_path, native_repo, override):
    before = config_hashes(native_repo)
    result, request = prepare(tmp_path, 'laproteina', 'motif_scaffolding', laproteina_preset='motif_idx_aa',
        laproteina_motif_task_name='1YCR_AA', laproteina_settings_json=override)
    assert result.returncode == 0, result.stderr
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    with initialize_config_dir(config_dir=str(native_repo / 'configs'), version_base='1.3'):
        original = compose(config_name='inference_motif_idx_aa')
    native = OmegaConf.to_container(original.generation.dataset.motif_dict_cfg['1YCR_AA'], resolve=False)
    actual = _configure_laproteina(request, tmp_path / 'composition')['receipt']['effective_config']
    assert actual['generation']['dataset']['motif_dict_cfg']['1YCR_AA'] == {**native, **override}
    assert actual['ckpt_name'] == 'LD4_motif_idx_aa.ckpt'
    assert actual['autoencoder_ckpt_path'].endswith('AE3_motif.ckpt')
    assert config_hashes(native_repo) == before


def test_named_projection_does_not_promote_old_primary_defaults():
    from scripts.lib.protein_cad_settings import bind_new_cad_settings, settings_projection
    primary = {'backend':'laproteina', 'laproteina_motif_task_name':'1YCR_AA',
               'laproteina_atom_selection_mode':'all_atom', 'laproteina_segment_order':'A',
               'laproteina_contig_string':'', 'laproteina_motif_min_length':'', 'laproteina_num_steps':400}
    for params in (primary, bind_new_cad_settings('protein_cad_experimental', 'generate', primary)):
        projected = settings_projection(params)['pcad_laproteina_settings_json']
        assert not (set(projected) & set(MOTIF_CONTROLS))
    explicit = {**primary, 'pcad_laproteina_settings_json':{'atom_selection_mode':'ca', 'motif_min_length':None}}
    assert settings_projection(explicit)['pcad_laproteina_settings_json'] == {'num_steps':400, 'atom_selection_mode':'ca', 'motif_min_length':None}


def test_metadata_covers_exported_leaves_without_fake_defaults():
    from scripts.lib.laproteina_contract import settings_schema as la_schema
    from scripts.lib.disco_contract import settings_schema as disco_schema
    la = la_schema(); disco = disco_schema()
    leaves = list(la['properties'].values()) + [v for v in disco['properties'].values() if '$ref' not in v]
    leaves += [leaf for definition in disco['$defs'].values() for leaf in definition.get('properties', {}).values()]
    for leaf in leaves:
        assert leaf['title'] and leaf['description'] and leaf['x-native-key'] and leaf['x-applicability']
        assert 'default' not in leaf
    assert 'does not read' in disco['$defs']['SequenceSampling']['properties']['score_temp']['description']


def test_custom_native_composition_uses_sparse_contig(tmp_path, native_repo):
    source = tmp_path / 'source.pdb'; source.write_text('END\n')
    result, request = prepare(tmp_path, 'laproteina', 'motif_scaffolding', laproteina_preset='motif_idx_aa',
        laproteina_motif_pdb=source, laproteina_contig_string='A9-10',
        laproteina_settings_json={'contig_string':'A1-2', 'motif_atom_spec':None, 'self_cond':False})
    assert result.returncode == 0, result.stderr
    config = _configure_laproteina(request, tmp_path / 'composition')['receipt']['effective_config']
    assert config['generation']['dataset']['motif_dict_cfg']['BMS_CUSTOM']['contig_string'] == 'A1-2'
    assert config['generation']['args']['self_cond'] is False
