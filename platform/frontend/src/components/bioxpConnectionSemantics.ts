import type { RuntimeStatus } from '../lib/bioxpClient.js';

export type BioXpRuntimeSummaryState = 'checking' | 'unconfigured' | 'reachable' | 'unreachable';

export interface BioXpRuntimeSummary {
    state: BioXpRuntimeSummaryState;
    label: string;
    badgeClassName: string;
    detail: string | null;
    linkageConfigured: boolean;
    linkedRuntimeReachable: boolean;
    hardwareConnected: boolean;
    adminControlAvailable: boolean;
    adminLabel: string;
    adminDetail: string;
}

const RUNTIME_BADGE_CLASS: Record<BioXpRuntimeSummaryState, string> = {
    checking: 'bg-warning/10 text-warning border-warning/30',
    unconfigured: 'bg-warning/10 text-warning border-warning/30',
    reachable: 'bg-success/10 text-success border-success/30',
    unreachable: 'bg-error/10 text-error border-error/30',
};

const ROBOT_LOCAL_ADMIN_DETAIL = 'Robot-local maintenance owns the bioxp.api service. BMS links to and proxies the runtime but does not start or stop it.';

export const deriveRuntimeStatusSummary = ({
    linkageConfigured,
    runtimeLoading,
    runtimeStatus,
}: {
    linkageConfigured: boolean;
    runtimeLoading: boolean;
    runtimeStatus?: Partial<RuntimeStatus> | null;
}): BioXpRuntimeSummary => {
    const effectiveLinkageConfigured = Boolean(runtimeStatus?.linkage_configured ?? linkageConfigured);
    const linkedRuntimeReachable = Boolean(runtimeStatus?.linked_runtime_reachable);
    const hardwareConnected = Boolean(runtimeStatus?.hardware_connected);
    const adminControlAvailable = Boolean(runtimeStatus?.admin_control_available);
    const maintenanceMode = String(runtimeStatus?.maintenance_mode ?? 'robot-local');
    const defaultDetail =
        !effectiveLinkageConfigured
            ? 'Connect BMS to the robot-local runtime URL first.'
            : linkedRuntimeReachable
                ? (hardwareConnected
                    ? 'Linked BioXP runtime responded to /status and reported hardware connectivity.'
                    : 'Linked BioXP runtime responded to /status, but hardware is not yet connected.')
                : 'Linked BioXP runtime is not reachable from BMS.';

    const state: BioXpRuntimeSummaryState =
        runtimeLoading && !runtimeStatus
            ? 'checking'
            : !effectiveLinkageConfigured
                ? 'unconfigured'
                : linkedRuntimeReachable
                    ? 'reachable'
                    : 'unreachable';

    const label =
        state === 'checking'
            ? 'CHECKING...'
            : state === 'unconfigured'
                ? 'NOT CONFIGURED'
                : state === 'reachable'
                    ? 'REACHABLE'
                    : 'UNREACHABLE';

    return {
        state,
        label,
        badgeClassName: RUNTIME_BADGE_CLASS[state],
        detail: String(runtimeStatus?.detail ?? defaultDetail),
        linkageConfigured: effectiveLinkageConfigured,
        linkedRuntimeReachable,
        hardwareConnected,
        adminControlAvailable,
        adminLabel: adminControlAvailable ? 'AVAILABLE' : maintenanceMode.toUpperCase(),
        adminDetail: adminControlAvailable
            ? String(runtimeStatus?.detail ?? 'Runtime maintenance control is available through the linked robot runtime.')
            : ROBOT_LOCAL_ADMIN_DETAIL,
    };
};
