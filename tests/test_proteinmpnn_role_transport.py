"""Real pinned native argparse/classes/featurizer with inert science objects.

No model inference or Rosetta computation. Opt-in native tests execute the real
Nextflow PrepMPNN/RunMPNN shell and installed Python runner inside its image.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import proteinmpnn_native_binding as binding
from test_generic_sequence_native_transport import atom


def test_author_roles_keep_case_insertions_numeric_chains_and_gaps(tmp_path, monkeypatch):
    source = tmp_path / 'source.with.dot.pdb'
    source.write_text(atom(1, 'T', 5) + atom(2, 'z', 10) + atom(3, 'z', 10, insertion='B') + atom(4, 'z', 12) + atom(5, '1', -2))
    request = dict(design_chain='z,1', target_chain='T', fixed_positions='z:10B,1:-2')
    reads=[]
    read_bytes=Path.read_bytes
    def observed(path):
        if path==source: reads.append(path)
        return read_bytes(path)
    monkeypatch.setattr(Path,'read_bytes',observed)
    contract = binding.prepare(source, tmp_path/'prepared.pdb', request)
    assert reads==[source]  # parse, hash and copy share the same owned read
    assert contract['designed_chains'] == ['z', '1']
    assert contract['fixed_positions'] == [['z', 10, 'B'], ['1', -2, '']]
    assert binding.feature_axes([tuple(r) for r in contract['source_residues']]) == {'T': [1], 'z': [2, 3, None, 4], '1': [5]}
    assert (tmp_path/'prepared.pdb').read_bytes().split(b'\n', 1)[1] == source.read_bytes()
    assert contract['source_pdb_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize('settings,error', [({}, 'explicit'), ({'design_chain':'Z'}, 'absent'), ({'design_chain':'a','target_chain':'a'}, 'overlap'), ({'design_chain':'a','fixed_positions':'a:99'}, 'absent')])
def test_role_controls(tmp_path, settings, error):
    source = tmp_path/'input.pdb'; source.write_text(atom(1,'T',1)+atom(2,'a',10))
    with pytest.raises(ValueError, match=error): binding.role_contract(source, settings)


def test_monomer_native_labels_and_legacy_prep(tmp_path):
    from prep_mpnn_designs import process_files
    source = tmp_path/'monomer.pdb'
    source.write_text('REMARK PDBinfo-LABEL: 15 FIXED\n'+atom(1,'a',10)+atom(2,'a',15))
    assert binding.role_contract(source)['fixed_positions'] == [['a',15,'']]
    # Historical antibody preparation remains on its original B-factor path.
    process_files(tmp_path, tmp_path/'legacy')
    text = (tmp_path/'legacy/monomer.pdb').read_text()
    assert 'REMARK PDBinfo-LABEL: 10 FIXED' in text
    assert binding.ROLE_REMARK not in text


PYROSETTA_STUB = r'''
# Explicit inert pose/mover fixture: no scientific computation.
from pathlib import Path
from types import SimpleNamespace as NS
import copy, json, os
AA3 = ['ALA','CYS','ASP','GLU','PHE','GLY','HIS','ILE','LYS','LEU','MET','ASN','PRO','GLN','ARG','SER','THR','VAL','TRP','TYR','UNK']
AA1 = 'ACDEFGHIKLMNPQRSTVWYX'
class Info:
    def __init__(self, pose): self.pose=pose
    def chain(self,i): return self.pose.ids[i-1][0]
    def number(self,i): return self.pose.ids[i-1][1]
    def icode(self,i): return self.pose.ids[i-1][2] or ' '
    def get_reslabels(self,i): return []
class Pose:
    def __init__(self,path):
        self.lines=Path(path).read_text().splitlines(True)
        atoms=[l for l in self.lines if l.startswith('ATOM')]
        self.ids=list(dict.fromkeys((l[21],int(l[22:26]),l[26].strip()) for l in atoms))
        self.names={r:next(l[17:20] for l in atoms if (l[21],int(l[22:26]),l[26].strip())==r) for r in self.ids}
    def pdb_info(self): return Info(self)
    def total_residue(self): return len(self.ids)
    def sequence(self): return ''.join(AA1[AA3.index(self.names[r])] for r in self.ids)
    def residue_type_set_for_pose(self,*a): return NS(name_map=lambda x:x)
    def replace_residue(self,i,res,*a): self.names[self.ids[i-1]]=res
    def dump_pdb(self,path):
        lines=[]
        for line in self.lines:
            if line.startswith('REMARK BMS_MPNN_ROLES '): continue
            if line.startswith('ATOM'):
                r=(line[21],int(line[22:26]),line[26].strip())
                line=line[:17]+self.names[r]+line[20:]
            lines.append(line)
        Path(path).write_text(''.join(lines))
    def clone(self): return copy.deepcopy(self)
    def num_chains(self): return len(dict.fromkeys(r[0] for r in self.ids))
    def chain_begin(self,c):
        name=list(dict.fromkeys(r[0] for r in self.ids))[c-1]
        return next(i+1 for i,r in enumerate(self.ids) if r[0]==name)
    def num_jump(self): return self.num_chains()-1
    def fold_tree(self): return NS(upstream_jump_residue=lambda j:1, downstream_jump_residue=lambda j:self.chain_begin(j+1))
class Mover:
    def apply(self,pose): pass
class XmlObjects:
    @staticmethod
    def create_from_file(path): return XmlObjects()
    @staticmethod
    def create_from_string(text):
        Path(os.environ['BMS_FIXTURE_EVIDENCE'],'relax.xml').write_text(text)
        return XmlObjects()
    def get_mover(self,name): return Mover()
core=NS(chemical=NS(FULL_ATOM_t=1),conformation=NS(ResidueFactory=NS(create_residue=lambda x:x)))
protocols=NS(rosetta_scripts=NS(XmlObjects=XmlObjects))
def init(*a): pass
def pose_from_pdb(path): return Pose(path)
'''

SITE_STUB = r'''
# Patch only weight loading/model calls; real native parser and featurizer run.
import sys, json, os
from pathlib import Path
sys.path.insert(0, '/dl_binder_design/mpnn_fr')
import torch
import util_protein_mpnn as util
class InertModel:
    def __call__(self,X,S,*a):
        return torch.full((*S.shape,21), -3.0, device=S.device)
    def sample(self,X,randn,S,chain_M,chain_encoding,residue_idx,**kw):
        movable=chain_M*kw['chain_M_pos']*kw['mask']
        self.calls=getattr(self,'calls',0)+1
        # Deliberately change relaxed samples to catch pre-thread output clones.
        offset=2 if self.calls<=2 else 3
        sample=torch.where(movable>0,(chain_encoding.long()+offset)%20,S)
        with Path(os.environ['BMS_FIXTURE_EVIDENCE'],'samples.jsonl').open('a') as f:
            f.write(json.dumps(dict(S=S.tolist(), chain_M=chain_M.tolist(), fixed_mask=kw['chain_M_pos'].tolist(), mask=kw['mask'].tolist(), output=sample.tolist(),temperature=kw['temperature'],bias=kw['bias_AAs_np'].tolist()))+'\n')
        return {'S':sample}
def initialize(*args,**kwargs):
    Path(os.environ['BMS_FIXTURE_EVIDENCE'],'model_args.json').write_text(json.dumps(kwargs))
    return InertModel()
util.init_seq_optimize_model=initialize
'''


def install_native_science_stubs(tmp_path):
    image = os.environ.get('BMS_TEST_PROTEINMPNN_SIF')
    if not image or not Path(image).is_file():
        pytest.skip('set BMS_TEST_PROTEINMPNN_SIF to run installed native boundary with inert science')
    stubs = tmp_path/'stubs'; stubs.mkdir()
    package=stubs/'pyrosetta'; package.mkdir()
    (package/'__init__.py').write_text(PYROSETTA_STUB)
    rosetta=package/'rosetta'; (rosetta/'core').mkdir(parents=True)
    (rosetta/'__init__.py').write_text('from pyrosetta import core, protocols\n')
    (rosetta/'core/__init__.py').write_text('')
    (rosetta/'core/scoring.py').write_text('def CA_rmsd(*args): return 0.0\n')
    (stubs/'silent_tools.py').write_text('from types import SimpleNamespace\nsilent_tools=SimpleNamespace()\n')
    (stubs/'sitecustomize.py').write_text(SITE_STUB)
    bin_dir=tmp_path/'bin'; bin_dir.mkdir()
    (bin_dir/'micromamba').write_text('#!/bin/sh\nexit 0\n'); (bin_dir/'micromamba').chmod(0o755)
    executable=bin_dir/'python'
    executable.write_text(f'''#!{sys.executable}
import os,sys,json
from pathlib import Path
args=sys.argv[1:]
with Path({str(tmp_path/'calls.jsonl')!r}).open('a') as f: f.write(json.dumps(args)+'\\n')
if args[0]=='/scripts/proteinmpnn_native_binding.py':
    args[0]={str(ROOT/'scripts/proteinmpnn_native_binding.py')!r}
    cmd=['apptainer','exec','--cleanenv','--bind',{str(ROOT)+':'+str(ROOT)!r},'--bind',{str(tmp_path)+':'+str(tmp_path)!r},{image!r},'/usr/bin/env','PYTHONPATH='+{str(stubs)!r},'BMS_FIXTURE_EVIDENCE='+{str(tmp_path)!r},'/opt/conda/envs/mpnn/bin/python',*args]
    os.execvp(cmd[0],cmd)
if args[0].startswith('/scripts/'): args[0]=str(Path({str(ROOT/'scripts')!r})/Path(args[0]).name)
os.execv({sys.executable!r},[{sys.executable!r},*args])
''')
    executable.chmod(0o755)
    return dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])


def run_native_boundary(tmp_path, *, monomer=False, relax=False, cycles=1):
    jar=os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar or not Path(jar).is_file(): pytest.skip('set BMS_TEST_NEXTFLOW_JAR for production shell test')
    env=install_native_science_stubs(tmp_path)
    source=tmp_path/'source.with.dot.pdb'
    residues=[('z',10,''),('z',10,'B'),('z',12,''),('1',5,''),('1',6,'')]
    if not monomer: residues=[('T',4,''),*residues,('Q',1,'')]
    else: residues=[('z',10,''),('z',12,'')]
    # Complete backbone atoms ensure native featurizer masks represent residues.
    text=''
    for chain, number, insertion in residues:
        for name in ['N','CA','C','O']:
            line=atom(len(text.splitlines())+1,chain,number,insertion=insertion)
            text+=line[:12]+f'{name:^4}'+line[16:]
    source.write_text(text)
    bias=tmp_path/'amino-acid-bias.json'
    bias.write_text(json.dumps({'A':-1.1,'F':0.7})+'\n')
    settings=dict(sequence_design_engine='proteinmpnn', design_chain='z' if monomer else 'z,1', target_chain=None if monomer else 'T',fixed_positions='z:10' if monomer else 'z:10B,1:6',
                  seqs_per_design=2, mpnn_temperature=0.7,mpnn_omitAAs='',mpnn_checkpoint_type='vanilla',mpnn_checkpoint_model='v_48_010',mpnn_backbone_noise=0,
                  mpnn_relax_max_cycles=cycles if relax else 0,mpnn_relax_output=relax,mpnn_relax_seqs_per_cycle=2,mpnn_relax_convergence_rmsd=0,mpnn_relax_convergence_score=0,mpnn_relax_convergence_max_cycles=0,
                  mpnn_num_connections=48,mpnn_bias_AA_jsonl=str(bias),mpnn_output_intermediates=relax and cycles>0,out_dir=str(tmp_path/'out'),allow_retries=False,input_pdb=str(source))
    (tmp_path/'params.json').write_text(json.dumps(settings))
    workflow=tmp_path/'boundary.nf'
    workflow.write_text(f'''nextflow.enable.dsl=2
include {{ PrepMPNN; RunMPNN }} from '{ROOT}/modules/proteinmpnn'
workflow {{
    PrepMPNN(Channel.of(tuple(file(params.input_pdb), [])))
    RunMPNN(PrepMPNN.out.pdbs)
}}
''')
    (tmp_path/'minimal.config').write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="256 MB"\n')
    command=['java','-jar',jar,'-C',str(tmp_path/'minimal.config'),'run',str(workflow),'-params-file',str(tmp_path/'params.json'),'-work-dir',str(tmp_path/'work'),'-with-trace',str(tmp_path/'trace.tsv')]
    result=subprocess.run(command,cwd=tmp_path,env=env,text=True,capture_output=True,timeout=180)
    (tmp_path/'nextflow.stdout').write_text(result.stdout)
    (tmp_path/'nextflow.stderr').write_text(result.stderr)
    assert result.returncode==0,result.stdout+result.stderr
    return source,settings


@pytest.mark.parametrize('monomer,relax,cycles', [(False,False,0),(True,False,0),(False,True,1),(False,True,0)])
def test_actual_shell_native_roles_and_threading(tmp_path, monomer, relax, cycles):
    source,settings=run_native_boundary(tmp_path,monomer=monomer,relax=relax,cycles=cycles)
    records=[json.loads(p.read_text()) for p in sorted((tmp_path/'out/pdb_files').glob('*.json'))]
    assert records
    for record in records:
        assert record['source_pdb']=='source.with.dot.pdb'
        assert record['design'].startswith('source.with.dot_seq_')
        assert record['source_pdb_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
        assert record['native_designed_chain_order']==(['z'] if monomer else ['1','z'])
        assert record['input_chain_sequences']['z']==('AA' if monomer else 'AAA')
        assert record['chain_sequences']['z']==('AE' if monomer else ('GAG' if relax and cycles else 'FAF'))
        if not monomer:
            assert record['chain_sequences']['1']==('FA' if relax and cycles else 'EA')
            assert record['chain_sequences']['T']=='A'
            assert record['chain_sequences']['Q']=='A'
        output=tmp_path/'out/pdb_files'/f"{record['design']}.pdb"
        assert list(binding.pdb_domain(output))==list(binding.pdb_domain(source))
        for line in source.read_text().splitlines():
            if line.startswith('ATOM') and line[21] in {'T','Q'}: assert line in output.read_text()
        assert len((tmp_path/'out/pdb_files'/f"{record['design']}.json").read_text().splitlines())==1
    samples=[json.loads(l) for l in (tmp_path/'samples.jsonl').read_text().splitlines()]
    assert all(r['temperature']==0.7 for r in samples)
    assert all(r['bias'][0]==-1.1 and r['bias'][4]==0.7 for r in samples)
    model_args=json.loads((tmp_path/'model_args.json').read_text())
    assert model_args['backbone_noise']==0
    assert model_args['num_connections']==48
    assert model_args['checkpoint_path']=='/dl_binder_design/mpnn_fr/ProteinMPNN/vanilla_model_weights/v_48_010.pt'
    if not monomer:
        # Native order: chain 1 (2 residues), z (4 feature slots), Q, T.
        assert samples[0]['chain_M']==[[1,1,1,1,1,1,0,0]]
        assert samples[0]['fixed_mask']==[[1,0,1,0,1,1,1,1]]
        assert samples[0]['mask']==[[1,1,1,1,0,1,1,1]]
    if relax:
        import xml.etree.ElementTree as ET
        xml=ET.fromstring((tmp_path/'relax.xml').read_text())
        assert xml.find("./RESIDUE_SELECTORS/Index[@name='chainA']").get('resnums')=='2,3,4,5,6'
        assert xml.find("./RESIDUE_SELECTORS/Index[@name='chainB']").get('resnums')=='1,7'
        assert len(records)==(4 if cycles else 2)
    else: assert len(records)==2
