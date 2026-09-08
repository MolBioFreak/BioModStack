"""Real guest commands only: no substituted runtimes, registrations or approvals."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time
from typing import Any

ROOT = Path('/home/ubuntu/bms')
if not Path('/mnt/seed/source-sha').is_file() or not ROOT.is_dir():
    raise SystemExit('This helper runs only inside the task-owned acceptance guest via run_kvm.py')
OUT = Path('/home/ubuntu/evidence')
OUT.mkdir(exist_ok=True)
rows = []
env = dict(os.environ, BMS_HOME=str(ROOT), BMS_PYTHON_ROOT='/home/ubuntu/python-runtime',
           BMS_FRONTEND_ROOT='/home/ubuntu/frontend-runtime', PYTHONDONTWRITEBYTECODE='1',
           XDG_RUNTIME_DIR='/run/user/1000', DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1000/bus',
           PATH='/opt/bms-node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')


def run(label, command, timeout=120):
    started = time.time()
    try:
        p = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
        code, stdout, stderr = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        stdout = exc.stdout or ''
        stderr = exc.stderr or ''
        if isinstance(stdout, bytes): stdout = stdout.decode(errors='replace')
        if isinstance(stderr, bytes): stderr = stderr.decode(errors='replace')
        stderr += '\nAcceptance command timed out; not a pass.'
    (OUT / (label + '.stdout')).write_text(stdout)
    (OUT / (label + '.stderr')).write_text(stderr)
    row: dict[str, Any] = dict(label=label, command=command, cwd=str(ROOT), exit_code=code,
               seconds=round(time.time()-started,3), stdout=stdout, stderr=stderr)
    try: row['json'] = json.loads(stdout)
    except ValueError: pass
    rows.append(row)
    (OUT / 'commands.json').write_text(json.dumps(rows, indent=2))
    print(json.dumps(dict(label=label, exit_code=code, status=row.get('json',{}).get('status'))), flush=True)
    return row


def cli(label, action, *args, timeout=120):
    return run(label, ['./start_ui.sh', action, *args, '--json'], timeout)


def observed_files():
    # Read only tracked archive entries. node_modules are expected guest-local outputs.
    manifest = json.loads(Path('/mnt/seed/source-manifest.json').read_text())
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in manifest}


before = observed_files()
run('inventory', ['bash','-c', 'id; uname -a; systemd-detect-virt; cat /etc/os-release; cat /proc/self/cgroup; cat /proc/meminfo; command -v python3 pip3 node npm uv pnpm apptainer singularity docker; python3 --version; node --version; npm --version; systemctl --user is-system-running; dpkg-query -W python3 python3-pip python3-venv git curl'])
cli('discover-before', 'discover', '--runtime', 'dev')
cli('plan-before', 'plan', '--runtime', 'dev')
cli('python-plan', 'python-plan')
cli('python-before', 'python-verify')
bootstrap = cli('python-bootstrap', 'python-bootstrap', timeout=1100)
cli('python-verify', 'python-verify')
cli('python-idempotent', 'python-bootstrap', timeout=120)
cli('frontend-plan', 'frontend-plan')
cli('frontend-before', 'frontend-verify')
frontend = cli('frontend-bootstrap', 'frontend-bootstrap', timeout=1100)
cli('frontend-verify', 'frontend-verify')
cli('frontend-idempotent', 'frontend-bootstrap', timeout=120)
cli('discover-after', 'discover', '--runtime', 'dev')
cli('plan-after', 'plan', '--runtime', 'dev')
document = Path('/home/ubuntu/install.json')
document.write_text(json.dumps({'schema_version':'bms.install.v1',
    'profile':{'data_root':'/home/ubuntu/bms-data'},
    'ingress':{'mode':'local-only'}}))
cli('configure-preview', 'configure-preview', '--document', str(document))
configured = cli('configure', 'configure', '--document', str(document))
if configured.get('json',{}).get('operation_id'):
    for action in ('recover','resume'):
        cli(action, action, '--operation-id', configured['json']['operation_id'])
cli('provision-plan-no-selection', 'provision-plan')
cli('provision-no-selection', 'provision')
plan = cli('provision-plan-protenix', 'provision-plan', '--model', 'protenix')
cli('provision-protenix', 'provision', '--model','protenix')
cli('verify-protenix', 'verify', '--model','protenix')
start = run('managed-start', ['./start_ui.sh','start','--runtime','dev'], timeout=240)
run('managed-status', ['./start_ui.sh','status','--runtime','dev','--json'])
run('units', ['systemctl','--user','show','biomodstack-api.service','biomodstack-frontend.service',
 'biomodstack-development-workflow-adapter.service','biomodstack-telemetry.service',
 'biomodstack-mobile-update-publisher.service','biomodstack-tailnet-global.service',
 'biomodstack-core-runtime.service','biomodstack-production-workflow-adapter.service',
 '-p','Id','-p','ActiveState','-p','SubState','-p','MainPID','-p','ExecMainStatus','-p','Environment','-p','ExecStart'])
run('listeners', ['ss','-lntp'])
for label,url in [('api-health','http://127.0.0.1:18002/api/health'),
                  ('frontend-health','http://127.0.0.1:18082/bms/'),
                  ('adapter-health','http://127.0.0.1:18001/api/workflow-adapter/health')]:
    run(label, ['curl','--fail','--max-time','10','-sS',url])
run('frontend-module',['curl','--fail','--silent','--show-error','--max-time','25','http://127.0.0.1:18082/bms/src/main.tsx'])
run('managed-start-idempotent',['./start_ui.sh','start','--runtime','dev'], timeout=300)
run('units-after-repeat',['systemctl','--user','show','biomodstack-api.service','biomodstack-frontend.service',
 'biomodstack-development-workflow-adapter.service','biomodstack-telemetry.service',
 '-p','Id','-p','ActiveState','-p','SubState','-p','MainPID','-p','ExecMainStatus'])
run('journals', ['journalctl','--user','-b','--no-pager','-n','200'])
for path in Path('/home/ubuntu/.local/state/biomodstack/logs').glob('*.log'):
    (OUT/('service-'+path.name)).write_bytes(path.read_bytes()[-100000:])
run('managed-stop', ['./start_ui.sh','stop','--runtime','dev'])
run('units-after-stop', ['systemctl','--user','is-active','biomodstack-api.service','biomodstack-frontend.service','biomodstack-development-workflow-adapter.service'])
after = observed_files()
required = {'python-bootstrap','python-verify','python-idempotent','frontend-bootstrap',
            'frontend-verify','frontend-idempotent','configure-preview','configure','recover','resume',
            'managed-start','api-health','frontend-health','adapter-health','managed-stop'}
required.update({'frontend-module','managed-start-idempotent','units-after-repeat'})
by_label = {r['label']: r for r in rows}
required_passed = all(label in by_label and by_label[label]['exit_code']==0 for label in required)
unit_states = {}
for block in by_label['units']['stdout'].split('\n\n'):
    fields = dict(line.split('=',1) for line in block.splitlines() if '=' in line)
    if 'Id' in fields: unit_states[fields['Id']] = {k:fields.get(k) for k in ('ActiveState','SubState','MainPID','ExecMainStatus')}
core_units = ['biomodstack-api.service','biomodstack-frontend.service',
              'biomodstack-development-workflow-adapter.service','biomodstack-telemetry.service']
units_passed = all(unit_states.get(unit,{}).get('ActiveState')=='active' and
                   int(unit_states.get(unit,{}).get('MainPID') or 0)>0 for unit in core_units)
repeat_units = {}
for block in by_label['units-after-repeat']['stdout'].split('\n\n'):
    fields = dict(line.split('=',1) for line in block.splitlines() if '=' in line)
    if 'Id' in fields: repeat_units[fields['Id']] = fields
repeat_preserved_pids = all(repeat_units.get(unit,{}).get('ActiveState')=='active' and
                           repeat_units.get(unit,{}).get('MainPID')==unit_states.get(unit,{}).get('MainPID')
                           for unit in core_units)
summary = {'tracked_source_unchanged':before == after,
           'source_sha':Path('/mnt/seed/source-sha').read_text().strip(),
           'rows':[{k:r[k] for k in ('label','exit_code')} for r in rows],
           'units':unit_states, 'required_commands_passed':required_passed,
           'idempotent_start_preserved_pids':repeat_preserved_pids,
           'production_activated':any(unit_states.get(unit,{}).get('ActiveState')=='active' for unit in ('biomodstack-core-runtime.service','biomodstack-production-workflow-adapter.service')),
           'scientific_approval_created':False,
           'no_license_acceptance_supplied':True,
           'scope':'real rootless KVM guest, no host HOME/tools/caches/SIF/socket mounts',
           'nonproduction_managed_start_passed':required_passed and units_passed and repeat_preserved_pids and before==after}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
for base in (Path(env['BMS_PYTHON_ROOT']),Path(env['BMS_FRONTEND_ROOT'])):
    if base.exists():
        for path in list(base.glob('*.log')) + list(base.glob('*.json')):
            (OUT/(base.name+'-'+path.name)).write_bytes(path.read_bytes())
print('BMS_ACCEPTANCE_SUMMARY='+json.dumps(summary),flush=True)
archive = Path('/home/ubuntu/evidence.tar.gz')
with tarfile.open(archive,'w:gz') as tf: tf.add(OUT, arcname='evidence')
raw = archive.read_bytes()
encoded = base64.b64encode(raw).decode()
chunks = [encoded[i:i+1000] for i in range(0, len(encoded), 1000)]
print('BMS_EVIDENCE_BEGIN='+json.dumps({'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'chunks':len(chunks)}), flush=True)
for index, chunk in enumerate(chunks):
    print(f'BMS_EVIDENCE_CHUNK={index}:{chunk}', flush=True)
print('BMS_EVIDENCE_END='+hashlib.sha256(raw).hexdigest(), flush=True)
