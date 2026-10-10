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
export function NativeScalarControls({query, result, onChange}: {
    query: NativeScalarQuery; result?: NativeResults | null; onChange: (query: NativeScalarQuery) => void;
}) {
    const descriptors = result?.cohorts[query.cohort_key ?? '']?.metric_descriptors ?? {};
    const descriptor = descriptors[query.metric_id ?? ''];
    const scale = query.metric_id?.includes('plddt') && descriptor?.unit === 'fraction' ? 100 : 1;
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
        onChange({...query, minimum: min, maximum: max});
    };
    const style = 'rounded border border-slate-600 bg-slate-900 p-1 text-xs';
    const summary = result?.summaries[query.cohort_key ?? '']?.[query.metric_id ?? ''];
    return <div className="space-y-2 text-xs">
        <div className="flex flex-wrap gap-2 items-center">
            <label>Cohort <select className={style} aria-label="Native cohort" value={query.cohort_key ?? ''} onChange={e => onChange(e.target.value ? {cohort_key: e.target.value} : {})}>
                <option value="">All cohorts · no pooled ranking</option>
                {Object.entries(result?.cohorts ?? {}).map(([key, cohort]) => <option key={key} value={key}>{key.split(':').slice(2).join(':')} · {Object.values(cohort.metric_descriptors)[0]?.producer_version} · {cohort.count} rows</option>)}
            </select></label>
            <label>Metric <select className={style} aria-label="Native metric" value={query.metric_id ?? ''} onChange={e => onChange({cohort_key: query.cohort_key, metric_id: e.target.value || undefined, unit: descriptors[e.target.value]?.unit})}>
                <option value="">Choose a native metric</option>
                {Object.entries(descriptors).map(([key, d]) => <option key={key} value={key}>{key} · {d.scope} · {key.includes('plddt') ? '0–100 display' : d.unit}</option>)}
            </select></label>
            {descriptor && <>
                <label>Minimum <input className={style} aria-label="Native minimum" inputMode="decimal" value={minimum} onChange={e => setMinimum(e.target.value)} onKeyDown={e => {if (e.key === 'Enter') apply();}} /></label>
                <label>Maximum <input className={style} aria-label="Native maximum" inputMode="decimal" value={maximum} onChange={e => setMaximum(e.target.value)} onKeyDown={e => {if (e.key === 'Enter') apply();}} /></label>
                <button className={style} onClick={apply}>Apply bounds</button>
                <label><input type="checkbox" checked={query.include_missing ?? false} onChange={e => onChange({...query, include_missing: e.target.checked})} /> Include unavailable/invalid under bounds</label>
                <select className={style} aria-label="Native ordering" value={query.order ?? ''} onChange={e => onChange({...query, order: (e.target.value || null) as NativeScalarQuery['order']})}>
                    <option value="">Name order</option><option value="desc">Native descending</option><option value="asc">Native ascending</option>
                </select>
            </>}
        </div>
        {descriptor && <p>{descriptor.scope}; raw {descriptor.unit}{scale === 100 ? '; displayed 0–100 (bounds converted once)' : ''}. Blank is unbounded; zero is a bound. Missing sorts last.</p>}
        {summary && <p>Matching cohort: {summary.observed_count} observed · {summary.unavailable_count} unavailable · {summary.invalid_count} invalid. {summary.statistics && <>Mean {summary.statistics.avg} {summary.descriptor.unit}; min {summary.statistics.min}; max {summary.statistics.max}.</>}</p>}
        {result && !result.count_exact && <p role="status">Partial scalar read: {result.unread_ids.length} unread. Counts and ordering are not global; retry or export the known evidence.</p>}
        <p>{result?.matching_count ?? '…'} matching / {result?.population_count ?? '…'} in population. Scalar bytes checked against persisted document binding; coordinates are checked when opened, not during table reads.</p>
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
