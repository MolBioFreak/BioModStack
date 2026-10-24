"""Run one frozen production-browser roster with retained inert native outputs.

Hermes Python provides websockets; native API uses the canonical locked Python.
No installation, main lifespan, provider, execution worker or live database.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[3]
HERE=Path(__file__).parent
P=argparse.ArgumentParser();P.add_argument('--root',type=Path,required=True);P.add_argument('--evidence',type=Path,required=True)
P.add_argument('--build',type=Path,default=Path('/home/dalab/.hermes/profiles/fresh/cache/scratch/denovo-complete-parent/phase2/frontend-build-ui-final'))
a=P.parse_args()
assert a.root.resolve().is_relative_to(Path('/home/dalab/.hermes/profiles/fresh/cache/scratch'))
assert not a.root.exists(), 'Use a fresh exclusive run root; never overwrite an attempt'
a.root.mkdir(parents=True);a.evidence.mkdir(parents=True,exist_ok=True)
assert not list(a.evidence.iterdir()), 'Use fresh evidence directory'
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
base='cc1525a5bb5e054284ec17c4eafb537e7ea7104e'
ui_base='828d3412d4f8c425e30c1fb9286a1a95d3556747'
assert subprocess.check_output(['git','diff','--name-only',ui_base,base,'--','platform/frontend','schemas'],cwd=REPO,text=True)==''
index_sha=hashlib.sha256((a.build/'index.html').read_bytes()).hexdigest()
assert index_sha=='ba055ae0e77e1e6b700ff53ec2b78c02d3617abba0091b9d316aab378c02afe4'
identity={'head':head,'frozen_product':base,'reused_build_source':ui_base,'build':str(a.build),'index_sha256':index_sha,
 'scope':'Inert execution ONLY; existing native writer fixtures NOT new scientific results',
 'source_hashes':{str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob('denovo_final_*.py')},
 'bundle_hashes':{str(p.relative_to(a.build)):hashlib.sha256(p.read_bytes()).hexdigest() for p in a.build.rglob('*') if p.is_file()},
 'commands':[],'process_exits':{}}
def run(name,cmd,timeout=120):
    identity['commands'].append({'name':name,'argv':cmd,'cwd':str(REPO)})
    with (a.evidence/(name+'.log')).open('w') as log:
        result=subprocess.run(cmd,cwd=REPO,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
    identity['process_exits'][name]=result.returncode
    (a.evidence/'execution.json').write_text(json.dumps(identity,indent=2))
    print(name,result.returncode,flush=True)
    return result.returncode

env=os.environ.copy();env.update({'BMS_FINAL_BROWSER_ROOT':str(a.root),'BMS_FINAL_BROWSER_BUILD':str(a.build),
 'BMS_FINAL_PARENT_NETNS':os.readlink('/proc/self/ns/net'),'PYTHONDONTWRITEBYTECODE':'1'})
cmd=['unshare','--user','--map-root-user','--net',sys.executable,str(HERE/'denovo_final_supervisor.py')]
identity['commands'].append({'name':'supervisor','argv':cmd,'cwd':str(REPO)})
log=(a.evidence/'supervisor.log').open('w')
supervisor=subprocess.Popen(cmd,cwd=REPO,env=env,stdout=log,stderr=subprocess.STDOUT)
status=0
try:
    for _ in range(180):
        if supervisor.poll() is not None:raise RuntimeError('Supervisor failed')
        if (a.root/'pids.json').exists():break
        time.sleep(.2)
    else:raise RuntimeError('No supervisor readiness')
    ns=['nsenter','-t',str(supervisor.pid),'-U','-n','--preserve-credentials',sys.executable]
    # Existing mounted capture bytes are loaded by native HTTP preparation only;
    # subsequent Save/OFF/restore/Open actions are the production browser owner.
    prepare="""import json,hashlib
from pathlib import Path
from urllib.request import urlopen,Request
r=Path(ROOT);s=json.loads((r/'seed.json').read_text());pid=s['project_id']
p=Path(CAPTURE);b=p.read_bytes()
def post(url,body,method,key):
 return json.load(urlopen(Request('http://127.0.0.1:18871'+url,data=json.dumps(body).encode(),method=method,headers={'Content-Type':'application/json','Idempotency-Key':key})))
x=post('/api/projects/'+pid+'/workflow-setups/'+s['setup']['setup_context_id']+'/draft',{'expected_generation':0,'draft':json.loads(b)},'PUT','retained-capture-seed')
(r/'capture-seed.json').write_text(json.dumps({'source':str(p),'sha256':hashlib.sha256(b).hexdigest(),'response':x},indent=2))
f=post('/api/projects/'+pid+'/workflow-setups',{'schema':'bms.project-workflow-setup.create.v1','relationship_kind':'follow_up','global_experiment_id':s['setup']['global_experiment_id'],'domain_kind':'protein_in_silico','capability_id':'protein.native.rfd4_proteina.generate'},'POST','fresh-browser-create')
(r/'fresh.json').write_text(json.dumps(f,indent=2))
""".replace('ROOT',repr(str(a.root))).replace('CAPTURE',repr('/home/dalab/Documents/BMS/Releases/de-novo-full-20261021/resume-controlled-20261022/complete-20261023/authoring/native-envelope-rfd4_proteina-generate.json'))
    assert run('prepare',ns+['-c',prepare])==0
    assert run('retained-import',[sys.executable,str(HERE/'denovo_final_retained.py'),'--root',str(a.root)])==0
    for phase in ['authoring','fresh','sources','packing','results']:
        code=run(phase,ns+[str(HERE/'denovo_final_journey.py'),'--phase',phase,'--root',str(a.root),'--evidence',str(a.evidence/phase)])
        status=max(status,code)
    # Close both owners, retain only SQLite/artifacts, then reopen in a fresh
    # Chrome process and fresh native engine connections (no cached React tree).
    (a.root/'STOP').touch();supervisor.wait(timeout=25)
    identity['process_exits']['supervisor-before-cold']=supervisor.returncode
    (a.evidence/'process-exits-before-cold.json').write_bytes((a.root/'process-exits.json').read_bytes())
    (a.evidence/'namespace-before-cold.json').write_bytes((a.root/'namespace.json').read_bytes())
    (a.root/'STOP').unlink();(a.root/'pids.json').unlink()
    supervisor=subprocess.Popen(cmd,cwd=REPO,env=env,stdout=log,stderr=subprocess.STDOUT)
    for _ in range(180):
        if supervisor.poll() is not None:raise RuntimeError('Cold supervisor failed')
        if (a.root/'pids.json').exists():break
        time.sleep(.2)
    else:raise RuntimeError('No cold supervisor readiness')
    ns=['nsenter','-t',str(supervisor.pid),'-U','-n','--preserve-credentials',sys.executable]
    status=max(status,run('cold',ns+[str(HERE/'denovo_final_journey.py'),'--phase','cold','--root',str(a.root),'--evidence',str(a.evidence/'cold')]))
finally:
    (a.root/'STOP').touch()
    try:supervisor.wait(timeout=25)
    except subprocess.TimeoutExpired:supervisor.terminate();supervisor.wait(timeout=10)
    identity['process_exits']['supervisor']=supervisor.returncode
    identity['exit_code']=status
    # Exact native readbacks after all browser actions, never fabricated replies.
    import sqlite3
    with sqlite3.connect('file:'+str(a.root/'project.db')+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        setups=[dict(row) for row in c.execute('select * from workflow_setup_contexts')]
    (a.evidence/'sqlite-setup-readback.json').write_text(json.dumps(setups,indent=2))
    for name in ['namespace.json','root-readback.json','retained-import.json','capture-seed.json','process-exits.json','http.jsonl','http-errors.jsonl']:
        src=a.root/name
        if src.exists(): (a.evidence/name).write_bytes(src.read_bytes())
    (a.evidence/'execution.json').write_text(json.dumps(identity,indent=2))
sys.exit(status)
