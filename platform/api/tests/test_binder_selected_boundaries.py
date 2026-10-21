"""Integrated source/target transport, real router/publication, no inference."""
import hashlib
import io
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from database import Design, Job, JobArtifact
from test_binder_continuation import selected, PDB
from test_project_workflow_setups import setup_store
from test_binder_selected_round import source, request


def cif(path, chain='A', model=7):
    from Bio.PDB import PDBParser, MMCIFIO
    structure = PDBParser(QUIET=True).get_structure('source', io.StringIO(
        PDB.replace('END\n', '') + PDB.replace('ALA A', 'GLY B')))
    structure[0].serial_num = model
    structure[0]['A'].id = chain
    writer = MMCIFIO()
    writer.set_structure(structure)
    writer.save(str(path))


@pytest.mark.asyncio
@pytest.mark.parametrize('chain', ['A', 'LONG'])
async def test_independent_structural_and_native_branches(selected, setup_store, chain):
    client, session, root, tmp = await source(selected)
    path = tmp / 'native.cif'
    cif(path, chain)
    original = path.read_bytes()
    design = await session.get(Design, 'd0')
    design.pdb_path = str(path)
    design.provenance = {'binder_chains': [chain], 'target_chains': ['B']}
    await session.commit()
    payload = request('mixed-' + chain)
    payload['round_plan']['stages'] = [
        {'id': 'mature', 'input': {'selected': True}, 'kind': 'maturation',
         'model_id': 'binder_refinement', 'mode': 'refine', 'params': {
             'ppiflow_task': 'protein_binder_partial', 'maturation_flow_enabled': True,
             'maturation_repack_enabled': False, 'maturation_anchors_enabled': False}},
        {'id': 'blind', 'input': {'selected': True}, 'kind': 'prediction',
         'models': [{'model_id': 'boltz2', 'params': {'boltz_use_msa': False}}]}]
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    children = [await session.get(Job, row['id']) for row in body['launched_jobs']]
    prediction = next(c for c in children if c.model_id == 'boltz2')
    assert [c['sequence'] for c in prediction.params['complex_components']] == ['A', 'G']
    assert not prediction.params.get('input_pdb')
    if chain == 'LONG':
        assert len(children) == 1
        assert body['round']['errors']
    else:
        assert len(children) == 2, body['round']
        maturation = next(c for c in children if c.model_id == 'binder_refinement')
        from scripts.run_ppiflow_protein_partial import prepare_pair, prepared_model
        prepared = json.loads(Path(maturation.params['selected_input_manifest']).read_text())['designs'][0]
        pdb = prepared['selection_pdb_path']
        serial = prepared_model(pdb)
        assert maturation.params['ppiflow_input_model'] == serial
        assert maturation.params['binder_chains'] == 'A'
        actual = prepare_pair(pdb, tmp / 'pair.pdb', 'A', 'B', serial)
        assert {r['role'] for r in actual['residues']} == {'binder', 'target'}
        assert actual['source_sha256'] == prepared['selection_structure_sha256']
    assert path.read_bytes() == original
    path.unlink()
    (tmp / 'independent.pdb').unlink()
    replay = await client.post('/api/binder-continuation/selected', json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json()['round_id'] == body['round_id']
    assert len(replay.json()['launched_jobs']) == len(children)
    payload['idempotency_key'] += '-new-intent'
    repeated = await client.post('/api/binder-continuation/selected', json=payload)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()['round_id'] != body['round_id']
    assert len(repeated.json()['launched_jobs']) == len(children), repeated.text


@pytest.mark.asyncio
async def test_typed_native_prepared_target_without_generated_binder(selected):
    import yaml
    from scripts.lib.boltzgen_inputs import identity_digest, input_identity
    client, session, root, tmp = await source(selected)
    native = tmp / 'prepared'
    native.mkdir()
    config = native / 'boltzgen_input.yaml'
    config.write_text(yaml.safe_dump({'entities': [
        {'protein': {'id': 'T', 'sequence': 'GG'}},
        {'dna': {'id': 'D', 'sequence': 'ACGT'}},
        {'rna': {'id': 'R', 'sequence': 'ACGU'}},
        {'ligand': {'id': 'L', 'smiles': 'CCO'}},
        {'protein': {'id': 'B', 'sequence': 'AAAA'}}]}))
    root.mode = 'protein_binder'
    root.params = {'boltzgen_yaml_config': str(native), 'boltzgen_binder_sequence': 'AAAA',
        'boltzgen_prepared_sha256': identity_digest(input_identity(native))}
    await session.commit()
    payload = request('typed')
    payload['round_plan']['stages'] = [payload['round_plan']['stages'][1]]
    payload['round_plan']['stages'][0]['input'] = {'selected': True}
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == 201, response.text
    assert len(response.json()['launched_jobs']) == 2, response.text
    for row in response.json()['launched_jobs']:
        child = await session.get(Job, row['id'])
        components = child.params['complex_components']
        assert [c['type'] for c in components] == ['protein', 'protein', 'dna', 'rna', 'ligand']
        assert components[1]['sequence'] == 'GG'
        assert components[-1]['smiles'] == 'CCO'
        assert all(not c.get('path') for c in components)


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['rfantibody', 'antibody_denovo', 'template_antibody_denovo'])
async def test_rf_independent_original_author_chain_not_generated_role(selected, model):
    client, session, root, tmp = await source(selected)
    original = tmp / 'rf-target.pdb'
    original.write_text(PDB.replace('ALA A', 'CYS Q'))
    root.model_id = model
    root.params = {'target_pdb': str(original), 'antigen_chains': 'Q'}
    await session.commit()
    payload = request('original-' + model)
    payload['round_plan']['stages'] = [payload['round_plan']['stages'][1]]
    payload['round_plan']['stages'][0]['input'] = {'selected': True}
    result = await client.post('/api/binder-continuation/selected', json=payload)
    assert result.status_code == 201, result.text
    assert len(result.json()['launched_jobs']) == 2, result.text
    for row in result.json()['launched_jobs']:
        child = await session.get(Job, row['id'])
        assert [c['sequence'] for c in child.params['complex_components']] == ['A', 'C']
        assert child.provenance['binder_round_step']['target_binding']['sha256'] == hashlib.sha256(original.read_bytes()).hexdigest()


@pytest.mark.parametrize('mode', ['protein_binder', 'peptide_binder', 'nanobody_binder'])
def test_actual_boltzgen_preparation_independent_target(tmp_path, mode):
    from types import SimpleNamespace
    from scripts.lib.boltzgen_inputs import materialize_generation_input
    from services.binder_diagnostic_selection import boltzgen_targets
    target = tmp_path / 'original.pdb'
    target.write_text(PDB.replace('ALA A', 'GLY Q'))
    params = {'boltzgen_generation_mode': mode, 'boltzgen_target_pdb_path': str(target),
              'boltzgen_target_chains': 'Q', 'boltzgen_scaffold_length': '20..30',
              'boltzgen_ligand_smiles': 'CCO'}
    params.update(materialize_generation_input(params, tmp_path / 'prepared', allowed_input_roots=[tmp_path]))
    target.unlink()  # The actual native prepared input owns independent bytes.
    targets = boltzgen_targets(SimpleNamespace(id='generator', mode=mode, params=params))
    assert targets[0]['components'][0] == {'id': 'Q', 'type': 'protein', 'sequence': 'G'}
    assert all(c.get('sequence') != '20..30' for c in targets[0]['components'])


@pytest.mark.asyncio
async def test_real_filter_bodyless_observation_readback_and_selection(selected):
    from test_boltzgen_candidate_accounting import published, make_job
    from services.result_ingester import ingest_job_results
    from services.binder_selected_round import resolve_ref
    from routers import jobs
    client, session, _, _, tmp = selected
    output = tmp / 'generation'
    output.mkdir()
    published(output)
    owner = make_job(output)
    session.add(owner)
    await session.commit()
    await ingest_job_results(owner.id, str(output), session)
    client._transport.app.include_router(jobs.router, prefix='/api/jobs')
    result = await client.get('/api/jobs/job/generation-results')
    assert result.status_code == 200, result.text
    record = next(r for r in result.json()['records'] if r['candidate_id'] == 'b')
    ref = record['candidate_ref']
    artifact = await session.get(JobArtifact, ref['publication_ref'])
    assert artifact.logical_path == 'boltzgen/native/filter_summary.json'
    assert ref['native_record_id'] == 'b'
    resolved = await resolve_ref(session, owner, ref, snapshot=False)
    assert not resolved.get('path') and not resolved.get('sequence')
    assert [d.name for d in await session.scalars(select(Design).where(Design.job_id == owner.id))] == ['a']
    saved = await client.post('/api/jobs/job/review-filter-sets', json={
        'name': 'Bodyless rejected', 'candidate_refs': [ref]})
    assert saved.status_code == 200, saved.text
    from services.binder_selected_round import wire
    assert [wire(r) for r in saved.json()['filter_set']['candidate_refs']] == [ref]
