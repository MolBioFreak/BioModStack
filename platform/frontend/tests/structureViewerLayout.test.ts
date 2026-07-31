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
const RESULTS_VIEWER_PATH = resolve(process.cwd(), 'src/components/ResultsViewer.tsx');
const STRUCTURE_VIEWER_HOST_PATH = resolve(process.cwd(), 'src/structureViewer/StructureViewerHost.tsx');
const METRIC_LEGEND_PATH = resolve(process.cwd(), 'src/structureViewer/extensions/metrics/MetricLegendPanel.tsx');

test('structure viewer top toolbar contains navigation only, not legacy metric controls', () => {
    const source = readFileSync(STRUCTURE_VIEWER_PANE_PATH, 'utf8');
    const start = source.indexOf('const renderViewerToolbar');
    const end = source.indexOf('\n    return (', start);
    const toolbar = source.slice(start, end);

    assert.doesNotMatch(toolbar, /renderQuickViewBar/);
    assert.doesNotMatch(toolbar, /Color Legend/);
    assert.doesNotMatch(toolbar, /Hide Analytics|Show Analytics/);
    assert.doesNotMatch(toolbar, /handleColorModeChange/);
    assert.match(toolbar, /Source Backbone/);
    assert.match(toolbar, /Reference/);
    assert.match(toolbar, /Fullscreen/);
});

test('legacy analytics presentation is disabled in favor of the shared metric workbench', () => {
    const source = readFileSync(STRUCTURE_VIEWER_PANE_PATH, 'utf8');
    assert.match(source, /const LEGACY_ANALYTICS_ENABLED = false/);
    assert.match(source, /LEGACY_ANALYTICS_ENABLED && !isFullscreen/);
    assert.match(source, /LEGACY_ANALYTICS_ENABLED && isFullscreen/);
});

test('new metric workbench owns minimize and restore in normal and fullscreen modes', () => {
    const host = readFileSync(STRUCTURE_VIEWER_HOST_PATH, 'utf8');
    const pane = readFileSync(STRUCTURE_VIEWER_PANE_PATH, 'utf8');

    assert.match(host, /aria-label="Minimize metric workbench"/);
    assert.match(host, /Show metrics/);
    assert.match(host, /onMetricWorkbenchVisibilityChange/);
    assert.match(pane, /showMetricWorkbench=\{!shapeMetrics && !isFullscreen && metricWorkbenchOpen\}/);
});

test('structure viewer separates persisted structure summaries from spatial visual layers', () => {
    const source = readFileSync(STRUCTURE_VIEWER_PANE_PATH, 'utf8');
    const host = readFileSync(STRUCTURE_VIEWER_HOST_PATH, 'utf8');
    const legend = readFileSync(METRIC_LEGEND_PATH, 'utf8');

    for (const id of ['ptm', 'complex-iplddt', 'complex-ipde', 'gyration-radius', 'residue-count', 'helix-percent', 'sheet-percent', 'coil-percent']) {
        assert.match(source, new RegExp(`id: '${id}'`));
    }
    assert.match(source, /metricLayers=\{allMetricLayers\}/);
    assert.match(legend, /Scalar value/);
    assert.match(host, /visualMetricLayers/);
    assert.match(host, /structureSummaryLayers/);
    assert.match(host, /Structure summary/);
    assert.match(host, /setSelection\(null\).*setSelectedMetricId/s);
    assert.match(host, /setCameraResetToken/);
});

test('structure tab does not render legacy result-table filter controls outside the viewer', () => {
    const source = readFileSync(RESULTS_VIEWER_PATH, 'utf8');
    const start = source.indexOf('{/* STRUCTURE TAB - Fullscreen-Aware with Overlays */}');
    const end = source.indexOf('{/* ANTIBODY TAB */}', start);
    const structureTabSource = source.slice(start, end);

    assert.ok(start >= 0 && end > start);
    assert.doesNotMatch(structureTabSource, />Sort by</);
    assert.doesNotMatch(structureTabSource, />Epi Cts ≥</);
    assert.doesNotMatch(structureTabSource, />Apply Filters</);
});

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
    assert.match(source, /aria-label="Minimize analytics panel"/);
    assert.match(source, /data-structure-viewer-fullscreen-analytics-layout=/);
    assert.match(source, /resolveStructureViewerFullscreenAnalyticsLayout/);
});

test('metric workbench visibility state is shared across normal and fullscreen modes', () => {
    const source = readFileSync(STRUCTURE_VIEWER_PANE_PATH, 'utf8');

    assert.match(source, /metricWorkbenchOpen/);
    assert.match(source, /showMetricWorkbench=\{!shapeMetrics && !isFullscreen && metricWorkbenchOpen\}/);
    assert.match(source, /onMetricWorkbenchVisibilityChange=\{shapeMetrics \? undefined : setMetricWorkbenchOpen\}/);
    assert.match(source, /showSequenceTrack/);
});

test('structure viewer source wires the responsive stacked analytics layout', () => {
    const source = readFileSync(STRUCTURE_VIEWER_PANE_PATH, 'utf8');

    assert.match(source, /resolveStructureViewerLayout/);
    assert.match(source, /data-structure-viewer-layout=/);
    assert.match(source, /data-structure-viewer-analytics-layout=/);
    assert.match(source, /renderViewerToolbar\(isFullscreen \|\| viewerLayout\.isStacked\)/);
    assert.match(source, /renderSectionButtons\(viewerLayout\.isStacked\)/);
});
