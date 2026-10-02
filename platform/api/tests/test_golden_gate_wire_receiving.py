"""Actual full/normalized ASGI A/B; native science, scratch SQLite, ZIP/import."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from services.assembly.golden_gate_workflow import freeze_selection
from services.assembly.golden_gate_workflow_types import WorkflowResult, SaveDesignRequest
from services.assembly.golden_gate_workflow_wire import project_workflow, expand_workflow
from test_golden_gate_design_core import request, binding, A, B, rc
from test_golden_gate_workflow_receiving import client_store

BASE = '/api/molbio/assembly/golden-gate/design'


def fixture(name) -> dict[str, Any]:
    body = request(kind='synthesis').model_dump(mode='json')
    if name == 'long':
        body['sources'][1]['source'].update(sequence='ACGT' * 2500, features=[])
        body['parts'][1]['preparation']['region']['end'] = 10000
        body['target']['exact_sequence'] = None
    elif name == 'split':
        body = dict(task='split_target', target=dict(id='target', source=dict(kind='inline',
            sequence='AATG'+A+'GGAG'+B, topology='circular')), enzyme=binding('BsaI'),
            windows=[dict(start=20,end=22),dict(start=60,end=61),dict(start=90,end=91)],
            preparation=dict(kind='synthesis',clamp='TT',spacer='A'),
            search=dict(ranking_mode='lexicographic',unique_classes=False,exclude_palindromes=False))
    elif name == 'edited':
        body['sources'][1]['source'].update(sequence='GGTCTC'+B[6:], features=[])
        body['target']['exact_sequence'] = None
        body['domestication'] = [dict(source_id='insert', settings=dict(enabled=True,
            editable_regions=[dict(start=0,end=6)],unwanted_sites=[dict(enzyme_id='BsaI',recognition_sequence='GGTCTC')],max_edits=1))]
    elif name == 'reverse':
        body['sources'][1]['source'].update(sequence='CATT'+rc(B), features=[])
        body['parts'][1].update(orientation='reverse', role='backbone', preparation=dict(kind='prepared',
            left_end=dict(type='sticky_5',overhang='CATT',protruding_strand='top'),
            right_end=dict(type='sticky_5',overhang='GGAG',protruding_strand='bottom')))
    elif name == 'aliases':
        body = request().model_dump(mode='json')
        body['sources'][1]['source']['features'] = [dict(id='compound', type='misc_feature',
            name='compound reverse', segments=[dict(start=9,end=18),dict(start=27,end=36)], strand=-1,
            qualifiers={'nested': [None,False,0,1.25,{'sequence_ref':0,'material_ref':0}],
                '__proto__': {'polluted':'never'}, 'constructor': {'prototype': {'polluted':True}},
                'sequence': 'literal-not-DNA', 'unicode':'αβ'})]
        alias = deepcopy(body['sources'][1]); alias['id'] = 'alias'
        body['sources'].append(alias)
    return body


def capture(response):
    assert response.status_code == 200, response.text
    return dict(request_bytes=len(response.request.content), response_bytes=len(response.content),
        content_encoding=response.headers.get('content-encoding'))


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['long','split','edited','reverse','aliases'])
async def test_real_wire_roundtrip(tmp_path, name):
    body = fixture(name)
    metrics = {}
    async with client_store(tmp_path) as (client, sessions):
        if name == 'edited':
            proposal = await client.post(BASE,json=body)
            assert proposal.status_code == 200, proposal.text
            body['domestication'][0]['accepted_sequence'] = proposal.json()['edits'][0]['proposal']['proposed_sequence']
        full = await client.post(BASE,json=body)
        normal = await client.post(BASE+'?view=normalized',json=project_workflow(body,'request').model_dump(mode='json'))
        metrics['preview_full'], metrics['preview_normalized'] = capture(full), capture(normal)
        expected = full.json()
        assert expand_workflow(normal.json(),'result') == expected
        assert project_workflow(expected,'result').model_dump(mode='json') == normal.json()
        if name == 'long':
            assert normal.json()['sequences'].count('ACGT'*2500) == 1
            assert normal.text.count('"'+'ACGT'*2500+'"') == 1
            assert metrics['preview_normalized']['response_bytes'] < metrics['preview_full']['response_bytes']
        if name == 'split':
            assert len(expected['solutions']) == 2
            refs = [s['design']['materials'] for s in normal.json()['payload']['solutions']]
            mats = normal.json()['materials']
            shared = [i for i,m in enumerate(mats) if m['id']=='preparation:part-2']
            assert len(shared) == 1
            assert all({'material_ref':shared[0]} in r for r in refs)
        if name == 'aliases':
            mats = normal.json()['materials']
            insert, alias = [next(m for m in mats if m['id']=='source:'+i) for i in ['insert','alias']]
            assert insert != alias and insert['sequence'] == alias['sequence']
        result = WorkflowResult.model_validate(expected)
        assert result.selected_solution_id is not None
        save = SaveDesignRequest(selection=freeze_selection(result,result.selected_solution_id),
            name='transport' if name=='long' else name,idempotency_key='transport' if name=='long' else name).model_dump(mode='json')
        saved_full = await client.post(BASE+'/save',json=save)
        saved_normal = await client.post(BASE+'/save?view=normalized',json=project_workflow(save,'save').model_dump(mode='json'))
        metrics['save_full'], metrics['save_normalized'] = capture(saved_full), capture(saved_normal)
        saved = saved_full.json()
        assert expand_workflow(saved_normal.json(),'saved') == saved
        if name == 'long':
            assert saved_normal.text.count('"'+'ACGT'*2500+'"') == 1
            assert saved_normal.request.content.count(('"'+'ACGT'*2500+'"').encode()) == 1
        operation = saved['operation_id']
        got = await client.get(BASE+'/'+operation+'?view=normalized')
        metrics['get_normalized'] = capture(got)
        full_get = await client.get(BASE+'/'+operation)
        metrics['get_full'] = capture(full_get)
        assert expand_workflow(got.json(),'saved') == full_get.json() == saved
        # Both preview request formats use the real portable owner; compare entries,
        # not ZIP timestamps. Saved export remains the full self-contained format.
        preview_archives = []
        for value in [expected, normal.json()]:
            response = await client.post(BASE+'/export',json=value)
            assert response.status_code == 200, response.text
            with ZipFile(io.BytesIO(response.content)) as z:
                preview_archives.append({n:z.read(n) for n in z.namelist()})
        assert preview_archives[0] == preview_archives[1]
        exported = await client.get(BASE+'/'+operation+'/export')
        assert exported.status_code == 200, exported.text
        with ZipFile(io.BytesIO(exported.content)) as z:
            document = json.loads(z.read('workflow.json'))
        assert document['result'] == saved['result']
        imported = await client.post(BASE+'/import?view=normalized',json=project_workflow(document,'portable').model_dump(mode='json'))
        assert imported.status_code == 200, imported.text
        assert expand_workflow(imported.json(),'result') == document['result']
        assert (await client.post(BASE+'/import',json=document)).json() == document['result']
        if name == 'long':
            from sqlalchemy import select
            from molbio_models import MolecularRevision
            async with sessions() as session:
                revisions = (await session.scalars(select(MolecularRevision).where(MolecularRevision.operation_id == operation))).all()
            revision_body = deepcopy(body)
            for source in revision_body['sources']:
                revision = next(r for r in revisions if r.provenance.get('material_id') == 'source:'+source['id'])
                source['source'] = dict(kind='molecular_revision',revision_id=revision.id)
            revision_full = await client.post(BASE,json=revision_body)
            revision_normal = await client.post(BASE+'?view=normalized',json=project_workflow(revision_body,'request').model_dump(mode='json'))
            metrics['revision_preview_full'],metrics['revision_preview_normalized'] = capture(revision_full),capture(revision_normal)
            assert expand_workflow(revision_normal.json(),'result') == revision_full.json()
            revision_result = WorkflowResult.model_validate(revision_full.json())
            revision_save = SaveDesignRequest(selection=freeze_selection(revision_result,'fixed'),
                name='revision-inputs',idempotency_key='revision-inputs').model_dump(mode='json')
            revision_saved = await client.post(BASE+'/save',json=revision_save)
            revision_saved_normal = await client.post(BASE+'/save?view=normalized',json=project_workflow(revision_save,'save').model_dump(mode='json'))
            metrics['revision_save_full'],metrics['revision_save_normalized'] = capture(revision_saved),capture(revision_saved_normal)
            assert expand_workflow(revision_saved_normal.json(),'saved') == revision_saved.json()
            # Fresh process, same scratch immutable records, no workflow execution.
            import subprocess,sys
            code = '''import asyncio,json,sys
from pathlib import Path
import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from molbio_database import get_molbio_session
from routers.molbio_golden_gate_design import router
from services.assembly import golden_gate_workflow_persistence as p
async def main():
 def forbidden(*a,**k):raise AssertionError('read recomputed science')
 p.run_workflow=forbidden
 engine=create_async_engine('sqlite+aiosqlite:///'+sys.argv[1])
 sessions=async_sessionmaker(engine)
 async def dep():
  async with sessions() as s:yield s
 app=FastAPI();app.include_router(router);app.dependency_overrides[get_molbio_session]=dep
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as c:
  response=await c.get('/api/molbio/assembly/golden-gate/design/'+sys.argv[2]+'?view=normalized')
  assert response.status_code==200,response.text
  Path(sys.argv[3]).write_text(response.text)
 await engine.dispose()
asyncio.run(main())
'''
            fresh = subprocess.run([sys.executable,'-c',code,str(tmp_path/'workups.db'),operation,str(tmp_path/'fresh.json')],capture_output=True,text=True)
            assert fresh.returncode == 0, fresh.stderr
            assert json.loads((tmp_path/'fresh.json').read_text()) == got.json()
        evidence = os.environ.get('BMS_GG_WIRE_EVIDENCE')
        if evidence:
            Path(evidence).mkdir(parents=True,exist_ok=True)
            Path(evidence,f'{name}.json').write_text(json.dumps(dict(request=body,preview=expected,
                normalized=normal.json(),save_request=save,saved=saved,saved_normalized=saved_normal.json(),
                document=document,imported=imported.json(),metrics=metrics),ensure_ascii=False))


@pytest.mark.asyncio
async def test_wire_schema_and_invalid_references(tmp_path):
    async with client_store(tmp_path) as (client, _):
        body = fixture('long')
        valid = project_workflow(body,'request').model_dump(mode='json')
        for bad_index in [-1,True,0.5,999999,'0',None]:
            bad = deepcopy(valid)
            bad['payload']['sources'][0]['source']['sequence'] = {'sequence_ref':bad_index}
            assert (await client.post(BASE,json=bad)).status_code == 422
        bad = deepcopy(valid); bad['kind'] = 'result'
        assert (await client.post(BASE,json=bad)).status_code == 422
        bad = deepcopy(valid); bad['payload']['sources'][0]['source']['sequence']['extra'] = True
        assert (await client.post(BASE,json=bad)).status_code == 422
        # Same native closed-contract validation after expansion, no permissive
        # wire path for science typos.
        bad = deepcopy(valid); bad['payload']['undocumented_science'] = True
        assert (await client.post(BASE,json=bad)).status_code == 422
    from fastapi import FastAPI
    from routers.molbio_golden_gate_design import router
    app=FastAPI();app.include_router(router)
    schema=app.openapi()
    assert 'WorkflowWire' in schema['components']['schemas']
    assert 'AssembleTask' in schema['components']['schemas']
    assert any(p['name']=='view' for p in schema['paths'][BASE]['post']['parameters'])


@pytest.mark.asyncio
async def test_actual_browser_wrapper_requests_reach_native_owners(tmp_path):
    fixtures = Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures/golden-gate'
    calls = json.loads((fixtures/'wire-browser-requests.json').read_text())
    expected = json.loads((fixtures/'wire-receiving.json').read_text())
    async with client_store(tmp_path) as (client, _):
        # These are captured actual Axios serialized requests, independently
        # pinned by the mounted wrapper test, not native-generated stand-ins.
        preview = await client.post(calls[0]['url'],params=calls[0]['params'],json=calls[0]['body'])
        assert preview.status_code == 200, preview.text
        assert preview.json() == expected['normalized']
        saved_response = await client.post(calls[1]['url'],params=calls[1]['params'],json=calls[1]['body'])
        assert saved_response.status_code == 200, saved_response.text
        saved = expand_workflow(saved_response.json(),'saved')
        assert saved['selection'] == expected['saved']['selection']
        assert saved['result'] == expected['saved']['result']
        # Fresh scratch UUIDs differ from the recorded instance, science does not.
        got = await client.get(BASE+'/'+saved['operation_id'],params=calls[2]['params'])
        assert got.json() == saved_response.json()
        exported = await client.post(calls[3]['url'],json=calls[3]['body'])
        assert exported.status_code == 200, exported.text
        with ZipFile(io.BytesIO(exported.content)) as z:
            assert json.loads(z.read('workflow.json'))['result'] == expected['preview']
        imported = await client.post(calls[5]['url'],params=calls[5]['params'],json=calls[5]['body'])
        assert imported.status_code == 200, imported.text
        assert imported.json() == expected['imported']
