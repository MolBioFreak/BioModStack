import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
    clampMolBioPanelWidth,
    getDefaultMolBioToolPanelWidth,
    getMolBioPanelBounds,
    MOLBIO_MIN_VIEWPORT_FOR_OPEN_SIDE_PANELS,
    MOLBIO_VIEWER_MIN_WIDTH,
    resolveMolBioViewerLayout,
    shouldCollapseMolBioPanelsForViewport,
} from '../src/components/MolBioToolkit/utils/viewerLayout.js';

const TOOLKIT_PATH = resolve(process.cwd(), 'src/components/MolBioToolkit/MolBioToolkitV2.tsx');
const HEADER_PATH = resolve(process.cwd(), 'src/components/MolBioToolkit/SequenceHeader.tsx');
const VIEWER_PATH = resolve(process.cwd(), 'src/components/MolBioToolkit/SequenceViewer.tsx');

test('tool panel defaults stay tuned per workflow', () => {
    assert.equal(getDefaultMolBioToolPanelWidth('view'), 288);
    assert.equal(getDefaultMolBioToolPanelWidth('align'), 416);
    assert.equal(getDefaultMolBioToolPanelWidth('history'), 416);
    assert.equal(getDefaultMolBioToolPanelWidth('assembly'), 544);
    assert.equal(getDefaultMolBioToolPanelWidth('primers'), 480);
});

test('side panel widths clamp to safe bounds', () => {
    assert.equal(clampMolBioPanelWidth(180, { min: 224, max: 480 }), 224);
    assert.equal(clampMolBioPanelWidth(320, { min: 224, max: 480 }), 320);
    assert.equal(clampMolBioPanelWidth(640, { min: 224, max: 480 }), 480);
});

test('fullscreen viewer layout collapses side menus while preserving tuned widths', () => {
    const focused = resolveMolBioViewerLayout({
        activePanel: 'align',
        viewportWidth: 1440,
        leftPanelWidth: 260,
        rightPanelWidth: 999,
        isViewerFullscreen: true,
        isLibraryPanelCollapsed: false,
        isToolPanelCollapsed: false,
    });
    const normal = resolveMolBioViewerLayout({
        activePanel: 'assembly',
        viewportWidth: 1440,
        leftPanelWidth: 999,
        rightPanelWidth: 999,
        isViewerFullscreen: false,
        isLibraryPanelCollapsed: false,
        isToolPanelCollapsed: false,
    });

    assert.equal(focused.showLibraryPanel, false);
    assert.equal(focused.showToolPanel, false);
    assert.equal(focused.showLibraryResizeHandle, false);
    assert.equal(focused.showToolResizeHandle, false);
    assert.equal(focused.rightPanelWidth, 416);

    assert.equal(normal.showLibraryPanel, true);
    assert.equal(normal.showToolPanel, true);
    assert.equal(normal.showLibraryResizeHandle, true);
    assert.equal(normal.showToolResizeHandle, true);
    assert.equal(normal.leftPanelWidth, 480);
    assert.equal(normal.rightPanelWidth, 640);
});

test('manual panel collapse can hide either side while keeping the central viewer open', () => {
    const libraryHidden = resolveMolBioViewerLayout({
        activePanel: 'view',
        viewportWidth: 1440,
        leftPanelWidth: 280,
        rightPanelWidth: 320,
        isViewerFullscreen: false,
        isLibraryPanelCollapsed: true,
        isToolPanelCollapsed: false,
    });
    const bothHidden = resolveMolBioViewerLayout({
        activePanel: 'view',
        viewportWidth: 1440,
        leftPanelWidth: 280,
        rightPanelWidth: 320,
        isViewerFullscreen: false,
        isLibraryPanelCollapsed: true,
        isToolPanelCollapsed: true,
    });

    assert.equal(libraryHidden.showLibraryPanel, false);
    assert.equal(libraryHidden.showToolPanel, true);
    assert.equal(libraryHidden.showLibraryResizeHandle, false);
    assert.equal(libraryHidden.showToolResizeHandle, true);

    assert.equal(bothHidden.showLibraryPanel, false);
    assert.equal(bothHidden.showToolPanel, false);
    assert.equal(bothHidden.showLibraryResizeHandle, false);
    assert.equal(bothHidden.showToolResizeHandle, false);
});

test('narrow viewports keep a minimum center viewer width when both side panels are visible', () => {
    const layout = resolveMolBioViewerLayout({
        activePanel: 'assembly',
        viewportWidth: 960,
        leftPanelWidth: 480,
        rightPanelWidth: 640,
        isViewerFullscreen: false,
        isLibraryPanelCollapsed: false,
        isToolPanelCollapsed: false,
    });

    assert.equal(layout.leftPanelWidth, 384);
    assert.equal(layout.rightPanelWidth, 256);
    assert.equal(960 - layout.leftPanelWidth - layout.rightPanelWidth, MOLBIO_VIEWER_MIN_WIDTH);
});

test('phone-sized viewports start with both side panels collapsed', () => {
    assert.equal(shouldCollapseMolBioPanelsForViewport(MOLBIO_MIN_VIEWPORT_FOR_OPEN_SIDE_PANELS - 1), true);
    assert.equal(shouldCollapseMolBioPanelsForViewport(MOLBIO_MIN_VIEWPORT_FOR_OPEN_SIDE_PANELS), false);
    assert.equal(shouldCollapseMolBioPanelsForViewport(390), true);
    assert.equal(shouldCollapseMolBioPanelsForViewport(1280), false);
});

test('phone-sized panel bounds allow narrower side menus without consuming the entire viewer', () => {
    assert.deepEqual(getMolBioPanelBounds('left', 390), { min: 176, max: 294 });
    assert.deepEqual(getMolBioPanelBounds('right', 390), { min: 208, max: 294 });
});

test('mol bio toolkit source wires fullscreen and side-panel collapse controls', () => {
    const source = readFileSync(TOOLKIT_PATH, 'utf8');

    assert.match(source, /isViewerFullscreen/);
    assert.match(source, /shouldCollapseMolBioPanelsForViewport/);
    assert.match(source, /data-molbio-viewer-fullscreen/);
    assert.match(source, /data-molbio-panel-resize-handle="left"/);
    assert.match(source, /data-molbio-panel-resize-handle="right"/);
    assert.match(source, /onToggleFullscreen/);
    assert.match(source, /onToggleLibraryPanel/);
    assert.match(source, /onToggleToolPanel/);
});

test('linear imported constructs force the sequence viewer out of plasmid/circular mode', () => {
    const source = readFileSync(TOOLKIT_PATH, 'utf8');

    assert.match(source, /const effectiveViewMode: ViewMode = sequenceData\.circular \? viewMode : 'linear';/);
    assert.equal((source.match(/viewMode=\{effectiveViewMode\}/g) || []).length, 3);
});

test('large circular viewers default to an unclipped circular-first layout', () => {
    const toolkitSource = readFileSync(TOOLKIT_PATH, 'utf8');
    const viewerSource = readFileSync(VIEWER_PATH, 'utf8');

    assert.match(toolkitSource, /useState<ViewMode>\('circular'\)/);
    assert.match(toolkitSource, /GC track visibility state[\s\S]*useState\(false\)/);
    assert.match(viewerSource, /overflowY: resolvedViewerMode === 'both' \? 'auto' : 'hidden'/);
});

test('SeqViz pointer drags publish one committed selection after pointer-up', () => {
    const source = readFileSync(VIEWER_PATH, 'utf8');

    assert.match(source, /pendingPointerSelectionRef/);
    assert.match(source, /flushPendingPointerSelection/);
    assert.match(source, /window\.requestAnimationFrame\(flushPendingPointerSelection\)/);
    assert.doesNotMatch(source, /if \(sourceSelection\) \{\s*onSelection\(sourceSelection\);\s*\}/);
});

test('sequence viewer does not add a separate linear drag overlay over SeqViz', () => {
    const source = readFileSync(VIEWER_PATH, 'utf8');

    assert.doesNotMatch(source, /data-linear-range-navigator/);
    assert.doesNotMatch(source, /Linear range drag/);
    assert.match(source, /onSelection=\{\(sel\) => \{/);
});

test('sequence viewer remount key changes when RNA/DNA identity changes at same length', () => {
    const source = readFileSync(VIEWER_PATH, 'utf8');

    assert.match(source, /viewerSequenceKey/);
    assert.match(source, /seqVizSeqType = normalizedSequenceType === 'protein' \? 'aa' : nucleotideSequenceType/);
    assert.match(source, /seqType=\{seqVizSeqType\}/);
    assert.match(source, /sequenceData\.sequenceType/);
    assert.match(source, /displaySequence\.slice\(0, 24\)/);
    assert.match(source, /key=\{`\$\{viewerSequenceKey\}:selection-reset-\$\{selectionResetVersion\}`\}/);
});

test('sequence header exposes focus and panel collapse actions', () => {
    const source = readFileSync(HEADER_PATH, 'utf8');

    assert.match(source, /onToggleFullscreen/);
    assert.match(source, /onToggleLibraryPanel/);
    assert.match(source, /onToggleToolPanel/);
    assert.match(source, /Focus Viewer/);
    assert.match(source, /Hide Shelf|Show Shelf/);
    assert.match(source, /Hide Tools|Show Tools/);
});

test('sequence header source keeps the plasmid toolbar horizontally scrollable on narrow screens', () => {
    const source = readFileSync(HEADER_PATH, 'utf8');

    assert.match(source, /data-sequence-header-scroll/);
    assert.match(source, /overflow-x-auto/);
    assert.match(source, /min-w-max/);
});

test('mol bio toolkit source gives mobile resize handles touch-safe hit targets', () => {
    const source = readFileSync(TOOLKIT_PATH, 'utf8');

    assert.match(source, /touch-none/);
    assert.match(source, /w-4/);
    assert.match(source, /md:w-1\.5/);
});
