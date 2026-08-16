/**
 * useThemeColors - Hook to get current theme colors for Plotly charts
 *
 * Reads CSS custom properties from the document and provides them
 * as JavaScript values for Plotly layout configuration.
 */

import { useMemo } from 'react';
import { useTheme } from './themeContext';

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
    bgPrimary: '#0f172a',
    bgSecondary: '#1e293b',
    bgTertiary: '#334155',
    textPrimary: '#f1f5f9',
    textSecondary: '#94a3b8',
    textMuted: '#64748b',
    accentPrimary: '#a855f7',
    accentSecondary: '#ec4899',
    borderPrimary: '#334155',
    success: '#22c55e',
    warning: '#f59e0b',
    error: '#ef4444',
    link: '#60a5fa',
};

function getCSSVariable(name: string, fallback: string): string {
    if (typeof window === 'undefined') return fallback;
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
}

export function useThemeColors(): ThemeColors {
    const { theme } = useTheme();

    // Recompute when theme changes
    return useMemo(() => {
        void theme;
        return {
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
        };
    }, [theme]);
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
