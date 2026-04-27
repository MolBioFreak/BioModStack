import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveInitialGpuPinningState } from '../src/components/gpuToggleState.js';

test('resolves saved GPU pinning with lock_gpus only when pins are present', () => {
    assert.deepEqual(
        resolveInitialGpuPinningState({ pinned_gpus: [7, 2], lock_gpus: true }),
        { pinnedGpus: [2, 7], lockGpus: true },
    );

    assert.deepEqual(
        resolveInitialGpuPinningState({ pinned_gpus: [], lock_gpus: true }),
        { pinnedGpus: [], lockGpus: false },
    );
});

test('normalizes sparse saved GPU ids without assuming a local four-GPU topology', () => {
    assert.deepEqual(
        resolveInitialGpuPinningState({ pinned_gpus: ['4', 0, '4', -1, 'bad', 9], lock_gpus: 'true' }),
        { pinnedGpus: [0, 4, 9], lockGpus: true },
    );
});

test('treats false-like saved lock_gpus values as unlocked', () => {
    for (const lockValue of [false, 0, '0', 'false', 'no', undefined]) {
        assert.deepEqual(
            resolveInitialGpuPinningState({ pinned_gpus: [3], lock_gpus: lockValue }),
            { pinnedGpus: [3], lockGpus: false },
        );
    }
});
