import type { Job, RFD3LocalRedesignReadModel } from '../lib/api';

export function isRFD3LocalRedesignResultJob(job: Job | null | undefined): boolean {
    if (!job) return false;
    return String(job.model_id || '').toLowerCase() === 'protein_local_redesign'
        && String(job.mode || '').toLowerCase() === 'local_redesign';
}

export function getRFD3LocalRedesignCandidateLabel(job: Job | null | undefined): string | null {
    if (!isRFD3LocalRedesignResultJob(job)) return null;
    const requestedCount = Number(job?.requested_design_count ?? job?.params?.num_designs);
    if (Number.isInteger(requestedCount) && requestedCount >= 0) {
        return `${requestedCount.toLocaleString()} RFD3 candidates`;
    }
    return 'RFD3 candidates';
}

export interface RFD3LocalRedesignRequestView {
    request: RFD3LocalRedesignReadModel['request']['request'] | undefined;
    status: string | undefined;
    requestSha256: string | undefined;
    profileId: string | undefined;
    profileRegistrySha256: string | undefined;
}

export function resolveRFD3LocalRedesignRequestView(
    result: RFD3LocalRedesignReadModel | undefined,
): RFD3LocalRedesignRequestView {
    const envelope = result?.request;
    return {
        request: envelope?.request,
        status: envelope?.status,
        requestSha256: envelope?.request_sha256,
        profileId: envelope?.profile_id,
        profileRegistrySha256: envelope?.profile_registry_sha256,
    };
}
