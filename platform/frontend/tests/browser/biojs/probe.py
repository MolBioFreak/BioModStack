import asyncio, json, urllib.request, websockets

async def main():
    page = next(t for t in json.load(urllib.request.urlopen('http://127.0.0.1:19233/json')) if t['type']=='page')
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
        targets=json.load(urllib.request.urlopen('http://127.0.0.1:19233/json'))
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
            rect=await child_ev('(()=>{let r=document.querySelector("canvas").getBoundingClientRect();return {x:r.x,y:r.y}})()')
            canvases=await child_ev('document.querySelectorAll("canvas").length')
            print('canvases',canvases); assert canvases > 0
            blocked=await child_ev("fetch('http://127.0.0.1:43733/probe-outbound').then(()=>false,()=>true)",True)
            print('fetch blocked',blocked); assert blocked
            image=await child_ev("new Promise(r=>{let i=new Image(); i.onload=()=>r('loaded'); i.onerror=()=>r('blocked'); i.src='http://127.0.0.1:43733/probe-image'})",True)
            print('image',image); assert image=='blocked'
            print('child network events',[(e.get('method'),e.get('params',{}).get('blockedReason')) for e in child_events if e.get('method','').startswith('Network.')])
        await ev("window.msgs=[];window.addEventListener('message',e=>msgs.push(e.data))")
        box=await ev('(()=>{const r=document.querySelector("iframe").getBoundingClientRect();return [r.x,r.y]})()')
        found=None
        for y in (15,20,25,30,35,40,45,50,55):
            for x in (130,145,160,175):
                for typ in ('mousePressed','mouseReleased'):
                    await call('Input.dispatchMouseEvent',{'type':typ,'x':box[0]+x,'y':box[1]+y,'button':'left','clickCount':1})
                await asyncio.sleep(.05)
                found=await ev('document.querySelector("#selected").textContent')
                if found!='null': break
            if found!='null': break
        print('selected',found, 'messages',await ev('window.msgs')); assert found!='null'
        for row, col, expected in [(0, 2, {'rowId':'one','column':2,'residue':2}), (1, 1, {'rowId':'two','column':1,'residue':None})]:
            x=box[0]+rect['x']+col*15+7
            y=box[1]+rect['y']+row*15+7
            for typ in ('mousePressed','mouseReleased'):
                await call('Input.dispatchMouseEvent',{'type':typ,'x':x,'y':y,'button':'left','clickCount':1})
            await asyncio.sleep(.15)
            actual=json.loads(await ev('document.querySelector("#selected").textContent'))
            print('coordinate',row,col,actual)
            assert all(actual.get(k)==v for k,v in expected.items())
        print('parent network requests', [e['params']['request']['url'] for e in events if e.get('method')=='Network.requestWillBeSent'])
asyncio.run(main())
