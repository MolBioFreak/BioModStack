type BioXpConnectionSnapshot = {
    configured: boolean;
    active: boolean;
    generation: number;
    target_url: string | null;
    reachable: boolean | null;
    runtime_ready: boolean | null;
    hardware_ready: boolean | null;
    capabilities: string[];
    observed_at: string | null;
    freshness_budget_seconds: number;
    fresh: boolean | null;
    last_error: string | null;
    command_active: boolean;
};

export type BioXpDisplayState =
    | 'NOT CONFIGURED'
    | 'SAVED / DISCONNECTED'
    | 'STALE'
    | 'UNREACHABLE'
    | 'UNKNOWN'
    | 'RUNTIME NOT READY'
    | 'HARDWARE NOT READY'
    | 'READY';

export interface BioXpDerivedStatus {
    label: BioXpDisplayState;
    ready: boolean;
    tone: 'neutral' | 'warning' | 'danger' | 'success';
    detail: string;
}

export function isBioXpCommandAvailable(
    availableCommands: readonly string[] | undefined,
    command: string,
    displayState: BioXpDisplayState | undefined,
): boolean {
    const freshTransportState = displayState === 'READY'
        || displayState === 'RUNTIME NOT READY'
        || displayState === 'HARDWARE NOT READY';
    return freshTransportState && availableCommands?.includes(command) === true;
}

export function deriveBioXpStatus(
    connection: BioXpConnectionSnapshot,
    nowMs: number = Date.now(),
): BioXpDerivedStatus {
    if (!connection.configured) {
        return {
            label: 'NOT CONFIGURED',
            ready: false,
            tone: 'neutral',
            detail: 'No saved BioXP profile exists.',
        };
    }
    if (!connection.active) {
        return {
            label: 'SAVED / DISCONNECTED',
            ready: false,
            tone: 'neutral',
            detail: 'The profile is saved, but this API process has not activated a robot client.',
        };
    }
    if (connection.fresh === true) {
        const observedMs = connection.observed_at ? Date.parse(connection.observed_at) : Number.NaN;
        const budgetMs = connection.freshness_budget_seconds * 1_000;
        if (!Number.isFinite(observedMs) || !Number.isFinite(budgetMs) || budgetMs <= 0) {
            return {
                label: 'UNKNOWN',
                ready: false,
                tone: 'warning',
                detail: 'Freshness metadata is missing or malformed.',
            };
        }
        if (nowMs > observedMs + budgetMs) {
            return {
                label: 'STALE',
                ready: false,
                tone: 'warning',
                detail: 'The cached observation exceeded its server freshness budget.',
            };
        }
    }
    if (connection.fresh === false) {
        return {
            label: 'STALE',
            ready: false,
            tone: 'warning',
            detail: 'The most recent observation is stale; readiness is unknown.',
        };
    }
    if (connection.reachable === false) {
        return {
            label: 'UNREACHABLE',
            ready: false,
            tone: 'danger',
            detail: connection.last_error ?? 'The configured API target did not answer the last probe.',
        };
    }
    if (connection.fresh !== true || connection.reachable !== true) {
        return {
            label: 'UNKNOWN',
            ready: false,
            tone: 'warning',
            detail: 'No fresh transport evidence is available.',
        };
    }
    if (connection.runtime_ready === false) {
        return {
            label: 'RUNTIME NOT READY',
            ready: false,
            tone: 'warning',
            detail: 'The API answered, but the BioXP runtime did not report ready.',
        };
    }
    if (connection.runtime_ready !== true) {
        return {
            label: 'UNKNOWN',
            ready: false,
            tone: 'warning',
            detail: 'API reachability is known, but runtime readiness is unknown.',
        };
    }
    if (connection.hardware_ready === false) {
        return {
            label: 'HARDWARE NOT READY',
            ready: false,
            tone: 'warning',
            detail: 'The runtime answered, but hardware readiness was not confirmed.',
        };
    }
    if (connection.hardware_ready !== true) {
        return {
            label: 'UNKNOWN',
            ready: false,
            tone: 'warning',
            detail: 'Runtime readiness is known, but hardware state is unknown.',
        };
    }
    return {
        label: 'READY',
        ready: true,
        tone: 'success',
        detail: 'Fresh API, runtime, and hardware evidence are all positive.',
    };
}
