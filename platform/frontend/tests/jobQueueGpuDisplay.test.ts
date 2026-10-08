import assert from 'node:assert/strict';
import test from 'node:test';

import { formatGpuList, resolveQueueGpuDisplay } from '../src/components/jobQueueGpuDisplay.js';

test('multi-gpu queue display stays expanded instead of collapsing to the anchor gpu', () => {
    const display = resolveQueueGpuDisplay({
        displayGpuIds: [0, 1, 2, 3],
        pinnedGpu: null,
        assignedGpu: 0,
    });

    assert.deepEqual(display.gpuIds, [0, 1, 2, 3]);
    assert.equal(display.isMultiGpu, true);
    assert.equal(display.badgeText, '📌 4 GPUs');
    assert.equal(display.detailText, 'RTX 5090, RTX 5060 Ti, RTX 3090 #1, RTX 3090 #2');
});

test('single-gpu queue display preserves the legacy gpu label', () => {
    const display = resolveQueueGpuDisplay({
        displayGpuIds: null,
        pinnedGpu: 2,
        assignedGpu: 2,
    });

    assert.deepEqual(display.gpuIds, [2]);
    assert.equal(display.isMultiGpu, false);
    assert.equal(display.badgeText, '📌 RTX 3090 #1');
    assert.equal(display.detailText, null);
});

test('formatGpuList returns null for empty selections and readable names for known gpus', () => {
    assert.equal(formatGpuList([]), null);
    assert.equal(formatGpuList([1, 3]), 'RTX 5060 Ti, RTX 3090 #2');
});
