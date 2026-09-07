"""Non-model, physical-file acceptance for the marked native Boltz producer."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'platform/api'), str(ROOT / 'platform/api/services')]
from write_structure_producer_manifest import build_manifest
from aligned_error_utils import load_aligned_error_artifact

# Exact pinned types.py Chain/Residue/AtomV2 layouts, not simplified JSON ledgers.
CHAIN = np.dtype([(k, t) for k,t in [('name','<U5'),('mol_type','i1'),('entity_id','i4'),('sym_id','i4'),('asym_id','i4'),('atom_idx','i4'),('atom_num','i4'),('res_idx','i4'),('res_num','i4'),('cyclic_period','i4')]])
RES = np.dtype([(k,t) for k,t in [('name','<U5'),('res_type','i1'),('res_idx','i4'),('atom_idx','i4'),('atom_num','i4'),('atom_center','i4'),('atom_disto','i4'),('is_standard','?'),('is_present','?')]])
ATOM = np.dtype([('name','<U4'),('element','i1'),('coords','3f4'),('is_present','?'),('bfactor','f4'),('plddt','f4')])

def native_data_writer():
    """Execute pinned data-only definitions, never provider imports/model code.

    Source: https://raw.githubusercontent.com/Novel-Therapeutics/boltz-community/
    7ebf1be087d4d61a02234c878402838bf3712d8b/src/boltz/data/{types.py,write/pdb.py}.
    The fixture snapshots are byte-exact. AST selection only excludes dependency
    imports and unrelated classes. The real remove_invalid_chains/to_pdb bodies
    run unchanged. The sole Chem shim handles fixture carbon atoms, not modeling.
    """
    import ast
    import __future__
    from dataclasses import dataclass
    from types import SimpleNamespace
    from typing import Optional
    directory=ROOT/'tests/fixtures/boltz_7ebf1be'
    namespace={'np':np,'dataclass':dataclass,'Optional':Optional,'NumpySerializable':object,'__name__':__name__}
    for filename,digest in [('types.py.txt','446f7f7cfbf45c6015f597be67df69e0dd0bca46e1ee1d6e3b9f438687bd30b6'),('pdb.py.txt','e2a6e35723f39024e2a0c2e94b14c85fec7b2725d055e0961239815d46e8f5bd')]:
        source=(directory/filename).read_bytes()
        if hashlib.sha256(source).hexdigest()!=digest:
            raise AssertionError('pinned provider fixture changed')
        tree=ast.parse(source)
        if filename=='types.py.txt':
            names={'AtomV2','Residue','Chain','BondV2','Interface','Coords','Ensemble'}
            selected=[node for node in tree.body if (isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in names) or (isinstance(node,ast.ClassDef) and node.name=='StructureV2')]
        else:
            selected=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='to_pdb']
        exec(compile(ast.Module(body=selected,type_ignores=[]),filename,'exec',flags=__future__.annotations.compiler_flag),namespace)
    def element(number):
        if number!=6: raise AssertionError('carbon-only fixture')
        return 'C'
    namespace['Chem']=SimpleNamespace(GetPeriodicTable=lambda:SimpleNamespace(GetElementSymbol=element))
    namespace['const']=SimpleNamespace(chain_type_ids={'NONPOLYMER':3})
    return namespace


class NativeIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.native = self.root / 'boltz_results_yamls'
        self.pred = self.root / 'predictions'
        self.pred.mkdir()
        self.orig = self.native / 'predictions/input'
        self.orig.mkdir(parents=True)
        self.ledger = self.native / 'processed/structures/input.npz'
        self.ledger.parent.mkdir(parents=True)
        self.chains = np.zeros(4, CHAIN)
        self.res = np.zeros(7, RES)
        self.atoms = np.zeros(7, ATOM)
        self.mask = np.array([True,False,True,True])
        start = 0
        for i,(name, asym, count) in enumerate([('T',7,3),('X',3,1),('H',9,2),('L',2,1)]):
            self.chains[i] = (name,0,0, i,asym,start,count,start,count,0)
            for j in range(count):
                n = start+j
                self.res[n] = ('ALA',2,j,n,1,n,n,True,True)
                self.atoms[n] = ('CA',6,(float(n),0.,0.),True,0.,0.)
            start += count
        self.save_ledger()
        native=native_data_writer()
        self.assertEqual(CHAIN,np.dtype(native['Chain']))
        self.assertEqual(RES,np.dtype(native['Residue']))
        self.assertEqual(ATOM,np.dtype(native['AtomV2']))
        with np.load(self.ledger,allow_pickle=False) as data:
            structure=native['StructureV2'](**dict(data))
        written=structure.remove_invalid_chains()
        # Matches prediction writer behavior: set predicted atoms/residues present.
        written.atoms['is_present']=True
        written.residues['is_present']=True
        self.structure = self.pred / 'input_model_0.pdb'
        self.structure.write_text(native['to_pdb'](written,plddts=np.linspace(.5,.9,6),boltz2=True))
        (self.orig / self.structure.name).write_bytes(self.structure.read_bytes())
        self.matrix = np.arange(36,dtype=np.float32).reshape(6,6)
        np.savez_compressed(self.orig/'pae_input_model_0.npz',pae=self.matrix)
        np.savez_compressed(self.orig/'plddt_input_model_0.npz',plddt=np.linspace(.5,.9,6))
        conf={'chains_ptm':{'7':.7,'9':.8,'2':.9},'pair_chains_iptm':{a:{b:.6 for b in ['7','9','2']} for a in ['7','9','2']}}
        conf.update({k:.6 for k in ['confidence_score','ptm','iptm','ligand_iptm','protein_iptm','complex_plddt','complex_iplddt','complex_pde','complex_ipde']})
        (self.orig/'confidence_input_model_0.json').write_text(json.dumps(conf))

    def save_ledger(self):
        np.savez_compressed(self.ledger,chains=self.chains,residues=self.res,atoms=self.atoms,mask=self.mask,coords=np.zeros(7,dtype=[('coords','3f4')]),ensemble=np.array([(0,7)],dtype=[('atom_coord_idx','i4'),('atom_num','i4')]),bonds=np.array([],dtype=[('chain_1','i4'),('chain_2','i4'),('res_1','i4'),('res_2','i4'),('atom_1','i4'),('atom_2','i4'),('type','i1')]),interfaces=np.array([],dtype=[('chain_1','i4'),('chain_2','i4')]))

    def build(self):
        return build_manifest(predictions_root=self.pred,producer_method='boltz',producer_sample='sample',formats=['pdb'],protein_science_contract_revision=1,boltz_native_root=self.native)

    def test_scripts_only_native_publication(self):
        """The model task sees scripts, not platform/api or its service imports."""
        import os
        import shutil
        isolated = self.root / 'scripts_only'
        scripts = isolated / 'scripts'
        shutil.copytree(ROOT / 'scripts', scripts, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME'}}
        result = subprocess.run([
            sys.executable, str(scripts / 'write_structure_producer_manifest.py'),
            '--predictions-root', str(self.pred), '--producer-method', 'boltz',
            '--producer-sample', 'sample', '--format', 'pdb',
            '--protein-science-contract-revision', '1', '--boltz-native-root', str(self.native),
            '--output', str(self.root / 'scripts_only_manifest.json'),
        ], cwd=isolated, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        published = json.loads((self.root / 'scripts_only_manifest.json').read_text())
        self.assertEqual(published, self.build())

    def test_native_file_axis_and_strict_parser(self):
        manifest=self.build()
        candidate=manifest['candidates'][0]
        evidence=candidate['boltz_native_identity']
        self.assertEqual([x['native_asym_id'] for x in evidence['chain_index_map']], [7,9,2])
        self.assertEqual([x['source_chain_index'] for x in evidence['chain_index_map']], [0,2,3])
        self.assertEqual([x['chain_id'] for x in evidence['aligned_error']['identity_evidence']['row_axis']['residues']],list('TTTHHL'))
        d=evidence['aligned_error']
        artifact=load_aligned_error_artifact(aligned_error_path=self.pred/d['artifact_key'],aligned_error_format='boltz_pae_npz',structure_path=self.structure,matrix_key=d['matrix_key'],contract_revision=1,candidate_id='input_model_0.pdb',document_id='input_model_0.pdb',identity_evidence=d['identity_evidence'])
        np.testing.assert_array_equal(artifact.matrix,self.matrix)
        self.assertEqual(evidence['native_token_count'],6)
        self.assertEqual(evidence['vectors'][0]['vector_key'],'plddt')

    def test_strict_loader_rejects_native_nonnumeric_dtype_before_conversion(self):
        d = self.build()['candidates'][0]['boltz_native_identity']['aligned_error']
        path = self.pred / d['artifact_key']
        for bad in (np.ones((6, 6), dtype=bool), np.full((6, 6), '0.5')):
            with self.subTest(dtype=str(bad.dtype)):
                np.savez_compressed(path, pae=bad)
                evidence = dict(d['identity_evidence'], artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                with self.assertRaisesRegex(ValueError, 'numeric'):
                    load_aligned_error_artifact(
                        aligned_error_path=path, aligned_error_format=d['format'],
                        structure_path=self.structure, matrix_key='pae', contract_revision=1,
                        candidate_id=self.structure.name, document_id=self.structure.name,
                        identity_evidence=evidence)

    def test_strict_json_rejects_numeric_strings_and_mixed_booleans(self):
        d = self.build()['candidates'][0]['boltz_native_identity']['aligned_error']
        path = self.pred / 'confidence.json'
        for value in (True, '0.5'):
            with self.subTest(value=value):
                matrix = self.matrix.tolist()
                matrix[0][0] = value
                path.write_text(json.dumps({'pae': matrix}))
                evidence = dict(d['identity_evidence'], artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                with self.assertRaisesRegex(ValueError, 'numeric'):
                    load_aligned_error_artifact(
                        aligned_error_path=path, aligned_error_format='confidence_json',
                        structure_path=self.structure, matrix_key='pae', contract_revision=1,
                        candidate_id=self.structure.name, document_id=self.structure.name,
                        identity_evidence=evidence)

    def test_native_ledger_rejects_consistently_swapped_claimed_axis(self):
        import copy
        import importlib.util
        module_path = ROOT / 'scripts/lib/boltz_native_identity.py'
        self.assertTrue(module_path.is_file(), 'shared pure native-ledger verifier is missing')
        from lib.boltz_native_identity import verify_boltz_native_identity
        native = self.build()['candidates'][0]['boltz_native_identity']
        kwargs = dict(source=self.structure.read_bytes(), structure_name=self.structure.name,
                      ledger_bytes=self.ledger.read_bytes(),
                      pae_bytes=(self.orig / 'pae_input_model_0.npz').read_bytes(),
                      plddt_bytes=(self.orig / 'plddt_input_model_0.npz').read_bytes(),
                      confidence_bytes=(self.orig / 'confidence_input_model_0.json').read_bytes(),
                      candidate_id=self.structure.name, document_id=self.structure.name)
        self.assertEqual(verify_boltz_native_identity(native, **kwargs), native)
        forged = copy.deepcopy(native)
        # Every sidecar agrees, hashes and dimensions are unchanged. Only the
        # independently parsed native ledger can disprove this fabricated order.
        for axis in (forged['aligned_error']['identity_evidence']['row_axis'],
                     forged['aligned_error']['identity_evidence']['column_axis'],
                     forged['vectors'][0]['axis']):
            axis['residues'][0], axis['residues'][3] = axis['residues'][3], axis['residues'][0]
        with self.assertRaisesRegex(ValueError, 'native ledger'):
            verify_boltz_native_identity(forged, **kwargs)
        self.assertEqual(self.structure.read_bytes(), kwargs['source'])

    def test_legacy_unchanged_without_ledger(self):
        m=build_manifest(predictions_root=self.pred,producer_method='boltz',producer_sample='sample',formats=['pdb'])
        self.assertEqual(set(m),{'schema_name','schema_version','candidates'})
        self.assertNotIn('boltz_native_identity',m['candidates'][0])

    def test_missing_ledger(self):
        self.ledger.unlink()
        with self.assertRaises((ValueError,FileNotFoundError)): self.build()

    def test_invalid_ledger_and_output(self):
        for mutation in ['duplicate','empty','ligand','foreign_residue','overlap']:
            with self.subTest(mutation=mutation):
                old=self.chains.copy(); oldres=self.res.copy()
                if mutation=='duplicate': self.chains[2]['name']='T'
                if mutation=='empty': self.chains[2]['name']=''
                if mutation=='ligand': self.chains[2]['mol_type']=3
                if mutation=='foreign_residue': self.res[4]['name']='GLY'
                if mutation=='overlap': self.chains[2]['res_idx']=0
                self.save_ledger()
                with self.assertRaises(ValueError): self.build()
                self.chains=old; self.res=oldres
        self.save_ledger()
        self.structure.write_text(self.structure.read_text().replace('ALA T','ALA A'))
        with self.assertRaises(ValueError): self.build()

    def test_dimensions_and_confidence_chain_keys(self):
        np.savez_compressed(self.orig/'pae_input_model_0.npz',pae=np.zeros((7,7)))
        with self.assertRaises(ValueError): self.build()
        np.savez_compressed(self.orig/'pae_input_model_0.npz',pae=self.matrix)
        confidence_path=self.orig/'confidence_input_model_0.json'
        confidence=json.loads(confidence_path.read_text())
        confidence['chains_ptm']={'0':.7,'1':.8,'2':.9}
        confidence_path.write_text(json.dumps(confidence))
        with self.assertRaisesRegex(ValueError,'chain keys'): self.build()

    def test_confidence_key_value_and_transport_fail_closed(self):
        path=self.orig/'pae_input_model_0.npz'
        original=path.read_bytes()
        for payload in [{'pae':self.matrix,'foreign':self.matrix},{'pae':np.full((6,6),np.nan)},{'pae':np.full((6,6),-1.)}]:
            np.savez_compressed(path,**payload)
            with self.assertRaises(ValueError): self.build()
            self.assertFalse((self.pred/path.name).exists())
        path.write_bytes(original)
        (self.pred/path.name).write_bytes(b'foreign destination')
        with self.assertRaises(ValueError): self.build()

    def test_marker_and_dtype_fail_closed(self):
        for revision,method,native in [(2,'boltz',self.native),(True,'boltz',self.native),(1,'protenix',self.native),(None,'boltz',self.native),(1,'boltz',None)]:
            with self.subTest(revision=revision,method=method), self.assertRaises(ValueError):
                build_manifest(predictions_root=self.pred,producer_method=method,producer_sample='sample',formats=['pdb'],protein_science_contract_revision=revision,boltz_native_root=native)
        dtype=np.dtype([(name,'f4' if name=='asym_id' else typ) for name,typ in CHAIN.descr])
        self.chains=self.chains.astype(dtype)
        self.save_ledger()
        with self.assertRaises(ValueError): self.build()

    def test_strict_consumer_rejects_foreign_hash_and_identity(self):
        import copy
        d=self.build()['candidates'][0]['boltz_native_identity']['aligned_error']
        for mutation in ['artifact_hash','source_hash','candidate','duplicate_axis']:
            evidence=copy.deepcopy(d['identity_evidence'])
            if mutation=='artifact_hash': evidence['artifact_sha256']='0'*64
            if mutation=='source_hash': evidence['row_axis']['source_sha256']='0'*64
            if mutation=='candidate': evidence['row_axis']['candidate_id']='foreign'
            if mutation=='duplicate_axis': evidence['row_axis']['residues'][1]=evidence['row_axis']['residues'][0]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                load_aligned_error_artifact(aligned_error_path=self.pred/d['artifact_key'],aligned_error_format=d['format'],structure_path=self.structure,matrix_key='pae',contract_revision=1,candidate_id=self.structure.name,document_id=self.structure.name,identity_evidence=evidence)

    def test_modified_protein_is_one_token_and_empty_ids_fail(self):
        from write_structure_producer_manifest import boltz_native_identity
        self.res[4]['is_standard']=False
        self.save_ledger()
        self.assertEqual(self.build()['candidates'][0]['boltz_native_identity']['native_token_count'],6)
        with self.assertRaises(ValueError):
            boltz_native_identity(native_root=self.native,predictions_root=self.pred,structure=self.structure,source=self.structure.read_bytes(),candidate_id='',document_id='doc')

    def test_single_structure_snapshot(self):
        from unittest.mock import patch
        from lib import boltz_native_identity as native_identity
        source=self.structure.read_bytes()
        parse=native_identity._strict_structure_records
        def mutate_after_snapshot(data,*args):
            self.structure.write_bytes(b'changed after capture')
            return parse(data,*args)
        with patch.object(native_identity,'_strict_structure_records',side_effect=mutate_after_snapshot):
            c=self.build()['candidates'][0]
        self.assertEqual(c['producer_artifact_sha256'],hashlib.sha256(source).hexdigest())
        self.assertEqual(c['boltz_native_identity']['structure_sha256'],c['producer_artifact_sha256'])

    def test_marked_module_publication_shell(self):
        # Execute the exact publication command from each owning module task,
        # substituting bounded Nextflow variables only. No predictor is executed.
        import shlex
        text=(ROOT/'modules/structure_prediction.nf').read_text()
        metadata=dict(producer_artifact_id='candidate',producer_artifact_key='candidate',producer_sample='candidate',producer_sequence='AAA:AA:A',producer_fold=None,producer_rank=None,producer_submission_id='submission',producer_submission_name='submission',original_submission_identity={'id':'submission','name':'submission'})
        encoded=base64.b64encode(json.dumps(metadata).encode()).decode()
        for process in ['BoltzFromSequenceTask','BoltzFromSequenceWithMSATask','BoltzFromComplex']:
            with self.subTest(process=process):
                block=text.split('process '+process+' {',1)[1].split('\\n}',1)[0]
                line=next(x for x in block.splitlines() if 'python3 ' in x and 'write_' in x and '_producer_manifest.py' in x)
                command=block[block.index(line):].split('--output producer_candidates.json',1)[0]+'--output producer_candidates.json'
                command=command.replace("${params.code_root ?: '/app'}",str(ROOT)).replace('${params.code_root}',str(ROOT))
                command=command.replace('${producerMetadataBase64}',encoded).replace('${producerSampleBase64}',base64.b64encode(b'sample').decode())
                command=command.replace("${params.protein_science_contract_revision == 1 ? '--protein-science-contract-revision 1 --boltz-native-root boltz_results_yamls' : ''}",'--protein-science-contract-revision 1 --boltz-native-root boltz_results_yamls')
                command=command.replace('python3 ',shlex.quote(sys.executable)+' ',1)
                self.assertNotIn('${',command)
                result=subprocess.run(['bash','-e','-c',command],cwd=self.root,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                c=json.loads((self.root/'producer_candidates.json').read_text())['candidates'][0]
                self.assertEqual(c['boltz_native_identity']['native_token_count'],6)
                self.assertTrue((self.pred/c['boltz_native_identity']['processed_structure']['artifact_key']).is_file())

    def test_marked_sequence_command(self):
        metadata=dict(producer_artifact_id='candidate',producer_artifact_key='candidate',producer_sample='candidate',producer_sequence='AAA:AA:A',producer_fold=None,producer_rank=None,producer_submission_id='submission',producer_submission_name='submission',original_submission_identity={'id':'submission','name':'submission'})
        output=self.root/'producer_candidates.json'
        command=[sys.executable,str(ROOT/'scripts/write_sequence_producer_manifest.py'),'--metadata-base64',base64.b64encode(json.dumps(metadata).encode()).decode(),'--predictions-dir',str(self.pred),'--producer-method','boltz','--output',str(output),'--protein-science-contract-revision','1','--boltz-native-root',str(self.native)]
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        c=json.loads(output.read_text())['candidates'][0]
        axis=c['boltz_native_identity']['aligned_error']['identity_evidence']['row_axis']
        self.assertEqual((axis['candidate_id'],axis['document_id']),('candidate','candidate/input_model_0.pdb'))
        self.assertEqual(axis['source_sha256'],hashlib.sha256(self.structure.read_bytes()).hexdigest())

if __name__=='__main__': unittest.main()
