import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isTailnetHostname,
  readTailnetEnvironmentStatus,
  resolveStatsToolkitEntryUrl,
  resolveConfiguredTailnetOrigin,
  resolveApiResourceUrl,
} from '../src/runtime/tailnetEnvironment';

test('Tailnet detection and Stats Toolkit entry stay same-origin on the stable hostname', () => {
  assert.equal(isTailnetHostname('compute-node.taileb3a90.ts.net'), true);
  assert.equal(isTailnetHostname('127.0.0.1'), false);
  assert.equal(
    resolveStatsToolkitEntryUrl('http://127.0.0.1:18180/stats/', 'compute-node.taileb3a90.ts.net'),
    '/stats/embed/?ui=66882da',
  );
  assert.equal(
    resolveStatsToolkitEntryUrl('http://127.0.0.1:18180/stats/', '127.0.0.1'),
    'http://127.0.0.1:18180/stats/',
  );
  assert.equal(
    resolveStatsToolkitEntryUrl('/stats/embed/', '127.0.0.1'),
    'http://127.0.0.1:18180/stats/',
  );
});

test('packaged UI routes Stats to its configured Tailnet API owner, never phone loopback', () => {
  const api = 'https://node.example.ts.net';
  for (const entry of ['/stats/embed/', 'http://127.0.0.1:18180/stats/']) {
    assert.equal(resolveStatsToolkitEntryUrl(entry, 'localhost', api), `${api}/stats/embed/?ui=66882da`);
  }
  assert.equal(resolveConfiguredTailnetOrigin(api + '/'), api);
  for (const invalid of [undefined, '', 'not a URL', 'http://127.0.0.1:8000']) {
    assert.equal(resolveConfiguredTailnetOrigin(invalid), null);
    assert.equal(resolveStatsToolkitEntryUrl('/stats/embed/', 'localhost', invalid), 'http://127.0.0.1:18180/stats/');
  }
});

test('raw server resources use the same API base as fetch without rewriting React routes or external URLs', () => {
  const base = 'https://node.example.ts.net';
  const page = 'https://localhost/__bms_ui__/active/index.html';
  for (const value of ['/api/files/a%20b.cif?download=1#atom', 'api/files/a%20b.cif?download=1#atom', 'https://localhost/api/files/a%20b.cif?download=1#atom']) {
    assert.equal(resolveApiResourceUrl(value, base + '/', page), base + '/api/files/a%20b.cif?download=1#atom');
  }
  for (const value of ['/bioxp', '/projects/a', 'assets/viewer.js', 'blob:https://localhost/123', 'https://external.example/file.cif']) {
    assert.equal(resolveApiResourceUrl(value, base, page), value);
  }
  assert.equal(resolveApiResourceUrl('/api/files/a.cif', '', 'http://localhost:18082/'), '/api/files/a.cif');
});

test('Tailnet status treats absent local control surfaces as unavailable', async () => {
  const fetcher = async () => new Response('', { status: 404 });
  assert.equal(await readTailnetEnvironmentStatus(fetcher as typeof fetch), null);
});
