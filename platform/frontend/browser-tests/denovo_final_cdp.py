"""Text-first CDP action/capture; actual DOM events, no UI callback invocation."""
import argparse
import json
import time
from pathlib import Path
from urllib.request import urlopen
from websockets.sync.client import connect
p=argparse.ArgumentParser()
p.add_argument('--url');p.add_argument('--expression',default='document.body.innerText')
p.add_argument('--wait',type=float,default=2);p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
page=next(x for x in json.load(urlopen('http://127.0.0.1:18873/json')) if x['type']=='page')
events=[]
with connect(page['webSocketDebuggerUrl'],origin=None,max_size=64*1024*1024) as ws:
    serial=0
    def call(method,**params):
        global serial
        serial+=1;ws.send(json.dumps({'id':serial,'method':method,'params':params}))
        while True:
            msg=json.loads(ws.recv(timeout=45))
            if msg.get('id')==serial:
                if 'error' in msg: raise RuntimeError(msg['error'])
                return msg.get('result',{})
            events.append(msg)
    call('Network.enable');call('Network.setCacheDisabled',cacheDisabled=True);call('Runtime.enable');call('Page.enable')
    if a.url:call('Page.navigate',url=a.url)
    time.sleep(a.wait)
    result=call('Runtime.evaluate',expression=a.expression,awaitPromise=True,returnByValue=True)
    time.sleep(.4)
    dom=call('Runtime.evaluate',expression='({url:location.href,text:document.body.innerText,html:document.body.innerHTML})',returnByValue=True)
    responses=[]
    for e in list(events):
        if e.get('method')!='Network.responseReceived':continue
        v=e['params'];r=v['response']
        if '/api/' not in r['url']:continue
        try:body=call('Network.getResponseBody',requestId=v['requestId'])
        except Exception as exc:body={'unavailable':str(exc)}
        responses.append({'url':r['url'],'status':r['status'],'headers':r['headers'],'body':body})
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps({'result':result,'dom':dom,'events':events,'responses':responses},indent=2))
    print(json.dumps(result,indent=2))
