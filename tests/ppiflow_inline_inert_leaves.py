"""Real installed FA parser/mask/writer, with inert denoising/analysis leaves."""
import ast
import dataclasses
import json
import os
import pickle
import re
import textwrap
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from Bio.PDB import PDBParser, MMCIFParser, Structure
from Bio.PDB.Atom import DisorderedAtom

from test_fampnn_native_binding import definitions, native, native_constants, Tensor, tensor, source_root


def native_inline(args):
    options = dict(x.split('=',1) for x in args if '=' in x)
    rc = native_constants()
    scope = dict(np=np, dataclasses=dataclasses, _bms=native, residue_constants=rc, Path=Path,
        PDBParser=PDBParser, MMCIFParser=MMCIFParser, Structure=Structure, DisorderedAtom=DisorderedAtom,
        PDB_CHAIN_IDS='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', PDB_MAX_CHAINS=62,
        __name__=__name__)
    definitions('fampnn/data/protein.py', ['Protein','read_pdb','to_pdb','_chain_end','are_atoms_bonded'], scope)
    rc.STANDARD_ATOM_MASK_WITH_X=np.ones((21,37),dtype=int)
    rc.non_bb_idxs=[i for i in range(37) if i not in [0,1,2,4]]
    torch=SimpleNamespace(Tensor=Tensor,float32=np.float32,tensor=tensor,where=np.where,
        zeros_like=lambda a,**kw:tensor(np.zeros_like(a,**kw)),
        unique=lambda a:tensor(np.unique(a)),sort=lambda a:SimpleNamespace(values=tensor(np.sort(a))))
    writer=dict(torch=torch,rc=rc,protein=SimpleNamespace(**scope),_bms=native,PDB_CHAIN_IDS=scope['PDB_CHAIN_IDS'])
    definitions('fampnn/data/pdb_utils.py',['write_to_pdb','write_batched_to_pdb'],writer)
    definitions('fampnn/model/sd_model.py',['save_samples_to_pdb'],writer)
    # Execute native mask receivers and the exact native CSV loading statements.
    env=dict(torch=torch,np=np,re=re,pd=pd,rc=rc)
    tree=ast.parse((source_root()/'fampnn/sampling_utils.py').read_text())
    import __future__
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'parse_fixed_positions','get_override_masks'}]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'installed-mask-parser','exec',flags=__future__.annotations.compiler_flag),env)
    text=(source_root()/'fampnn/inference/seq_design.py').read_text()
    block=text.split('    ### Read in fixed positions ###\n',1)[1].split('    ### Load in PDB files ###',1)[0]
    env['cfg']=SimpleNamespace(fixed_pos_csv=options['fixed_pos_csv'])
    exec(textwrap.dedent(block),env)
    out=Path(options['out_dir'])
    for folder in ['samples','pkls','fastas']:(out/folder).mkdir(parents=True)
    receipts=[]
    for source in sorted(Path('.').glob('*.pdb')):
        prot,chain_map=scope['read_pdb'](str(source))
        context=native.capture_input(source,vars(prot))
        n=len(prot.aatype)
        batch={k:tensor([getattr(prot,k)]) for k in ['residue_index','chain_index','aatype']}
        seq,sc=env['get_override_masks'](batch,[source.stem],[chain_map],env['fixed_pos_df'],mode='seq_design')
        receipts.append({'source':source.name,'chain_map':chain_map,'fixed_sequence':seq.tolist(),'fixed_sidechains':sc.tolist()})
        # Explicit inert scientific leaf: change only unfixed identities to ARG.
        aa=tensor([prot.aatype.copy()]); aa[seq==0]=1
        samples=dict(x_denoised=tensor([prot.atom_positions]),residue_index=batch['residue_index'],
            chain_index=batch['chain_index'],seq_mask=tensor(np.ones((1,n))),pred_aatype=aa,
            missing_atom_mask=tensor([1-prot.atom_mask]),psce=tensor(np.zeros((1,n,33))),
            aatype_override_mask=seq,seq_probs=tensor(np.eye(21)[aa]))
        Tensor.bool=lambda self:tensor(self.astype(bool))
        export=native.instrument_source('fampnn/inference/seq_design.py',(source_root()/'fampnn/inference/seq_design.py').read_bytes())
        block=export.split('        # Save outputs\n',1)[1].split('        pbar.update(B)',1)[0]
        for sample in range(int(options['num_seqs_per_pdb'])):
            values=dict(_bms=native,cfg=SimpleNamespace(num_seqs_per_pdb=int(options['num_seqs_per_pdb'])),i=sample,B=1,
                pdb_names=[source.stem],pdb_batch_files=[source],sample_out_dir=str(out/'samples'),
                fasta_out_dir=str(out/'fastas'),sample_pkl_dir=str(out/'pkls'),samples=samples,bms_inputs=[context],
                SeqDenoiser=SimpleNamespace(save_samples_to_pdb=writer['save_samples_to_pdb']),
                rc=SimpleNamespace(restypes_with_x='ARNDCQEGHILKMFPSTWYVX'),pickle=pickle)
            exec(compile(textwrap.dedent(block),'installed-native-export','exec'),values)
    Path('native_masks.json').write_text(json.dumps(receipts))


def inert_preparation(args):
    """No Rosetta: copy unchanged identities, then production constraint checks."""
    import shutil
    source=Path(args[args.index('--input_dir')+1]); dest=Path(args[args.index('--out_dir')+1]);dest.mkdir()
    for p in source.glob('*.pdb'):shutil.copy2(p,dest/p.name)


def inert_anchors(args):
    """Physical anchor analysis is inert; real downstream typed transport runs."""
    source=Path(args[args.index('--pdb')+1])
    output=Path(args[args.index('--output_enriched_pdb')+1])
    output.write_text('\n'.join(line for line in source.read_text().splitlines() if not line.startswith(('MODEL ', 'ENDMDL')))+'\n')
    for key in ['--output_score','--output_rotamer_enrichment','--output_cdr_positions_by_loop']:
        Path(args[args.index(key)+1]).write_text('{}')
    for key in ['--output_positions','--output_cdr_positions']:
        Path(args[args.index(key)+1]).write_text('')
    Path(args[args.index('--output_anchors')+1]).write_text(json.dumps({'anchors':[{'chain':'R','resnum':19,'icode':'B','pdb_position':'R19B','movable_region_member':False}]}))


def inert_analysis(args):
    directory=Path(args[args.index('--input_dir')+1])
    for p in directory.glob('*.pdb'):p.with_suffix('.json').write_text('{}')
