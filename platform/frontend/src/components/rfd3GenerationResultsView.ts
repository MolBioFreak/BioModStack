import type { Job } from '../lib/api';

export const isRFD3GenerationResultJob = (job: Job | null | undefined): boolean =>
    job?.model_id === 'protein_modification_experimental'
    && job?.mode === 'de_novo_design'
    && job?.params?.generator === 'rfd3';
