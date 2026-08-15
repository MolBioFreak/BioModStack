import type { Job } from './api';

export interface JobOutputSummary {
    count: number;
    label: string;
}

export function getCompletedScientificResultStatus(
    status: Job['status'],
    acceptedResultCount: number | undefined,
): { styleKey: 'no_results'; label: string } | null {
    return status === 'completed' && acceptedResultCount === 0
        ? { styleKey: 'no_results', label: 'completed · no accepted results' }
        : null;
}

export function getJobOutputSummary(job: Job): JobOutputSummary {
    const isFrustraMpnnOutput = job.model_id === 'frustrampnn'
        || (job.frustrampnn_result_count ?? 0) > 0;
    if (isFrustraMpnnOutput) {
        const count = job.frustrampnn_result_count ?? 0;
        return {
            count,
            label: `${count.toLocaleString()} FrustraMPNN result${count === 1 ? '' : 's'}`,
        };
    }

    const count = job.requested_design_count ?? job.design_count;
    return {
        count,
        label: `${count.toLocaleString()} design${count === 1 ? '' : 's'}`,
    };
}
