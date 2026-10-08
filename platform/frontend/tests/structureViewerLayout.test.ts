import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
    resolveStructureViewerFullscreenAnalyticsLayout,
    resolveStructureViewerLayout,
    shouldStackStructureViewerPanelsForViewport,
    shouldUseCompactFullscreenAnalytics,
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

test('fullscreen analytics uses compact bottom-sheet sizing on landscape phones', () => {
    assert.equal(shouldUseCompactFullscreenAnalytics({ viewportWidth: 915, viewportHeight: 412 }), true);

    const layout = resolveStructureViewerFullscreenAnalyticsLayout({
        viewportWidth: 915,
        viewportHeight: 412,
    });

    assert.equal(layout.mode, 'compact');
    assert.match(layout.frameClassName, /inset-x-2/);
    assert.match(layout.frameClassName, /bottom-2/);
    assert.match(layout.panelClassName, /w-full/);
    assert.match(layout.contentClassName, /overflow-y-auto/);
    assert.equal(layout.panelMaxHeight, 316);
});

test('fullscreen analytics keeps a bounded right-side panel on desktop viewports', () => {
    assert.equal(shouldUseCompactFullscreenAnalytics({ viewportWidth: 1280, viewportHeight: 800 }), false);

    const layout = resolveStructureViewerFullscreenAnalyticsLayout({
        viewportWidth: 1280,
        viewportHeight: 800,
    });

    assert.equal(layout.mode, 'sidebar');
    assert.match(layout.frameClassName, /right-4/);
    assert.match(layout.panelClassName, /w-80/);
    assert.match(layout.contentClassName, /overflow-y-auto/);
    assert.equal(layout.panelMaxHeight, 720);
});

test('structure viewer source wires closeable analytics in fullscreen and normal modes', () => {
    const source = readFileSync(STRUCTURE_VIEWER_PANE_PATH, 'utf8');

    assert.match(source, /analyticsPanelOpen/);
    assert.match(source, /setAnalyticsPanelOpen\(false\)/);
    assert.match(source, /aria-label="Close analytics panel"/);
    assert.match(source, /data-structure-viewer-fullscreen-analytics-layout=/);
    assert.match(source, /resolveStructureViewerFullscreenAnalyticsLayout/);
});

test('structure viewer source wires the responsive stacked analytics layout', () => {
    const source = readFileSync(STRUCTURE_VIEWER_PANE_PATH, 'utf8');

    assert.match(source, /resolveStructureViewerLayout/);
    assert.match(source, /data-structure-viewer-layout=/);
    assert.match(source, /data-structure-viewer-analytics-layout=/);
    assert.match(source, /ViewerToolbar isCompact=\{isFullscreen \|\| viewerLayout\.isStacked\}/);
    assert.match(source, /renderSectionButtons\(viewerLayout\.isStacked\)/);
});
