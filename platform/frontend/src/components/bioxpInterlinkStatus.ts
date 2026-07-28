type BioXpConnectionSnapshot = {
    configured: boolean;
    active: boolean;
    generation: number;
    target_url: string | null;
    reachable: boolean | null;
    runtime_ready: boolean | null;
    hardware_ready: boolean | null;
    hardware_observed_at: string | null;
    hardware_fresh: boolean | null;
    hardware_stale: boolean;
    hardware_evidence_error: string | null;
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
    | 'UNKNOWN'
    | 'STALE'
    | 'UNREACHABLE'
    | 'RUNTIME NOT READY'
    | 'RUNTIME READY'
    | 'HARDWARE NOT READY'
    | 'READY';

export interface BioXpDerivedStatus {
    label: BioXpDisplayState;
    ready: boolean;
    tone: 'neutral' | 'warning' | 'danger' | 'success';
    detail: string;
}

function localFreshnessMetadata(
    connection: BioXpConnectionSnapshot,
    nowMs: number,
): 'fresh' | 'expired' | 'invalid' {
    const observedMs = connection.observed_at ? Date.parse(connection.observed_at) : Number.NaN;
    const budgetMs = connection.freshness_budget_seconds * 1_000;
    if (!Number.isFinite(observedMs) || !Number.isFinite(budgetMs) || budgetMs <= 0) return 'invalid';
    return nowMs <= observedMs + budgetMs ? 'fresh' : 'expired';
}

export function isBioXpControlPlaneFresh(
    connection: BioXpConnectionSnapshot | undefined,
    nowMs: number = Date.now(),
): boolean {
    return connection?.active === true
        && connection.fresh === true
        && connection.reachable === true
        && localFreshnessMetadata(connection, nowMs) === 'fresh';
}

const RETIRED_BIOXP_COMMANDS = new Set([
    'activate_usb_for_service',
    'initialize_oem_environment',
    'run_oem_motor_stage',
    'record_oem_motor_stage_observation',
]);

export function isBioXpCommandAvailable(
    availableCommands: readonly string[] | undefined,
    command: string,
    displayState: BioXpDisplayState | undefined,
): boolean {
    void displayState;
    // These names are retained only for parsing historical records. A stale or
    // mismatched server must never reactivate their operator surfaces.
    if (RETIRED_BIOXP_COMMANDS.has(command)) return false;
    const serverAdmitted = availableCommands?.includes(command) === true;
    // The server has already evaluated lifecycle, freshness, runtime,
    // hardware, capability, and mutation policy for supported commands.
    // Display labels must not impose a second, broader admission policy.
    return serverAdmitted;
}

export function deriveBioXpNoCommandsMessage(
    commandActive: boolean,
    availableCommands: readonly string[] | undefined,
): string | null {
    if ((availableCommands?.length ?? 0) > 0) return null;
    if (commandActive) {
        return 'Hardware snapshot or another OEM command is in progress; commands are temporarily locked.';
    }
    return 'No normal OEM commands are available. The current robot runtime has not yet advertised an admitted command capability.';
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
        const localFreshness = localFreshnessMetadata(connection, nowMs);
        if (localFreshness === 'invalid') {
            return {
                label: 'UNKNOWN',
                ready: false,
                tone: 'warning',
                detail: 'Freshness metadata is missing or malformed.',
            };
        }
        if (localFreshness === 'expired') {
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
    if (connection.hardware_fresh === false || connection.hardware_stale) {
        return {
            label: 'RUNTIME READY',
            ready: false,
            tone: 'warning',
            detail: 'Runtime is reachable and ready. Hardware evidence is stale; Automatic inline evidence refresh is pending or retrying, and hardware-dependent writes remain disabled until fresh evidence arrives.',
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
