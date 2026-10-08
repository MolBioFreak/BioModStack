import assert from 'node:assert/strict';
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
    assert.equal(normal.rightPanelWidth, 628);
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

    assert.equal(layout.leftPanelWidth, 372);
    assert.equal(layout.rightPanelWidth, 256);
    assert.equal(960 - layout.leftPanelWidth - layout.rightPanelWidth - 12, MOLBIO_VIEWER_MIN_WIDTH);
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

test('desktop-to-narrow resize retains a usable viewer and permits explicit overlay tools', () => {
    for (const width of [390, 640, 800]) {
        const layout = resolveMolBioViewerLayout({
            activePanel: 'features', viewportWidth: width, leftPanelWidth: 256,
            rightPanelWidth: 288, isViewerFullscreen: false,
            isLibraryPanelCollapsed: false, isToolPanelCollapsed: false,
        });
        assert.equal(layout.overlayPanels, true);
        assert.equal(layout.showLibraryResizeHandle, false);
        assert.equal(layout.showToolResizeHandle, false);
        assert.equal(layout.showToolPanel, true, 'explicitly opened tools remain available as an overlay');
    }

});

test('wide layouts budget both resize handles and preserve manual panel visibility', () => {
    for (const width of [832, 960, 1024, 1440]) {
        for (const leftCollapsed of [false, true]) {
            for (const rightCollapsed of [false, true]) {
                const layout = resolveMolBioViewerLayout({
                    activePanel: 'assembly', viewportWidth: width, leftPanelWidth: 480,
                    rightPanelWidth: 640, isViewerFullscreen: false,
                    isLibraryPanelCollapsed: leftCollapsed, isToolPanelCollapsed: rightCollapsed,
                });
                const consumed = (layout.showLibraryPanel ? layout.leftPanelWidth + 6 : 0)
                    + (layout.showToolPanel ? layout.rightPanelWidth + 6 : 0);
                assert.ok(width - consumed >= MOLBIO_VIEWER_MIN_WIDTH);
                assert.equal(layout.showLibraryPanel, !leftCollapsed);
                assert.equal(layout.showToolPanel, !rightCollapsed);
            }
        }
    }
});
