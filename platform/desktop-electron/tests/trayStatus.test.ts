import assert from 'node:assert/strict';
import test from 'node:test';
import type { Tray } from 'electron';

import {
  disposeAppTray,
  registerAppTrayStatusRefresh,
  startTrayStatusRefresh,
  trayStatusFromPayload,
  trayStatusPresentation,
  trayStatusTooltip,
} from '../src/tray.js';

const readyContainer = {
  runtime_mode: 'container',
  runtime_active: true,
  runtime_ready: true,
  health: { api_ready: true, frontend_ready: true, adapter_ready: true },
};

test('tray only turns green from a complete healthy descriptor for the selected runtime', () => {
  assert.equal(trayStatusFromPayload({}, 'container'), 'unavailable');
  assert.equal(trayStatusFromPayload({ runtime_active: 'true' }, 'container'), 'unavailable');
  assert.equal(trayStatusFromPayload({ runtime_mode: 'container', runtime_active: false }, 'container'), 'offline');
  assert.equal(trayStatusFromPayload({ runtime_active: true }, 'container'), 'unavailable');
  assert.equal(trayStatusFromPayload({ ...readyContainer, runtime_mode: 'dev' }, 'container'), 'unavailable');
  assert.equal(trayStatusFromPayload({ ...readyContainer, health: { api_ready: true, frontend_ready: false, adapter_ready: true } }, 'container'), 'unavailable');
  assert.equal(trayStatusFromPayload({ ...readyContainer, health: { api_ready: true, frontend_ready: true, adapter_ready: false } }, 'container'), 'unavailable');
  assert.equal(trayStatusFromPayload(readyContainer, 'container'), 'ready');

  assert.match(trayStatusPresentation('checking').tooltip, /checking/i);
  assert.match(trayStatusPresentation('offline').tooltip, /offline/i);
  assert.match(trayStatusPresentation('unavailable').tooltip, /unavailable/i);
  assert.match(trayStatusPresentation('ready').tooltip, /ready/i);
  assert.notEqual(trayStatusPresentation('offline').color, trayStatusPresentation('ready').color);
});

test('tray requires canonical required-component readiness and exposes owner plus log diagnostics', () => {
  const payload = {
    runtime_mode: 'container',
    runtime_active: true,
    runtime_ready: false,
    components: {
      api: {
        label: 'API', required: true, ready: false, state: 'wrong-owner',
        ownership_status: 'wrong-owner', listeners: [{ owner: 'foreign-api' }],
        log_ref: '/tmp/api.log',
      },
      frontend: { label: 'Frontend', required: true, ready: true, state: 'ready', listeners: [{ owner: 'bms-web' }] },
    },
  };
  assert.equal(trayStatusFromPayload(payload, 'container'), 'unavailable');
  const tooltip = trayStatusTooltip('unavailable', payload);
  assert.match(tooltip, /API: wrong-owner; owner=foreign-api/);
  assert.match(tooltip, /log: \/tmp\/api\.log/);
  assert.match(tooltip, /Frontend: ready; owner=bms-web/);
});

test('tray refresh replaces a prior green status after a later offline probe', async () => {
  const payloads = [readyContainer, { runtime_mode: 'container', runtime_active: false }];
  const statuses: string[] = [];
  let refresh: (() => void) | undefined;
  let cancelled = false;

  const stop = startTrayStatusRefresh(
    'container',
    async () => payloads.shift() ?? {},
    (status) => statuses.push(status),
    (callback) => {
      refresh = callback;
      return 1 as unknown as ReturnType<typeof setInterval>;
    },
    () => { cancelled = true; },
  );

  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(statuses, ['ready']);
  assert.ok(refresh);
  refresh();
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(statuses, ['ready', 'offline']);
  stop();
  assert.equal(cancelled, true);
});

test('stopping refresh cancels its interval and ignores a late in-flight result', async () => {
  const statuses: string[] = [];
  let resolveProbe: ((payload: Record<string, unknown>) => void) | undefined;
  let cancelled = false;
  const stop = startTrayStatusRefresh(
    'container',
    () => new Promise<Record<string, unknown>>((resolve) => { resolveProbe = resolve; }),
    (status) => statuses.push(status),
    () => 1 as unknown as ReturnType<typeof setInterval>,
    () => { cancelled = true; },
  );
  stop();
  resolveProbe?.(readyContainer);
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.equal(cancelled, true);
  assert.deepEqual(statuses, []);
});

test('tray refresh bounds status probes to one in-flight call', async () => {
  const statuses: string[] = [];
  const resolvers: Array<(payload: Record<string, unknown>) => void> = [];
  let refresh: (() => void) | undefined;
  const stop = startTrayStatusRefresh(
    'container',
    () => new Promise<Record<string, unknown>>((resolve) => resolvers.push(resolve)),
    (status) => statuses.push(status),
    (callback) => { refresh = callback; return 1 as unknown as ReturnType<typeof setInterval>; },
    () => undefined,
  );
  assert.ok(refresh);
  refresh();
  assert.equal(resolvers.length, 1);
  resolvers[0]({ runtime_mode: 'container', runtime_active: false });
  await new Promise<void>((resolve) => setImmediate(resolve));
  assert.deepEqual(statuses, ['offline']);
  stop();
});

test('registered tray refresh ownership is disposed exactly once', () => {
  const tray = {} as Tray;
  let stops = 0;
  registerAppTrayStatusRefresh(tray, () => { stops += 1; });
  disposeAppTray(tray);
  disposeAppTray(tray);
  assert.equal(stops, 1);
});
