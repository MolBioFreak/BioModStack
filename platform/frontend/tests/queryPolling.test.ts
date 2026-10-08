import assert from 'node:assert/strict';
import test from 'node:test';

import { jobPollingInterval } from '../src/lib/queryPolling.js';

test('foreground polling retains its interval while hidden so TanStack can resume it on focus', () => {
    const originalDocument = globalThis.document;
    Object.defineProperty(globalThis, 'document', {
        configurable: true,
        value: { hidden: true },
    });

    try {
        assert.equal(jobPollingInterval(1500), 1500);
    } finally {
        Object.defineProperty(globalThis, 'document', {
            configurable: true,
            value: originalDocument,
        });
    }
});

test('polling retains its schedule while offline and backs off after failures', () => {
    const originalNavigator = globalThis.navigator;
    Object.defineProperty(globalThis, 'navigator', {
        configurable: true,
        value: { onLine: false },
    });

    try {
        assert.equal(jobPollingInterval(1500, { state: { fetchFailureCount: 2 } }), 6000);
    } finally {
        Object.defineProperty(globalThis, 'navigator', {
            configurable: true,
            value: originalNavigator,
        });
    }
    assert.equal(jobPollingInterval(1500, { state: { fetchFailureCount: 20 } }), 24000);
});
