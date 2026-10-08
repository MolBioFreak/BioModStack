import { useQuery } from '@tanstack/react-query';

import { fetchOntFastqQcResult, type OntFastqQcResult } from '../../lib/ontFastqQcResult';
import { isAlignmentAccessDenied } from '../../lib/ngsAlignmentSession';
import type { Job } from '../../lib/api';

const TERMINAL_JOB_STATUSES: Array<Job['status']> = ['completed', 'failed', 'cancelled'];

export interface OntFastqQcResultQueryState {
    result: OntFastqQcResult | null;
    loading: boolean;
    error: string | null;
    accessDenied: boolean;
}

export function useOntFastqQcResult(
    jobId: string | null | undefined,
    jobStatus?: Job['status'] | null,
    workflowId?: string | null,
): OntFastqQcResultQueryState {
    const enabled = Boolean(jobId)
        && workflowId === 'ont_fastq_qc'
        && (!jobStatus || TERMINAL_JOB_STATUSES.includes(jobStatus));
    const query = useQuery({
        queryKey: ['ont-fastq-qc-result', jobId, jobStatus, workflowId],
        queryFn: async () => {
            if (!jobId) throw new Error('ONT FASTQ-QC result requires a job id');
            return fetchOntFastqQcResult(jobId);
        },
        enabled,
        retry: false,
        refetchOnWindowFocus: false,
        staleTime: 30_000,
    });
    if (!enabled) return { result: null, loading: false, error: null, accessDenied: false };
    return {
        result: query.data ?? null,
        loading: query.isLoading || query.isFetching,
        error: query.error instanceof Error ? query.error.message : query.error ? 'Unable to load ONT FASTQ-QC result' : null,
        accessDenied: isAlignmentAccessDenied(query.error),
    };
}
