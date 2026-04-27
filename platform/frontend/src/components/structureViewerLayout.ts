export const STRUCTURE_VIEWER_STACKED_LAYOUT_BREAKPOINT = 768;
export const STRUCTURE_VIEWER_DEFAULT_HEIGHT = 450;
export const STRUCTURE_VIEWER_COMPACT_HEIGHT = 360;
export const STRUCTURE_VIEWER_FULLSCREEN_ANALYTICS_DESKTOP_MAX_HEIGHT = 720;
export const STRUCTURE_VIEWER_FULLSCREEN_ANALYTICS_COMPACT_MAX_HEIGHT = 420;
export const STRUCTURE_VIEWER_FULLSCREEN_ANALYTICS_COMPACT_VERTICAL_MARGIN = 96;
export const STRUCTURE_VIEWER_FULLSCREEN_ANALYTICS_DESKTOP_VERTICAL_MARGIN = 32;

export interface ResolveStructureViewerLayoutOptions {
    viewportWidth: number;
    isFullscreen: boolean;
}

export interface ResolvedStructureViewerLayout {
    isStacked: boolean;
    viewerHeight: number;
}

export interface ResolveStructureViewerFullscreenAnalyticsOptions {
    viewportWidth: number;
    viewportHeight: number;
}

export interface ResolvedStructureViewerFullscreenAnalyticsLayout {
    mode: 'compact' | 'sidebar';
    frameClassName: string;
    panelClassName: string;
    contentClassName: string;
    panelMaxHeight: number;
}

function normalizeViewportDimension(value: number, fallback: number): number {
    if (!Number.isFinite(value) || value <= 0) {
        return fallback;
    }
    return Math.round(value);
}

export function shouldStackStructureViewerPanelsForViewport(viewportWidth: number): boolean {
    if (!Number.isFinite(viewportWidth)) {
        return false;
    }
    return Math.round(viewportWidth) < STRUCTURE_VIEWER_STACKED_LAYOUT_BREAKPOINT;
}

export function shouldUseCompactFullscreenAnalytics({
    viewportWidth,
    viewportHeight,
}: ResolveStructureViewerFullscreenAnalyticsOptions): boolean {
    const width = normalizeViewportDimension(viewportWidth, 1280);
    const height = normalizeViewportDimension(viewportHeight, 720);
    return Math.min(width, height) < STRUCTURE_VIEWER_STACKED_LAYOUT_BREAKPOINT;
}

export function resolveStructureViewerFullscreenAnalyticsLayout({
    viewportWidth,
    viewportHeight,
}: ResolveStructureViewerFullscreenAnalyticsOptions): ResolvedStructureViewerFullscreenAnalyticsLayout {
    const height = normalizeViewportDimension(viewportHeight, 720);
    const compact = shouldUseCompactFullscreenAnalytics({ viewportWidth, viewportHeight });

    if (compact) {
        return {
            mode: 'compact',
            frameClassName: 'absolute inset-x-2 bottom-2 z-40',
            panelClassName: 'flex w-full flex-col rounded-lg border border-slate-700/60 bg-slate-900/90 shadow-2xl shadow-black/40 backdrop-blur-md overflow-hidden',
            contentClassName: 'overflow-y-auto p-2',
            panelMaxHeight: Math.max(
                220,
                Math.min(
                    STRUCTURE_VIEWER_FULLSCREEN_ANALYTICS_COMPACT_MAX_HEIGHT,
                    height - STRUCTURE_VIEWER_FULLSCREEN_ANALYTICS_COMPACT_VERTICAL_MARGIN,
                ),
            ),
        };
    }

    return {
        mode: 'sidebar',
        frameClassName: 'absolute bottom-4 right-4 z-40',
        panelClassName: 'flex w-80 flex-col rounded-lg border border-slate-700/50 bg-slate-900/80 shadow-2xl shadow-black/30 backdrop-blur-sm overflow-hidden',
        contentClassName: 'overflow-y-auto p-3',
        panelMaxHeight: Math.max(
            320,
            Math.min(
                STRUCTURE_VIEWER_FULLSCREEN_ANALYTICS_DESKTOP_MAX_HEIGHT,
                height - STRUCTURE_VIEWER_FULLSCREEN_ANALYTICS_DESKTOP_VERTICAL_MARGIN,
            ),
        ),
    };
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
