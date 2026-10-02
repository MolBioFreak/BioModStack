import { useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import type { Data } from 'plotly.js';
import { CohortPlot, usePlotTheme } from './CohortAnalytics';
import { CohortMetricPicker } from './CohortMetricPicker';
import { describeCohortMetric } from '../lib/cohortMetricPresentation';
import { fetchBC2Trace, object } from '../lib/bindcraft2Results';
import type { NativeGenerationRecord } from '../lib/nativeBinderResults';

const phaseLabel = (phase: string) => ({ screen: 'Screening', refine: 'Refinement', anneal: 'Annealing', harden: 'Hardening', mutate: 'Mutation', final: 'Final check' }[phase] ?? phase);

export function BindCraft2Trajectory({ jobId, row, arm }: { jobId: string; row: NativeGenerationRecord; arm: string | null }) {
    const native = object(row.native_record), analytics = object(native.analytics);
    const design = String(native.design ?? '');
    const trajectory = native.stage === 'trajectory';
    const [choice, setChoice] = useState('');
    const theme = usePlotTheme();
    const query = useInfiniteQuery({ queryKey: ['bc2-trajectory', jobId, arm, design], initialPageParam: 0,
        enabled: trajectory && !!design && analytics.trace_available !== false,
        queryFn: ({ pageParam, signal }) => fetchBC2Trace(jobId, design, arm, pageParam, signal),
        getNextPageParam: page => page.rows.length && page.offset + page.rows.length < page.total ? page.offset + page.rows.length : undefined,
        retry: false, refetchOnWindowFocus: false });
    useEffect(() => {
        if (query.hasNextPage && !query.isFetching && !query.isFetchNextPageError) void query.fetchNextPage();
    }, [query.hasNextPage, query.isFetching, query.isFetchNextPageError, query.fetchNextPage, query.data?.pages.length]);
    const rows = useMemo(() => query.data?.pages.flatMap(page => page.rows) ?? [], [query.data]);
    const keys = [...new Set(rows.flatMap(item => Object.keys(item)))].filter(key => !['round', 'phase'].includes(key) && rows.some(item => typeof item[key] === 'number' && Number.isFinite(item[key])));
    const key = keys.includes(choice) ? choice : keys.find(key => /iptm$/i.test(key)) ?? keys[0] ?? '';
    const metric = describeCohortMetric(key, keys);
    const phases = [...new Set(rows.map(item => String(item.phase)))];
    const traces: Data[] = phases.map(phase => {
        const points = rows.map((item, index) => ({ item, update: index + 1 })).filter(({ item }) => String(item.phase) === phase);
        return { type: 'scatter', mode: 'lines+markers', name: phaseLabel(phase),
            x: points.map(point => point.update),
            y: points.map(({ item }) => typeof item[key] === 'number' && Number.isFinite(item[key]) ? item[key] as number : null),
            customdata: points.map(({ item }) => item.round),
            hovertemplate: 'Update %{x}<br>Value: %{y}<br>Step within stage: %{customdata}<extra>%{fullData.name}</extra>',
            connectgaps: false } as Data;
    });
    const warnings = [...new Set(query.data?.pages.flatMap(page => page.warnings ?? []) ?? [])];
    return <section aria-label="Native trajectory detail" className="my-4 min-w-0 space-y-3">
        {native.terminated != null && <p className="text-sm">Stopped at {phaseLabel(String(native.terminated))}.</p>}
        {native.outcome != null && <p className="text-sm">Recorded outcome: {String(native.outcome)}</p>}
        {typeof native.sequence === 'string' && native.sequence && <details><summary>Sequence</summary><p className="break-all font-mono">{native.sequence}</p></details>}
        {trajectory && <>
            <h4 className="font-semibold">Optimization history</h4>
            <p className="text-sm text-[var(--text-secondary)]">Follow this attempt from left to right. Each point is a recorded update; colors identify the optimization stage. These are not the final acceptance scores.</p>
            {query.isLoading && <p role="status">Loading optimization history…</p>}
            {(analytics.trace_available === false || (query.data && !rows.length)) && <p role="status">No recorded loss trace is available for this trajectory.</p>}
            {query.isError && <p role="status">Trace {rows.length ? 'partially loaded' : 'unavailable'}: {String(query.error)} <button type="button" onClick={() => void (query.isFetchNextPageError ? query.fetchNextPage() : query.refetch())}>Retry trace</button></p>}
            {!!rows.length && <>
                <CohortMetricPicker label="Trace measurement" keys={keys} value={key} onChange={setChoice} />
                <CohortPlot label={`Phase trace: ${metric.label}`} data={traces} layout={{ paper_bgcolor: theme.background, plot_bgcolor: theme.background, font: { color: theme.text }, showlegend: true,
                    legend: { orientation: 'h', x: 0, y: 1.12 },
                    xaxis: { title: { text: 'Optimization update' }, gridcolor: theme.grid }, yaxis: { title: { text: metric.shortLabel }, gridcolor: theme.grid, automargin: true }, margin: { l: 70, r: 20, t: 45, b: 65 } }} />
                <p className="text-xs text-[var(--text-secondary)]">{rows.length} of {query.data?.pages[0].total} recorded updates loaded. Missing measurements appear as gaps.</p>
            </>}
            {!!warnings.length && <details><summary className="cursor-pointer text-sm">Trace details</summary>{warnings.map((warning, i) => <p key={i} className="my-2 text-sm">{warning}</p>)}</details>}
        </>}
        {!row.structures?.length && <p className="text-sm text-[var(--text-secondary)]">No structure was saved for this record.</p>}
    </section>;
}

export function BindCraft2OutcomeSummary({ rows }: { rows: NativeGenerationRecord[] }) {
    const trajectories = rows.filter(row => object(row.native_record).stage === 'trajectory');
    if (!trajectories.length) return null;
    const counts = trajectories.reduce<Record<string, number>>((result, row) => { const stage = String(object(row.native_record).terminated ?? 'Not reported'); result[stage] = (result[stage] ?? 0) + 1; return result; }, {});
    return <div aria-label="Native outcomes in view" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm"><span className="text-[var(--text-secondary)]">Attempts stopped at</span>{Object.entries(counts).map(([phase, count]) => <span key={phase}>{phaseLabel(phase)} <strong className="font-medium tabular-nums">{count}</strong></span>)}</div>;
}
