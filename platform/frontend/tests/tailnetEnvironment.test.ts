import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isTailnetHostname,
  readTailnetEnvironmentStatus,
  resolveStatsToolkitEntryUrl,
} from '../src/runtime/tailnetEnvironment';

test('Tailnet detection and Stats Toolkit entry stay same-origin on the stable hostname', () => {
  assert.equal(isTailnetHostname('compute-node.taileb3a90.ts.net'), true);
  assert.equal(isTailnetHostname('127.0.0.1'), false);
  assert.equal(
    resolveStatsToolkitEntryUrl('http://127.0.0.1:18180/stats/', 'compute-node.taileb3a90.ts.net'),
    '/stats/embed/',
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

test('Tailnet status treats absent local control surfaces as unavailable', async () => {
  const fetcher = async () => new Response('', { status: 404 });
  assert.equal(await readTailnetEnvironmentStatus(fetcher as typeof fetch), null);
});
