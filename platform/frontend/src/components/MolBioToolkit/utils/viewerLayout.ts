export type MolBioActivePanel =
    | 'view'
    | 'history'
    | 'assembly'
    | 'align'
    | 'digest'
    | 'pcr'
    | 'primers'
    | 'rna'
    | 'features'
    | 'edit'
    | 'search'
    | null;

export interface MolBioPanelWidthBounds {
    min: number;
    max: number;
}

export interface ResolveMolBioViewerLayoutOptions {
    activePanel: MolBioActivePanel;
    viewportWidth: number;
    leftPanelWidth: number;
    rightPanelWidth: number;
    isViewerFullscreen: boolean;
    isLibraryPanelCollapsed: boolean;
    isToolPanelCollapsed: boolean;
}

export interface ResolvedMolBioViewerLayout {
    leftPanelWidth: number;
    rightPanelWidth: number;
    leftPanelBounds: MolBioPanelWidthBounds;
    rightPanelBounds: MolBioPanelWidthBounds;
    showLibraryPanel: boolean;
    showToolPanel: boolean;
    showLibraryResizeHandle: boolean;
    showToolResizeHandle: boolean;
}

export const MOLBIO_LIBRARY_PANEL_DEFAULT_WIDTH = 256;
export const MOLBIO_VIEWER_MIN_WIDTH = 320;
export const MOLBIO_MIN_VIEWPORT_FOR_OPEN_SIDE_PANELS = MOLBIO_VIEWER_MIN_WIDTH + 224 + 256;

export function shouldCollapseMolBioPanelsForViewport(viewportWidth: number): boolean {
    if (!Number.isFinite(viewportWidth)) {
        return false;
    }
    return Math.round(viewportWidth) < MOLBIO_MIN_VIEWPORT_FOR_OPEN_SIDE_PANELS;
}

export function getDefaultMolBioToolPanelWidth(activePanel: MolBioActivePanel): number {
    if (activePanel === 'primers') return 480;
    if (activePanel === 'assembly') return 544;
    if (activePanel === 'align' || activePanel === 'rna' || activePanel === 'history') return 416;
    return 288;
}

export function clampMolBioPanelWidth(width: number, bounds: MolBioPanelWidthBounds): number {
    if (!Number.isFinite(width)) {
        return bounds.min;
    }
    return Math.min(bounds.max, Math.max(bounds.min, Math.round(width)));
}

export function getMolBioPanelBounds(side: 'left' | 'right', viewportWidth: number): MolBioPanelWidthBounds {
    const safeViewportWidth = Number.isFinite(viewportWidth) ? Math.max(960, Math.round(viewportWidth)) : 1440;
    if (side === 'left') {
        return {
            min: 224,
            max: Math.min(480, Math.max(320, safeViewportWidth - 480)),
        };
    }
    return {
        min: 256,
        max: Math.min(640, Math.max(384, safeViewportWidth - 320)),
    };
}

export function resolveMolBioViewerLayout({
    activePanel,
    viewportWidth,
    leftPanelWidth,
    rightPanelWidth,
    isViewerFullscreen,
    isLibraryPanelCollapsed,
    isToolPanelCollapsed,
}: ResolveMolBioViewerLayoutOptions): ResolvedMolBioViewerLayout {
    const safeViewportWidth = Number.isFinite(viewportWidth) ? Math.max(960, Math.round(viewportWidth)) : 1440;
    const leftPanelBounds = getMolBioPanelBounds('left', safeViewportWidth);
    const rightPanelBounds = getMolBioPanelBounds('right', safeViewportWidth);
    const defaultRightPanelWidth = getDefaultMolBioToolPanelWidth(activePanel);
    const showLibraryPanel = !isViewerFullscreen && !isLibraryPanelCollapsed;
    const showToolPanel = !isViewerFullscreen && !isToolPanelCollapsed;
    let resolvedLeftPanelWidth = clampMolBioPanelWidth(leftPanelWidth, leftPanelBounds);
    let resolvedRightPanelWidth = isViewerFullscreen
        ? clampMolBioPanelWidth(defaultRightPanelWidth, rightPanelBounds)
        : clampMolBioPanelWidth(rightPanelWidth, rightPanelBounds);

    if (showLibraryPanel && showToolPanel) {
        const panelBudget = safeViewportWidth - MOLBIO_VIEWER_MIN_WIDTH;
        let overflow = resolvedLeftPanelWidth + resolvedRightPanelWidth - panelBudget;

        if (overflow > 0) {
            const shrinkRight = Math.min(overflow, resolvedRightPanelWidth - rightPanelBounds.min);
            resolvedRightPanelWidth -= shrinkRight;
            overflow -= shrinkRight;
        }

        if (overflow > 0) {
            const shrinkLeft = Math.min(overflow, resolvedLeftPanelWidth - leftPanelBounds.min);
            resolvedLeftPanelWidth -= shrinkLeft;
        }
    }

    return {
        leftPanelBounds,
        rightPanelBounds,
        leftPanelWidth: resolvedLeftPanelWidth,
        rightPanelWidth: resolvedRightPanelWidth,
        showLibraryPanel,
        showToolPanel,
        showLibraryResizeHandle: showLibraryPanel,
        showToolResizeHandle: showToolPanel,
    };
}
