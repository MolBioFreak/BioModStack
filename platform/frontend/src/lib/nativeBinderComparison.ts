import type { Job } from './api';
import { isRfd3Comparison, fetchRfd3ComparisonPage } from './rfd3Comparison';
import { bc2Page, fetchBC2Page } from './bindcraft2Results';
import { fetchNativeBinderGeneration, isNativeBinderGeneration, nativeGenerationMetrics, nativeGenerationMetricUnit, type NativeGenerationRecord, type NativeGenerationPage } from './nativeBinderResults';
import type { NativeCandidateSource } from './binderContinuation';

/** A view of the native publication, never a replacement Design or numerical store. */
export interface NativeComparisonSet {
    job: Pick<Job, 'id' | 'name' | 'model_id' | 'mode'> & { params?: Job['params'] };
    records: NativeGenerationRecord[];
    total: number;
    complete: boolean;
    error?: string;
}
export const hasNativeComparison = (job: { model_id: string; mode: string; params?: Job['params'] }) => job.model_id === 'bindcraft2' || isNativeBinderGeneration(job) || isRfd3Comparison(job);
export const nativeComparisonKey = (jobId: string, record: NativeGenerationRecord, index: number) => JSON.stringify([jobId, record.candidate_key ?? record.design_id ?? `record:${index}`]);

/** Reuse the paginated producer reader; later-page failure leaves earlier evidence usable. */
export async function readNativeComparison(job: NativeComparisonSet['job'], signal?: AbortSignal): Promise<NativeComparisonSet> {
    const records: NativeGenerationRecord[] = [];
    const seen = new Set<string>();
    let total = 0, offset = 0;
    try {
        let arms: Array<string | null> = [null];
        for (let armIndex = 0; armIndex < arms.length; armIndex++) {
            offset = 0;
            let armTotal = 0;
            do {
                let page: NativeGenerationPage;
                if (job.model_id === 'bindcraft2') {
                    const native = await fetchBC2Page(job.id, arms[armIndex], 'draw', offset, signal);
                    page = bc2Page(native);
                    if (armIndex === 0 && offset === 0 && native.arms.length > 1) {
                        const rest = native.arms.map(arm => arm.name).filter(name => name !== native.arm);
                        arms = [native.arm, ...rest];
                    }
                } else if (isRfd3Comparison(job)) page = await fetchRfd3ComparisonPage(job.id, offset, signal);
                else page = await fetchNativeBinderGeneration(job.id, offset, 100, signal);
                if (offset === 0) { armTotal = page.total; total += page.total; }
                if (page.total !== armTotal) return { job, records, total, complete: false, error: 'The native population changed while loading. Refresh to compare one current population.' };
                const previousCount = records.length;
                for (const row of page.records) {
                    const key = nativeComparisonKey(job.id, row, offset + records.length - previousCount);
                    if (!seen.has(key)) { seen.add(key); records.push(row); }
                }
                if (!page.records.length || records.length === previousCount) {
                    if (offset < armTotal) return { job, records, total, complete: false, error: 'Native pagination ended before the declared population.' };
                    break;
                }
                offset += page.records.length;
            } while (offset < armTotal);
            if (job.model_id === 'bindcraft2') {
                // The producer explicitly maps ranked representatives to scored draws.
                // Keep the draw's values; attach only those exact selectable identities.
                const recordIndex = new Map(records.map((row, index) => [row.candidate_key, index]));
                let rankedOffset = 0, rankedTotal = 0;
                do {
                    const ranked = await fetchBC2Page(job.id, arms[armIndex], 'retained', rankedOffset, signal);
                    rankedTotal = ranked.total;
                    for (const binding of ranked.rows) {
                        const values = binding.values && typeof binding.values === 'object' ? binding.values as Record<string, unknown> : {};
                        if (typeof values.bms_scored_design !== 'string') continue;
                        const i = recordIndex.get(JSON.stringify([ranked.arm, 'draw', values.bms_scored_design]));
                        if (i === undefined) continue;
                        const row = records[i];
                        const previous = Array.isArray(row.comparison_bindings) ? row.comparison_bindings : [];
                        records[i] = { ...row, comparison_bindings: [...previous, { design_id: binding.design_id, structures: binding.structures, native_record: binding }] };
                    }
                    if (!ranked.rows.length && rankedOffset < rankedTotal) return { job, records, total, complete: false, error: 'Ranked source links are only partially available.' };
                    rankedOffset += ranked.rows.length;
                } while (rankedOffset < rankedTotal);
            }
        }
        return { job, records, total, complete: records.length === total };
    } catch (reason) {
        if (signal?.aborted) throw reason;
        return { job, records, total, complete: false, error: reason instanceof Error ? reason.message : String(reason) };
    }
}

export function nativeComparisonSelection(set: NativeComparisonSet, designs?: string[], sources?: NativeCandidateSource[]): NativeGenerationRecord[] {
    if (designs === undefined && sources === undefined) return set.records;
    const designIds = new Set(designs ?? []);
    const artifacts = new Set((sources ?? []).filter(source => source.job_id === set.job.id).map(source => source.artifact_id));
    return set.records.filter(row => (!!row.design_id && designIds.has(row.design_id))
        || row.structures?.some(document => artifacts.has(document.artifact_id))
        || (Array.isArray(row.comparison_bindings) && row.comparison_bindings.some(binding => {
            if (!binding || typeof binding !== 'object') return false;
            const item = binding as { design_id?: string; structures?: Array<{ artifact_id: string }> };
            return (!!item.design_id && designIds.has(item.design_id)) || item.structures?.some(document => artifacts.has(document.artifact_id));
        })));
}
export function nativeComparisonOutcome(row: NativeGenerationRecord): string {
    const native = row.native_record && typeof row.native_record === 'object' ? row.native_record as Record<string, unknown> : row;
    const outcome = native.outcome ?? native.disposition ?? row.outcome ?? row.disposition;
    return typeof outcome === 'string' ? outcome : '';
}
export const nativeComparisonValues = nativeGenerationMetrics;
export function nativeComparisonLabel(row: NativeGenerationRecord, ordinal: number): string {
    for (const value of [row.display_name, row.label]) if (typeof value === 'string' && value.length <= 48) return value;
    return `Candidate ${ordinal + 1}`;
}
const BC2_LABELS: Record<string, string> = {
    i_pTM: 'iPTM', pTM: 'pTM', pLDDT: 'Binder pLDDT in complex', Unbound_Binder_pLDDT: 'Binder-alone pLDDT',
    Target_pLDDT: 'Target pLDDT', i_pAE: 'Interface PAE / 31',
    i_pDAE: 'Interface confidence (iPDae)', Binder_RMSD: 'Binder RMSD', Interface_BuriedArea: 'Buried area', seq_length: 'Sequence length',
};
const BC2_UNITS: Record<string, string> = { i_pTM: 'fraction', pTM: 'fraction', pLDDT: 'fraction', Target_pLDDT: 'fraction', Unbound_Binder_pLDDT: 'fraction', i_pAE: 'dimensionless', i_pDAE: 'fraction', Binder_RMSD: 'angstrom', Interface_BuriedArea: 'angstrom_squared', seq_length: 'residues' };
export function nativeComparisonMetric(set: NativeComparisonSet, row: NativeGenerationRecord, key: string) {
    const unit = nativeGenerationMetricUnit(row, key) ?? (set.job.model_id === 'bindcraft2' ? BC2_UNITS[key] : undefined);
    const native = row.native_record && typeof row.native_record === 'object' ? row.native_record as Record<string, unknown> : {};
    const values = native.values && typeof native.values === 'object' ? native.values as Record<string, unknown> : {};
    const version = typeof values.bindcraft_version === 'string' ? values.bindcraft_version : null;
    // Unknown units/producer versions are not silently pooled across independent runs.
    const cohort = JSON.stringify([set.job.model_id, set.job.mode, version ?? set.job.id, key, unit ?? set.job.id]);
    const label = set.job.model_id === 'bindcraft2' && BC2_LABELS[key] ? BC2_LABELS[key] : key.replaceAll('_', ' ');
    const suffix = unit === 'fraction' ? '0–1' : unit === 'angstrom' ? 'Å' : unit === 'angstrom_squared' ? 'Å²' : unit;
    return { key: cohort, metric: key, label: `${label}${suffix ? ` (${suffix})` : ''}`, unit: unit ?? 'native units' };
}
