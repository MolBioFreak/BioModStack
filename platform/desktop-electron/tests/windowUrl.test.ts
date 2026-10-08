import assert from 'node:assert/strict';
import test from 'node:test';

import {
  EXPOSED_BIOMODSTACK_API_KEYS,
  buildBrowserWindowOptions,
  resolveShellContext,
} from '../src/windowState.js';

test('dev shell context defaults to the root BioModStack frontend url', () => {
  const context = resolveShellContext({ runtimeMode: 'dev' });

  assert.equal(context.routerBasename, '/');
  assert.equal(context.windowUrl, 'http://127.0.0.1:5173/');
  assert.equal(context.browserUrl, 'http://127.0.0.1:5173/');
});

test('container shell context defaults to the /bms/ hosted frontend url', () => {
  const context = resolveShellContext({ runtimeMode: 'container' });

  assert.equal(context.routerBasename, '/bms/');
  assert.equal(context.windowUrl, 'http://127.0.0.1:5173/bms/');
  assert.equal(context.browserUrl, 'http://127.0.0.1:5173/bms/');
});

test('browser window options keep the renderer hardened and expose only the audited preload api', () => {
  const options = buildBrowserWindowOptions('/tmp/biomodstack-preload.js');

  assert.deepEqual(EXPOSED_BIOMODSTACK_API_KEYS, ['getShellContext', 'openInBrowser']);
  assert.equal(options.webPreferences?.preload, '/tmp/biomodstack-preload.js');
  assert.equal(options.webPreferences?.contextIsolation, true);
  assert.equal(options.webPreferences?.nodeIntegration, false);
  assert.equal(options.webPreferences?.sandbox, true);
});
