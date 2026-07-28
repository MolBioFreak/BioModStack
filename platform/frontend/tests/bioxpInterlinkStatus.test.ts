import assert from 'node:assert/strict';
import test from 'node:test';

import {
    deriveBioXpNoCommandsMessage,
    deriveBioXpStatus,
    isBioXpControlPlaneFresh,
    isBioXpCommandAvailable,
} from '../src/components/bioxpInterlinkStatus.js';

const base = {
    configured: true,
    active: true,
    generation: 2,
    target_url: 'http://ro***:8123',
    reachable: true,
    runtime_ready: true,
    hardware_ready: true,
    hardware_observed_at: '2026-07-18T00:00:00Z',
    hardware_fresh: true,
    hardware_stale: false,
    hardware_evidence_error: null,
    capabilities: [],
    observed_at: '2026-07-18T00:00:00Z',
    freshness_budget_seconds: 30,
    fresh: true,
    last_error: null,
    command_active: false,
};

test('empty command list explains serialized command ownership instead of claiming missing capabilities', () => {
    assert.equal(
        deriveBioXpNoCommandsMessage(true, []),
        'Hardware snapshot or another OEM command is in progress; commands are temporarily locked.',
    );
    assert.equal(
        deriveBioXpNoCommandsMessage(false, []),
        'No normal OEM commands are available. The current robot runtime has not yet advertised an admitted command capability.',
    );
    assert.equal(deriveBioXpNoCommandsMessage(false, ['collect_hardware_snapshot']), null);
});

test('ready requires active, fresh, reachable, runtime-ready and hardware-ready', () => {
    const observed = Date.parse(base.observed_at);
    const withinBudget = observed + 29_000;
    assert.equal(deriveBioXpStatus(base, withinBudget).label, 'READY');
    assert.equal(deriveBioXpStatus({ ...base, fresh: false }, withinBudget).label, 'STALE');
    assert.equal(deriveBioXpStatus({ ...base, reachable: null }, withinBudget).label, 'UNKNOWN');
    assert.equal(deriveBioXpStatus({ ...base, reachable: false }, withinBudget).label, 'UNREACHABLE');
    assert.equal(deriveBioXpStatus({ ...base, runtime_ready: null }, withinBudget).label, 'UNKNOWN');
    assert.equal(deriveBioXpStatus({ ...base, hardware_ready: null }, withinBudget).label, 'UNKNOWN');
    const runtimeReadyWithStaleHardware = deriveBioXpStatus({
        ...base,
        hardware_ready: null,
        hardware_fresh: false,
        hardware_stale: true,
        hardware_evidence_error: 'Hardware snapshot is stale',
    }, withinBudget);
    assert.equal(runtimeReadyWithStaleHardware.label, 'RUNTIME READY');
    assert.match(runtimeReadyWithStaleHardware.detail, /hardware evidence is stale/i);
});


test('cached ready evidence expires locally at the server freshness budget', () => {
    const observed = Date.parse(base.observed_at);
    assert.equal(deriveBioXpStatus(base, observed + 30_001).label, 'STALE');
    assert.equal(deriveBioXpStatus({ ...base, observed_at: null }, observed).label, 'UNKNOWN');
    assert.equal(isBioXpControlPlaneFresh(base, observed + 29_000), true);
    assert.equal(isBioXpControlPlaneFresh(base, observed + 30_001), false);
    assert.equal(isBioXpControlPlaneFresh({ ...base, observed_at: null }, observed), false);
    assert.equal(isBioXpControlPlaneFresh({ ...base, observed_at: 'malformed' }, observed), false);
});

test('failed automatic restore leaves the saved target visibly disconnected and retryable', () => {
    const status = deriveBioXpStatus({ ...base, active: false, reachable: null, runtime_ready: null, hardware_ready: null });
    assert.equal(status.label, 'SAVED / DISCONNECTED');
    assert.equal(status.ready, false);
});

test('server-admitted query-only snapshot remains available to recover stale or missing evidence', () => {
    const admitted = ['collect_hardware_snapshot'];
    assert.equal(isBioXpCommandAvailable(admitted, 'collect_hardware_snapshot', 'HARDWARE NOT READY'), true);
    assert.equal(isBioXpCommandAvailable(admitted, 'collect_hardware_snapshot', 'STALE'), true);
    assert.equal(isBioXpCommandAvailable(admitted, 'collect_hardware_snapshot', 'UNKNOWN'), true);
    assert.equal(isBioXpCommandAvailable(admitted, 'initialize_oem_environment', 'HARDWARE NOT READY'), false);
    assert.equal(isBioXpCommandAvailable(admitted, 'initialize_oem_environment', 'STALE'), false);
    assert.equal(isBioXpCommandAvailable(undefined, 'collect_hardware_snapshot', 'READY'), false);
});

test('retired USB activation remains unavailable even if a stale server advertises it', () => {
    const admitted = ['activate_usb_for_service'];
    assert.equal(isBioXpCommandAvailable(admitted, 'activate_usb_for_service', 'UNKNOWN'), false);
    assert.equal(isBioXpCommandAvailable(admitted, 'activate_usb_for_service', 'RUNTIME READY'), false);
    assert.equal(isBioXpCommandAvailable(undefined, 'activate_usb_for_service', 'UNKNOWN'), false);
});

test('retired initialization remains unavailable even if a stale server advertises it', () => {
    const admitted = ['initialize_oem_environment'];
    assert.equal(isBioXpCommandAvailable(admitted, 'initialize_oem_environment', 'RUNTIME READY'), false);
    assert.equal(isBioXpCommandAvailable(admitted, 'initialize_oem_environment', 'READY'), false);
});
