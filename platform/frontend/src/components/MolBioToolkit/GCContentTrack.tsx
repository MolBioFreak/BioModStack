/**
 * Sequence diagnostics track for GC and other practical construct QC metrics.
 */

import { useCallback, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout, PlotRelayoutEvent, Shape } from 'plotly.js';
import type { SelectionInfo } from './SequenceViewer';
import { findRestrictionSites, getRestrictionEnzyme } from './utils/restrictionEnzymes';

type MetricId = 'gc' | 'restriction_density' | 'ambiguity_density' | 'homopolymer_burden';

interface AnalyticsPoint {
    position: number;
    start: number;
    end: number;
    value: number;
}

interface MetricDefinition {
    id: MetricId;
    label: string;
    shortLabel: string;
    description: string;
    color: string;
    fillColor: string;
    range?: [number, number];
    preferredBand?: [number, number] | null;
    suffix: string;
    tickFormat: string;
    decimals: number;
    summaryLabel: string;
}

interface GCContentTrackProps {
    sequence: string;
    circular?: boolean;
    selectedEnzymes?: string[];
    windowSize?: number;
    stepSize?: number;
    height?: number;
    selection?: SelectionInfo | null;
    onSelectionChange?: (selection: SelectionInfo) => void;
}

const CANONICAL_BASES = new Set(['A', 'C', 'G', 'T']);

const METRIC_DEFINITIONS: MetricDefinition[] = [
    {
        id: 'gc',
        label: 'GC Content',
        shortLabel: 'GC%',
        description: 'Sliding-window GC percentage. Useful for spotting synthesis, cloning, and expression trouble zones.',
        color: '#22c55e',
        fillColor: 'rgba(34, 197, 94, 0.12)',
        range: [0, 100],
        preferredBand: [40, 60],
        suffix: '%',
        tickFormat: '.0f',
        decimals: 1,
        summaryLabel: 'Mean GC',
    },
    {
        id: 'restriction_density',
        label: 'Restriction Density',
        shortLabel: 'Cuts/kb',
        description: 'Cut-site density for the currently mapped enzyme set. Useful when reviewing cloning-accessible regions.',
        color: '#f97316',
        fillColor: 'rgba(249, 115, 22, 0.12)',
        preferredBand: null,
        suffix: '/kb',
        tickFormat: '.1f',
        decimals: 2,
        summaryLabel: 'Mean density',
    },
    {
        id: 'ambiguity_density',
        label: 'Ambiguity Load',
        shortLabel: 'Ambig%',
        description: 'Fraction of ambiguous or non-canonical bases in each window. This should usually be zero.',
        color: '#f43f5e',
        fillColor: 'rgba(244, 63, 94, 0.12)',
        range: [0, 100],
        preferredBand: [0, 0],
        suffix: '%',
        tickFormat: '.0f',
        decimals: 1,
        summaryLabel: 'Ambiguous',
    },
    {
        id: 'homopolymer_burden',
        label: 'Homopolymer Burden',
        shortLabel: 'Runs≥4',
        description: 'Percent of bases sitting inside homopolymer runs of length 4 or more. Useful for synthesis and sequencing QC.',
        color: '#38bdf8',
        fillColor: 'rgba(56, 189, 248, 0.12)',
        range: [0, 100],
        preferredBand: [0, 10],
        suffix: '%',
        tickFormat: '.0f',
        decimals: 1,
        summaryLabel: 'Run burden',
    },
];

const METRIC_INDEX = new Map(METRIC_DEFINITIONS.map((metric) => [metric.id, metric]));

function normalizeSequence(sequence: string): string {
    return sequence.toUpperCase().replace(/U/g, 'T');
}

function countBases(sequence: string): Record<'A' | 'C' | 'G' | 'T', number> {
    const counts = { A: 0, C: 0, G: 0, T: 0 };
    for (const base of sequence) {
        if (base in counts) {
            counts[base as keyof typeof counts] += 1;
        }
    }
    return counts;
}

function calculateGC(sequence: string): number {
    if (!sequence) return 0;
    const counts = countBases(sequence);
    const total = counts.A + counts.C + counts.G + counts.T;
    if (total === 0) return 0;
    return ((counts.G + counts.C) / total) * 100;
}

function calculateAmbiguityDensity(sequence: string): number {
    if (!sequence) return 0;
    let ambiguous = 0;
    for (const base of sequence) {
        if (!CANONICAL_BASES.has(base)) {
            ambiguous += 1;
        }
    }
    return (ambiguous / sequence.length) * 100;
}

function calculateHomopolymerBurden(sequence: string, minimumRunLength: number = 4): number {
    if (!sequence) return 0;
    let burdenBases = 0;
    let index = 0;

    while (index < sequence.length) {
        const current = sequence[index];
        let end = index + 1;
        while (end < sequence.length && sequence[end] === current) {
            end += 1;
        }

        const runLength = end - index;
        if (runLength >= minimumRunLength && CANONICAL_BASES.has(current)) {
            burdenBases += runLength;
        }
        index = end;
    }

    return (burdenBases / sequence.length) * 100;
}

function buildWindows(sequenceLength: number, windowSize: number, stepSize: number): Array<{ start: number; end: number }> {
    if (sequenceLength <= 0) return [];
    if (sequenceLength <= windowSize) return [{ start: 0, end: sequenceLength }];

    const windows: Array<{ start: number; end: number }> = [];
    for (let start = 0; start <= sequenceLength - windowSize; start += stepSize) {
        windows.push({ start, end: start + windowSize });
    }

    const lastWindow = windows[windows.length - 1];
    if (!lastWindow || lastWindow.end < sequenceLength) {
        windows.push({ start: sequenceLength - windowSize, end: sequenceLength });
    }
    return windows;
}

function smoothValues(values: number[]): number[] {
    if (values.length <= 2) return values;
    return values.map((value, index) => {
        const left = values[Math.max(0, index - 1)];
        const right = values[Math.min(values.length - 1, index + 1)];
        return (left + value + right) / 3;
    });
}

function countPositionsInRange(positions: number[], start: number, end: number): number {
    let count = 0;
    for (const position of positions) {
        if (position >= start && position < end) {
            count += 1;
        }
    }
    return count;
}

function computeMetricValue(
    metric: MetricId,
    sequence: string,
    start: number,
    end: number,
    restrictionPositions: number[],
): number {
    const windowSeq = sequence.slice(start, end);
    if (!windowSeq) return 0;

    switch (metric) {
        case 'gc':
            return calculateGC(windowSeq);
        case 'ambiguity_density':
            return calculateAmbiguityDensity(windowSeq);
        case 'homopolymer_burden':
            return calculateHomopolymerBurden(windowSeq);
        case 'restriction_density': {
            const windowLengthKb = Math.max(1, end - start) / 1000;
            return countPositionsInRange(restrictionPositions, start, end) / windowLengthKb;
        }
        default:
            return 0;
    }
}

function formatMetricValue(metric: MetricDefinition, value: number): string {
    return `${value.toFixed(metric.decimals)}${metric.suffix}`;
}

function formatRange(metric: MetricDefinition, minimum: number, maximum: number): string {
    return `${minimum.toFixed(metric.decimals)}-${maximum.toFixed(metric.decimals)}${metric.suffix}`;
}

function clamp(value: number, minimum: number, maximum: number): number {
    return Math.min(maximum, Math.max(minimum, value));
}

function calculateYRange(metric: MetricDefinition, values: number[]): [number, number] {
    if (values.length === 0) {
        return metric.range ?? [0, 1];
    }

    const observedMin = Math.min(...values);
    const observedMax = Math.max(...values);

    if (metric.id === 'gc') {
        const paddedMin = Math.floor((observedMin - 4) / 5) * 5;
        const paddedMax = Math.ceil((observedMax + 4) / 5) * 5;
        const bandMin = metric.preferredBand?.[0] ?? paddedMin;
        const bandMax = metric.preferredBand?.[1] ?? paddedMax;
        let lower = clamp(Math.min(paddedMin, bandMin), 0, 100);
        let upper = clamp(Math.max(paddedMax, bandMax), 0, 100);
        if ((upper - lower) < 22) {
            const midpoint = (upper + lower) / 2;
            lower = clamp(Math.floor(midpoint - 11), 0, 100);
            upper = clamp(Math.ceil(midpoint + 11), 0, 100);
        }
        if ((upper - lower) < 12) {
            return [0, 100];
        }
        return [lower, upper];
    }

    if (metric.id === 'ambiguity_density' || metric.id === 'homopolymer_burden') {
        const upper = clamp(Math.ceil((observedMax * 1.25) / 5) * 5, 5, 100);
        return [0, upper];
    }

    if (metric.id === 'restriction_density') {
        const upper = Math.max(1, Math.ceil(observedMax * 1.25 * 10) / 10);
        return [0, upper];
    }

    return metric.range ?? [0, Math.max(1, observedMax * 1.18)];
}

export function GCContentTrack({
    sequence,
    circular = false,
    selectedEnzymes = [],
    windowSize = 60,
    stepSize,
    height = 156,
    selection,
    onSelectionChange,
}: GCContentTrackProps) {
    const [metricId, setMetricId] = useState<MetricId>('gc');
    const metric = METRIC_INDEX.get(metricId) ?? METRIC_DEFINITIONS[0];
    const normalizedSequence = useMemo(() => normalizeSequence(sequence), [sequence]);
    const computedStepSize = stepSize ?? Math.max(12, Math.floor(windowSize / 3));

    const restrictionPositions = useMemo(() => {
        const allPositions = selectedEnzymes.flatMap((name) => {
            const enzyme = getRestrictionEnzyme(name);
            if (!enzyme) return [];
            return findRestrictionSites(normalizedSequence, enzyme.site, circular);
        });
        return Array.from(new Set(allPositions)).sort((left, right) => left - right);
    }, [circular, normalizedSequence, selectedEnzymes]);

    const analyticsData = useMemo<AnalyticsPoint[]>(() => {
        if (!normalizedSequence || normalizedSequence.length < 10) return [];

        return buildWindows(normalizedSequence.length, windowSize, computedStepSize).map(({ start, end }) => ({
            start,
            end,
            position: start + Math.floor((end - start) / 2),
            value: computeMetricValue(metricId, normalizedSequence, start, end, restrictionPositions),
        }));
    }, [computedStepSize, metricId, normalizedSequence, restrictionPositions, windowSize]);

    const smoothedValues = useMemo(
        () => smoothValues(analyticsData.map((point) => point.value)),
        [analyticsData],
    );

    const summary = useMemo(() => {
        if (analyticsData.length === 0) {
            return { mean: 0, minimum: 0, maximum: 0 };
        }
        const rawValues = analyticsData.map((point) => point.value);
        return {
            mean: rawValues.reduce((sum, value) => sum + value, 0) / rawValues.length,
            minimum: Math.min(...rawValues),
            maximum: Math.max(...rawValues),
        };
    }, [analyticsData]);

    const selectionStats = useMemo(() => {
        if (!selection || !normalizedSequence) return null;
        const start = Math.max(0, Math.min(selection.start, selection.end));
        const end = Math.min(normalizedSequence.length, Math.max(selection.start, selection.end));
        if (end <= start) return null;

        return {
            value: computeMetricValue(metricId, normalizedSequence, start, end, restrictionPositions),
            length: end - start,
        };
    }, [metricId, normalizedSequence, restrictionPositions, selection]);

    const preferredWindowCount = useMemo(() => {
        if (!metric.preferredBand) return null;
        const [low, high] = metric.preferredBand;
        let count = 0;
        for (const point of analyticsData) {
            if (point.value >= low && point.value <= high) {
                count += 1;
            }
        }
        return count;
    }, [analyticsData, metric.preferredBand]);

    const handleRelayout = useCallback((event: PlotRelayoutEvent) => {
        if (!onSelectionChange || !normalizedSequence) return;
        if ('xaxis.range[0]' in event && 'xaxis.range[1]' in event) {
            const start = Math.max(0, Math.floor(event['xaxis.range[0]'] as number));
            const end = Math.min(normalizedSequence.length, Math.ceil(event['xaxis.range[1]'] as number));
            if (end > start && (end - start) < normalizedSequence.length * 0.98) {
                onSelectionChange({ start, end });
            }
        }
    }, [normalizedSequence, onSelectionChange]);

    if (!normalizedSequence || analyticsData.length === 0) {
        return null;
    }

    const rawValues = analyticsData.map((point) => point.value);
    const yRange = calculateYRange(metric, rawValues);

    const traces: Data[] = [
        {
            x: analyticsData.map((point) => point.position),
            y: rawValues,
            type: 'scatter',
            mode: 'lines',
            line: {
                width: 1,
                color: metric.color,
                shape: 'linear',
            },
            opacity: 0.28,
            hoverinfo: 'skip',
            showlegend: false,
        },
        {
            x: analyticsData.map((point) => point.position),
            y: smoothedValues,
            customdata: analyticsData.map((point) => [point.start + 1, point.end, point.value]),
            type: 'scatter',
            mode: 'lines',
            line: {
                width: 2.8,
                color: metric.color,
                shape: 'spline',
                smoothing: 0.55,
            },
            fill: 'tozeroy',
            fillcolor: metric.fillColor,
            hovertemplate: `<b>${metric.label}</b><br>%{customdata[0]:,.0f}-%{customdata[1]:,.0f}<br>%{customdata[2]:.${metric.decimals}f}${metric.suffix}<extra></extra>`,
            showlegend: false,
        },
    ];

    const shapes: Partial<Shape>[] = [];
    if (metric.preferredBand && metric.preferredBand[1] > metric.preferredBand[0]) {
        shapes.push({
            type: 'rect',
            x0: 0,
            x1: normalizedSequence.length,
            y0: metric.preferredBand[0],
            y1: metric.preferredBand[1],
            fillcolor: 'rgba(148, 163, 184, 0.08)',
            line: { width: 0 },
        });
    }

    shapes.push({
        type: 'line',
        x0: 0,
        x1: normalizedSequence.length,
        y0: summary.mean,
        y1: summary.mean,
        line: {
            color: 'rgba(148, 163, 184, 0.45)',
            width: 1,
            dash: 'dot',
        },
    });

    if (selection) {
        const start = Math.min(selection.start, selection.end);
        const end = Math.max(selection.start, selection.end);
        if (end > start) {
            shapes.push({
                type: 'rect',
                x0: start,
                x1: end,
                y0: yRange[0],
                y1: yRange[1],
                fillcolor: 'rgba(34, 197, 94, 0.08)',
                line: { color: 'rgba(34, 197, 94, 0.35)', width: 1 },
            });
        }
    }

    const layout: Partial<Layout> = {
        height,
        margin: { l: 54, r: 18, t: 10, b: 30 },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(2, 6, 23, 0.62)',
        hovermode: 'x unified',
        showlegend: false,
        dragmode: 'zoom',
        xaxis: {
            showgrid: false,
            zeroline: false,
            color: '#94a3b8',
            tickfont: { size: 10, color: '#64748b' },
            range: [0, normalizedSequence.length],
            tickformat: ',',
            fixedrange: false,
        },
        yaxis: {
            showgrid: true,
            gridcolor: 'rgba(51, 65, 85, 0.28)',
            zeroline: false,
            color: '#94a3b8',
            tickfont: { size: 10, color: '#64748b' },
            range: yRange,
            tickformat: metric.tickFormat,
            fixedrange: true,
        },
        shapes,
        hoverlabel: {
            bgcolor: '#020617',
            bordercolor: '#1e293b',
            font: { color: '#e2e8f0', size: 11 },
        },
    };

    return (
        <div className="border-b border-slate-700 bg-slate-950/20 px-3 py-3">
            <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70 shadow-[0_0_0_1px_rgba(15,23,42,0.5)]">
                <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 px-4 py-3">
                    <div className="min-w-0 flex-1">
                        <div className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Sequence Diagnostics</div>
                        <div className="mt-2 flex flex-wrap gap-1.5">
                            {METRIC_DEFINITIONS.map((definition) => (
                                <button
                                    key={definition.id}
                                    onClick={() => setMetricId(definition.id)}
                                    className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                                        metricId === definition.id
                                            ? 'border-slate-500 bg-slate-700 text-white'
                                            : 'border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800'
                                    }`}
                                    title={definition.description}
                                >
                                    {definition.shortLabel}
                                </button>
                            ))}
                        </div>
                        <p className="mt-2 max-w-3xl text-[11px] leading-5 text-slate-500">
                            {metric.description}
                        </p>
                    </div>

                    <div className="flex flex-wrap items-center gap-2 text-[11px]">
                        <div className="rounded-full border border-slate-700 bg-slate-950 px-2.5 py-1 text-slate-200">
                            {metric.summaryLabel}: {formatMetricValue(metric, summary.mean)}
                        </div>
                        <div className="rounded-full border border-slate-700 bg-slate-950 px-2.5 py-1 text-slate-400">
                            Range: {formatRange(metric, summary.minimum, summary.maximum)}
                        </div>
                        {preferredWindowCount !== null && (
                            <div className="rounded-full border border-slate-700 bg-slate-950 px-2.5 py-1 text-slate-400">
                                In band: {preferredWindowCount}/{analyticsData.length} windows
                            </div>
                        )}
                        {selectionStats && (
                            <div className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-emerald-200">
                                Selection: {formatMetricValue(metric, selectionStats.value)} over {selectionStats.length.toLocaleString()} bp
                            </div>
                        )}
                    </div>
                </div>

                <div className="px-3 py-3">
                    <Plot
                        data={traces}
                        layout={layout}
                        config={{
                            displayModeBar: false,
                            responsive: true,
                            staticPlot: false,
                            scrollZoom: true,
                        }}
                        style={{ width: '100%', height }}
                        useResizeHandler={true}
                        onRelayout={handleRelayout}
                    />
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 px-4 py-2 text-[11px] text-slate-500">
                    <div className="flex flex-wrap items-center gap-2">
                        <span>{windowSize.toLocaleString()} bp window</span>
                        <span>•</span>
                        <span>{computedStepSize.toLocaleString()} bp step</span>
                        {metricId === 'restriction_density' && (
                            <>
                                <span>•</span>
                                <span>{selectedEnzymes.length} mapped enzymes</span>
                            </>
                        )}
                    </div>
                    <div>
                        Drag on the chart to focus a region. Double-click to reset.
                    </div>
                </div>
            </div>
        </div>
    );
}
