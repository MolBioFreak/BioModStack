"""CPU-only installed-native regression. No model construction or inference.

Run inside pinned ESMFold2 image with CCD read-only and an evidence directory.
"""
import json
import os
from pathlib import Path
import socket
import sys

os.environ.update(CUDA_VISIBLE_DEVICES='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
def deny(*args, **kwargs):
    raise RuntimeError('network forbidden in preparation regression')
socket.socket.connect = deny
socket.create_connection = deny
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import torch
import numpy as np
from esm.utils.structure.input_builder import (ProteinInput, DNAInput, RNAInput, LigandInput,
    StructurePredictionInput, deserialize_structure_prediction_input, serialize_structure_prediction_input)
from esm.utils.msa import MSA
from esm.models.esmfold2.processor import ESMFold2InputBuilder
import run_esmfold2_inference as runner

def run(output):
    torch.set_num_threads(1)
    fixtures = {
        'modified': {'sequences': [{'type':'protein','id':['B','A'],'sequence':'ACMS','modifications':[{'position':2,'ccd':'MSE'}]}]},
        'mixed': {'sequences': [{'type':'protein','id':'B','sequence':'ACMS'}, {'type':'dna','id':'D','sequence':'ACGT','modifications':[{'position':1,'ccd':'5CM'}]}, {'type':'rna','id':'R','sequence':'ACGU'}, {'type':'ligand','id':'L','smiles':'CCO'}]},
        'no_protein': {'sequences':[{'type':'dna','id':'D','sequence':'ACGT'}]},
        'inline': {'sequences':[{'type':'protein','id':'A','sequence':'ACMS','msa':{'sequences':['ACMS','AC-S']}}]},
        'distogram': {'sequences':[{'type':'protein','id':'A','sequence':'ACMS'}], 'distogram_conditioning':[{'chain_id':'A','distogram':[[0,4,6,8],[4,0,4,6],[6,4,0,4],[8,6,4,0]]}]},
        'bonds': {'sequences':[{'type':'protein','id':'A','sequence':'C'},{'type':'protein','id':'B','sequence':'C'}], 'covalent_bonds':[{'chain_id1':'A','res_idx1':0,'atom_idx1':5,'chain_id2':'B','res_idx2':0,'atom_idx2':5}]},
    }
    native_classes = dict(ProteinInput=ProteinInput, DNAInput=DNAInput, RNAInput=RNAInput,
        LigandInput=LigandInput, StructurePredictionInput=StructurePredictionInput, MSA=MSA)
    def build(document):
        args = runner.build_parser().parse_args(['--output-dir', str(output), '--complex-components-json', json.dumps(document)])
        return runner.build_structure_prediction_input(args, **native_classes)[0]
    results = {}
    for name, document in fixtures.items():
        argv, receipt = runner.compile_workflow_request({'core_protein_scientific_contract':1,
            'complex_components':document, 'seed':0, 'num_loops':2, 'num_sampling_steps':7,
            'num_diffusion_samples':2, 'output_dir':str(output)}, {})
        args = runner.build_parser().parse_args(argv)
        actual, manifest = runner.build_structure_prediction_input(args, **native_classes)
        expected = deserialize_structure_prediction_input(document)
        assert serialize_structure_prediction_input(actual) == serialize_structure_prediction_input(expected), name
        a, ac = ESMFold2InputBuilder().prepare_input(actual, seed=0, device='cpu')
        b, bc = ESMFold2InputBuilder().prepare_input(expected, seed=0, device='cpu')
        checked = []
        for key, value in a.items():
            if isinstance(value, torch.Tensor):
                assert torch.equal(value, b[key]), (name, key)
                checked.append(key)
        np.savez_compressed(output / (name + '.npz'), **{k:v.numpy() for k,v in a.items() if isinstance(v,torch.Tensor)})
        results[name] = {'equal_tensor_keys':checked,'parsed':serialize_structure_prediction_input(actual),
            'seed':args.seed,'loops':args.num_loops,'steps':args.num_sampling_steps,'samples':args.num_diffusion_samples}
    empty = build({'components':[{'type':'protein','id':'A','sequence':'ACMS','msa':{'sequences':[]}}]})
    assert empty.sequences[0].msa is not None and empty.sequences[0].msa.sequences == []
    copies = build({'components':[{'type':'protein','id':'B','count':3,'sequence':'ACMS'}, {'type':'protein','id':'A','sequence':'GG'}]})
    assert copies.sequences[0].id == ['B','C','D']
    assert copies.sequences[1].id == 'A'
    ESMFold2InputBuilder().prepare_input(copies, seed=0, device='cpu')
    for kind, sequence in [('dna','ACGU'), ('rna','ACGT')]:
        assert build({'components':[{'type':kind,'id':'A','sequence':sequence}]}).sequences[0].sequence == sequence
    pdb = output / 'context.pdb'
    pdb.write_text(''.join(f'HETATM{i:5d}  CA  {name:3s} {chain}{i:4d}    {0.:8.3f}{0.:8.3f}{0.:8.3f}{1.:6.2f}{0.:6.2f}           C\n' for i,(chain,name) in enumerate([('Z','MSE'),('Z','SEP'),('Z','ALA'),('A','GLY'),('L','ATP')],1)))
    components = runner.parse_pdb_polymer_components(pdb, include_ligands=True)
    assert [x['id'] for x in components] == ['Z','A','L']
    assert components[0]['sequence'] == 'MSA'
    assert components[0]['modifications'] == [{'position':0,'ccd':'MSE'}, {'position':1,'ccd':'SEP'}]
    assert components[2]['ccd'] == ['ATP']
    ESMFold2InputBuilder().prepare_input(build({'components':components}),seed=0,device='cpu')
    msa_file=output/'query.a3m'; msa_file.write_text('>query\nMSA\n>hit\nM-A\n')
    from run_esmfold2_batch import validation_requests
    requests=validation_requests([str(pdb)], [str(msa_file)], {'esmfold2_validation_seed':0,
        'esmfold2_validation_num_loops':2,'esmfold2_validation_num_sampling_steps':7,
        'esmfold2_validation_num_diffusion_samples':2}, output/'batch')
    args=runner.build_parser().parse_args(requests[0]['argv'])
    actual,_=runner.build_structure_prediction_input(args,**native_classes)
    assert actual.sequences[0].msa.sequences == ['MSA','M-A']
    assert args.seed == 0 and args.num_loops == 2 and args.num_sampling_steps == 7 and args.num_diffusion_samples == 2
    results['pdb'] = components
    results['batch'] = requests
    results['copies'] = serialize_structure_prediction_input(copies)
    # Inert model boundary: exercise actual writer and resident caller, not inference.
    from types import SimpleNamespace
    from unittest.mock import patch
    # Model module eagerly initializes Triton even for CPU import. Replace this
    # entire model boundary, never enable GPU access to qualify a writer.
    from types import ModuleType
    model_module = ModuleType('transformers.models.esmfold2.modeling_esmfold2')
    class ESMFold2Model:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            raise AssertionError('real model construction forbidden')
    model_module.ESMFold2Model = ESMFold2Model
    sys.modules[model_module.__name__] = model_module
    from Bio.PDB import PDBParser, MMCIFIO
    from io import StringIO
    structure = PDBParser(QUIET=True).get_structure('fixture', str(pdb))
    writer = MMCIFIO(); writer.set_structure(structure)
    stream = StringIO(); writer.save(stream)
    sample = SimpleNamespace(complex=SimpleNamespace(to_mmcif=lambda: stream.getvalue()),
        plddt=torch.tensor([0.25, 0.75]), pae=torch.tensor([[0., 1.], [2., 0.]]),
        ptm=torch.tensor(0.6), iptm=torch.tensor(0.4))
    calls = []
    class InertModel:
        def to(self, device):
            return self
        def eval(self):
            return self
    def inert_fold(self, model, spi, **kwargs):
        calls.append(kwargs)
        return [sample, sample]
    from run_esmfold2_batch import run_requests
    writer_requests=[]
    for name in ('writer_one','writer_two'):
        writer_requests.append({'argv':['--sequence','ACMS','--sequence-name',name,
            '--output-dir',str(output/'writer'/name),'--device','cpu','--seed','0',
            '--num-loops','2','--num-sampling-steps','7','--num-diffusion-samples','2']})
    with patch.object(ESMFold2Model, 'from_pretrained', return_value=InertModel()) as load, patch.object(ESMFold2InputBuilder, 'fold', inert_fold):
        run_requests(writer_requests)
        assert load.call_count == 1
    assert len(calls) == 2 and all(c['seed'] == 0 and c['num_loops'] == 2 and c['num_sampling_steps'] == 7 and c['num_diffusion_samples'] == 2 for c in calls)
    from normalize_esmfold2_validation import normalize_design
    normalized=output/'normalized'; normalized.mkdir(exist_ok=True)
    for name in ('writer_one','writer_two'):
        design=output/(name+'.pdb'); design.write_text(pdb.read_text())
        rows=normalize_design(design,output/'writer',normalized)
        assert len(rows)==2
        for row in rows:
            assert row['scalar_dialect']['name']=='biohub_esmfold2_token_scalar_v1'
            with np.load(normalized/row['native_confidence']) as arrays:
                np.testing.assert_array_equal(arrays['pae'],sample.pae.numpy())
                np.testing.assert_array_equal(arrays['plddt'],sample.plddt.numpy())
    results['inert_writer']={'model_initializations':1,'fold_calls':calls,'samples_retained':4,
        'scope':'fixture output only; native model boundary replaced; not inference'}
    if len(sys.argv) > 2:
        emitted = json.loads(Path(sys.argv[2]).read_text())
        consumed = []
        for meta, request, sources, files in emitted:
            argv, receipt = runner.compile_workflow_request(request, dict(zip(sources, files)))
            args = runner.build_parser().parse_args(argv)
            actual, _ = runner.build_structure_prediction_input(args, **native_classes)
            ESMFold2InputBuilder().prepare_input(actual, seed=0, device='cpu')
            consumed.append({'name': args.sequence_name, 'sequence': actual.sequences[0].sequence,
                'seed': args.seed, 'parsed': serialize_structure_prediction_input(actual)})
        assert [row['sequence'] for row in consumed] == ['ACMS', 'ACMT']
        assert len({row['name'] for row in consumed}) == 2 and all(row['seed'] == 0 for row in consumed)
        results['nextflow_batch_consumed'] = consumed
    (output/'native-results.json').write_text(json.dumps(results,indent=2))
    print('PASS: native parser/features, copies, identity, PDB chemistry, batch MSA/controls, inert writer and resident caller')

if __name__ == '__main__':
    output=Path(sys.argv[1]); output.mkdir(parents=True,exist_ok=True); run(output)
