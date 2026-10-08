/**
 * AnalyticsDashboard - Multi-chart dashboard view for DOE analysis
 * 
 * Displays 12+ charts simultaneously in a responsive grid layout
 * with 2 custom chart builders (2D and 3D) at the bottom.
 */

import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Data, PlotSelectionEvent, PlotMouseEvent } from 'plotly.js';
import { fetchChainMetrics, fetchPAEData } from '../lib/api';

interface Design {
    id: string;
    name: string;
    plddt_overall: number | null;
    plddt_binder: number | null;
    pae_overall: number | null;
    pae_interaction: number | null;
    ptm: number | null;
    iptm: number | null;
    conf_score: number | null;
    affinity_score: number | null;
    binder_probability: number | null;
    rmsd_binder: number | null;
    rog: number | null;
    mpnn_score: number | null;
    cdr_h1_length?: number | null;
    cdr_h2_length?: number | null;
    cdr_h3_length?: number | null;
    binder_length?: number | null;
    epitope_contact_count?: number | null;
}

interface AnalyticsDashboardProps {
    designs: Design[];
    jobName?: string;
}

interface ChainMetric {
    type: string;
    length: number;
    avg_plddt?: number;
    plddt: number[];
    residue_numbers?: number[];
}

// Available metrics
const METRICS = [
    { key: 'plddt_overall', label: 'pLDDT', color: '#60a5fa' },
    { key: 'pae_overall', label: 'PAE', color: '#fbbf24' },
    { key: 'ptm', label: 'pTM', color: '#a78bfa' },
    { key: 'iptm', label: 'iPTM', color: '#8b5cf6' },
    { key: 'conf_score', label: 'Confidence', color: '#34d399' },
    { key: 'affinity_score', label: 'Affinity', color: '#10b981' },
    { key: 'binder_probability', label: 'Binder Prob.', color: '#22c55e' },
    { key: 'rog', label: 'RoG', color: '#ec4899' },
    { key: 'mpnn_score', label: 'MPNN', color: '#14b8a6' },
] as const;

type MetricKey = typeof METRICS[number]['key'];

// Color schemes for charts
const COLOR_SCHEMES = {
    viridis: { name: 'Viridis', scale: 'Viridis', bars: ['#440154', '#482878', '#3e4a89', '#31688e', '#26838f', '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725'] },
    plasma: { name: 'Plasma', scale: 'Plasma', bars: ['#0d0887', '#46039f', '#7201a8', '#9c179e', '#bd3786', '#d8576b', '#ed7953', '#fb9f3a', '#fdca26', '#f0f921'] },
    inferno: { name: 'Inferno', scale: 'Inferno', bars: ['#000004', '#1b0c41', '#4a0c6b', '#781c6d', '#a52c60', '#cf4446', '#ed6925', '#fb9b06', '#f7d13d', '#fcffa4'] },
    turbo: { name: 'Turbo', scale: 'Turbo', bars: ['#30123b', '#4777ef', '#1bd0d5', '#62fc6b', '#d2e935', '#fe9b2d', '#db3a07', '#7a0403'] },
    cool: { name: 'Cool Blues', scale: 'Blues', bars: ['#08306b', '#08519c', '#2171b5', '#4292c6', '#6baed6', '#9ecae1', '#c6dbef', '#deebf7'] },
    warm: { name: 'Warm Reds', scale: 'Reds', bars: ['#67000d', '#a50f15', '#cb181d', '#ef3b2c', '#fb6a4a', '#fc9272', '#fcbba1', '#fee0d2'] },
    earth: { name: 'Earth Tones', scale: 'Earth', bars: ['#543005', '#8c510a', '#bf812d', '#dfc27d', '#80cdc1', '#35978f', '#01665e', '#003c30'] },
    neon: { name: 'Neon', scale: 'Electric', bars: ['#ff00ff', '#ff00aa', '#ff0055', '#ff5500', '#ffaa00', '#aaff00', '#00ff55', '#00ffaa', '#00ffff'] },
} as const;

type ColorScheme = keyof typeof COLOR_SCHEMES;

// Shared layout config
const CHART_BG = 'transparent';
const PLOT_BG = '#1e293b';
const FONT_COLOR = '#e2e8f0';
const GRID_COLOR = '#334155';
const AXIS_COLOR = '#94a3b8';

// Extract numeric values from designs
const extractValues = (designs: Design[], key: string): number[] => {
    return designs
        .map(d => (d as unknown as Record<string, unknown>)[key])
        .filter((v): v is number => v != null && typeof v === 'number');
};

interface ChartCardProps {
    title: string;
    hasData?: boolean;
    isHidden?: boolean;
    onToggleHidden?: () => void;
    children: React.ReactNode;
}

function ChartCard({ title, hasData = true, isHidden = false, onToggleHidden, children }: ChartCardProps) {
    const cardRef = useRef<HTMLDivElement>(null);
    const [isFullscreen, setIsFullscreen] = useState(false);

    // Toggle native browser fullscreen
    const toggleFullscreen = useCallback(() => {
        if (!cardRef.current) return;

        if (!document.fullscreenElement) {
            cardRef.current.requestFullscreen().catch(err => {
                console.error('Failed to enter fullscreen:', err);
            });
        } else {
            document.exitFullscreen();
        }
    }, []);

    // Listen to fullscreen changes
    useEffect(() => {
        const handleFullscreenChange = () => {
            setIsFullscreen(!!document.fullscreenElement);
        };
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
    }, []);

    // Eye icon (visible)
    const EyeIcon = () => (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
        </svg>
    );

    // Eye-off icon (hidden)
    const EyeOffIcon = () => (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
        </svg>
    );

    // Expand icon
    const ExpandIcon = () => (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
        </svg>
    );

    return (
        <div
            ref={cardRef}
            className={`bg-slate-800/50 rounded-xl border border-slate-700/50 overflow-hidden transition-all ${isHidden ? 'opacity-60' : ''} ${!hasData ? 'border-amber-700/30' : ''} ${isFullscreen ? 'fixed inset-0 z-50 bg-slate-900 rounded-none' : ''}`}
        >
            <div className="px-3 py-2 bg-slate-800/80 border-b border-slate-700/50 flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <h3 className={`font-medium text-slate-300 ${isFullscreen ? 'text-lg' : 'text-sm'}`}>{title}</h3>
                    {!hasData && (
                        <span className="px-1.5 py-0.5 text-xs bg-amber-900/50 text-amber-400 rounded">No data</span>
                    )}
                </div>
                <div className="flex items-center gap-1">
                    {!isFullscreen && (
                        <button
                            onClick={onToggleHidden}
                            className="p-1.5 rounded hover:bg-slate-700/50 text-slate-400 hover:text-slate-200 transition-colors"
                            title={isHidden ? "Show chart" : "Hide chart"}
                        >
                            {isHidden ? <EyeOffIcon /> : <EyeIcon />}
                        </button>
                    )}
                    {hasData && (
                        <button
                            onClick={toggleFullscreen}
                            className={`p-1.5 rounded hover:bg-slate-700/50 text-slate-400 hover:text-slate-200 transition-colors ${isFullscreen ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30' : ''}`}
                            title={isFullscreen ? "Exit fullscreen" : "Expand to fullscreen"}
                        >
                            {isFullscreen ? <span className="text-sm">✕</span> : <ExpandIcon />}
                        </button>
                    )}
                </div>
            </div>
            {!isHidden && (
                <div
                    className={`p-2 ${isFullscreen ? 'flex-1 flex flex-col' : ''}`}
                    style={isFullscreen ? { height: 'calc(100vh - 48px)' } : undefined}
                >
                    <div className={isFullscreen ? 'flex-1 [&_.js-plotly-plot]:!h-full [&_.plotly]:!h-full [&_.svg-container]:!h-full' : ''}>
                        {children}
                    </div>
                </div>
            )}
        </div>
    );
}

// Fullscreen modal for expanded charts
interface ExpandedChartModalProps {
    title: string;
    onClose: () => void;
    children: React.ReactNode;
}

function ExpandedChartModal({ title, onClose, children }: ExpandedChartModalProps) {
    // Close on Escape key
    useEffect(() => {
        const handleEsc = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', handleEsc);
        return () => window.removeEventListener('keydown', handleEsc);
    }, [onClose]);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm" onClick={onClose}>
            <div
                className="bg-slate-900 rounded-xl border border-slate-700 w-[95vw] h-[90vh] flex flex-col overflow-hidden"
                onClick={e => e.stopPropagation()}
            >
                <div className="px-4 py-3 bg-slate-800 border-b border-slate-700 flex items-center justify-between">
                    <h2 className="text-lg font-semibold text-slate-200">{title}</h2>
                    <button
                        onClick={onClose}
                        className="p-2 rounded hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
                    >
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>
                <div className="flex-1 p-4">
                    {children}
                </div>
            </div>
        </div>
    );
}

export function AnalyticsDashboard({ designs, jobName }: AnalyticsDashboardProps) {
    // Global color scheme
    const [colorScheme, setColorScheme] = useState<ColorScheme>('viridis');

    // Selected design for per-residue view
    const [selectedDesignId, setSelectedDesignId] = useState<string>('');

    // Auto-select first design
    const sortedDesigns = useMemo(() =>
        [...designs].sort((a, b) => (b.plddt_overall ?? 0) - (a.plddt_overall ?? 0)),
        [designs]
    );

    // Fetch chain metrics for selected design
    const { data: chainMetricsData, isLoading: chainLoading } = useQuery({
        queryKey: ['chainMetrics', selectedDesignId || sortedDesigns[0]?.id],
        queryFn: () => fetchChainMetrics(selectedDesignId || sortedDesigns[0]?.id || ''),
        enabled: !!(selectedDesignId || sortedDesigns[0]?.id),
    });
    const chainMetrics = chainMetricsData?.data as Record<string, ChainMetric> | null;

    // Fetch PAE matrix for selected design (for heatmap)
    const { data: paeData, isLoading: paeLoading } = useQuery({
        queryKey: ['paeData', selectedDesignId || sortedDesigns[0]?.id],
        queryFn: () => fetchPAEData(selectedDesignId || sortedDesigns[0]?.id || ''),
        enabled: !!(selectedDesignId || sortedDesigns[0]?.id),
        staleTime: 60000,
    });
    const paeMatrix = paeData?.data;

    // Custom chart state
    const [custom2dX, setCustom2dX] = useState<MetricKey>('plddt_overall');
    const [custom2dY, setCustom2dY] = useState<MetricKey>('pae_overall');
    const [custom2dColor, setCustom2dColor] = useState<MetricKey>('iptm');
    const [custom3dX, setCustom3dX] = useState<MetricKey>('plddt_overall');
    const [custom3dY, setCustom3dY] = useState<MetricKey>('iptm');
    const [custom3dZ, setCustom3dZ] = useState<MetricKey>('pae_overall');
    const [custom3dColor, setCustom3dColor] = useState<MetricKey>('conf_score');

    // Chart visibility state
    const [hiddenCharts, setHiddenCharts] = useState<Set<string>>(new Set());
    const [expandedChart, setExpandedChart] = useState<string | null>(null);

    // Selection state for chart interactions
    const [selectedDesigns, setSelectedDesigns] = useState<Set<string>>(new Set());

    // Toggle chart visibility
    const toggleHidden = useCallback((id: string) => {
        setHiddenCharts(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    // Handle 2D chart lasso/box selection
    const handlePlotlySelect = useCallback((event: PlotSelectionEvent | null) => {
        if (!event || !event.points || event.points.length === 0) return;

        const newSelection = new Set<string>();
        event.points.forEach(pt => {
            // The 'text' field contains the design name (set in hovertext)
            const name = (pt as unknown as { text?: string }).text;
            if (name) newSelection.add(name);
        });

        // Add to existing selection (use Shift key behavior)
        setSelectedDesigns(prev => {
            const merged = new Set(prev);
            newSelection.forEach(n => merged.add(n));
            return merged;
        });
    }, []);

    // Handle 3D chart click selection
    const handlePlotlyClick = useCallback((event: PlotMouseEvent) => {
        if (!event || !event.points || event.points.length === 0) return;

        const clickedName = (event.points[0] as unknown as { text?: string }).text;
        if (!clickedName) return;

        // Toggle selection on click
        setSelectedDesigns(prev => {
            const next = new Set(prev);
            if (next.has(clickedName)) {
                next.delete(clickedName);
            } else {
                next.add(clickedName);
            }
            return next;
        });
    }, []);

    // Clear all selections
    const clearSelection = useCallback(() => {
        setSelectedDesigns(new Set());
    }, []);

    // Check if data exists for a metric combination
    const hasScatterData = useCallback((xKey: MetricKey, yKey: MetricKey) => {
        return designs.some(d => {
            const rec = d as unknown as Record<string, unknown>;
            return typeof rec[xKey] === 'number' && typeof rec[yKey] === 'number';
        });
    }, [designs]);

    const hasHistogramData = useCallback((key: MetricKey) => {
        return extractValues(designs, key).length > 0;
    }, [designs]);

    const has3DData = useCallback((xKey: MetricKey, yKey: MetricKey, zKey: MetricKey) => {
        return designs.some(d => {
            const rec = d as unknown as Record<string, unknown>;
            return typeof rec[xKey] === 'number' && typeof rec[yKey] === 'number' && typeof rec[zKey] === 'number';
        });
    }, [designs]);

    // Get current color scheme
    const scheme = COLOR_SCHEMES[colorScheme];

    // Helper to get label
    const getLabel = (key: MetricKey) => METRICS.find(m => m.key === key)?.label || key;

    // Scatter data generator
    const makeScatter = (xKey: MetricKey, yKey: MetricKey, colorKey?: MetricKey): Data[] => {
        const xVals: number[] = [];
        const yVals: number[] = [];
        const colorVals: number[] = [];
        const names: string[] = [];

        designs.forEach(d => {
            const x = (d as unknown as Record<string, unknown>)[xKey];
            const y = (d as unknown as Record<string, unknown>)[yKey];
            if (typeof x === 'number' && typeof y === 'number') {
                xVals.push(x);
                yVals.push(y);
                if (colorKey) {
                    const c = (d as unknown as Record<string, unknown>)[colorKey];
                    colorVals.push(typeof c === 'number' ? c : 0);
                }
                names.push(d.name);
            }
        });

        if (xVals.length === 0) return [];

        return [{
            type: 'scatter',
            mode: 'markers',
            x: xVals,
            y: yVals,
            text: names,
            hovertemplate: `<b>%{text}</b><br>${getLabel(xKey)}: %{x:.2f}<br>${getLabel(yKey)}: %{y:.2f}<extra></extra>`,
            marker: colorKey ? {
                size: 8,
                color: colorVals,
                colorscale: scheme.scale,
                showscale: true,
                colorbar: { title: { text: getLabel(colorKey) }, thickness: 12, len: 0.6 },
                line: { color: '#ffffff30', width: 0.5 },
            } : { size: 8, color: scheme.bars[3] },
        }];
    };

    // 3D Scatter generator
    const make3DScatter = (xKey: MetricKey, yKey: MetricKey, zKey: MetricKey, colorKey: MetricKey): Data[] => {
        const xVals: number[] = [];
        const yVals: number[] = [];
        const zVals: number[] = [];
        const colorVals: number[] = [];
        const names: string[] = [];

        designs.forEach(d => {
            const rec = d as unknown as Record<string, unknown>;
            const x = rec[xKey];
            const y = rec[yKey];
            const z = rec[zKey];
            const c = rec[colorKey];
            if (typeof x === 'number' && typeof y === 'number' && typeof z === 'number') {
                xVals.push(x);
                yVals.push(y);
                zVals.push(z);
                colorVals.push(typeof c === 'number' ? c : 0);
                names.push(d.name);
            }
        });

        if (xVals.length === 0) return [];

        return [{
            type: 'scatter3d',
            mode: 'markers',
            x: xVals,
            y: yVals,
            z: zVals,
            text: names,
            hovertemplate: `<b>%{text}</b><br>${getLabel(xKey)}: %{x:.2f}<br>${getLabel(yKey)}: %{y:.2f}<br>${getLabel(zKey)}: %{z:.2f}<extra></extra>`,
            marker: {
                size: 6,
                color: colorVals,
                colorscale: scheme.scale,
                showscale: true,
                colorbar: { title: { text: getLabel(colorKey) }, thickness: 12, len: 0.5 },
            },
        }];
    };

    // Histogram generator - with gradient fill and better styling
    const makeHistogram = (key: MetricKey, colorIndex: number): Data[] => {
        const values = extractValues(designs, key);
        if (values.length === 0) return [];
        const barColor = scheme.bars[colorIndex % scheme.bars.length];
        return [{
            type: 'histogram',
            x: values,
            marker: {
                color: barColor,
                line: { color: '#ffffff20', width: 1 },
                opacity: 0.85,
            },
            nbinsx: 25,
            hovertemplate: `<b>${getLabel(key)}</b><br>Value: %{x:.2f}<br>Count: %{y}<extra></extra>`,
        } as Data];
    };

    // Violin generator
    const makeViolin = (keys: MetricKey[]): Data[] => {
        return keys.map(key => {
            const values = extractValues(designs, key);
            const metric = METRICS.find(m => m.key === key);
            return {
                type: 'violin' as const,
                y: values,
                name: metric?.label || key,
                marker: { color: metric?.color || '#60a5fa' },
                box: { visible: true },
                meanline: { visible: true },
            };
        }).filter(d => (d.y as number[]).length > 0);
    };

    // Parallel coordinates - improved for readability
    const parallelCoordsData = useMemo((): Data[] => {
        const metricsWithData = METRICS.filter(m => extractValues(designs, m.key).length > 3);
        if (metricsWithData.length < 3) return [];

        // Limit to top 50 designs by pLDDT for performance and readability
        const topDesigns = [...designs]
            .filter(d => typeof d.plddt_overall === 'number')
            .sort((a, b) => (b.plddt_overall || 0) - (a.plddt_overall || 0))
            .slice(0, 50);

        if (topDesigns.length < 5) return [];

        const dimensions = metricsWithData.slice(0, 6).map(m => {  // Limit to 6 axes for readability
            const values = topDesigns.map(d => {
                const val = (d as unknown as Record<string, unknown>)[m.key];
                return typeof val === 'number' ? val : null;
            });
            const validValues = values.filter((v): v is number => v !== null);
            const minVal = validValues.length > 0 ? Math.min(...validValues) : 0;
            const maxVal = validValues.length > 0 ? Math.max(...validValues) : 1;
            // Truncate long labels
            const shortLabel = m.label.length > 12 ? m.label.substring(0, 10) + '…' : m.label;
            return {
                label: shortLabel,
                values: values.map(v => v ?? minVal),
                range: [minVal, maxVal],
                tickfont: { size: 10, color: '#94a3b8' },
            };
        });

        const colorValues = topDesigns.map(d => typeof d.plddt_overall === 'number' ? d.plddt_overall : 0);

        return [{
            type: 'parcoords',
            line: {
                color: colorValues,
                colorscale: 'Viridis',
                showscale: true,
                colorbar: { title: { text: 'pLDDT', font: { size: 11, color: '#e2e8f0' } }, tickfont: { size: 9, color: '#94a3b8' } },
            },
            dimensions,
            labelfont: { size: 11, color: '#e2e8f0' },
            tickfont: { size: 9, color: '#94a3b8' },
        } as Data];
    }, [designs]);

    // Correlation matrix
    const correlationData = useMemo(() => {
        const metricsWithData = METRICS.filter(m => extractValues(designs, m.key).length > 5);
        if (metricsWithData.length < 3) return null;

        const dataMatrix = metricsWithData.map(m => extractValues(designs, m.key));
        const n = metricsWithData.length;

        const pearson = (x: number[], y: number[]): number => {
            const len = Math.min(x.length, y.length);
            if (len < 2) return 0;
            const xs = x.slice(0, len), ys = y.slice(0, len);
            const xm = xs.reduce((a, b) => a + b, 0) / len;
            const ym = ys.reduce((a, b) => a + b, 0) / len;
            let num = 0, dx2 = 0, dy2 = 0;
            for (let i = 0; i < len; i++) {
                const dx = xs[i] - xm, dy = ys[i] - ym;
                num += dx * dy; dx2 += dx * dx; dy2 += dy * dy;
            }
            const denom = Math.sqrt(dx2 * dy2);
            return denom === 0 ? 0 : num / denom;
        };

        const matrix: number[][] = [];
        for (let i = 0; i < n; i++) {
            matrix[i] = [];
            for (let j = 0; j < n; j++) {
                matrix[i][j] = pearson(dataMatrix[i], dataMatrix[j]);
            }
        }

        return { matrix, labels: metricsWithData.map(m => m.label) };
    }, [designs]);

    // Contour data
    const contourData = useMemo((): Data[] => {
        const xVals = extractValues(designs, 'plddt_overall');
        const yVals = extractValues(designs, 'pae_overall');
        if (xVals.length < 3 || yVals.length < 3) return [];
        return [{
            type: 'histogram2dcontour',
            x: xVals.slice(0, yVals.length),
            y: yVals.slice(0, xVals.length),
            colorscale: 'Viridis',
            contours: { showlabels: true, labelfont: { color: 'white', size: 9 } },
        }];
    }, [designs]);

    // Shared layout mini
    const miniLayout = (title?: string) => ({
        paper_bgcolor: CHART_BG,
        plot_bgcolor: PLOT_BG,
        font: { color: FONT_COLOR, size: 10 },
        margin: { l: 40, r: 20, t: title ? 30 : 10, b: 30 },
        xaxis: { gridcolor: GRID_COLOR, color: AXIS_COLOR },
        yaxis: { gridcolor: GRID_COLOR, color: AXIS_COLOR },
        showlegend: false,
        ...(title && { title: { text: title, font: { size: 12 } } }),
    });

    const mini3DLayout = (xLabel: string, yLabel: string, zLabel: string) => ({
        paper_bgcolor: CHART_BG,
        font: { color: FONT_COLOR, size: 10 },
        margin: { l: 0, r: 0, t: 10, b: 0 },
        scene: {
            xaxis: { title: { text: xLabel }, color: AXIS_COLOR, gridcolor: GRID_COLOR },
            yaxis: { title: { text: yLabel }, color: AXIS_COLOR, gridcolor: GRID_COLOR },
            zaxis: { title: { text: zLabel }, color: AXIS_COLOR, gridcolor: GRID_COLOR },
            bgcolor: PLOT_BG,
        },
    });

    const miniConfig = { responsive: true, displayModeBar: true };
    const chartStyle = { width: '100%', height: '280px' };  // Compact for 4-col grid
    const chart3DStyle = { width: '100%', height: '450px' };
    const scatterStyle = { width: '100%', height: '350px' };  // Medium for 2-col scatter plots

    if (designs.length === 0) {
        return (
            <div className="flex items-center justify-center h-64 text-slate-500">
                No designs available for analysis
            </div>
        );
    }

    return (
        <div className="space-y-6 p-4">
            {/* Header with Color Scheme Selector */}
            <div className="flex items-center justify-between flex-wrap gap-4">
                <h2 className="text-lg font-semibold text-slate-200">
                    Analytics Dashboard {jobName && <span className="text-slate-400 font-normal">— {jobName}</span>}
                </h2>
                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-400">Color Scheme:</span>
                        <select
                            value={colorScheme}
                            onChange={e => setColorScheme(e.target.value as ColorScheme)}
                            className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-white cursor-pointer hover:bg-slate-600 transition-colors"
                        >
                            {Object.entries(COLOR_SCHEMES).map(([key, val]) => (
                                <option key={key} value={key}>{val.name}</option>
                            ))}
                        </select>
                    </div>
                    <div className="flex items-center gap-2 border-l border-slate-700 pl-4">
                        <button
                            onClick={() => setHiddenCharts(new Set())}
                            className="px-2 py-1 text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 rounded transition-colors"
                            title="Show all charts"
                        >
                            Show All
                        </button>
                        <button
                            onClick={() => {
                                const emptyCharts = new Set<string>();
                                if (!hasScatterData('plddt_overall', 'pae_overall')) emptyCharts.add('plddt-pae');
                                if (!hasScatterData('conf_score', 'iptm')) emptyCharts.add('conf-iptm');
                                if (!hasScatterData('affinity_score', 'binder_probability')) emptyCharts.add('affinity-binder');
                                if (!hasScatterData('plddt_overall', 'rog')) emptyCharts.add('plddt-rog');
                                if (!hasHistogramData('plddt_overall')) emptyCharts.add('hist-plddt');
                                if (!hasHistogramData('pae_overall')) emptyCharts.add('hist-pae');
                                if (!hasHistogramData('iptm')) emptyCharts.add('hist-iptm');
                                if (!hasHistogramData('conf_score')) emptyCharts.add('hist-conf');
                                if (!has3DData('plddt_overall', 'iptm', 'pae_overall')) emptyCharts.add('3d-quality');
                                if (!has3DData('affinity_score', 'binder_probability', 'iptm')) emptyCharts.add('3d-binding');
                                setHiddenCharts(emptyCharts);
                            }}
                            className="px-2 py-1 text-xs bg-amber-900/50 hover:bg-amber-800/50 text-amber-300 rounded transition-colors"
                            title="Hide charts with no data"
                        >
                            Hide Empty
                        </button>
                    </div>
                    <div className="text-sm text-slate-400">{designs.length} designs</div>
                </div>
            </div>

            {/* Selected Designs Panel */}
            {selectedDesigns.size > 0 && (
                <div className="bg-emerald-900/30 border border-emerald-700/50 rounded-xl p-4">
                    <div className="flex items-center justify-between mb-2">
                        <h3 className="text-sm font-semibold text-emerald-300">
                            Selected: {selectedDesigns.size} design{selectedDesigns.size > 1 ? 's' : ''}
                        </h3>
                        <button
                            onClick={clearSelection}
                            className="px-2 py-1 text-xs bg-emerald-800/50 hover:bg-emerald-700/50 text-emerald-300 rounded transition-colors"
                        >
                            Clear
                        </button>
                    </div>
                    <div className="flex flex-wrap gap-1 max-h-24 overflow-y-auto">
                        {Array.from(selectedDesigns).map(name => (
                            <span
                                key={name}
                                className="px-2 py-0.5 bg-emerald-800/50 text-emerald-200 text-xs rounded cursor-pointer hover:bg-emerald-700/50"
                                onClick={() => setSelectedDesigns(prev => {
                                    const next = new Set(prev);
                                    next.delete(name);
                                    return next;
                                })}
                                title="Click to remove from selection"
                            >
                                {name}
                            </span>
                        ))}
                    </div>
                </div>
            )}

            {/* Chain-by-Chain pLDDT Profile - Full Width at Top */}
            <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-4">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-base font-semibold text-slate-200">Per-Residue pLDDT Profile</h3>
                    <select
                        value={selectedDesignId || sortedDesigns[0]?.id || ''}
                        onChange={e => setSelectedDesignId(e.target.value)}
                        className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white max-w-xs"
                    >
                        {sortedDesigns.slice(0, 50).map(d => (
                            <option key={d.id} value={d.id}>
                                {d.name} {d.plddt_overall ? `(${d.plddt_overall.toFixed(1)})` : ''}
                            </option>
                        ))}
                    </select>
                </div>
                {chainLoading ? (
                    <div className="h-[400px] flex items-center justify-center text-slate-400">
                        Loading chain data...
                    </div>
                ) : chainMetrics && Object.keys(chainMetrics).length > 0 ? (
                    <Plot
                        data={Object.entries(chainMetrics)
                            .filter(([, m]) => m.type !== 'ligand')
                            .sort(([idA, a], [idB, b]) => {
                                const order = { protein: 0, dna: 1, rna: 2, ligand: 3 };
                                return (order[a.type as keyof typeof order] ?? 4) - (order[b.type as keyof typeof order] ?? 4) || idA.localeCompare(idB);
                            })
                            .map(([chainId, metric]) => ({
                                type: 'scatter' as const,
                                mode: 'lines' as const,
                                x: metric.residue_numbers ?? Array.from({ length: metric.length }, (_, i) => i + 1),
                                y: metric.plddt,
                                name: `Chain ${chainId} (${metric.type}, avg: ${metric.avg_plddt?.toFixed(1) ?? '—'})`,
                                line: {
                                    width: 2.5,
                                    color: metric.type === 'protein' ? scheme.bars[2] : metric.type === 'dna' ? scheme.bars[5] : metric.type === 'rna' ? scheme.bars[7] : '#64748b',
                                    shape: 'spline' as const,
                                },
                                hovertemplate: `<b>Chain ${chainId}</b><br>Residue %{x}<br>pLDDT: <b>%{y:.1f}</b><extra></extra>`,
                            })) as Data[]}
                        layout={{
                            paper_bgcolor: CHART_BG,
                            plot_bgcolor: 'transparent',
                            font: { color: FONT_COLOR, family: 'Inter, sans-serif' },
                            margin: { l: 60, r: 40, t: 20, b: 60 },
                            xaxis: {
                                title: { text: 'Residue Number', font: { color: AXIS_COLOR, size: 12 } },
                                gridcolor: GRID_COLOR,
                                color: AXIS_COLOR,
                                zeroline: false,
                            },
                            yaxis: {
                                title: { text: 'pLDDT Score', font: { color: AXIS_COLOR, size: 12 } },
                                gridcolor: '#33415580',
                                color: AXIS_COLOR,
                                range: [0, 100],
                                dtick: 20,
                                zeroline: false,
                            },
                            legend: {
                                orientation: 'h',
                                y: -0.15,
                                x: 0.5,
                                xanchor: 'center',
                                font: { size: 11, color: '#cbd5e1' },
                                bgcolor: 'transparent',
                            },
                            shapes: [
                                { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 90, y1: 100, fillcolor: '#1d4ed820', line: { width: 0 } },
                                { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 70, y1: 90, fillcolor: '#0d948820', line: { width: 0 } },
                                { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 50, y1: 70, fillcolor: '#ca8a0420', line: { width: 0 } },
                                { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 0, y1: 50, fillcolor: '#dc262620', line: { width: 0 } },
                                { type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 90, y1: 90, line: { color: '#3b82f6', width: 1, dash: 'dot' } },
                                { type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 70, y1: 70, line: { color: '#14b8a6', width: 1, dash: 'dot' } },
                                { type: 'line', x0: 0, x1: 1, xref: 'paper', y0: 50, y1: 50, line: { color: '#f59e0b', width: 1, dash: 'dot' } },
                            ],
                            annotations: [
                                { x: 1.01, xref: 'paper', y: 95, text: '<b>Very High</b>', showarrow: false, font: { size: 9, color: '#60a5fa' }, xanchor: 'left' },
                                { x: 1.01, xref: 'paper', y: 80, text: '<b>High</b>', showarrow: false, font: { size: 9, color: '#2dd4bf' }, xanchor: 'left' },
                                { x: 1.01, xref: 'paper', y: 60, text: '<b>Low</b>', showarrow: false, font: { size: 9, color: '#fbbf24' }, xanchor: 'left' },
                                { x: 1.01, xref: 'paper', y: 30, text: '<b>Very Low</b>', showarrow: false, font: { size: 9, color: '#f87171' }, xanchor: 'left' },
                            ],
                            hovermode: 'x unified',
                            showlegend: true,
                        }}
                        config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: 'plddt_profile' } }}
                        style={{ width: '100%', height: '400px' }}
                    />
                ) : (
                    <div className="h-[400px] flex items-center justify-center text-slate-500">
                        No per-residue data available
                    </div>
                )}
            </div>

            {/* PAE Heatmap - Full Width */}
            <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-4">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-base font-semibold text-slate-200">Predicted Aligned Error (PAE)</h3>
                    <select
                        value={selectedDesignId || sortedDesigns[0]?.id || ''}
                        onChange={e => setSelectedDesignId(e.target.value)}
                        className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white max-w-xs"
                    >
                        {sortedDesigns.slice(0, 50).map(d => (
                            <option key={d.id} value={d.id}>
                                {d.name} {d.pae_overall ? `(PAE: ${d.pae_overall.toFixed(1)})` : ''}
                            </option>
                        ))}
                    </select>
                </div>
                {paeLoading ? (
                    <div className="h-[500px] flex items-center justify-center text-slate-400">
                        Loading PAE matrix...
                    </div>
                ) : paeMatrix && paeMatrix.pae_matrix ? (() => {
                    // Compute chain regions from chainMetrics
                    const chainColors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];
                    const chainRegions: { start: number; end: number; name: string; color: string }[] = [];

                    if (chainMetrics && Object.keys(chainMetrics).length > 0) {
                        // First compute total residues from chainMetrics
                        const totalFullResidues = Object.entries(chainMetrics)
                            .filter(([, m]) => m.type !== 'ligand')
                            .reduce((sum, [, m]) => sum + m.length, 0);

                        // Scale factor: ratio of PAE matrix size to total residues
                        // This handles downsampling (e.g., 1000 residues -> 200 matrix)
                        const scaleFactor = totalFullResidues > 0 ? paeMatrix.size / totalFullResidues : 1;

                        let cumPos = 0;
                        Object.entries(chainMetrics)
                            .filter(([, m]) => m.type !== 'ligand')
                            .sort(([a], [b]) => a.localeCompare(b))
                            .forEach(([chainId, metric], index) => {
                                const scaledLength = Math.round(metric.length * scaleFactor);
                                chainRegions.push({
                                    start: cumPos,
                                    end: cumPos + scaledLength,
                                    name: chainId,
                                    color: chainColors[index % chainColors.length]
                                });
                                cumPos += scaledLength;
                            });
                    }

                    // Create solid boundary lines between chains
                    const shapes: any[] = chainRegions.length > 1 ? chainRegions.slice(1).flatMap(({ start }) => [
                        // Vertical line
                        { type: 'line', x0: start, x1: start, y0: 0, y1: paeMatrix.size, line: { color: '#ffffff', width: 2 } },
                        // Horizontal line  
                        { type: 'line', x0: 0, x1: paeMatrix.size, y0: start, y1: start, line: { color: '#ffffff', width: 2 } },
                    ]) : [];

                    // Add colored band rectangles along the axes to show chain regions
                    const bandWidth = 8;
                    chainRegions.forEach(({ start, end, color }) => {
                        // Left edge band (Y-axis)
                        shapes.push({
                            type: 'rect',
                            x0: -bandWidth - 2, x1: -2,
                            y0: start, y1: end,
                            fillcolor: color,
                            line: { width: 0, color: 'transparent' },
                            xref: 'x',
                        });
                        // Top edge band (X-axis) - we'll put at bottom since Y is reversed
                        shapes.push({
                            type: 'rect',
                            x0: start, x1: end,
                            y0: -bandWidth - 2, y1: -2,
                            fillcolor: color,
                            line: { width: 0, color: 'transparent' },
                            yref: 'y',
                        });
                    });

                    // Create chain label annotations - positioned at center of each region
                    const annotations = chainRegions.map(({ start, end, name, color }) => ({
                        x: (start + end) / 2,
                        y: paeMatrix.size + 15,
                        text: `<b>${name}</b>`,
                        showarrow: false,
                        font: { size: 12, color: color },
                        xanchor: 'center' as const,
                    }));

                    // Also add labels on Y-axis
                    chainRegions.forEach(({ start, end, name, color }) => {
                        annotations.push({
                            x: -20,
                            y: (start + end) / 2,
                            text: `<b>${name}</b>`,
                            showarrow: false,
                            font: { size: 12, color: color },
                            xanchor: 'center' as const,
                        });
                    });

                    return (
                        <Plot
                            data={[{
                                type: 'heatmap',
                                z: paeMatrix.pae_matrix,
                                colorscale: [
                                    [0, '#0d1f2d'],       // Very dark blue-gray (best alignment)
                                    [0.1, '#1a4a5e'],    // Deep teal
                                    [0.25, '#2d8a8a'],   // Soft teal
                                    [0.4, '#4fb3a0'],    // Seafoam green
                                    [0.55, '#90cfa0'],   // Sage green
                                    [0.7, '#d4e8b0'],    // Pale lime
                                    [0.85, '#f5e8c0'],   // Soft cream
                                    [1, '#f8d8c8']       // Warm peach (worst alignment)
                                ],
                                zmin: 0,
                                zmax: 30,
                                hoverongaps: false,
                                hovertemplate: 'Residue %{x} ↔ Residue %{y}<br>PAE: %{z:.1f} Å<extra></extra>',
                                colorbar: {
                                    title: { text: 'PAE (Å)', font: { color: '#e2e8f0' } },
                                    tickfont: { color: '#94a3b8' },
                                    thickness: 15,
                                },
                            } as Data]}
                            layout={{
                                title: {
                                    text: `PAE Matrix: ${paeMatrix.design_name}${chainRegions.length > 1 ? ` (${chainRegions.length} chains)` : ''}`,
                                    font: { color: '#e2e8f0', size: 16 }
                                },
                                paper_bgcolor: 'transparent',
                                plot_bgcolor: '#1e293b',
                                font: { color: '#e2e8f0' },
                                margin: { l: 80, r: 80, t: 50, b: 80 },
                                xaxis: {
                                    title: { text: 'Scored Residue', font: { color: '#94a3b8' }, standoff: 30 },
                                    color: '#94a3b8',
                                    scaleanchor: 'y',
                                },
                                yaxis: {
                                    title: { text: 'Aligned Residue', font: { color: '#94a3b8' }, standoff: 30 },
                                    color: '#94a3b8',
                                    autorange: 'reversed',
                                },
                                shapes: shapes,
                                annotations: annotations,
                            }}
                            config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: `pae_${paeMatrix.design_name}` } }}
                            style={{ width: '100%', height: '550px' }}
                        />
                    );
                })() : (
                    <div className="h-[500px] flex items-center justify-center text-slate-500">
                        <div className="text-center">
                            <div className="text-4xl mb-2">🔲</div>
                            No PAE data available for selected design<br />
                            <span className="text-xs text-slate-600">PAE matrices require Boltz2/RF3 confidence files</span>
                        </div>
                    </div>
                )}
            </div>

            {/* Chart Grid - 4 columns for histograms, 2 for scatter plots */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">

                {/* Row 1: Scatter plots - span 2 columns each */}
                <div className="col-span-2">
                    <ChartCard
                        title="pLDDT vs PAE"
                        hasData={hasScatterData('plddt_overall', 'pae_overall')}
                        isHidden={hiddenCharts.has('plddt-pae')}
                        onToggleHidden={() => toggleHidden('plddt-pae')}

                    >
                        <Plot data={makeScatter('plddt_overall', 'pae_overall', 'iptm')} layout={miniLayout()} config={miniConfig} style={scatterStyle} onSelected={handlePlotlySelect} />
                    </ChartCard>
                </div>

                <div className="col-span-2">
                    <ChartCard
                        title="Confidence vs iPTM"
                        hasData={hasScatterData('conf_score', 'iptm')}
                        isHidden={hiddenCharts.has('conf-iptm')}
                        onToggleHidden={() => toggleHidden('conf-iptm')}

                    >
                        <Plot data={makeScatter('conf_score', 'iptm', 'plddt_overall')} layout={miniLayout()} config={miniConfig} style={scatterStyle} onSelected={handlePlotlySelect} />
                    </ChartCard>
                </div>

                <div className="col-span-2">
                    <ChartCard
                        title="Affinity vs Binder Prob."
                        hasData={hasScatterData('affinity_score', 'binder_probability')}
                        isHidden={hiddenCharts.has('affinity-binder')}
                        onToggleHidden={() => toggleHidden('affinity-binder')}

                    >
                        <Plot data={makeScatter('affinity_score', 'binder_probability', 'plddt_overall')} layout={miniLayout()} config={miniConfig} style={scatterStyle} onSelected={handlePlotlySelect} />
                    </ChartCard>
                </div>

                <div className="col-span-2">
                    <ChartCard
                        title="pLDDT vs RoG"
                        hasData={hasScatterData('plddt_overall', 'rog')}
                        isHidden={hiddenCharts.has('plddt-rog')}
                        onToggleHidden={() => toggleHidden('plddt-rog')}

                    >
                        <Plot data={makeScatter('plddt_overall', 'rog', 'mpnn_score')} layout={miniLayout()} config={miniConfig} style={scatterStyle} onSelected={handlePlotlySelect} />
                    </ChartCard>
                </div>

                {/* Row 2: Histograms - 1 column each = 4 per row */}
                <ChartCard
                    title="pLDDT Distribution"
                    hasData={hasHistogramData('plddt_overall')}
                    isHidden={hiddenCharts.has('hist-plddt')}
                    onToggleHidden={() => toggleHidden('hist-plddt')}

                >
                    <Plot data={makeHistogram('plddt_overall', 0)} layout={miniLayout()} config={miniConfig} style={chartStyle} />
                </ChartCard>

                <ChartCard
                    title="PAE Distribution"
                    hasData={hasHistogramData('pae_overall')}
                    isHidden={hiddenCharts.has('hist-pae')}
                    onToggleHidden={() => toggleHidden('hist-pae')}

                >
                    <Plot data={makeHistogram('pae_overall', 2)} layout={miniLayout()} config={miniConfig} style={chartStyle} />
                </ChartCard>

                <ChartCard
                    title="iPTM Distribution"
                    hasData={hasHistogramData('iptm')}
                    isHidden={hiddenCharts.has('hist-iptm')}
                    onToggleHidden={() => toggleHidden('hist-iptm')}

                >
                    <Plot data={makeHistogram('iptm', 4)} layout={miniLayout()} config={miniConfig} style={chartStyle} />
                </ChartCard>

                <ChartCard
                    title="Confidence Distribution"
                    hasData={hasHistogramData('conf_score')}
                    isHidden={hiddenCharts.has('hist-conf')}
                    onToggleHidden={() => toggleHidden('hist-conf')}

                >
                    <Plot data={makeHistogram('conf_score', 6)} layout={miniLayout()} config={miniConfig} style={chartStyle} />
                </ChartCard>

                {/* Row 3: Statistical */}
                <ChartCard title="Quality Metrics (Violin)">
                    <Plot data={makeViolin(['plddt_overall', 'ptm', 'iptm'])} layout={miniLayout()} config={miniConfig} style={chartStyle} />
                </ChartCard>

                <ChartCard title="Binding Metrics (Violin)">
                    <Plot data={makeViolin(['affinity_score', 'binder_probability', 'conf_score'])} layout={miniLayout()} config={miniConfig} style={chartStyle} />
                </ChartCard>

                <ChartCard title="Contour: pLDDT vs PAE">
                    <Plot data={contourData} layout={miniLayout()} config={miniConfig} style={chartStyle} />
                </ChartCard>

                {/* Correlation Matrix - spans 1 */}
                <ChartCard title="Correlation Matrix">
                    {correlationData ? (
                        <Plot
                            data={[{
                                type: 'heatmap',
                                z: correlationData.matrix,
                                x: correlationData.labels,
                                y: correlationData.labels,
                                colorscale: 'RdBu',
                                zmin: -1, zmax: 1,
                                hovertemplate: '%{x}<br>%{y}<br>r = %{z:.2f}<extra></extra>',
                            }]}
                            layout={{ ...miniLayout(), margin: { l: 60, r: 20, t: 10, b: 60 }, xaxis: { tickangle: -45, color: AXIS_COLOR }, yaxis: { color: AXIS_COLOR } }}
                            config={miniConfig}
                            style={chartStyle}
                        />
                    ) : <div className="h-[220px] flex items-center justify-center text-slate-500 text-xs">Not enough data</div>}
                </ChartCard>
            </div>

            {/* Wide charts row */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {/* Parallel Coordinates - full width */}
                <div className="lg:col-span-2">
                    <ChartCard title="Parallel Coordinates (Top 50 by pLDDT)">
                        {parallelCoordsData.length > 0 ? (
                            <Plot
                                data={parallelCoordsData}
                                layout={{
                                    paper_bgcolor: CHART_BG,
                                    font: { color: FONT_COLOR, size: 11 },
                                    margin: { l: 80, r: 80, t: 40, b: 40 },
                                }}
                                config={miniConfig}
                                style={{ width: '100%', height: '400px' }}
                            />
                        ) : <div className="h-[300px] flex items-center justify-center text-slate-500 text-sm">Not enough metrics with data</div>}
                    </ChartCard>
                </div>

                {/* 3D preset charts */}
                <ChartCard
                    title="Quality Metrics 3D (pLDDT × iPTM × PAE)"
                    hasData={has3DData('plddt_overall', 'iptm', 'pae_overall')}
                    isHidden={hiddenCharts.has('3d-quality')}
                    onToggleHidden={() => toggleHidden('3d-quality')}

                >
                    <Plot
                        data={make3DScatter('plddt_overall', 'iptm', 'pae_overall', 'conf_score')}
                        layout={mini3DLayout('pLDDT', 'iPTM', 'PAE')}
                        config={miniConfig}
                        style={chart3DStyle}
                        onClick={handlePlotlyClick}
                    />
                </ChartCard>

                <ChartCard
                    title="Binding Landscape 3D (Affinity × Binder% × iPTM)"
                    hasData={has3DData('affinity_score', 'binder_probability', 'iptm')}
                    isHidden={hiddenCharts.has('3d-binding')}
                    onToggleHidden={() => toggleHidden('3d-binding')}

                >
                    <Plot
                        data={make3DScatter('affinity_score', 'binder_probability', 'iptm', 'plddt_overall')}
                        layout={mini3DLayout('Affinity', 'Binder%', 'iPTM')}
                        config={miniConfig}
                        style={chart3DStyle}
                        onClick={handlePlotlyClick}
                    />
                </ChartCard>
            </div>

            {/* Custom Chart Builders */}
            <div className="space-y-4">
                <h3 className="text-md font-semibold text-slate-300 border-b border-slate-700 pb-2">Custom Chart Builders</h3>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    {/* Custom 2D Scatter */}
                    <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-4 space-y-4">
                        <div className="flex flex-wrap items-center gap-3">
                            <span className="text-sm text-slate-400">2D Scatter:</span>
                            <select value={custom2dX} onChange={e => setCustom2dX(e.target.value as MetricKey)} className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white">
                                {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
                            </select>
                            <span className="text-slate-500">vs</span>
                            <select value={custom2dY} onChange={e => setCustom2dY(e.target.value as MetricKey)} className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white">
                                {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
                            </select>
                            <span className="text-slate-500">color:</span>
                            <select value={custom2dColor} onChange={e => setCustom2dColor(e.target.value as MetricKey)} className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white">
                                {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
                            </select>
                        </div>
                        <Plot
                            data={makeScatter(custom2dX, custom2dY, custom2dColor)}
                            layout={{
                                ...miniLayout(),
                                margin: { l: 50, r: 50, t: 20, b: 50 },
                                xaxis: { title: { text: getLabel(custom2dX) }, gridcolor: GRID_COLOR, color: AXIS_COLOR },
                                yaxis: { title: { text: getLabel(custom2dY) }, gridcolor: GRID_COLOR, color: AXIS_COLOR },
                            }}
                            config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: 'custom_2d_scatter' } }}
                            style={{ width: '100%', height: '350px' }}
                        />
                    </div>

                    {/* Custom 3D Scatter */}
                    <div className="bg-slate-800/50 rounded-xl border border-slate-700/50 p-4 space-y-4">
                        <div className="flex flex-wrap items-center gap-3">
                            <span className="text-sm text-slate-400">3D Scatter:</span>
                            <select value={custom3dX} onChange={e => setCustom3dX(e.target.value as MetricKey)} className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white">
                                {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
                            </select>
                            <select value={custom3dY} onChange={e => setCustom3dY(e.target.value as MetricKey)} className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white">
                                {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
                            </select>
                            <select value={custom3dZ} onChange={e => setCustom3dZ(e.target.value as MetricKey)} className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white">
                                {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
                            </select>
                            <span className="text-slate-500">color:</span>
                            <select value={custom3dColor} onChange={e => setCustom3dColor(e.target.value as MetricKey)} className="bg-slate-700 border border-slate-600 rounded px-2 py-1 text-sm text-white">
                                {METRICS.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
                            </select>
                        </div>
                        <Plot
                            data={make3DScatter(custom3dX, custom3dY, custom3dZ, custom3dColor)}
                            layout={{
                                ...mini3DLayout(getLabel(custom3dX), getLabel(custom3dY), getLabel(custom3dZ)),
                                margin: { l: 0, r: 0, t: 10, b: 0 },
                            }}
                            config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg', filename: 'custom_3d_scatter' } }}
                            style={{ width: '100%', height: '400px' }}
                        />
                    </div>
                </div>
            </div>

            {/* Expanded Chart Modal */}
            {expandedChart && (
                <ExpandedChartModal
                    title={
                        expandedChart === 'plddt-pae' ? 'pLDDT vs PAE' :
                            expandedChart === 'conf-iptm' ? 'Confidence vs iPTM' :
                                expandedChart === 'affinity-binder' ? 'Affinity vs Binder Prob.' :
                                    expandedChart === 'plddt-rog' ? 'pLDDT vs RoG' :
                                        expandedChart === 'hist-plddt' ? 'pLDDT Distribution' :
                                            expandedChart === 'hist-pae' ? 'PAE Distribution' :
                                                expandedChart === 'hist-iptm' ? 'iPTM Distribution' :
                                                    expandedChart === 'hist-conf' ? 'Confidence Distribution' :
                                                        expandedChart === '3d-quality' ? 'Quality Metrics 3D' :
                                                            expandedChart === '3d-binding' ? 'Binding Landscape 3D' :
                                                                'Chart'
                    }
                    onClose={() => setExpandedChart(null)}
                >
                    <div style={{ width: '100%', height: '100%' }}>
                        {expandedChart === 'plddt-pae' && <Plot data={makeScatter('plddt_overall', 'pae_overall', 'iptm')} layout={miniLayout()} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                        {expandedChart === 'conf-iptm' && <Plot data={makeScatter('conf_score', 'iptm', 'plddt_overall')} layout={miniLayout()} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                        {expandedChart === 'affinity-binder' && <Plot data={makeScatter('affinity_score', 'binder_probability', 'plddt_overall')} layout={miniLayout()} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                        {expandedChart === 'plddt-rog' && <Plot data={makeScatter('plddt_overall', 'rog', 'mpnn_score')} layout={miniLayout()} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                        {expandedChart === 'hist-plddt' && <Plot data={makeHistogram('plddt_overall', 0)} layout={miniLayout()} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                        {expandedChart === 'hist-pae' && <Plot data={makeHistogram('pae_overall', 2)} layout={miniLayout()} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                        {expandedChart === 'hist-iptm' && <Plot data={makeHistogram('iptm', 4)} layout={miniLayout()} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                        {expandedChart === 'hist-conf' && <Plot data={makeHistogram('conf_score', 6)} layout={miniLayout()} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                        {expandedChart === '3d-quality' && <Plot data={make3DScatter('plddt_overall', 'iptm', 'pae_overall', 'conf_score')} layout={mini3DLayout('pLDDT', 'iPTM', 'PAE')} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                        {expandedChart === '3d-binding' && <Plot data={make3DScatter('affinity_score', 'binder_probability', 'iptm', 'plddt_overall')} layout={mini3DLayout('Affinity', 'Binder%', 'iPTM')} config={miniConfig} style={{ width: '100%', height: '100%' }} />}
                    </div>
                </ExpandedChartModal>
            )}
        </div >
    );
}

export default AnalyticsDashboard;
