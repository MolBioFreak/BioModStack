import { lazy, Suspense, useMemo, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { fetchJobById } from '../lib/api';
import { useThemeColors } from './useThemeColors';
import type { NativeCandidateSource } from '../lib/binderContinuation';
import { hasNativeComparison, nativeComparisonKey, nativeComparisonLabel, nativeComparisonMetric, nativeComparisonOutcome, nativeComparisonSelection, nativeComparisonValues, readNativeComparison, type NativeComparisonSet } from '../lib/nativeBinderComparison';
import { formatMetric, summarizeMetric, type CohortRow } from '../lib/cohortAnalytics';
import type { NativeGenerationRecord } from '../lib/nativeBinderResults';
const CohortPlot = lazy(() => import('./CohortAnalytics').then(module => ({ default: module.CohortPlot })));

interface Props {
    jobIds: string[];
    selectedDesignIds?: string[];
    selectedNativeSources?: NativeCandidateSource[];
    launchContextId?: string | null;
}
const control = 'rounded border border-[var(--border-primary)] bg-[var(--bg-primary)] px-3 py-2 text-sm';
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** Compact comparison of original producer populations, not embedded Results pages. */
export function NativeBinderComparison({ jobIds, selectedDesignIds, selectedNativeSources, launchContextId }: Props) {
    const colors = useThemeColors();
    const ids = [...new Set([...jobIds, ...(selectedNativeSources ?? []).map(source => source.job_id)])];
    const jobs = useQueries({ queries: ids.map(id => ({ queryKey: ['native-comparison-job', id], queryFn: () => fetchJobById(id), staleTime: 60000 })) });
    const nativeJobs = jobs.flatMap(query => query.data && hasNativeComparison(query.data.data) ? [query.data.data] : []);
    const queries = useQueries({ queries: nativeJobs.map(job => ({ queryKey: ['native-comparison-population', job.id], queryFn: ({ signal }: { signal: AbortSignal }) => readNativeComparison(job, signal), staleTime: 60000, refetchOnWindowFocus: false })) });
    const [population, setPopulation] = useState('all');
    const [chosenMetric, setChosenMetric] = useState('');
    const [activeId, setActiveId] = useState('');
    const sets = queries.flatMap(query => query.data ? [query.data] : []);
    const sourceRows = useMemo(() => sets.map(set => ({ set, records: nativeComparisonSelection(set, selectedDesignIds, selectedNativeSources)
        .filter(row => population === 'all' || nativeComparisonOutcome(row) === population) })), [sets, selectedDesignIds, selectedNativeSources, population]);
    const metrics = new Map<string, { metric: string; label: string; unit: string; model: string; jobs: Set<string> }>();
    for (const { set, records } of sourceRows) for (const row of records) for (const [key, value] of Object.entries(nativeComparisonValues(row))) {
        if (!finite(value) || ['rank', 'duration_seconds'].includes(key)) continue;
        const definition = nativeComparisonMetric(set, row, key);
        if (!metrics.has(definition.key)) metrics.set(definition.key, { ...definition, model: set.job.model_id, jobs: new Set() });
        metrics.get(definition.key)!.jobs.add(set.job.id);
    }
    const choices = [...metrics.entries()].sort(([, a], [, b]) => (a.metric === 'i_pTM' ? -1 : b.metric === 'i_pTM' ? 1 : a.label.localeCompare(b.label)));
    const metricId = metrics.has(chosenMetric) ? chosenMetric : choices[0]?.[0] ?? '';
    const definition = metrics.get(metricId);
    const groups = sourceRows.flatMap(({ set, records }) => {
        const matching = definition ? records.filter(row => nativeComparisonMetric(set, row, definition.metric).key === metricId) : [];
        if (!matching.length) return [];
        const cohort: CohortRow[] = matching.map(row => ({ id: nativeComparisonKey(set.job.id, row, set.records.indexOf(row)), label: nativeComparisonLabel(row, set.records.indexOf(row)), values: nativeComparisonValues(row) }));
        return [{ set, records: matching, cohort, stats: summarizeMetric(cohort, definition!.metric) }];
    });
    const selected = selectedDesignIds !== undefined || selectedNativeSources !== undefined;
    const allLoaded = sets.reduce((count, set) => count + set.records.length, 0);
    const total = sets.reduce((count, set) => count + set.total, 0);
    const active = groups.flatMap(group => group.records.map((record, index) => ({ ...group, record, id: group.cohort[index].id, label: group.cohort[index].label }))).find(row => row.id === activeId);
    const route = (set: NativeComparisonSet, row: NativeGenerationRecord) => {
        const bindings = Array.isArray(row.comparison_bindings) ? row.comparison_bindings as Array<{ structures?: NativeGenerationRecord['structures'] }> : [];
        const documents = [...(row.structures ?? []), ...bindings.flatMap(binding => binding.structures ?? [])];
        const explicit = selectedNativeSources?.find(source => source.job_id === set.job.id && documents.some(document => document.artifact_id === source.artifact_id));
        const document = (explicit && documents.find(document => document.artifact_id === explicit.artifact_id)) ?? documents.find(item => item.primary) ?? documents[0];
        const query = new URLSearchParams();
        if (row.design_id) query.set('design_id', row.design_id);
        if (document) {
            query.set('artifact_id', document.artifact_id);
            if (document.target_state != null) query.set('target_state', document.target_state);
        }
        if (set.job.model_id === 'bindcraft2' && row.candidate_key) {
            try { const key = JSON.parse(row.candidate_key); if (Array.isArray(key) && key.length === 3) query.set('native_scope', JSON.stringify(['bindcraft2', key[0], key[1]])); } catch { /* identity remains the exact document */ }
        }
        if (launchContextId) query.set('launch_context_id', launchContextId);
        return `/designs/${encodeURIComponent(set.job.id)}?${query}`;
    };
    const download = () => {
        const data = { scope: selected ? 'selected_candidates' : 'native_populations', population, metric: definition ? { name: definition.metric, unit: definition.unit } : null,
            results: sourceRows.map(({ set, records }) => ({ job_id: set.job.id, model_id: set.job.model_id, total: set.total, loaded: set.records.length, complete: set.complete, error: set.error ?? null, records })) };
        const href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
        const link = document.createElement('a'); link.href = href; link.download = 'native-candidate-comparison.json'; link.click(); setTimeout(() => URL.revokeObjectURL(href), 0);
    };
    if (jobs.some(query => query.isPending)) return <p role="status">Loading comparison sources…</p>;
    if (!nativeJobs.length) return jobs.some(query => query.isError) ? <p role="status">Some comparison sources could not be read.</p> : null;
    return <section aria-label="Native candidate comparison" className="space-y-3 text-[var(--text-primary)]">
        <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-semibold">{selected ? 'Selected candidates' : 'Candidate populations'}</h3>
            <div className="flex gap-2"><button type="button" className={control} onClick={() => queries.forEach(query => void query.refetch())}>Refresh</button><button type="button" className={control} onClick={download}>Export native data</button></div></div>
        {queries.some(query => query.isPending) && <p role="status">Loading native records…</p>}
        {sets.some(set => !set.complete) && <p role="status" className="text-sm text-[var(--warning)]">Partial comparison: {allLoaded} of {total} declared records loaded. Available rows remain usable.</p>}
        {sets.filter(set => set.error).map(set => <p key={set.job.id} className="text-sm" role="status">{set.job.name}: {set.error}</p>)}
        <div className="flex flex-wrap items-end gap-3">
            <label className="text-sm">Population<select aria-label="Comparison population" className={`${control} ml-2`} value={population} onChange={event => setPopulation(event.target.value)}><option value="all">All outcomes</option><option value="passed">Passed</option><option value="rejected">Rejected</option></select></label>
            {!!choices.length && <label className="min-w-0 text-sm">Measurement<select aria-label="Native comparison measurement" className={`${control} ml-2 max-w-full`} value={metricId} onChange={event => setChosenMetric(event.target.value)}>{choices.map(([key, item]) => <option key={key} value={key}>{item.label} · {item.model}{choices.filter(([, other]) => other.label === item.label).length > 1 ? ` · ${sets.filter(set => item.jobs.has(set.job.id)).map(set => set.job.name).join(' / ')}` : ''}</option>)}</select></label>}
        </div>
        <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th className="p-2">Source</th><th>Compared</th><th>Median</th><th>Range</th><th>Not reported</th></tr></thead><tbody>{groups.map(group => <tr key={group.set.job.id} className="border-t border-[var(--border-primary)]"><td className="p-2">{group.set.job.name}<span className="block text-xs text-[var(--text-secondary)]">{group.set.job.model_id} · {group.set.records.length} native records</span></td><td>{group.records.length}</td><td>{formatMetric(group.stats.median)}</td><td>{formatMetric(group.stats.min)} – {formatMetric(group.stats.max)}</td><td title={`Missing ${group.stats.missing}; null ${group.stats.nulls}; nonnumeric ${group.stats.nonNumeric}`}>{group.stats.missing + group.stats.nulls + group.stats.nonNumeric}</td></tr>)}</tbody></table></div>
        {!!groups.length && definition ? <Suspense fallback={<p>Loading comparison chart…</p>}><CohortPlot label="Native candidate measurement comparison" height={320}
            data={groups.map(group => ({ type: 'scatter', mode: 'markers', name: group.set.job.name,
                x: group.cohort.map(row => row.label), y: group.cohort.map(row => finite(row.values[definition.metric]) ? row.values[definition.metric] as number : null),
                customdata: group.cohort.map(row => row.id), marker: { size: 8 }, connectgaps: false,
                hovertemplate: `%{x}<br>${definition.label}: %{y}<extra>%{fullData.name}</extra>` }))}
            layout={{ paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', font: { color: colors.textPrimary }, margin: { l: 60, r: 20, t: 45, b: 60 }, xaxis: { title: { text: 'Native candidate' } }, yaxis: { title: { text: definition.label } }, legend: { orientation: 'h' } }}
            onClick={event => { const id = event.points[0]?.customdata; if (typeof id === 'string') setActiveId(id); }} /></Suspense> : !queries.some(query => query.isPending) && <p className="text-sm text-[var(--text-secondary)]">No native measurements are reported for this selection.</p>}
        {active && <div className="flex flex-wrap gap-3 text-sm"><span>{active.label} · {active.set.job.name}</span><a className="text-[var(--link)] underline" href={route(active.set, active.record)}>Open candidate</a><span>{nativeComparisonOutcome(active.record) || 'Outcome not reported'}</span></div>}
        <details className="text-xs text-[var(--text-secondary)]"><summary>Scope and sources</summary><p>Original producer records only. Different native definitions stay separate; missing values are not zero. Candidate labels are display aliases, not cross-run correspondences.</p><ul>{sets.map(set => <li key={set.job.id}>{set.job.name} · {set.job.id} · {set.records.length} / {set.total} records</li>)}</ul></details>
    </section>;
}
