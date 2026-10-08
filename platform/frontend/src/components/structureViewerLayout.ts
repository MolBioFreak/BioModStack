export const STRUCTURE_VIEWER_STACKED_LAYOUT_BREAKPOINT = 768;
export const STRUCTURE_VIEWER_DEFAULT_HEIGHT = 450;
export const STRUCTURE_VIEWER_COMPACT_HEIGHT = 360;

export interface ResolveStructureViewerLayoutOptions {
    viewportWidth: number;
    isFullscreen: boolean;
}

export interface ResolvedStructureViewerLayout {
    isStacked: boolean;
    viewerHeight: number;
}

export function shouldStackStructureViewerPanelsForViewport(viewportWidth: number): boolean {
    if (!Number.isFinite(viewportWidth)) {
        return false;
    }
    return Math.round(viewportWidth) < STRUCTURE_VIEWER_STACKED_LAYOUT_BREAKPOINT;
}

export function resolveStructureViewerLayout({
    viewportWidth,
    isFullscreen,
}: ResolveStructureViewerLayoutOptions): ResolvedStructureViewerLayout {
    const isStacked = !isFullscreen && shouldStackStructureViewerPanelsForViewport(viewportWidth);
    return {
        isStacked,
        viewerHeight: isStacked ? STRUCTURE_VIEWER_COMPACT_HEIGHT : STRUCTURE_VIEWER_DEFAULT_HEIGHT,
    };
}
