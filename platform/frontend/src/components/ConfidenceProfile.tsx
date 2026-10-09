// Shared owner extracted from AnalyticsDashboard's working pLDDT chart.
import { useEffect, useMemo, useRef, useState } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout, Legend } from 'plotly.js';
import type { AtomRef } from '../structureViewer/contracts/structureIdentity';
import type { ChainMetric } from '../lib/api';
import { useThemeColors } from './useThemeColors';

const DEFAULT_PLOT_CONFIG = {
    responsive: true,
    displayModeBar: 'hover' as const,
    displaylogo: false,
    toImageButtonOptions: { format: 'svg' as const },
};
const CHAIN_COLORS = ['#60a5fa', '#2dd4bf', '#f59e0b', '#a78bfa'];
export type ConfidenceChainMetric = ChainMetric & { positionLabels?: string[]; identities?: AtomRef[]; label?: string };
type ConfidenceProfileProps = {
    chainMetrics: Record<string, ConfidenceChainMetric>;
    onInspect?: (ref: AtomRef) => void;
    height?: number;
};

export function ConfidenceProfile({ chainMetrics, onInspect, height = 270 }: ConfidenceProfileProps) {
    const colors = useThemeColors();
    const container = useRef<HTMLDivElement>(null);
    const [width, setWidth] = useState<number>();
    // Resize with the results grid / side panel as well as with the window.
    useEffect(() => {
        const element = container.current;
        if (!element || typeof ResizeObserver === 'undefined') return;
        const observer = new ResizeObserver(([entry]) => {
            const nextWidth = Math.floor(entry.contentRect.width);
            if (nextWidth > 0) setWidth(nextWidth);
        });
        observer.observe(element);
        return () => observer.disconnect();
    }, []);

    const profile = useMemo(() => {
        const chains = Object.entries(chainMetrics || {})
            .filter(([, metric]) => metric.type !== 'ligand')
            .sort(([leftId, leftMetric], [rightId, rightMetric]) => {
                const order: Record<string, number> = { protein: 0, dna: 1, rna: 2, ligand: 3 };
                return (order[leftMetric.type] ?? 4) - (order[rightMetric.type] ?? 4) || leftId.localeCompare(rightId);
            });
        // Only use native numbers when the supplied labels describe those exact
        // positions. Insertion codes remain categorical, never parsed as integers.
        const categorical = chains.some(([, metric]) => metric.positionLabels?.some(
            (label, index) => label !== String(metric.residue_numbers?.[index]),
        ));
        const positional = chains.some(([, metric]) => !metric.positionLabels && !metric.residue_numbers);
        const categories: string[] = [];
        const seen = new Set<string>();
        const data = chains.map(([chainId, metric], index) => {
            const positions = metric.positionLabels ?? metric.residue_numbers
                ?? Array.from({ length: metric.length }, (_, value) => value + 1);
            const labels = positions.map(String);
            if (categorical) labels.forEach(label => {
                if (!seen.has(label)) { seen.add(label); categories.push(label); }
            });
            return {
                type: 'scatter' as const,
                mode: 'lines' as const,
                x: categorical ? labels : (metric.residue_numbers ?? positions),
                y: metric.plddt,
                text: labels,
                customdata: metric.identities,
                name: metric.label ?? `Chain ${chainId}`,
                meta: [metric.label ?? `Chain ${chainId}`, metric.type, metric.avg_plddt?.toFixed(1) ?? 'n/a'],
                line: { width: 1.8, color: CHAIN_COLORS[index % CHAIN_COLORS.length], shape: 'linear' as const },
                hovertemplate: '<b>%{meta[0]}</b><br>Position %{text}<br>pLDDT: %{y:.1f}<br>%{meta[1]} · mean %{meta[2]}<extra></extra>',
            };
        });
        const numericPositions = categorical ? [] : data.flatMap(trace => trace.x.map(Number)).filter(Number.isFinite);
        const numericExtent = numericPositions.reduce(([min, max], value) => [Math.min(min, value), Math.max(max, value)], [Infinity, -Infinity]);
        return { data: data as Data[], categorical, categories, positional, numericExtent };
    }, [chainMetrics]);
    const tickCount = Math.max(3, Math.min(7, Math.floor(((width ?? 600) - 70) / 60)));
    const [firstPosition, lastPosition] = profile.numericExtent;
    const numericTicks = Number.isFinite(firstPosition) && Number.isFinite(lastPosition)
        ? [...new Set(Array.from({ length: tickCount }, (_, i) => Math.round(firstPosition + i * (lastPosition - firstPosition) / (tickCount - 1))))]
        : undefined;
    const categoryTicks = profile.categories.filter((_, index, all) =>
        index % Math.max(1, Math.ceil(all.length / tickCount)) === 0,
    );
    const layout: Partial<Layout> = {
        autosize: true,
        width,
        height,
        paper_bgcolor: 'transparent',
        plot_bgcolor: colors.bgSecondary,
        font: { color: colors.textPrimary, size: 11 },
        xaxis: {
            type: profile.categorical ? 'category' : 'linear',
            title: { text: profile.positional ? 'Profile position' : 'Residue number', standoff: 8 },
            ...(profile.categorical ? {
                categoryorder: 'array',
                categoryarray: profile.categories,
                tickmode: 'array',
                tickvals: categoryTicks,
                ticktext: categoryTicks.map(label => label.length > 14 ? `${label.slice(0, 12)}…` : label),
            } : { tickmode: 'array', tickvals: numericTicks, tickformat: 'd' }),
            tickangle: 0,
            automargin: true,
            showgrid: false,
            color: colors.textSecondary,
            zeroline: false,
            ticks: 'outside',
            ticklen: 5,
            tickcolor: colors.borderPrimary,
        },
        yaxis: {
            title: { text: 'pLDDT', standoff: 6 },
            gridcolor: colors.borderPrimary,
            color: colors.textSecondary,
            range: [0, 100],
            dtick: 20,
            automargin: true,
            zeroline: false,
            ticks: 'outside',
            ticklen: 5,
            tickcolor: colors.borderPrimary,
        },
        margin: { l: 48, r: 16, t: profile.data.length > 2 ? 72 : 48, b: 48 },
        // Keep legend above the plot, away from the residue axis. A bounded,
        // scrollable legend prevents large complexes from consuming the plot.
        showlegend: true,
        legend: {
            orientation: 'h',
            x: 0,
            xanchor: 'left',
            y: 1.04,
            yanchor: 'bottom',
            maxheight: 44,
            font: { size: 11, color: colors.textPrimary },
            bgcolor: colors.bgSecondary,
            itemclick: 'toggle',
            itemdoubleclick: 'toggleothers',
        } as Partial<Legend> & { maxheight: number }, // Plotly 3.3 supports this; bundled declarations lag.
        shapes: [
            { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 90, y1: 100, fillcolor: 'rgba(29,78,216,0.10)', line: { width: 0 }, layer: 'below' },
            { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 70, y1: 90, fillcolor: 'rgba(13,148,136,0.10)', line: { width: 0 }, layer: 'below' },
            { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 50, y1: 70, fillcolor: 'rgba(202,138,4,0.10)', line: { width: 0 }, layer: 'below' },
            { type: 'rect', x0: 0, x1: 1, xref: 'paper', y0: 0, y1: 50, fillcolor: 'rgba(220,38,38,0.10)', line: { width: 0 }, layer: 'below' },
        ],
        hovermode: 'closest',
        hoverlabel: { bgcolor: colors.bgSecondary, bordercolor: colors.borderPrimary, font: { color: colors.textPrimary }, namelength: -1 },
        dragmode: 'zoom',
    };

    return <div ref={container} style={{ width: '100%', minWidth: 0 }}>
        <Plot
            data={profile.data}
            layout={layout}
            onClick={event => {
                const ref = event.points[0]?.customdata as unknown as AtomRef | undefined;
                if (ref?.documentId) onInspect?.(ref);
            }}
            config={DEFAULT_PLOT_CONFIG}
            useResizeHandler
            style={{ width: '100%', height }}
        />
    </div>;
}
