"""RP16 receiving through native Methods API/store/compiler; inert transport only."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import pytest
from test_bioxp_methods_api import store, BASE
from test_bioxp_methods_integrated import submit_body
from test_bioxp_method_finish import recipe


@pytest.mark.asyncio
async def test_referenced_nonempty_class_content_is_frozen_during_async_submit(store):
    client, transport, path, _ = store
    starters = (await client.get(BASE + '/liquid-classes/starters')).json()
    entry = deepcopy(next(x for x in starters if x['settings'].get('aspirate_speed_ul_s')))
    entry['id'] = 'rp16-referenced-class'
    entry['settings'] = {'aspirate_speed_ul_s': '050.000'}
    entry['authored_settings'] = deepcopy(entry['settings'])
    entry['context']['applicable_fields'] = ['aspirate_speed_ul_s']
    saved = await client.post(BASE + '/liquid-classes', json={'method': entry, 'name': 'Content mutation probe'})
    assert saved.status_code == 201, saved.text
    url = BASE + '/liquid-classes/' + saved.json()['id']
    pin = (await client.get(url + '/revisions/1')).json()
    dependency = {**pin['method'], 'revision': pin['revision']}
    native_recipe = recipe(); native_recipe.pop('aspiration_speed_ul_s')
    method = {'schema':'bms.bioxp-method.v1','name':'RP16 actual referenced class', 'steps':[{
        'type':'action','step_id':'liquid','action':'liquid_recipe','inputs':{'recipe':native_recipe,
        'liquid':{'liquid_class':dependency['id'],'context':dependency['context']}}}]}
    body = submit_body(method=method, dependencies={'liquid_classes':[dependency]})
    before = deepcopy(body)
    compiled_response = await client.post(BASE + '/compile',json={k: before[k] for k in ('method','bindings','dependencies')})
    assert compiled_response.status_code == 200, compiled_response.text
    compiled = compiled_response.json()
    assert compiled['document'], compiled['issues']
    transport.release = asyncio.Event()
    task = asyncio.create_task(client.post(BASE + '/quick-runs',json=body))
    await asyncio.wait_for(transport.entered.wait(),10)
    try:
        changed = deepcopy(entry)
        changed['settings']['aspirate_speed_ul_s'] = '075.2500'
        changed['authored_settings']['aspirate_speed_ul_s'] = '075.2500'
        updated = await client.put(url,json={'method':changed,'expected_base_revision':1})
        assert updated.status_code == 200, updated.text
        assert updated.json()['revision'] == 2
        assert (await client.get(url)).json()['method']['settings']['aspirate_speed_ul_s']=='075.2500'
        assert (await client.get(url+'/revisions/1')).json()['method']==entry
        # Mutate caller-owned content too; JSON HTTP boundary must already own it.
        body['dependencies']['liquid_classes'][0]['settings']['aspirate_speed_ul_s']='999'
    finally:
        transport.release.set()
    response = await task
    assert response.status_code == 202, response.text
    snapshot = response.json()['method_snapshot']
    assert snapshot['dependencies'] == before['dependencies']
    sent = transport.calls[0][1]['json_data']['document']
    assert sent['metadata']['bms_method_run'] == snapshot
    recipes=[a['params']['recipe'] for s in sent['stages'] for a in s['actions'] if 'recipe' in a.get('params',{})]
    assert recipes and all(r['aspiration_speed_ul_s']==50 for r in recipes)
    assert snapshot['compilation']==compiled
    if os.environ.get('RECEIVING_ASYNC_EXPORT'):
        Path(os.environ['RECEIVING_ASYNC_EXPORT']).write_text(json.dumps({'request':before,'revision1':pin,'revision2':updated.json(),'snapshot':snapshot,'native_document':sent,'sqlite':str(path),'scope':'real ASGI Methods and SQLite; actual compiler; inert admission transport, no physical execution'},indent=2))
