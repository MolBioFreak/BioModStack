from types import SimpleNamespace

import numpy as np

from services import analysis_subprocess as worker


def test_native_projection_calls_strict_loader_and_preserves_direction_and_samples(monkeypatch, tmp_path):
    structure = tmp_path / 'candidate.pdb'
    structure.write_text('native structure fixture')
    artifact_path = tmp_path / 'pae.json'
    artifact_path.write_text('native matrix fixture')
    import hashlib
    source_sha = hashlib.sha256(structure.read_bytes()).hexdigest()
    rows = [dict(index=i, chain_id=c, residue_name='ALA', insertion_code=ins,
                 selected_model=1, selected_altloc='', auth_asym_id=c, auth_seq_id=100,
                 label_asym_id=None, label_seq_id=None, source_entity_id=None,
                 entity_instance_id=c) for i, (c, ins) in enumerate([('T',''),('H','A'),('L','B')])]
    axis = dict(candidate_id='candidate', document_id='primary', source_sha256=source_sha, residues=rows)
    evidence = dict(artifact_sha256=hashlib.sha256(artifact_path.read_bytes()).hexdigest(), row_axis=axis, column_axis=axis)
    seen = {}
    def loader(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(matrix=np.array([[0,1,2],[3,4,5],[6,7,8]]), residues=[],
                               row_positions=[0,1,2], column_positions=[0,1,2], identity_evidence=evidence,
                               format='test', path=artifact_path)
    monkeypatch.setattr(worker, 'load_aligned_error_artifact', loader)
    monkeypatch.setattr(worker, '_resolve_design_structure_path', lambda d: structure)
    monkeypatch.setattr(worker, '_resolve_design_aligned_error_path', lambda d: artifact_path)
    monkeypatch.setattr(worker, '_safe_allowed_relative', lambda p: p)
    design = SimpleNamespace(id='candidate', name='Candidate', aligned_error_format='test', aligned_error_key=None)
    result, _, _ = worker._compute_pae_matrix(design, {'max_size': 2}, contract_revision=1, identity_evidence=evidence, producer_binding={"candidate_id":"candidate", "document_id":"primary"})
    assert seen.get('contract_revision') == 1
    assert seen['candidate_id'] == 'candidate'
    assert seen['identity_evidence'] == evidence
    assert result['native_shape'] == [3, 3]
    assert result['sampled_row_indices'] == [0, 2]
    assert result['row_axis']['residues'] == rows
    assert result['pae_matrix'] == [[0, 2], [6, 8]]
    assert result['document']['contentSha256'] == source_sha
