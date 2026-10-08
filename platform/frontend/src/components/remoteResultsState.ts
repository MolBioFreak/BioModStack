// Derived only from persisted server state: rendering/polling never requests a pull.
export interface RemoteResultsJob {
    id: string;
    status?: string;
    queue_status?: string;
    execution_target_id?: string | null;
    remote_state?: string | null;
    awaiting_input?: boolean | null;
    awaiting_stage?: string | null;
    error_message?: string | null;
    remote_waiting_reason?: string | null;
}

export function remoteResultsState(job: RemoteResultsJob) {
    if (!job.execution_target_id || (job.status ?? job.queue_status) !== 'awaiting_input'
        || job.awaiting_input !== true || job.awaiting_stage !== 'remote_results') return null;
    switch (job.remote_state) {
        case 'results_available': return { busy: false, failed: false, label: 'Pull results' };
        case 'returning': return { busy: true, failed: false, label: 'Pulling results…' };
        case 'result_pull_failed': return { busy: false, failed: true, label: 'Retry pull' };
        default: return null;
    }
}

export function remotePullError(error: unknown): string | null {
    if (!error) return null;
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    const detail = response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') return detail.message;
    return error instanceof Error ? error.message : 'Result pull failed. Results remain on the worker.';
}

// Existing query families; mark cached output lists stale even when not mounted.
export const remoteResultQueryKeys = (jobId: string): readonly (readonly string[])[] => [
    ['queue'], ['jobs'], ['job', jobId], ['designs'], ['backboneSummary', jobId],
    ['structure-files', jobId], ['docking-results', jobId],
];
