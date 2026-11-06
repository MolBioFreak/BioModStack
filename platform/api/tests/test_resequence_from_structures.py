"""Resequence route mapping through real source acquisition and private stores."""
from pathlib import Path

import pytest
from fastapi import HTTPException

from test_denovo_api_final import api, stores  # noqa: F401

PDB = "".join(
    f"ATOM  {i:5d}  CA  ALA A{i:4d}    {3.8 * i:8.3f}   0.000   0.000  1.00 20.00           C\n" for i in (1, 2, 3)
) + "END\n"


def test_request_requires_sources_and_chains():
    from routers.jobs import ResequenceLaunchRequest
    with pytest.raises(Exception):
        ResequenceLaunchRequest(sources=[], design_chains="A")
    with pytest.raises(Exception):
        ResequenceLaunchRequest(sources=[{"path": "x.pdb"}], design_chains="")
    ok = ResequenceLaunchRequest(sources=[{"path": "x.pdb"}], design_chains="A", seq_method="mpnn",
                                 fixed_positions="A1-2")
    assert ok.seq_method == "mpnn" and ok.fixed_positions == "A1-2"
    assert ResequenceLaunchRequest(sources=[], backbone_input_pdbs='inputs/basket', design_chains='A')


@pytest.mark.asyncio
async def test_launch_stages_pdb_and_builds_loop_job(api):
    client, core, root = api
    (root / 'inputs/design.pdb').write_text(PDB)
    response = await client.post('/api/jobs/resequence/from-structures', json=dict(
        sources=[{'path': 'inputs/design.pdb'}], design_chains='A', fixed_positions='A1-2', seq_method='mpnn'))
    assert response.status_code == 201, response.text
    launch = response.json()
    assert (launch['model_id'], launch['mode']) == ('protein_modification_experimental', 'region_redesign')
    params = launch['params']
    assert params['fixed_positions'] == 'A1-2' and params['seq_method'] == 'mpnn'
    assert 'designable_positions' not in params
    assert [p.name for p in Path(params['backbone_input_pdbs']).glob('*.pdb')] == ['001_design.pdb']


@pytest.mark.asyncio
async def test_staging_failure_removes_the_selection_dir(api):
    client, _, root = api
    response = await client.post('/api/jobs/resequence/from-structures', json=dict(
        sources=[{'path': 'inputs/missing.pdb'}], design_chains='A'))
    assert response.status_code in (404, 422), response.text
    assert not list((root / 'inputs/design_selections/protein_local_redesign').glob('*'))


@pytest.mark.asyncio
async def test_msa_backend_is_api_based_by_default(api):
    from routers.jobs import ResequenceLaunchRequest
    client, _, root = api
    (root / 'inputs/d.pdb').write_text(PDB)
    for extra, expected in (({}, 'colabfold_api'), ({'msa_provider': 'neurosnap_api'}, 'neurosnap_api')):
        response = await client.post('/api/jobs/resequence/from-structures/prepare', json=dict(
            sources=[{'path': 'inputs/d.pdb'}], design_chains='A', **extra))
        assert response.status_code == 200, response.text
        assert response.json()['params']['protenix_msa_backend'] == expected
        assert 'msa_provider' not in response.json()['params']
    with pytest.raises(Exception):
        ResequenceLaunchRequest(sources=[{'path': 'inputs/d.pdb'}], design_chains='A', msa_provider='local')


def test_fampnn_loop_launch_declares_each_selected_source(tmp_path):
    from services.fampnn_policy_admission import compile_declaration
    (tmp_path / 'selected.pdb').write_text(PDB)
    params = {'backbone_input_pdbs': str(tmp_path), 'design_chains': 'A', 'seq_method': 'fampnn'}
    declaration = compile_declaration('protein_modification_experimental', 'region_redesign', params, None)
    assert declaration['owner'] == 'protein_local_redesign'
    assert declaration['inputs']['selected']['sequence_design'] == ['A:1:', 'A:2:', 'A:3:']


def test_staged_name_drops_every_structure_extension():
    from routers import jobs
    assert jobs._structure_stem('/x/design_model_0.cif.gz') == 'design_model_0'
    assert jobs._structure_stem('/x/design.cif') == 'design'
    assert jobs._structure_stem('/x/design.pdb') == 'design'
    assert jobs._structure_stem('/x/my.design.v2.cif.gz') == 'my.design.v2'


@pytest.mark.asyncio
async def test_prepare_returns_the_staged_request_without_creating_a_job_or_choosing_a_worker(api):
    from database import Job
    from sqlalchemy import select
    client, core, root = api
    (root / 'inputs/d.pdb').write_text(PDB)
    response = await client.post('/api/jobs/resequence/from-structures/prepare', json=dict(
        sources=[{'path': 'inputs/d.pdb'}], design_chains='A', fixed_positions='A1-2', seq_method='mpnn'))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['model_id'], body['mode']) == ('protein_modification_experimental', 'region_redesign')
    assert body['params']['fixed_positions'] == 'A1-2'
    assert body.get('execution_target_id') is None and not body.get('execution_plan_approval')
    assert [p.name for p in Path(body['params']['backbone_input_pdbs']).glob('*.pdb')] == ['001_d.pdb']
    async with core() as session:
        assert not (await session.scalars(select(Job))).all()


@pytest.mark.asyncio
async def test_the_local_launch_route_builds_the_same_request_the_prepare_route_returns(api, monkeypatch):
    from routers import jobs
    from fastapi import BackgroundTasks
    _, core, root = api
    (root / 'inputs/d.pdb').write_text(PDB)
    seen = []
    async def capture(launch, background, session):
        seen.append(launch.model_dump(mode='json', exclude_unset=True))
    monkeypatch.setattr(jobs, 'create_job', capture)
    req = jobs.ResequenceLaunchRequest(sources=[{'path': 'inputs/d.pdb'}], design_chains='A', seq_method='mpnn')
    async with core() as session:
        await jobs.launch_resequence_from_structures(req, BackgroundTasks(), session)
        prepared = await jobs.prepare_resequence_from_structures(req, session)
    # Each request owns distinct capability/path; scientific content is identical.
    first = seen[0]
    assert first['params']['resequence_sources'][0]['sha256'] == prepared['params']['resequence_sources'][0]['sha256']
    for body in (first, prepared):
        for key in ('resequence_preparation', 'resequence_sources', 'backbone_input_pdbs'):
            body['params'].pop(key)
    assert first == prepared


@pytest.mark.asyncio
async def test_prepare_removes_its_staging_when_a_source_cannot_be_read(api):
    client, _, root = api
    response = await client.post('/api/jobs/resequence/from-structures/prepare', json=dict(
        sources=[{'path': 'inputs/missing.pdb'}], design_chains='A'))
    assert response.status_code in (404, 422), response.text
    assert not list((root / 'inputs/design_selections/protein_local_redesign').glob('*'))
