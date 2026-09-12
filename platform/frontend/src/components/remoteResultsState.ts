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
    provenance?: Record<string, unknown> | null;
}

export function remoteResultsState(job: RemoteResultsJob) {
    if (!job.execution_target_id || job.awaiting_input !== true || job.awaiting_stage !== 'remote_results') return null;
    const rawReceipt = job.provenance?.remote_execution_receipt;
    const receipt = rawReceipt && typeof rawReceipt === 'object' && !Array.isArray(rawReceipt)
        ? rawReceipt as Record<string, unknown> : null;
    const received = typeof receipt?.received_manifest_sha256 === 'string'
        && receipt.received_manifest_sha256.length > 0
        && receipt.received_manifest_sha256 === receipt.result_manifest_sha256;
    if (job.remote_state === 'returning') {
        return job.status === 'running'
            ? { busy: true, failed: false, received, label: received ? 'Importing received results…' : 'Pulling results…' } : null;
    }
    if (job.status !== 'awaiting_input') return null;
    switch (job.remote_state) {
        case 'results_available': return { busy: false, failed: false, received: false, label: 'Pull results' };
        case 'result_pull_failed': return { busy: false, failed: true, received, label: received ? 'Retry import' : 'Retry pull' };
        default: return null;
    }
}

export function remotePullError(error: unknown): string | null {
    if (!error) return null;
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    const detail = response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') return detail.message;
    return error instanceof Error ? error.message : 'Result return/import failed. Retained results remain available for retry.';
}

// Existing query families; mark cached output lists stale even when not mounted.
export const remoteResultQueryKeys = (jobId: string): readonly (readonly string[])[] => [
    ['queue'], ['jobs'], ['job', jobId], ['designs'], ['backboneSummary', jobId],
    ['structure-files', jobId], ['docking-results', jobId],
];
