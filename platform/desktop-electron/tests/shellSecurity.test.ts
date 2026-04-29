import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import {
  assertTrustedIpcSender,
  isAllowedExternalUrl,
  isAllowedShellNavigationUrl,
} from '../src/shellSecurity.js';
import type { ShellContext } from '../src/windowState.js';

const containerContext: ShellContext = {
  runtimeMode: 'container',
  frontendOrigin: 'http://127.0.0.1:18080',
  routerBasename: '/bms/',
  windowUrl: 'http://127.0.0.1:18080/bms/',
  browserUrl: 'http://127.0.0.1:18080/bms/',
};

function fakeIpcEvent(url: string) {
  return {
    senderFrame: { url },
    sender: { getURL: () => 'http://127.0.0.1:18080/bms/fallback' },
  };
}

test('shell navigation allowlist keeps container shell on the stable /bms/ origin', () => {
  assert.equal(isAllowedShellNavigationUrl('http://127.0.0.1:18080/bms/', containerContext), true);
  assert.equal(isAllowedShellNavigationUrl('http://127.0.0.1:18080/bms/designs', containerContext), true);
  assert.equal(isAllowedShellNavigationUrl('http://127.0.0.1:5173/', containerContext), false);
  assert.equal(isAllowedShellNavigationUrl('https://example.com/', containerContext), false);
  assert.equal(isAllowedShellNavigationUrl('file:///tmp/index.html', containerContext), false);
});

test('ipc sender validation rejects forged origins before privileged handlers run', () => {
  assert.doesNotThrow(() => assertTrustedIpcSender(fakeIpcEvent('http://127.0.0.1:18080/bms/') as never, containerContext));
  assert.throws(
    () => assertTrustedIpcSender(fakeIpcEvent('http://127.0.0.1:5173/') as never, containerContext),
    /Untrusted BioModStack shell IPC sender/,
  );
});

test('window-open external allowlist rejects unsafe protocols', () => {
  assert.equal(isAllowedExternalUrl('https://docs.example.test/path'), true);
  assert.equal(isAllowedExternalUrl('http://127.0.0.1:18080/bms/'), true);
  assert.equal(isAllowedExternalUrl('javascript:alert(1)'), false);
  assert.equal(isAllowedExternalUrl('file:///etc/passwd'), false);
  assert.equal(isAllowedExternalUrl('data:text/html,boom'), false);
});

test('main process wires ipc, navigation, and window-open guards', () => {
  const mainSource = fs.readFileSync(path.join(process.cwd(), 'src', 'main.ts'), 'utf8');

  assert.match(mainSource, /assertTrustedIpcSender/);
  assert.match(mainSource, /will-navigate/);
  assert.match(mainSource, /setWindowOpenHandler/);
  assert.match(mainSource, /isAllowedExternalUrl/);
});
