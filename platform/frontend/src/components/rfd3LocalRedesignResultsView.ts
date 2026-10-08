import type { RFD3LocalRedesignReadModel } from '../lib/api';

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
