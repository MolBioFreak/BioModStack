"""CPU installed-native reader/writer and sampler-control regressions.

Run directly with the installed FA image Python; no pytest/model weights needed.
The denoiser boundary below is deliberately inert, not scientific inference.
"""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import pickle
import socket
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
NATIVE = Path(os.environ.get('BMS_G09_FAMPNN_SOURCE', '/app/fampnn'))
sys.path.insert(0, str(NATIVE))
os.environ['CUDA_VISIBLE_DEVICES'] = ''
socket.socket.connect = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('network prohibited'))
import numpy as np
import pandas as pd
import torch
from fampnn import sampling_utils
from fampnn.data import protein
from fampnn.data.data import load_feats_from_pdb
from fampnn.model import sd_model
from prep_fampnn_constraints_generic import write_constraints, fampnn_correspondence
from analyse_fampnn import average_per_residue_bfactor, compute_psce_profile, psce_policy, predicted_sidechain_summary
from analyse_fampnn_seq_probs import analyze_sample_pkl
from filter_fampnn import load_json_data, filter_designs, copy_filtered_designs
import fampnn_native_binding as binding
import fampnn_native_runtime as runtime


def atom(serial, chain, number, name='CA', bf=.2, residue='LYS'):
    return f'ATOM  {serial:5d} {name:>4s} {residue} {chain}{number:4d}    {0:8.3f}{0:8.3f}{0:8.3f}{1:6.2f}{bf:6.2f}          {name[0]:>2s}\n'


def definition(text, name, scope):
    node = next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<installed-native-definition>', 'exec'), scope)
    return scope[name]


class FidelityRepair(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.work = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()

    def test_public_csv_actual_native_reader_and_masks(self):
        source, prepared = self.work/'source', self.work/'prepared'
        source.mkdir(); prepared.mkdir()
        pdb = ''.join(atom(i,c,n) for i,(c,n) in enumerate([('A',10),('A',12),('B',21),('C',3)],1))
        for folder in (source,prepared): (folder/'fixture.pdb').write_text(pdb)
        csv = self.work/'constraints.csv'
        request = dict(sequence_design_mode='binder_design', design_chain='A', target_chain='B', fixed_positions='A:10-11', fampnn_fix_target_sidechains=True)
        write_constraints(source,csv,request,prepared)
        # Execute the actual installed reader statements, not a reimplemented parser.
        text = (NATIVE/'fampnn/inference/seq_design.py').read_text()
        tree = ast.parse(text)
        reader = next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='cfg.fixed_pos_csv is not None')
        scope = dict(cfg=SimpleNamespace(fixed_pos_csv=str(csv)),pd=pd)
        exec(compile(ast.Module(body=[reader],type_ignores=[]),'<native-csv-reader>','exec'),scope)
        batch = dict(residue_index=torch.tensor([[10,12,21,3]]),chain_index=torch.tensor([[0,0,1,2]]))
        masks = sampling_utils.get_override_masks(batch,['fixture'],[{'A':0,'B':1,'C':2}],scope['fixed_pos_df'],'seq_design')
        self.assertEqual([v.tolist() for v in masks], [[[1,0,1,1]],[[0,0,1,1]]])
        self.assertEqual(json.loads(Path(str(csv)+'.custody.json').read_text())['fixture'], json.loads((prepared/'fixture.fampnn_source.json').read_text()))
        # Binder transport remains independently staged beside its native CSV.
        from prep_binder_fampnn_constraints import prepare
        anchors=self.work/'anchors.json';anchors.write_text('{"anchors": []}')
        previous=os.getcwd()
        try:
            os.chdir(self.work)
            prepare(source,prepared,self.work/'binder.csv',request,anchors)
        finally: os.chdir(previous)
        self.assertTrue((self.work/'transport/source.csv.custody.json').is_file())

    def test_full_precision_and_exact_materialization(self):
        pdb=self.work/'candidate1.pdb'
        pdb.write_text(atom(1,'A',1,'CG',.30)+atom(2,'B',2,'CG',.31)+atom(3,'B',2,'CD',.32)+atom(4,'C',3,'CG',5))
        average_per_residue_bfactor(self.work,'A,B',True,self.work)
        metadata=json.loads(pdb.with_suffix('.json').read_text())
        self.assertAlmostEqual(metadata['fampnn_avg_psce'],.3075)
        selected=filter_designs(load_json_data(self.work),.308,None)
        self.assertIn('candidate1',selected)
        pdb.rename(self.work/'prefix_candidate10.pdb')
        self.assertEqual(copy_filtered_designs(selected,self.work,self.work,self.work/'out'),0)
        self.assertFalse(list((self.work/'out').glob('*.pdb')))
        self.assertTrue((self.work/'out/candidate1.json').is_file())

    def test_optional_probability_axes_fixed_scope_and_native_alphabet(self):
        sample=self.work/'native.pkl'
        payload=dict(seq_probs=np.array([[.2,.8]+[0.]*19,[.9,.1]+[0.]*19]), pred_aatype=np.array([0]),seq_mask=np.array([1,1]),aatype_override_mask=np.array([1,0]),residue_index=np.array([10,12]),chain_index=np.array([0,0]))
        sample.write_bytes(pickle.dumps(payload));result=analyze_sample_pkl(sample)
        self.assertFalse(result['fampnn_seq_probs_available'])
        payload['pred_aatype']=np.array([0,1]);sample.write_bytes(pickle.dumps(payload));result=analyze_sample_pkl(sample)
        self.assertEqual(result['designed_residue_count'],1)
        self.assertEqual(result['scored_designed_residue_count'],1)
        self.assertTrue(all(r['residue_index']==12 for r in result['fampnn_top_model_favored_mutations']))
        self.assertEqual(result['fampnn_top_model_favored_mutations'][0]['mutation'],'R12A')
        payload['seq_probs'][1]=0;sample.write_bytes(pickle.dumps(payload))
        self.assertFalse(analyze_sample_pkl(sample)['fampnn_seq_probs_available'])

    def test_pinned_instrumentation_compile_and_feature_cache_ownership(self):
        for relative in binding.SOURCE_SHA256['fampnn']:
            original=(NATIVE/relative).read_bytes()
            compile(binding.instrument_source(relative,original),relative,'exec')
        pdb=self.work/'native.pdb';pdb.write_text(atom(1,'A',10)+atom(2,'B',20))
        calls=[]
        def loader(path):
            calls.append(path); return load_feats_from_pdb(path)
        cache={};state=torch.get_rng_state().clone()
        first=runtime.cached_features(cache,loader,str(pdb));second=runtime.cached_features(cache,loader,str(pdb))
        self.assertEqual(len(calls),1)
        self.assertTrue(torch.equal(state,torch.get_rng_state()))
        for key,value in first.items():
            if isinstance(value,torch.Tensor): self.assertTrue(torch.equal(value,second[key]),key)
        first['residue_index'][0]=900
        self.assertEqual(second['residue_index'][0].item(),10)

    def test_native_sample_control_rng_parity_and_actual_writer(self):
        original=(NATIVE/'fampnn/model/sd_model.py').read_text()
        modified=runtime.instrument('fampnn/model/sd_model.py',original)
        native_sample=definition(original,'sample',dict(vars(sd_model)))
        adapted_sample=definition(modified,'sample',dict(vars(sd_model)))
        class InertDenoiser:
            # Deliberately uses RNG so call/order changes are observable.
            def denoiser(self,x,aa,**kw):
                b,n,a,_=x.shape
                return torch.rand_like(x), torch.ones_like(aa), dict(seq_probs=torch.softmax(torch.rand(b,n,21),-1),seq_logits=torch.rand(b,n,21),scn_diffusion_aux={'psce':torch.rand(b,n,33)})
            sidechain_pack=sd_model.SeqDenoiser.sidechain_pack
        inputs=dict(x=torch.zeros(1,3,37,3),aatype=torch.tensor([[11,11,11]]),seq_mask=torch.ones(1,3),missing_atom_mask=torch.zeros(1,3,37),residue_index=torch.tensor([[10,12,21]]),chain_index=torch.tensor([[0,0,1]]),timesteps=torch.tensor([[0.,.5,1.]]),temperature=0.,aatype_override_mask=torch.tensor([[1,0,1]]),scn_override_mask=torch.tensor([[1,0,0]]),scd_inputs={})
        for seq_only in (False,True):
            for repack in (False,True):
                for threshold in (None,0.,.3):
                    kw=dict(inputs,seq_only=seq_only,repack_last=repack,psce_threshold=threshold)
                    torch.manual_seed(27);expected=native_sample(InertDenoiser(),**copy.deepcopy(kw));expected_rng=torch.get_rng_state().clone()
                    torch.manual_seed(27);actual=adapted_sample(InertDenoiser(),**copy.deepcopy(kw))
                    self.assertTrue(torch.equal(expected_rng,torch.get_rng_state()))
                    for left,right in [(expected[0],actual[0]),(expected[1],actual[1]),(expected[2]['psce'],actual[2]['psce']),(expected[2]['seq_probs'],actual[2]['seq_probs'])]: self.assertTrue(torch.equal(left,right))
                    self.assertNotIn('xt_traj',actual[2])
                    expected_mask=[[False,not seq_only or repack,not seq_only or repack]]
                    self.assertEqual(actual[2]['bms_psce_observed_mask'].tolist(),expected_mask)
        # Actual native structure writer, with controlled CPU tensors, not fake PDB output.
        samples=dict(x_denoised=torch.zeros(1,3,37,3), pred_aatype=inputs['aatype'], seq_mask=inputs['seq_mask'],missing_atom_mask=inputs['missing_atom_mask'],residue_index=inputs['residue_index'],chain_index=inputs['chain_index'],psce=torch.tensor([[[0.]*33,[.31]*33,[.62]*33]]),bms_psce_observed_mask=torch.tensor([[False,True,True]]))
        pdb=self.work/'native_sample0.pdb'
        sd_model.SeqDenoiser.save_samples_to_pdb(samples,[str(pdb)])
        runtime.write_psce_scope(samples,[str(pdb)])
        profile=compute_psce_profile(pdb,psce_policy())
        scoped=predicted_sidechain_summary(pdb,profile)
        self.assertEqual(scoped['residue_count'],2)
        self.assertAlmostEqual(scoped['avg_psce'],.465)
        self.assertAlmostEqual(profile['summary']['avg_psce'],.31)
        # Native PKL export comprehension/crop is used verbatim.
        sample_j={k:v[0].cpu().numpy() for k,v in samples.items()}
        sample_j={k:v[sample_j['seq_mask'].astype(bool)] for k,v in sample_j.items()}
        self.assertEqual(sample_j['bms_psce_observed_mask'].tolist(),[False,True,True])

    def test_instrumented_actual_writer_custody_and_strict_probability(self):
        from fampnn.data import pdb_utils
        from fampnn_policy_resolution import bind_native_candidates
        # Instrument the actual parser and writer function definitions. Real
        # native atom masks/Protein/to_pdb are used; only model predictions are fixtures.
        source=self.work/'input.pdb'
        source.write_text(atom(1,'Z',10,'CG')+atom(2,'B',21,'CG'))
        scope=dict(vars(protein),_bms=binding)
        text=binding.instrument_source('fampnn/data/protein.py',(NATIVE/'fampnn/data/protein.py').read_bytes())
        parser=definition(text,'read_pdb',scope)
        parsed,_=parser(str(source))
        context=binding.capture_input(source,vars(parsed))
        writer_scope=dict(vars(pdb_utils),_bms=binding)
        text=binding.instrument_source('fampnn/data/pdb_utils.py',(NATIVE/'fampnn/data/pdb_utils.py').read_bytes())
        definition(text,'write_to_pdb',writer_scope)
        batched_writer=definition(text,'write_batched_to_pdb',writer_scope)
        save_scope=dict(vars(sd_model),write_batched_to_pdb=batched_writer)
        save=definition((NATIVE/'fampnn/model/sd_model.py').read_text(),'save_samples_to_pdb',save_scope)
        candidates=self.work/'samples';candidates.mkdir()
        candidate=candidates/'input_sample0.pdb'
        samples=dict(x_denoised=torch.zeros(1,2,37,3),pred_aatype=torch.tensor([[11,11]]),seq_mask=torch.ones(1,2),missing_atom_mask=torch.zeros(1,2,37),residue_index=torch.tensor([[10,21]]),chain_index=torch.tensor([[0,1]]),psce=torch.ones(1,2,33)*.31,aatype_override_mask=torch.tensor([[1,0]]),seq_probs=torch.ones(1,2,21)/21)
        binding.save_samples(save,samples,[str(candidate)],[context])
        sample=self.work/'input_sample0.pkl'
        sample.write_bytes(pickle.dumps({k:v[0].cpu().numpy() for k,v in samples.items()}))
        binding.capture_sample(sample,candidate,context)
        receipt=json.loads(binding.receipt_path(candidate).read_text())
        self.assertEqual(receipt['records'][0]['source'],'Z:10:')
        self.assertEqual(receipt['records'][0]['candidate'],'A:10:')
        Path(str(candidate)+'.fa_binding').write_text(json.dumps(receipt))
        csv=self.work/'input.csv';csv.write_text('input,,\n')
        custody=dict(source_structure_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),prepared_sha256=receipt['source_pdb_sha256'])
        (self.work/'input.fampnn_source.json').write_text(json.dumps(custody))
        joined=fampnn_correspondence(candidate,csv)
        self.assertEqual(joined['source_residue_mapping'][0]['source']['chain_id'],'Z')
        self.assertEqual(joined['source_residue_mapping'][0]['output']['chain_id'],'A')
        ids=['Z:10:','B:21:']
        policy=dict(schema_version=1,owner='protein_design',version=1,declaration='declared_protein_inputs',dialect='fampnn-18363df253dbeb7b2cb963daf7a732fbaa25157d',require_full_coverage=False,allow_summary_override=True,inputs={'input':dict(input_domain=ids,sequence_design=ids,summary=ids,summary_override=None,mutation_override=None,artifact_binding=dict(producer_input_id='input',source_pdb_sha256=receipt['source_pdb_sha256']))})
        policy=bind_native_candidates(policy,candidates)
        result=analyze_sample_pkl(sample,core_protein_scientific_contract=1,analysis_policy=policy,source_pdb_dir=self.work,candidate_pdb_dir=candidates)
        self.assertTrue(result['fampnn_seq_probs_available'])
        self.assertEqual(result['designed_residue_count'],1)
        self.assertAlmostEqual(result['fampnn_mean_sampled_prob'],1/21)
        self.assertEqual(result['resolved_mutation_membership'],['B:21:'])

    def test_shape_optional_analysis_and_requested_filter(self):
        from fampnn_shape_analysis import analyze_sample
        (self.work/'samples').mkdir()
        p=self.work/'samples/input_sample0.pdb';p.write_text(atom(1,'A',10,'CG',.30)+atom(2,'A',12,'CG',.31)+atom(3,'A',12,'CD',.32))
        result=analyze_sample(self.work,'input_sample0',{'fampnn_max_psce':.308})
        self.assertTrue(result['passes_psce_filter'])
        self.assertFalse(analyze_sample(self.work,'input_sample0',{'fampnn_max_psce':.307})['passes_psce_filter'])
        self.assertTrue(analyze_sample(self.work,'absent',{'fampnn_max_psce':0})['passes_psce_filter'])

    def test_plr_manifest_actual_cli_and_native_masks(self):
        manifest=self.work/'manifest.json';manifest.write_text(json.dumps({'sequence_fixed_positions_spec':'A10,B21'}))
        (self.work/'input.pdb').write_text(atom(1,'A',10)+atom(2,'A',12)+atom(3,'B',21))
        csv=self.work/'plr.csv'
        subprocess.run([sys.executable,str(ROOT/'scripts/prep_fampnn_constraints_from_spec.py'),'--input_dir',str(self.work),'--manifest',str(manifest),'--out_csv',str(csv),'--fix_sidechains'],check=True)
        df=pd.read_csv(csv,names=['fixed_pos_seq','fixed_pos_scn'],index_col=0).fillna('')
        batch=dict(residue_index=torch.tensor([[10,12,21]]),chain_index=torch.tensor([[0,0,1]]))
        masks=sampling_utils.get_override_masks(batch,['input'],[{'A':0,'B':1}],df,'seq_design')
        self.assertEqual([m.tolist() for m in masks],[[[1,0,1]],[[1,0,1]]])


if __name__ == '__main__':
    unittest.main(verbosity=2)
