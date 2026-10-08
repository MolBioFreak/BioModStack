/**
 * Sequence diagnostics track for GC and other practical construct QC metrics.
 */

import { useCallback, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout, PlotSelectionEvent, Shape } from 'plotly.js';
import type { SelectionInfo } from './SequenceViewer';
import { findRestrictionSites, getRestrictionEnzyme } from './utils/restrictionEnzymes';
import {
    createSelectionSnapshot,
    selectionForPlotDisplay,
    selectionFromPlotRange,
    sequenceForPlotDisplay,
    type SelectionRange,
} from './utils/selectionActions';

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
    sequenceType?: 'dna' | 'rna';
    reverseCoordinates?: boolean;
    circular?: boolean;
    selectedEnzymes?: string[];
    windowSize?: number;
    stepSize?: number;
    height?: number;
    selection?: SelectionInfo | null;
    onSelectionChange?: (selection: SelectionInfo) => void;
    onClearSelection?: () => void;
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

function computeSelectionMetricValue(
    metric: MetricId,
    sequence: string,
    ranges: SelectionRange[],
    restrictionPositions: number[],
): number {
    const totalLength = ranges.reduce((total, range) => total + (range.end - range.start), 0);
    if (totalLength <= 0) return 0;

    if (metric === 'restriction_density') {
        const cutCount = ranges.reduce(
            (total, range) => total + countPositionsInRange(restrictionPositions, range.start, range.end),
            0,
        );
        return cutCount / (totalLength / 1000);
    }

    const selectedSequence = ranges
        .map((range) => sequence.slice(range.start, range.end))
        .join('');
    return computeMetricValue(metric, selectedSequence, 0, selectedSequence.length, []);
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
    sequenceType = 'dna',
    reverseCoordinates = false,
    circular = false,
    selectedEnzymes = [],
    windowSize = 60,
    stepSize,
    height = 156,
    selection,
    onSelectionChange,
    onClearSelection,
}: GCContentTrackProps) {
    const [metricId, setMetricId] = useState<MetricId>('gc');
    const metric = METRIC_INDEX.get(metricId) ?? METRIC_DEFINITIONS[0];
    const normalizedSequence = useMemo(
        () => normalizeSequence(sequenceForPlotDisplay(sequence, sequenceType, reverseCoordinates)),
        [reverseCoordinates, sequence, sequenceType],
    );
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

    const plotSelection = useMemo(
        () => selectionForPlotDisplay(
            selection,
            normalizedSequence.length,
            circular,
            reverseCoordinates,
        ),
        [circular, normalizedSequence.length, reverseCoordinates, selection],
    );
    const selectionSnapshot = useMemo(
        () => createSelectionSnapshot(plotSelection, normalizedSequence, circular),
        [circular, normalizedSequence, plotSelection],
    );

    const selectionStats = useMemo(() => {
        if (!selectionSnapshot) return null;

        return {
            value: computeSelectionMetricValue(
                metricId,
                normalizedSequence,
                selectionSnapshot.ranges,
                restrictionPositions,
            ),
            length: selectionSnapshot.length,
        };
    }, [metricId, normalizedSequence, restrictionPositions, selectionSnapshot]);

    const handleSelected = useCallback((event: PlotSelectionEvent) => {
        if (!onSelectionChange || !normalizedSequence) return;
        const mappedSelection = selectionFromPlotRange(
            event.range?.x,
            normalizedSequence.length,
            reverseCoordinates,
        );
        if (mappedSelection) {
            onSelectionChange(mappedSelection);
        }
    }, [normalizedSequence, onSelectionChange, reverseCoordinates]);

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
                width: 1.2,
                color: metric.color,
                shape: 'linear',
            },
            opacity: 0.36,
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
                width: 2.6,
                color: metric.color,
                shape: 'linear',
            },
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
            layer: 'below',
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
        layer: 'below',
    });

    if (selectionSnapshot) {
        selectionSnapshot.ranges.forEach((range) => {
            shapes.push({
                type: 'rect',
                x0: range.start,
                x1: range.end,
                y0: yRange[0],
                y1: yRange[1],
                fillcolor: 'rgba(34, 211, 238, 0.12)',
                line: { color: 'rgba(34, 211, 238, 0.62)', width: 1 },
                layer: 'below',
            });
        });
    }

    const layout: Partial<Layout> = {
        height,
        margin: { l: 50, r: 14, t: 6, b: 28 },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(2, 6, 23, 0.62)',
        hovermode: 'x unified',
        showlegend: false,
        dragmode: 'select',
        selectdirection: 'h',
        uirevision: `sequence-diagnostics-${metric.id}-${normalizedSequence.length}`,
        xaxis: {
            showgrid: false,
            zeroline: false,
            color: '#94a3b8',
            tickfont: { size: 10, color: '#64748b' },
            range: [0, normalizedSequence.length],
            tickformat: ',',
            fixedrange: false,
            title: { text: 'Position (bp)', font: { size: 10, color: '#64748b' } },
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
            title: { text: metric.shortLabel, font: { size: 10, color: '#64748b' } },
        },
        shapes,
        hoverlabel: {
            bgcolor: '#020617',
            bordercolor: '#1e293b',
            font: { color: '#e2e8f0', size: 11 },
        },
    };

    return (
        <div className="border-b border-slate-700 bg-slate-950/20 px-2 py-2">
            <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/70 shadow-[0_0_0_1px_rgba(15,23,42,0.5)]">
                <div className="flex min-w-0 flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-2">
                    <div className="mr-1 flex shrink-0 items-baseline gap-2">
                        <span className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Profile</span>
                        <span className="text-xs font-semibold text-slate-200">{metric.label}</span>
                    </div>
                    <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto py-0.5">
                            {METRIC_DEFINITIONS.map((definition) => (
                                <button
                                    key={definition.id}
                                    onClick={() => setMetricId(definition.id)}
                                    className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors ${
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
                    <div className="ml-auto flex shrink-0 items-center gap-1.5 text-[10px]">
                        <div className="rounded-full border border-slate-700 bg-slate-950 px-2 py-0.5 text-slate-200">
                            {metric.summaryLabel}: {formatMetricValue(metric, summary.mean)}
                        </div>
                        <div className="hidden rounded-full border border-slate-700 bg-slate-950 px-2 py-0.5 text-slate-400 xl:block">
                            Range: {formatRange(metric, summary.minimum, summary.maximum)}
                        </div>
                        {selectionStats && (
                            <div className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-cyan-100">
                                Selected {selectionSnapshot?.coordinateLabel}: {formatMetricValue(metric, selectionStats.value)}
                            </div>
                        )}
                    </div>
                </div>

                <div className="px-2 py-1">
                    <Plot
                        data={traces}
                        layout={layout}
                        config={{
                            displayModeBar: false,
                            responsive: true,
                            staticPlot: false,
                            scrollZoom: false,
                        }}
                        style={{ width: '100%', height }}
                        useResizeHandler={true}
                        onSelected={handleSelected}
                        onDoubleClick={() => onClearSelection?.()}
                    />
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 px-3 py-1.5 text-[10px] text-slate-500">
                    <div className="flex flex-wrap items-center gap-2">
                        <span>{windowSize.toLocaleString()} bp window</span>
                        <span>•</span>
                        <span>{computedStepSize.toLocaleString()} bp step</span>
                        <span>•</span>
                        <span className="inline-flex items-center gap-1">
                            <span className="h-px w-4 bg-slate-500" /> raw
                        </span>
                        <span className="inline-flex items-center gap-1">
                            <span className="h-0.5 w-4" style={{ backgroundColor: metric.color }} /> 3-window mean
                        </span>
                        {metricId === 'restriction_density' && (
                            <>
                                <span>•</span>
                                <span>{selectedEnzymes.length} mapped enzymes</span>
                            </>
                        )}
                    </div>
                    <div className="flex items-center gap-2">
                        <span>Drag horizontally to select a durable sequence range.</span>
                        {selectionSnapshot && onClearSelection && (
                            <button
                                type="button"
                                onClick={onClearSelection}
                                className="rounded border border-slate-700 px-2 py-1 text-slate-300 transition-colors hover:bg-slate-800"
                            >
                                Clear range
                            </button>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
