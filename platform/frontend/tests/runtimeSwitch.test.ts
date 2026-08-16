import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildRuntimeSwitchTargets,
  buildTailnetRuntimeSwitchTargets,
  runtimeModeLabel,
  selectTailnetRuntimeEnvironment,
  type RuntimePortSettings,
} from '../src/runtime/runtimeSwitch.js';

const ports: RuntimePortSettings = {
  dev_web_host_port: 5173,
  prod_web_host_port: 18080,
};

test('runtime switch targets preserve the app route when moving between stable /bms/ and Vite dev', () => {
  const fromStable = buildRuntimeSwitchTargets({
    ports,
    currentPathname: '/bms/designs/job-123',
    currentSearch: '?tab=structure',
    currentHash: '#metrics',
    currentRouterBasename: '/bms/',
  });
  const fromDev = buildRuntimeSwitchTargets({
    ports,
    currentPathname: '/designer/oligos',
    currentSearch: '',
    currentHash: '',
    currentRouterBasename: '/',
  });

  assert.equal(fromStable.dev.url, 'http://127.0.0.1:5173/designs/job-123?tab=structure#metrics');
  assert.equal(fromStable.stable.url, 'http://127.0.0.1:18080/bms/designs/job-123?tab=structure#metrics');
  assert.equal(fromDev.dev.url, 'http://127.0.0.1:5173/designer/oligos');
  assert.equal(fromDev.stable.url, 'http://127.0.0.1:18080/bms/designer/oligos');
});

test('runtime switch labels keep dev and stable channels explicit', () => {
  assert.equal(runtimeModeLabel('dev'), 'Vite dev web');
  assert.equal(runtimeModeLabel('container'), 'Stable /bms/ web');
});

test('tailnet runtime switch targets stay on the shared origin and preserve the app route', () => {
  const targets = buildTailnetRuntimeSwitchTargets({
    origin: 'https://compute-node.example.ts.net',
    currentPathname: '/designs/job-123',
    currentSearch: '?tab=structure',
    currentHash: '#metrics',
    currentRouterBasename: '/',
  });

  assert.equal(targets.dev.url, 'https://compute-node.example.ts.net/designs/job-123?tab=structure#metrics');
  assert.equal(targets.stable.url, 'https://compute-node.example.ts.net/bms/designs/job-123?tab=structure#metrics');
});

test('browser runtime switching selects the canonical tailnet environment before navigation', async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fakeFetch: typeof fetch = async (input, init) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify({ selected_environment: 'production' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  await selectTailnetRuntimeEnvironment('container', fakeFetch);

  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.url, '/api/tailnet-environment/select');
  assert.equal(calls[0]?.init?.method, 'POST');
  assert.deepEqual(JSON.parse(String(calls[0]?.init?.body)), { environment: 'production' });
});
