"""Installed receiving contracts; no checkpoint, sampler, or scientific run."""
import ast
import argparse
import collections
import json
import os
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from Bio import PDB

from test_ppiflow_protein_partial import partial, pdb_text, ROOT, native, native_io, definitions


def test_approved_hotspots_actual_pinned_preprocessing(native, native_io, tmp_path):
    source = tmp_path / 'source.pdb'; source.write_text(pdb_text())
    receipt = partial.prepare({'binder_chains': 'R', 'target_chains': 'r',
                               'epitope_residues': 'r19A', 'ppiflow_fixed_positions': 'R19B'}, source, tmp_path/'out', native)
    utils = dict(np=np, collections=collections, List=list, Dict=dict,
                 residue_constants=native_io['residue_constants'])
    definitions(native/'data/utils.py', {'parse_chain_feats', 'concat_np_features'}, utils)
    captured = {}
    du = types.SimpleNamespace(**{k:v for k,v in utils.items() if not k.startswith('__')},
        chain_str_to_int=lambda c: native_io['PDB_CHAIN_IDS'].index(c),
        write_pkl=lambda path, data: captured.update(path=path, features=data))
    env = dict(np=np, os=os, PDB=PDB, du=du,
               parsers=types.SimpleNamespace(process_chain=native_io['process_chain']),
               dataclasses=types.SimpleNamespace(asdict=lambda value: vars(value).copy()))
    definitions(native/'preprocessing/process_partial.py', {'process_file', 'generate_virtual_binder_feats'}, env)
    env.update(argparse=argparse, pd=pd)
    corrected = tmp_path/'corrected.py'
    corrected.write_text(partial.correct_hotspot_source((native/'sample_binder_partial.py').read_text()))
    definitions(corrected, {'get_parser', 'preprocess_csv_and_pkl', 'get_motif_residues'}, env)
    args = env['get_parser']().parse_args(receipt['argv'][1:])
    csv = env['preprocess_csv_and_pkl'](args.input_pdb, str(tmp_path), args)
    metadata = pd.read_csv(csv).iloc[0]
    assert metadata.binder_id == 'B' and metadata.target_id == 'A'
    assert metadata.binder_seq_len == metadata.target_seq_len == 2
    assert captured['features']['target_interface_residues'] == [2]
    assert captured['features']['binder_motif'] == [[2]]
    # Exact AST delta: one assignment, no auto-hotspot substitution/protocol change.
    original = ast.parse((native/'sample_binder_partial.py').read_text())
    patched = ast.parse(corrected.read_text())
    method = next(n for n in patched.body if isinstance(n, ast.FunctionDef) and n.name == 'preprocess_csv_and_pkl')
    branch = next(n for n in method.body if isinstance(n, ast.If))
    assert ast.unparse(branch.body.pop()) == "input_info['chain2_id'] = args.binder_chain"
    assert ast.dump(original) == ast.dump(patched)


@pytest.mark.parametrize('model,selected', [(7,None),(7,7),(1,None)])
def test_actual_prepared_model_identity(tmp_path, model, selected):
    source = tmp_path/'prepared.pdb'
    source.write_text((f'MODEL     {model:4d}\n' if model != 1 else '')+pdb_text())
    row = partial.prepare_pair(source, tmp_path/'pair.pdb', 'R', 'r', selected)
    assert row['model'] == model
    assert all(r['source']['model'] == model for r in row['residues'])


def test_reachable_interpolant_contract_and_compilation(native, tmp_path):
    contract = yaml.safe_load((ROOT/'platform/api/config/models/binder_refinement.yaml').read_text())
    mapped = {r['protein_native_path']:r for r in contract['params'] if r.get('protein_native_path')}
    tree = ast.parse((native/'data/interpolant_binder_partial.py').read_text())
    # Enumerate actual config reads, ignoring comments and training-only config.
    aliases = {'self._cfg': 'interpolant', 'self._rots_cfg': 'interpolant.rots',
               'self._trans_cfg':'interpolant.trans', 'self._sample_cfg':'interpolant.sampling'}
    reads = set()
    for n in ast.walk(tree):
        if isinstance(n,ast.Attribute):
            value = ast.unparse(n)
            for alias,prefix in aliases.items():
                if value.startswith(alias+'.'): reads.add(prefix+value[len(alias):])
    leaves = {v for v in reads if not any(x.startswith(v+'.') for x in reads)}
    assert leaves - set(mapped) == {'interpolant.min_t','interpolant.max_t'}  # CLI start_t,+.02
    params = {'binder_chains':'R','target_chains':'r', 'ppiflow_protein_scaffold_contig':'R7,1',
              'ppiflow_protein_num_timesteps':41, 'ppiflow_protein_twisting_use':True}
    for path,field in mapped.items():
        if path.startswith('interpolant.twisting.') and field['default'] is None:
            params[field['name']] = field.get('enum',['linear'])[0] if field['type']=='string' else True if field['type']=='boolean' else 2
    source = tmp_path/'source.pdb'; source.write_text(pdb_text())
    receipt = partial.prepare(params, source, tmp_path/'out', native)
    cfg = yaml.safe_load((tmp_path/'out/native_config.yaml').read_text())
    assert cfg['ppi_dataset']['scaffold_contig'] == 'B1-1,1'
    assert cfg['interpolant']['sampling']['num_timesteps'] == 41
    for path,field in mapped.items():
        if field['name'] in params and field['name'] != 'ppiflow_protein_scaffold_contig':
            value=cfg
            for part in path.split('.'):value=value[part]
            assert value == params[field['name']]
    assert cfg['model']['seq_decoder']['use'] is False
    assert all(f['read_only'] and f['fixed_reason'] for f in mapped.values() if f.get('authority')=='profile')
    assert not any('training' in p or 'optimizer' in p for p in mapped)
    assert receipt['settings']['ppiflow_input_model'] == 1
    # Actual installed dataset constructor receives compiled scaffold/batching.
    import logging
    source_tree = ast.parse((native/'data/datasets_binder_partial.py').read_text())
    native_class = next(n for n in source_tree.body if isinstance(n, ast.ClassDef) and n.name=='PpiTestPartialDataset')
    constructor = next(n for n in native_class.body if isinstance(n, ast.FunctionDef) and n.name=='__init__')
    isolated = ast.ClassDef(name='NativePartial', bases=[], keywords=[], body=[constructor], decorator_list=[])
    env = dict(np=np, pd=pd, logging=logging, torch=types.SimpleNamespace(tensor=np.asarray))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[isolated],type_ignores=[])), 'installed-dataset-constructor','exec'),env)
    env['NativePartial'].dataset_cfg=property(lambda self:self._dataset_cfg)
    csv=tmp_path/'native.csv'; pd.DataFrame([{'pdb_name':'selected_pair_A','binder_seq_len':2}]).to_csv(csv,index=False)
    dataset_cfg=dict(cfg['ppi_dataset'],test_csv_path=str(csv),samples_per_target=2,motif=None)
    dataset=env['NativePartial'](dataset_cfg=types.SimpleNamespace(**dataset_cfg),task='binder_motif_partial')
    assert dataset.scaffold_contig=='B1-1' and dataset.add_binder_len==1
    assert dataset.scaffold_residues.tolist()==[1]
    assert [int(sample[2]) for sample in dataset.all_sample_ids]==[3,3]


def test_pinned_randomized_time_is_not_fixed(native):
    tree = ast.parse((native/'data/interpolant_binder_partial.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name=='Interpolant')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name=='sample_t')
    env = {'torch':types.SimpleNamespace(rand=lambda *a,**k:types.SimpleNamespace(expand=lambda n:np.full(n,.5)))}
    exec(compile(ast.Module(body=[method],type_ignores=[]),'installed-native-time','exec'),env)
    result=env['sample_t'](types.SimpleNamespace(_device='cpu'),3,x=.15,y=.17)
    np.testing.assert_allclose(result,(.15+.5*.02)*.98+.01)
    assert np.all(result != .15)


def test_source_fixed_and_anchor_remapping(tmp_path, monkeypatch):
    from prep_binder_fampnn_constraints import prepare
    monkeypatch.chdir(tmp_path)
    inp=tmp_path/'in'; inp.mkdir(); prep=tmp_path/'prep'; prep.mkdir()
    # Native output uses different roles/numbering from original author PDB.
    text=pdb_text().replace('r   7 ', 'A   1 ').replace('r  19A','A   2 ').replace('R   7 ','B   1 ').replace('R  19B','B   2 ')
    (inp/'sample.pdb').write_text(text); (prep/'sample.pdb').write_text(text)
    anchors=tmp_path/'anchors.json'; anchors.write_text(json.dumps({'anchors':[{'chain':'R','resnum':19,'icode':'B','pdb_position':'R19B'}]}))
    rows=[{'source':{'chain_id':c,'auth_seq_id':n,'insertion_code':i},'output':{'chain_id':o,'auth_seq_id':j}} for c,n,i,o,j in [('r',7,'','A',1),('r',19,'A','A',2),('R',7,'','B',1),('R',19,'B','B',2)]]
    request={'sequence_design_mode':'binder_design','design_chain':'B','target_chain':'A','fixed_positions':'R:7','fampnn_fix_target_sidechains':True}
    result=prepare(inp,prep,tmp_path/'native.csv',request,anchors,{'residue_mapping':rows})
    assert result == [('sample','A1,A2,B1,B2','A1,A2')]
    assert (tmp_path/'native.csv').read_text().startswith('sample,')
    assert json.loads((tmp_path/'transport/request.json').read_text())['fixed_positions']=='B:1,B:2'
