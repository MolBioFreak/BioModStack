export type BioXpInterlinkStatusState = 'quiet' | 'saved' | 'linked' | 'unreachable' | 'unverified' | 'stale';

export interface BioXpInterlinkStatusInput {
    active?: boolean | null;
    configured?: boolean | null;
    reachable?: boolean | null;
    lastProbeAt?: string | null;
    runtimeNote?: string | null;
    nowMs?: number;
    freshProbeWindowMs?: number;
}

export interface BioXpInterlinkMenuStatus {
    state: BioXpInterlinkStatusState;
    statusLabel: string;
    humanStatusLabel: string;
    reachabilityText: string;
    indicatorClass: string;
    isRobotReachabilityProven: boolean;
}

export const BIOXP_INTERLINK_FRESH_PROBE_WINDOW_MS = 60_000;

const parseProbeTimestamp = (value?: string | null): number | null => {
    if (!value) return null;
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : null;
};

export const isFreshBioXpProbe = ({
    lastProbeAt,
    nowMs = Date.now(),
    freshProbeWindowMs = BIOXP_INTERLINK_FRESH_PROBE_WINDOW_MS,
}: {
    lastProbeAt?: string | null;
    nowMs?: number;
    freshProbeWindowMs?: number;
}) => {
    const probeMs = parseProbeTimestamp(lastProbeAt);
    if (probeMs == null) return false;
    return nowMs - probeMs >= 0 && nowMs - probeMs <= freshProbeWindowMs;
};

const INTERLINK_INDICATOR_CLASS: Record<BioXpInterlinkStatusState, string> = {
    quiet: 'bg-slate-600',
    saved: 'bg-sky-400',
    linked: 'bg-emerald-400',
    unreachable: 'bg-red-500',
    unverified: 'bg-amber-400',
    stale: 'bg-amber-400',
};

export const deriveBioXpInterlinkMenuStatus = ({
    active,
    configured,
    reachable,
    lastProbeAt,
    nowMs,
    freshProbeWindowMs,
}: BioXpInterlinkStatusInput): BioXpInterlinkMenuStatus => {
    const activeLink = Boolean(active);
    const savedProfile = Boolean(configured);
    const freshProbe = isFreshBioXpProbe({ lastProbeAt, nowMs, freshProbeWindowMs });

    let state: BioXpInterlinkStatusState;
    if (!activeLink) {
        state = savedProfile ? 'saved' : 'quiet';
    } else if (reachable === false) {
        state = 'unreachable';
    } else if (reachable === true && freshProbe) {
        state = 'linked';
    } else if (reachable === true) {
        state = 'stale';
    } else {
        state = 'unverified';
    }

    const statusLabelByState: Record<BioXpInterlinkStatusState, string> = {
        quiet: 'QUIET',
        saved: 'SAVED',
        linked: 'LINKED',
        unreachable: 'UNREACHABLE',
        unverified: 'UNVERIFIED',
        stale: 'STALE',
    };
    const humanStatusByState: Record<BioXpInterlinkStatusState, string> = {
        quiet: 'Not configured',
        saved: 'Saved, inactive',
        linked: 'Robot API reachable',
        unreachable: 'Robot API unreachable',
        unverified: 'Active, not yet verified',
        stale: 'Last robot probe is stale',
    };
    const reachabilityByState: Record<BioXpInterlinkStatusState, string> = {
        quiet: 'no robot profile saved',
        saved: 'profile saved; no active robot polling',
        linked: 'fresh robot API probe succeeded',
        unreachable: 'robot API probe failed or timed out; hardware state unknown',
        unverified: 'no successful fresh robot API probe; hardware state unknown',
        stale: 'previous robot API success is stale; refresh diagnostics before trusting hardware state',
    };

    return {
        state,
        statusLabel: statusLabelByState[state],
        humanStatusLabel: humanStatusByState[state],
        reachabilityText: reachabilityByState[state],
        indicatorClass: INTERLINK_INDICATOR_CLASS[state],
        isRobotReachabilityProven: state === 'linked',
    };
};
