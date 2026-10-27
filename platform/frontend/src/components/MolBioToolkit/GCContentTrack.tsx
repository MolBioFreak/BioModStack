/**
 * Sequence diagnostics track for GC and other practical construct QC metrics.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import { useThemeColors } from '../useThemeColors';
import type { Data, Layout, PlotSelectionEvent, Shape } from 'plotly.js';
import type { SelectionInfo } from './SequenceViewer';
import type { RestrictionOccurrence } from '../../lib/restrictionAnalysis';
import {
    createSelectionSnapshot,
    selectionForPlotDisplay,
    selectionFromPlotRange,
    sequenceForPlotDisplay,
    type SelectionRange,
} from './utils/selectionActions';
import { countPositionsInRange, shouldComputeRestrictionPositions } from './utils/gcTrackPolicy';

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
    range?: [number, number];
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
    restrictionOccurrences?: RestrictionOccurrence[];
    onRestrictionAnalysisRequested?: () => void;
    windowSize?: number;
    stepSize?: number;
    height?: number;
    selection?: SelectionInfo | null;
    onSelectionChange?: (selection: SelectionInfo) => void;
    onClearSelection?: () => void;
}

export function restrictionBoundaryPositions(
    occurrences: RestrictionOccurrence[],
    selectedEnzymes: string[],
    sequenceLength: number,
    reverseCoordinates = false,
    circular = false,
): number[] {
    if (sequenceLength <= 0) return [];
    const selected = new Set(selectedEnzymes);
    const seenDsbGroups = new Set<string>();
    const positions: number[] = [];
    for (const occurrence of occurrences) {
        if (!selected.has(occurrence.enzyme_id)) continue;
        for (const event of occurrence.double_strand_events) {
            if (event.status !== 'complete' || seenDsbGroups.has(event.contributor_group_id)) continue;
            const boundary = event.top_boundary;
            if (boundary === null || !Number.isInteger(boundary) || boundary < 0 || boundary > sequenceLength || (circular && boundary === sequenceLength)) continue;
            seenDsbGroups.add(event.contributor_group_id);
            positions.push(boundary);
        }
        for (const event of occurrence.nicks) {
            const boundary = event.boundary;
            if (event.status !== 'complete' || boundary === null || !Number.isInteger(boundary) || boundary < 0 || boundary > sequenceLength || (circular && boundary === sequenceLength)) continue;
            positions.push(boundary);
        }
    }
    const displayed = positions.map((position) => reverseCoordinates
        ? (circular ? (sequenceLength - position) % sequenceLength : sequenceLength - position)
        : position);
    return displayed.sort((left, right) => left - right);
}

const CANONICAL_BASES = new Set(['A', 'C', 'G', 'T']);

const METRIC_DEFINITIONS: MetricDefinition[] = [
    {
        id: 'gc',
        label: 'GC Content',
        shortLabel: 'GC%',
        description: 'Measured GC percentage in each sequence window; no smoothing. Ambiguous bases are excluded from the GC denominator.',
        range: [0, 100],
        suffix: '%',
        tickFormat: '.0f',
        decimals: 1,
        summaryLabel: 'Whole-sequence GC',
    },
    {
        id: 'restriction_density',
        label: 'Restriction Density',
        shortLabel: 'Cuts/kb',
        description: 'Cut-site density for the currently mapped enzyme set. Useful when reviewing cloning-accessible regions.',
        suffix: '/kb',
        tickFormat: '.1f',
        decimals: 2,
        summaryLabel: 'Mean density',
    },
    {
        id: 'ambiguity_density',
        label: 'Ambiguity Load',
        shortLabel: 'Ambiguous bases %',
        description: 'Fraction of ambiguous or non-canonical bases in each window. This should usually be zero.',
        range: [0, 100],
        suffix: '%',
        tickFormat: '.0f',
        decimals: 1,
        summaryLabel: 'Ambiguous',
    },
    {
        id: 'homopolymer_burden',
        label: 'Homopolymer Burden',
        shortLabel: 'Homopolymer runs ≥4',
        description: 'Percent of bases sitting inside homopolymer runs of length 4 or more. Useful for synthesis and sequencing QC.',
        range: [0, 100],
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
            return countPositionsInRange(restrictionPositions, start, end, end === sequence.length) / windowLengthKb;
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
            (total, range) => total + countPositionsInRange(restrictionPositions, range.start, range.end, range.end === sequence.length),
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

    const observedMax = Math.max(...values);

    if (metric.id === 'gc') return [0, 100];

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
    restrictionOccurrences = [],
    onRestrictionAnalysisRequested,
    windowSize = 60,
    stepSize,
    height = 156,
    selection,
    onSelectionChange,
    onClearSelection,
}: GCContentTrackProps) {
    const colors = useThemeColors();
    const [metricId, setMetricId] = useState<MetricId>('gc');
    useEffect(() => {
        if (metricId === 'restriction_density') onRestrictionAnalysisRequested?.();
    }, [metricId, onRestrictionAnalysisRequested]);
    const metric = METRIC_INDEX.get(metricId) ?? METRIC_DEFINITIONS[0];
    const normalizedSequence = useMemo(
        () => normalizeSequence(sequenceForPlotDisplay(sequence, sequenceType, reverseCoordinates)),
        [reverseCoordinates, sequence, sequenceType],
    );
    const computedStepSize = stepSize ?? Math.max(12, Math.floor(windowSize / 3));

    const restrictionPositions = useMemo(() => {
        if (!shouldComputeRestrictionPositions(metricId)) {
            return [];
        }
        return restrictionBoundaryPositions(
            restrictionOccurrences,
            selectedEnzymes,
            normalizedSequence.length,
            reverseCoordinates,
            circular,
        );
    }, [circular, metricId, normalizedSequence.length, restrictionOccurrences, reverseCoordinates, selectedEnzymes]);

    const analyticsData = useMemo<AnalyticsPoint[]>(() => {
        if (!normalizedSequence) return [];

        return buildWindows(normalizedSequence.length, windowSize, computedStepSize).map(({ start, end }) => ({
            start,
            end,
            position: start + Math.floor((end - start) / 2),
            value: computeMetricValue(metricId, normalizedSequence, start, end, restrictionPositions),
        }));
    }, [computedStepSize, metricId, normalizedSequence, restrictionPositions, windowSize]);

    const summary = useMemo(() => {
        if (analyticsData.length === 0) {
            return { mean: 0, minimum: 0, maximum: 0 };
        }
        const rawValues = analyticsData.map((point) => point.value);
        return {
            mean: computeMetricValue(metricId, normalizedSequence, 0, normalizedSequence.length, restrictionPositions),
            minimum: Math.min(...rawValues),
            maximum: Math.max(...rawValues),
        };
    }, [analyticsData, metricId, normalizedSequence, restrictionPositions]);

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

    const rawValues = useMemo(() => analyticsData.map((point) => point.value), [analyticsData]);
    const yRange = useMemo(() => calculateYRange(metric, rawValues), [metric, rawValues]);

    const traces = useMemo<Data[]>(() => [{
        x: analyticsData.map(point => point.position),
        y: rawValues,
        width: Math.max(1, computedStepSize * .88),
        customdata: analyticsData.map(point => [point.start + 1, point.end]),
        type: 'bar',
        marker: { color: colors.accentPrimary },
        hovertemplate: `<b>${metric.label}</b><br>Bases %{customdata[0]:,.0f}–%{customdata[1]:,.0f}<br>%{y:.${metric.decimals}f}${metric.suffix}<extra></extra>`,
        showlegend: false,
    }], [analyticsData, colors.accentPrimary, computedStepSize, metric, rawValues]);

    const layout = useMemo<Partial<Layout>>(() => {
        const shapes: Partial<Shape>[] = [];
        shapes.push({
            type: 'line',
            x0: 0,
            x1: normalizedSequence.length,
            y0: summary.mean,
            y1: summary.mean,
            line: { color: colors.textSecondary, width: 1, dash: 'dot' },
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
        return {
            height,
            margin: { l: 50, r: 14, t: 6, b: 28 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: colors.bgPrimary,
            hovermode: 'x unified',
            showlegend: false,
            dragmode: 'select',
            selectdirection: 'h',
            uirevision: `sequence-diagnostics-${metric.id}-${normalizedSequence.length}`,
            xaxis: {
                showgrid: false,
                zeroline: false,
                        tickfont: { size: 12, color: colors.textSecondary },
                range: [0, normalizedSequence.length],
                tickformat: ',',
                fixedrange: false,
                title: { text: 'Position (bp)', font: { size: 12, color: colors.textSecondary } },
            },
            yaxis: {
                showgrid: true,
                gridcolor: colors.borderPrimary,
                zeroline: false,
                        tickfont: { size: 12, color: colors.textSecondary },
                range: yRange,
                tickformat: metric.tickFormat,
                fixedrange: true,
                title: { text: metric.shortLabel, font: { size: 12, color: colors.textSecondary } },
            },
            shapes,
            hoverlabel: {
                bgcolor: colors.bgSecondary,
                bordercolor: colors.borderPrimary,
                font: { color: colors.textPrimary, size: 11 },
            },
        };
    }, [colors, height, metric, normalizedSequence.length, selectionSnapshot, summary.mean, yRange]);

    if (!normalizedSequence || analyticsData.length === 0) {
        return null;
    }

    return (
        <div className="border-b border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-2">
            <div className="overflow-hidden rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)]">
                <div className="flex min-w-0 flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-2">
                    <div className="mr-1 flex shrink-0 items-baseline gap-2">
                        <span className="text-sm font-semibold text-[var(--text-primary)]">Sequence analytics</span>
                        <span className="text-xs font-semibold text-slate-200">{metric.label}</span>
                    </div>
                    <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto py-0.5">
                            {METRIC_DEFINITIONS.map((definition) => (
                                <button
                                    key={definition.id}
                                    onClick={() => setMetricId(definition.id)}
                                    className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors ${
                                        metricId === definition.id
                                            ? 'border-[var(--accent-primary)] bg-[var(--bg-tertiary)] text-[var(--text-primary)]'
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

                <div className="grid grid-cols-2 gap-2 border-b border-[var(--border-primary)] p-3 text-sm lg:grid-cols-4" aria-label="Sequence composition summary">
                    <div><p className="text-xs text-[var(--text-secondary)]">Whole-sequence GC</p><p className="text-2xl font-semibold text-[var(--text-primary)]">{calculateGC(normalizedSequence).toFixed(1)}%</p></div>
                    <div><p className="text-xs text-[var(--text-secondary)]">Sequence length</p><p className="font-semibold">{normalizedSequence.length.toLocaleString()} bases</p></div>
                    <div><p className="text-xs text-[var(--text-secondary)]">Ambiguous bases</p><p className="font-semibold">{Array.from(normalizedSequence).filter(base => !CANONICAL_BASES.has(base)).length.toLocaleString()}</p></div>
                    <div><p className="text-xs text-[var(--text-secondary)]">Selected metric · window range</p><p className="font-semibold">{formatRange(metric, summary.minimum, summary.maximum)}</p></div>
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
                        <span>Bars show measured window values; no smoothing. Dotted line: whole-sequence value.</span>
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
