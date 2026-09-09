import assert from 'node:assert/strict';
import test from 'node:test';

import { bioXpReceiptTimestampText } from '../src/lib/bioxpReceiptTimestamp.js';

// Retained SQLite history also publishes numeric epoch seconds, not only strings.
test('BioXP numeric receipt timestamps do not crash the cockpit', () => {
    for (const value of [1786300200.7960694, 0]) {
        assert.equal(bioXpReceiptTimestampText(value), new Date(value * 1000).toLocaleString());
    }
    assert.equal(bioXpReceiptTimestampText(Number.NaN), 'NaN');
});

test('BioXP receipt timestamps accept persisted epoch-second strings', () => {
    const persisted = '1786300200.7960694';
    assert.equal(
        bioXpReceiptTimestampText(persisted),
        new Date(Number(persisted) * 1000).toLocaleString(),
    );
});

test('BioXP receipt timestamps preserve ISO values and pending state', () => {
    const iso = '2026-08-09T18:30:00Z';
    assert.equal(bioXpReceiptTimestampText(iso), new Date(iso).toLocaleString());
    assert.equal(bioXpReceiptTimestampText(null), 'in progress');
});
