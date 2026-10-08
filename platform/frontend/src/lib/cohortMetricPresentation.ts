import { metricLabel } from './cohortAnalytics';

export const cohortPhases = ['screen', 'refine', 'anneal', 'harden', 'mutate'];
export const cohortPhaseLabel = (phase: string) => ({ screen: 'Screening', refine: 'Refinement', anneal: 'Annealing', harden: 'Hardening', mutate: 'Mutation' }[phase] ?? phase);
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
    const multipleTargets = new Set(keys.map(other => splitCohortMetric(other).target).filter(Boolean)).size > 1;
    const context = [p.target && (!keys.length || multipleTargets) ? metricLabel(p.target) : ''].filter(Boolean);
    const collisions = keys.filter(other => other !== key && splitCohortMetric(other).measurement !== p.measurement && metricLabel(splitCohortMetric(other).measurement) === shortLabel);
    const metric = name === 'iptm' ? 'iPTM' : name === 'ptm' ? 'pTM' : shortLabel;
    const label = `${p.phase ? `${cohortPhaseLabel(p.phase)} ${metric} (${p.reading === 'last recorded' ? 'last update' : 'peak'})` : shortLabel}${collisions.length ? ` (${p.measurement})` : ''}${context.length ? ` · ${context.join(' / ')}` : ''}`;
    const meaning = objective ? 'An optimization objective term, not a physical count or distance.' : name === 'iptm' ? 'The model’s confidence in the interface between interacting chains.' : name === 'ptm' ? 'The model’s confidence in the overall structure.' : name === 'seq_length' ? 'The number of amino acids in the sequence.' : name === 'duration_seconds' ? 'Time spent on the attempt, in seconds.' : 'A recorded model measurement.';
    const reading = p.reading === 'peak recorded' ? ` Highest recorded value during ${cohortPhaseLabel(p.phase).toLowerCase()}, not necessarily the best result.` : p.reading ? ` Last recorded value during ${cohortPhaseLabel(p.phase).toLowerCase()}; not the final acceptance prediction.` : '';
    return { label, shortLabel, description: `${meaning}${reading}`, group, nativeKey: key };
}
