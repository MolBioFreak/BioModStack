"""Run from a loopback-only user/net namespace with the locked API Python.

Required: ONT_ACCEPTANCE_ROOT, ONT_ACCEPTANCE_CLONE_OUTPUT, ONT_ACCEPTANCE_TOKEN
('synthetic-ont-ui-only'). Runs real NGSToolkit + native HTTP; imports prior output,
not science. Missing governed clone artifacts are recorded as BLOCKED, not passed.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
FRONTEND = HERE.parent
ROOT = Path(os.environ['ONT_ACCEPTANCE_ROOT']).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
assert os.environ['ONT_ACCEPTANCE_TOKEN'] == 'synthetic-ont-ui-only'
routes = subprocess.check_output(['ip', 'route'], text=True).strip()
assert not routes, f'Acceptance requires a route-free namespace: {routes}'
processes = []
handles = []

def start(argv, name, env=None, pass_fds=()):
    log = (ROOT / f'{name}.log').open('w')
    handles.append(log)
    p = subprocess.Popen(argv, cwd=FRONTEND, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, pass_fds=pass_fds)
    processes.append(p)
    return p

def ready(url):
    for _ in range(100):
        try:
            with urlopen(Request(url, headers={'x-ont-acceptance': os.environ['ONT_ACCEPTANCE_TOKEN']}), timeout=1) as response:
                if response.status == 200: return
        except Exception: time.sleep(.2)
    raise RuntimeError(f'Not ready: {url}')

try:
    start([sys.executable, str(HERE / 'ont_native_server.py')], 'native')
    ready('http://127.0.0.1:18761/acceptance-health')
    env = {**os.environ, 'NODE_ENV': 'production', 'BMS_DEV_API_PROXY_TARGET': 'http://127.0.0.1:18761', 'BMS_VITE_CACHE_DIR': str(ROOT / 'vite-cache')}
    start([str(FRONTEND / 'node_modules/.bin/vite'), '--host', '127.0.0.1', '--port', '18762', '--strictPort'], 'vite', env)
    ready('http://127.0.0.1:18762/browser-tests/ont-suite.html')
    temporary = ROOT / 'chrome-tmp'
    temporary.mkdir(exist_ok=True)
    fd = os.open(temporary, os.O_RDONLY)
    start(['/usr/bin/google-chrome', '--headless=new', '--no-sandbox', '--disable-dev-shm-usage', '--disable-background-networking', '--disable-component-update', '--no-first-run', '--remote-debugging-port=18763', f'--user-data-dir={ROOT / "chrome"}', 'about:blank'], 'chrome', {**os.environ, 'TMPDIR': f'/proc/self/fd/{fd}'}, (fd,))
    os.close(fd)
    ready('http://127.0.0.1:18763/json')
    job = '3a786c08-88d9-4343-9d58-1db49bb03f9b'
    expression = '''(async()=>{
        let reuse;
        for (let attempt=0; attempt<200; attempt++) {
            reuse = [...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Reuse Params');
            if (reuse && !reuse.disabled) break;
            await new Promise(r=>setTimeout(r,100));
        }
        const initial = document.body.innerText;
        const download = await fetch('/api/jobs/''' + job + '''/ngs-artifacts');
        const catalog = {status:download.status, body:await download.text()};
        if (!reuse || reuse.disabled) throw Error('Native source job not ready for reuse');
        reuse.click(); await new Promise(r=>setTimeout(r,1500));
        [...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Show advanced controls')?.click();
        await new Promise(r=>setTimeout(r,100));
        return {initial, catalog, url:location.href, text:document.body.innerText,
            assembly:[...document.querySelectorAll('select')].find(e=>e.querySelector('option[value=flye]'))?.value,
            jobName:[...document.querySelectorAll('input')].find(e=>e.value==='Retained native Flye clone control')?.value};
    })()'''
    probe = ROOT / 'browser.json'
    subprocess.run([sys.executable, str(HERE / 'ont_cdp.py'), '--alignment-job', job, '--url', f'http://127.0.0.1:18762/browser-tests/ont-suite.html?section=analyses&job_id={job}', '--wait', '3', '--expression', expression, '--evidence', str(probe)], check=True, stdout=(ROOT / 'browser.stdout').open('w'))
    data = json.loads(probe.read_text())
    value = data['result']['result']['value']
    assert 'job_id=' not in value['url'], value
    assert value['jobName'] == 'Retained native Flye clone control' and value['assembly'] == 'flye', value
    assert 'Primers FASTA' in value['text'] and 'Regions BED file' in value['text']
    assert 'Run Inspector' in value['initial']
    summary = {'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=FRONTEND, text=True).strip(),
        'routes': routes, 'netns': os.readlink('/proc/self/ns/net'), 'pids': [p.pid for p in processes],
        'synthetic_auth': True, 'science_executed': False,
        'passed': ['native scratch job list/detail/stages', 'Runs to Reuse Params with one cache', 'clone workflow advanced controls and saved assembler'],
        'blocked': [] if value['catalog']['status'] == 200 else ['native clone artifact catalog: ' + str(value['catalog']['status'])],
        'catalog': value['catalog']}
    (ROOT / 'acceptance.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
finally:
    for p in reversed(processes):
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: os.killpg(p.pid, signal.SIGKILL); p.wait()
    for handle in handles: handle.close()
