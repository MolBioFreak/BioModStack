"""Actual production DOM/CDP journeys against scratch native HTTP; NOT INFERENCE."""
import argparse, base64, json, time
from pathlib import Path
from urllib.request import urlopen
from websockets.sync.client import connect

p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--phase',required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
for attempt in range(120):
    try:
        page=next(x for x in json.load(urlopen('http://127.0.0.1:18873/json',timeout=1)) if x['type']=='page');break
    except Exception:
        if attempt==119:raise
        time.sleep(.25)
ws=connect(page['webSocketDebuggerUrl'],origin=None,max_size=64*1024*1024)
events=[]; serial=0; assertions=[]
def call(method,**params):
    global serial
    serial+=1; ws.send(json.dumps(dict(id=serial,method=method,params=params)))
    while True:
        msg=json.loads(ws.recv(timeout=60))
        if msg.get('id')==serial:
            if 'error' in msg:raise RuntimeError(msg['error'])
            return msg.get('result',{})
        events.append(msg)
def js(expr):
    r=call('Runtime.evaluate',expression=expr,awaitPromise=True,returnByValue=True)
    if 'exceptionDetails' in r:raise RuntimeError(r['exceptionDetails'])
    return r.get('result',{}).get('value')
def wait(expr):
    for _ in range(160):
        if js(expr):return
        time.sleep(.25)
    raise AssertionError('Timeout: '+expr)
def check(label,condition):
    assertions.append(dict(label=label,passed=bool(condition)))
    assert condition,label
def click_expr(expr):
    xy=js(f"(()=>{{let e={expr};if(!e)throw Error('Missing control');e.scrollIntoView({{block:'center'}});let r=e.getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}}}})()")
    call('Input.dispatchMouseEvent',type='mousePressed',button='left',clickCount=1,**xy)
    call('Input.dispatchMouseEvent',type='mouseReleased',button='left',clickCount=1,**xy);time.sleep(.5)
def button(text,owner='document'):
    click_expr(f"[...{owner}.querySelectorAll('button')].find(e=>e.textContent.trim()==={json.dumps(text)})")
def goto(path):
    call('Page.navigate',url='http://127.0.0.1:18871/bms/'+path);time.sleep(2)
def capture(name):
    responses=[]
    for e in list(events):
        if e.get('method')!='Network.responseReceived':continue
        r=e['params']['response']
        if '/api/' not in r['url']:continue
        try:body=call('Network.getResponseBody',requestId=e['params']['requestId'])
        except Exception as exc:body={'unavailable':str(exc)}
        responses.append(dict(url=r['url'],status=r['status'],body=body))
    (a.output/(name+'.json')).write_text(json.dumps(dict(url=js('location.href'),text=js('document.body.innerText'),storage=js('({...localStorage})'),controls=js("[...document.querySelectorAll('input,select,button')].filter(x=>x.checkVisibility()).map(x=>({tag:x.tagName,label:x.getAttribute('aria-label'),text:x.textContent,value:x.value,checked:x.checked,pressed:x.getAttribute('aria-pressed')}))"),events=events.copy(),responses=responses,assertions=assertions),indent=2))
    events.clear()
    (a.output/(name+'.png')).write_bytes(base64.b64decode(call('Page.captureScreenshot',format='png')['data']))
call('Network.enable');call('Runtime.enable');call('Page.enable');call('Network.setCacheDisabled',cacheDisabled=True);call('Emulation.setDeviceMetricsOverride',width=1500,height=1100,deviceScaleFactor=1,mobile=False)
try:
    if a.phase=='clone':
        goto('');wait("document.body.innerText.includes('NOT INFERENCE Resequence Clone')")
        button('Clone',"[...document.querySelectorAll('tr')].find(r=>r.innerText.includes('NOT INFERENCE Resequence Clone'))")
        wait("!!document.querySelector('[aria-label=\"Resequence engine\"]')")
        check('Dashboard Clone reaches ordinary Resequence editor',js("location.pathname==='/bms/submit'"))
        check('Clone hydrates native engine',js("document.querySelector('[aria-label=\"Resequence engine\"]').value==='mpnn'"))
        check('Clone preserves explicit zero',js("document.querySelector('[aria-label=\"Designer setting mpnn_backbone_noise\"]').value==='0'"))
        check('Clone preserves explicit Local',js("[...document.querySelectorAll('[aria-label=\"Execution target\"] button')].find(x=>x.textContent.trim()==='Local').getAttribute('aria-pressed')==='true'"))
        check('Clone data consumed rather than preseeded',js("localStorage.getItem('clonedJobData')===null"))
        capture('clone-received')
        button('Template Manager');wait("!!document.querySelector('input[placeholder=\"e.g., My Boltz Config\"]')")
        click_expr("document.querySelector('input[placeholder=\"e.g., My Boltz Config\"]')");call('Input.insertText',text='NOT INFERENCE R3 clone')
        button('Save Template');time.sleep(2);capture('clone-saved')
        templates=js("fetch('/api/user-templates').then(r=>r.json())")
        (a.output/'template-readback.json').write_text(json.dumps(templates,indent=2))
        check('Real native Template readback contains browser save','NOT INFERENCE R3 clone' in json.dumps(templates))
        saved=next(t for t in templates if t['name']=='NOT INFERENCE R3 clone')['params']['editor_state']
        check('Saved clone retains sparse zero and false native settings',saved['designer_settings']['mpnn_backbone_noise']==0 and saved['validator_drafts']['protenix_v2']['values']['protenix_use_msa'] is False)
        check('Saved clone retains explicit Local and three sequences',saved['execution_target_id'] is None and saved['designer_settings']['seqs_per_design']==3)

    elif a.phase=='checkpoint':
        goto('');wait("document.body.innerText.includes('NOT INFERENCE checkpoint')")
        button('Continue',"[...document.querySelectorAll('tr')].find(r=>r.innerText.includes('NOT INFERENCE checkpoint'))")
        wait("!!document.querySelector('[aria-label=\"Completed stage continuation\"] input[type=checkbox]')")
        owner="document.querySelector('[aria-label=\"Completed stage continuation\"]')"
        check('Dashboard Continue reaches real checkpoint review',js("location.pathname==='/bms/designs/checkpoint'"))
        check('Current worker initially retained',js(f"[...{owner}.querySelectorAll('button')].find(x=>x.textContent.trim()==='Vast · NOT INFERENCE worker').getAttribute('aria-pressed')==='true'"))
        click_expr(owner+".querySelector('input[type=checkbox]')");button('Local',owner)
        check('Explicit Local replaces inherited target',js(f"[...{owner}.querySelectorAll('button')].find(x=>x.textContent.trim()==='Local').getAttribute('aria-pressed')==='true'"))
        check('Selected stage outputs enable Continue',js(f"![...{owner}.querySelectorAll('button')].find(x=>x.textContent.trim()==='Continue selected stage outputs').disabled"))
        capture('checkpoint-local')
        button('Vast · NOT INFERENCE worker',owner);button('Keep current execution target',owner)
        check('Keep current restores inherited semantics',js("document.body.innerText.includes('Default: keep the current execution target.')"))
        capture('checkpoint-restored')
    elif a.phase=='comparison':
        artifact='70e6bea4-3518-5283-a76a-d06550619962'
        for model in (2,1):
            goto(f'designs/job?artifact_id={artifact}&model_number={model}')
            wait("document.body.innerText.includes('Select saved structures for another operation')")
            click_expr("[...document.querySelectorAll('summary')].find(x=>x.textContent.trim()==='Select saved structures for another operation')")
            click_expr("document.querySelector('input[aria-label^=\"Use native document\"]')")
        native=js("JSON.parse(localStorage.getItem('bms:selected-designs:job:native'))")
        check('Actual source selections retain both exact MODEL identities',sorted(s['document'].get('model') for s in native)==[1,2])
        button('Compare result sets');button('Candidates',"document.querySelector('[aria-label=\"Binder result comparison\"]')")
        wait("!!document.querySelector('a[download=\"native-confidence-comparison.json\"]')")
        time.sleep(2)
        capture('comparison-selected')
        call('Page.reload',ignoreCache=True);time.sleep(3);button('Compare result sets')
        wait("!!document.querySelector('a[download=\"native-confidence-comparison.json\"]')")
        saved=js("JSON.parse(localStorage.getItem('binder-result-comparison::job'))")
        check('Cold browser reload preserves comparison exact selections',sorted(s['document'].get('model') for s in saved['native'])==[1,2])
        check('Cold browser reload preserves Candidates view',saved['view']=='designs')
        downloads=a.output/'downloads';downloads.mkdir(exist_ok=True)
        call('Browser.setDownloadBehavior',behavior='allow',downloadPath=str(downloads.resolve()))
        click_expr("document.querySelector('a[download=\"native-confidence-comparison.json\"]')")
        for _ in range(100):
            if (downloads/'native-confidence-comparison.json').exists():break
            time.sleep(.1)
        export=json.loads((downloads/'native-confidence-comparison.json').read_text())
        check('Actual downloaded comparison JSON retains MODEL 1 and MODEL 2',sorted(s['document'].get('model') for s in export['selection']['native_sources'])==[1,2])
        check('Export retains original document hash for both MODEL selections',all(s['document']['sha256']=='a1916e81afe2cb0511e68fc3af3c39a8801a665f14fa11ae4fe176a2dfebc5d5' for s in export['selection']['native_sources']))
        check('Comparison export settled native reads',export['read_status']=='loaded')
        profiles={p['id'].rsplit(',',1)[-1]:p['metric'] for p in export['profiles']}
        check('MODEL 1 retains genuine zero-valued confidence',profiles['1]']['status']=='ok' and profiles['1]']['values']==[0] and profiles['1]']['residues'][0]['modelId']=='1')
        check('MODEL 2 does not borrow MODEL 1 confidence',profiles['2]']=={'status':'unavailable','reason':'native_local_qa_not_published'})
        click_expr("[...document.querySelectorAll('summary')].find(x=>x.textContent.trim()==='Profile sources')")
        capture('comparison-cold-export')
    else:raise ValueError(a.phase)
finally:
    capture(a.phase+'-final');ws.close()
    (a.output/(a.phase+'-assertions.json')).write_text(json.dumps(assertions,indent=2))
