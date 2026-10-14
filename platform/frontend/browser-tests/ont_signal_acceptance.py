"""Native retained-signal browser acceptance; run in route-free user/net namespace.

ONT_SIGNAL_ROOT is a fresh output directory; ONT_SIGNAL_NATIVE is the previously
qualified native-reproduction directory (raw, dna, rna). No runtime responses are
mocked; no live DB, scheduler, Docker, worker or instrument is accessed.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from websockets.sync.client import connect

HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parent
ROOT = Path(os.environ['ONT_SIGNAL_ROOT']).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
TOKEN = 'synthetic-ont-ui-only'

def ready(url):
    for _ in range(150):
        try:
            with urlopen(Request(url, headers={'x-ont-acceptance': TOKEN}), timeout=1) as r:
                if r.status == 200: return
        except Exception:
            time.sleep(.2)
    raise RuntimeError('Not ready: '+url)

class Chrome:
    def __init__(self):
        pages=json.load(urlopen('http://127.0.0.1:18763/json'))
        self.ws=connect(next(p for p in pages if p['type']=='page')['webSocketDebuggerUrl'],origin=None,max_size=64*1024*1024)
        self.serial=0; self.events=[]; self.bodies={}; self.contexts={}; self.frames={}; self.enabled_frames=set()
        for method in ['Page.enable','Runtime.enable','Network.enable','Log.enable']:
            self.call(method)
        self.call('Target.setAutoAttach',autoAttach=True,waitForDebuggerOnStart=False,flatten=True)
        self.call('Network.setCacheDisabled', cacheDisabled=True)
        self.call('Network.setExtraHTTPHeaders',headers={'x-ont-acceptance':TOKEN})
        self.call('Network.setCookie',name='ont-acceptance',value=TOKEN,url='http://127.0.0.1:18762',httpOnly=True,sameSite='Strict')
    def call(self,method,**params):
        self.serial+=1; serial=self.serial
        session=params.pop('_session',None)
        self.ws.send(json.dumps({'id':serial,'method':method,'params':params,**({'sessionId':session} if session else {})}))
        while True:
            msg=json.loads(self.ws.recv(timeout=60))
            if msg.get('id')==serial:
                if 'error' in msg: raise RuntimeError(msg['error'])
                return msg.get('result',{})
            self.events.append(msg)
            if msg.get('method')=='Runtime.executionContextCreated':
                ctx=msg['params']['context']; ctx['sessionId']=msg.get('sessionId'); self.contexts[(msg.get('sessionId'),ctx['id'])]=ctx
            elif msg.get('method')=='Runtime.executionContextDestroyed':
                self.contexts.pop((msg.get('sessionId'),msg['params']['executionContextId']),None)
            elif msg.get('method')=='Runtime.executionContextsCleared':
                self.contexts={k:v for k,v in self.contexts.items() if k[0]!=msg.get('sessionId')}
            elif msg.get('method')=='Target.attachedToTarget':self.frames[msg['params']['sessionId']]=msg['params']['targetInfo']
            elif msg.get('method')=='Target.detachedFromTarget':self.frames.pop(msg['params']['sessionId'],None)
    def evaluate(self,expr,context=None):
        result=self.call('Runtime.evaluate',expression=expr,awaitPromise=True,returnByValue=True,**({'contextId':context[1], '_session':context[0]} if context else {}))
        if 'exceptionDetails' in result:raise AssertionError(result)
        return result.get('result',{}).get('value')
    def wait(self,expr,timeout=35,context=None):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            value=self.evaluate(expr,context)
            if value:return value
            time.sleep(.2)
        raise AssertionError({'wait':expr,'dom':self.evaluate('document.body.innerText'),'contexts':self.contexts})
    def click(self,expr):
        box=self.evaluate('(()=>{const e='+expr+';if(!e)throw Error("Missing control");e.scrollIntoView({block:"center"}); const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()')
        self.call('Input.dispatchMouseEvent',type='mousePressed',button='left',clickCount=1,**box)
        self.call('Input.dispatchMouseEvent',type='mouseReleased',button='left',clickCount=1,**box)
    def button(self,text):
        self.click('[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==='+json.dumps(text)+')')
    def fill(self,selector,text):
        self.click('document.querySelector('+json.dumps(selector)+')')
        self.call('Input.dispatchKeyEvent',type='keyDown',key='a',code='KeyA',windowsVirtualKeyCode=65,modifiers=2)
        self.call('Input.dispatchKeyEvent',type='keyUp',key='a',code='KeyA',windowsVirtualKeyCode=65,modifiers=2)
        self.call('Input.dispatchKeyEvent',type='keyDown',key='Backspace',code='Backspace',windowsVirtualKeyCode=8)
        self.call('Input.dispatchKeyEvent',type='keyUp',key='Backspace',code='Backspace',windowsVirtualKeyCode=8)
        if text:self.call('Input.insertText',text=text)
    def fetch(self,path):
        return self.evaluate('(async()=>{const r=await fetch('+json.dumps(path)+');return {status:r.status,body:await r.json()};})()')
    def flush(self,name):
        urls={e['params']['requestId']:e['params']['response']['url'] for e in self.events if e.get('method')=='Network.responseReceived'}
        statuses={e['params']['requestId']:e['params']['response']['status'] for e in self.events if e.get('method')=='Network.responseReceived'}
        finished={e['params']['requestId'] for e in self.events if e.get('method')=='Network.loadingFinished'}
        for rid,url in list(urls.items()):
            if '/api/' not in url or rid not in finished or rid in self.bodies:continue
            try:
                body=self.call('Network.getResponseBody',requestId=rid)
                data=base64.b64decode(body['body']) if body['base64Encoded'] else body['body'].encode()
                path=ROOT / ('response-'+rid.replace('.','-')+'.body')
                path.write_bytes(data)
                self.bodies[rid]={'url':url,'status':statuses[rid],'file':path.name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            except Exception as e:self.bodies[rid]={'url':url,'error':str(e)}
        (ROOT/(name+'.json')).write_text(json.dumps({'events':self.events,'bodies':self.bodies,'contexts':list(self.contexts.values()),'dom':self.evaluate('document.body.innerText')},indent=2))
    def bokeh(self):
        self.wait('!!document.querySelector("iframe")')
        until=time.monotonic()+30
        while time.monotonic()<until:
            self.evaluate('1')
            for sid in list(self.frames):
                if sid not in self.enabled_frames:
                    for method in ['Runtime.enable','Network.enable','Log.enable']:self.call(method,_session=sid)
                    self.enabled_frames.add(sid)
            for cid,ctx in list(self.contexts.items()):
                if not ctx.get('auxData',{}).get('isDefault'):continue
                try:
                    value=self.evaluate('typeof Bokeh!=="undefined" && Bokeh.documents.length && {version:Bokeh.version,roots:Bokeh.documents[0].roots().length}',cid)
                    if value:return cid,value
                except Exception:pass
            time.sleep(.2)
        self.flush('bokeh-failure')
        raise AssertionError('Native iframe did not initialize Bokeh; see bokeh-failure.json')

def run():
    assert os.readlink('/proc/self/ns/net') != os.environ.get('ONT_SIGNAL_PARENT_NETNS'), 'Requires private net namespace'
    routes=subprocess.check_output(['ip','route'],text=True).strip()
    assert not routes, routes
    processes=[]; logs=[]; summary={'passed':[],'routes':routes,'netns':os.readlink('/proc/self/ns/net'),'science_executed':False,'synthetic_metadata':True}
    def start(argv,name,env=None,pass_fds=()):
        log=(ROOT/(name+'.log')).open('w');logs.append(log)
        p=subprocess.Popen(argv,cwd=FRONTEND,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=True,pass_fds=pass_fds);processes.append(p)
    try:
        start([sys.executable,str(HERE/'ont_signal_fixture.py')],'native')
        ready('http://127.0.0.1:18761/acceptance-health')
        # Build the real receiving components with the repository production
        # configuration; only the entry point and private output are overridden.
        bundle=ROOT/'bundle'
        vite_program='import {build,preview} from "vite"; const options='+json.dumps({
            'configFile':str(FRONTEND/'vite.config.ts'),
            'build':{'outDir':str(bundle),'emptyOutDir':True,'rollupOptions':{'input':str(HERE/'ont-signal-fixture.html')}},
            'preview':{'host':'127.0.0.1','port':18762,'strictPort':True,'proxy':{'/api':{'target':'http://127.0.0.1:18761'}}},
        })+'; await build(options); await preview(options);'
        start(['node','--input-type=module','-e',vite_program],'vite',
            {**os.environ,'NODE_ENV':'production','BMS_DEV_API_PROXY_TARGET':'http://127.0.0.1:18761','BMS_VITE_CACHE_DIR':str(ROOT/'vite-cache')})
        ready('http://127.0.0.1:18762/bms/browser-tests/ont-signal-fixture.html')
        temp=ROOT/'chrome-tmp';temp.mkdir(exist_ok=True);fd=os.open(temp,os.O_RDONLY)
        start(['/usr/bin/google-chrome','--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-background-networking','--disable-component-update','--no-first-run','--remote-debugging-port=18763','--window-size=1400,1200',f'--user-data-dir={ROOT/"chrome"}','about:blank'],'chrome',{**os.environ,'TMPDIR':f'/proc/self/fd/{fd}'},(fd,));os.close(fd)
        ready('http://127.0.0.1:18763/json')
        c=Chrome()
        url='http://127.0.0.1:18762/bms/browser-tests/ont-signal-fixture.html'
        c.call('Page.navigate',url=url)
        c.wait('[...document.querySelectorAll("button")].some(e=>e.textContent.trim()==="Load waveform")')
        waveform=json.loads((Path(os.environ['ONT_SIGNAL_NATIVE'])/'raw/waveform.json').read_text())
        c.click('[...document.querySelectorAll("summary")].find(e=>e.textContent==="Exact read ID recovery")')
        c.fill('input[placeholder="Exact read ID"]','')
        assert c.evaluate('[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==="Load waveform").disabled')
        c.fill('input[placeholder="Exact read ID"]',waveform['read_id'])
        c.button('Load waveform')
        c.wait('document.querySelector("svg polyline")?.getAttribute("points")')
        waveform=json.loads((Path(os.environ['ONT_SIGNAL_NATIVE'])/'raw/waveform.json').read_text())
        points=c.evaluate('document.querySelector("svg polyline").getAttribute("points")')
        samples=waveform['samples'];lo,hi=min(samples),max(samples)
        actual=[tuple(map(float,p.split(','))) for p in points.split()]
        assert len(actual)==len(samples)
        assert all(abs(x-i*600/(len(samples)-1))<1e-8 and abs(y-(118-(v-lo)*116/(hi-lo)))<1e-8 for i,((x,y),v) in enumerate(zip(actual,samples)))
        assert waveform['units']=='pA' and len(samples)==waveform['returned_sample_count']
        assert f"{waveform['sample_count']} samples · {len(samples)} shown · pA" in c.evaluate('document.body.innerText')
        summary['raw_waveform']={'read_id':waveform['read_id'],'sample_count':waveform['sample_count'],'shown':len(samples),'units':waveform['units'],'stride':waveform['stride'],'every_polyline_point_verified':True}
        summary['passed'].append('raw_native_lookup_exact_pa_polyline');c.flush('raw')
        for case in ['dna','rna','dna-comparison','rna-comparison']:
            c.click('[...document.querySelectorAll("nav a")].find(e=>e.textContent==='+json.dumps(case)+')')
            cid,bokeh=c.bokeh()
            paint=c.wait('''(()=>{function deep(root){return [...root.querySelectorAll('*')].flatMap(e=>[e,...(e.shadowRoot?deep(e.shadowRoot):[])]);}const canvases=deep(document).filter(e=>e.tagName==='CANVAS');let ink=0;for(const canvas of canvases){const ctx=canvas.getContext('2d');if(ctx){const data=ctx.getImageData(0,0,canvas.width,canvas.height).data;for(let i=0;i<data.length;i+=4)if(data[i+3]&& (data[i]<240||data[i+1]<240||data[i+2]<240))ink++;}}return ink>100&&{canvases:canvases.length,ink};})()''',context=cid)
            state=c.evaluate('(()=>{const d=Bokeh.documents[0];const all=[...d._all_models.values()];const plots=all.filter(m=>m.type==="Figure"||m.type==="Plot");return {plots:plots.map(p=>({id:p.id,x_range:p.x_range.id,start:p.x_range.start,end:p.x_range.end,renderers:p.renderers.length})),canvases:document.querySelectorAll("canvas").length,models:all.length,body:document.body.innerText};})()',cid)
            if 'comparison' in case:
                assert len(state['plots'])==2 and state['plots'][0]['x_range']==state['plots'][1]['x_range'],state
            else:assert len(state['plots'])==1,state
            assert state['models']>0
            # Trusted wheel gesture on the native Bokeh canvas, not a model edit.
            c.evaluate('document.querySelector("iframe").scrollIntoView({block:"center"})')
            canvas_box=c.evaluate('''(()=>{function deep(root){return [...root.querySelectorAll('*')].flatMap(e=>[e,...(e.shadowRoot?deep(e.shadowRoot):[])]);}const canvas=deep(document).find(e=>e.tagName==='CANVAS'&&e.width>100);const r=canvas.getBoundingClientRect();return {x:r.x+Math.min(r.width/2,200),y:r.y+Math.min(r.height/2,100)};})()''',cid)
            ranges='[...Bokeh.documents[0]._all_models.values()].filter(m=>m.type==="Figure"||m.type==="Plot").map(p=>({start:p.x_range.start,end:p.x_range.end}))'
            initial_ranges=c.evaluate(ranges,cid)
            labels='[...Bokeh.documents[0]._all_models.values()].filter(m=>m.type==="LabelSet").map(m=>({id:m.id,font:m.text_font_size}))'
            before_labels=c.evaluate(labels,cid)
            c.call('Input.dispatchMouseEvent',type='mouseWheel',deltaX=0,deltaY=-160,**canvas_box,_session=cid[0])
            changed=c.wait('JSON.stringify('+ranges+')!=='+json.dumps(json.dumps(initial_ranges,separators=(',',':')))+'&&'+ranges,context=cid)
            if 'comparison' in case:assert changed[0]==changed[1],changed
            after_labels=c.wait('JSON.stringify('+labels+')!=='+json.dumps(json.dumps(before_labels,separators=(',',':')))+'&&'+labels,context=cid)
            assert c.evaluate('document.querySelector("iframe").getAttribute("sandbox")')=='allow-scripts'
            confinement=c.evaluate('(()=>{let blocked=false;try{parent.document.body}catch(e){blocked=e.name==="SecurityError"}return {parent_access_blocked:blocked,csp:document.querySelector("meta[http-equiv] ")?.content};})()',cid)
            assert confinement['parent_access_blocked']
            assert all(value in confinement['csp'] for value in ["connect-src 'none'","frame-src 'none'","default-src 'none'"])
            summary['passed'].append(case+'_trusted_wheel_shared_axis' if 'comparison' in case else case+'_trusted_wheel')
            summary['passed'].append(case+'_native_html_bokeh')
            (ROOT/(case+'-bokeh.json')).write_text(json.dumps({'bokeh':bokeh,'state':state,'paint':paint,'before_ranges':initial_ranges,'after_ranges':changed,'before_labels':before_labels,'after_labels':after_labels,'confinement':confinement},indent=2))
            c.flush(case)
            session_path='/api/ont/signal-workbench/viewer-sessions/signal-viewer-'+case
            before=c.fetch(session_path)
            c.button('Save session')
            c.wait('[...document.querySelectorAll("button")].some(e=>e.textContent.trim()==="Save session"&&!e.disabled)')
            time.sleep(.3)
            saved=c.fetch(session_path)
            c.flush(case+'-saved')
            assert saved['status']==200 and saved['body']['revision']==before['body']['revision']+1,(before,saved)
            if 'comparison' in case:
                # Native negative receiving controls: another job's artifact and
                # divergent saved settings must still be refused without a write.
                molecule=case.split('-')[0]
                other='rna' if molecule=='dna' else 'dna'
                foreign=c.fetch('/api/ont/signal-workbench/comparisons/signal-comparison-'+molecule+'/artifacts/signal-comparison-html-'+other)
                assert foreign['status']==404,foreign
                body={key:saved['body'][key] for key in ['contig','locus_start','locus_end','selected_read_id','igv_state','signal_state']}
                body=json.loads(json.dumps(body));body['expected_revision']=saved['body']['revision']
                body['signal_state']['comparison_settings']['simulation_settings']['seed']=2
                rejected=c.evaluate('(async()=>{const r=await fetch('+json.dumps(session_path)+',{method:"PATCH",headers:{"Content-Type":"application/json"},body:'+json.dumps(json.dumps(body))+'});return {status:r.status,body:await r.json()};})()')
                assert rejected['status']==409,rejected
                assert c.fetch(session_path)['body']==saved['body']
                (ROOT/(case+'-ownership-controls.json')).write_text(json.dumps({'foreign_artifact':foreign,'divergent_settings':rejected,'unchanged_revision':saved['body']['revision']},indent=2))
                assert saved['body']['signal_state'].get('comparison_job_id')==before['body']['signal_state']['comparison_job_id'],saved
            # Repeat acknowledged save against the same native row, then cold reopen.
            c.button('Save session')
            c.wait('[...document.querySelectorAll("button")].some(e=>e.textContent.trim()==="Save session"&&!e.disabled)')
            time.sleep(.3)
            twice=c.fetch(session_path)
            assert twice['body']['revision']==saved['body']['revision']+1
            assert twice['body']['signal_state']==saved['body']['signal_state']
            c.flush(case+'-saved-twice')
            c.call('Page.reload',ignoreCache=True)
            reopened_cid,reopened=c.bokeh()
            if 'comparison' in case:
                assert c.evaluate('document.querySelector("[aria-label=\\"Simulation seed\\"]").value')=='1'
            summary['passed'].append(case+'_saved_reopened')
            if 'comparison' in case:
                controls=c.evaluate('Object.fromEntries([...document.querySelectorAll("input[aria-label],select[aria-label]")].filter(e=>e.getAttribute("aria-label").startsWith("Comparison ")||e.getAttribute("aria-label").startsWith("Simulation ")).map(e=>[e.getAttribute("aria-label"),e.type==="checkbox"?e.checked:e.value]))')
                assert controls['Simulation profile']==case.split('-')[0]+'-r9-min' and controls['Simulation seed']=='1'
                assert controls['Comparison base limit']=='100' and controls['Comparison signal sample limit']=='10000'
                (ROOT/(case+'-restored-controls.json')).write_text(json.dumps(controls,indent=2))
            c.flush(case+'-reopened')
            assert not c.evaluate('[...document.querySelectorAll("[role=alert]")].map(e=>e.innerText).filter(Boolean)'),c.evaluate('document.body.innerText')
        summary['external_attempts']=[e for e in c.events if e.get('method')=='Network.requestWillBeSent' and e['params']['request']['url'].startswith(('http:','https:')) and not e['params']['request']['url'].startswith('http://127.0.0.1:')]
        assert not summary['external_attempts']
        summary['uncaught_exceptions']=[e for e in c.events if e.get('method')=='Runtime.exceptionThrown']
        assert not summary['uncaught_exceptions']
        imported=json.loads((ROOT/'imported-native.json').read_text())
        hashes={item['sha256'] for item in imported['files']}
        html_bodies=[v for v in c.bodies.values() if '/artifacts/' in v['url'] and v.get('status')==200]
        assert len(html_bodies)>=4
        assert all(v.get('sha256') in hashes for v in html_bodies),html_bodies
        summary['verified_native_html_transfers']=html_bodies
        import sqlite3
        with sqlite3.connect(ROOT/'jobs.db') as sql:
            summary['foreign_key_check']=sql.execute('pragma foreign_key_check').fetchall()
            summary['saved_rows']=sql.execute('select id,revision from ont_signal_viewer_sessions order by id').fetchall()
        assert not summary['foreign_key_check']
        assert len(summary['saved_rows'])==5
        summary['source_sha']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=FRONTEND,text=True).strip()
        summary['acceptance_scope']='Production-built focused ReadAndSignalWorkbench, retained native bytes, native SQL/HTTP; synthetic authority metadata, no new scientific execution or admission proof'
        summary['remaining_outside_scope']=['NGSToolkit/Project navigation and populated IGV belong to the other receiving lane','RNA legacy fixture has no RG model; not admitted for a new mapping','External move-BAM discovery is natively unavailable in this namespace; no fake candidate response']
    except Exception as e:
        if 'c' in locals(): c.flush('failure')
        summary['failed']=repr(e)
        raise
    finally:
        summary['pids']=[p.pid for p in processes]
        (ROOT/'acceptance.json').write_text(json.dumps(summary,indent=2))
        for p in reversed(processes):
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
        for log in logs:log.close()

if __name__=='__main__':run()
