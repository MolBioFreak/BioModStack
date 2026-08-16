import assert from 'node:assert/strict';
import test from 'node:test';

import {
    buildGpuCatalog,
    formatGpuLabel,
    getGpuCatalogEntry,
    getGpuMemoryTotalMb,
    listGpuCatalogEntries,
} from '../src/components/gpuCatalog.js';

test('buildGpuCatalog uses live non-DALAB GPU names, sparse indices, and memory totals', () => {
    const catalog = buildGpuCatalog([
        { index: 2, name: 'NVIDIA A100-SXM4-80GB', memory_total_mb: 81920 },
        { index: 7, name: 'NVIDIA L40S', memory_total_mb: 46068 },
    ]);

    assert.deepEqual(listGpuCatalogEntries(catalog).map((gpu) => gpu.index), [2, 7]);
    assert.equal(formatGpuLabel(2, catalog), 'A100-SXM4-80GB');
    assert.equal(formatGpuLabel(7, catalog), 'L40S');
    assert.equal(getGpuMemoryTotalMb(2, catalog), 81920);
    assert.equal(getGpuMemoryTotalMb(7, catalog), 46068);
});

test('buildGpuCatalog disambiguates duplicate detected GPU names without hardcoded model labels', () => {
    const catalog = buildGpuCatalog([
        { index: 0, name: 'NVIDIA GeForce RTX 3090', memory_total_mb: 24576 },
        { index: 3, name: 'NVIDIA GeForce RTX 3090', memory_total_mb: 24576 },
        { index: 4, name: 'NVIDIA RTX 6000 Ada Generation', memory_total_mb: 49140 },
    ]);

    assert.equal(formatGpuLabel(0, catalog), 'RTX 3090 #1');
    assert.equal(formatGpuLabel(3, catalog), 'RTX 3090 #2');
    assert.equal(formatGpuLabel(4, catalog), 'RTX 6000 Ada Generation');
    assert.equal(getGpuCatalogEntry(99, catalog), undefined);
    assert.equal(formatGpuLabel(99, catalog), 'GPU 99');
});

test('buildGpuCatalog ignores invalid GPU records and keeps zero-memory values out of VRAM controls', () => {
    const catalog = buildGpuCatalog([
        { index: Number.NaN, name: 'Broken', memory_total_mb: 12345 },
        { index: 1, name: '', memory_total_mb: 0 },
        { index: 5, name: 'NVIDIA H100 PCIe', memory_total_mb: 81559 },
    ]);

    assert.deepEqual(listGpuCatalogEntries(catalog).map((gpu) => gpu.index), [1, 5]);
    assert.equal(formatGpuLabel(1, catalog), 'GPU 1');
    assert.equal(getGpuMemoryTotalMb(1, catalog), null);
    assert.equal(formatGpuLabel(5, catalog), 'H100 PCIe');
});
