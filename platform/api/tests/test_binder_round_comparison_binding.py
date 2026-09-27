"""Cross-owner software fixtures: actual round submission, synthetic native bytes."""
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Design, Job
from services import binder_round as rounds
from services.binder_diagnostic_selection import declared_targets
from test_binder_continuation import selected
from test_project_workflow_setups import setup_store
from test_binder_round_orchestration import envelope
from test_binder_pose_comparison import geometry, pdb_bytes, native_geometry_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize('same_document', [True, False])
@pytest.mark.parametrize('producer_mapping', [True, False])
async def test_round_metadata_reaches_native_comparison(selected, setup_store, monkeypatch, same_document, producer_mapping):
    import hashlib
    from dataclasses import replace
    import test_protenix_native_confidence as native
    from services.result_ingester import _ingest_protenix_primary_publications
    from services import analysis_subprocess as worker
    from test_core_protein_analysis_dispatch import cache
    from routers.analyses import trigger_design_analysis, get_design_analysis, AnalysisRunRequest

    _, session, root, _, tmp = selected
    records, _ = geometry()
    reference = await session.get(Design, 'd0')
    Path(reference.pdb_path).write_bytes(pdb_bytes(records))
    reference.artifact_class = 'binder_complex'
    reference.review_profile_id = 'binder_design_v1'
    target = tmp / 'independent-input.pdb'
    # Consumer-contract fixture: ordinary separate target has different source
    # numbering. Only the explicit producer map, not sequence matching, joins it.
    target_records = records if same_document else [replace(r, residue_number=r.residue_number + 200) for r in records[4:]]
    target.write_bytes(pdb_bytes(target_records))
    if producer_mapping:
        reference.provenance = {**(reference.provenance or {}), 'target_residue_mapping': {
            'source_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'residues': [{'source': {'chain_id': src.chain_id, 'auth_seq_id': src.residue_number, 'insertion_code': ''},
                          'output': {'chain_id': out.chain_id, 'auth_seq_id': out.residue_number, 'insertion_code': ''}}
                         for src, out in zip(target_records[-4:], records[4:])]}}
    root.model_id = 'boltzgen'
    root.params = {'boltzgen_target_pdb_path': str(target), 'target_chains': 'T'}
    root.provenance = {rounds.REQUEST: envelope(binder_chains=['B'], target_chains=['T'])}
    await session.commit()
    async with setup_store() as experiments:
        progress = await rounds.reconcile_round(session, experiments, root.id)
    step = next(iter(progress['steps'].values()))
    assert step['state'] == 'queued', progress
    child = await session.get(Job, step['job_id'])
    metadata = child.provenance[rounds.STEP]
    assert all(set(c) == {'id', 'type', 'sequence'} for c in child.params['complex_components'])
    assert metadata['input_components'][0]['reference_residues'][0] == {
        'chain_id': 'B', 'auth_seq_id': 1, 'insertion_code': ''}
    if not same_document and not producer_mapping:
        assert metadata['input_components'][1]['reference_residues'] == [None] * 4
    else:
        assert metadata['input_components'][1]['reference_residues'][0] == {
            'chain_id': 'T', 'auth_seq_id': 1, 'insertion_code': ''}
    assert not child.params.get('input_pdb')

    monkeypatch.setattr(native, 'mixed_bytes', native_geometry_bytes)
    publication_owner, prediction, _ = native.make_publication(tmp)
    child.output_dir = publication_owner.output_dir
    child.status = 'completed'
    child.provenance = {**child.provenance, 'core_protein_scientific_contract': 1}
    prediction.job_id = child.id
    prediction.review_profile_id = 'binder_design_v1'
    session.add(prediction)
    await session.commit()
    await _ingest_protenix_primary_publications(child, Path(child.output_dir), session)
    await session.commit()
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    cache(monkeypatch, tmp, factory)
    async with factory() as queue_session:
        queued = await trigger_design_analysis(prediction.id, 'binder_pose_comparison', AnalysisRunRequest(), queue_session)
        await queue_session.commit()
    assert await worker._run_analysis(queued.run_id) == 0
    async with factory() as readback:
        response = await get_design_analysis(prediction.id, 'binder_pose_comparison', None, readback)
        result = response.result
        comparable = same_document or producer_mapping
        assert result['status'] == ('ok' if comparable else 'partial'), result
        assert result['binder_fitted_ca_rmsd'] == pytest.approx(0, abs=1e-12)
        assert result['unmapped_input_residue_counts']['target'] == (0 if comparable else 4)
        if comparable:
            assert result['target_fitted_binder_ca_rmsd'] == pytest.approx(5)
        else:
            assert result['target_fitted_binder_ca_rmsd'] is None


@pytest.mark.asyncio
async def test_ppiflow_prepared_input_survives_original_removal(tmp_path):
    from services.ppiflow_generation import materialize_ppiflow_generation_request
    target = tmp_path / 'target.pdb'
    target.write_bytes(pdb_bytes(geometry()[0]))
    params = {'target_pdb': str(target), 'target_chain': 'T', 'binder_chain': 'B'}
    prepared = materialize_ppiflow_generation_request('protein_binder', params, tmp_path / 'prepared')
    owner = Job(id='source', name='source', model_id='ppiflow', mode='protein_binder', params={**params, **prepared})
    target.unlink()
    targets = await declared_targets(owner, None)
    assert len(targets) == 1
    assert targets[0]['chains'] == 'T'
    assert Path(targets[0]['target_path']).is_file()
    assert targets[0]['name'] == 'bms_target'


@pytest.mark.asyncio
async def test_ppiflow_feature_csv_does_not_invent_independent_target(tmp_path):
    from services.ppiflow_generation import materialize_ppiflow_generation_request
    # Opaque bytes are intentionally not executable pickle data.
    (tmp_path / 'features.pkl').write_bytes(b'opaque native feature fixture')
    csv = tmp_path / 'sources.csv'
    csv.write_text('pdb_name,processed_path\nstate-a,features.pkl\nstate-b,features.pkl\n')
    params = {'input_csv': str(csv)}
    prepared = materialize_ppiflow_generation_request('protein_binder', params, tmp_path / 'prepared')
    owner = Job(id='source', name='source', model_id='ppiflow', mode='protein_binder', params={**params, **prepared})
    assert await declared_targets(owner, None) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['proteinmpnn', 'fampnn', 'caliby_binder'])
@pytest.mark.parametrize('mapping_available', [True, False])
async def test_designed_descendant_binds_retained_backbone_without_gate(selected, setup_store, mapping_available, model):
    import json
    import hashlib
    from dataclasses import replace
    _, session, root, _, tmp = selected
    reference = await session.get(Design, 'd0')
    records, _ = geometry()
    Path(reference.pdb_path).write_bytes(pdb_bytes(records))
    target = tmp / 'target.pdb'
    target.write_bytes(pdb_bytes(records[4:]))
    settings = envelope(binder_chains=['B'], target_chains=['T'],
                        sequence_design={'model_id': model, 'params': {}})
    root.params = {'target_pdb': str(target)}
    root.provenance = {rounds.REQUEST: settings}
    root.model_id = 'ppiflow'
    reference.artifact_class = 'binder_backbone'
    await session.commit()
    async with setup_store() as experiments:
        progress = await rounds.reconcile_round(session, experiments, root.id)
    designer_step = next(iter(progress['steps'].values()))
    assert designer_step['state'] == 'queued', progress
    designer = await session.get(Job, designer_step['job_id'])
    designer.status = 'completed'
    output = tmp / 'threaded.pdb'
    output_records = [replace(r, residue_number=r.residue_number + 100,
        chain_id=('X' if r.chain_id == 'B' else 'Y') if mapping_available else r.chain_id) for r in records]
    output.write_bytes(pdb_bytes(output_records).replace(b'GLY X', b'ALA X').replace(b'GLY B', b'ALA B'))
    sidecar = tmp / 'threaded.json'
    # Consumer-contract fixture; native emitters are exercised separately through
    # their actual Nextflow shells. Worker paths and output author numbers differ.
    manifest = json.loads(Path(designer.params['selected_input_manifest']).read_text())
    selected_path = Path(manifest['designs'][0]['selection_pdb_path'])
    sidecar.write_text(json.dumps({'source_input_path': '/worker/staged/input.pdb',
        'source_structure_sha256': hashlib.sha256(selected_path.read_bytes()).hexdigest(),
        'source_residue_mapping': [
            {'source': {'chain_id': r.chain_id, 'auth_seq_id': r.residue_number, 'insertion_code': ''},
             'output': {'chain_id': out.chain_id, 'auth_seq_id': out.residue_number, 'insertion_code': ''}}
            for r, out in zip(records, output_records)]} if mapping_available else {}))
    designed = Design(id='threaded', name='threaded', job_id=designer.id,
                      parent_design_id=reference.id, pdb_path=str(output), json_path=str(sidecar))
    session.add(designed)
    await session.commit()
    async with setup_store() as experiments:
        progress = await rounds.reconcile_round(session, experiments, root.id)
    predictions = [s for s in progress['steps'].values() if s['metadata']['stage'] == 'prediction']
    assert len(predictions) == 1, progress
    assert predictions[0]['state'] == 'queued', progress
    metadata = predictions[0]['metadata']
    refs = metadata['input_components'][0]['reference_residues']
    assert bool(refs[0]) is mapping_available
    if mapping_available:
        assert refs[0]['auth_seq_id'] == 1
        assert metadata['input_components'][0]['source_chain'] == 'X'
        assert metadata['input_components'][0]['source_residues'][0]['residue_number'] == 101
    assert metadata['input_components'][1]['reference_residues'] == [None] * 4
    assert metadata['backbone_design_id'] == reference.id
    assert metadata['source_design_id'] == designed.id
    if mapping_available:
        import hashlib
        assert metadata['pose_comparison']['reference_sha256'] == hashlib.sha256(Path(reference.pdb_path).read_bytes()).hexdigest()
