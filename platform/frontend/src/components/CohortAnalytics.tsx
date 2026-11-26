import { useEffect, useMemo, useRef, useState, type JSX, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { PlotlyLab, type MetricPickerProps } from './PlotlyLab';
import Plot from 'react-plotly.js';
import type { Config, Data, Layout, PlotMouseEvent, PlotSelectionEvent } from 'plotly.js';
import {
    formatMetric,
    histogramTraceFromStored,
    isFiniteMetric,
    metricKeys,
    metricLabel,
    numericMetricKeys,
    secondaryStructureComposition,
    storedMetricSummary,
    type CohortRow,
    type CorrelationSummary,
    type StoredToolkitColumn,
} from '../lib/cohortAnalytics';
import {
    fetchJobAnalysis,
    triggerJobAnalysis,
    type CorrelationMatrix,
    type PersistedAnalysisRun,
} from '../lib/api';
import type { ScientificCohort } from '../lib/scientificAnalytics';
import {
    StatsPanelControls,
    type CorrelationRecomputeOptions,
    type SummaryRecomputeOptions,
} from './StatsPanelControls';

export interface CohortInitialMetrics { x?: string; y?: string; distribution?: string; headline?: string[] }

interface StoredSummaryResult {
    columns?: Record<string, StoredToolkitColumn>;
    metrics?: Record<string, StoredToolkitColumn>;
    scientific_cohorts?: ScientificCohort[];
}

interface Props {
    initialMetrics?: CohortInitialMetrics;
    retentionKey?: string;
    rows: CohortRow[];
    selectedIds: string[];
    activeId?: string;
    onInspect: (id: string) => void;
    onSelect: (ids: string[]) => void;
    mode: 'dashboard' | 'analytics';
    getMetricLabel?: (key: string) => string;
    getMetricDescription?: (key: string) => string;
    renderMetricPicker?: (props: MetricPickerProps) => ReactNode;
    inspectionHint?: string;
    jobId?: string;
    cohortKey?: string;
    storedCohort?: ScientificCohort;
    statsRunId?: string | null;
    statsStatus?: string | null;
    statsReason?: string | null;
    statsStale?: boolean;
}
const CONFIG: Partial<Config> = {
    responsive: true, displayModeBar: true, displaylogo: false,
    toImageButtonOptions: { format: 'svg', filename: 'native-cohort', width: 1000, height: 650, scale: 1 },
};
const control = 'min-w-0 max-w-full rounded border border-[var(--border-color)] bg-[var(--bg-primary)] p-2 text-sm text-[var(--text-primary)]';
const card = 'min-w-0 rounded-lg border border-[var(--border-color)] bg-[var(--bg-secondary)] p-3';
const escape = (text: string) => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

export function usePlotTheme() {
    const [theme, setTheme] = useState({ text: '#94a3b8', grid: '#64748b', background: 'transparent' });
    useEffect(() => {
        const update = () => {
            const style = getComputedStyle(document.documentElement);
            setTheme({ text: style.getPropertyValue('--text-primary').trim() || '#94a3b8',
                grid: style.getPropertyValue('--border-color').trim() || '#64748b',
                background: style.getPropertyValue('--bg-secondary').trim() || 'transparent' });
        };
        update();
        const observer = new MutationObserver(update);
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
        observer.observe(document.body, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
        const media = window.matchMedia('(prefers-color-scheme: dark)');
        media.addEventListener('change', update);
        return () => { observer.disconnect(); media.removeEventListener('change', update); };
    }, []);
    return theme;
}

/** Observe the actual panel, including side-panel and tab layout changes, not just window resizes. */
export function CohortPlot({ label, data, layout, onClick, onSelected, height = 350 }: {
    label: string; data: Data[]; layout: Partial<Layout>; height?: number;
    onClick?: (event: PlotMouseEvent) => void; onSelected?: (event: PlotSelectionEvent) => void;
}) {
    const host = useRef<HTMLDivElement>(null);
    const [width, setWidth] = useState<number>();
    useEffect(() => {
        const element = host.current;
        if (!element) return;
        const resize = () => { if (element.clientWidth > 0) setWidth(element.clientWidth); };
        resize();
        const observer = new ResizeObserver(resize);
        observer.observe(element);
        return () => observer.disconnect();
    }, []);
    return <div ref={host} role="region" aria-label={label} className="min-w-0 overflow-hidden">
        <Plot data={data} layout={{ ...layout, autosize: true, width, height }} config={CONFIG}
            useResizeHandler style={{ width: '100%', height }} onClick={onClick} onSelected={onSelected} />
    </div>;
}

function lookupStoredColumn(
    run: PersistedAnalysisRun<StoredSummaryResult> | undefined,
    cohortKey: string | undefined,
    storedCohort: ScientificCohort | undefined,
    key: string,
): StoredToolkitColumn | null {
    const result = run?.result;
    const columns = result?.columns;
    if (columns && typeof columns === 'object') {
        if (cohortKey && columns[`cohort::${cohortKey}::${key}`]) {
            return columns[`cohort::${cohortKey}::${key}`];
        }
        if (columns[key]) {
            return columns[key];
        }
        const suffix = `::${key}`;
        const matched = Object.entries(columns).find(([colKey]) => colKey.endsWith(suffix));
        if (matched) return matched[1];
    }
    if (result?.metrics && result.metrics[key]) {
        return result.metrics[key];
    }
    const cohortMetric = storedCohort?.metrics?.[key];
    if (cohortMetric?.statistics) {
        return {
            count: cohortMetric.observed_count,
            ...cohortMetric.statistics,
        };
    }
    return null;
}

function lookupStoredCorrelation(
    corrRun: PersistedAnalysisRun<CorrelationMatrix> | undefined,
    storedCohort: ScientificCohort | undefined,
    xKey: string,
    yKey: string,
    unavailable: boolean,
): CorrelationSummary {
    const result = corrRun?.result;
    if (xKey === yKey) {
        if (result?.metrics) {
            const idx = result.metrics.indexOf(xKey);
            if (idx >= 0) {
                const n = result.sample_sizes?.[idx]?.[idx] ?? 0;
                return { pairs: n, r: 1, reason: null };
            }
        }
        return { pairs: 0, r: null, reason: unavailable ? 'stats_toolkit_unavailable' : 'not_computed' };
    }
    if (result?.pairs) {
        const pair = result.pairs.find(
            (item) => (item.var_a === xKey && item.var_b === yKey) || (item.var_a === yKey && item.var_b === xKey),
        );
        if (pair) {
            return {
                pairs: typeof pair.n === 'number' ? pair.n : 0,
                r: isFiniteMetric(pair.statistic) ? pair.statistic : null,
                reason: isFiniteMetric(pair.statistic) ? null : 'undefined',
            };
        }
    }
    if (result?.metrics && Array.isArray(result.matrix)) {
        const ix = result.metrics.indexOf(xKey);
        const iy = result.metrics.indexOf(yKey);
        if (ix >= 0 && iy >= 0) {
            const value = result.matrix[ix]?.[iy];
            const n = result.sample_sizes?.[ix]?.[iy] ?? 0;
            if (isFiniteMetric(value)) {
                return { pairs: n, r: value, reason: null };
            }
            return { pairs: n, r: null, reason: 'undefined' };
        }
    }
    const pairFromCohort = storedCohort?.pairs?.[`${xKey}_vs_${yKey}`] ?? storedCohort?.pairs?.[`${yKey}_vs_${xKey}`];
    if (pairFromCohort) {
        if (pairFromCohort.correlation.state === 'ok') {
            return { pairs: pairFromCohort.pair_count, r: pairFromCohort.correlation.value, reason: null };
        }
        return { pairs: pairFromCohort.pair_count, r: null, reason: pairFromCohort.correlation.reason_code };
    }
    return { pairs: 0, r: null, reason: unavailable ? 'stats_toolkit_unavailable' : 'not_computed' };
}

export function CohortAnalytics({
    retentionKey, rows, selectedIds, activeId, onInspect, onSelect, mode, initialMetrics,
    getMetricLabel = metricLabel, getMetricDescription, renderMetricPicker,
    inspectionHint = 'click a point to inspect its exact native document',
    jobId, cohortKey, storedCohort, statsRunId, statsStatus, statsReason, statsStale,
}: Props): JSX.Element {
    const queryClient = useQueryClient();
    const caption = getMetricLabel;
    const description = (key: string) => getMetricDescription?.(key) || `${caption(key)}: recorded value; missing values are omitted.`;
    // Settings are browser-session UI state, not scientific data or workspace selection.
    const [saved] = useState<Record<string, unknown>>(() => {
        try {
            const value = retentionKey ? JSON.parse(sessionStorage.getItem(`${retentionKey}:settings`) ?? '{}') : {};
            return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
        } catch { return {}; }
    });
    const [palette, setPalette] = useState(typeof saved.palette === 'string' ? saved.palette : 'Viridis');
    const [reverse, setReverse] = useState(typeof saved.reverse === 'boolean' ? saved.reverse : false);
    const numeric = useMemo(() => numericMetricKeys(rows), [rows]);
    const allKeys = useMemo(() => metricKeys(rows), [rows]);
    const [xChoice, setXChoice] = useState(typeof saved.xChoice === 'string' ? saved.xChoice : initialMetrics?.x ?? 'seq_length');
    const [yChoice, setYChoice] = useState(typeof saved.yChoice === 'string' ? saved.yChoice : initialMetrics?.y ?? 'dsasa');
    const [distributionChoice, setDistributionChoice] = useState(typeof saved.distributionChoice === 'string' ? saved.distributionChoice : initialMetrics?.distribution ?? 'dsasa');
    const [colorChoice, setColorChoice] = useState(typeof saved.colorChoice === 'string' ? saved.colorChoice : '');
    const [dragMode, setDragMode] = useState<'zoom' | 'select' | 'lasso'>(saved.dragMode === 'select' || saved.dragMode === 'lasso' ? saved.dragMode : 'zoom');
    const [heatChoice, setHeatChoice] = useState<string[] | null>(Array.isArray(saved.heatChoice) && saved.heatChoice.every(key => typeof key === 'string') ? saved.heatChoice : null);
    const [heatAdd, setHeatAdd] = useState(typeof saved.heatAdd === 'string' ? saved.heatAdd : '');
    const [labDimension, setLabDimension] = useState<'2D' | '3D'>(saved.labDimension === '3D' ? '3D' : '2D');
    const [metricSearch, setMetricSearch] = useState(typeof saved.metricSearch === 'string' ? saved.metricSearch : '');
    const [metricPage, setMetricPage] = useState(typeof saved.metricPage === 'number' && Number.isSafeInteger(saved.metricPage) && saved.metricPage >= 0 ? saved.metricPage : 0);
    const [zChoice, setZChoice] = useState(typeof saved.zChoice === 'string' ? saved.zChoice : 'radius_of_gyration');
    const [color3dChoice, setColor3dChoice] = useState(typeof saved.color3dChoice === 'string' ? saved.color3dChoice : '');
    const [barChoice, setBarChoice] = useState(typeof saved.barChoice === 'string' ? saved.barChoice : 'dsasa');
    useEffect(() => {
        if (!retentionKey) return;
        try { sessionStorage.setItem(`${retentionKey}:settings`, JSON.stringify({ palette, reverse, xChoice, yChoice, distributionChoice, colorChoice, dragMode, heatChoice, heatAdd, labDimension, metricSearch, metricPage, zChoice, color3dChoice, barChoice })); }
        catch { /* Unavailable session storage must not stop chart interaction. */ }
    }, [retentionKey, palette, reverse, xChoice, yChoice, distributionChoice, colorChoice, dragMode, heatChoice, heatAdd, labDimension, metricSearch, metricPage, zChoice, color3dChoice, barChoice]);

    const summaryQuery = useQuery({
        queryKey: ['job-analysis', jobId, 'job_metric_summary'],
        queryFn: () => fetchJobAnalysis<StoredSummaryResult>(jobId!, 'job_metric_summary', { include_children: true }).then((res) => res.data),
        enabled: Boolean(jobId),
        retry: false,
        staleTime: 30_000,
    });

    const correlationQuery = useQuery({
        queryKey: ['job-analysis', jobId, 'job_correlation_matrix'],
        queryFn: () => fetchJobAnalysis<CorrelationMatrix>(jobId!, 'job_correlation_matrix', { include_children: true }).then((res) => res.data),
        enabled: Boolean(jobId && mode === 'analytics'),
        retry: false,
        staleTime: 30_000,
    });

    const recomputeSummary = useMutation({
        mutationFn: (options: SummaryRecomputeOptions) =>
            triggerJobAnalysis<StoredSummaryResult>(jobId!, 'job_metric_summary', {
                include_children: true,
                histogram_bins: options.histogram_bins,
                shared_bins: options.shared_bins,
            }, true).then((res) => res.data),
        onSuccess: (updated) => {
            queryClient.setQueryData(['job-analysis', jobId, 'job_metric_summary'], updated);
            void queryClient.invalidateQueries({ queryKey: ['job-analysis', jobId, 'job_metric_summary'] });
            void queryClient.invalidateQueries({ queryKey: ['job-analytics', jobId] });
        },
    });

    const recomputeCorrelation = useMutation({
        mutationFn: (options: CorrelationRecomputeOptions) =>
            triggerJobAnalysis<CorrelationMatrix>(jobId!, 'job_correlation_matrix', {
                include_children: true,
                method: options.method,
                p_adjust_method: options.p_adjust_method,
            }, true).then((res) => res.data),
        onSuccess: (updated) => {
            queryClient.setQueryData(['job-analysis', jobId, 'job_correlation_matrix'], updated);
            void queryClient.invalidateQueries({ queryKey: ['job-analysis', jobId, 'job_correlation_matrix'] });
        },
    });

    const summaryRun = summaryQuery.data;
    const correlationRun = correlationQuery.data;
    const effectiveSummaryRunId = summaryRun?.stats_run?.run_id ?? statsRunId ?? null;
    const effectiveSummaryStatus = summaryRun?.status ?? (summaryQuery.isError ? 'unavailable' : statsStatus ?? null);
    const effectiveSummaryReason = summaryRun?.reason ?? statsReason ?? null;
    const effectiveSummaryStale = Boolean(summaryRun?.stale ?? statsStale);
    const summaryUnavailable = effectiveSummaryStatus === 'unavailable' || effectiveSummaryReason === 'stats_toolkit_unavailable';

    const effectiveCorrRunId = correlationRun?.stats_run?.run_id ?? null;
    const effectiveCorrStatus = correlationRun?.status ?? (correlationQuery.isError ? 'unavailable' : null);
    const effectiveCorrReason = correlationRun?.reason ?? null;
    const effectiveCorrStale = Boolean(correlationRun?.stale);
    const corrUnavailable = effectiveCorrStatus === 'unavailable' || effectiveCorrReason === 'stats_toolkit_unavailable';
    const corrMethod = (correlationRun?.result?.method ?? (correlationRun?.params?.method as string | undefined) ?? 'pearson').toLowerCase();
    const corrMethodLabel = corrMethod === 'spearman' ? 'Spearman ρ' : corrMethod === 'kendall' ? 'Kendall τ' : 'Pearson r';

    const theme = usePlotTheme();
    const xKey = numeric.includes(xChoice) ? xChoice : numeric.includes('seq_length') ? 'seq_length' : numeric[0] ?? '';
    const yKey = numeric.includes(yChoice) ? yChoice : numeric.includes('dsasa') ? 'dsasa' : numeric[1] ?? numeric[0] ?? '';
    const distKey = numeric.includes(distributionChoice) ? distributionChoice : yKey;
    const zKey = numeric.includes(zChoice) ? zChoice : numeric.find(key => key !== xKey && key !== yKey) ?? '';
    const barKey = numeric.includes(barChoice) ? barChoice : yKey;
    const color3dKey = numeric.includes(color3dChoice) ? color3dChoice : '';
    const colorKey = mode === 'analytics' && numeric.includes(colorChoice) ? colorChoice : '';
    const selected = useMemo(() => new Set(selectedIds), [selectedIds]);
    const pairs = useMemo(() => rows.filter(row => isFiniteMetric(row.values[xKey]) && isFiniteMetric(row.values[yKey])), [rows, xKey, yKey]);
    const distributionCount = useMemo(() => rows.filter(row => isFiniteMetric(row.values[distKey])).length, [rows, distKey]);
    const distributionSummary = useMemo(
        () => storedMetricSummary(lookupStoredColumn(summaryRun, cohortKey, storedCohort, distKey), rows, distKey),
        [summaryRun, cohortKey, storedCohort, rows, distKey],
    );
    const histogramTrace = useMemo(
        () => histogramTraceFromStored(distributionSummary.histogramBins, distributionSummary.histogramCounts, {
            label: caption(distKey),
            color: '#3b82f6',
            lineColor: theme.background,
            hoverUnit: 'Records',
        }),
        [distributionSummary, distKey, theme.background, getMetricLabel],
    );
    const barRows = useMemo(() => rows.filter(row => isFiniteMetric(row.values[barKey]))
        .sort((a, b) => (b.values[barKey] as number) - (a.values[barKey] as number)).slice(0, 20), [rows, barKey]);
    const active = rows.find(row => row.id === activeId);
    const composition = secondaryStructureComposition(active);
    const spacePoints = rows.filter(row => isFiniteMetric(row.values[xKey]) && isFiniteMetric(row.values[yKey]) && isFiniteMetric(row.values[zKey]));
    const heatKeys = (heatChoice ?? (initialMetrics ? [...new Set([initialMetrics.x, initialMetrics.y, initialMetrics.distribution, 'seq_length', 'duration_seconds', ...numeric].filter((key): key is string => !!key && numeric.includes(key)))].slice(0, 6) : numeric.slice(0, 6))).filter(key => numeric.includes(key));
    const heatKeyIdentity = JSON.stringify(heatKeys);
    const correlations = useMemo(() => {
        const keys: string[] = JSON.parse(heatKeyIdentity);
        return mode === 'analytics'
            ? keys.map(y => keys.map(x => lookupStoredCorrelation(correlationRun, storedCohort, x, y, corrUnavailable)))
            : [];
    }, [correlationRun, storedCohort, corrUnavailable, heatKeyIdentity, mode]);
    const hasAnyStoredCorrelation = useMemo(
        () => correlations.some((row) => row.some((cell) => cell.r !== null)),
        [correlations],
    );
    const searchedKeys = allKeys.filter(key => `${key} ${getMetricLabel(key)}`.toLowerCase().includes(metricSearch.toLowerCase()));
    const page = Math.min(metricPage, Math.max(0, Math.ceil(searchedKeys.length / 20) - 1));
    const summaryKeys = searchedKeys.slice(page * 20, (page + 1) * 20);
    const summaries = useMemo(
        () => mode === 'analytics'
            ? allKeys.map(key => ({ key, ...storedMetricSummary(lookupStoredColumn(summaryRun, cohortKey, storedCohort, key), rows, key) }))
            : [],
        [summaryRun, cohortKey, storedCohort, rows, allKeys, mode],
    );
    const summaryMap = new Map(summaries.map(summary => [summary.key, summary]));
    const axis = (title: string) => ({ title: { text: escape(title), font: { size: 11 } }, automargin: true, gridcolor: theme.grid, zeroline: false });
    const base: Partial<Layout> = { paper_bgcolor: theme.background, plot_bgcolor: theme.background,
        font: { color: theme.text, size: 11 }, margin: { l: 58, r: 24, t: 24, b: 65 }, showlegend: false, hovermode: 'closest' };
    const picker = (label: string, value: string, change: (value: string) => void, optional = false) => renderMetricPicker ? renderMetricPicker({ label, keys: numeric, value, onChange: change, allowEmpty: optional }) : <label className="flex min-w-0 flex-1 items-center gap-2 text-xs">
        <span className="shrink-0">{label.replace(' metric', '')}</span><select className={`${control} w-full`} aria-label={label} title={caption(value)} value={value} onChange={event => change(event.target.value)}>
            {optional && <option value="">No color metric</option>}
            {numeric.map(key => <option key={key} value={key} title={key}>{getMetricLabel(key)}</option>)}
        </select>
    </label>;
    const trace = (points: CohortRow[], colored: boolean): Data => ({
        type: 'scatter', mode: 'markers', name: colored ? caption(colorKey) : colorKey ? 'Color unavailable' : 'Candidates',
        x: points.map(row => row.values[xKey] as number), y: points.map(row => row.values[yKey] as number),
        customdata: points.map(row => row.id), text: points.map(row => escape(row.label)),
        hovertemplate: `%{text}<br>${escape(caption(xKey))}: %{x}<br>${escape(caption(yKey))}: %{y}${colorKey ? colored ? `<br>${escape(caption(colorKey))}: %{marker.color}` : '<br>Color metric: unavailable' : ''}<extra></extra>`,
        marker: { size: points.map(row => row.id === activeId ? 14 : selected.has(row.id) ? 11 : 8),
            symbol: points.map(row => row.id === activeId ? 'star' : selected.has(row.id) ? 'diamond' : 'circle'),
            color: colored ? points.map(row => row.values[colorKey] as number) : colorKey ? '#94a3b8' : '#3b82f6',
            opacity: 0.8, colorscale: palette, reversescale: reverse, showscale: colored,
            colorbar: { title: { text: escape(getMetricLabel(colorKey)), side: 'right' }, thickness: 12, len: 0.85 },
            line: { color: points.map(row => selected.has(row.id) || row.id === activeId ? theme.text : theme.background),
                width: points.map(row => selected.has(row.id) || row.id === activeId ? 2 : 0.5) } },
    });
    const scatterData: Data[] = colorKey
        ? [trace(pairs.filter(row => isFiniteMetric(row.values[colorKey])), true), trace(pairs.filter(row => !isFiniteMetric(row.values[colorKey])), false)]
        : [trace(pairs, false)];
    const validIds = new Set(rows.map(row => row.id));
    const inspect = (event: PlotMouseEvent) => {
        const id = event.points?.[0]?.customdata;
        if (typeof id === 'string' && validIds.has(id)) onInspect(id);
    };
    const selectPoints = (event: PlotSelectionEvent) => {
        const ids = [...new Set((event?.points ?? []).flatMap(point => typeof point.customdata === 'string' && validIds.has(point.customdata) ? [point.customdata] : []))];
        if (ids.length) onSelect(ids);
    };
    // The earlier nanobody Plotly Lab's X/Y/Z/color controls use Design metrics.
    // Adapt that scatter to the native-row authority without imputing missing color.
    const spaceTrace = (points: CohortRow[], colored: boolean): Data => ({
        type: 'scatter3d', mode: 'markers',
        x: points.map(row => row.values[xKey] as number), y: points.map(row => row.values[yKey] as number),
        z: points.map(row => row.values[zKey] as number), customdata: points.map(row => row.id),
        text: points.map(row => escape(row.label)),
        hovertemplate: `%{text}<br>${escape(caption(xKey))}: %{x}<br>${escape(caption(yKey))}: %{y}<br>${escape(caption(zKey))}: %{z}<extra></extra>`,
        marker: { size: points.map(row => row.id === activeId ? 8 : selected.has(row.id) ? 7 : 5),
            color: colored ? points.map(row => row.values[color3dKey] as number) : color3dKey ? '#94a3b8' : '#3b82f6',
            colorscale: palette, reversescale: reverse, showscale: colored, opacity: 0.85,
            colorbar: { title: { text: escape(getMetricLabel(color3dKey)) }, thickness: 12 } },
    });
    return <section aria-label={mode === 'dashboard' ? 'Cohort dashboard charts' : 'Cohort exploratory analytics'} className="space-y-3 text-[var(--text-primary)]">
        {!numeric.length ? <p role="status" className={card}>No finite numeric observations in this cohort. Missing, explicit null, boolean, string and nonfinite values are not plotted.</p> : <>
            {mode === 'analytics' && <PlotlyLab dimension={labDimension} onDimensionChange={setLabDimension} keys={numeric} getMetricLabel={getMetricLabel} renderMetricPicker={renderMetricPicker}
                palette={palette} onPalette={setPalette} reverse={reverse} onReverse={setReverse}
                axes2d={{ x: xKey, y: yKey, color: colorKey, onX: setXChoice, onY: setYChoice, onColor: setColorChoice }}
                axes3d={{ x: xKey, y: yKey, z: zKey, color: color3dKey, onX: setXChoice, onY: setYChoice, onZ: setZChoice, onColor: setColor3dChoice }}
                plot2d={<article className={card}>
                    <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">Compare measurements</h4><span className="text-xs text-[var(--text-secondary)]">{pairs.length} plotted · {rows.length - pairs.length} omitted</span></div>
                    <p className="text-xs">Each point is one record. Compare {caption(xKey)} on the horizontal axis with {caption(yKey)} on the vertical axis.</p>
                    {pairs.length ? <CohortPlot label={`Candidate scatter: ${caption(xKey)} versus ${caption(yKey)}`} data={scatterData}
                        layout={{ ...base, xaxis: axis(caption(xKey)), yaxis: axis(caption(yKey)), dragmode: dragMode,
                            uirevision: JSON.stringify([xKey, yKey, rows.map(row => row.id)]) }} onClick={inspect} onSelected={selectPoints} />
                        : <p className="flex h-[350px] items-center justify-center text-sm" role="status">No complete finite pairs for these axes. Choose other metrics.</p>}
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                        <label className="flex items-center gap-2">Drag <select className={control} aria-label="Scatter drag mode" value={dragMode} onChange={event => setDragMode(event.target.value as typeof dragMode)}>
                            <option value="zoom">Zoom</option><option value="select">Box select</option><option value="lasso">Lasso select</option>
                        </select></label>
                        <span>★ inspected · ◆ selected</span>
                    </div>
                    {colorKey && <p className="mt-1 text-xs text-[var(--text-secondary)]">{pairs.filter(row => !isFiniteMetric(row.values[colorKey])).length} with unavailable color shown gray.</p>}
                </article>} plot3d={<article className={card}>
                <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">3D comparison</h4><span className="text-xs text-[var(--text-secondary)]">{spacePoints.length} plotted · {rows.length - spacePoints.length} omitted</span></div>
                <p className="text-xs">Each point has all three selected measurements. Rotate the plot to compare them.</p>
                {spacePoints.length ? <CohortPlot label={`Native 3D scatter: ${caption(xKey)}, ${caption(yKey)}, ${caption(zKey)}`}
                    height={440} data={color3dKey ? [spaceTrace(spacePoints.filter(row => isFiniteMetric(row.values[color3dKey])), true), spaceTrace(spacePoints.filter(row => !isFiniteMetric(row.values[color3dKey])), false)] : [spaceTrace(spacePoints, false)]}
                    layout={{ ...base, margin: { l: 0, r: 0, t: 18, b: 0 }, scene: {
                        xaxis: { title: { text: escape(getMetricLabel(xKey)) }, color: theme.text, gridcolor: theme.grid },
                        yaxis: { title: { text: escape(getMetricLabel(yKey)) }, color: theme.text, gridcolor: theme.grid },
                        zaxis: { title: { text: escape(getMetricLabel(zKey)) }, color: theme.text, gridcolor: theme.grid },
                        bgcolor: theme.background }, uirevision: JSON.stringify([xKey, yKey, zKey, rows.map(row => row.id)]) }} onClick={inspect} />
                    : <p role="status" className="py-6 text-sm">No records have complete finite coordinates for these three metrics.</p>}
                <p className="pt-2 text-xs text-[var(--text-secondary)]">Rotate to explore; {inspectionHint}. Missing color observations remain gray, not zero.</p>
            </article>} />}
            <div className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-2">
                {mode === 'dashboard' && <>
                <article className={card}>
                    <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">Compare measurements</h4><span className="text-xs text-[var(--text-secondary)]">{pairs.length} plotted · {rows.length - pairs.length} omitted</span></div>
                    <p className="text-xs">Each point is one record. Compare {caption(xKey)} on the horizontal axis with {caption(yKey)} on the vertical axis.</p>
                    {pairs.length ? <CohortPlot label={`Candidate scatter: ${caption(xKey)} versus ${caption(yKey)}`} data={scatterData}
                        layout={{ ...base, xaxis: axis(caption(xKey)), yaxis: axis(caption(yKey)), dragmode: dragMode,
                            uirevision: JSON.stringify([xKey, yKey, rows.map(row => row.id)]) }} onClick={inspect} onSelected={selectPoints} />
                        : <p className="flex h-[350px] items-center justify-center text-sm" role="status">No complete finite pairs for these axes. Choose other metrics.</p>}
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                        <label className="flex items-center gap-2">Drag <select className={control} aria-label="Scatter drag mode" value={dragMode} onChange={event => setDragMode(event.target.value as typeof dragMode)}>
                            <option value="zoom">Zoom</option><option value="select">Box select</option><option value="lasso">Lasso select</option>
                        </select></label>
                        <span>★ inspected · ◆ selected</span>
                    </div>
                    {colorKey && <p className="mt-1 text-xs text-[var(--text-secondary)]">{pairs.filter(row => !isFiniteMetric(row.values[colorKey])).length} with unavailable color shown gray.</p>}
                </article>
                </>}
                <article className={card}>
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <h4 className="text-sm font-semibold">Metric distribution</h4>
                        <div className="flex flex-wrap items-center gap-2">
                            <span className="text-xs text-[var(--text-secondary)]">{distributionCount} observed · {rows.length - distributionCount} omitted</span>
                            <StatsPanelControls
                                kind="summary"
                                statsRunId={effectiveSummaryRunId}
                                stale={effectiveSummaryStale}
                                status={effectiveSummaryStatus}
                                reason={effectiveSummaryReason}
                                histogramBins={typeof summaryRun?.params?.histogram_bins === 'number' ? summaryRun.params.histogram_bins : undefined}
                                sharedBins={typeof summaryRun?.params?.shared_bins === 'boolean' ? summaryRun.params.shared_bins : undefined}
                                onRecompute={jobId ? (opts) => recomputeSummary.mutate(opts) : undefined}
                                isRecomputing={recomputeSummary.isPending}
                            />
                        </div>
                    </div>
                    {mode === 'analytics' && picker('Distribution metric', distKey, setDistributionChoice)}
                    <p className="text-xs">Each bin counts candidates within a range of recorded values from the stored Stats Toolkit run. {description(distKey)}</p>
                    {histogramTrace.length > 0 ? (
                        <CohortPlot label={`Histogram: ${caption(distKey)}`} data={histogramTrace}
                            layout={{ ...base, bargap: 0.04, xaxis: axis(caption(distKey)), yaxis: { ...axis('Candidate count'), rangemode: 'tozero' },
                                uirevision: JSON.stringify([distKey, effectiveSummaryRunId, rows.map(row => row.id)]) }} />
                    ) : (
                        <p role="status" className="py-6 text-sm text-[var(--text-secondary)]">
                            {summaryUnavailable
                                ? 'Distribution histogram unavailable (Stats Toolkit unreachable).'
                                : 'Stored distribution histogram is not available yet.'}
                        </p>
                    )}
                    <p className="pt-2 text-xs text-[var(--text-secondary)]">
                        Median {distributionSummary.median === null ? '—' : formatMetric(distributionSummary.median)} · Q1–Q3 {distributionSummary.q1 === null ? '—' : formatMetric(distributionSummary.q1)}–{distributionSummary.q3 === null ? '—' : formatMetric(distributionSummary.q3)}.
                    </p>
                </article>
                {mode === 'analytics' && barRows.length > 0 && <article className={card}>
                    <div className="mb-2 flex items-center justify-between gap-2"><h4 className="text-sm font-semibold">Candidates by recorded value</h4><span className="text-xs text-[var(--text-secondary)]">{barRows.length} of {rows.filter(row => isFiniteMetric(row.values[barKey])).length} observed</span></div>
                    {mode === 'analytics' && picker('Bar metric', barKey, setBarChoice)}
                    <p className="text-xs">Each bar is one candidate’s recorded value. {description(barKey)}</p>
                    <CohortPlot label={`Candidate bars: ${caption(barKey)}`} data={[{
                        type: 'bar', x: barRows.map(row => row.label), y: barRows.map(row => row.values[barKey] as number),
                        customdata: barRows.map(row => row.id), marker: { color: barRows.map(row => row.id === activeId ? '#14b8a6' : selected.has(row.id) ? '#a78bfa' : '#3b82f6') },
                        hovertemplate: `%{x}<br>${escape(caption(barKey))}: %{y}<extra></extra>`,
                    } as Data]} layout={{ ...base, margin: { l: 60, r: 24, t: 24, b: 95 },
                        xaxis: { type: 'category', tickangle: -45, automargin: true, color: theme.text }, yaxis: axis(caption(barKey)) }} onClick={inspect} />
                    <p className="pt-2 text-xs text-[var(--text-secondary)]">The highest recorded values in this view, not an acceptance ranking. Click a bar to inspect the record.</p>
                </article>}
                {composition && <article className={card}>
                    <h4 className="mb-2 text-sm font-semibold">Selected candidate: secondary structure</h4>
                    <p className="text-xs text-[var(--text-secondary)]">{active?.label} · native residue fractions</p>
                    <CohortPlot label="Selected candidate native secondary-structure composition" data={[{
                        type: 'pie', labels: ['Coil', 'Helix', 'Strand'], values: composition, hole: 0.45,
                        marker: { colors: ['#3b82f6', '#14b8a6', '#a78bfa'] },
                        textinfo: 'label+percent', hovertemplate: '%{label}: %{value:.4f} of residues<extra></extra>',
                    } as Data]} layout={{ ...base, margin: { l: 24, r: 24, t: 24, b: 24 }, showlegend: false }} height={300} />
                    <p className="pt-2 text-xs text-[var(--text-secondary)]">Shown only when this record reports a complete coil/helix/strand partition. Not a campaign or binding composition.</p>
                </article>}
            </div>
            <p className="text-xs text-[var(--text-secondary)]">Click a point to inspect it. Box or lasso selection adds records to your selection; the camera downloads an SVG. Use custom charts to change measurements and colors.</p>
        </>}
        {mode === 'analytics' && <>
            {numeric.length > 0 && <article className={card}>
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-sm font-semibold">How measurements vary together</h4>
                    <StatsPanelControls
                        kind="correlation"
                        statsRunId={effectiveCorrRunId}
                        stale={effectiveCorrStale}
                        status={effectiveCorrStatus}
                        reason={effectiveCorrReason}
                        method={corrMethod}
                        pAdjustMethod={correlationRun?.result?.p_adjust_method ?? (correlationRun?.params?.p_adjust_method as string | undefined)}
                        onRecompute={jobId ? (opts) => recomputeCorrelation.mutate(opts) : undefined}
                        isRecomputing={recomputeCorrelation.isPending}
                    />
                </div>
                <p className="my-2 text-xs text-[var(--text-secondary)]">Compare up to eight measurements from the stored Stats Toolkit correlation matrix. Blue/red cells show opposite/similar variation; blank cells have insufficient data or no variation. Correlation does not show causation.</p>
                <div aria-label="Correlation metrics" className="my-3 flex flex-wrap items-center gap-2">
                    {heatKeys.map(key => <button type="button" key={key} title={key} aria-label={`Remove correlation ${getMetricLabel(key)}`} className={control} onClick={() => setHeatChoice(heatKeys.filter(value => value !== key))}>{getMetricLabel(key)} ×</button>)}
                    {heatKeys.length < 8 && <div className="min-w-48">{picker('Add correlation measurement', heatAdd, value => { if (numeric.includes(value) && !heatKeys.includes(value)) setHeatChoice([...heatKeys, value]); setHeatAdd(''); }, true)}</div>}
                </div>
                {corrUnavailable ? (
                    <p role="status" className="py-6 text-sm text-[var(--text-secondary)]">Correlations unavailable (Stats Toolkit unreachable).</p>
                ) : heatKeys.length && hasAnyStoredCorrelation ? <CohortPlot label={`${corrMethodLabel} correlation heatmap`} height={Math.max(380, heatKeys.length * 52)} data={[
                    { type: 'heatmap', x: heatKeys.map(caption), y: heatKeys.map(caption), z: correlations.map(row => row.map(cell => cell.r)),
                        customdata: correlations.map(row => row.map(cell => `n=${cell.pairs}; ${cell.reason ?? `${corrMethodLabel}=${formatMetric(cell.r)}`}`)),
                        hovertemplate: '%{x}<br>%{y}<br>%{customdata}<extra></extra>', hoverongaps: false,
                        zmin: -1, zmax: 1, zmid: 0, colorscale: 'RdBu', reversescale: true,
                        colorbar: { title: { text: corrMethodLabel }, thickness: 14 } } as Data,
                    { type: 'scatter', mode: 'text', x: correlations.flatMap(row => row.flatMap((cell, x) => cell.r === null ? [caption(heatKeys[x])] : [])),
                        y: correlations.flatMap((row, y) => row.flatMap(cell => cell.r === null ? [caption(heatKeys[y])] : [])),
                        text: correlations.flatMap(row => row.flatMap(cell => cell.r === null ? ['—'] : [])),
                        customdata: correlations.flatMap(row => row.flatMap(cell => cell.r === null ? [`${cell.reason}; n=${cell.pairs}`] : [])),
                        hovertemplate: '%{x}<br>%{y}<br>%{customdata}<extra></extra>', textfont: { color: theme.text } },
                ]} layout={{ ...base, margin: { l: 110, r: 60, t: 25, b: 100 },
                    xaxis: { ...axis(''), type: 'category', tickangle: -30 }, yaxis: { ...axis(''), type: 'category', autorange: 'reversed' } }} />
                    : <p role="status" className="py-6 text-sm text-[var(--text-secondary)]">{heatKeys.length ? 'Stored correlation matrix is not available yet.' : 'Choose metrics to plot their pairwise correlations.'}</p>}
                <details className="mt-2 text-xs"><summary className="cursor-pointer">Pair counts and undefined reasons (accessible table)</summary>
                    <div className="max-h-64 overflow-auto"><table className="w-full text-left"><thead><tr><th>X</th><th>Y</th><th>Complete pairs</th><th>{corrMethodLabel} / reason</th></tr></thead>
                        <tbody>{correlations.flatMap((row, y) => row.map((cell, x) => <tr key={`${x}:${y}`}><td className="p-2">{caption(heatKeys[x])}</td><td>{caption(heatKeys[y])}</td><td>{cell.pairs}</td><td>{cell.reason ?? formatMetric(cell.r)}</td></tr>))}</tbody></table></div>
                </details>
            </article>}
            <article className={card}>
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <h4 className="text-sm font-semibold">Descriptive metric summary</h4>
                    <StatsPanelControls
                        kind="summary"
                        statsRunId={effectiveSummaryRunId}
                        stale={effectiveSummaryStale}
                        status={effectiveSummaryStatus}
                        reason={effectiveSummaryReason}
                        histogramBins={typeof summaryRun?.params?.histogram_bins === 'number' ? summaryRun.params.histogram_bins : undefined}
                        sharedBins={typeof summaryRun?.params?.shared_bins === 'boolean' ? summaryRun.params.shared_bins : undefined}
                        onRecompute={jobId ? (opts) => recomputeSummary.mutate(opts) : undefined}
                        isRecomputing={recomputeSummary.isPending}
                    />
                </div>
                <p className="my-2 text-xs text-[var(--text-secondary)]">Stored Stats Toolkit descriptive statistics. “—” means unavailable.</p><details className="text-xs"><summary>Calculation details</summary><p>Computed in BioModStack Stats Toolkit using type-7 quartiles and sample standard deviation (n − 1). Missing, null and nonnumeric values are counted separately and never replaced with zero.</p></details>
                {summaryUnavailable && <p role="status" className="my-2 text-xs text-rose-300">Statistics unavailable (Stats Toolkit unreachable).</p>}
                <label className="flex max-w-md flex-col gap-1 text-xs">Find summary metrics<input className={control} type="search" aria-label="Find summary metrics" value={metricSearch} onChange={event => { setMetricSearch(event.target.value); setMetricPage(0); }} /></label>
                <div className="mt-3 max-h-[480px] overflow-auto"><table className="w-full text-right text-xs tabular-nums">
                    <caption className="sr-only">Descriptive statistics for the full filtered cohort</caption>
                    <thead className="sticky top-0 bg-[var(--bg-primary)]"><tr><th className="p-2 text-left">Metric / native key</th>{['n', 'Missing', 'Null', 'Nonnumeric', 'Min', 'Q1', 'Median', 'Q3', 'Max', 'Mean', 'Sample SD'].map(name => <th key={name} className="whitespace-nowrap p-2">{name}</th>)}</tr></thead>
                    <tbody>{summaryKeys.map(key => { const summary = summaryMap.get(key)!; return <tr key={key} className="border-t border-[var(--border-color)]">
                        <th scope="row" className="p-2 text-left font-normal" title={key}>{getMetricLabel(key)}</th>
                        {(['observed', 'missing', 'nulls', 'nonNumeric', 'min', 'q1', 'median', 'q3', 'max', 'mean', 'stdDev'] as const).map(field => <td key={field} className="whitespace-nowrap p-2" title={summary[field] === null ? 'Undefined or unavailable' : String(summary[field])}>{summary[field] === null ? '—' : formatMetric(summary[field])}</td>)}
                    </tr>; })}</tbody>
                </table></div>
                {!searchedKeys.length && <p className="py-3 text-sm">No matching metric keys.</p>}
                <nav aria-label="Metric summary pages" className="mt-2 flex items-center gap-3 text-xs"><button type="button" className={control} disabled={page === 0} onClick={() => setMetricPage(page - 1)}>Previous metrics</button>
                    <span>{searchedKeys.length ? page * 20 + 1 : 0}–{Math.min((page + 1) * 20, searchedKeys.length)} of {searchedKeys.length} metrics</span>
                    <button type="button" className={control} disabled={(page + 1) * 20 >= searchedKeys.length} onClick={() => setMetricPage(page + 1)}>Next metrics</button></nav>
            </article>
        </>}
    </section>;
}
