"""Final bounded production-shell browser actions, NOT INFERENCE.

Run inside supervisor namespace with Hermes Python (websockets installed).
Native HTTP seeds are explicit preparation, never counted as browser actions.
"""
import argparse
import json
import time
from pathlib import Path
from urllib.request import urlopen
from websockets.sync.client import connect

P=argparse.ArgumentParser();P.add_argument('--root',type=Path,required=True);P.add_argument('--evidence',type=Path,required=True);P.add_argument('--phase',default='authoring');a=P.parse_args()
a.evidence.mkdir(parents=True,exist_ok=True)
page=next(x for x in json.load(urlopen('http://127.0.0.1:18873/json')) if x['type']=='page')
events=[];assertions=[]
with connect(page['webSocketDebuggerUrl'],origin=None,max_size=64*1024*1024) as ws:
    serial=0
    def call(method,**params):
        global serial
        serial+=1;ws.send(json.dumps({'id':serial,'method':method,'params':params}))
        while True:
            m=json.loads(ws.recv(timeout=45))
            if m.get('id')==serial:
                if 'error' in m:raise RuntimeError(m['error'])
                return m.get('result',{})
            events.append(m)
    def js(expr):
        r=call('Runtime.evaluate',expression=expr,awaitPromise=True,returnByValue=True)
        if 'exceptionDetails' in r:raise RuntimeError(r['exceptionDetails'])
        return r.get('result',{}).get('value')
    def wait(expr,timeout=12):
        until=time.time()+timeout
        while time.time()<until:
            if js(expr):return
            time.sleep(.15)
        raise AssertionError('Timed out '+expr+'\n'+str(js('document.body.innerText')))
    def click(text):
        js("(()=>{const e=[...document.querySelectorAll('button,summary,a')].find(e=>e.checkVisibility()&&e.textContent.trim()==="+json.dumps(text)+");if(!e)throw Error('No visible control '+"+json.dumps(text)+");e.click()})()")
        time.sleep(.25)
    def edit(label,value):
        js("(()=>{const e=[...document.querySelectorAll('[aria-label]')].find(e=>e.checkVisibility()&&e.getAttribute('aria-label')==="+json.dumps(label)+");if(!e)throw Error('No visible field '+"+json.dumps(label)+");Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e),'value').set.call(e,"+json.dumps(value)+");e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}))})()")
        time.sleep(.2)
    def checkbox(label):
        js("(()=>{const e=[...document.querySelectorAll('input[type=checkbox]')].find(e=>e.checkVisibility()&&e.getAttribute('aria-label')==="+json.dumps(label)+");if(!e)throw Error('No visible checkbox '+"+json.dumps(label)+");e.click()})()")
        time.sleep(.2)
    def check(name,expr):
        value=js(expr);assertions.append({'name':name,'expression':expr,'actual':value,'passed':bool(value)})
        assert value,name
    def snapshot(name):
        data=js('({url:location.href,text:document.body.innerText,html:document.body.innerHTML,fields:[...document.querySelectorAll("input,select,textarea")].filter(e=>e.checkVisibility()).map(e=>({label:e.getAttribute("aria-label"),value:e.value,checked:e.checked}))})')
        (a.evidence/(name+'.json')).write_text(json.dumps(data,indent=2))
    def navigate(url):
        call('Page.navigate',url=url);time.sleep(1)
        wait("!!document.querySelector('[data-native-de-novo]')")
    call('Network.enable');call('Network.setCacheDisabled',cacheDisabled=True);call('Runtime.enable');call('Page.enable')
    try:
        seed=json.loads((a.root/'seed.json').read_text());pid=seed['project_id'];sid=seed['setup']['setup_context_id']
        url='http://127.0.0.1:18871/bms/submit?model=rfd4_proteina&mode=generate&project_id='+pid+'&setup_context_id='+sid
        if a.phase=='authoring':
            navigate(url);click('Sequence Design')
            wait("!!document.querySelector('input[aria-label=\"Enable sequence design\"]')")
            check('retained-v1 generated sequence OFF',"!document.querySelector('[aria-label=\"Enable sequence design\"]').checked")
            click('Save draft');time.sleep(.5);click('Save draft');time.sleep(.5)
            snapshot('legacy-two-saves')
        elif a.phase=='fresh':
            # New setup created by real native HTTP preparation before this phase.
            seed=json.loads((a.root/'fresh.json').read_text());sid=seed['setup_context_id']
            url='http://127.0.0.1:18871/bms/submit?model=rfd4_proteina&mode=generate&project_id='+pid+'&setup_context_id='+sid
            navigate(url);click('Sequence Design')
            wait("!!document.querySelector('[aria-label=\"Enable optional operations\"]')")
            check('fresh optional operations OFF',"!document.querySelector('[aria-label=\"Enable optional operations\"]').checked")
            checkbox('Enable optional operations')
            click('Add Sequence design');click('Add proteinmpnn call')
            click('Add Blind prediction');click('Add boltz2 call')
            click('Add Molecular dynamics');click('Add molecular_dynamics call')
            snapshot('fresh-three-independent-stages')
            # Native prediction fields arrive from the actual model router.
            for section in js("[...document.querySelectorAll('section[aria-label^=\"Invocation \" ]')].map(e=>e.getAttribute('aria-label'))"):
                js("(()=>{const e=[...document.querySelectorAll('section[aria-label]')].find(e=>e.getAttribute('aria-label')==="+json.dumps(section)+");const d=e.querySelector('details');if(d&&!d.open)d.querySelector('summary').click()})()")
            wait("!!document.querySelector('[aria-label=\"mpnn_temperature\"]')")
            edit('mpnn_temperature','0.27')
            click('molecular_dynamics settings')
            edit('MD invocation input kind','compiled');edit('MD invocation name','INERT independent MD draft');edit('tpr','inputs/retained-unexecuted.tpr')
            edit('Replicas per window','2');edit('Orchestration seed (not native stochastic seed)','0')
            click('Back to round')
            snapshot('native-invocation-settings')
            checkbox('Enable optional operations')
            check('optional operations switched OFF',"!document.querySelector('[aria-label=\"Enable optional operations\"]').checked")
            click('Save draft');time.sleep(.5)
            snapshot('fresh-off-saved')
            call('Page.reload',ignoreCache=True);time.sleep(1);wait("!!document.querySelector('[data-native-de-novo]')");click('Sequence Design')
            check('cold Project reopen remains OFF',"!document.querySelector('[aria-label=\"Enable optional operations\"]').checked")
            click('Retained operation settings')
            check('cold Project retains all three call identities',"document.querySelectorAll('section[aria-label^=\"Invocation \" ]').length===3")
            checkbox('Enable optional operations');click('molecular_dynamics settings')
            check('cold MD draft exact name',"document.querySelector('[aria-label=\"MD invocation name\"]').value==='INERT independent MD draft'")
            check('cold MD compiled source draft exact',"document.querySelector('[aria-label=\"tpr\"]').value==='inputs/retained-unexecuted.tpr'")
            click('Back to round');snapshot('fresh-restored-cold')
            click('Save draft');time.sleep(.5);click('Save draft');time.sleep(.5)
            snapshot('fresh-restored-save-twice')
        elif a.phase=='packing':
            seed=json.loads((a.root/'fresh.json').read_text());sid=seed['setup_context_id']
            url='http://127.0.0.1:18871/bms/submit?model=rfd4_proteina&mode=generate&project_id='+pid+'&setup_context_id='+sid
            navigate(url);click('Sequence Design')
            click('Add caliby_experimental call')
            wait("[...document.querySelectorAll('section[aria-label^=\"Invocation \" ]')].some(e=>e.textContent.includes('caliby_experimental'))")
            cid=js("[...document.querySelectorAll('section[aria-label^=\"Invocation \" ]')].find(e=>e.textContent.includes('caliby_experimental')).getAttribute('aria-label').slice(11)")
            edit('Mode '+cid,'sidechain_pack')
            click('Add boltz2 call')
            prediction=js("[...document.querySelectorAll('section[aria-label^=\"Invocation \" ]')].filter(e=>e.textContent.includes('boltz2')).map(e=>e.getAttribute('aria-label').slice(11))")
            check('independent same-predictor calls have distinct identities',json.dumps(len(prediction)==2 and len(set(prediction))==2))
            edit('Mode '+prediction[1],'predict')
            checkbox('Enable invocation '+cid)
            click('Save draft');time.sleep(.5)
            call('Page.reload',ignoreCache=True);time.sleep(1);wait("!!document.querySelector('[data-native-de-novo]')");click('Sequence Design')
            check('packing invocation OFF retained after cold reopen',"!document.querySelector('[aria-label='+"+json.dumps(json.dumps('Enable invocation '+cid))+"+']').checked")
            check('packing mode retained after cold reopen',"document.querySelector('[aria-label='+"+json.dumps(json.dumps('Mode '+cid))+"+']').value==='sidechain_pack'")
            checkbox('Enable invocation '+cid)
            check('independent predictor modes retained',"document.querySelector('[aria-label='+"+json.dumps(json.dumps('Mode '+prediction[0]))+"+']').value==='complex'&&document.querySelector('[aria-label='+"+json.dumps(json.dumps('Mode '+prediction[1]))+"+']').value==='predict'")
            click('Save draft');time.sleep(.5)
            snapshot('packing-and-independent-prediction-cold')
        elif a.phase=='sources':
            seed=json.loads((a.root/'fresh.json').read_text());sid=seed['setup_context_id']
            url='http://127.0.0.1:18871/bms/submit?model=rfd4_proteina&mode=generate&project_id='+pid+'&setup_context_id='+sid
            navigate(url);click('Native sources')
            edit('Native source name','exact-returned');click('Add native source')
            click('Choose native result for exact-returned')
            snapshot('native-job-picker')
            wait("[...document.querySelectorAll('h4')].some(e=>e.checkVisibility()&&e.textContent.trim()==='job')")
            js("[...document.querySelectorAll('h4')].find(e=>e.checkVisibility()&&e.textContent.trim()==='job').closest('div.cursor-pointer').click()")
            wait("!!document.querySelector('[aria-label=\"Use native document 70e6bea4-3518-5283-a76a-d06550619962\"]')")
            js("document.querySelector('[aria-label=\"Use native document 70e6bea4-3518-5283-a76a-d06550619962\"]').click()")
            wait("!!document.querySelector('[aria-label=\"Source MODEL for exact-returned\"]')")
            snapshot('exact-document-received')
            edit('Source MODEL for exact-returned','2')
            wait("document.querySelector('[aria-label=\"Source MODEL for exact-returned\"]')?.value==='2'")
            snapshot('exact-model-two-received')
            click('Save draft');time.sleep(.5)
            call('Page.reload',ignoreCache=True);time.sleep(1);wait("!!document.querySelector('[data-native-de-novo]')");click('Native sources')
            check('cold native source slot retained',"document.body.innerText.includes('exact-returned')")
            click('Inspect retained exact-returned')
            wait("document.querySelector('[aria-label=\"Source MODEL for exact-returned\"]')?.value==='2'")
            check('cold source MODEL 2 exact document',"document.querySelector('[aria-label=\"Source MODEL for exact-returned\"]').value==='2'")
            snapshot('cold-exact-source-model-two')
        elif a.phase=='cold':
            seed=json.loads((a.root/'fresh.json').read_text());sid=seed['setup_context_id']
            url='http://127.0.0.1:18871/bms/submit?model=rfd4_proteina&mode=generate&project_id='+pid+'&setup_context_id='+sid
            navigate(url);click('Sequence Design')
            check('full process cold reopen retains five invocation identities',"document.querySelectorAll('section[aria-label^=\"Invocation \" ]').length===5")
            click('molecular_dynamics settings')
            check('full process cold reopen retains native MD draft',"document.querySelector('[aria-label=\"MD invocation name\"]').value==='INERT independent MD draft'&&document.querySelector('[aria-label=\"tpr\"]').value==='inputs/retained-unexecuted.tpr'")
            click('Back to round');click('Design');click('Native sources');click('Inspect retained exact-returned')
            wait("document.querySelector('[aria-label=\"Source MODEL for exact-returned\"]')?.value==='2'")
            check('full process cold reopen preserves exact MODEL 2',"document.querySelector('[aria-label=\"Source MODEL for exact-returned\"]').value==='2'")
            snapshot('full-process-cold-reopen')
        elif a.phase=='results':
            call('Page.navigate',url='http://127.0.0.1:18871/bms/designs/job')
            wait("document.body.innerText.includes('3 in view')")
            check('retained failed generation shows all three returned records',"document.body.innerText.includes('3 in view')&&document.body.innerText.includes('Execution: failed')")
            check('exact native generation receipt retains missing measurement',"document.body.innerText.includes('Missing')&&document.body.innerText.includes('conditioned-native')")
            wait("[...document.querySelectorAll('a')].some(e=>e.checkVisibility()&&e.textContent.trim()==='Download exact native document')")
            download=js("(async()=>{const e=[...document.querySelectorAll('a')].find(e=>e.checkVisibility()&&e.textContent.trim()==='Download exact native document');if(!e)throw Error('Missing native download');const r=await fetch(e.href);const b=await r.arrayBuffer();return {url:e.href,status:r.status,bytes:b.byteLength,sha256:[...new Uint8Array(await crypto.subtle.digest('SHA-256',b))].map(x=>x.toString(16).padStart(2,'0')).join('')}})()")
            (a.evidence/'native-download.json').write_text(json.dumps(download,indent=2))
            check('native download byte-identical to retained capture',json.dumps(download['status']==200 and download['sha256']=='a1916e81afe2cb0511e68fc3af3c39a8801a665f14fa11ae4fe176a2dfebc5d5'))
            snapshot('generation-returned-results')
            call('Page.navigate',url='http://127.0.0.1:18871/bms/designs/rfd')
            wait("document.body.innerText.includes('1 published candidates')")
            check('retained redesign preserves cancelled execution and one native candidate',"document.body.innerText.includes('Execution: cancelled')&&document.body.innerText.includes('Generated: 1')")
            snapshot('redesign-returned-results')
            call('Page.navigate',url='http://127.0.0.1:18871/bms/designs/job')
            wait("document.body.innerText.includes('3 in view')")
            wait("[...document.querySelectorAll('a')].some(e=>e.checkVisibility()&&e.textContent.trim()==='Redesign exact structure')")
            href=js("[...document.querySelectorAll('a')].find(e=>e.checkVisibility()&&e.textContent.trim()==='Redesign exact structure').href")
            (a.evidence/'continuation-href.json').write_text(json.dumps({'href':href,'expected_prefix':'/bms/submit'},indent=2))
            click('Redesign exact structure');time.sleep(1)
            snapshot('actual-continuation-target')
            check('Results continuation preserves deployed browser basename and mounts receiving editor',"location.pathname==='/bms/submit'&&document.body.innerText.includes('Back to selected result')")
        else:raise ValueError(a.phase)
    finally:
        responses=[]
        for e in list(events):
            if e.get('method')!='Network.responseReceived':continue
            v=e['params'];r=v['response']
            if '/api/' not in r['url']:continue
            try:body=call('Network.getResponseBody',requestId=v['requestId'])
            except Exception as exc:body={'unavailable':str(exc)}
            responses.append({'url':r['url'],'status':r['status'],'headers':r['headers'],'body':body})
        snapshot(a.phase+'-final')
        (a.evidence/(a.phase+'-capture.json')).write_text(json.dumps({'assertions':assertions,'events':events,'responses':responses},indent=2))
