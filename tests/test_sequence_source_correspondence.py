"""Actual producer identity through compiled leaf shells; no model inference."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from test_generic_sequence_native_transport import ROOT, atom, run_wrapper


def emit_native_fa(source, output, count, mutable_ids=None):
    """Execute installed parser and writer definitions, replacing only tensors/science."""
    import dataclasses
    import pickle
    import numpy as np
    from types import SimpleNamespace
    from Bio.PDB import PDBParser, MMCIFParser, Structure
    from Bio.PDB.Atom import DisorderedAtom
    from test_fampnn_native_binding import definitions, native, native_constants, Tensor, tensor
    rc = native_constants()
    scope = dict(np=np, dataclasses=dataclasses, _bms=native, residue_constants=rc, Path=Path,
                 PDBParser=PDBParser, MMCIFParser=MMCIFParser, Structure=Structure, DisorderedAtom=DisorderedAtom,
                 PDB_CHAIN_IDS='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', PDB_MAX_CHAINS=62,
                 __name__=__name__)
    definitions('fampnn/data/protein.py', ['Protein', 'read_pdb', 'to_pdb', '_chain_end', 'are_atoms_bonded'], scope)
    prot = scope['read_pdb'](str(source))[0]
    context = native.capture_input(source, vars(prot))
    rc.STANDARD_ATOM_MASK_WITH_X = np.ones((21, 37), dtype=int)
    rc.non_bb_idxs = [i for i in range(37) if i not in [0, 1, 2, 4]]
    torch = SimpleNamespace(Tensor=Tensor, float32=np.float32, tensor=tensor,
                           zeros_like=lambda a, **kw: tensor(np.zeros_like(a, **kw)),
                           unique=lambda a: tensor(np.unique(a)), sort=lambda a: SimpleNamespace(values=tensor(np.sort(a))))
    writer = dict(torch=torch, rc=rc, protein=SimpleNamespace(**scope), _bms=native, PDB_CHAIN_IDS=scope['PDB_CHAIN_IDS'])
    definitions('fampnn/data/pdb_utils.py', ['write_to_pdb', 'write_batched_to_pdb'], writer)
    definitions('fampnn/model/sd_model.py', ['save_samples_to_pdb'], writer)
    n = len(prot.residue_index)
    aatype = np.array(prot.aatype, copy=True)
    for position, identity in enumerate(prot.bms_identity):
        if mutable_ids is None or (chr(int(identity[0])), int(identity[1])) in mutable_ids:
            aatype[position] = rc.restype_order['G']
    samples = dict(x_denoised=tensor([prot.atom_positions]), residue_index=tensor([prot.residue_index]),
                   chain_index=tensor([prot.chain_index]), seq_mask=tensor(np.ones((1,n))),
                   pred_aatype=tensor([aatype]),
                   missing_atom_mask=tensor([1-prot.atom_mask]), psce=tensor(np.zeros((1,n,33))))
    for index in range(count):
        path = output/'samples'/f'{source.stem}_sample{index}.pdb'
        path.with_name(path.name+'.fa_binding.json').unlink(missing_ok=True)
        native.save_samples(writer['save_samples_to_pdb'], samples, [str(path)], [context])
        sample = dict(seq_probs=np.eye(21)[aatype], pred_aatype=aatype,
                      seq_mask=np.ones(n), chain_index=prot.chain_index,
                      residue_index=prot.residue_index,
                      aatype_override_mask=np.array([
                          mutable_ids is not None and (chr(int(r[0])), int(r[1])) not in mutable_ids
                          for r in prot.bms_identity], dtype=int))
        pkl_path = output/'sample_pkls'/f'{source.stem}_sample{index}.pkl'
        pkl_path.parent.mkdir(exist_ok=True)
        pkl_path.write_bytes(pickle.dumps(sample))
        native.capture_sample(pkl_path, path, context)


def test_fa_actual_parser_writer_compiled_shell(tmp_path, monkeypatch):
    assert Path(os.environ['BMS_G09_FAMPNN_SOURCE']).is_dir()
    monkeypatch.setenv('BMS_TEST_FA_REAL_WRITER', '1')
    text = atom(1,'Z',10)+atom(2,'Z',30)+atom(3,'B',91)+atom(4,'B',98)
    result, calls, source = run_wrapper(tmp_path, 'fampnn', 'binder_design',
                                       dict(design_chain='Z', target_chain='B'), text=text)
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads((tmp_path/'out/pdb_files/subject_seq_0.json').read_text())
    assert row['source_structure_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    pairs = [(r['source']['chain_id'],r['source']['auth_seq_id'],r['output']['chain_id'],r['output']['auth_seq_id'])
             for r in row['source_residue_mapping']]
    assert pairs == [('Z',10,'A',10),('Z',30,'A',30),('B',91,'B',91),('B',98,'B',98)]
    assert row['chain_sequences'] == {'A':'GG','B':'AA'}
    assert row['binder_chains'] == ['A']
    assert row['target_chains'] == ['B']
    assert row['input_binder_chains'] == ['Z']
    assert row['designed_chain_sequences'] == {'A':'GG'}
    assert row['chain_roles_namespace'] == 'output'
    # Identity is independent of controller vs staged task paths and sequences.
    assert all(str(source.parent) != call['cwd'] for call in calls)
    assert 'ALA' in source.read_text()
    prepared = next((tmp_path/'work').glob('**/fampnn_input/subject.pdb'))
    assert prepared.read_bytes() != source.read_bytes()
    assert row['source_structure_sha256'] != hashlib.sha256(prepared.read_bytes()).hexdigest()


CALIBY_INERT = r'''
# Real cleaner, parser and CIF serialization; sampling/preflight are inert.
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import run_caliby_sequence_design as runner
from caliby.eval.eval_utils.seq_des_utils import get_sd_example
from atomworks.io.utils.io_utils import to_cif_string
runner.preflight_caliby_runtime = lambda **kw: None
runner.maybe_run_self_consistency = lambda **kw: {}
def sample(paths, **kw):
    output=Path(kw['out_dir']); output.mkdir(parents=True)
    result=dict(example_id=[],out_pdb=[],seq=[],input_seq=[],U=[])
    for path in paths:
        example=get_sd_example(path, data_cfg=None)
        array=example['atom_array']
        constraints = kw.get('pos_constraint_df')
        if constraints is not None:
            import json
            from caliby.eval.eval_utils.seq_des_utils import parse_fixed_pos_str, get_token_starts
            row = constraints.set_index('pdb_key').loc[example['example_id']]
            tokens = array[get_token_starts(array)]
            observed = {}
            for column in ('fixed_pos_seq', 'fixed_pos_scn'):
                value = row.get(column)
                indices = parse_fixed_pos_str(value, array) if isinstance(value, str) else []
                observed[column] = [[str(tokens.chain_id[i]), int(tokens.res_id[i])] for i in indices]
            (output.parent/'observed_constraints.json').write_text(json.dumps(observed))
        # Explicit non-science amino-acid substitutions, no model/checkpoint.
        array.res_name[:] = 'GLY'
        target=output/'arbitrary-native-name.cif'
        target.write_text(to_cif_string(array, include_nan_coords=False))
        result['example_id'].append(example['example_id'])
        result['out_pdb'].append(str(target))
        result['seq'].append('G'*20)
        result['input_seq'].append('A'*20)
        result['U'].append(0.0)
    return result
runner.load_caliby_model = lambda name: SimpleNamespace(sample=sample)
sys.argv = ['run_caliby_sequence_design.py', *sys.argv[2:]]
runner.main()
'''


def full_backbone():
    lines=[]
    for ci, chain in enumerate(['B','A']):
        for ri, number in enumerate([10,11,12,13,14,30,31,32,33,34]):
            for name,dx,dy in [('N',0,0),('CA',1.4,0),('C',2.4,1),('O',2.4,2),('CB',1.4,-1.4)]:
                lines.append(f'ATOM  {len(lines)+1:5d} {name:^4s} ALA {chain}{number:4d}    {ri*3.8+dx:8.3f}{ci*20+dy:8.3f}{0:8.3f}{1:6.2f}{50:6.2f}          {name[0]:>2s}\n')
    return ''.join(lines)+'END\n'


def test_caliby_native_cleaner_relabel_mapping(tmp_path):
    image = Path(os.environ['BMS_TEST_CALIBY_IMAGE'])
    source = tmp_path/'renamed.pdb'
    # Original B->Z, A->B; native parser sorts chains before cleaner labels them.
    source.write_text(''.join(l[:21] + {'B':'Z','A':'B'}[l[21]] + l[22:] if l.startswith('ATOM') else l
                              for l in full_backbone().splitlines(keepends=True)))
    code = ('import sys; sys.path.insert(0,sys.argv[1]); '
            'from run_caliby_sequence_design import clean_pdb_with_correspondence; '
            'clean_pdb_with_correspondence(sys.argv[2],sys.argv[3])')
    result = subprocess.run(['apptainer','exec','--bind',f'{tmp_path}:/cache',str(image),'python','-c',code,
                             str(ROOT/'scripts'),str(source),str(tmp_path/'cleaned')],
                            text=True,capture_output=True,timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    mapping = json.loads((tmp_path/'cleaned/renamed.cif.source_mapping.json').read_text())
    assert len(mapping) == 20
    assert {(r['source']['chain_id'],r['output']['chain_id']) for r in mapping} == {('Z','B'),('B','A')}
    assert all(r['source']['auth_seq_id'] == r['output']['auth_seq_id'] for r in mapping)


def test_caliby_real_cleaning_compiled_shell(tmp_path):
    image = Path(os.environ['BMS_TEST_CALIBY_IMAGE'])
    assert image.is_file()
    jar = os.environ['BMS_TEST_NEXTFLOW_JAR']
    source=tmp_path/'selected.pdb'; source.write_text(full_backbone())
    inert=tmp_path/'inert.py'; inert.write_text(CALIBY_INERT)
    bin_dir=tmp_path/'bin'; bin_dir.mkdir()
    wrapper=bin_dir/'python3'
    wrapper.write_text(f'''#!{sys.executable}
import os,sys
from pathlib import Path
args=sys.argv[1:]
if Path(args[0]).name == 'run_caliby_sequence_design.py':
    args=[{str(inert)!r},{str(ROOT/'scripts')!r},*args[1:]]
os.execvp('apptainer',['apptainer','exec','--bind',{str(tmp_path)+':/cache'!r},{str(image)!r},'python',*args])
''')
    wrapper.chmod(0o755)
    workflow=tmp_path/'main.nf'
    workflow.write_text(f"include {{ RunCalibyBinder }} from '{ROOT}/modules/caliby.nf'\nworkflow {{ RunCalibyBinder(Channel.of(tuple(file(params.input), []))) }}\n")
    params=dict(input=str(source),out_dir=str(tmp_path/'out'),code_root=str(ROOT),binder_chains='B',target_chains='A',
                caliby_num_seqs_per_pdb=1,caliby_clean_num_workers=2)
    settings=tmp_path/'params.json'; settings.write_text(json.dumps(params))
    config=tmp_path/'minimal.config'; config.write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="128 MB"\n')
    env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
    result=subprocess.run(['java','-jar',jar,'-C',str(config),'run',str(workflow),'-params-file',str(settings),
                           '-work-dir',str(tmp_path/'work')],cwd=tmp_path,env=env,text=True,capture_output=True,timeout=180)
    assert result.returncode == 0, result.stdout+result.stderr
    row=json.loads(next((tmp_path/'out/collected/binder_generation/caliby').glob('generator_*.json')).read_text())
    assert row['source_structure_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    mapping=row['source_residue_mapping']
    assert len(mapping)==20
    assert {r['source']['auth_seq_id'] for r in mapping} == {10,11,12,13,14,30,31,32,33,34}
    assert all(r['source']==r['output'] for r in mapping)
    assert row['chain_sequences']=={'A':'G'*10,'B':'G'*10}
    native=tmp_path/'out/collected/binder_generation/caliby'/row['native_output_structure']['relative_path']
    assert native.is_file()
