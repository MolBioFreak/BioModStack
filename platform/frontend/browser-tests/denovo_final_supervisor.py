"""Owns isolated server + Chrome, bounded 30-minute diagnostic lease."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[3]
ROOT = Path(os.environ['BMS_FINAL_BROWSER_ROOT'])
ROOT.mkdir(parents=True, exist_ok=True)
assert os.readlink('/proc/self/ns/net') != os.environ['BMS_FINAL_PARENT_NETNS']
subprocess.run(['ip','link','set','lo','up'],check=True)
routes = subprocess.check_output(['ip','route'],text=True)
assert not routes.strip()
(ROOT/'namespace.json').write_text(json.dumps({'pid':os.getpid(),'netns':os.readlink('/proc/self/ns/net'),'routes':routes,'argv':sys.argv},indent=2))
python = '/home/dalab/biomodstack/dev-test-canonical/platform/api/.venv/bin/python'
attempt = str(time.time_ns())
server_log = (ROOT/('server-'+attempt+'.log')).open('w')
chrome_log = (ROOT/('chrome-'+attempt+'.log')).open('w')
processes=[]
try:
    processes.append(subprocess.Popen([python,str(Path(__file__).with_name('denovo_final_native_server.py'))], cwd=REPO/'platform/api',stdout=server_log,stderr=subprocess.STDOUT))
    for _ in range(120):
        if processes[0].poll() is not None: raise RuntimeError('Server exited '+str(processes[0].returncode))
        try:
            health=json.load(urlopen('http://127.0.0.1:18871/acceptance-health',timeout=1)); break
        except Exception: time.sleep(.25)
    else: raise RuntimeError('No health')
    tmp=ROOT/'chrome-tmp';tmp.mkdir(exist_ok=True)
    fd=os.open(tmp,os.O_RDONLY);os.set_inheritable(fd,True)
    env=os.environ.copy();env['TMPDIR']=f'/proc/self/fd/{fd}'
    processes.append(subprocess.Popen(['/usr/bin/google-chrome','--headless=new','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--no-first-run','--disable-background-networking','--disable-component-update','--disable-sync','--disable-default-apps','--remote-debugging-address=127.0.0.1','--remote-debugging-port=18873','--user-data-dir='+str(ROOT/'chrome-profile'),'about:blank'],env=env,pass_fds=(fd,),stdout=chrome_log,stderr=subprocess.STDOUT))
    (ROOT/'pids.json').write_text(json.dumps({'supervisor':os.getpid(),'server':processes[0].pid,'chrome':processes[1].pid,'health':health},indent=2))
    print('READY',os.getpid(),flush=True)
    until=time.time()+1800
    while time.time()<until and not (ROOT/'STOP').exists():
        assert all(p.poll() is None for p in processes),'Owned child exited'
        time.sleep(1)
finally:
    for p in reversed(processes):
        if p.poll() is None: p.terminate()
    for p in processes:
        try:p.wait(timeout=15)
        except subprocess.TimeoutExpired:p.kill();p.wait()
    (ROOT/'process-exits.json').write_text(json.dumps({str(p.pid):p.returncode for p in processes},indent=2))
