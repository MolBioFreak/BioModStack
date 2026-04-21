import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
    resolveStructureViewerLayout,
    shouldStackStructureViewerPanelsForViewport,
    STRUCTURE_VIEWER_COMPACT_HEIGHT,
    STRUCTURE_VIEWER_DEFAULT_HEIGHT,
    STRUCTURE_VIEWER_STACKED_LAYOUT_BREAKPOINT,
} from '../src/components/structureViewerLayout.js';

const STRUCTURE_VIEWER_PANE_PATH = resolve(process.cwd(), 'src/components/StructureViewerPane.tsx');

test('phone-sized structure viewer viewports stack analytics below the Mol* viewer', () => {
    assert.equal(shouldStackStructureViewerPanelsForViewport(390), true);
    assert.equal(shouldStackStructureViewerPanelsForViewport(STRUCTURE_VIEWER_STACKED_LAYOUT_BREAKPOINT - 1), true);

    const layout = resolveStructureViewerLayout({
        viewportWidth: 390,
        isFullscreen: false,
    });

    assert.deepEqual(layout, {
        isStacked: true,
        viewerHeight: STRUCTURE_VIEWER_COMPACT_HEIGHT,
    });
});

test('desktop and fullscreen structure viewers keep the split analytics layout', () => {
    assert.equal(shouldStackStructureViewerPanelsForViewport(STRUCTURE_VIEWER_STACKED_LAYOUT_BREAKPOINT), false);

    assert.deepEqual(resolveStructureViewerLayout({
        viewportWidth: 1280,
        isFullscreen: false,
    }), {
        isStacked: false,
        viewerHeight: STRUCTURE_VIEWER_DEFAULT_HEIGHT,
    });

    assert.deepEqual(resolveStructureViewerLayout({
        viewportWidth: 390,
        isFullscreen: true,
    }), {
        isStacked: false,
        viewerHeight: STRUCTURE_VIEWER_DEFAULT_HEIGHT,
    });
});

test('structure viewer source wires the responsive stacked analytics layout', () => {
    const source = readFileSync(STRUCTURE_VIEWER_PANE_PATH, 'utf8');

    assert.match(source, /resolveStructureViewerLayout/);
    assert.match(source, /data-structure-viewer-layout=/);
    assert.match(source, /data-structure-viewer-analytics-layout=/);
    assert.match(source, /ViewerToolbar isCompact=\{isFullscreen \|\| viewerLayout\.isStacked\}/);
    assert.match(source, /renderSectionButtons\(viewerLayout\.isStacked\)/);
});
