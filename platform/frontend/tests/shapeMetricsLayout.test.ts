import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const source = fs.readFileSync(path.join(process.cwd(), 'src/components/StructureViewerPane.tsx'), 'utf8');

test('Shape metrics stay out of the Molstar canvas control region', () => {
    const marker = 'Shape Blueprint candidate';
    const metricIndex = source.indexOf(marker);
    const viewerIndex = source.indexOf('{/* Main Viewer - ALWAYS at this exact tree position */}');
    assert.ok(metricIndex > 0);
    assert.ok(metricIndex < viewerIndex, 'Shape summary must render before the Molstar canvas');
    const window = source.slice(metricIndex - 500, metricIndex + 500);
    assert.doesNotMatch(window, /absolute right-3 top-3/u);
    assert.match(window, /shapeMetrics && !isFullscreen/u);
});
