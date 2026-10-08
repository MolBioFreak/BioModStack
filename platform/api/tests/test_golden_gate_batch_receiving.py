"""One routed batch, unchanged native science, partial delivery and existing retention."""
import io
import json
import os
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest
from fastapi import FastAPI
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from routers import molbio_golden_gate_batch as route
from services.assembly.golden_gate_batch import BatchRequest, BatchEvent, combination_count, combination, selected_indices
from services.assembly.golden_gate_workflow_types import WorkflowResult, SaveDesignRequest, REQUEST_ADAPTER
from services.assembly.golden_gate_workflow import freeze_selection, run_workflow
from test_golden_gate_design_core import request, binding, A, B
from test_golden_gate_workflow_receiving import client_store

URL = '/api/molbio/assembly/golden-gate/design'


def batch_request():
    base = REQUEST_ADAPTER.validate_python(request().model_dump())
    alternatives = []
    for i, (kind, reverse) in enumerate([('pcr', False), ('synthesis', True)]):
        native = request(kind=kind, reverse=reverse)
        alternatives.append(dict(id=f'insert-{i}', source=native.sources[1], part=native.parts[1]))
    slots = [dict(part_id='backbone', alternatives=[dict(id=f'vector-{i}', source=base.sources[0],
        part=base.parts[0].model_copy(update={'name': f'vector-{i}'})) for i in range(2)]),
        dict(part_id='insert', alternatives=alternatives)]
    return BatchRequest(base=base, slots=slots)


def evidence(name, value):
    if path := os.environ.get('BMS_GG_BATCH_EVIDENCE'):
        Path(path, name).write_text(json.dumps(value, indent=2))


def test_cartesian_sampling_and_native_child_contracts():
    body = batch_request()
    assert combination_count(body) == 4
    assert list(selected_indices(body)) == [0, 1, 2, 3]
    assert [combination(body, i)[1] for i in range(4)] == [
        ['vector-0', 'insert-0'], ['vector-0', 'insert-1'], ['vector-1', 'insert-0'], ['vector-1', 'insert-1']]
    sample = BatchRequest.model_validate({**body.model_dump(), 'scope': dict(mode='sampled', count=2, seed=17)})
    assert selected_indices(sample) == selected_indices(BatchRequest.model_validate_json(sample.model_dump_json()))
    assert len(set(selected_indices(sample))) == 2
    for index in range(4):
        native, _ = combination(body, index)
        alt = body.slots[1].alternatives[index % 2]
        assert native.parts[1] == alt.part
        assert native.sources[1] == alt.source
        assert native.enzyme == body.base.enzyme and native.primer_settings == body.base.primer_settings
    assert body == batch_request()  # enumeration never mutates its inputs
    schema = BatchRequest.model_json_schema()
    assert schema['$defs']['SlotAlternative']['properties']['part']['$ref'].endswith('/Part')
    with pytest.raises(ValueError, match='exceeds'):
        BatchRequest.model_validate({**body.model_dump(), 'scope': dict(mode='sampled', count=5, seed=0)})
    evidence('batch-request.schema.json', schema)
    evidence('batch-event.schema.json', TypeAdapter(BatchEvent).json_schema())
    evidence('batch-request.json', body.model_dump(mode='json'))


def test_large_cartesian_count_has_no_enumeration_cap():
    base = batch_request().base
    source = base.sources[0]
    parts = [base.parts[0].model_copy(update={'id': f'p{i}'}) for i in range(70)]
    body = BatchRequest(base=base.model_copy(update={'parts': parts}), slots=[
        dict(part_id=p.id, alternatives=[dict(id=str(j), source=source, part=p) for j in range(2)]) for p in parts],
        scope=dict(mode='sampled', count=3, seed=2))
    assert combination_count(body) == 2 ** 70
    assert len(selected_indices(body)) == 3
    assert all(0 <= i < 2 ** 70 for i in selected_indices(body))


@pytest.mark.asyncio
async def test_one_http_batch_all_results_save_reopen_export_and_next_stage(tmp_path):
    async with client_store(tmp_path) as (client, sessions):
        schema = client._transport.app.openapi()
        endpoint = schema['paths'][URL + '/batch']['post']
        assert endpoint['requestBody']['content']['application/json']['schema']['$ref'].endswith('/BatchRequest')
        event_schema = endpoint['responses']['200']['content']['application/x-ndjson']['schema']
        assert 'anyOf' in event_schema or 'oneOf' in event_schema
        evidence('batch-openapi.json', schema)
        body = batch_request()
        response = await client.post(URL + '/batch', json=body.model_dump(mode='json'))
        assert response.status_code == 200, response.text
        assert response.headers['content-type'].startswith('application/x-ndjson')
        events = [TypeAdapter(BatchEvent).validate_json(line) for line in response.text.splitlines()]
        from jsonschema import Draft202012Validator
        validator = Draft202012Validator({'allOf': [event_schema], 'components': schema['components']})
        for event in events:
            validator.validate(event.model_dump(mode='json'))
        assert [e.event for e in events] == ['scope', 'result', 'result', 'result', 'result', 'finished']
        assert events[-1].coverage == 'full'
        assert events[-1].progress.model_dump() == dict(total='4', selected='4', evaluated='4', completed='4', omitted='0', remaining='0')
        expected = 'AATG' + A + 'GGAG' + B
        saved_values = []
        for event in events[1:-1]:
            assert event.result.solutions[0].design.solutions[0].sequence == expected
            native, _ = combination(body, int(event.index))
            assert event.result == run_workflow(native)
            fixed = freeze_selection(event.result, event.result.selected_solution_id)
            saved = await client.post(URL + '/save', json=SaveDesignRequest(selection=fixed, name='batch ' + event.index, idempotency_key=event.index).model_dump(mode='json'))
            assert saved.status_code == 200, saved.text
            value = saved.json()
            assert (await client.get(URL + '/' + value['operation_id'])).json() == value
            exported = await client.get(URL + '/' + value['operation_id'] + '/export')
            assert exported.status_code == 200
            with ZipFile(io.BytesIO(exported.content)) as archive:
                portable = json.loads(archive.read('workflow.json'))
                assert portable['result'] == value['result']
                assert expected in archive.read('product.fasta').decode().replace('\n', '')
            saved_values.append(value)
        # A new enzyme/stage consumes the actual immutable product, not its editable projection.
        first = saved_values[0]
        from molbio_models import NucleotideSequence
        async with sessions() as session:
            row = await session.get(NucleotideSequence, first['product_document_id'])
            row.sequence = 'AAAA'
            await session.commit()
        stage = request('BbsI', kind='synthesis').model_dump(mode='json')
        stage['sources'][0] = dict(id='donor', source=dict(kind='molecular_revision', revision_id=first['product_revision_id']))
        stage['parts'][0]['preparation'] = dict(kind='synthesis', region=dict(start=0, end=len(expected)),
            left=dict(clamp='CC', spacer='AC', fusion='AATG'), right=dict(clamp='GG', spacer='AC', fusion='GGAG'))
        stage['target']['exact_sequence'] = 'AATG' + expected + 'GGAG' + B
        stage['primer_settings']['primer_concentration_nM'] = 375
        second_response = await client.post(URL, json=stage)
        assert second_response.status_code == 200, second_response.text
        second = WorkflowResult.model_validate(second_response.json())
        assert second.solutions[0].design.solutions[0].sequence == stage['target']['exact_sequence']
        assert second.requested.enzyme.enzyme_id == 'BbsI'
        assert second.requested.primer_settings.primer_concentration_nM == 375
        assert first['result']['requested']['primer_settings']['primer_concentration_nM'] == 250
        second_saved = await client.post(URL + '/save', json=SaveDesignRequest(selection=freeze_selection(second, 'fixed'), name='Stage two BbsI', idempotency_key='second').model_dump(mode='json'))
        assert second_saved.status_code == 200, second_saved.text
        assert (await client.get(URL + '/' + first['operation_id'])).json() == first
        evidence('batch-events.json', [e.model_dump(mode='json') for e in events])
        evidence('stage-two.json', second_saved.json())
        evidence('batch-first-saved.json', first)
        evidence('http-sizes.json', dict(request_bytes=len(response.request.content), response_bytes=len(response.content), content_encoding=response.headers.get('content-encoding')))


@pytest.mark.asyncio
async def test_sampled_scope_and_bad_combination_keep_other_results(tmp_path):
    async with client_store(tmp_path) as (client, _):
        body = batch_request().model_dump(mode='json')
        body['scope'] = dict(mode='sampled', count=2, seed=17)
        first = await client.post(URL + '/batch', json=body)
        second = await client.post(URL + '/batch', json=body)
        assert first.status_code == 200, first.text
        events = [json.loads(line) for line in first.text.splitlines()]
        assert events == [json.loads(line) for line in second.text.splitlines()]
        assert events[-1]['coverage'] == 'sampled'
        assert events[-1]['progress']['omitted'] == '2'
        body = batch_request().model_dump(mode='json')
        body['slots'][0]['alternatives'][0]['part']['preparation']['retained_fragment_index'] = 999
        bad = await client.post(URL + '/batch', json=body)
        events = [json.loads(line) for line in bad.text.splitlines()]
        assert [e['event'] for e in events] == ['scope', 'error', 'error', 'result', 'result', 'finished']
        assert events[-1]['coverage'] == 'incomplete'
        assert events[-1]['progress'] == dict(total='4', selected='4', evaluated='4', completed='2', omitted='0', remaining='0')
        assert all(e['result']['solutions'] for e in events if e['event'] == 'result')
        evidence('sampled-events.json', [json.loads(line) for line in first.text.splitlines()])
        evidence('mixed-outcomes.json', events)


@pytest.mark.asyncio
async def test_cooperative_disconnect_retains_delivered_native_result(tmp_path):
    async with client_store(tmp_path) as (_, sessions):
        checks = 0
        async def disconnected():
            nonlocal checks
            checks += 1
            return checks > 1
        async with sessions() as session:
            events = [e async for e in route.batch_events(batch_request(), session, disconnected)]
        assert [e.event for e in events] == ['scope', 'result']
        assert events[1].progress.completed == '1' and events[1].progress.remaining == '3'
        assert events[1].result.solutions[0].design.solutions[0].sequence == 'AATG' + A + 'GGAG' + B
        assert freeze_selection(events[1].result, 'fixed').request.parts


@pytest.mark.asyncio
async def test_named_user_batch_template_actual_http_roundtrip(tmp_path):
    from database import UserTemplate, get_session
    from routers import user_templates
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "templates.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(UserTemplate.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def dependency():
        async with sessions() as session:
            yield session
    app = FastAPI()
    app.include_router(user_templates.router, prefix="/api/user-templates")
    app.dependency_overrides[get_session] = dependency
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            params = batch_request().model_dump(mode='json')
            params['scope'] = dict(mode='sampled', count=2, seed=99)
            posted = await client.post('/api/user-templates', json=dict(name='My exact BsaI stage', mode='golden_gate_design', params=params))
            assert posted.status_code == 201, posted.text
            read = await client.get('/api/user-templates/' + posted.json()['id'])
            assert read.json()['name'] == 'My exact BsaI stage'
            assert read.json()['params'] == params
            assert BatchRequest.model_validate(read.json()['params']).scope.seed == 99
            evidence('named-configuration.json', read.json())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_actual_asgi_disconnect_after_first_delivered_result(tmp_path):
    import asyncio
    async with client_store(tmp_path) as (client, _):
        app = client._transport.app
        payload = batch_request().model_dump_json().encode()
        request_sent = False
        disconnected = asyncio.Event()
        chunks = []
        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {'type': 'http.request', 'body': payload, 'more_body': False}
            await disconnected.wait()
            return {'type': 'http.disconnect'}
        async def send(message):
            if message['type'] == 'http.response.start':
                assert message['status'] == 200
            if message['type'] == 'http.response.body':
                chunks.append(message.get('body', b''))
                if b'"event":"result"' in message.get('body', b''):
                    disconnected.set()
        await app({'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
            'http_version': '1.1', 'method': 'POST', 'scheme': 'http', 'path': URL + '/batch',
            'raw_path': (URL + '/batch').encode(), 'query_string': b'', 'root_path': '',
            'headers': [(b'content-type', b'application/json'), (b'content-length', str(len(payload)).encode())],
            'client': ('test', 1), 'server': ('test', 80)}, receive, send)
        events = [json.loads(line) for line in b''.join(chunks).splitlines()]
        assert [e['event'] for e in events] == ['scope', 'result']
        assert events[-1]['progress']['remaining'] == '3'
        assert events[-1]['result']['solutions'][0]['design']['solutions'][0]['sequence'] == 'AATG' + A + 'GGAG' + B
        evidence('asgi-disconnected-events.json', events)


def test_selected_alternative_preserves_full_native_control_children():
    body = batch_request().model_dump(mode='json')
    body['base']['reaction'] = dict(settings=dict(total_volume_uL=12, mastermix_overage_percent=7), parts=[], reagents=[])
    alternative = body['slots'][1]['alternatives'][0]
    alternative['automatic_primer'] = dict(part_id='insert', pair_rank=1, settings=dict(
        primer_min_length=20, primer_max_length=20, product_min_length=len(B), product_max_length=len(B),
        flank_search_span=20, gc_min_percent=0, gc_max_percent=100, gc_clamp_min=0, max_poly_x=100, tm_max_delta_c=100))
    alternative['domestication'] = dict(source_id='insert', settings=dict(enabled=False), accepted_sequence=None)
    alternative['reaction_part'] = dict(part_id='insert', amount=dict(value=0.05, unit='pmol'),
        stock=dict(value=10, unit='ng/uL'), dilution=dict(factor=2, preparation_volume_uL=30), purification='selected purified fragment')
    parsed = BatchRequest.model_validate(body)
    native, _ = combination(parsed, 0)
    chosen = parsed.slots[1].alternatives[0]
    assert native.automatic_primers == [chosen.automatic_primer]
    assert native.domestication == [chosen.domestication]
    assert list(native.reaction.parts) == [chosen.reaction_part]
    assert native.reaction.settings == parsed.base.reaction.settings
    result = run_workflow(native)
    assert result.solutions and result.requested == native
    assert result.solutions[0].design.solutions[0].sequence == 'AATG' + A + 'GGAG' + B
    assert result.solutions[0].reaction.request.settings.total_volume_uL == 12


@pytest.mark.asyncio
async def test_domestication_compact_catalog_complete_native_inventory():
    from routers import molbio_restriction
    from services.restriction_catalog import catalog_authority
    app = FastAPI()
    app.include_router(molbio_restriction.router)
    pages, items = [], []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        cursor = None
        while True:
            response = await client.get('/api/molbio/restriction/catalog', params={
                'limit': 200, 'response_view': 'compact', **({'cursor': cursor} if cursor else {})})
            assert response.status_code == 200, response.text
            page = response.json()
            pages.append(page)
            items.extend(page['items'])
            cursor = page['next_cursor']
            if cursor is None:
                break
    asset = json.loads((Path(__file__).parents[1] / 'config/molbio/restriction/restriction_enzyme_catalog_v1.json').read_text())
    expected = {r['enzyme_id']: r['recognition']['site_iupac'] for r in asset['records']}
    actual = {r['enzyme_id']: r['site_iupac'] for r in items}
    assert actual == expected
    assert len(items) == len(actual) == page['catalog']['counts']['total']
    assert len(pages) > 1
    assert all('cleavage' not in row and 'supplier_provenance' not in row for row in items)
    evidence('complete-catalog.json', dict(count=len(items), pages=len(pages), inventory=actual,
        catalog_sha256=catalog_authority.require().content_sha256))
