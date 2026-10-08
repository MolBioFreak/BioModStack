import assert from 'node:assert/strict';
import test from 'node:test';

import {
    getFrustraMpnnResultContext,
    hasFrustraMpnnResultSurface,
} from '../src/components/frustraMpnnResultSurface.js';

test('standalone FrustraMPNN jobs own the data-first result surface', () => {
    assert.equal(hasFrustraMpnnResultSurface({ model_id: 'frustrampnn', params: {} }), true);
});

test('parent workflows explicitly enabling FrustraMPNN own the same result surface', () => {
    assert.equal(hasFrustraMpnnResultSurface({ model_id: 'boltz2', params: { run_frustrampnn: true } }), true);
    assert.deepEqual(getFrustraMpnnResultContext({ model_id: 'boltz2', params: { run_frustrampnn: true } }), {
        kind: 'integrated-parent',
        usesChildReceipt: false,
        canReanalyzePersistedInputs: false,
        executionLabel: 'Persisted workflow analysis',
    });
});

test('only scheduler-owned FrustraMPNN children may use receipt and reanalysis APIs', () => {
    assert.deepEqual(getFrustraMpnnResultContext({ model_id: 'frustrampnn', params: {} }), {
        kind: 'scheduler-child',
        usesChildReceipt: true,
        canReanalyzePersistedInputs: true,
        executionLabel: 'Persisted execution child',
    });
});

test('persisted FrustraMPNN results own the surface when historical job params omit the feature flag', () => {
    const historical = { model_id: 'boltz2', params: {}, frustrampnn_result_count: 1 };
    assert.equal(hasFrustraMpnnResultSurface(historical), true);
    assert.equal(getFrustraMpnnResultContext(historical)?.kind, 'integrated-parent');
});

test('absent, false, and string values do not fabricate FrustraMPNN applicability', () => {
    assert.equal(hasFrustraMpnnResultSurface({ model_id: 'boltz2', params: {} }), false);
    assert.equal(hasFrustraMpnnResultSurface({ model_id: 'boltz2', params: { run_frustrampnn: false } }), false);
    assert.equal(hasFrustraMpnnResultSurface({ model_id: 'boltz2', params: { run_frustrampnn: 'true' } }), false);
});
