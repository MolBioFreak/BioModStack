"""Destructive-to-fixture-only acceptance harness; run INSIDE an isolated guest.

Host prepares a tracked git archive, mounts only that tar read-only, and runs:
  podman run --rm --memory=4g --cpus=2 --pids-limit=512 \
    -v /tmp/bms-frontend-source.tar:/input/source.tar:ro \
    docker.io/library/node:22.14.0-bookworm bash -c \
    'mkdir /tmp/source; tar -xf /input/source.tar -C /tmp/source; \
     cd /tmp/source; PYTHONPATH=. python3 -B platform/api/tests/frontend_prerequisites_guest.py'
No host HOME/cache/tool/socket mounts; no production builds or image cleanup.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

import biomodstack_frontend_prerequisites as frontend

source = Path.cwd()
assert str(source).startswith('/tmp/'), 'guest-local temporary source only'
os.environ['BMS_FRONTEND_ROOT'] = '/tmp/frontend-managed'
original = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob('*') if p.is_file()}
for action in ('frontend-plan', 'frontend-bootstrap', 'frontend-verify', 'frontend-bootstrap'):
    report = frontend.prerequisite_report(action, project_root=source)
    print(json.dumps(report), flush=True)
    if report['status'] == 'blocked':
        for log in Path('/tmp/frontend-managed').glob('*.log'):
            print(log.name, log.read_text()[-12000:], flush=True)
        raise SystemExit(1)
assert original == {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in original}, 'source changed'
new = [str(p.relative_to(source)) for p in source.rglob('*') if p.is_file() and str(p.relative_to(source)) not in original]
assert all('node_modules' in Path(p).parts for p in new), new[:20]
resolved = frontend.resolve_frontend_environment(source)
with open('/tmp/frontend-vite.log', 'w') as log:
    proc = subprocess.Popen([resolved['node'], resolved['vite'], '--host', '127.0.0.1', '--port', '18082', '--strictPort'], cwd=resolved['cwd'], env=resolved['env'], stdout=log, stderr=subprocess.STDOUT)
    try:
        for attempt in range(60):
            if proc.poll() is not None:
                raise RuntimeError(Path('/tmp/frontend-vite.log').read_text())
            try:
                with urllib.request.urlopen('http://127.0.0.1:18082/', timeout=3) as response:
                    assert response.status == 200
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError('Vite readiness timeout: ' + Path('/tmp/frontend-vite.log').read_text())
        with urllib.request.urlopen('http://127.0.0.1:18082/src/main.tsx', timeout=60) as response:
            assert response.status == 200
            print('VITE_MAIN_TSX_HTTP_200', len(response.read()), flush=True)
        print('GUEST_ACCEPTANCE_OK: frozen install, offline verify, idempotence, source guards, direct Vite HTTP', flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print(Path('/tmp/frontend-vite.log').read_text()[-8000:])
