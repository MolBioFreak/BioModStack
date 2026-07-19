import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EXPOSED_BIOMODSTACK_API_KEYS,
  buildBrowserWindowOptions,
  resolveRuntimeSwitchContext,
  resolveShellContext,
} from '../src/windowState.js';
import { SHELL_STORAGE_PARTITION } from '../src/shellPaths.js';

test('dev shell context defaults to the root BioModStack frontend url', () => {
  const context = resolveShellContext({ runtimeMode: 'dev' });

  assert.equal(context.routerBasename, '/');
  assert.equal(context.windowUrl, 'http://127.0.0.1:5173/');
  assert.equal(context.browserUrl, 'http://127.0.0.1:5173/');
});

test('dev shell context honors the configured Vite dev port without changing stable /bms/', () => {
  const previousDevPort = process.env.BMS_DEV_WEB_HOST_PORT;
  const previousProdPort = process.env.BMS_WEB_HOST_PORT;
  process.env.BMS_DEV_WEB_HOST_PORT = '5179';
  process.env.BMS_WEB_HOST_PORT = '19090';
  try {
    const devContext = resolveShellContext({ runtimeMode: 'dev' });
    const stableContext = resolveShellContext({ runtimeMode: 'container' });

    assert.equal(devContext.routerBasename, '/');
    assert.equal(devContext.windowUrl, 'http://127.0.0.1:5179/');
    assert.equal(stableContext.routerBasename, '/bms/');
    assert.equal(stableContext.windowUrl, 'http://127.0.0.1:19090/bms/');
  } finally {
    if (previousDevPort === undefined) {
      delete process.env.BMS_DEV_WEB_HOST_PORT;
    } else {
      process.env.BMS_DEV_WEB_HOST_PORT = previousDevPort;
    }
    if (previousProdPort === undefined) {
      delete process.env.BMS_WEB_HOST_PORT;
    } else {
      process.env.BMS_WEB_HOST_PORT = previousProdPort;
    }
  }
});

test('container shell context defaults to the /bms/ hosted frontend url', () => {
  const context = resolveShellContext({ runtimeMode: 'container' });

  assert.equal(context.routerBasename, '/bms/');
  assert.equal(context.windowUrl, 'http://127.0.0.1:18080/bms/');
  assert.equal(context.browserUrl, 'http://127.0.0.1:18080/bms/');
});

test('runtime switch context reloads the requested channel while preserving the app path', () => {
  const stableContext = resolveShellContext({ runtimeMode: 'container' });
  const devSwitch = resolveRuntimeSwitchContext({
    currentContext: stableContext,
    currentUrl: 'http://127.0.0.1:18080/bms/designs?job=abc#metrics',
    targetRuntimeMode: 'dev',
  });
  const stableSwitch = resolveRuntimeSwitchContext({
    currentContext: devSwitch,
    currentUrl: 'http://127.0.0.1:5173/designer/oligos',
    targetRuntimeMode: 'container',
  });

  assert.equal(devSwitch.runtimeMode, 'dev');
  assert.equal(devSwitch.routerBasename, '/');
  assert.equal(devSwitch.windowUrl, 'http://127.0.0.1:5173/designs?job=abc#metrics');
  assert.equal(devSwitch.browserUrl, 'http://127.0.0.1:5173/designs?job=abc#metrics');
  assert.equal(stableSwitch.runtimeMode, 'container');
  assert.equal(stableSwitch.routerBasename, '/bms/');
  assert.equal(stableSwitch.windowUrl, 'http://127.0.0.1:18080/bms/designer/oligos');
});

test('runtime switching ignores the currently active shell origin and basename env', () => {
  const previousRuntimeMode = process.env.BMS_RUNTIME_MODE;
  const previousActiveOrigin = process.env.BMS_ACTIVE_FRONTEND_ORIGIN;
  const previousFrontendOrigin = process.env.BMS_FRONTEND_ORIGIN;
  const previousRouterBasename = process.env.BMS_ROUTER_BASENAME;
  process.env.BMS_RUNTIME_MODE = 'container';
  process.env.BMS_ACTIVE_FRONTEND_ORIGIN = 'http://127.0.0.1:18080';
  process.env.BMS_FRONTEND_ORIGIN = 'http://127.0.0.1:18080';
  process.env.BMS_ROUTER_BASENAME = '/bms/';
  try {
    const stableContext = resolveShellContext({ runtimeMode: 'container' });
    const devSwitch = resolveRuntimeSwitchContext({
      currentContext: stableContext,
      currentUrl: 'http://127.0.0.1:18080/bms/designer/oligos',
      targetRuntimeMode: 'dev',
    });

    assert.equal(devSwitch.runtimeMode, 'dev');
    assert.equal(devSwitch.frontendOrigin, 'http://127.0.0.1:5173');
    assert.equal(devSwitch.routerBasename, '/');
    assert.equal(devSwitch.windowUrl, 'http://127.0.0.1:5173/designer/oligos');
  } finally {
    if (previousRuntimeMode === undefined) delete process.env.BMS_RUNTIME_MODE;
    else process.env.BMS_RUNTIME_MODE = previousRuntimeMode;
    if (previousActiveOrigin === undefined) delete process.env.BMS_ACTIVE_FRONTEND_ORIGIN;
    else process.env.BMS_ACTIVE_FRONTEND_ORIGIN = previousActiveOrigin;
    if (previousFrontendOrigin === undefined) delete process.env.BMS_FRONTEND_ORIGIN;
    else process.env.BMS_FRONTEND_ORIGIN = previousFrontendOrigin;
    if (previousRouterBasename === undefined) delete process.env.BMS_ROUTER_BASENAME;
    else process.env.BMS_ROUTER_BASENAME = previousRouterBasename;
  }
});

test('browser window options keep the renderer hardened, persistent, and expose only the audited preload api', () => {
  const options = buildBrowserWindowOptions('/tmp/biomodstack-preload.js');

  assert.deepEqual(EXPOSED_BIOMODSTACK_API_KEYS, [
    'getShellContext',
    'getStatus',
    'startAll',
    'stopAll',
    'restartAll',
    'restartApi',
    'switchRuntime',
    'startRuntimeTarget',
    'openInBrowser',
    'getZoomFactor',
    'setZoomFactor',
    'adjustZoom',
    'resetZoom',
  ]);
  assert.equal(options.webPreferences?.preload, '/tmp/biomodstack-preload.js');
  assert.equal(options.webPreferences?.contextIsolation, true);
  assert.equal(options.webPreferences?.nodeIntegration, false);
  assert.equal(options.webPreferences?.sandbox, true);
  assert.equal(options.webPreferences?.partition, SHELL_STORAGE_PARTITION);
  assert.equal(options.webPreferences?.backgroundThrottling, false);
});
