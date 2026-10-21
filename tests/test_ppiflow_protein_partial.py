"""Protein partial transport; science remains explicitly inert."""
import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_ppiflow_protein_partial as partial
import run_ppiflow_generation as observation
from test_ppiflow_target_producer_mapping import native, native_io, tensor, definitions, runner, torch


def actual_dataset_row(native, features, sample=0, motif=None):
    tree = ast.parse((native / 'data/datasets_binder_partial.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PpiTestPartialDataset')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__getitem__')
    isolated = ast.ClassDef(name=cls.name, bases=[], keywords=[], body=[method], decorator_list=[])
    env = {'torch': torch, 'np': types.SimpleNamespace(nonzero=lambda x: np.argwhere(x)), '_bms_partial': partial}
    exec(partial.instrument(ast.unparse(ast.fix_missing_locations(ast.Module(body=[isolated], type_ignores=[]))), 'native-dataset', dataset=True), env)
    obj = env[cls.name]()
    obj.all_sample_ids = [({'pdb_name': 'selected_pair', 'original_index': 0}, sample, 2)]
    obj.process_csv_row = lambda row: features
    obj.setup_binder_mask = lambda feats: (feats['chain_idx'] == 1).view(type(tensor([])))
    obj.setup_target_hotspots = lambda feats: tensor(np.zeros(len(feats['chain_idx'])))
    obj.post_process_feats = lambda feats: feats
    obj._dataset_cfg = obj.dataset_cfg = types.SimpleNamespace(motif=motif, scaffold_contig=None)
    return obj[0]


def pdb_text():
    return ''.join(f'ATOM  {i:5d}  CA  {aa:3s} {chain}{number:4d}{icode}   {float(i):8.3f}{2.:8.3f}{3.:8.3f}  1.00  0.00           C\n'
                   for i, (chain, number, icode, aa) in enumerate([('r', 7, ' ', 'GLY'), ('r', 19, 'A', 'SER'), ('R', 7, ' ', 'TRP'), ('R', 19, 'B', 'LEU')], 1)) + 'END\n'


def load_authority():
    spec = importlib.util.spec_from_file_location('partial_authority', ROOT / 'platform/api/services/ppiflow_partial.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_task_defaults_replay():
    api = load_authority()
    assert api.apply_partial_defaults({})['ppiflow_start_t'] == .8
    protein = api.apply_partial_defaults({'ppiflow_task': 'protein_binder_partial'})
    assert protein['ppiflow_start_t'] == .15
    assert protein['ppiflow_samples_per_target'] == 100
    assert protein['ppiflow_checkpoint'] == 'binder'
    assert api.apply_partial_defaults({'ppiflow_task': 'nanobody_partial'})['ppiflow_checkpoint'] == 'nanobody'
    assert api.apply_partial_defaults(dict(protein, ppiflow_start_t=.37))['ppiflow_start_t'] == .37
    assert not any(k in protein for k in ['cdr_positions_by_loop', 'ppiflow_retry_limit', 'maturation_redesign_enabled'])


def test_pair_model_and_masks(tmp_path):
    source = tmp_path / 'source.pdb'
    source.write_text('MODEL        1\n' + pdb_text() + 'ENDMDL\nMODEL        2\n' + pdb_text().replace('GLY', 'ALA') + 'ENDMDL\n')
    mapping = partial.prepare_pair(source, tmp_path / 'pair.pdb', 'R', 'r', 2)
    assert len(mapping['residues']) == 4
    assert all(r['source']['model'] == 2 for r in mapping['residues'])
    assert partial.map_mask('R:19B,R7', mapping, 'binder') == 'B2,B1'
    assert partial.map_mask('r19A', mapping, 'target') == 'A2'
    with pytest.raises(ValueError):
        partial.map_mask('R19', mapping, 'binder')


def test_real_native_parser_and_config(native, tmp_path):
    source = tmp_path / 'source.pdb'; source.write_text(pdb_text())
    settings = load_authority().apply_partial_defaults({'ppiflow_task': 'protein_binder_partial', 'binder_chains': 'R', 'target_chains': 'r', 'ppiflow_fixed_positions': 'R19B', 'epitope_residues': 'r19A'})
    receipt = partial.prepare(settings, source, tmp_path / 'out', native)
    namespace = {'argparse': argparse}
    definitions(native / 'sample_binder_partial.py', {'get_parser'}, namespace)
    args = namespace['get_parser']().parse_args(receipt['argv'][1:])
    assert args.start_t == .15 and args.samples_per_target == 100
    assert args.fixed_positions == 'B2' and args.specified_hotspots == 'A2'
    assert args.config.endswith('native_config.yaml')
    assert args.model_weights == '/opt/ppiflow/ckpt/binder.ckpt'
    assert not hasattr(args, 'retry_Limit')
    cfg = yaml.safe_load(Path(args.config).read_text())
    assert cfg['model']['seq_decoder']['use'] is False
    assert receipt['explicit_hotspot_chain2_correction'] == 'approved_explicit_binder_chain_assignment'


def test_actual_native_writer_identity_and_relocation(native_io, tmp_path, monkeypatch):
    # Actual native writer with inert atom coordinates; no model or sampler.
    source = tmp_path / 'source.pdb'; source.write_text(pdb_text())
    mapping = partial.prepare_pair(source, tmp_path / 'pair.pdb', 'R', 'r')
    partial._CONTEXT.update(correspondence=mapping, settings={'ppiflow_start_t': .15})
    # Existing test fixture instruments actual writer against its own module.
    monkeypatch.setattr(partial, 'scalar', lambda x: x.tolist() if hasattr(x, 'tolist') else x)
    monkeypatch.setitem(sys.modules, 'run_ppiflow_generation', runner)
    positions = np.zeros((4, 37, 3)); positions[:, :4] = np.arange(48).reshape(4, 4, 3) + 1
    batch = {'chain_idx': tensor([[0, 0, 1, 1]]), 'original_res_idx': tensor([[1, 2, 1, 2]]),
             'diffuse_mask': tensor([[0, 0, 1, 0]]), 'sample_ids': tensor([3]), 'pdb_name': ['selected_pair'],
             'bms_partial_residues': [json.dumps({'chains': [0, 0, 1, 1], 'numbers': [1, 2, 1, 2]})]}
    out = tmp_path / 'native'; out.mkdir()
    path = partial.write_sample(native_io['write_prot_to_pdb'], batch, 0, positions, str(out / 'sample3_-1.pdb'),
                                no_indexing=True, chain_index=batch['chain_idx'][0], aatype=tensor([7, 15, 0, 10]), b_factors=tensor([0, 1, 0, 1]))
    meta = json.loads(Path(path + '.sample.json').read_text())
    assert meta['sample_index'] == 3 and meta['artifact_class'] == 'backbone_complex'
    assert len(meta['residue_mapping']) == 4
    assert {r['source']['insertion_code'] for r in meta['residue_mapping']} == {'', 'A', 'B'}
    relocated = tmp_path / 'relocated'; shutil.move(out, relocated)
    rows = partial.collect(relocated, tmp_path / 'collected', 'seed', tmp_path / 'manifest.json')
    assert len(rows) == 1 and Path(rows[0]['path']).read_bytes() == (relocated / 'sample3_-1.pdb').read_bytes()
    assert partial.collect(tmp_path / 'empty', tmp_path / 'empty_out', 'seed', tmp_path / 'empty.json') == []


def test_protein_instrumentation_is_writer_only(native):
    source = (native / 'models/flow_module_binder_partial.py').read_text()
    code = partial.instrument(source, 'native-producer')
    assert code
    def names(compiled):
        return set(compiled.co_names).union(*(names(c) for c in compiled.co_consts if isinstance(c, types.CodeType)))
    assert 'write_sample' in names(code)
    assert 'correct_hotspot_source' not in names(code)


@pytest.mark.parametrize('samples,fail_after_one,expected,redesign,anchors', [(2, False, 2, False, False), (0, False, 0, False, False), (2, True, 1, False, False), (2, False, 4, True, False), (2, False, 4, True, True)])
def test_actual_nextflow_module_shell_with_inert_science(native, tmp_path, samples, fail_after_one, expected, redesign, anchors):
    executable = shutil.which('nextflow')
    jar = Path.home() / '.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    command = ['java', '-jar', str(jar)] if jar.is_file() else [executable]
    if not executable and not jar.is_file():
        pytest.skip('Nextflow required for actual shell composition')
    bindir = tmp_path / 'bin'; bindir.mkdir()
    launcher = bindir / 'python3'
    launcher.write_text(f'''#!{sys.executable}
import os, sys
if len(sys.argv) > 1 and sys.argv[1].endswith('/run_ppiflow_protein_partial.py'):
    sys.path.insert(0, {str(ROOT / 'tests')!r})
    from ppiflow_partial_inert_leaves import run_adapter
    run_adapter(sys.argv[1], sys.argv[2:], {str(native)!r})
elif len(sys.argv)>1 and sys.argv[1].endswith(('/prep_fampnn_designs.py','/prep_fampnn_constraints_generic.py','/analyse_fampnn.py','/prepare_ppiflow_maturation.py')):
    sys.path[:0] = [{str(ROOT/'tests')!r}, {str(ROOT/'scripts')!r}]
    from ppiflow_inline_inert_leaves import native_inline, inert_preparation, inert_analysis, inert_anchors
    if sys.argv[1].endswith('/prep_fampnn_designs.py'): inert_preparation(sys.argv[2:])
    elif sys.argv[1].endswith('/analyse_fampnn.py'): inert_analysis(sys.argv[2:])
    elif sys.argv[1].endswith('/prepare_ppiflow_maturation.py'): inert_anchors(sys.argv[2:])
    else: native_inline(sys.argv[2:])
else:
    os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
''')
    launcher.chmod(0o755)
    source = tmp_path / 'source.pdb'; source.write_text(pdb_text())
    config = tmp_path / 'inert.config'
    config.write_text('process.executor="local"\nprocess.container=null\nprocess.cpus=1\nprocess.memory="1 GB"\napptainer.enabled=false\ndocker.enabled=false\n')
    # Run the real workflow from a minimal relocated portable source closure.
    portable = tmp_path / 'portable'
    for relative in ['workflows/binder_refinement.nf', 'modules/ppiflow.nf',
                     'scripts/run_ppiflow_protein_partial.py', 'scripts/run_ppiflow_generation.py',
                     'scripts/publish_binder_refinement.py', 'platform/api/config/models/binder_refinement.yaml',
                     'scripts/prep_binder_fampnn_constraints.py', 'scripts/prep_fampnn_designs.py', 'scripts/prepare_ppiflow_maturation.py',
                     'scripts/prep_fampnn_constraints_generic.py', 'scripts/fampnn_native_binding.py', 'scripts/analyse_fampnn.py']:
        dest = portable / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, dest)
    params = {'pdb_paths': str(source), 'code_root': str(portable), 'out_dir': str(tmp_path / 'out'),
              'binder_chains': 'R', 'target_chains': 'r', 'ppiflow_task': 'protein_binder_partial',
              'maturation_flow_enabled': True, 'maturation_repack_enabled': False,
              'maturation_anchors_enabled': anchors, 'maturation_redesign_enabled': redesign,
              'seqs_per_design': 2, 'fixed_positions': 'R:19B', 'epitope_residues': 'r19A',
              'ppiflow_samples_per_target': samples, 'ppiflow_start_t': .15}
    # Source conversion selected model 99 and wrote MODEL 7, not original 99 or implicit1.
    source.write_text('MODEL        7\n'+pdb_text()+'ENDMDL\n')
    identity=tmp_path/'identity.json'
    identity.write_text(json.dumps([{'staged_name':source.name,'prepared_pdb_model':7,'source_meta':{'model':99}}]))
    params['source_identity_json']=str(identity)
    param_file = tmp_path / 'params.json'; param_file.write_text(json.dumps(params))
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'], NXF_OFFLINE='true',
               BMS_INERT_FAIL_AFTER_ONE='1' if fail_after_one else '0')
    result = subprocess.run([*command, '-C', str(config), 'run', str(portable / 'workflows/binder_refinement.nf'),
                             '-params-file', str(param_file), '-work-dir', str(tmp_path / 'work'), '-with-trace', str(tmp_path / 'trace.txt')],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=120)
    (tmp_path / 'nextflow.stdout').write_text(result.stdout + result.stderr)
    assert (result.returncode != 0) == fail_after_one, result.stdout + result.stderr
    trace = (tmp_path / 'trace.txt').read_text()
    assert 'RunProteinPartialFlow' in trace
    assert ('RunMaturationFAMPNN' in trace) == redesign
    assert ('IdentifyAnchorResidues' in trace) == anchors
    outputs = list((tmp_path / 'out/collected/binder_refinement').rglob('generator_*.json'))
    if redesign:
        # Retained parent backbones plus explicitly requested descendants.
        designed=[json.loads(p.read_text()) for p in outputs if json.loads(p.read_text()).get('terminal_producer')=='fampnn']
        assert len(designed)==expected
        for row in designed:
            assert row['artifact_class']=='sequence_designed_complex'
            assert row['sequence_semantics']=='native_fampnn_output'
            assert row['source_residue_mapping']
            assert row['parent_artifact_class']=='backbone_complex'
            assert row['parent_native_output_id'].startswith('source:sample:')
            assert row['native_output_id'].startswith(row['native_input_id']+'_sample')
            assert 'sample_index' not in row
            assert row['sha256']
            assert row['binder_chains'] and row['target_chains']
            assert row['chain_roles_namespace']=='output'
            assert list(row['designed_chain_sequences'].values())==['RL' if anchors else 'RA']
        masks=[json.loads(p.read_text()) for p in (tmp_path/'work').rglob('native_masks.json')]
        assert masks
        for rows in masks:
            for row in rows:
                assert row['fixed_sequence']==[[1,1,0,1]]
        outputs=[p for p in outputs if json.loads(p.read_text()).get('terminal_producer')!='fampnn']
        assert len(outputs)==samples
    else:
        assert len(outputs) == expected
    for output in outputs:
        row = json.loads(output.read_text())
        assert row['artifact_class'] == 'backbone_complex'
        assert row['sequence_semantics'] == 'movable_positions_masked_alanine'
        assert len(row['residue_mapping']) == 4
        assert row['native_output_id'].startswith('source:sample:')
        assert row['effective_settings']['ppiflow_input_model']==(1 if anchors else 7)
        assert all(r['source']['model']==(1 if anchors else 7) for r in row['residue_mapping'])
