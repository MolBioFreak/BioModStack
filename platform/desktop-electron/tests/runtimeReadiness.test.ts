import assert from 'node:assert/strict';
import test from 'node:test';

import { ensureRuntimeReady } from '../src/runtimeReadiness.js';

const readyContainer = {
  runtime_mode: 'container',
  runtime_active: true,
  health: { adapter_ready: true, api_ready: true, frontend_ready: true },
};

test('ensureRuntimeReady starts the requested channel and waits through wrong/incomplete status until that channel is ready', async () => {
  const calls: string[] = [];
  const statuses = [
    { runtime_mode: 'dev', runtime_active: true, health: { api_ready: true, frontend_ready: true } },
    { runtime_mode: 'container', runtime_active: true, health: { api_ready: true, frontend_ready: false } },
    readyContainer,
  ];
  let now = 0;

  await ensureRuntimeReady('container', {
    startAll: async (runtimeMode) => { calls.push(`start:${runtimeMode}`); },
    getStatus: async (runtimeMode) => {
      calls.push(`status:${runtimeMode}`);
      return statuses.shift() ?? {};
    },
  }, {
    timeoutMs: 100,
    initialDelayMs: 1,
    maxDelayMs: 4,
    now: () => now,
    sleep: async (delayMs) => { calls.push(`sleep:${delayMs}`); now += delayMs; },
  });

  assert.deepEqual(calls, [
    'start:container',
    'status:container',
    'sleep:1',
    'status:container',
    'sleep:2',
    'status:container',
  ]);
});

test('ensureRuntimeReady rejects after the readiness deadline and never treats a stale channel as ready', async () => {
  let now = 0;
  await assert.rejects(
    ensureRuntimeReady('container', {
      startAll: async () => undefined,
      getStatus: async () => ({ runtime_mode: 'dev', runtime_active: true, health: { api_ready: true, frontend_ready: true } }),
    }, {
      timeoutMs: 3,
      initialDelayMs: 2,
      maxDelayMs: 2,
      now: () => now,
      sleep: async (delayMs) => { now += delayMs; },
    }),
    /container.*ready/i,
  );
});

test('container readiness requires the workflow adapter but native dev readiness does not', async () => {
  let now = 0;
  await assert.rejects(
    ensureRuntimeReady('container', {
      startAll: async () => undefined,
      getStatus: async () => ({
        runtime_mode: 'container',
        runtime_active: true,
        health: { adapter_ready: false, api_ready: true, frontend_ready: true },
      }),
    }, {
      timeoutMs: 3,
      initialDelayMs: 2,
      maxDelayMs: 2,
      now: () => now,
      sleep: async (delayMs) => { now += delayMs; },
    }),
    /container.*ready/i,
  );

  await ensureRuntimeReady('dev', {
    startAll: async () => undefined,
    getStatus: async () => ({
      runtime_mode: 'dev',
      runtime_active: true,
      health: { api_ready: true, frontend_ready: true },
    }),
  });
});

test('ensureRuntimeReady rejects a ready status that arrives after its deadline', async () => {
  let now = 0;
  await assert.rejects(
    ensureRuntimeReady('container', {
      startAll: async () => undefined,
      getStatus: async () => {
        now = 4;
        return readyContainer;
      },
    }, {
      timeoutMs: 3,
      now: () => now,
    }),
    /container.*ready/i,
  );
});
