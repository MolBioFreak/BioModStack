import assert from 'node:assert/strict';
import test from 'node:test';

import { deriveRuntimeStatusSummary } from '../src/components/bioxpConnectionSemantics.js';

test('missing linkage surfaces an unconfigured runtime and robot-local admin ownership', () => {
    const summary = deriveRuntimeStatusSummary({
        linkageConfigured: false,
        runtimeLoading: false,
        runtimeStatus: null,
    });

    assert.equal(summary.state, 'unconfigured');
    assert.equal(summary.label, 'NOT CONFIGURED');
    assert.equal(summary.linkedRuntimeReachable, false);
    assert.equal(summary.hardwareConnected, false);
    assert.equal(summary.adminLabel, 'ROBOT-LOCAL');
    assert.match(summary.detail || '', /connect bms/i);
    assert.match(summary.adminDetail, /robot-local/i);
});

test('reachable runtime reports HTTP truth without implying SSH/admin lifecycle control', () => {
    const summary = deriveRuntimeStatusSummary({
        linkageConfigured: true,
        runtimeLoading: false,
        runtimeStatus: {
            linked_runtime_reachable: true,
            hardware_connected: true,
            admin_control_available: false,
            maintenance_mode: 'robot-local',
            detail: 'Linked BioXP runtime responded to /status and reported hardware connectivity.',
        },
    });

    assert.equal(summary.state, 'reachable');
    assert.equal(summary.label, 'REACHABLE');
    assert.equal(summary.linkedRuntimeReachable, true);
    assert.equal(summary.hardwareConnected, true);
    assert.equal(summary.adminLabel, 'ROBOT-LOCAL');
    assert.doesNotMatch(summary.detail || '', /ssh/i);
});

test('configured but unreachable runtime surfaces the proxy failure cleanly', () => {
    const summary = deriveRuntimeStatusSummary({
        linkageConfigured: true,
        runtimeLoading: false,
        runtimeStatus: {
            linked_runtime_reachable: false,
            hardware_connected: false,
            admin_control_available: false,
            maintenance_mode: 'robot-local',
            detail: 'Cannot connect to BioXP hardware node at http://robot:8123.',
        },
    });

    assert.equal(summary.state, 'unreachable');
    assert.equal(summary.label, 'UNREACHABLE');
    assert.equal(summary.linkedRuntimeReachable, false);
    assert.equal(summary.hardwareConnected, false);
    assert.match(summary.detail || '', /cannot connect/i);
    assert.match(summary.adminDetail, /maintenance/i);
});
