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
            reuse = [...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Reuse Params' && !b.disabled);
            if (reuse && !reuse.disabled) break;
            await new Promise(r=>setTimeout(r,100));
        }
        const initial = document.body.innerText;
        const download = await fetch('/api/jobs/''' + job + '''/ngs-artifacts');
        const catalog = {status:download.status, body:await download.text()};
        const downloads = [];
        if (catalog.status === 200) {
            for (const artifact of JSON.parse(catalog.body).artifacts) {
                if (artifact.state !== 'present' || !artifact.url) continue;
                const response = await fetch(artifact.url);
                const bytes = await response.arrayBuffer();
                const sha256 = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
                downloads.push({artifact_id:artifact.artifact_id, filename:artifact.filename, url:artifact.url,
                    status:response.status, size_bytes:bytes.byteLength, sha256,
                    declared_size:artifact.size_bytes, declared_sha256:artifact.sha256});
            }
        }

        if (!reuse || reuse.disabled) throw Error('Native source job not ready for reuse: '+document.body.innerText);
        reuse.click(); await new Promise(r=>setTimeout(r,1500));
        [...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Show advanced controls')?.click();
        await new Promise(r=>setTimeout(r,100));
        return {initial, catalog, downloads, url:location.href, text:document.body.innerText,
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
    catalog_pass = value['catalog']['status'] == 200
    imported = json.loads((ROOT / 'imported-artifacts.json').read_text())
    imported_hashes = {a['sha256'] for a in imported['artifacts']}
    verified_downloads = []
    for item in value['downloads']:
        assert item['status'] == 200, item
        assert item['size_bytes'] == item['declared_size'], item
        assert item['sha256'] == item['declared_sha256'], item
        assert item['sha256'] in imported_hashes, item
        verified_downloads.append(item)
    (ROOT / 'verified-downloads.json').write_text(json.dumps(verified_downloads, indent=2))
    summary = {'source_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=FRONTEND, text=True).strip(),
        'routes': routes, 'netns': os.readlink('/proc/self/ns/net'), 'pids': [p.pid for p in processes],
        'synthetic_auth': True, 'science_executed': False,
        'passed': ['native scratch job list/detail/stages', 'Runs to Reuse Params with one cache', 'clone workflow advanced controls and saved assembler'],
        'download_count': len(verified_downloads),
        'blocked': [] if catalog_pass and verified_downloads else ['native clone catalog/downloads incomplete: ' + str(value['catalog']['status'])],
        'catalog': value['catalog']}
    cases_file = os.environ.get('ONT_ACCEPTANCE_RETAINED_JOBS')
    summary['retained_cases'] = []
    if cases_file:
        for case in json.loads(Path(cases_file).read_text()):
            case_id = case['job_id']
            case_probe = ROOT / f'browser-{case_id}.json'
            case_expression = """(async()=>{
                await new Promise(r=>setTimeout(r,3000));
                const catalogResponse = await fetch('/api/jobs/""" + case_id + """/ngs-artifacts');
                const catalog = {status:catalogResponse.status, body:await catalogResponse.text()};
                return {text:document.body.innerText, url:location.href, catalog,
                    links:[...document.querySelectorAll('a[href]')].map(a=>({text:a.textContent,url:a.getAttribute('href')})),
                    tables:[...document.querySelectorAll('table')].map(t=>t.innerText),
                    svg_count:document.querySelectorAll('svg').length};
            })()"""
            subprocess.run([sys.executable, str(HERE / 'ont_cdp.py'), '--alignment-job', case_id,
                '--url', f'http://127.0.0.1:18762/browser-tests/ont-suite.html?section=analyses&job_id={case_id}',
                '--wait', '3', '--expression', case_expression, '--evidence', str(case_probe)],
                check=True, stdout=(ROOT / f'browser-{case_id}.stdout').open('w'))
            result = json.loads(case_probe.read_text())['result']['result']['value']
            missing = [text for text in case.get('expected_text', []) if ''.join(text.split()) not in ''.join(result['text'].split())]
            case_result = {'job_id': case_id, 'source_output': case['source_output'], 'reused_native_output': True,
                'catalog_status': result['catalog']['status'], 'missing_expected_text': missing,
                'generic_download_links': [a for a in result['links'] if a['url'].startswith('/api/files/')],
                'status': 'passed' if result['catalog']['status'] == 200 and case.get('expected_text') and not missing else 'blocked'}
            assert not case_result['generic_download_links'], case_result
            summary['retained_cases'].append(case_result)
            (ROOT / 'retained-cases.json').write_text(json.dumps(summary['retained_cases'], indent=2))
    from ont_result_journeys import run_result_journey
    summary['result_journeys'] = [run_result_journey(HERE, ROOT, case) for case in json.loads(Path(cases_file).read_text())] if cases_file else []
    if os.environ.get('ONT_ACCEPTANCE_POOLED_SOURCE'):
        from ont_result_fixtures import POOLED_JOB
        from ont_result_journeys import run_pooled_release
        summary['pooled_release'] = run_pooled_release(HERE, ROOT)
        from ont_result_journeys import run_saved_continuations
        summary['saved_continuations'] = run_saved_continuations(HERE, ROOT, summary['pooled_release'])
        summary['retained_bam_reclassified'] = True
        summary['remote_execution_performed'] = False
        summary['result_journeys'].append(run_result_journey(HERE, ROOT, {'job_id':POOLED_JOB, 'expected_text':['Pooled assignment review', 'Target target-a']}))
    (ROOT / 'acceptance.json').write_text(json.dumps(summary, indent=2))
    assert all(not row['missing_text'] and row['igv_tracks'] and row['reopened'] for row in summary['result_journeys']), summary['result_journeys']
    print(json.dumps(summary, indent=2))
finally:
    for p in reversed(processes):
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            try: p.wait(timeout=10)
            except subprocess.TimeoutExpired: os.killpg(p.pid, signal.SIGKILL); p.wait()
    for handle in handles: handle.close()
