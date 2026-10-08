import { useQuery } from '@tanstack/react-query';

import {
    fetchSequenceQcManifest,
    type Job,
    type SequenceQcManifest,
} from '../../lib/api';
import {
    classifySequenceQcManifestError,
    getSequenceQcManifestErrorMessage,
    type SequenceQcManifestStatus,
} from './sequenceQcManifestState';
import { withAlignmentAccessRecovery } from '../../lib/ngsAlignmentSession';

export interface SequenceQcManifestQueryState {
    status: SequenceQcManifestStatus;
    manifest: SequenceQcManifest | null;
    message: string | null;
    isFetching: boolean;
}

const TERMINAL_JOB_STATUSES: Array<Job['status']> = ['completed', 'failed', 'cancelled'];

export function shouldFetchSequenceQcManifest(jobStatus?: Job['status'] | null): boolean {
    return !jobStatus || TERMINAL_JOB_STATUSES.includes(jobStatus);
}

export function useSequenceQcManifest(
    jobId: string | null | undefined,
    jobStatus?: Job['status'] | null,
): SequenceQcManifestQueryState {
    const query = useQuery({
        queryKey: ['sequence-qc-manifest', jobId, jobStatus],
        queryFn: async () => {
            if (!jobId) throw new Error('sequence-QC manifest requires a job id');
            return withAlignmentAccessRecovery(jobId, async () => {
                const response = await fetchSequenceQcManifest(jobId);
                return response.data;
            });
        },
        enabled: Boolean(jobId) && shouldFetchSequenceQcManifest(jobStatus),
        retry: false,
        refetchOnWindowFocus: false,
        staleTime: 30_000,
    });

    if (!jobId) {
        return { status: 'idle', manifest: null, message: null, isFetching: false };
    }

    if (!shouldFetchSequenceQcManifest(jobStatus)) {
        return {
            status: 'unavailable-pending',
            manifest: null,
            message: 'Sequence-QC manifest will be available after this run reaches a terminal state.',
            isFetching: false,
        };
    }

    if (query.isLoading) {
        return { status: 'loading', manifest: null, message: null, isFetching: query.isFetching };
    }

    if (query.data) {
        return { status: 'available', manifest: query.data, message: null, isFetching: query.isFetching };
    }

    if (query.error) {
        const classified = classifySequenceQcManifestError(query.error);
        const status = classified === 'unavailable-old-run' && jobStatus && !['completed', 'failed', 'cancelled'].includes(jobStatus)
            ? 'unavailable-pending'
            : classified;
        return {
            status,
            manifest: null,
            message: getSequenceQcManifestErrorMessage(query.error),
            isFetching: query.isFetching,
        };
    }

    return { status: 'idle', manifest: null, message: null, isFetching: query.isFetching };
}
