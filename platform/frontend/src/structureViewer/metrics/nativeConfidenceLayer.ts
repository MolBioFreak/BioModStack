import type { ScientificNativeMetric } from '../../lib/scientificViewerIdentity';
import type { MetricLayer } from './metricContracts.js';

/** Established AlphaFold-style display bands; native values are never rescaled. */
export function plddtBandColor(value: number): { r: number; g: number; b: number } {
    if (value >= 90) return { r: 59, g: 130, b: 246 };
    if (value >= 70) return { r: 34, g: 211, b: 238 };
    if (value >= 50) return { r: 250, g: 204, b: 21 };
    return { r: 249, g: 115, b: 22 };
}

export const PLDDT_BANDS = [
    { key: 'veryHigh', label: '≥90', color: '#3b82f6' },
    { key: 'confident', label: '70–<90', color: '#22d3ee' },
    { key: 'low', label: '50–<70', color: '#facc15' },
    { key: 'veryLow', label: '<50', color: '#f97316' },
] as const;

const hasAtom = (identity: { labelAtomId?: string; authAtomId?: string }) => Boolean(identity.labelAtomId || identity.authAtomId);

/** Shared native pLDDT viewer layers: residue/atom layer plus the separate atom-token layer of mixed token ledgers. */
export function nativePlddtLayers(native: ScientificNativeMetric): { residue: MetricLayer | null; atomTokens: MetricLayer | null } {
    if (native.status !== 'ok' || native.metric === 'chain_metrics') return { residue: null, atomTokens: null };
    const residue = {
        descriptor: {
            id: 'native-plddt',
            label: native.metric === 'atom_plddt' ? 'Native atom pLDDT' : native.metric === 'token_plddt' ? 'Native polymer-residue pLDDT' : 'Native residue pLDDT',
            dimension: native.metric === 'atom_plddt' ? 'atom-scalar' : 'residue-scalar',
            units: 'fraction', direction: 'higher_is_better', valueRange: [0, 1], projectionPolicy: 'direct', normalization: 'none',
            categories: Object.fromEntries(PLDDT_BANDS.map(band => [band.key, { label: band.label, color: band.color }])),
            provenance: { source: 'Verified native confidence vector', artifactSha256: native.artifactSha256 },
        },
        values: native.residues.map((identity, index) => ({ identity, value: native.values[index], displayColor: plddtBandColor(native.values[index]! * 100) }))
            .filter(entry => native.metric !== 'token_plddt' || !hasAtom(entry.identity)),
    } as MetricLayer;
    const atomTokens = native.metric !== 'token_plddt' ? null : {
        descriptor: { ...residue.descriptor, id: 'native-plddt-atoms', label: 'Native atom-token pLDDT', dimension: 'atom-scalar' },
        values: native.residues.flatMap((identity, index) => hasAtom(identity)
            ? [{ identity, value: native.values[index], displayColor: plddtBandColor(native.values[index]! * 100) }] : []),
    } as MetricLayer;
    return { residue, atomTokens };
}
