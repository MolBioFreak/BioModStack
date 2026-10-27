import { isNgsJob, type NgsJobRouteIdentity } from './ngsResultRouting';

export type JobCategory = 'ngs' | 'alignments' | 'other';
export type JobCategoryVisibility = { ngs: boolean; alignments: boolean };
type JobIdentity = Pick<NgsJobRouteIdentity, 'model_id' | 'mode' | 'params'>;

const ALIGNMENT_MODELS = new Set(['family_msa_protein', 'family_msa_nucleic']);

/** Folding-MSA jobs (msa_batch) are intentionally not family-alignment Jobs. */
export function jobCategory(job: JobIdentity): JobCategory {
    const model = typeof job.model_id === 'string' ? job.model_id.trim().toLowerCase() : '';
    if (ALIGNMENT_MODELS.has(model)) return 'alignments';
    return isNgsJob(job) ? 'ngs' : 'other';
}

export function isCategoryVisible(job: JobIdentity, visibility: JobCategoryVisibility): boolean {
    const category = jobCategory(job);
    return category === 'other' || visibility[category];
}


