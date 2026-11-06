"""Run in unshare -Urn: real mounted parent/SQLite HTTP and read-only full browser.

Requires BMS_PARENT_LIFECYCLE_ROOT (new scratch directory),
BMS_PARENT_BROWSER_BUILD (actual Vite output), BMS_PARENT_NETNS (outer namespace).
The current interpreter supplies the repository's API/test dependencies.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from websockets.sync.client import connect

REPO = Path(__file__).resolve().parents[3]
ROOT = Path(os.environ['BMS_PARENT_LIFECYCLE_ROOT']).resolve()
ROOT.mkdir(parents=True, exist_ok=False)
assert os.readlink('/proc/self/ns/net') != os.environ['BMS_PARENT_NETNS']
subprocess.run(['ip','link','set','lo','up'],check=True)
routes = subprocess.check_output(['ip','route'],text=True)
assert not routes.strip()
os.environ['BMS_SCHEMA_PYTHON'] = sys.executable
os.environ['BMS_PARENT_PORT'] = '18879'
(ROOT/'namespace.json').write_text(json.dumps({'pid':os.getpid(),'namespace':os.readlink('/proc/self/ns/net'),'routes':routes,'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()},indent=2))
processes = []

def ready(url, owner):
    for _ in range(160):
        if owner.poll() is not None:
            raise RuntimeError(f'Owned process exited: {owner.returncode}')
        try:
            return json.load(urlopen(url,timeout=1))
        except Exception:
            time.sleep(.25)
    raise RuntimeError('No readiness: '+url)

def run(command, name, cwd=REPO):
    with (ROOT/(name+'.log')).open('w') as log:
        result = subprocess.run(command,cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
    (ROOT/(name+'-exit.json')).write_text(json.dumps({'command':command,'cwd':str(cwd),'returncode':result.returncode}))
    if result.returncode:
        raise RuntimeError(f'{name} failed: {result.returncode}')

try:
    log = (ROOT/'server.log').open('w')
    server = subprocess.Popen([sys.executable,str(Path(__file__).with_name('denovo_parent_lifecycle_server.py'))],cwd=REPO,stdout=log,stderr=subprocess.STDOUT)
    processes.append(server)
    health = ready('http://127.0.0.1:18879/acceptance-health',server)
    (ROOT/'health.json').write_text(json.dumps(health,indent=2))
    run(['./node_modules/.bin/vitest','run','--config','vitest.md.config.ts','tests/vitest/deNovoV15ParentHttpLifecycleMounted.test.tsx','tests/vitest/deNovoV15GenerationMounted.test.tsx','tests/vitest/deNovoV15SourceAuthoringMounted.test.tsx','--maxWorkers=1','--reporter=default','--reporter=json','--outputFile.json='+str(ROOT/'mounted.json')], 'mounted', REPO/'platform/frontend')
    report = json.loads((ROOT/'mounted.json').read_text())
    assert report['numFailedTests'] == 0 and report['numPendingTests'] == 0
    run([sys.executable,'-m','pytest','platform/api/tests/test_denovo_parent_mounted_persistence.py','-q','--basetemp',str(ROOT/'pytest-tmp'),'--junitxml',str(ROOT/'persistence.xml')], 'persistence')
    (ROOT/'browser-readonly').touch()
    tmp = ROOT/'chrome-tmp'; tmp.mkdir()
    fd = os.open(tmp,os.O_RDONLY); os.set_inheritable(fd,True)
    env = os.environ.copy(); env['TMPDIR'] = f'/proc/self/fd/{fd}'
    chrome_log = (ROOT/'chrome.log').open('w')
    chrome = subprocess.Popen([os.environ.get('BMS_CHROME','/usr/bin/google-chrome'),'--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-first-run','--disable-background-networking','--disable-component-update','--disable-sync','--disable-default-apps','--remote-debugging-address=127.0.0.1','--remote-debugging-port=18873','--user-data-dir='+str(ROOT/'chrome-profile'),'about:blank'],env=env,pass_fds=(fd,),stdout=chrome_log,stderr=subprocess.STDOUT)
    processes.append(chrome)
    pages = ready('http://127.0.0.1:18873/json',chrome)
    for _ in range(100):
        if any(page['type']=='page' for page in pages): break
        time.sleep(.1); pages = ready('http://127.0.0.1:18873/json',chrome)
    page = next(page for page in pages if page['type']=='page')
    events=[]; serial=0
    with connect(page['webSocketDebuggerUrl'],origin=None,max_size=64*1024*1024) as ws:
        def call(method,**params):
            global serial
            serial += 1; ws.send(json.dumps({'id':serial,'method':method,'params':params}))
            while True:
                message=json.loads(ws.recv(timeout=40))
                if message.get('id') == serial:
                    if 'error' in message: raise RuntimeError(message['error'])
                    return message.get('result',{})
                events.append(message)
        call('Network.enable'); call('Runtime.enable'); call('Page.enable')
        seed=json.loads((ROOT/'seed.json').read_text())
        url='http://127.0.0.1:18879/bms/submit?model=rfd4_proteina&mode=generate&project_id='+seed['project_id']+'&setup_context_id='+seed['setup']['setup_context_id']
        call('Page.navigate',url=url)
        dom={}
        for _ in range(160):
            time.sleep(.25)
            dom=call('Runtime.evaluate',expression='({url:location.href,text:document.body.innerText,html:document.body.innerHTML,minimum:document.querySelector(\'[aria-label="Chain 1 min_length"]\')?.value,maximum:document.querySelector(\'[aria-label="Chain 1 max_length"]\')?.value})',returnByValue=True).get('result',{}).get('value',{})
            if dom.get('minimum') == '73' and dom.get('maximum') == '99': break
        (ROOT/'browser-dom.json').write_text(json.dumps(dom,indent=2))
        image=call('Page.captureScreenshot',format='png',captureBeyondViewport=True)
        (ROOT/'browser.png').write_bytes(base64.b64decode(image['data']))
        (ROOT/'browser-events.json').write_text(json.dumps(events,indent=2))
        assert dom.get('minimum') == '73' and dom.get('maximum') == '99', 'Full shell did not restore native Project science; see browser-dom.json'
        assert 'Mounted parent no execution' in dom['text']
        external=[e['params']['request']['url'] for e in events if e.get('method')=='Network.requestWillBeSent' and not e['params']['request']['url'].startswith(('http://127.0.0.1:18879/','data:','blob:'))]
        writes=[e['params']['request'] for e in events if e.get('method')=='Network.requestWillBeSent' and e['params']['request']['method'] not in ('GET','HEAD')]
        (ROOT/'browser-readonly.json').write_text(json.dumps({'url':url,'external_attempts':external,'nonread_requests':writes,'rendered_minimum':dom['minimum'],'rendered_maximum':dom['maximum']},indent=2))
        assert not writes
    build=Path(os.environ['BMS_PARENT_BROWSER_BUILD'])
    manifest={str(path.relative_to(build)):hashlib.sha256(path.read_bytes()).hexdigest() for path in build.rglob('*') if path.is_file()}
    (ROOT/'build-sha256.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({'root':str(ROOT),'mounted_passed':report['numPassedTests'],'browser':'real full shell Project render; read-only','persistence':'passed'}),flush=True)
finally:
    for process in reversed(processes):
        if process.poll() is None: process.terminate()
    for process in processes:
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    (ROOT/'process-exits.json').write_text(json.dumps({str(process.pid):process.returncode for process in processes},indent=2))
