import { useEffect, useState } from 'react';
import type { NativeResults, NativeScalarQuery } from '../lib/scientificAnalytics';
import { useQueries } from '@tanstack/react-query';
import { fetchJobDesignMetrics, type Design } from '../lib/api';
import type { ScientificPoint } from '../lib/scientificAnalytics';
const asRecord = (value: unknown): Record<string, unknown> | null => value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const nativeSummaryKeys: Record<string, string> = { plddt_mean: 'Mean pLDDT (fraction)', plddt: 'Mean pLDDT (0–100)', complex_plddt: 'Complex pLDDT (fraction)', complex_iplddt: 'Interface pLDDT (fraction)', ptm: 'pTM', iptm: 'iPTM', structure_confidence: 'Provider structure confidence', ranking_score: 'Producer ranking score', confidence_score: 'Producer confidence score', gpde: 'gPDE (Å)', complex_pde: 'Complex PDE (Å)', complex_ipde: 'Interface PDE (Å)', ligand_iptm: 'Ligand iPTM', protein_iptm: 'Protein iPTM'  };
type ScalarDisplay = { key: string; label: string; display: string; title: string; raw?: number; unit?: string };
export type ScalarEvidence = { entries: ScalarDisplay[]; reason?: string };
export const canonicalScalars = (design: Design) => design.core_protein_scientific_contract === 1 || design.confidence_metrics?.core_protein_scientific_contract === 1 || !!design.scientific_structure_document || !!design.confidence_metrics?.core_protein_scientific || asRecord(design.provenance?.external_import)?.provider === 'boltz_api';
const finiteScalar = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
/** Consume the existing source-verified analytics transport, not nullable DB projections. */
export function scalarEvidence(design: Design, point?: ScientificPoint, reason?: string): ScalarEvidence {
    if (!canonicalScalars(design) && !point) {
        const values = { ...asRecord(design.confidence_metrics) };
        values.plddt_overall ??= design.plddt_overall;
        values.ptm ??= design.ptm;
        values.iptm ??= design.iptm;
        return { entries: Object.entries(values).flatMap(([key, value]) => {
            if (!finiteScalar(value) || !(key in nativeSummaryKeys || key === 'plddt_overall')) return [];
            const fractionPlddt = ['plddt_mean', 'complex_plddt', 'complex_iplddt'].includes(key);
            const plddt = key.includes('plddt');
            return [{ key, label: (nativeSummaryKeys[key] ?? 'Overall pLDDT (0–100)').replace('(fraction)', '(0–100; native fraction)'),
                display: (fractionPlddt ? value * 100 : value).toFixed(plddt ? 2 : 4), title: String(value) }];
        }) };
    }
    if (!point) return { entries: [], reason: reason ?? 'Native scalar evidence not retained.' };
    if (point.id !== design.id || point.source_job_id !== design.job_id || (design.scientific_structure_document && design.scientific_structure_document.candidateId !== design.id)) return { entries: [], reason: 'Native scalar identity mismatch.' };
    if (point.publication_state) return { entries: [], reason: point.publication_state.reason_code ?? 'Native publication unavailable.' };
    return { entries: Object.entries(point.metric_states).flatMap(([key, state]) => {
        if (!(key in nativeSummaryKeys)) return [];
        const descriptor = point.metric_descriptors[key];
        const plddt = key.includes('plddt');
        const label = plddt ? `${key === 'complex_iplddt' ? 'Interface' : key === 'complex_plddt' ? 'Complex' : 'Mean'} pLDDT (0–100)` : nativeSummaryKeys[key];
        const title = state.state === 'ok' ? `${state.value} ${descriptor.unit}; ${descriptor.scope}` : state.reason_code;
        if (state.state !== 'ok') return [{ key, label, display: '—', title }];
        if (plddt && !['fraction', 'percent'].includes(descriptor.unit)) return [{ key, label, display: '—', title: `Unsupported native pLDDT unit: ${descriptor.unit}` }];
        return [{ key, label, raw: state.value, unit: descriptor.unit, display: (plddt && descriptor.unit === 'fraction' ? state.value * 100 : state.value).toFixed(plddt ? 2 : 4), title }];
    }) };
}
export const scalarCell = (evidence: ScalarEvidence, keys: string[]) => {
    const entry = keys.map(key => evidence.entries.find(e => e.key === key)).find(Boolean);
    return <td title={entry ? `${entry.label}: ${entry.title}` : evidence.reason ?? 'Not reported'}>{entry?.display ?? '—'}</td>;
};

/** Bounds are typed locally and applied once, in the descriptor's raw unit. */
export function NativeScalarControls({query, result, onChange, jobLabel}: {
    query: NativeScalarQuery; result?: NativeResults | null; onChange: (query: NativeScalarQuery) => void;
    jobLabel?: (jobId: string) => string | undefined;
}) {
    const cohorts = Object.entries(result?.cohorts ?? {});
    const cohortKey = query.cohort_key ?? (cohorts.length === 1 ? cohorts[0][0] : '');
    const descriptors = result?.cohorts[cohortKey]?.metric_descriptors ?? {};
    const metricKey = query.metric_id ?? ['complex_plddt', 'plddt', 'plddt_mean', 'ptm'].find(key => key in descriptors) ?? '';
    const descriptor = descriptors[metricKey];
    const activeQuery = {...query, cohort_key: cohortKey || undefined, metric_id: metricKey || undefined, unit: descriptor?.unit};
    const scale = metricKey.includes('plddt') && descriptor?.unit === 'fraction' ? 100 : 1;
    const [minimum, setMinimum] = useState('');
    const [maximum, setMaximum] = useState('');
    useEffect(() => {
        setMinimum(query.minimum == null ? '' : String(query.minimum * scale));
        setMaximum(query.maximum == null ? '' : String(query.maximum * scale));
    }, [query.minimum, query.maximum, query.cohort_key, query.metric_id, scale]);
    const apply = () => {
        const min = minimum.trim() === '' ? null : Number(minimum) / scale;
        const max = maximum.trim() === '' ? null : Number(maximum) / scale;
        if ((min !== null && !Number.isFinite(min)) || (max !== null && !Number.isFinite(max))) return;
        onChange({...activeQuery, minimum: min, maximum: max});
    };
    const style = 'min-w-0 max-w-full rounded border border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-primary)] px-2 py-1 text-sm';
    const summary = result?.summaries[cohortKey]?.[metricKey];
    const displayValue = (value: number) => (value * scale).toFixed(metricKey.includes('plddt') ? 2 : 4);
    const displayUnit = metricKey.includes('plddt') ? '0–100' : descriptor?.unit;
    return <div className="space-y-2 text-xs">
        <div className="flex flex-wrap gap-2 items-center">
            <label className="min-w-0 max-w-full">Result group <select className={style} aria-label="Native cohort" value={cohortKey} onChange={e => onChange(e.target.value ? {cohort_key: e.target.value} : {})}>
                {cohorts.length !== 1 && <option value="">All result groups</option>}
                {cohorts.map(([key, cohort], index) => <option key={key} value={key}>{jobLabel?.(key.split(':').slice(2).join(':')) ?? `Result group ${index + 1}`} · {cohort.count} results</option>)}
            </select></label>
            <label className="min-w-0 max-w-full">Metric <select className={style} aria-label="Native metric" value={metricKey} onChange={e => onChange({cohort_key: cohortKey || undefined, metric_id: e.target.value || undefined, unit: descriptors[e.target.value]?.unit})}>
                {!metricKey && <option value="">Choose a native metric</option>}
                {Object.keys(descriptors).map(key => <option key={key} value={key}>{(nativeSummaryKeys[key] ?? key).replace('(fraction)', '(0–100)')}</option>)}
            </select></label>
            {descriptor && <>
                <label>Minimum <input className={style} aria-label="Native minimum" inputMode="decimal" value={minimum} onChange={e => setMinimum(e.target.value)} onKeyDown={e => {if (e.key === 'Enter') apply();}} /></label>
                <label>Maximum <input className={style} aria-label="Native maximum" inputMode="decimal" value={maximum} onChange={e => setMaximum(e.target.value)} onKeyDown={e => {if (e.key === 'Enter') apply();}} /></label>
                <button className={style} onClick={apply}>Apply bounds</button>
                <label><input type="checkbox" checked={query.include_missing ?? false} onChange={e => onChange({...activeQuery, include_missing: e.target.checked})} /> Include missing values</label>
                <select className={style} aria-label="Native ordering" value={query.order ?? ''} onChange={e => onChange({...activeQuery, order: (e.target.value || null) as NativeScalarQuery['order']})}>
                    <option value="">Name order</option><option value="desc">Highest first</option><option value="asc">Lowest first</option>
                </select>
            </>}
        </div>
        {descriptor && <p>{(nativeSummaryKeys[metricKey] ?? metricKey).replace('(fraction)', '(0–100)')}. Leave a bound blank for no limit; missing values sort last.</p>}
        {summary && <p>{summary.observed_count} measured · {summary.unavailable_count + summary.invalid_count} missing. {summary.statistics && <span title={`Raw mean ${summary.statistics.avg} ${summary.descriptor.unit}; min ${summary.statistics.min}; max ${summary.statistics.max}`}>Mean {displayValue(summary.statistics.avg)}; range {displayValue(summary.statistics.min)}–{displayValue(summary.statistics.max)} ({displayUnit}).</span>}</p>}
        {result && !result.count_exact && <p role="status">Partial scalar read: {result.unread_ids.length} unread. Counts and ordering are not global; retry or export the known evidence.</p>}
        <p>{result?.matching_count ?? '…'} matching of {result?.population_count ?? '…'} results.</p>
        {result && <details><summary>Metric source details</summary><p>Scalar files are checked against the saved document identity. Coordinate files are checked when opened.</p>{descriptor && <p>{metricKey} · {descriptor.scope} · native {descriptor.unit} · producer {descriptor.producer_version}</p>}</details>}
    </div>;
}

export function usePredictionScalars(designs: Design[], enabled = true) {
    const jobs = [...new Set(designs.filter(canonicalScalars).map(d => d.job_id))];
    const scalarQueries = useQueries({ queries: jobs.map(jobId => ({
        queryKey: ['prediction-native-scalars', jobId],
        queryFn: () => fetchJobDesignMetrics(jobId, false).then(r => r.data),
        enabled, retry: false, staleTime: 60_000,
    })) });
    const scalarsFor = (d: Design) => {
        const query = scalarQueries[jobs.indexOf(d.job_id)];
        const point = query?.data?.find(p => p.id === d.id && p.contract_revision === 1) as ScientificPoint | undefined;
        return scalarEvidence(d, point, query?.isPending ? 'Loading native scalar evidence…' : query?.isError ? 'Native scalar request failed. Structure and other confidence remain available.' : undefined);
    };
    return scalarsFor;
}
