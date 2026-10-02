import { metricLabel } from './cohortAnalytics';

export const cohortPhases = ['screen', 'refine', 'anneal', 'harden', 'mutate'];
export interface CohortMetricParts { measurement: string; target: string; phase: string; reading: string; kind: string }
/** Parse only the adapter's explicit phase/reading grammar; never change the key. */
export function splitCohortMetric(key: string): CohortMetricParts {
    const parts = key.split(' · ');
    if (parts.length === 3 && cohortPhases.includes(parts[0]) && ['last recorded', 'peak recorded'].includes(parts[2])) {
        const dot = parts[1].lastIndexOf('.');
        return { measurement: parts[1].slice(dot + 1), target: dot < 0 ? '' : parts[1].slice(0, dot), phase: parts[0], reading: parts[2], kind: 'trajectory' };
    }
    if (parts.length === 2) return { measurement: parts[1], target: parts[0], phase: '', reading: '', kind: 'target' };
    // Trace columns use the same target.measurement identity without a stage summary.
    const dot = key.lastIndexOf('.');
    if (parts.length === 1 && dot > 0 && dot < key.length - 1) return { measurement: key.slice(dot + 1), target: key.slice(0, dot), phase: '', reading: '', kind: 'trace' };
    return { measurement: key, target: '', phase: '', reading: '', kind: 'generic' };
}
const objectives: Record<string, string> = {
    binder_coldspot: 'Binder coldspot objective', binder_contacts: 'Binder contacts objective',
    binder_helicity: 'Binder helicity objective', binder_intra_coldspot: 'Binder internal coldspot objective',
    binder_pae: 'Binder PAE objective', compactness: 'Compactness objective',
    interface_contacts: 'Interface contacts objective', interface_pae: 'Interface PAE objective',
    iptm_loss: 'iPTM loss objective', plddt_loss: 'pLDDT loss objective',
};
export function describeCohortMetric(key: string, keys: string[] = []) {
    const p = splitCohortMetric(key);
    const name = p.measurement.toLowerCase();
    const objective = ['trajectory', 'trace'].includes(p.kind) && objectives[name] || (name.endsWith('_loss') ? `${metricLabel(p.measurement.slice(0, -5))} loss objective` : '');
    const shortLabel = objective || ({ iptm: 'Interface confidence (iPTM)', ptm: 'Overall structure confidence (pTM)', seq_length: 'Sequence length', duration_seconds: 'Duration' }[name] ?? metricLabel(p.measurement));
    const group = objective ? 'Optimization objectives' : ['iptm', 'ptm', 'plddt'].includes(name) ? 'Confidence' : ['seq_length', 'duration_seconds', 'rank'].includes(name) ? 'Record properties' : 'Other measurements';
    // Keep target identity whenever present: charts may compare different targets.
    const context = [p.target, p.phase && metricLabel(p.phase), p.reading === 'last recorded' ? 'Last' : p.reading === 'peak recorded' ? 'Peak' : ''].filter(Boolean);
    const collisions = keys.filter(other => other !== key && splitCohortMetric(other).measurement !== p.measurement && metricLabel(splitCohortMetric(other).measurement) === shortLabel);
    const label = `${shortLabel}${collisions.length ? ` (${p.measurement})` : ''}${context.length ? ` — ${context.join(' / ')}` : ''}`;
    const meaning = objective ? 'Native optimization objective term; not a physical contact count or distance.' : name === 'iptm' ? 'Model-reported interface confidence.' : name === 'ptm' ? 'Model-reported overall structure confidence.' : 'Native numeric measurement; no additional meaning, units or preferred direction inferred.';
    const reading = p.reading === 'peak recorded' ? ' Peak is the maximum recorded value in this stage, not necessarily the best value.' : p.reading ? ' Last is the final recorded value in this stage, not a fresh validation prediction.' : '';
    return { label, shortLabel, description: `${meaning}${reading} Native key: ${key}`, group, nativeKey: key };
}
