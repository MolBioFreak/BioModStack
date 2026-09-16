from pathlib import Path
import numpy as np
import pytest

from services import aligned_error_utils as ae
from services.ipsae import _directed_pair_summary, _residue_label


def atom(serial, name, chain, number, icode='', x=0, altloc=''):
    return f'ATOM  {serial:5d} {name:^4}{altloc:1}ALA {chain:1}{number:4d}{icode:1}   {x:8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           C\n'


def insertion_fixture(tmp_path):
    import json
    p = tmp_path / 'candidate.pdb'
    p.write_text(''.join(atom(i*2+1, 'CA', 'H', 100, code, x=i) + atom(i*2+2, 'CB', 'H', 100, code, x=x) for i, (code, x) in enumerate([('', 1), ('A', 2), ('B', 30)])) + ''.join(atom(7+i, 'CA', 'T', i+1) for i in range(26)))
    matrix = np.full((29, 29), 2.0)
    matrix[1, 3:] = 0.5
    path = tmp_path/'pae.json'
    path.write_text(json.dumps({'pae': matrix.tolist()}))
    return p, path, matrix


@pytest.mark.parametrize('via_loader', [False, True])
def test_unmarked_insertion_outputs_preserve_c276_baseline(tmp_path, via_loader):
    from services.ipsae import compute_ipsae_interface
    p, path, matrix = insertion_fixture(tmp_path)
    residues, _ = ae.load_structure_residue_records(p)
    artifact = (ae.load_aligned_error_artifact(aligned_error_path=path, aligned_error_format='confidence_json', structure_path=p) if via_loader else ae.AlignedErrorArtifact(path, 'confidence_json', 'pae', matrix, residues))
    assert [r.cb_coord[0] for r in artifact.residues[:3]] == [30, 30, 30]
    result = compute_ipsae_interface(artifact, binder_chains=['H'], target_chains=['T'])
    # Captured from the governing c276 baseline, not the repaired implementation.
    assert result['ipsae_n0dom'] == 27
    assert result['ipsae_d0dom'] == 0.8
    assert result['ipsae_selected_residue'] == 'ALA  H  100'
    pair = result['pair_scores'][0]
    assert pair['interface_residue_count_chain_1'] == 1
    assert pair['interface_residue_count_chain_2'] == 26
    assert pair['interface_dist_residue_count_chain_1'] == 0
    assert pair['dist_valid_pair_count'] == 0


@pytest.mark.parametrize('check', ['domain', 'label'])
def test_unmarked_artifact_does_not_infer_repair_from_record_identity(tmp_path, check):
    from services.ipsae import compute_ipsae_interface
    p, path, matrix = insertion_fixture(tmp_path)
    residues, _ = ae.load_structure_residue_records(p, contract_revision=1)
    artifact = ae.AlignedErrorArtifact(path, 'confidence_json', 'pae', matrix, residues)
    result = compute_ipsae_interface(artifact, binder_chains=['H'], target_chains=['T'])
    if check == 'domain':
        assert result['ipsae_n0dom'] == 27
        assert result['ipsae_d0dom'] == 0.8
    else:
        assert result['ipsae_selected_residue'] == 'ALA  H  100'


def test_strict_insertion_outputs_are_independently_correct(tmp_path):
    import hashlib
    from services.ipsae import compute_ipsae_interface
    p, path, matrix = insertion_fixture(tmp_path)
    residues, _ = ae.load_structure_residue_records(p, contract_revision=1)
    axis = ae.residue_identity_axis(residues, candidate_id='c', document_id='d')
    artifact = ae.load_aligned_error_artifact(aligned_error_path=path, aligned_error_format='confidence_json', structure_path=p, contract_revision=1, candidate_id='c', document_id='d', identity_evidence={'row_axis': axis, 'column_axis': axis, 'matrix_key': 'pae', 'artifact_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    assert getattr(artifact, 'contract_revision', None) == 1
    assert [r.cb_coord[0] for r in residues[:3]] == [1, 2, 30]
    assert [r.insertion_code for r in residues[:3]] == ['', 'A', 'B']
    pair = _directed_pair_summary(artifact, chain_1='H', chain_2='T', pae_cutoff=10, dist_cutoff=10)
    assert pair.interface_residue_count_chain_1 == 3
    assert pair.n0dom == 29
    # Independently derive d0 and the best (H100A) row's uniform 0.5-PAE score.
    expected_d0 = 1.24 * (29 - 15)**(1/3) - 1.8
    assert pair.d0dom == pytest.approx(expected_d0)
    assert pair.ipsae_d0dom_asym == pytest.approx(1 / (1 + (0.5 / expected_d0)**2))
    assert pair.interface_dist_residue_count_chain_1 == 2
    assert pair.dist_valid_pair_count == 52
    result = compute_ipsae_interface(artifact, binder_chains=['H'], target_chains=['T'])
    assert result['ipsae_selected_residue'] == 'ALA  H  100A'


@pytest.mark.parametrize('revision', [True, 0, 2, '1'])
def test_artifact_factory_rejects_invalid_revision(tmp_path, revision):
    p, path, _ = insertion_fixture(tmp_path)
    with pytest.raises(ValueError, match='contract revision'):
        ae.load_aligned_error_artifact(aligned_error_path=path, aligned_error_format='confidence_json', structure_path=p, contract_revision=revision)



def test_strict_pdb_selection_and_absent_namespaces(tmp_path):
    p = tmp_path / 'models.pdb'
    p.write_text('MODEL        2\n' + atom(1, 'CA', 'H', 100, 'A', x=2) + atom(2, 'CB', 'H', 100, 'A', x=3) + 'ENDMDL\nMODEL        3\n' + atom(3, 'CA', 'H', 100, 'A', x=9) + 'ENDMDL\n')
    records, _ = ae.load_structure_residue_records(p, contract_revision=1, selected_model=2)
    assert len(records) == 1
    r = records[0]
    assert (r.selected_model, r.auth_asym_id, r.auth_seq_id, r.insertion_code) == (2, 'H', 100, 'A')
    assert (r.label_asym_id, r.label_seq_id, r.source_entity_id) == (None, None, None)
    assert r.cb_coord[0] == 3
    import hashlib
    assert r.source_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='selected model'):
        ae.load_structure_residue_records(p, contract_revision=1, selected_model=1)


def test_strict_cif_preserves_dual_namespaces_and_instances(tmp_path):
    p = tmp_path / 'candidate.cif'
    fields = ['group_PDB', 'id', 'label_atom_id', 'label_alt_id', 'label_comp_id', 'label_asym_id', 'label_entity_id', 'label_seq_id', 'auth_asym_id', 'auth_seq_id', 'pdbx_PDB_ins_code', 'Cartn_x', 'Cartn_y', 'Cartn_z', 'pdbx_PDB_model_num']
    p.write_text('data_test\nloop_\n' + ''.join('_atom_site.'+f+'\n' for f in fields) + 'ATOM 1 CA . ALA X 1 1 H 100 A 1 0 0 1\nATOM 2 CB . ALA X 1 1 H 100 A 2 0 0 1\nATOM 3 CA . ALA Y 1 1 H 100 A 7 0 0 1\n#\n')
    records, _ = ae.load_structure_residue_records(p, contract_revision=1)
    assert len(records) == 2
    assert records[0].auth_seq_id == 100
    assert records[0].residue_number == 100
    assert records[0].label_seq_id == 1
    assert records[0].label_asym_id == 'X'
    assert records[0].source_entity_id == '1'
    assert records[0].entity_instance_id != records[1].entity_instance_id
    assert [r.cb_coord[0] for r in records] == [2, 7]


@pytest.mark.parametrize('field,replacement', [('label_entity_id', '2'), ('auth_asym_id', 'Q'), ('auth_seq_id', '101'), ('label_comp_id', 'GLY')])
@pytest.mark.parametrize('reverse', [False, True])
def test_strict_cif_rejects_conflicting_selected_atom_identity(tmp_path, field, replacement, reverse):
    p = tmp_path/'conflicting.cif'
    fields = ['group_PDB', 'id', 'label_atom_id', 'label_alt_id', 'label_comp_id', 'label_asym_id', 'label_entity_id', 'label_seq_id', 'auth_asym_id', 'auth_seq_id', 'pdbx_PDB_ins_code', 'Cartn_x', 'Cartn_y', 'Cartn_z', 'pdbx_PDB_model_num']
    rows = ['ATOM 1 CA . ALA X 1 1 H 100 A 1 0 0 1'.split(), 'ATOM 2 CB . ALA X 1 1 H 100 A 2 0 0 1'.split()]
    rows[1][fields.index(field)] = replacement
    if reverse:
        rows.reverse()
    p.write_text('data_test\nloop_\n' + ''.join('_atom_site.'+f+'\n' for f in fields) + '\n'.join(' '.join(row) for row in rows) + '\n#\n')
    with pytest.raises(ValueError, match='Conflicting residue identity'):
        ae.load_structure_residue_records(p, contract_revision=1)


@pytest.mark.parametrize('revision', [True, 0, 2, '1'])
def test_invalid_revision_rejected(tmp_path, revision):
    p = tmp_path / 'candidate.pdb'
    p.write_text(atom(1, 'CA', 'H', 1))
    with pytest.raises(ValueError, match='contract revision'):
        ae.load_structure_residue_records(p, contract_revision=revision)


def test_strict_duplicate_atoms_fail_instead_of_overwriting(tmp_path):
    p = tmp_path / 'candidate.pdb'
    p.write_text(atom(1, 'CA', 'H', 1) + atom(2, 'CB', 'H', 1) + atom(3, 'CB', 'H', 1))
    with pytest.raises(ValueError, match='Duplicate'):
        ae.load_structure_residue_records(p, contract_revision=1)


def test_strict_altloc_selection_is_explicit(tmp_path):
    p = tmp_path / 'candidate.pdb'
    p.write_text(atom(1, 'CA', 'H', 1, x=2, altloc='A') + atom(2, 'CA', 'H', 1, x=8, altloc='B'))
    records, _ = ae.load_structure_residue_records(p, contract_revision=1, selected_altloc='B')
    assert len(records) == 1
    assert records[0].ca_coord[0] == 8
    assert records[0].selected_altloc == 'B'



def strict_fixture(tmp_path):
    import hashlib, json
    p = tmp_path / 'candidate.pdb'
    p.write_text(''.join(atom(i+1, 'CA', c, 1) for i, c in enumerate('HLT')))
    records, _ = ae.load_structure_residue_records(p, contract_revision=1)
    artifact = tmp_path / 'pae.json'
    artifact.write_text(json.dumps({'pae': [[0, 1, 2], [3, 0, 4], [5, 6, 0]]}))
    # Matrix native rows T,H,L; native columns L,T,H, intentionally directional.
    rows = ae.residue_identity_axis([records[i] for i in [2, 0, 1]], candidate_id='c', document_id='d')
    cols = ae.residue_identity_axis([records[i] for i in [1, 2, 0]], candidate_id='c', document_id='d')
    evidence = {'row_axis': rows, 'column_axis': cols, 'matrix_key': 'pae', 'artifact_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()}
    kwargs = dict(aligned_error_path=artifact, aligned_error_format='confidence_json', structure_path=p, contract_revision=1, candidate_id='c', document_id='d', identity_evidence=evidence)
    return kwargs


def test_native_directional_axes_preserved_and_scoring_maps_cells(tmp_path):
    kwargs = strict_fixture(tmp_path)
    artifact = ae.load_aligned_error_artifact(**kwargs)
    assert artifact.matrix.tolist() == [[0, 1, 2], [3, 0, 4], [5, 6, 0]]
    assert artifact.row_positions == (2, 0, 1)
    assert artifact.column_positions == (1, 2, 0)
    pair = _directed_pair_summary(artifact, chain_1='H', chain_2='T', pae_cutoff=1, dist_cutoff=10)
    assert pair.valid_pair_count == 1  # native H row/T column is 0, NOT structure-order cell 4


@pytest.mark.parametrize('damage', ['candidate', 'document', 'source', 'foreign_row', 'foreign_column', 'dimension', 'missing', 'artifact'])
def test_strict_axes_fail_closed_on_foreign_or_missing_identity(tmp_path, damage):
    kwargs = strict_fixture(tmp_path)
    evidence = kwargs['identity_evidence']
    if damage in ('candidate', 'document'):
        kwargs[damage+'_id'] = 'foreign'
    elif damage == 'source':
        evidence['row_axis']['source_sha256'] = '0'*64
    elif damage.startswith('foreign_'):
        axis = 'row_axis' if damage.endswith('row') else 'column_axis'
        evidence[axis]['residues'][0]['auth_seq_id'] = 999
    elif damage == 'dimension':
        evidence['column_axis']['residues'].pop()
    elif damage == 'missing':
        kwargs['identity_evidence'] = None
    else:
        evidence['artifact_sha256'] = '0'*64
    with pytest.raises(ValueError, match='identity|axis|artifact'):
        ae.load_aligned_error_artifact(**kwargs)


@pytest.mark.parametrize('requested,declared', [('pae', 'pde'), ('pde', 'pae'), ('pae', 'pae'), ('pde', 'pde')])
def test_multikey_npz_identity_binds_native_array(tmp_path, requested, declared):
    import hashlib
    kwargs = strict_fixture(tmp_path)
    path = tmp_path/'multi.npz'
    pae = np.array([[0, 1, 2], [3, 0, 4], [5, 6, 0]])
    pde = np.array([[0, 7, 8], [9, 2, 11], [12, 13, 0]])
    np.savez(path, pae=pae, pde=pde)
    kwargs.update(aligned_error_path=path, aligned_error_format='boltz_pae_npz', matrix_key=requested)
    kwargs['identity_evidence'].update(artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), matrix_key=declared)
    if requested != declared:
        with pytest.raises(ValueError, match='matrix.key'):
            ae.load_aligned_error_artifact(**kwargs)
    else:
        artifact = ae.load_aligned_error_artifact(**kwargs)
        assert artifact.matrix_key == declared
        np.testing.assert_array_equal(artifact.matrix, pae if declared == 'pae' else pde)
        assert artifact.row_positions == (2, 0, 1)
        assert artifact.column_positions == (1, 2, 0)
        pair = _directed_pair_summary(artifact, chain_1='H', chain_2='T', pae_cutoff=1, dist_cutoff=10)
        assert pair.valid_pair_count == (1 if declared == 'pae' else 0)


@pytest.mark.parametrize('damage', ['matrix_key', 'artifact_sha256', 'row_axis', 'column_axis', 'extra'])
def test_strict_evidence_has_exact_closed_keys(tmp_path, damage):
    kwargs = strict_fixture(tmp_path)
    if damage == 'extra':
        kwargs['identity_evidence']['unrecognized'] = 'ignored?'
    else:
        kwargs['identity_evidence'].pop(damage)
    with pytest.raises(ValueError, match='identity evidence'):
        ae.load_aligned_error_artifact(**kwargs)


@pytest.mark.parametrize('format,key', [('boltz_pae_npz', 'pde'), ('confidence_json', 'pde'), ('protenix_full_json', 'token_pair_pde')])
def test_strict_native_key_has_no_pae_fallback(tmp_path, format, key):
    import hashlib, json
    kwargs = strict_fixture(tmp_path)
    path = tmp_path/('only_pae.npz' if format == 'boltz_pae_npz' else 'only_pae.json')
    pae_key = 'token_pair_pae' if format == 'protenix_full_json' else 'pae'
    matrix = np.eye(3)
    if format == 'boltz_pae_npz':
        np.savez(path, **{pae_key: matrix})
    else:
        path.write_text(json.dumps({pae_key: matrix.tolist()}))
    kwargs.update(aligned_error_path=path, aligned_error_format=format, matrix_key=key)
    kwargs['identity_evidence'].update(matrix_key=key, artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='missing matrix_key'):
        ae.load_aligned_error_artifact(**kwargs)
    # Format-native default is PAE; a PDE descriptor must not redirect it.
    kwargs['matrix_key'] = None
    with pytest.raises(ValueError, match='matrix_key identity'):
        ae.load_aligned_error_artifact(**kwargs)
    kwargs['identity_evidence']['matrix_key'] = pae_key
    artifact = ae.load_aligned_error_artifact(**kwargs)
    assert artifact.matrix_key == pae_key
    np.testing.assert_array_equal(artifact.matrix, matrix)


@pytest.mark.parametrize('shared_auth', [True, False])
@pytest.mark.parametrize('entrypoint', ['loader', 'compute', 'directed', 'legacy'])
def test_numerical_auth_projection_preserves_distinct_instances(tmp_path, shared_auth, entrypoint):
    import hashlib
    from services.ipsae import compute_ipsae_interface
    p = tmp_path/'instances.cif'
    fields = ['group_PDB', 'id', 'label_atom_id', 'label_alt_id', 'label_comp_id', 'label_asym_id', 'label_entity_id', 'label_seq_id', 'auth_asym_id', 'auth_seq_id', 'pdbx_PDB_ins_code', 'Cartn_x', 'Cartn_y', 'Cartn_z', 'pdbx_PDB_model_num']
    second_auth = 'H' if shared_auth else 'L'
    p.write_text('data_test\nloop_\n' + ''.join('_atom_site.'+f+'\n' for f in fields) + f'ATOM 1 CA . ALA X 1 1 H 1 . 1 0 0 1\nATOM 2 CA . ALA Y 1 1 {second_auth} 1 . 2 0 0 1\nATOM 3 CA . ALA Z 2 1 T 1 . 3 0 0 1\n#\n')
    records, _ = ae.load_structure_residue_records(p, contract_revision=1)
    assert len(records) == 3
    assert len({r.entity_instance_id for r in records}) == 3
    assert [r.label_asym_id for r in records] == ['X', 'Y', 'Z']
    path = tmp_path/'matrix.npz'
    matrix = np.array([[0, 1, 2], [3, 0, 4], [5, 6, 0]])
    np.savez(path, pae=matrix)
    axis = ae.residue_identity_axis(records, candidate_id='c', document_id='d')
    evidence = dict(artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), matrix_key='pae', row_axis=axis, column_axis=axis)
    artifact = ae.AlignedErrorArtifact(path, 'boltz_pae_npz', 'pae', matrix, records, (0, 1, 2), (0, 1, 2), evidence, contract_revision=None if entrypoint == 'legacy' else 1)
    def run():
        if entrypoint == 'loader':
            return ae.load_aligned_error_artifact(aligned_error_path=path, aligned_error_format='boltz_pae_npz', structure_path=p, contract_revision=1, candidate_id='c', document_id='d', identity_evidence=evidence)
        if entrypoint == 'directed':
            return _directed_pair_summary(artifact, chain_1='H', chain_2='T', pae_cutoff=10, dist_cutoff=10)
        return compute_ipsae_interface(artifact, binder_chains=['H'], target_chains=['T'])
    if shared_auth and entrypoint != 'legacy':
        with pytest.raises(ValueError, match='Ambiguous auth.chain projection'):
            run()
    else:
        result = run()
        if entrypoint == 'loader':
            np.testing.assert_array_equal(result.matrix, matrix)
        elif entrypoint == 'directed':
            assert result.valid_pair_count == 1
        else:
            assert result['pair_scores']


def test_identity_axis_rejects_unmarked_records(tmp_path):
    p = tmp_path / 'candidate.pdb'
    p.write_text(atom(1, 'CA', 'H', 1))
    records, _ = ae.load_structure_residue_records(p)
    with pytest.raises(ValueError, match='identity'):
        ae.residue_identity_axis(records, candidate_id='c', document_id='d')
