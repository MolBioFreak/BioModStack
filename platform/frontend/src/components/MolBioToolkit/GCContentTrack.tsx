/**
 * GCContentTrack - analytics track system for sequence composition and restriction density
 */

import { useCallback, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout, PlotRelayoutEvent, Shape } from 'plotly.js';
import type { SelectionInfo } from './SequenceViewer';
import { findRestrictionSites, getRestrictionEnzyme } from './utils/restrictionEnzymes';

type MetricId = 'gc' | 'gc_skew' | 'at_skew' | 'complexity' | 'restriction_density';

interface AnalyticsPoint {
    position: number;
    start: number;
    end: number;
    value: number;
}

interface MetricDefinition {
    id: MetricId;
    label: string;
    badgeLabel: string;
    description: string;
    color: string;
    fillColor: string;
    range?: [number, number];
    baseline?: number;
    suffix: string;
    selectionLabel: string;
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

const METRIC_DEFINITIONS: MetricDefinition[] = [
    {
        id: 'gc',
        label: 'GC%',
        badgeLabel: 'GC',
        description: 'Sliding-window GC percentage',
        color: '#22c55e',
        fillColor: 'rgba(34, 197, 94, 0.15)',
        range: [0, 100],
        baseline: 50,
        suffix: '%',
        selectionLabel: 'GC',
    },
    {
        id: 'gc_skew',
        label: 'GC Skew',
        badgeLabel: 'GC skew',
        description: '(G - C) / (G + C)',
        color: '#8b5cf6',
        fillColor: 'rgba(139, 92, 246, 0.12)',
        range: [-100, 100],
        baseline: 0,
        suffix: '%',
        selectionLabel: 'GC skew',
    },
    {
        id: 'at_skew',
        label: 'AT Skew',
        badgeLabel: 'AT skew',
        description: '(A - T) / (A + T)',
        color: '#f59e0b',
        fillColor: 'rgba(245, 158, 11, 0.12)',
        range: [-100, 100],
        baseline: 0,
        suffix: '%',
        selectionLabel: 'AT skew',
    },
    {
        id: 'complexity',
        label: 'Complexity',
        badgeLabel: 'Complexity',
        description: 'Normalized Shannon entropy',
        color: '#38bdf8',
        fillColor: 'rgba(56, 189, 248, 0.12)',
        range: [0, 100],
        baseline: 50,
        suffix: '%',
        selectionLabel: 'Complexity',
    },
    {
        id: 'restriction_density',
        label: 'Restriction/kb',
        badgeLabel: 'Restriction',
        description: 'Cut-site density using the map enzyme set',
        color: '#f43f5e',
        fillColor: 'rgba(244, 63, 94, 0.12)',
        baseline: 0,
        suffix: '/kb',
        selectionLabel: 'Restriction density',
    },
];

const METRIC_INDEX = new Map(METRIC_DEFINITIONS.map((metric) => [metric.id, metric]));

function normalizeSequence(sequence: string): string {
    return sequence.toUpperCase().replace(/U/g, 'T');
}

function countBases(sequence: string): Record<'A' | 'C' | 'G' | 'T', number> {
    const counts = { A: 0, C: 0, G: 0, T: 0 };
    for (const base of normalizeSequence(sequence)) {
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

function calculateSkew(sequence: string, positiveBase: 'A' | 'C' | 'G' | 'T', negativeBase: 'A' | 'C' | 'G' | 'T'): number {
    const counts = countBases(sequence);
    const total = counts[positiveBase] + counts[negativeBase];
    if (total === 0) return 0;
    return ((counts[positiveBase] - counts[negativeBase]) / total) * 100;
}

function calculateComplexity(sequence: string): number {
    const counts = countBases(sequence);
    const total = counts.A + counts.C + counts.G + counts.T;
    if (total === 0) return 0;

    const entropy = (Object.values(counts) as number[])
        .filter((count) => count > 0)
        .reduce((sum, count) => {
            const probability = count / total;
            return sum - probability * Math.log2(probability);
        }, 0);

    return (entropy / 2) * 100;
}

function buildWindows(sequenceLength: number, windowSize: number, stepSize: number): Array<{ start: number; end: number }> {
    if (sequenceLength <= 0) {
        return [];
    }
    if (sequenceLength <= windowSize) {
        return [{ start: 0, end: sequenceLength }];
    }

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
    if (values.length <= 2) {
        return values;
    }
    return values.map((value, index) => {
        const neighbors = [
            values[Math.max(0, index - 1)],
            value,
            values[Math.min(values.length - 1, index + 1)],
        ];
        return neighbors.reduce((sum, item) => sum + item, 0) / neighbors.length;
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
        case 'gc_skew':
            return calculateSkew(windowSeq, 'G', 'C');
        case 'at_skew':
            return calculateSkew(windowSeq, 'A', 'T');
        case 'complexity':
            return calculateComplexity(windowSeq);
        case 'restriction_density': {
            const windowLengthKb = Math.max(1, end - start) / 1000;
            return countPositionsInRange(restrictionPositions, start, end) / windowLengthKb;
        }
        default:
            return 0;
    }
}

function formatMetricValue(metric: MetricDefinition, value: number): string {
    if (metric.id === 'restriction_density') {
        return `${value.toFixed(2)}${metric.suffix}`;
    }
    return `${value.toFixed(1)}${metric.suffix}`;
}

export function GCContentTrack({
    sequence,
    circular = false,
    selectedEnzymes = [],
    windowSize = 50,
    stepSize,
    height = 118,
    selection,
    onSelectionChange,
}: GCContentTrackProps) {
    const [metricId, setMetricId] = useState<MetricId>('gc');
    const metric = METRIC_INDEX.get(metricId) ?? METRIC_DEFINITIONS[0];
    const normalizedSequence = useMemo(() => normalizeSequence(sequence), [sequence]);
    const computedStepSize = stepSize ?? Math.max(8, Math.floor(windowSize / 3));

    const restrictionPositions = useMemo(() => {
        const allPositions = selectedEnzymes.flatMap((name) => {
            const enzyme = getRestrictionEnzyme(name);
            if (!enzyme) return [];
            return findRestrictionSites(normalizedSequence, enzyme.site, circular);
        });
        return Array.from(new Set(allPositions)).sort((left, right) => left - right);
    }, [circular, normalizedSequence, selectedEnzymes]);

    const analyticsData = useMemo<AnalyticsPoint[]>(() => {
        if (!normalizedSequence || normalizedSequence.length < 10) {
            return [];
        }

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

    const averageValue = useMemo(() => {
        if (analyticsData.length === 0) return 0;
        return analyticsData.reduce((sum, point) => sum + point.value, 0) / analyticsData.length;
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

    const markerStride = Math.max(1, Math.floor(analyticsData.length / 90));
    const markerPoints = analyticsData.filter((_, index) => index % markerStride === 0);
    const maxValue = analyticsData.reduce((max, point) => Math.max(max, point.value), 0);
    const yRange = metric.range ?? [0, Math.max(1, maxValue * 1.15)];

    const traces: Data[] = [
        {
            x: analyticsData.map((point) => point.position),
            y: smoothedValues,
            type: 'scatter',
            mode: 'lines',
            line: {
                width: 2.6,
                color: metric.color,
                shape: 'spline',
                smoothing: 0.6,
            },
            fill: 'tozeroy',
            fillcolor: metric.fillColor,
            hoverinfo: 'skip',
            showlegend: false,
        },
        {
            x: markerPoints.map((point) => point.position),
            y: markerPoints.map((point) => point.value),
            customdata: markerPoints.map((point) => [point.start + 1, point.end]),
            type: 'scatter',
            mode: 'markers',
            marker: {
                size: metric.id === 'restriction_density' ? 3.5 : 3,
                color: metric.color,
                opacity: 0.42,
            },
            hovertemplate: `<b>%{y:.2f}${metric.suffix}</b><br>%{customdata[0]:,.0f}-%{customdata[1]:,.0f}<extra></extra>`,
            showlegend: false,
        },
    ];

    const shapes: Partial<Shape>[] = [];
    if (typeof metric.baseline === 'number') {
        shapes.push({
            type: 'line',
            x0: 0,
            x1: normalizedSequence.length,
            y0: metric.baseline,
            y1: metric.baseline,
            line: { color: 'rgba(148, 163, 184, 0.65)', width: 1, dash: 'dot' },
        });
    }

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
                fillcolor: 'rgba(56, 189, 248, 0.12)',
                line: { color: 'rgba(56, 189, 248, 0.6)', width: 1 },
            });
        }
    }

    const layout: Partial<Layout> = {
        height,
        margin: { l: 46, r: 16, t: 44, b: 28 },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(15,23,42,0.75)',
        hovermode: 'x unified',
        showlegend: false,
        dragmode: 'zoom',
        xaxis: {
            showgrid: false,
            zeroline: false,
            color: '#94a3b8',
            tickfont: { size: 10, color: '#94a3b8' },
            range: [0, normalizedSequence.length],
            tickformat: ',',
        },
        yaxis: {
            showgrid: true,
            gridcolor: 'rgba(51,65,85,0.45)',
            zeroline: false,
            color: '#94a3b8',
            tickfont: { size: 10, color: '#94a3b8' },
            range: yRange,
            tickformat: metric.id === 'restriction_density' ? '.1f' : '.0f',
        },
        shapes,
    };

    return (
        <div className="gc-content-track border-b border-slate-700 bg-slate-950/20">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-3 py-2">
                <div className="flex flex-wrap items-center gap-1.5">
                    {METRIC_DEFINITIONS.map((definition) => (
                        <button
                            key={definition.id}
                            onClick={() => setMetricId(definition.id)}
                            className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                                metricId === definition.id
                                    ? 'border-cyan-500 bg-cyan-500/15 text-cyan-200'
                                    : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800'
                            }`}
                            title={definition.description}
                        >
                            {definition.label}
                        </button>
                    ))}
                </div>
                <div className="flex items-center gap-2 text-[11px] text-slate-500">
                    <span>{windowSize.toLocaleString()} bp window</span>
                    <span>•</span>
                    <span>{computedStepSize.toLocaleString()} bp step</span>
                    {metricId === 'restriction_density' && (
                        <>
                            <span>•</span>
                            <span>{selectedEnzymes.length} map enzymes</span>
                        </>
                    )}
                </div>
            </div>

            <div className="relative">
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

                <div
                    className="absolute left-3 top-2 rounded-full px-2 py-0.5 text-[11px] font-semibold text-white shadow-sm"
                    style={{ backgroundColor: metric.color }}
                    title={metric.description}
                >
                    {metric.badgeLabel}: {formatMetricValue(metric, averageValue)}
                </div>

                {selectionStats && (
                    <div className="absolute left-40 top-2 rounded-full border border-cyan-400/40 bg-slate-900/80 px-2 py-0.5 text-[11px] font-semibold text-cyan-100 shadow-sm">
                        Selection {metric.selectionLabel}: {formatMetricValue(metric, selectionStats.value)} ({selectionStats.length.toLocaleString()} bp)
                    </div>
                )}
            </div>
        </div>
    );
}
