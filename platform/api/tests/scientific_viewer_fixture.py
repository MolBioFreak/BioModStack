"""Labelled producer-shaped fixture; does not claim live producer publication."""
import hashlib
import json
from types import SimpleNamespace

from services import aligned_error_utils as ae
from services.analysis_subprocess import _compute_pae_matrix
from services.scientific_viewer_contract import ScientificViewerMetric


def native_fixture(tmp_path, max_size=200):
    structure = tmp_path / 'candidate.pdb'
    def atom(serial, chain, number, code=''):
        return f'ATOM  {serial:5d} {"CA":^4} ALA {chain:1}{number:4d}{code:1}   {float(serial):8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           C\n'
    structure.write_text(atom(1,'H',100) + atom(2,'H',100,'A') + atom(3,'H',100,'B') + atom(4,'L',10) + atom(5,'T',1))
    records, _ = ae.load_structure_residue_records(structure, contract_revision=1)
    artifact = tmp_path / 'pae.json'
    artifact.write_text(json.dumps({'pae': [[float(i*5+j) for j in range(5)] for i in range(5)]}))
    binding = {'candidate_id': 'native-sample-7', 'document_id': 'native/sample-7.cif'}
    evidence = {'artifact_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(), 'matrix_key': 'pae',
                'row_axis': ae.residue_identity_axis([records[i] for i in [4,0,1,2,3]], **binding),
                'column_axis': ae.residue_identity_axis([records[i] for i in [3,4,0,1,2]], **binding)}
    design = SimpleNamespace(id='candidate', name='Candidate', pdb_path=str(structure),
                             aligned_error_path=str(artifact), aligned_error_format='confidence_json', aligned_error_key='pae')
    result, _, _ = _compute_pae_matrix(design, {'max_size':max_size}, contract_revision=1,
                                     identity_evidence=evidence, producer_binding=binding)
    return ScientificViewerMetric.model_validate(result), evidence
