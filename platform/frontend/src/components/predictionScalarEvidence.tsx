import { useQueries } from '@tanstack/react-query';
import { fetchJobDesignMetrics, type Design } from '../lib/api';
import type { ScientificPoint } from '../lib/scientificAnalytics';
const asRecord = (value: unknown): Record<string, unknown> | null => value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const nativeSummaryKeys: Record<string, string> = { plddt_mean: 'Mean pLDDT (fraction)', plddt: 'Mean pLDDT (0–100)', complex_plddt: 'Complex pLDDT (fraction)', complex_iplddt: 'Interface pLDDT (fraction)', ptm: 'pTM', iptm: 'iPTM', ranking_score: 'Producer ranking score', confidence_score: 'Producer confidence score', gpde: 'gPDE (Å)', complex_pde: 'Complex PDE (Å)', complex_ipde: 'Interface PDE (Å)', ligand_iptm: 'Ligand iPTM', protein_iptm: 'Protein iPTM'  };
type ScalarDisplay = { key: string; label: string; display: string; title: string };
export type ScalarEvidence = { entries: ScalarDisplay[]; reason?: string };
export const canonicalScalars = (design: Design) => design.core_protein_scientific_contract === 1 || design.confidence_metrics?.core_protein_scientific_contract === 1 || !!design.scientific_structure_document || !!design.confidence_metrics?.core_protein_scientific;
const finiteScalar = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
/** Consume the existing source-verified analytics transport, not nullable DB projections. */
function scalarEvidence(design: Design, point?: ScientificPoint, reason?: string): ScalarEvidence {
    if (!canonicalScalars(design)) {
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
        return [{ key, label, display: (plddt && descriptor.unit === 'fraction' ? state.value * 100 : state.value).toFixed(plddt ? 2 : 4), title }];
    }) };
}
export const scalarCell = (evidence: ScalarEvidence, keys: string[]) => {
    const entry = keys.map(key => evidence.entries.find(e => e.key === key)).find(Boolean);
    return <td title={entry ? `${entry.label}: ${entry.title}` : evidence.reason ?? 'Not reported'}>{entry?.display ?? '—'}</td>;
};

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
