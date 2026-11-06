"""Browser Save qualification. Run in isolated user/network namespace with
BMS_PARENT_NETNS set to the outer namespace. Requires API Python dependencies,
websockets.sync, Chrome and ip. Backup must contain seed.json and both SQLite
stores with an RFD4 draft; output must be a new directory under scratch.
"""
import argparse
import os,sys,json,time,sqlite3,subprocess,hashlib,shutil
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import unquote
from websockets.sync.client import connect
R = Path(__file__).resolve().parents[3]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--scratch-root', type=Path, required=True)
p.add_argument('--backup', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--build', type=Path, required=True)
p.add_argument('--chrome', default='google-chrome')
p.add_argument('--port', type=int, default=18879)
p.add_argument('--debug-port', type=int, default=18873)
p.add_argument('--initial', type=int, default=73)
p.add_argument('--values', type=int, nargs=2, default=[74,75])
p.add_argument('--max-length', type=int, default=99)
a = p.parse_args()
scratch, S, D = (x.resolve() for x in (a.scratch_root, a.backup, a.output))
assert S != scratch and S.is_relative_to(scratch) and D != scratch and D.is_relative_to(scratch)
assert S != D and not D.is_relative_to(S) and not S.is_relative_to(D)
assert (a.build / 'index.html').is_file() and (a.build / 'assets').is_dir()
D.mkdir(parents=True, exist_ok=False)
assert os.readlink('/proc/self/ns/net') != os.environ['BMS_PARENT_NETNS']
subprocess.run(['ip','link','set','lo','up'],check=True); routes=subprocess.check_output(['ip','route'],text=True); assert not routes.strip()
for name in ('core.db','project.db'):
 with sqlite3.connect('file:'+str(S/name)+'?mode=ro',uri=True) as source, sqlite3.connect(D/name) as dest: source.execute('pragma query_only=on'); source.backup(dest)
shutil.copy(S/'seed.json',D/'seed.json'); seed=json.loads((D/'seed.json').read_text()); pid=seed['project_id']; sid=seed['setup']['setup_context_id']; path=f'/api/projects/{pid}/workflow-setups/{sid}/draft'
os.environ.update(BMS_PARENT_LIFECYCLE_ROOT=str(D),BMS_PARENT_SAVE_ONLY='1',BMS_PARENT_SAVE_PATH=path,BMS_PARENT_PORT=str(a.port),BMS_SCHEMA_PYTHON=sys.executable,BMS_PARENT_SCRATCH_ROOT=str(scratch),BMS_PARENT_BROWSER_BUILD=str(a.build.resolve()))
procs=[]; events=[]; bodies={}; serial=0
(D/'namespace.json').write_text(json.dumps({
 'netns':os.readlink('/proc/self/ns/net'), 'routes':routes,
 'source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip(),
 'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
 'server_sha256':hashlib.sha256((R/'platform/frontend/browser-tests/denovo_parent_lifecycle_server.py').read_bytes()).hexdigest(),
 'build':str(a.build.resolve()), 'build_index_sha256':hashlib.sha256((a.build/'index.html').read_bytes()).hexdigest(),
 'backup':str(S), 'arguments':sys.argv[1:], 'save_path':path,
},indent=2))
def ready(url,p):
 for _ in range(160):
  if p.poll() is not None: raise RuntimeError('process exited '+str(p.returncode))
  try: return json.load(urlopen(url,timeout=1))
  except Exception: time.sleep(.25)
 raise RuntimeError('not ready '+url)
try:
 s=subprocess.Popen([sys.executable,str(R/'platform/frontend/browser-tests/denovo_parent_lifecycle_server.py')],cwd=R,stdout=(D/'server.log').open('w'),stderr=subprocess.STDOUT); procs.append(s); health=ready(f'http://127.0.0.1:{a.port}/acceptance-health',s); (D/'health.json').write_text(json.dumps(health,indent=2))
 tmp=D/'chrome-tmp'; tmp.mkdir(); fd=os.open(tmp,os.O_RDONLY); os.set_inheritable(fd,True); env=os.environ.copy(); env['TMPDIR']=f'/proc/self/fd/{fd}'
 c=subprocess.Popen([a.chrome,'--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-first-run','--disable-background-networking','--disable-component-update','--disable-sync','--disable-default-apps','--remote-debugging-address=127.0.0.1',f'--remote-debugging-port={a.debug_port}','--user-data-dir='+str(D/'chrome-profile'),'about:blank'],env=env,pass_fds=(fd,),stdout=(D/'chrome.log').open('w'),stderr=subprocess.STDOUT); procs.append(c); page=next(x for x in ready(f'http://127.0.0.1:{a.debug_port}/json',c) if x['type']=='page')
 with connect(page['webSocketDebuggerUrl'],max_size=64*1024*1024) as ws:
  def call(method,**params):
   global serial
   serial+=1; n=serial; ws.send(json.dumps({'id':n,'method':method,'params':params}))
   while True:
    m=json.loads(ws.recv(timeout=40))
    if m.get('id')==n:
     if 'error' in m: raise RuntimeError(str(m['error']))
     return m.get('result',{})
    events.append(m)
  def ev(expr): return call('Runtime.evaluate',expression=expr,returnByValue=True).get('result',{}).get('value')
  def snapshot(): return ev('({url:location.href,text:document.body.innerText,min:document.querySelector(\'[aria-label="Chain 1 min_length"]\')?.value,max:document.querySelector(\'[aria-label="Chain 1 max_length"]\')?.value,buttons:[...document.querySelectorAll("button")].map(x=>({text:x.innerText,disabled:x.disabled}))})')
  def wait(cond):
   for _ in range(160):
    time.sleep(.2); v=snapshot()
    if cond(v): return v
   raise RuntimeError('timeout '+str(v))
  def response_count(): return sum(e.get('method')=='Network.responseReceived' and unquote(e['params']['response']['url']).endswith(path) for e in events)
  def clicksave():
   pos=ev('(()=>{let b=[...document.querySelectorAll("button")].find(x=>x.innerText.trim()==="Save draft"); if(!b||b.disabled)return null; let r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()'); assert pos, 'save unavailable'; call('Input.dispatchMouseEvent',type='mousePressed',button='left',clickCount=1,**pos); call('Input.dispatchMouseEvent',type='mouseReleased',button='left',clickCount=1,**pos)
  def flush():
   for e in list(events):
    if e.get('method')=='Network.responseReceived' and (unquote(e['params']['response']['url']).endswith(path) or unquote(e['params']['response']['url']).endswith(path.removesuffix('/draft'))):
     rid=e['params']['requestId']
     if rid not in bodies:
      try: bodies[rid]=call('Network.getResponseBody',requestId=rid)
      except Exception as ex: bodies[rid]={'error':str(ex)}
  call('Network.enable'); call('Network.setCacheDisabled',cacheDisabled=True); call('Runtime.enable'); call('Page.enable')
  url=f'http://127.0.0.1:{a.port}/bms/submit?model=rfd4_proteina&mode=generate&project_id={pid}&setup_context_id={sid}'; call('Page.navigate',url=url)
  initial=wait(lambda x:x and x.get('min')==str(a.initial) and x.get('max')==str(a.max_length)); (D/'initial.json').write_text(json.dumps(initial,indent=2))
  for value in map(str, a.values):
   ev('(()=>{let e=document.querySelector(\'[aria-label="Chain 1 min_length"]\');let s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set;s.call(e,"'+value+'");e.dispatchEvent(new Event("input",{bubbles:true}));e.dispatchEvent(new Event("change",{bubbles:true}));e.blur()})()')
   time.sleep(.5); before=response_count(); clicksave()
   for _ in range(150):
    time.sleep(.2); snap=snapshot()
    if response_count()>before: break
   else: raise RuntimeError('save response missing '+str(snap))
   time.sleep(.7); snapshot(); flush(); (D/('after-'+value+'.json')).write_text(json.dumps(snap,indent=2))
  before_reload = len(events)
  call('Page.reload',ignoreCache=True); cold=wait(lambda x:x and x.get('min')==str(a.values[-1]) and x.get('max')==str(a.max_length)); (D/'cold.json').write_text(json.dumps(cold,indent=2)); time.sleep(1); snapshot(); flush()
 (D/'events.json').write_text(json.dumps(events,indent=2)); (D/'response-bodies.json').write_text(json.dumps(bodies,indent=2))
 rows=[json.loads(x) for x in (D/'http.jsonl').read_text().splitlines()]; writes=[x for x in rows if x['method'] not in ('GET','HEAD')]; external=[e['params']['request']['url'] for e in events if e.get('method')=='Network.requestWillBeSent' and not e['params']['request']['url'].startswith((f'http://127.0.0.1:{a.port}/','data:','blob:'))]
 # Verify actual HTTP acknowledgements and the GET after the reload.
 requests = {e['params']['requestId']: e['params']['request'] for e in events if e.get('method') == 'Network.requestWillBeSent'}
 def qualifying_response(e, method, endpoint):
  if e.get('method') != 'Network.responseReceived': return False
  q=e['params']; req=requests.get(q['requestId'], {})
  return req.get('method') == method and unquote(q['response']['url']).endswith(endpoint)
 def payload(e):
  q=e['params']; assert q['response']['status']==200, q['response']
  b=bodies[q['requestId']]; assert not b.get('error') and not b.get('base64Encoded'), b
  return json.loads(b['body'])
 def lengths(doc):
  chain=doc['draft']['rfd4_request']['input']['chains'][0]
  return chain['min_length'], chain['max_length']
 puts=[e for e in events if qualifying_response(e, 'PUT', path)]
 assert len(puts)==2, len(puts)
 for e,value in zip(puts,a.values):
  assert lengths(payload(e))==(value,a.max_length)
  submitted=json.loads(requests[e['params']['requestId']]['postData'])
  chain=submitted['draft']['editor_state']['rfd4_request']['input']['chains'][0]
  assert (chain['min_length'],chain['max_length'])==(value,a.max_length)
 gets=[e for e in events[before_reload:] if qualifying_response(e, 'GET', path.removesuffix('/draft'))]
 assert gets, 'cold GET response missing'
 assert lengths(payload(gets[-1]))==(a.values[-1],a.max_length)
 result={'writes':writes,'external':external,'cold_min':cold['min'],'cold_max':cold['max'],'cold_get_status':200,'cold_get_lengths':lengths(payload(gets[-1])),'put_response_lengths':[lengths(payload(e)) for e in puts],'responses':bodies}; (D/'result.json').write_text(json.dumps(result,indent=2)); assert len(writes)==2 and all(x['method']=='PUT' and x['path'].endswith(path) and x['status']==200 for x in writes) and not external
 print(json.dumps({'root':str(D),'cold':(cold['min'],cold['max']),'writes':[(x['method'],x['status']) for x in writes],'external':external}))
finally:
 (D/'events-partial.json').write_text(json.dumps(events,indent=2)); (D/'responses-partial.json').write_text(json.dumps(bodies,indent=2))
 for p in reversed(procs):
  if p.poll() is None:p.terminate()
 for p in procs:
  try:p.wait(timeout=15)
  except subprocess.TimeoutExpired:p.kill();p.wait()
 (D/'process-exits.json').write_text(json.dumps({p.pid:p.returncode for p in procs}))
