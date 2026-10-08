"""Supported shell CLI exercised with tiny real HTTP bytes and isolated state."""
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(tmp_path, request):
    model_id = getattr(request, "param", "esmfold2")
    class Handler(BaseHTTPRequestHandler):
        failed = False
        requests = []
        payload = b'test-only-pinned-bytes'
        def log_message(self, *args):
            pass
        def do_GET(self):
            type(self).requests.append((self.path, self.headers.get('Range')))
            body = self.payload
            offset = int(self.headers.get('Range', 'bytes=0-')[6:-1])
            self.send_response(206 if offset else 200)
            if offset:
                self.send_header('Content-Range', f'bytes {offset}-{len(body)-1}/{len(body)}')
            self.send_header('Content-Length', str(len(body)-offset))
            self.end_headers()
            self.wfile.write(body[offset:offset+5] if self.failed and self.path == '/member' else body[offset:])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    home = tmp_path / 'home'
    home.mkdir()
    env = {**os.environ, 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / 'config'),
           'XDG_STATE_HOME': str(home / 'state'), 'XDG_DATA_HOME': str(home / 'data'),
           'BMS_CONTAINER_DIR': str(tmp_path / 'images'), 'BMS_WEIGHTS': str(tmp_path / 'weights')}
    for key in list(env):
        if key.startswith('BMS_') and key not in {'BMS_CONTAINER_DIR', 'BMS_WEIGHTS'}:
            del env[key]
    authority = f'127.0.0.1:{server.server_port}'
    entries = []
    for kind, path, member in [('image', f'{model_id}.sif', None), ('weights', model_id,
                               'checkpoint/protenix-v2.pt' if model_id == 'protenix' else 'nested/model.bin')]:
        entries.append({'artifact_id': f'fixture-{kind}', 'dependency': {'kind': kind, 'relative_path': path},
                        'url': f'http://{authority}/' + ('image' if kind == 'image' else 'member'),
                        'sha256': hashlib.sha256(Handler.payload).hexdigest(), 'size_bytes': len(Handler.payload),
                        'source_authority': authority, 'approval_ref': 'TEST-ONLY-NOT-APPROVAL',
                        'license_id': 'TEST-LICENSE' if member else None, 'member_path': member})
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(json.dumps({'entries': entries, 'model_id': model_id}))
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    python = bindir / 'python3'
    python.write_text(f'#!{sys.executable}\nimport runpy, sys\nsys.argv = [{str(ROOT / "tests/provision_cli_fixture_harness.py")!r}, {str(fixture)!r}, *sys.argv[1:]]\nrunpy.run_path(sys.argv[0], run_name="__main__")\n')
    python.chmod(0o700)
    env['PATH'] = str(bindir) + os.pathsep + os.environ['PATH']
    def run(action, *args, production=False):
        selected_env = dict(env)
        if production:
            selected_env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']
        result = subprocess.run(['bash', str(ROOT / 'start_ui.sh'), action, '--json', '--model', model_id, *args],
                                env=selected_env, text=True, capture_output=True, timeout=20)
        assert result.returncode in (0, 3), result.stderr
        return result.returncode, json.loads(result.stdout)
    try:
        yield run, Handler, tmp_path, env, fixture
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        for p in tmp_path.rglob('*'):
            if p.is_dir():
                p.chmod(0o700)


def identity(run):
    code, report = run('provision-plan')
    assert code == 0, report
    return ['--expect-plan-sha256', report['plan_digest'], '--operation-id', 'fixture-operation']


def test_shell_plan_provision_resume_and_layout_binding(cli):
    run, handler, root, env, fixture = cli
    args = identity(run)
    assert not list((root / 'home').rglob('journal.json'))
    code, report = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 0, report
    assert report['status'] == 'bytes-materialized'
    assert not report['ready'] and not report['registered']
    row = report['models'][0]
    assert row['qualification'] == 'not-qualified'
    weights = next(b for b in row['bindings'] if b['dependency']['kind'] == 'weights')
    assert Path(weights['path']).is_relative_to(root / 'weights')
    assert (Path(weights['path']) / 'nested/model.bin').read_bytes() == handler.payload
    image = next(b for b in row['bindings'] if b['dependency']['kind'] == 'image')
    assert Path(image['path']).is_relative_to(root / 'images')
    journal = json.loads(Path(report['journal_path']).read_text())
    assert journal['license_acceptance']['licenses'] == ['TEST-LICENSE']
    assert journal['license_acceptance']['plan_digest'] == args[1]
    count = len(handler.requests)
    code, resumed = run('resume', *args)
    assert code == 0, resumed
    assert len(handler.requests) == count
    assert run('provision', *args)[0] == 3


def test_interrupted_transfer_durable_resume(cli):
    run, handler, root, env, fixture = cli
    args = identity(run)
    handler.failed = True
    code, report = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 3 and report['models'][0]['status'] == 'blocked'
    handler.failed = False
    code, report = run('resume', *args)
    assert code == 0, report
    assert ('/member', 'bytes=5-') in handler.requests
    assert sum(path == '/image' for path, _ in handler.requests) == 1


def test_explicit_license_and_real_default_blockers(cli):
    run, handler, root, env, fixture = cli
    args = identity(run)
    code, report = run('provision', *args)
    assert code == 3 and not handler.requests
    assert report['models'][0]['blockers'][0]['code'] == 'license_acceptance_required'
    assert run('resume', *args, '--accept-license', 'TEST-LICENSE')[0] == 3
    code, report = run('provision-plan', production=True)
    assert code == 3
    assert {b['code'] for b in report['models'][0]['blockers']} == {'approved_acquisition_metadata_missing'}
    assert not handler.requests


@pytest.mark.parametrize('change', ['metadata', 'roots', 'selection'])
def test_stale_plan_fail_closed(cli, change):
    run, handler, root, env, fixture = cli
    args = identity(run)
    if change == 'metadata':
        data = json.loads(fixture.read_text())
        data['entries'][1]['member_path'] = 'changed.bin'
        fixture.write_text(json.dumps(data))
    elif change == 'roots':
        env['BMS_WEIGHTS'] = str(root / 'other-weights')
    else:
        args += ['--model', 'unknown']
    code, report = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 3 and 'stale_plan' in str(report)
    assert not handler.requests and not list(root.rglob('journal.json'))


def test_resume_rejects_corrupt_materialized_output(cli):
    run, handler, root, env, fixture = cli
    args = identity(run)
    code, report = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 0
    member = Path(report['models'][0]['receipt']['layouts'][0]['path']) / 'nested/model.bin'
    member.chmod(0o600)
    member.write_bytes(b'corrupt')
    member.chmod(0o400)
    count = len(handler.requests)
    code, report = run('resume', *args)
    assert code == 3 and report['models'][0]['status'] == 'blocked'
    assert len(handler.requests) == count
    assert 'mismatch' in str(report)


def test_mixed_models_report_partial_success_without_readiness(cli):
    run, handler, root, env, fixture = cli
    code, plan = run('provision-plan', '--model', 'unknown')
    assert code == 3
    code, report = run('provision', '--model', 'unknown', '--operation-id', 'mixed',
                       '--expect-plan-sha256', plan['plan_digest'], '--accept-license', 'TEST-LICENSE')
    assert code == 3
    assert [r['status'] for r in report['models']] == ['bytes-materialized', 'blocked']
    assert all(r['qualification'] == 'not-qualified' for r in report['models'])
    assert not report['ready']


def test_resume_rejects_changed_authority_and_missing_operation(cli):
    run, handler, root, env, fixture = cli
    args = identity(run)
    assert 'operation_not_found' in str(run('resume', *args)[1])
    assert run('provision', *args, '--accept-license', 'TEST-LICENSE')[0] == 0
    count = len(handler.requests)
    data = json.loads(fixture.read_text())
    data['entries'][1]['approval_ref'] = 'CHANGED-TEST-ONLY'
    fixture.write_text(json.dumps(data))
    assert 'stale_plan' in str(run('resume', *args)[1])
    new_args = identity(run)
    assert 'journal_plan_mismatch' in str(run('resume', *new_args)[1])
    assert len(handler.requests) == count


def test_overlapping_stores_and_opaque_weight_binding_block(cli):
    run, handler, root, env, fixture = cli
    data = json.loads(fixture.read_text())
    data['entries'][1]['member_path'] = None
    fixture.write_text(json.dumps(data))
    code, report = run('provision-plan')
    assert code == 3 and 'weight_member_layout_required' in str(report)
    env['BMS_WEIGHTS'] = env['BMS_CONTAINER_DIR']
    assert 'overlapping_artifact_stores' in str(run('provision-plan')[1])
    assert not handler.requests


def test_configuration_lock_prevents_provisioning(cli):
    run, handler, root, env, fixture = cli
    args = identity(run)
    config = Path(env['XDG_CONFIG_HOME']) / 'biomodstack'
    config.mkdir(parents=True)
    with (config / 'configuration.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code, report = run('provision', *args, '--accept-license', 'TEST-LICENSE')
    assert code == 3 and 'configuration_busy' in str(report)
    assert not handler.requests
