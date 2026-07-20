import assert from 'node:assert/strict';
import test from 'node:test';

import { deriveBioXpStatus, isBioXpCommandAvailable } from '../src/components/bioxpInterlinkStatus.js';

const base = {
    configured: true,
    active: true,
    generation: 2,
    target_url: 'http://ro***:8123',
    reachable: true,
    runtime_ready: true,
    hardware_ready: true,
    capabilities: [],
    observed_at: '2026-07-18T00:00:00Z',
    freshness_budget_seconds: 30,
    fresh: true,
    last_error: null,
    command_active: false,
};

test('ready requires active, fresh, reachable, runtime-ready and hardware-ready', () => {
    const observed = Date.parse(base.observed_at);
    const withinBudget = observed + 29_000;
    assert.equal(deriveBioXpStatus(base, withinBudget).label, 'READY');
    assert.equal(deriveBioXpStatus({ ...base, fresh: false }, withinBudget).label, 'STALE');
    assert.equal(deriveBioXpStatus({ ...base, reachable: null }, withinBudget).label, 'UNKNOWN');
    assert.equal(deriveBioXpStatus({ ...base, reachable: false }, withinBudget).label, 'UNREACHABLE');
    assert.equal(deriveBioXpStatus({ ...base, runtime_ready: null }, withinBudget).label, 'UNKNOWN');
    assert.equal(deriveBioXpStatus({ ...base, hardware_ready: null }, withinBudget).label, 'UNKNOWN');
});

test('cached ready evidence expires locally at the server freshness budget', () => {
    const observed = Date.parse(base.observed_at);
    assert.equal(deriveBioXpStatus(base, observed + 30_001).label, 'STALE');
    assert.equal(deriveBioXpStatus({ ...base, observed_at: null }, observed).label, 'UNKNOWN');
});

test('saved profile remains disconnected after restart', () => {
    const status = deriveBioXpStatus({ ...base, active: false, reachable: null, runtime_ready: null, hardware_ready: null });
    assert.equal(status.label, 'SAVED / DISCONNECTED');
    assert.equal(status.ready, false);
});

test('per-command server admission allows degraded snapshot collection without global hardware readiness', () => {
    const admitted = ['collect_hardware_snapshot'];
    assert.equal(isBioXpCommandAvailable(admitted, 'collect_hardware_snapshot', 'HARDWARE NOT READY'), true);
    assert.equal(isBioXpCommandAvailable(admitted, 'construct_pipettes', 'HARDWARE NOT READY'), false);
    assert.equal(isBioXpCommandAvailable(admitted, 'collect_hardware_snapshot', 'STALE'), false);
    assert.equal(isBioXpCommandAvailable(admitted, 'collect_hardware_snapshot', 'UNKNOWN'), false);
    assert.equal(isBioXpCommandAvailable(undefined, 'collect_hardware_snapshot', 'READY'), false);
});
