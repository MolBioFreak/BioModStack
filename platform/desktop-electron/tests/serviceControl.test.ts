import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildManageDesktopServicesInvocation,
  createServiceControl,
  ServiceManagerActionError,
} from '../src/serviceControl.js';

test('service control builds status invocations against the existing desktop manager cli', () => {
  const invocation = buildManageDesktopServicesInvocation('status', {
    projectRoot: '/work/biomodstack',
    runtimeMode: 'container',
    json: true,
  });

  assert.equal(invocation.command, 'python3');
  assert.equal(invocation.cwd, '/work/biomodstack');
  assert.deepEqual(invocation.args, [
    '/work/biomodstack/scripts/manage_desktop_services.py',
    'status',
    '--runtime',
    'container',
    '--json',
  ]);
});

test('service control builds explicit runtime target start invocations', () => {
  const invocation = buildManageDesktopServicesInvocation('start-target', {
    projectRoot: '/work/biomodstack',
    target: 'both',
  });

  assert.deepEqual(invocation.args, [
    '/work/biomodstack/scripts/manage_desktop_services.py',
    'start-target',
    '--target',
    'both',
  ]);
});

test('service control maps start stop restart target and status calls onto the manager cli contract', async () => {
  const calls: Array<{ command: string; args: string[]; cwd: string }> = [];
  const control = createServiceControl({
    projectRoot: '/work/biomodstack',
    run: async (invocation) => {
      calls.push(invocation);
      if (invocation.args[1] === 'status') {
        return {
          stdout: JSON.stringify({ runtime_mode: 'container', browser_url: 'http://127.0.0.1:18080/bms/' }),
          stderr: '',
          exitCode: 0,
        };
      }
      return { stdout: '', stderr: '', exitCode: 0 };
    },
  });

  const status = await control.getStatus('container');
  await control.startAll('dev');
  await control.startRuntimeTarget('both');
  await control.stopAll('container');
  await control.restartAll('container');
  await control.restartApi('dev');

  assert.equal(status.runtime_mode, 'container');
  assert.equal(status.browser_url, 'http://127.0.0.1:18080/bms/');
  assert.deepEqual(
    calls.map((call) => call.args.slice(1)),
    [
      ['status', '--runtime', 'container', '--json'],
      ['start', '--runtime', 'dev'],
      ['start-target', '--target', 'both'],
      ['stop', '--runtime', 'container'],
      ['restart', '--runtime', 'container'],
      ['restart-api', '--runtime', 'dev'],
    ],
  );
});

test('service control surfaces invalid status payloads as hard failures', async () => {
  const control = createServiceControl({
    projectRoot: '/work/biomodstack',
    run: async () => ({ stdout: 'not-json', stderr: '', exitCode: 0 }),
  });

  await assert.rejects(() => control.getStatus('container'), /Invalid service-manager status payload/);
});

test('service-control action errors preserve operator-safe structured diagnostics', async () => {
  const control = createServiceControl({
    projectRoot: '/work/biomodstack',
    run: async () => ({ stdout: 'managed unit bms-api.service is blocked', stderr: 'foreign listener on 8000', exitCode: 17 }),
  });

  await assert.rejects(
    () => control.startAll('container'),
    (error: unknown) => {
      assert.ok(error instanceof ServiceManagerActionError);
      assert.equal(error.action, 'start');
      assert.equal(error.exitCode, 17);
      assert.match(error.message, /foreign listener on 8000/);
      assert.match(error.stdout, /managed unit/);
      return true;
    },
  );
});
