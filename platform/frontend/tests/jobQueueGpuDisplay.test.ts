import assert from 'node:assert/strict';
import test from 'node:test';

import { buildGpuCatalog } from '../src/components/gpuCatalog.js';
import { formatGpuList, resolveQueueGpuDisplay } from '../src/components/jobQueueGpuDisplay.js';

const foreignGpuCatalog = buildGpuCatalog([
    { index: 0, name: 'NVIDIA A100-SXM4-80GB', memory_total_mb: 81920 },
    { index: 4, name: 'NVIDIA L40S', memory_total_mb: 46068 },
]);

test('multi-gpu queue display uses dynamic non-DALAB labels instead of hardcoded local rig names', () => {
    const display = resolveQueueGpuDisplay({
        displayGpuIds: [0, 4],
        pinnedGpu: null,
        assignedGpu: 0,
        gpuCatalog: foreignGpuCatalog,
    });

    assert.deepEqual(display.gpuIds, [0, 4]);
    assert.equal(display.isMultiGpu, true);
    assert.equal(display.badgeText, '📌 2 GPUs');
    assert.equal(display.detailText, 'A100-SXM4-80GB, L40S');
    assert.equal(display.title, 'A100-SXM4-80GB, L40S');
});

test('single-gpu queue display uses dynamic sparse-index labels', () => {
    const display = resolveQueueGpuDisplay({
        displayGpuIds: null,
        pinnedGpu: 4,
        assignedGpu: 4,
        gpuCatalog: foreignGpuCatalog,
    });

    assert.deepEqual(display.gpuIds, [4]);
    assert.equal(display.isMultiGpu, false);
    assert.equal(display.badgeText, '📌 L40S');
    assert.equal(display.detailText, null);
});

test('formatGpuList returns null for empty selections, dynamic names for known gpus, and generic fallbacks', () => {
    assert.equal(formatGpuList([], foreignGpuCatalog), null);
    assert.equal(formatGpuList([4, 9], foreignGpuCatalog), 'L40S, GPU 9');
});

test('queue display falls back to generic labels when no live catalog is available', () => {
    const display = resolveQueueGpuDisplay({
        displayGpuIds: [0, 1, 2, 3],
        pinnedGpu: null,
        assignedGpu: 0,
    });

    assert.equal(display.detailText, 'GPU 0, GPU 1, GPU 2, GPU 3');
});
