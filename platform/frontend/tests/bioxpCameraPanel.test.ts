import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import { createCameraObjectUrlOwner, deriveBioXpCameraPresentation } from '../src/components/bioxpCameraState';

const panel = readFileSync(resolve('src/components/BioXpCameraPanel.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('camera keeps the finite same-origin endpoint allowlist', () => {
    for (const endpoint of ['/api/bioxp/camera/status', '/api/bioxp/camera/frame/latest', '/api/bioxp/camera/snapshot']) {
        assert.match(client, new RegExp(endpoint));
    }
});

test('camera UI is image plus refresh and capture only', () => {
    for (const label of ['>Camera<', "'Refresh'", "'Capture'"]) assert.match(panel, new RegExp(label));
    for (const rejected of ['frame sequence', 'provider generation', 'dropped frames', 'content sha256', 'Camera Observability']) {
        assert.doesNotMatch(panel, new RegExp(rejected, 'i'));
    }
});

test('camera URL owner revokes replaced and disposed frames exactly once', () => {
    const revoked: string[] = [];
    let next = 0;
    const owner = createCameraObjectUrlOwner({
        create: () => `blob:${++next}`,
        revoke: (url) => revoked.push(url),
    });
    const first = owner.begin();
    assert.equal(owner.adopt(first, {} as Blob), 'blob:1');
    const second = owner.begin();
    assert.equal(owner.adopt(second, {} as Blob), 'blob:2');
    assert.deepEqual(revoked, ['blob:1']);
    owner.dispose();
    owner.dispose();
    assert.deepEqual(revoked, ['blob:1', 'blob:2']);
});

test('camera URL owner rejects and revokes stale asynchronous frames', () => {
    const revoked: string[] = [];
    let next = 0;
    const owner = createCameraObjectUrlOwner({
        create: () => `blob:${++next}`,
        revoke: (url) => revoked.push(url),
    });
    const stale = owner.begin();
    const current = owner.begin();
    assert.equal(owner.adopt(stale, {} as Blob), null);
    assert.deepEqual(revoked, ['blob:1']);
    assert.equal(owner.adopt(current, {} as Blob), 'blob:2');
    owner.clear();
    assert.deepEqual(revoked, ['blob:1', 'blob:2']);
});

test('camera presentation expires at the exact freshness boundary', () => {
    const presentation = deriveBioXpCameraPresentation({
        status: {
            available: true,
            state: 'live',
            frame_sequence: 7,
            frame_age_seconds: 1,
            freshness_budget_seconds: 5,
            provider_generation: 3,
        },
        statusReceivedAtMs: 1_000,
        lastSequenceAdvanceAtMs: 1_000,
        nowMs: 5_000,
        error: null,
    });
    assert.equal(presentation.label, 'STALE');
});
