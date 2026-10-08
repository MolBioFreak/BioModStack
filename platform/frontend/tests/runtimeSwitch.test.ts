import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildRuntimeSwitchTargets,
  runtimeModeLabel,
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
