"""ESMFold2 compiler/source/caller regressions; native tensors have a separate probe."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_esmfold2_inference as runner
import run_esmfold2_batch as batch
import run_binder_blind_pose as blind


def pdb(path, chain, names):
    path.write_text(''.join(f'HETATM{i:5d}  CA  {name:3s} {chain}{i:4d}    {0.:8.3f}{0.:8.3f}{0.:8.3f}{1.:6.2f}{0.:6.2f}           C\n' for i,name in enumerate(names,1)))
    return path


def test_compiler_keeps_native_task_document_and_zero(tmp_path):
    document={'components':[{'type':'protein','id':['B','A'],'sequence':'ACMS',
        'modifications':[{'position':2,'ccd':'MSE'}], 'msa':{'sequences':['ACMS','AC-S']}}],
        'covalent_bonds':[{'chain_id1':'B','res_idx1':1,'atom_idx1':5,'chain_id2':'A','res_idx2':1,'atom_idx2':5}],
        'distogram_conditioning':[{'chain_id':'B','distogram':[[0,4],[4,0]]}]}
    argv,receipt=runner.compile_workflow_request({'core_protein_scientific_contract':1,
        'complex_components':document,'seed':0,'num_loops':2,'num_sampling_steps':7,
        'num_diffusion_samples':2}, {})
    args=runner.build_parser().parse_args(argv)
    actual=json.loads(args.complex_components_json)
    for key in ('covalent_bonds','distogram_conditioning'):
        assert actual[key]==document[key]
    for key in ('id','sequence','modifications','msa'):
        assert actual['components'][0][key]==document['components'][0][key]
    assert args.seed==0 and args.num_loops==2 and args.num_sampling_steps==7 and args.num_diffusion_samples==2
    assert receipt['argv']==argv


def test_selected_source_keeps_independent_target_chemistry_not_design_history(tmp_path):
    candidate=pdb(tmp_path/'candidate.pdb','B',['MSE','SEP','ALA'])
    target=pdb(tmp_path/'target.pdb','T',['MSE','SEP','ALA'])
    manifest={'schema_version':1,'target_chains':['T'],'candidates':[
        {'candidate_key':'candidate','source_pdb':candidate.name,'binder_chains':['B']}]}
    result=blind.compile_selected_inputs(manifest,tmp_path,target)[0]
    binder,context=result['components']
    assert binder['sequence']=='MSA' and context['sequence']=='MSA'
    assert 'modifications' not in binder
    assert context['modifications']==[{'position':0,'ccd':'MSE'},{'position':1,'ccd':'SEP'}]


def test_seeded_batch_is_resident_unseeded_is_fresh_and_receipts_scoped(monkeypatch,tmp_path):
    import os
    calls=[]; subprocesses=[]
    monkeypatch.setenv('BMS_ESMFOLD2_EFFECTIVE_SETTINGS','outer')
    monkeypatch.setattr(batch,'predict',lambda argv,model_cache: calls.append((id(model_cache),os.environ.get('BMS_ESMFOLD2_EFFECTIVE_SETTINGS'))))
    monkeypatch.setattr(batch.subprocess,'run',lambda argv,check:subprocesses.append(argv))
    args=['--sequence','ACMS','--output-dir',str(tmp_path)]
    batch.run_requests([{'argv':args+['--seed','0'],'receipt':'one'},
        {'argv':args+['--seed','1'],'receipt':'two'},{'argv':args}])
    assert len(calls)==2 and calls[0][0]==calls[1][0]
    assert [c[1] for c in calls]==['one','two']
    assert len(subprocesses)==1 and '--seed' not in subprocesses[0]
    assert os.environ['BMS_ESMFOLD2_EFFECTIVE_SETTINGS']=='outer'


def test_nucleic_identity_and_source_modification_roster(tmp_path):
    assert runner.normalize_component_sequence('ACGU','dna')=='ACGU'
    assert runner.normalize_component_sequence('ACGT','rna')=='ACGT'
    source=pdb(tmp_path/'source.pdb','Z',['MSE','SEP','ALA'])
    rows=runner.parse_pdb_polymer_components(source)
    assert rows[0]['sequence']=='MSA'
    assert [m['position'] for m in rows[0]['modifications']]==[0,1]
