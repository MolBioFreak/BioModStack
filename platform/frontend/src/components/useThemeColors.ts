/**
 * useThemeColors - Hook to get current theme colors for Plotly charts
 *
 * Reads CSS custom properties from the document and provides them
 * as JavaScript values for Plotly layout configuration.
 */

import { useEffect, useMemo, useState } from 'react';

export interface ThemeColors {
    bgPrimary: string;
    bgSecondary: string;
    bgTertiary: string;
    textPrimary: string;
    textSecondary: string;
    textMuted: string;
    accentPrimary: string;
    accentSecondary: string;
    borderPrimary: string;
    success: string;
    warning: string;
    error: string;
    link: string;
}

// Fallback colors (midnight theme) in case CSS vars aren't available
const FALLBACK_COLORS: ThemeColors = {
    bgPrimary: '#0b1020',
    bgSecondary: '#151e33',
    bgTertiary: '#1b2740',
    textPrimary: '#eef4ff',
    textSecondary: '#a6b3ca',
    textMuted: '#939fb6',
    accentPrimary: '#83a7ff',
    accentSecondary: '#b28cff',
    borderPrimary: '#293a59',
    success: '#54d8a2',
    warning: '#ffb84a',
    error: '#ff6b78',
    link: '#83a7ff',
};

function getCSSVariable(name: string, fallback: string): string {
    if (typeof window === 'undefined') return fallback;
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

export function useThemeColors(): ThemeColors {
    const read = (): ThemeColors => ({
        bgPrimary: getCSSVariable('--bg-primary', FALLBACK_COLORS.bgPrimary),
        bgSecondary: getCSSVariable('--bg-secondary', FALLBACK_COLORS.bgSecondary),
        bgTertiary: getCSSVariable('--bg-tertiary', FALLBACK_COLORS.bgTertiary),
        textPrimary: getCSSVariable('--text-primary', FALLBACK_COLORS.textPrimary),
        textSecondary: getCSSVariable('--text-secondary', FALLBACK_COLORS.textSecondary),
        textMuted: getCSSVariable('--text-muted', FALLBACK_COLORS.textMuted),
        accentPrimary: getCSSVariable('--accent-primary', FALLBACK_COLORS.accentPrimary),
        accentSecondary: getCSSVariable('--accent-secondary', FALLBACK_COLORS.accentSecondary),
        borderPrimary: getCSSVariable('--border-primary', FALLBACK_COLORS.borderPrimary),
        success: getCSSVariable('--success', FALLBACK_COLORS.success),
        warning: getCSSVariable('--warning', FALLBACK_COLORS.warning),
        error: getCSSVariable('--error', FALLBACK_COLORS.error),
        link: getCSSVariable('--link', FALLBACK_COLORS.link),
    });
    const [colors, setColors] = useState(read);
    useEffect(() => {
        const refresh = () => setColors(read());
        // Read after the root attribute changes; reading during a theme-context
        // render can capture the previous palette before ThemeProvider applies it.
        const observer = new MutationObserver(refresh);
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] });
        refresh();
        return () => observer.disconnect();
    }, []);
    return colors;
}

/**
 * Creates a Plotly layout configuration with theme-aware colors
 */
export function useThemePlotlyLayout() {
    const colors = useThemeColors();

    return useMemo(() => ({
        paper_bgcolor: 'transparent',
        plot_bgcolor: colors.bgSecondary,
        font: { color: colors.textPrimary },
        title: { font: { color: colors.textPrimary } },
        xaxis: {
            gridcolor: colors.borderPrimary,
            color: colors.textSecondary,
            title: { font: { color: colors.textSecondary } },
        },
        yaxis: {
            gridcolor: colors.borderPrimary,
            color: colors.textSecondary,
            title: { font: { color: colors.textSecondary } },
        },
        colorway: [
            colors.accentPrimary,
            colors.accentSecondary,
            colors.success,
            colors.warning,
            colors.link,
            colors.error,
        ],
    }), [colors]);
}
