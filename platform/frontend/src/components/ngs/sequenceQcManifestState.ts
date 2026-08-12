export type SequenceQcManifestStatus =
    | 'idle'
    | 'loading'
    | 'available'
    | 'unavailable-old-run'
    | 'unavailable-pending'
    | 'malformed'
    | 'access-denied'
    | 'forbidden'
    | 'error';

function getStatus(error: unknown): number | null {
    if (typeof error !== 'object' || error === null) return null;
    const response = (error as { response?: unknown }).response;
    if (typeof response !== 'object' || response === null) return null;
    const status = (response as { status?: unknown }).status;
    return typeof status === 'number' ? status : null;
}

export function getSequenceQcManifestErrorMessage(error: unknown): string {
    if (typeof error !== 'object' || error === null) return 'Unable to load sequence-QC manifest';
    const response = (error as { response?: unknown }).response;
    if (typeof response === 'object' && response !== null) {
        const data = (response as { data?: unknown }).data;
        if (typeof data === 'object' && data !== null) {
            const detail = (data as { detail?: unknown }).detail;
            if (typeof detail === 'string' && detail.trim()) return detail;
        }
    }
    const message = (error as { message?: unknown }).message;
    return typeof message === 'string' && message.trim()
        ? message
        : 'Unable to load sequence-QC manifest';
}

export function classifySequenceQcManifestError(error: unknown): SequenceQcManifestStatus {
    const status = getStatus(error);
    const message = getSequenceQcManifestErrorMessage(error).toLowerCase();

    if (status === 404 && message.includes('manifest not found')) return 'unavailable-old-run';
    if (status === 403 && message.includes('alignment access denied')) return 'access-denied';
    if (status === 403) return 'forbidden';
    if (status === 400 || message.includes('not valid json') || message.includes('unsupported artifact_schema_version')) {
        return 'malformed';
    }
    return 'error';
}

export function sequenceQcManifestUnavailableLabel(status: SequenceQcManifestStatus): string {
    switch (status) {
        case 'unavailable-old-run':
            return 'manifest unavailable for older run';
        case 'unavailable-pending':
            return 'manifest not available yet';
        case 'malformed':
            return 'malformed sequence-QC manifest';
        case 'access-denied':
            return 'manifest access requires browser authorization';
        case 'forbidden':
            return 'manifest blocked by path safety';
        case 'idle':
            return 'no run selected';
        default:
            return 'sequence-QC manifest unavailable';
    }
}
