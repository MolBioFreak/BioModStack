import { useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import type { Data } from 'plotly.js';
import { CohortPlot, usePlotTheme } from './CohortAnalytics';
import { fetchBC2Trace, object } from '../lib/bindcraft2Results';
import type { NativeGenerationRecord } from '../lib/nativeBinderResults';

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
    const phases = [...new Set(rows.map(item => String(item.phase)))];
    const traces: Data[] = phases.map(phase => ({ type: 'scatter', mode: 'lines+markers', name: phase,
        x: rows.filter(item => String(item.phase) === phase).map(item => item.round as number),
        y: rows.filter(item => String(item.phase) === phase).map(item => typeof item[key] === 'number' && Number.isFinite(item[key]) ? item[key] as number : null),
        connectgaps: false }));
    return <section aria-label="Native trajectory detail" className="my-4 min-w-0 space-y-3">
        <p className="break-all text-xs">{design}</p>
        <p>Native termination: {String(native.terminated ?? 'Not reported')} · Native outcome: {String(native.outcome ?? 'Not reported')}</p>
        {typeof native.sequence === 'string' && native.sequence && <details><summary>Published sequence</summary><p className="break-all font-mono">{native.sequence}</p></details>}
        {trajectory && <>
            <h4 className="font-semibold">Phase loss and confidence trace</h4>
            <p className="text-sm text-[var(--text-secondary)]">Recorded optimization updates, not the native verdict. The last recorded update is not the final prediction: native selection uses the best weighted loss and a fresh prediction.</p>
            {query.isLoading && <p role="status">Loading phase updates…</p>}
            {(analytics.trace_available === false || (query.data && !rows.length)) && <p role="status">No recorded loss trace is available for this trajectory.</p>}
            {query.isError && <p role="status">Trace {rows.length ? 'partially loaded' : 'unavailable'}: {String(query.error)} <button type="button" onClick={() => void (query.isFetchNextPageError ? query.fetchNextPage() : query.refetch())}>Retry trace</button></p>}
            {query.data?.pages.flatMap(page => page.warnings ?? []).map((warning, i) => <p role="status" key={i}>{warning}</p>)}
            {!!rows.length && <>
                <label className="flex flex-wrap gap-2">Trace measurement <select className="min-w-0 max-w-full rounded border border-[var(--border-color)] bg-[var(--bg-primary)] p-2" aria-label="Trace measurement" value={key} onChange={event => setChoice(event.target.value)}>{keys.map(key => <option key={key}>{key}</option>)}</select></label>
                <CohortPlot label={`Phase trace: ${key}`} data={traces} layout={{ paper_bgcolor: theme.background, plot_bgcolor: theme.background, font: { color: theme.text }, showlegend: true,
                    xaxis: { title: { text: 'Native round within phase' }, gridcolor: theme.grid }, yaxis: { title: { text: key }, gridcolor: theme.grid }, margin: { l: 70, r: 20, t: 20, b: 65 } }} />
                <p className="text-xs">{rows.length} of {query.data?.pages[0].total} recorded updates loaded. Native scales; phases plotted separately, missing values remain gaps.</p>
            </>}
        </>}
        {!row.structures?.length && <p className="text-sm">No native coordinates were published for this record.</p>}
    </section>;
}

export function BindCraft2OutcomeSummary({ rows }: { rows: NativeGenerationRecord[] }) {
    const counts = rows.reduce<Record<string, number>>((result, row) => { const native = object(row.native_record); const key = String(native.terminated ?? native.outcome ?? 'Not reported'); result[key] = (result[key] ?? 0) + 1; return result; }, {});
    return <p aria-label="Native outcomes in view" className="text-sm">Native outcomes in this view: {Object.entries(counts).map(([key, count]) => `${key}: ${count}`).join(' · ') || 'No matching records'}. Computational outcomes, not experimental binding.</p>;
}
