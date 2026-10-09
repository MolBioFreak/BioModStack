"""Text-first task-Chrome CDP probe. Run in the scratch server's net namespace.
No production browser, credentials, request interception or response mocks.
"""
import argparse
import json
import hashlib
from pathlib import Path
import time
from urllib.request import urlopen
from websockets.sync.client import connect

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=18763)
parser.add_argument('--url')
parser.add_argument('--alignment-job')
parser.add_argument('--expression', default='document.body.innerText')
parser.add_argument('--wait', type=float, default=2)
parser.add_argument('--evidence', type=Path, required=True)
args = parser.parse_args()
pages = json.load(urlopen(f'http://127.0.0.1:{args.port}/json'))
page = next(p for p in pages if p['type'] == 'page')
events = []
with connect(page['webSocketDebuggerUrl'], origin=None) as ws:
    serial = 0
    def call(method, **params):
        global serial
        serial += 1
        ws.send(json.dumps({'id': serial, 'method': method, 'params': params}))
        while True:
            message = json.loads(ws.recv(timeout=60))
            if message.get('id') == serial:
                if 'error' in message: raise RuntimeError(message['error'])
                return message.get('result', {})
            events.append(message)
    call('Network.enable')
    call('Network.setCacheDisabled', cacheDisabled=True)
    call('Runtime.enable')
    download_dir = args.evidence.parent / 'downloads'
    download_dir.mkdir(parents=True, exist_ok=True)
    call('Browser.setDownloadBehavior', behavior='allow', downloadPath=str(download_dir.resolve()), eventsEnabled=True)
    call('Network.setExtraHTTPHeaders', headers={'x-ont-acceptance': 'synthetic-ont-ui-only'})
    call('Network.setCookie', name='ont-acceptance', value='synthetic-ont-ui-only', url='http://127.0.0.1:18762', httpOnly=True, sameSite='Strict')
    if args.alignment_job:
        call('Network.setCookie', name='bms-ngs-' + hashlib.sha256(args.alignment_job.encode()).hexdigest()[:16], value='synthetic-ont-ui-only', url='http://127.0.0.1:18762', httpOnly=True, sameSite='Strict')
    if args.url: call('Page.navigate', url=args.url)
    time.sleep(args.wait)
    value = call('Runtime.evaluate', expression=args.expression, awaitPromise=True, returnByValue=True)
    responses = []
    for event in list(events):
        if event.get('method') != 'Network.responseReceived': continue
        params = event['params']
        if '/api/' not in params['response']['url']: continue
        try: body = call('Network.getResponseBody', requestId=params['requestId'])
        except Exception as exc: body = {'unavailable': str(exc)}
        responses.append({'requestId': params['requestId'], 'url': params['response']['url'], 'status':params['response']['status'], 'body':body})
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps({'page': page, 'result': value, 'events': events, 'responses': responses}, indent=2))
    print(json.dumps(value, indent=2))
