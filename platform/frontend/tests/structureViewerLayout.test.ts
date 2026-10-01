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
const VIEWER_CSS = readFileSync(resolve(process.cwd(), 'src/structureViewer/structureViewer.css'), 'utf8');

// Focused stylesheet contracts, not a browser-cascade emulator. The token parser
// follows themeContrast.test.ts, including default-token inheritance for light themes.
function themeTokenBlocks() {
    const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');
    const blocks: { name: string; tokens: Record<string, string> }[] = [];
    const re = /(?:^|\n)((?:\[data-theme="[^"]+"\]|:root)(?:,\s*\n\[data-theme="[^"]+"\])*)\s*\{([\s\S]*?)\n\}/g;
    for (const match of css.matchAll(re)) {
        const tokens = Object.fromEntries([...match[2].matchAll(/(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map(decl => [decl[1], decl[2]]));
        for (const selector of match[1].split(',')) blocks.push({ name: selector.trim(), tokens });
    }
    const root = blocks.find(block => block.name === ':root')?.tokens;
    assert.ok(root, 'default theme parsed');
    return blocks.map(block => ({ name: block.name, tokens: { ...root, ...block.tokens } }));
}

function chromeRule(selector: string, property: string): string {
    const rules = [...VIEWER_CSS.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)];
    const propertyPattern = new RegExp(`(?:^|[;\\n])\\s*${property}:\\s*([^;]+);`);
    const match = rules.find(rule => rule[1].includes(selector) && propertyPattern.test(rule[2]));
    assert.ok(match, `${selector} has explicit ${property} coverage`);
    assert.match(match[1], /\[data-bms-molstar-adapter\] \.msp-plugin/, 'native overrides remain scoped');
    const declaration = match[2].match(propertyPattern);
    assert.ok(declaration);
    return declaration[1].trim();
}

function themeContrast(foreground: string, background: string): number {
    const luminance = (hex: string) => {
        const channels = [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255)
            .map(value => value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
        return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
    };
    const a = luminance(foreground), b = luminance(background);
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function declarationToken(value: string): string {
    const match = value.match(/^var\((--[a-z-]+)\)(?: !important)?$/);
    assert.ok(match, `expected existing BMS token, got ${value}`);
    return match[1];
}

test('native settings and help labels have themed effective surfaces in every theme', () => {
    const pairs = [
        ['.msp-control-row-label', '.msp-control-row,'],
        ['.msp-simple-help,', '.msp-simple-help,'],
        ['.msp-simple-help-section,', '.msp-simple-help-section,'],
        ['.msp-control-button-label,', '.msp-control-row > div'],
        ['.msp-sequence-number,', '.msp-sequence-wrapper,'],
        ['.msp-sequence-missing', '.msp-sequence-wrapper,'],
        ['.msp-btn-link-toggle-off', '.msp-btn-link-toggle-off'],
        ['.msp-btn-link-toggle-on', '.msp-btn-link-toggle-on'],
        [':is(:hover, :focus, :active)', ':is(:hover, :focus, :active)'],
        [':is(:disabled, [readonly])', '.msp-btn-link-toggle-on'],
        [':is(:disabled, [readonly])', ':is(:disabled, [readonly])'],
        ['[data-seqid][style*="background-color:"]', '[data-seqid][style*="background-color:"]'],
    ];
    const blocks = themeTokenBlocks();
    for (const name of ['black', 'light', 'clean_light']) assert.ok(blocks.some(block => block.name === `[data-theme="${name}"]`));
    for (const [foregroundSelector, backgroundSelector] of pairs) {
        const foregroundToken = declarationToken(chromeRule(foregroundSelector, 'color'));
        const backgroundToken = declarationToken(chromeRule(backgroundSelector, 'background-color'));
        for (const { name, tokens } of blocks) {
            assert.ok(tokens[foregroundToken] && tokens[backgroundToken], `${name}: defined tokens`);
            const ratio = themeContrast(tokens[foregroundToken], tokens[backgroundToken]);
            assert.ok(ratio >= 4.5, `${name} ${foregroundSelector} on ${backgroundSelector}: ${ratio.toFixed(2)}:1`);
        }
    }
    // Preserve the demonstrated baseline rather than treating the native beige as acceptable.
    assert.ok(themeContrast('#9aa6b3', '#eeece7') < 2.2);
});

test('native states retain readable distinctions without hiding controls or recoloring the molecule', () => {
    assert.equal(chromeRule('.msp-btn-link-toggle-off', 'color'), 'var(--text-secondary) !important');
    assert.equal(chromeRule('.msp-btn-link-toggle-on', 'color'), 'var(--text-primary) !important');
    assert.equal(chromeRule('.msp-control-group-header >', 'background-color'), 'var(--bg-secondary) !important');
    assert.equal(chromeRule(':is(:disabled, [readonly])', 'opacity'), '1');
    assert.equal(chromeRule(':is(:disabled, [readonly])', 'color'), 'var(--text-muted) !important');
    assert.equal(chromeRule('[data-seqid][style*="background-color:"]', 'background-color'), 'var(--surface-control-strong) !important');
    assert.equal(chromeRule('[data-seqid][style*="background-color:"]', 'box-shadow'), 'inset 0 -2px var(--accent-primary)');
    assert.equal(chromeRule('[data-seqid][style*="rgb(255, 102, 153)"]', 'outline'), '1px dashed var(--accent-primary)');
    assert.doesNotMatch(VIEWER_CSS.replace(/\/\*[\s\S]*?\*\//g, ''), /(?:canvas|\.msp-color-swatch)[^{}]*\{[^}]*\b(?:color|background|fill):/);
    const chrome = VIEWER_CSS.slice(VIEWER_CSS.indexOf('/* Mol* ships'));
    assert.doesNotMatch(chrome, /display:\s*none|visibility:\s*hidden|pointer-events:|filter:/);
    assert.doesNotMatch(chrome, /(?:\.msp-plugin\s*\*|\.msp-plugin\s+span)\s*\{/);
});

test('shared workbench docks beside or below the scene instead of covering it', () => {
    const host = readFileSync(STRUCTURE_VIEWER_HOST_PATH, 'utf8');
    const css = readFileSync(resolve(process.cwd(), 'src/structureViewer/structureViewer.css'), 'utf8');
    assert.match(host, /className="bms-structure-canvas"/);
    assert.match(host, /<MolstarViewerImpl[^>]*height="100%"/);
    assert.match(host, /<aside hidden=\{workbenchCollapsed\} className="bms-structure-workbench/);
    assert.doesNotMatch(host, /<aside[^>]*className="absolute/);
    assert.match(css, /grid-template-columns: minmax\(0, 1fr\) minmax\(18rem, 22rem\)/);
    assert.match(css, /@container \(min-width: 56rem\)/);
    assert.match(css, /background-color: var\(--surface-control\)/);
    assert.match(css, /color: var\(--text-primary\)/);
});

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
    assert.match(pane, /showMetricWorkbench=\{!shapeMetrics && metricWorkbenchOpen\}/);
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
    assert.match(source, /showMetricWorkbench=\{!shapeMetrics && metricWorkbenchOpen\}/);
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
