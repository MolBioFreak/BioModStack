"""Own the isolated real-browser run and preserve every subprocess exit."""
import argparse,json,os,subprocess,sys,time,shutil,hashlib
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--build',type=Path,required=True);p.add_argument('--retained',type=Path,required=True);a=p.parse_args()
assert not a.root.exists();a.root.mkdir(parents=True)
here=Path(__file__).resolve().parent;repo=here.parents[2]
python=os.environ.get('BMS_QA_API_PYTHON') or sys.executable
env={**os.environ,'PYTHONPATH':f'{repo}/platform/api:{repo}:{repo}/scripts','BMS_FINAL_BROWSER_ROOT':str(a.root),'BMS_FINAL_BROWSER_BUILD':str(a.build),'BMS_FINAL_RETAINED_BASE':str(a.retained),'BMS_FINAL_REDESIGN_UNEXECUTABLE':'1','BMS_FINAL_SERVER_SCRIPT':'denovo_remaining_server.py','BMS_FINAL_PARENT_NETNS':os.readlink('/proc/self/ns/net')}
log=(a.root/'supervisor.log').open('w');commands=[]
owner=subprocess.Popen(['unshare','--user','--map-root-user','--net',python,str(here/'denovo_final_supervisor.py')],env=env,cwd=repo,stdout=log,stderr=subprocess.STDOUT)
def run(argv,name):
    with (a.root/(name+'.log')).open('w') as stream:r=subprocess.run(argv,env=env,cwd=repo,stdout=stream,stderr=subprocess.STDOUT)
    commands.append(dict(argv=argv,exit=r.returncode));(a.root/'execution.json').write_text(json.dumps(commands,indent=2));assert r.returncode==0,name
try:
    for _ in range(800):
        if (a.root/'pids.json').exists():break
        assert owner.poll() is None,'supervisor exited';time.sleep(.25)
    run([python,str(here/'denovo_final_retained.py'),'--root',str(a.root)],'import')
    for phase in ['clone','checkpoint','comparison']:
        run(['nsenter','-t',str(owner.pid),'-U','-n','--preserve-credentials',python,str(here/'denovo_remaining_journeys.py'),'--phase',phase,'--output',str(a.root/phase)],phase)
    rows=[dict(phase=phase,assertions=json.loads((a.root/phase/(phase+'-assertions.json')).read_text())) for phase in ['clone','checkpoint','comparison']]
    (a.root/'results.json').write_text(json.dumps(rows,indent=2))
finally:
    (a.root/'STOP').touch()
    try:owner.wait(timeout=30)
    except subprocess.TimeoutExpired:owner.terminate();owner.wait(timeout=20)
    (a.root/'owner-exit.json').write_text(json.dumps(dict(pid=owner.pid,exit=owner.returncode)))
    manifest={str(f.relative_to(repo)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [*here.glob('denovo_remaining*.py'),here/'denovo_final_native_server.py',here/'denovo_final_supervisor.py',repo/'platform/frontend/src/components/ResultsViewer.tsx',repo/'platform/frontend/src/components/NativeBinderGenerationResults.tsx',repo/'platform/api/routers/designs.py',repo/'platform/api/services/bindcraft2_scientific_consumer.py']}
    (a.root/'source-binding.json').write_text(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),'files':manifest,'bundle':{str(f.relative_to(a.build)):hashlib.sha256(f.read_bytes()).hexdigest() for f in a.build.rglob('*') if f.is_file()}},indent=2))
