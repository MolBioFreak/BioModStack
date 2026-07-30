export interface BioXpCameraHealthStatus {
    state: 'live' | 'stale' | 'unavailable';
    available: boolean;
    frame_sequence: number | null;
    frame_age_seconds: number | null;
    freshness_budget_seconds: number;
    provider_generation: number;
}

export interface BioXpCameraPresentation {
    label: 'LIVE' | 'STALE' | 'UNAVAILABLE';
    detail: string;
    effectiveFrameAgeSeconds: number | null;
}

export function deriveBioXpCameraPresentation({
    status,
    statusReceivedAtMs,
    lastSequenceAdvanceAtMs,
    nowMs,
    error,
}: {
    status: BioXpCameraHealthStatus | null;
    statusReceivedAtMs: number;
    lastSequenceAdvanceAtMs: number;
    nowMs: number;
    error: string | null;
}): BioXpCameraPresentation {
    if (error) {
        return { label: 'UNAVAILABLE', detail: error, effectiveFrameAgeSeconds: null };
    }
    if (!status || !status.available || status.state === 'unavailable') {
        return {
            label: 'UNAVAILABLE',
            detail: 'No validated camera frame is available from the managed BioXP target.',
            effectiveFrameAgeSeconds: null,
        };
    }
    const effectiveFrameAgeSeconds = status.frame_age_seconds === null
        ? null
        : status.frame_age_seconds + Math.max(0, nowMs - statusReceivedAtMs) / 1_000;
    if (status.state === 'stale'
        || effectiveFrameAgeSeconds === null
        || effectiveFrameAgeSeconds > status.freshness_budget_seconds) {
        return {
            label: 'STALE',
            detail: 'The latest validated camera frame is older than its freshness budget.',
            effectiveFrameAgeSeconds,
        };
    }
    if (nowMs - lastSequenceAdvanceAtMs > status.freshness_budget_seconds * 1_000) {
        return {
            label: 'STALE',
            detail: 'The camera frame sequence has not advanced within its freshness budget.',
            effectiveFrameAgeSeconds,
        };
    }
    return {
        label: 'LIVE',
        detail: 'The managed camera proxy reports a fresh, advancing frame.',
        effectiveFrameAgeSeconds,
    };
}

export interface CameraObjectUrlOwner {
    begin(): number;
    adopt(token: number, blob: Blob): string | null;
    clear(): void;
    dispose(): void;
    isCurrent(token: number): boolean;
}

export function createCameraObjectUrlOwner(operations: {
    create: (blob: Blob) => string;
    revoke: (url: string) => void;
} = {
    create: (blob) => URL.createObjectURL(blob),
    revoke: (url) => URL.revokeObjectURL(url),
}): CameraObjectUrlOwner {
    let generation = 0;
    let currentUrl: string | null = null;
    let disposed = false;

    const revokeCurrent = () => {
        if (!currentUrl) return;
        operations.revoke(currentUrl);
        currentUrl = null;
    };

    return {
        begin() {
            generation += 1;
            return generation;
        },
        adopt(token, blob) {
            const candidate = operations.create(blob);
            if (disposed || token !== generation) {
                operations.revoke(candidate);
                return null;
            }
            revokeCurrent();
            currentUrl = candidate;
            return candidate;
        },
        clear() {
            generation += 1;
            revokeCurrent();
        },
        dispose() {
            if (disposed) return;
            disposed = true;
            generation += 1;
            revokeCurrent();
        },
        isCurrent(token) {
            return !disposed && token === generation;
        },
    };
}
