"""Synthetic CA geometry only: no inference or real biological sequences."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import numpy as np
import pytest

from services.aligned_error_utils import ResidueRecord
from services.binder_pose_comparison import compare_exact, normalize_params, compute_comparison, build_signature


def geometry():
    points = [(0,0,0), (2,0,0), (0,3,0), (0,0,2)]
    records, mapping = [], []
    for chain, role in [('B', 'binder'), ('T', 'target')]:
        for i, point in enumerate(points):
            xyz = np.asarray(point, dtype=float) + (10 if chain == 'B' else 0)
            records.append(ResidueRecord(index=len(records), chain_id=chain, residue_name='GLY',
                residue_number=i+1, ca_coord=xyz, cb_coord=xyz, chain_type='protein',
                auth_asym_id=chain, auth_seq_id=i+1, selected_model=1, selected_altloc=''))
            selector = dict(chain_id=chain, auth_seq_id=i+1, insertion_code='')
            mapping.append(dict(role=role, reference=selector, prediction=dict(selector)))
    return records, mapping


def test_fold_and_pose_are_separate_with_no_refit():
    reference, mapping = geometry()
    prediction = [replace(r, ca_coord=r.ca_coord + ([5,0,0] if r.chain_id == 'B' else [0,0,0])) for r in reference]
    result = compare_exact(reference, prediction, mapping)
    assert result['status'] == 'ok'
    assert result['binder_fitted_ca_rmsd'] == pytest.approx(0, abs=1e-12)
    assert result['target_fit_ca_rmsd'] == pytest.approx(0, abs=1e-12)
    assert result['target_fitted_binder_ca_rmsd'] == pytest.approx(5)
    assert result['counts']['binder']['matched'] == 4
    assert not {'accepted', 'score', 'classification'} & result.keys()


def test_global_rotation_translation_invariance_and_chain_renaming():
    reference, mapping = geometry()
    rotation = np.array([[0,1,0], [-1,0,0], [0,0,1]])
    prediction = [replace(r, auth_asym_id='X' if r.chain_id == 'B' else 'Y',
        ca_coord=r.ca_coord @ rotation + [30,-20,7]) for r in reference]
    for row in mapping: row['prediction']['chain_id'] = 'X' if row['role'] == 'binder' else 'Y'
    result = compare_exact(reference, prediction, mapping)
    for key in ('binder_fitted_ca_rmsd', 'target_fit_ca_rmsd', 'target_fitted_binder_ca_rmsd'):
        assert result[key] == pytest.approx(0, abs=1e-12)


def test_missing_residues_and_degenerate_fit_remain_evidence():
    reference, mapping = geometry()
    result = compare_exact(reference, reference[1:], mapping)
    assert result['counts']['binder']['missing_prediction'] == 1
    assert result['unmatched_reference_CA'] == 1
    assert result['counts']['binder']['matched'] == 3
    result = compare_exact(reference, reference[:5], mapping)
    assert result['status'] == 'partial'
    assert result['binder_fitted_ca_rmsd'] is not None
    assert result['target_fit_ca_rmsd'] is None and result['target_fitted_binder_ca_rmsd'] is None


@pytest.mark.parametrize('fault', ['duplicate', 'ambiguous', 'missing_map', 'wrong_insertion'])
def test_exact_identity_no_alignment_guessing(fault):
    reference, mapping = geometry()
    prediction = list(reference)
    if fault == 'duplicate': mapping.append(deepcopy(mapping[0]))
    if fault == 'ambiguous': prediction.append(reference[0])
    if fault == 'missing_map': mapping = None
    if fault == 'wrong_insertion':
        for row in mapping: row['prediction']['insertion_code'] = 'A'
        result = compare_exact(reference, prediction, mapping)
        assert result['status'] == 'unavailable' and result['counts']['binder']['missing_prediction'] == 4
    else:
        with pytest.raises(ValueError): compare_exact(reference, prediction, mapping)


def pdb_bytes(records):
    return ''.join(f'ATOM  {i+1:5d}  CA  GLY {r.chain_id}{r.residue_number:4d}    {r.ca_coord[0]:8.3f}{r.ca_coord[1]:8.3f}{r.ca_coord[2]:8.3f}  1.00 80.00           C\n' for i, r in enumerate(records)).encode()


def native_geometry_bytes():
    """Synthetic native CIF/full-data container, not a model execution."""
    from io import StringIO
    import json
    from Bio.PDB import MMCIFIO
    records, _ = geometry()
    cif = {'data_': 'synthetic_geometry', '_entity_poly.entity_id': ['1', '2'],
           '_entity_poly.type': ['polypeptide(L)', 'polypeptide(L)']}
    fields = ('id', 'group_PDB', 'label_atom_id', 'auth_atom_id', 'type_symbol', 'label_comp_id',
              'auth_comp_id', 'label_asym_id', 'auth_asym_id', 'label_entity_id', 'label_seq_id',
              'auth_seq_id', 'pdbx_PDB_ins_code', 'label_alt_id', 'pdbx_PDB_model_num',
              'B_iso_or_equiv', 'Cartn_x', 'Cartn_y', 'Cartn_z', 'occupancy')
    columns = {field: [] for field in fields}
    for index, residue in enumerate(records):
        binder = residue.chain_id == 'B'
        xyz = residue.ca_coord + ([5,0,0] if binder else [0,0,0])
        values = [str(index+1), 'ATOM', 'CA', 'CA', 'C', 'GLY', 'GLY', 'R' if binder else 'S',
            'X' if binder else 'Y', '1' if binder else '2', str(residue.residue_number),
            str(residue.residue_number), '.', '.', '1', '80', *map(str, xyz), '1']
        for key, value in zip(fields, values, strict=True): columns[key].append(value)
    cif.update({'_atom_site.' + key: value for key, value in columns.items()})
    out = StringIO(); writer = MMCIFIO(); writer.set_dict(cif); writer.save(out)
    full = dict(atom_plddt=[.8]*8, atom_to_token_idx=list(range(8)), token_asym_id=[0]*4+[1]*4,
                token_pair_pae=[[0. if i == j else 2. for j in range(8)] for i in range(8)])
    summary = dict(chain_ptm=[.7,.8], chain_pair_iptm=[[0.,.3],[.6,0.]])
    return out.getvalue().encode(), json.dumps(full).encode(), json.dumps(summary).encode()


def bound_components():
    _, mapping = geometry()
    return [dict(index=i, id=identity, source_chain=chain, role=role, sequence='GGGG',
                 reference_residues=[r['reference'] for r in mapping if r['role'] == role])
            for i, (identity, chain, role) in enumerate([('input-B','B','binder'), ('input-T','T','target')])]


@pytest.mark.asyncio
async def test_automatic_native_comparison_from_submitted_residue_bindings(tmp_path, monkeypatch):
    import test_protenix_native_confidence as native
    from database import Job, Design
    from services import analysis_subprocess as worker
    from test_core_protein_analysis_dispatch import cache
    from routers.analyses import trigger_design_analysis, get_design_analysis, AnalysisRunRequest
    records, _ = geometry()
    reference_path = tmp_path/'reference.pdb'; reference_path.write_bytes(pdb_bytes(records))
    components = bound_components()
    monkeypatch.setattr(native, 'mixed_bytes', native_geometry_bytes)
    owner, design, _ = native.make_publication(tmp_path)
    owner.params = {'complex_components': [dict(id=c['id'], sequence=c['sequence'], type='protein') for c in components]}
    owner.provenance = dict(owner.provenance, binder_round_step=dict(stage='prediction',
        backbone_design_id='reference', binder_chains=['B'], target_chains=['T'], input_components=components,
        pose_comparison={'reference_sha256': hashlib.sha256(reference_path.read_bytes()).hexdigest()}))
    engine, factory = await native.store(tmp_path, owner, design)
    cache(monkeypatch, tmp_path, factory)
    try:
        async with factory() as session:
            session.add(Job(id='reference-owner', name='synthetic reference', model_id='rfdiffusion', mode='default', params={}, provenance={}))
            await session.flush()
            session.add(Design(id='reference', job_id='reference-owner', name='reference', pdb_path=str(reference_path), review_profile_id='binder_design_v1'))
            prediction = await session.get(Design, 'candidate')
            prediction.review_profile_id = 'binder_design_v1'
            await session.commit()
            result, _, _ = await compute_comparison(prediction, {}, session)
            assert result['status'] == 'ok', result['reason']
            assert result['target_fitted_binder_ca_rmsd'] == pytest.approx(5)
            assert result['binder_fitted_ca_rmsd'] == pytest.approx(0, abs=1e-12)
            assert result['mapping'][0]['prediction']['chain_id'] == 'X'
            assert result['mapping'][0]['reference']['chain_id'] == 'B'
            assert result['unmapped_input_residue_counts'] == {'binder': 0, 'target': 0}
            queued = await trigger_design_analysis('candidate', 'binder_pose_comparison', AnalysisRunRequest(), session)
        assert await worker._run_analysis(queued.run_id) == 0
        async with factory() as session:
            response = await get_design_analysis('candidate', 'binder_pose_comparison', None, session)
            assert response.result['status'] == 'ok'
            assert response.result['target_fitted_binder_ca_rmsd'] == pytest.approx(5)
            current = await session.get(Job, owner.id)
            changed = deepcopy(current.provenance)
            changed['binder_round_step']['input_components'][0]['reference_residues'][0] = None
            current.provenance = changed
            prediction = await session.get(Design, 'candidate')
            result, _, _ = await compute_comparison(prediction, {}, session)
            assert result['unmapped_input_residue_counts']['binder'] == 1
            assert result['counts']['binder']['matched'] == 3
            changed = deepcopy(current.provenance)
            changed['binder_round_step']['pose_comparison']['reference_sha256'] = 'f'*64
            current.provenance = changed
            result, _, _ = await compute_comparison(prediction, {}, session)
            assert result['status'] == 'unavailable'
            assert result['reason'] == 'missing_or_changed_reference_mapping_binding'
    finally: await engine.dispose()


@pytest.mark.asyncio
async def test_comparison_persisted_queue_and_source_signatures(tmp_path, monkeypatch):
    from database import Base, Job, Design
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from services import analysis_subprocess as worker
    from test_core_protein_analysis_dispatch import cache, snapshot
    from routers.analyses import trigger_design_analysis, get_design_analysis, AnalysisRunRequest
    records, mapping = geometry()
    reference_path, prediction_path = tmp_path/'reference.pdb', tmp_path/'prediction.pdb'
    reference_path.write_bytes(pdb_bytes(records))
    prediction_path.write_bytes(pdb_bytes([replace(r, ca_coord=r.ca_coord + ([5,0,0] if r.chain_id == 'B' else [0,0,0])) for r in records]))
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path/"comparison.sqlite"}')
    async with engine.begin() as connection: await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cache(monkeypatch, tmp_path, factory)
    try:
        async with factory() as session:
            session.add_all([Job(id='source-job', name='synthetic source', model_id='rfdiffusion', mode='default', params={}, provenance={}),
                Job(id='prediction-job', name='synthetic prediction', model_id='esmfold2', mode='predict', params={},
                    provenance={'binder_round_step': dict(stage='prediction', backbone_design_id='reference',
                        pose_comparison={'residue_mapping': mapping})})])
            await session.flush()
            source = Design(id='reference', job_id='source-job', name='reference', pdb_path=str(reference_path), review_profile_id='binder_design_v1')
            prediction = Design(id='prediction', job_id='prediction-job', name='prediction', pdb_path=str(prediction_path), review_profile_id='binder_design_v1')
            session.add_all([source, prediction]); await session.commit()
            await session.refresh(source); await session.refresh(prediction)
            before = snapshot(prediction)
            signature = await build_signature(prediction, {}, session)
            result, _, _ = await compute_comparison(prediction, {}, session)
            assert result['reference_design_id'] == 'reference', result['reason']
            assert result['reference_document']['contentSha256'] == hashlib.sha256(reference_path.read_bytes()).hexdigest()
            assert result['target_fitted_binder_ca_rmsd'] == pytest.approx(5)
            queued = await trigger_design_analysis('prediction', 'binder_pose_comparison', AnalysisRunRequest(), session)
        assert await worker._run_analysis(queued.run_id) == 0
        async with factory() as session:
            response = await get_design_analysis('prediction', 'binder_pose_comparison', None, session)
            assert response.result['target_fitted_binder_ca_rmsd'] == pytest.approx(5)
            prediction = await session.get(Design, 'prediction')
            assert snapshot(prediction) == before
            reference_path.write_bytes(reference_path.read_bytes() + b'REMARK changed source bytes\n')
            assert await build_signature(prediction, {}, session) != signature
            owner = await session.get(Job, 'prediction-job')
            owner.provenance = {}
            standalone = dict(reference_design_id='reference', residue_mapping=mapping)
            assert normalize_params(standalone) == standalone
            result, _, _ = await compute_comparison(prediction, standalone, session)
            assert result['status'] == 'ok'
            result, _, _ = await compute_comparison(prediction, {}, session)
            assert result['status'] == 'unavailable' and result['target_fit_ca_rmsd'] is None
    finally: await engine.dispose()
