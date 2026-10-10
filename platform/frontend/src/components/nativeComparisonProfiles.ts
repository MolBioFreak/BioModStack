import type { ScientificNativeMetric } from '../lib/scientificViewerIdentity';
import { canonicalSpatialRefKey, type AtomRef } from '../structureViewer/contracts/structureIdentity';

export const comparisonChain = (ref: AtomRef) => ref.authAsymId ?? ref.labelAsymId ?? '';
export const comparisonLabel = (ref: AtomRef) => `${comparisonChain(ref)}:${ref.authSeqId ?? ref.labelSeqId}${ref.insertionCode ?? ''} ${ref.componentId ?? ''}${ref.labelAtomId || ref.authAtomId ? ` / ${ref.labelAtomId ?? ref.authAtomId}` : ''} · model ${ref.modelId} · altloc ${ref.altLoc || 'none'}`;
// Match all published identity fields, except the document (each sample has its own).
// This is an explicit label correspondence, not evidence of structural alignment.
export const comparisonKey = (ref: AtomRef) => canonicalSpatialRefKey({ ...ref, documentId: 'comparison-labels' });
export type ComparisonProfile = { id: string; name: string; metric: ScientificNativeMetric };
export function comparisonProfiles(profiles: ComparisonProfile[], chain: string) {
    const categories = new Map<string, string>();
    const traces = profiles.flatMap(profile => {
        if (profile.metric.status !== 'ok') return [];
        const metric = profile.metric;
        const chains = [...new Set(metric.residues.map(comparisonChain))].filter(id => !chain || id === chain);
        return chains.map(chainId => {
            const indices = metric.residues.flatMap((ref, i) => comparisonChain(ref) === chainId ? [i] : []);
            const keys = indices.map(i => comparisonKey(metric.residues[i]));
            keys.forEach((key, j) => categories.set(key, comparisonLabel(metric.residues[indices[j]])));
            return { id: profile.id, chain: chainId, name: `${profile.name} (${profile.id}) · ${chainId} · ${metric.metric}`,
                x: keys, y: indices.map(i => metric.values[i] * 100),
                text: indices.map(i => `${comparisonLabel(metric.residues[i])}<br>Native ${metric.values[i]} fraction<br>Document ${metric.document.documentId}`),
            };
        });
    });
    const counts = new Map<string, Set<string>>();
    traces.forEach(t => t.x.forEach(key => { const ids = counts.get(key) ?? new Set<string>(); ids.add(t.id); counts.set(key, ids); }));
    return { traces, categories: [...categories], rows: profiles.map(profile => {
        const keys = traces.filter(t => t.id === profile.id).flatMap(t => t.x);
        return { ...profile, count: keys.length, matched: keys.filter(key => (counts.get(key)?.size ?? 0) > 1).length };
    }) };
}
