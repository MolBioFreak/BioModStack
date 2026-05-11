import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const systemResourcesSource = readFileSync(resolve('src/components/dashboard/SystemResources.tsx'), 'utf8');

function sourceBetween(startNeedle: string, endNeedle: string): string {
    const start = systemResourcesSource.indexOf(startNeedle);
    assert.notEqual(start, -1, `missing start marker ${startNeedle}`);
    const end = systemResourcesSource.indexOf(endNeedle, start + startNeedle.length);
    assert.notEqual(end, -1, `missing end marker ${endNeedle}`);
    return systemResourcesSource.slice(start, end);
}

test('GPU scheduler config fetch does not store API error payloads as config', () => {
    const guard = sourceBetween('function isSchedulerConfigPayload', 'function GPUSchedulerSettings');
    const fetchEffect = sourceBetween("fetch('/api/gpu/scheduler-config')", '}, []);');

    assert.match(guard, /candidate\.global/);
    assert.match(guard, /candidate\.overrides/);
    assert.match(fetchEffect, /!res\.ok \|\| !isSchedulerConfigPayload\(data\)/);
    assert.match(fetchEffect, /return null/);
    assert.match(fetchEffect, /if \(!data\) return/);
});

test('GPU scheduler panel stays hidden instead of rendering against null config', () => {
    const schedulerPanel = sourceBetween('function GPUSchedulerSettings', 'function SystemResources');

    assert.match(schedulerPanel, /if \(!config\) return null/);
    assert.doesNotMatch(schedulerPanel, /\.then\(res => res\.json\(\)\)/);
});
