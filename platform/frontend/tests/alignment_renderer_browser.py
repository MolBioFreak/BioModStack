"""Real pinned viewer state/mark smoke test. Build viewer bundles first.
Requires Chrome, Python websockets and TMPDIR; writes only private scratch artifacts.
This checks native rendering/state, not an authenticated PM export/import journey.
"""
import asyncio, json, subprocess, urllib.request, pathlib, websockets, os, tempfile, shutil
from typing import Any
root = pathlib.Path(__file__).resolve().parents[1]
out = pathlib.Path(tempfile.mkdtemp(prefix='msa-viewer-state-', dir=os.environ['TMPDIR']))
frames = {}
rows = [{'rowId':f'r{i}', 'label':'duplicate label', 'aligned': ('ACDEFGHIKLMNPQRSTVWY'*40)+str('A' if i % 2 else 'C')} for i in range(60)]
for name in ['jbrowse','av2','biojs']:
    path = root/'alignment-viewers'/name
    code = (path/('dist/vendor/msa.js' if name=='biojs' else 'dist/viewer.js')).read_text()
    if name=='biojs': code += '\n'+(path/'src.js').read_text()
    payload = {'rows':rows, 'token':name, 'state':{}, 'marks':[]}
    code = "window.addEventListener('error', e=>parent.postMessage({error:e.message, viewer:"+json.dumps(name)+"},'*'));\n"+code
    frames[name] = '<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'nonce-probe\' '+("\'unsafe-eval\'" if name=='biojs' else '')+'; style-src \'unsafe-inline\'; img-src data: blob:; connect-src \'none\'; worker-src \'none\'"><div id="root"></div><div id="viewer"></div><script id="payload" type="application/json">'+json.dumps(payload)+'</script><script nonce="probe">'+code.replace('</script','<\\/script')+'</script>'
    if name == 'av2': frames[name] = frames[name].replace("worker-src 'none'", "worker-src blob:")
html = '<!doctype html><script>window.messages=[];addEventListener("message",e=>messages.push(e.data));window.framesData='+json.dumps(frames).replace('</script','<\\/script')+';function mount(n){document.querySelectorAll("iframe").forEach(x=>x.remove());let f=document.createElement("iframe");f.id=n;f.sandbox="allow-scripts";f.style="width:1100px;height:650px";f.srcdoc=framesData[n];document.body.append(f)};function control(n,state,marks,selection){document.querySelector("iframe").contentWindow.postMessage({channel:"bms-"+n+"-inline-v1",token:n,type:"control",state,marks,selection},"*")}</script><body></body>'
(out/'probe.html').write_text(html)
(out/'chrome/DevToolsActivePort').unlink(missing_ok=True)
chrome = shutil.which('google-chrome') or shutil.which('chromium')
assert chrome, 'Chrome or Chromium is required'
proc = subprocess.Popen([chrome,'--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-site-isolation-trials','--disable-features=IsolateOrigins,site-per-process','--enable-unsafe-swiftshader','--remote-debugging-port=0','--user-data-dir='+str(out/'chrome'),'about:blank'],stdout=(out/'chrome.log').open('w'),stderr=subprocess.STDOUT)
async def main():
    portfile=out/'chrome/DevToolsActivePort'
    for _ in range(100):
        portfile=out/'chrome/DevToolsActivePort'
        if portfile.exists(): break
        await asyncio.sleep(.1)
    port = portfile.read_text().splitlines()[0]
    page = next(t for t in json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json')) if t['type']=='page')
    async with websockets.connect(page['webSocketDebuggerUrl'],max_size=30000000) as ws:
        seq=0; contexts={}; errors=[]
        async def call(method,params={}):
            nonlocal seq
            seq+=1; ident=seq
            await ws.send(json.dumps({'id':ident,'method':method,'params':params}))
            while True:
                data=json.loads(await ws.recv())
                if data.get('method')=='Runtime.executionContextCreated':
                    c=data['params']['context']; contexts[c['id']]=c
                if data.get('method')=='Runtime.exceptionThrown': errors.append(data['params']['exceptionDetails'])
                if data.get('id')==ident: return data.get('result',data)
        async def js(expr,context=None) -> Any:
            p={'expression':expr,'returnByValue':True,'awaitPromise':True}
            if context:p['contextId']=context
            result=await call('Runtime.evaluate',p)
            if 'exceptionDetails' in result: return {'exception':result['exceptionDetails']}
            return result.get('result',{}).get('value')
        await call('Runtime.enable'); await call('Page.enable')
        await call('Page.navigate',{'url':(out/'probe.html').as_uri()})
        await asyncio.sleep(.5)
        evidence={}
        hashjs="JSON.stringify([...document.querySelectorAll('canvas')].map(c=>{try{let d=c.getContext('2d')?.getImageData(0,0,c.width,c.height).data;if(!d){let s=c.toDataURL();let h=0;for(let i=0;i<s.length;i++)h=(Math.imul(h,31)+s.charCodeAt(i))|0;return [c.width,c.height,h]}let h=0;for(let i=0;i<d.length;i++)h=(Math.imul(h,31)+d[i])|0;return [c.width,c.height,h]}catch(e){return String(e)}}))"
        for name in frames:
            await js('messages=[];mount('+json.dumps(name)+')')
            for _ in range(80):
                await asyncio.sleep(.15)
                msgs=await js('messages')
                if any(x.get('type')=='ready' for x in msgs): break
                if any(x.get('error') for x in msgs): break
            # AV2 also creates a font-measurement document; choose the payload frame.
            candidates=[i for i,c in contexts.items() if c.get('auxData',{}).get('isDefault')]
            ctx = None
            for candidate in reversed(candidates):
                if await js("!!document.querySelector('#payload')", candidate):
                    ctx = candidate; break
            assert ctx is not None, 'No viewer frame context'
            before=await js(hashjs,ctx)
            state= {'scrollX': -180, 'scrollY': -30, 'columnWidth':15, 'rowHeight':20, 'colorScheme':'clustal', 'hideGaps':True, 'allowedGappyness':90} if name=='jbrowse' else ({'scrollX':180,'scrollY':30,'columnWidth':18,'rowHeight':18,'colorScheme':'zappo','hiddenColumns':[1,3]} if name=='biojs' else {'scrollX':180,'scrollY':30,'zoomLevel':15,'colorScheme':'Taylor'})
            await js('control('+json.dumps(name)+','+json.dumps(state)+',[{id:"m",label:"mark",start:20,end:25}],{index:2,column:22})')
            await asyncio.sleep(1)
            after=await js(hashjs,ctx)
            msgs=await js('messages')
            evidence[name]={'dom':await js('document.body.innerText.slice(0,2000)',ctx), 'tags':await js('JSON.stringify([...new Set([...document.querySelectorAll("*")].map(x=>x.tagName))])',ctx),'messages':msgs[-12:],'canvasBefore':before,'canvasAfter':after,'errors':[x for x in msgs if 'error' in x]}
            # Clear marks/selection at the same viewport: pixel difference proves native overlay effect.
            actual=next((x['state'] for x in reversed(msgs) if x.get('type')=='state'),state)
            await js('control('+json.dumps(name)+','+json.dumps(actual)+',[],null)')
            await asyncio.sleep(.4)
            evidence[name]['canvasCleared']=await js(hashjs,ctx)
            if name=='biojs': evidence[name]['native']=await js('JSON.stringify({state:snapshot(),selection:viewer.g.selcol.toJSON()})',ctx)
            assert not evidence[name]['errors'], evidence[name]['errors']
            assert after != '[]' and evidence[name]['canvasCleared'] != after, f'{name}: marks did not change native pixels'
            for key, value in state.items(): assert actual.get(key) == value, (name, key, actual)
            await js('messages=[];framesData['+json.dumps(name)+']=framesData['+json.dumps(name)+'].replace(\'"state": {}\',\'"state": \'+JSON.stringify('+json.dumps(actual)+'));mount('+json.dumps(name)+')')
            for _ in range(80):
                await asyncio.sleep(.15)
                reopened=await js('messages')
                if any(x.get('type')=='state' for x in reopened): break
            await asyncio.sleep(.4)
            reopened=await js('messages')
            restored=next((x['state'] for x in reversed(reopened) if x.get('type')=='state'),{})
            evidence[name]['remountedState']=restored
            assert restored == actual, (name, 'remount', restored, actual)
        evidence['runtimeErrors']=errors
        (out/'evidence.json').write_text(json.dumps(evidence,indent=2))
        assert not errors, errors
        print(json.dumps({'viewers': list(frames), 'native_state_remount': 'passed', 'native_mark_pixels': 'passed', 'runtime_errors': len(errors), 'evidence': str(out/'evidence.json')}))
try: asyncio.run(main())
finally: proc.terminate(); proc.wait(timeout=20)
