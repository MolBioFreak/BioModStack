"""Real HTTP/SQLite portable workflow receiving; no application lifespan."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from molbio_database import get_molbio_session
from molbio_models import MolecularRevision, NucleotideSequence
from routers import molbio_golden_gate_design as routes
from services.assembly.golden_gate_exports import build_design_exports, read_design_export, portable_source
from services.assembly.golden_gate_workflow import freeze_selection
from services.assembly.golden_gate_workflow_types import WorkflowResult, SaveDesignRequest
from test_golden_gate_workflow_receiving import client_store
from test_golden_gate_design_core import request, binding, A, B

BASE = '/api/molbio/assembly/golden-gate/design'


def archive(response, name):
    assert response.status_code == 200, response.text if response.status_code != 200 else ''
    assert response.headers['content-type'] == 'application/zip'
    assert 'attachment;' in response.headers['content-disposition']
    evidence = os.environ.get('BMS_GG_EXPORT_EVIDENCE')
    if evidence:
        Path(evidence, name + '.zip').write_bytes(response.content)
    with ZipFile(io.BytesIO(response.content)) as zipped:
        return {name: zipped.read(name) for name in zipped.namelist()}


def forbid(*a, **k):
    raise AssertionError('Frozen export/import attempted science or mutable source resolution')


def freeze_owners(monkeypatch):
    for path in (
        'routers.molbio_golden_gate_design.run_workflow',
        'routers.molbio_golden_gate_design.resolve_sources',
        'services.assembly.golden_gate_workflow_persistence.run_workflow',
        'services.assembly.golden_gate_workflow_persistence.resolve_sources',
        'services.assembly.golden_gate_workflow.design_golden_gate',
        'services.assembly.golden_gate_workflow.propose_domestication',
        'services.assembly.golden_gate_workflow.calculate_reaction',
        'services.assembly.golden_gate_fidelity.load_dataset',
    ):
        monkeypatch.setattr(path, forbid)


async def offline_import(document):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_molbio_session] = forbid
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://offline') as client:
        response = await client.post(BASE + '/import', json=document)
        assert response.status_code == 200, response.text
        return response.json()


@pytest.mark.asyncio
async def test_saved_revision_route_zip_offline_identity_settings_and_all_native_entries(tmp_path, monkeypatch):
    async with client_store(tmp_path) as (client, sessions):
        first = await client.post(BASE, json=request().model_dump(mode='json'))
        result = WorkflowResult.model_validate(first.json())
        seeded = await client.post(BASE + '/save', json=SaveDesignRequest(
            selection=freeze_selection(result, 'fixed'), name='Seed', idempotency_key='seed').model_dump(mode='json'))
        assert seeded.status_code == 200, seeded.text
        async with sessions() as session:
            revisions = list((await session.scalars(select(MolecularRevision).where(
                MolecularRevision.operation_id == seeded.json()['operation_id']))).all())
            source_ids = {r.provenance.get('material_id'): r.id for r in revisions}
        authored = request().model_dump(mode='json')
        for source in authored['sources']:
            source['source'] = {'kind': 'molecular_revision', 'revision_id': source_ids['source:' + source['id']]}
        authored['fidelity'] = {'dataset_id': None, 'condition_use': 'explicit_proxy', 'include_pair_observations': True}
        authored['reaction'] = {'settings': {'total_volume_uL': 17}, 'parts': [
            {'part_id': 'insert', 'amount': {'value': 0.13, 'unit': 'pmol'}, 'stock': None}]}
        preview = await client.post(BASE, json=authored)
        assert preview.status_code == 200, preview.text
        result = WorkflowResult.model_validate(preview.json())
        saved = await client.post(BASE + '/save', json=SaveDesignRequest(
            selection=freeze_selection(result, 'fixed'), name='Portable revision', idempotency_key='portable').model_dump(mode='json'))
        assert saved.status_code == 200, saved.text
        expected = saved.json()['result']
        async with sessions() as session:
            for projection in (await session.scalars(select(NucleotideSequence))).all():
                projection.sequence = 'AAAA'
            await session.commit()
        freeze_owners(monkeypatch)
        reopened = await client.get(BASE + '/' + saved.json()['operation_id'])
        assert reopened.json() == saved.json()
        files = archive(await client.get(BASE + '/' + saved.json()['operation_id'] + '/export'), 'saved-revision')
        candidate = WorkflowResult.model_validate(expected).solutions[0]
        native = build_design_exports(candidate.fixed_request, candidate.design,
            fidelity=candidate.fidelity, reaction=candidate.reaction)
        assert set(files) == set(native) | {'workflow.json', 'comparison.txt'}
        assert all(files[key] == value for key, value in native.items())
        core = read_design_export(files['design.json'])
        assert core.request.sources[0].source.kind == 'molecular_revision'
        assert portable_source(core, 'insert').sequence == B
        assert core.reaction == candidate.reaction
        assert candidate.reaction.rows[0].transfer_volume_uL is None
        document = json.loads(files['workflow.json'])
        assert document['result'] == expected
        assert await offline_import(document) == expected
        # Fresh interpreter gets just the ZIP, not a DB or revision resolver.
        path = tmp_path / 'portable.zip'
        path.write_bytes((await client.get(BASE + '/' + saved.json()['operation_id'] + '/export')).content)
    (tmp_path / 'workups.db').unlink()
    script = '''import asyncio,json,sys
from pathlib import Path
from zipfile import ZipFile
import httpx
from fastapi import FastAPI
from routers.molbio_golden_gate_design import router
from molbio_database import get_molbio_session
from services.assembly.golden_gate_exports import read_design_export
async def main():
    with ZipFile(sys.argv[1]) as z:
        document=json.loads(z.read('workflow.json'))
        core=read_design_export(z.read('design.json'))
        assert core.result.materials
    app=FastAPI(); app.include_router(router)
    def forbidden(): raise AssertionError('offline DB access')
    app.dependency_overrides[get_molbio_session]=forbidden
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as c:
        r=await c.post('/api/molbio/assembly/golden-gate/design/import',json=document)
        assert r.status_code==200,r.text
        assert r.json()==document['result']
        Path(sys.argv[2]).write_text(r.text)
asyncio.run(main())
'''
    fresh = subprocess.run([sys.executable, '-c', script, str(path), str(tmp_path / 'fresh.json')], capture_output=True, text=True)
    assert fresh.returncode == 0, fresh.stderr
    assert json.loads((tmp_path / 'fresh.json').read_text()) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('task', ['evaluate_overhangs', 'optimize_overhangs'])
async def test_evidence_only_export_import_without_save_or_scores(tmp_path, monkeypatch, task):
    body = {'task': task, 'fidelity': {'dataset_id': None}}
    if task == 'evaluate_overhangs':
        body['junctions'] = ['AATG', 'GGAG']
    else:
        body.update(candidate_domain=['AATG', 'GGAG', 'TGAC'], junction_count=2, end_length=4,
            search={'ranking_mode': 'lexicographic', 'seed': 19, 'alternatives': 3})
    async with client_store(tmp_path) as (client, _):
        preview = await client.post(BASE, json=body)
        assert preview.status_code == 200, preview.text
        freeze_owners(monkeypatch)
        files = archive(await client.post(BASE + '/export', json=preview.json()), task)
        assert set(files) == {'workflow.json', 'comparison.txt'}
        assert b'not server-attested' in files['comparison.txt']
        document = json.loads(files['workflow.json'])
        assert await offline_import(document) == preview.json()
        assert not document['result']['solutions']
        assert document['result']['evaluation' if task == 'evaluate_overhangs' else 'search_result']


@pytest.mark.asyncio
async def test_selected_split_alternative_owns_actual_retained_reaction(tmp_path, monkeypatch):
    body = dict(task='split_target', target=dict(id='target', source=dict(kind='inline',
        sequence='AATG' + A + 'GGAG' + B, topology='circular')), enzyme=binding('BsaI'),
        windows=[dict(start=20,end=24),dict(start=80,end=84)],
        preparation=dict(clamp='TT',spacer='A'), search=dict(ranking_mode='lexicographic',
            unique_classes=False,exclude_palindromes=False,alternatives=12,seed=23),
        reaction=dict(settings=dict(total_volume_uL=19),parts=[
            dict(part_id='part-1',amount=dict(value=0.1,unit='pmol'),stock=dict(value=20,unit='ng/uL')),
            dict(part_id='part-2',length_bp=999,amount=dict(value=2,unit='ng'),stock=None)]))
    async with client_store(tmp_path) as (client, _):
        response = await client.post(BASE, json=body)
        assert response.status_code == 200, response.text
        result = WorkflowResult.model_validate(response.json())
        first = result.solutions[0]
        other = next(s for s in result.solutions if s.reaction.request.parts[0].length_bp != first.reaction.request.parts[0].length_bp)
        from services.assembly.golden_gate_design import design_material
        for candidate in result.solutions:
            retained = design_material(candidate.design, candidate.design.preparations[0].retained_material_id)
            assert candidate.reaction.request.parts[0].length_bp == len(retained.sequence)
            assert candidate.reaction.rows[0].requested_mass_ng == pytest.approx(0.1 * 660 * len(retained.sequence) / 1000)
            assert candidate.reaction.request.parts[1].length_bp == 999
            assert candidate.reaction.request.parts[1].stock is None
        chosen = result.model_copy(update={'selected_solution_id': other.id})
        saved = await client.post(BASE + '/save', json=SaveDesignRequest(selection=freeze_selection(chosen,other.id),
            name='Alternative',idempotency_key='alternative').model_dump(mode='json'))
        assert saved.status_code == 200, saved.text
        assert saved.json()['result']['solutions'][0]['reaction'] == other.reaction.model_dump(mode='json')
        freeze_owners(monkeypatch)
        files = archive(await client.post(BASE + '/export', json=chosen.model_dump(mode='json')), 'selected-alternative')
        portable = read_design_export(files['design.json'])
        assert portable.request == other.fixed_request
        assert portable.reaction == other.reaction != first.reaction
        assert portable.fidelity == other.fidelity
        rows = list(csv.DictReader(io.StringIO(files['reaction.csv'].decode())))
        assert float(rows[0]['requested_mass_ng']) == other.reaction.rows[0].requested_mass_ng
        assert await offline_import(json.loads(files['workflow.json'])) == chosen.model_dump(mode='json')
        retained = archive(await client.get(BASE + '/' + saved.json()['operation_id'] + '/export'), 'saved-alternative')
        assert retained['reaction.csv'] == files['reaction.csv']


@pytest.mark.asyncio
async def test_accepted_edits_authored_and_derived_material_survive_zip(tmp_path, monkeypatch):
    body = request(kind='synthesis').model_dump(mode='json')
    original = 'GGTCTC' + B[6:]
    body['sources'][1]['source'].update(sequence=original,features=[])
    body['target']['exact_sequence'] = None
    body['domestication'] = [dict(source_id='insert',settings=dict(enabled=True,
        editable_regions=[dict(start=0,end=6)],unwanted_sites=[dict(enzyme_id='BsaI',recognition_sequence='GGTCTC')],max_edits=1))]
    async with client_store(tmp_path) as (client, sessions):
        proposal = await client.post(BASE,json=body)
        assert proposal.status_code == 200, proposal.text
        body['domestication'][0]['accepted_sequence'] = proposal.json()['edits'][0]['proposal']['proposed_sequence']
        accepted = await client.post(BASE,json=body)
        assert accepted.status_code == 200, accepted.text
        result = WorkflowResult.model_validate(accepted.json())
        saved = await client.post(BASE + '/save',json=SaveDesignRequest(selection=freeze_selection(result,'fixed'),
            name='Edited portable',idempotency_key='edited').model_dump(mode='json'))
        assert saved.status_code == 200, saved.text
        async with sessions() as session:
            revisions = list((await session.scalars(select(MolecularRevision).where(
                MolecularRevision.operation_id == saved.json()['operation_id']))).all())
            derived = next(r for r in revisions if r.provenance.get('transformation') == 'accepted_edit')
            assert derived.provenance['parent_revision_id']
        freeze_owners(monkeypatch)
        files = archive(await client.get(BASE + '/' + saved.json()['operation_id'] + '/export'), 'accepted-edits')
        document = json.loads(files['workflow.json'])
        assert document['result']['requested'] == result.requested.model_dump(mode='json')
        assert document['result']['edits'] == result.model_dump(mode='json')['edits']
        assert document['result']['edit_evidence_authority'] == 'operator_supplied_frozen'
        core = read_design_export(files['design.json'])
        assert core.domestication['insert'] == result.edits[0].proposal
        assert portable_source(core,'insert').sequence == body['domestication'][0]['accepted_sequence'] != original
        assert await offline_import(document) == saved.json()['result']


@pytest.mark.asyncio
async def test_import_closed_document_and_preview_has_no_store_dependency(tmp_path):
    async with client_store(tmp_path) as (client, _):
        response = await client.post(BASE,json={'task':'evaluate_overhangs','junctions':['AATG']})
        result = response.json()
    app = FastAPI(); app.include_router(routes.router)
    app.dependency_overrides[get_molbio_session] = forbid
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
        files = archive(await client.post(BASE + '/export',json=result),'unsaved-evidence')
        document = json.loads(files['workflow.json'])
        assert (await client.post(BASE + '/import',json=document)).json() == result
        for invalid in ({**document,'extra':True}, {**document,'schema_version':'future'}, {'result':result}):
            assert (await client.post(BASE + '/import',json=invalid)).status_code == 422
