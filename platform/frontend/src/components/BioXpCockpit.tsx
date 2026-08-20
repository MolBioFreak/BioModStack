import { useEffect, useMemo, useState } from 'react';

import {
    bioXpErrorText,
    bioXpReceiptIsNonTerminal,
    useBioXpStatus,
    useConnectBioXp,
    useDisconnectBioXp,
    useUpdateBioXpFreshness,

    useBioXpOperatorActionHistory,
    useBioXpOperatorControlCatalog,
    useBioXpOperatorDashboard,
    useInvokeBioXpOperatorAction,
    useRecoverBioXpMotion,
    type BioXpOperatorDashboardXAxis,
} from '../lib/bioxpClient';
import { bioXpReceiptTimestampText } from '../lib/bioxpReceiptTimestamp';
import { BioXpCameraPanel } from './BioXpCameraPanel';
import { BioXpOperatorControlTabs } from './BioXpOperatorControlTabs';
import { BioXpPipetteControlPanel } from './BioXpPipetteControlPanel';
import { BioXpQuickDashboard } from './BioXpQuickDashboard';


type Axis = 'x' | 'y' | 'z' | 'g' | 'door';
type Operation =
    | 'move-negative'
    | 'move-positive'
    | 'home'
    | 'commission-home'
    | 'close'
    | 'open'
    | 'open-wide';

interface Control {
    label: string;
    operation: Operation;
}

interface AxisControls {
    axis: Axis;
    label: string;
    controls: readonly Control[];
}

const AXES: readonly AxisControls[] = [
    {
        axis: 'x',
        label: 'X Axis',
        controls: [
            { label: 'Move −', operation: 'move-negative' },
            { label: 'Home', operation: 'home' },
            { label: 'Move +', operation: 'move-positive' },
        ],
    },
    {
        axis: 'y',
        label: 'Y Axis',
        controls: [
            { label: 'Move −', operation: 'move-negative' },
            { label: 'Home', operation: 'home' },
            { label: 'Move +', operation: 'move-positive' },
        ],
    },
    {
        axis: 'z',
        label: 'Z Axis',
        controls: [
            { label: 'Move −', operation: 'move-negative' },
            { label: 'OEM Z Home', operation: 'home' },
            { label: 'Move +', operation: 'move-positive' },
        ],
    },
    {
        axis: 'g',
        label: 'Gripper',
        controls: [
            { label: 'Move −', operation: 'move-negative' },
            { label: 'Home', operation: 'commission-home' },
            { label: 'Move +', operation: 'move-positive' },
            { label: 'Open', operation: 'open' },
            { label: 'Close', operation: 'close' },
            { label: 'Open Wide', operation: 'open-wide' },
        ],
    },
    {
        axis: 'door',
        label: 'Thermal Door',
        controls: [
            { label: 'Home', operation: 'home' },
            { label: 'Open', operation: 'open' },
            { label: 'Close', operation: 'close' },
        ],
    },
];

const actionClass = 'rounded bg-cyan-700 px-3 py-2 text-sm font-semibold hover:bg-cyan-600 disabled:cursor-not-allowed disabled:opacity-35';

export function BioXpCockpit() {
    const statusQuery = useBioXpStatus(true);
    const status = statusQuery.isError ? undefined : statusQuery.data;
    const connection = status?.connection;
    const active = connection?.active === true;
    const linkConnected = active && connection?.reachable !== false;
    const configured = connection?.configured === true;
    const generation = connection?.generation ?? 0;
    const [historyLimit, setHistoryLimit] = useState<8 | 25 | 50 | 100>(25);
    const dashboardQuery = useBioXpOperatorDashboard(generation, linkConnected);
    const historyQuery = useBioXpOperatorActionHistory(generation, linkConnected, historyLimit);
    const connect = useConnectBioXp();
    const disconnect = useDisconnectBioXp();
    const operatorCatalog = useBioXpOperatorControlCatalog(
        generation,
        linkConnected,
        dashboardQuery.data?.x_axis?.provider?.lifecycle?.state ?? dashboardQuery.data?.x_axis?.provider?.state ?? null,
    );
    const invokeOperatorAction = useInvokeBioXpOperatorAction();
    const emergencyAction = useInvokeBioXpOperatorAction();

    const recoverMotion = useRecoverBioXpMotion();
    const updateFreshness = useUpdateBioXpFreshness();
    const resetInvokeOperatorAction = invokeOperatorAction.reset;
    const resetEmergencyAction = emergencyAction.reset;
    const resetRecoverMotion = recoverMotion.reset;
    const [manualSteps, setManualSteps] = useState<Record<'x' | 'y' | 'z' | 'g', number>>({
        x: 10000,
        y: 10000,
        z: 10000,
        g: 10000,
    });
    const [absoluteTargets, setAbsoluteTargets] = useState<Record<'x' | 'y' | 'z' | 'g', number>>({
        x: 60,
        y: 0,
        z: 65000,
        g: 0,
    });
    const [freshnessMinutes, setFreshnessMinutes] = useState('30');
    const [freshnessDisabled, setFreshnessDisabled] = useState(false);
    const catalog = !linkConnected || operatorCatalog.isError ? undefined : operatorCatalog.data;
    const dashboard = !linkConnected || dashboardQuery.isError ? undefined : dashboardQuery.data;
    const ownershipGeneration = catalog?.ownership_generation ?? 0;
    useEffect(() => {
        const budget = connection?.freshness_budget_seconds;
        if (budget === undefined) return;
        setFreshnessDisabled(budget === null);
        if (budget !== null) setFreshnessMinutes(String(Number((budget / 60).toFixed(2))));
    }, [connection?.freshness_budget_seconds]);
    const saveFreshness = () => {
        if (freshnessDisabled) {
            updateFreshness.mutate(null);
            return;
        }
        const minutes = Number(freshnessMinutes);
        if (Number.isFinite(minutes) && minutes > 0) updateFreshness.mutate(minutes * 60);
    };
    const freshnessSummary = connection?.freshness_budget_seconds == null
        ? 'Disabled — no BMS observation-age expiry'
        : `${Math.round(connection.freshness_budget_seconds / 60)} minutes`;

    const ownership = connection?.ownership;
    const maintenance = connection?.maintenance_state;
    const ownershipLabel = ownership
        ? `${ownership.transport ?? 'unknown'} / ${ownership.usb ?? 'unknown'} / ${ownership.router ?? 'unknown'}`
        : 'Unavailable';
    const dashboardMotion = dashboard?.motion;
    const motionLabel = dashboardMotion
        ? dashboardMotion.enabled ? 'Enabled — Z provider ready; each command verifies live controller state' : `Blocked${dashboardMotion.reason ? ` — ${dashboardMotion.reason}` : ''}`
        : 'Unavailable';
    const recentCommands = useMemo(
        () => (!linkConnected || historyQuery.isError ? [] : (historyQuery.data?.receipts ?? [])).slice(0, historyLimit),
        [historyQuery.data?.receipts, historyQuery.isError, linkConnected, historyLimit],
    );
    useEffect(() => {
        resetInvokeOperatorAction();
        resetEmergencyAction();
        resetRecoverMotion();
    }, [generation, linkConnected, resetEmergencyAction, resetInvokeOperatorAction, resetRecoverMotion]);
    const busy = invokeOperatorAction.isPending || emergencyAction.isPending || recoverMotion.isPending || updateFreshness.isPending;
    const connectedLabel = active
        ? connection?.reachable === false ? 'Connection error' : 'Connected'
        : 'Disconnected';

    const operatorActionForPath = (path: string) => (catalog?.actions ?? []).find(
        (action) => action.kind === 'primitive' && action.informational_path === path,
    );

    const operatorActionById = (actionId: string) => (catalog?.actions ?? []).find(
        (action) => action.action_id === actionId,
    );
    const actionUnavailableReason = (actionId: string, fallback: string) => {
        const action = operatorActionById(actionId);
        return action?.disabled_reason
            ?? action?.provider_unavailable_reason
            ?? action?.unavailable_reason
            ?? fallback;
    };
    const xAbsoluteAction = operatorActionById('oem.x.move_absolute');
    const xAbsoluteInput = xAbsoluteAction?.inputs.find((input) => input.name === 'position_steps');
    const xAbsoluteMinimum = Math.max(60, typeof xAbsoluteInput?.minimum === 'number' ? xAbsoluteInput.minimum : 0);
    const xAbsoluteMaximum = Math.min(90263, typeof xAbsoluteInput?.maximum === 'number' ? xAbsoluteInput.maximum : 90263);
    const xRelativeLimitMargin = 20;
    const xRelativeMaximum = 90263 - xRelativeLimitMargin;

    const xNegativeInputs = useMemo(() => ({ steps: -Math.abs(manualSteps.x) }), [manualSteps.x]);
    const xPositiveInputs = useMemo(() => ({ steps: Math.abs(manualSteps.x) }), [manualSteps.x]);
    const xAbsoluteInputs = useMemo(() => ({ position_steps: absoluteTargets.x }), [absoluteTargets.x]);
    const xHomeInputs = useMemo(() => ({}), []);
    const xRelativeMagnitudeInRange = Number.isInteger(manualSteps.x)
        && Math.abs(manualSteps.x) >= 1
        && Math.abs(manualSteps.x) <= xRelativeMaximum;
    const xAbsoluteTargetInRange = Number.isInteger(absoluteTargets.x)
        && absoluteTargets.x >= xAbsoluteMinimum
        && absoluteTargets.x <= xAbsoluteMaximum;
    const zAbsoluteAction = operatorActionById('oem.z.move_absolute');
    const zAbsoluteInput = zAbsoluteAction?.inputs.find((input) => input.name === 'position_steps');
    const zAbsoluteMinimum = typeof zAbsoluteInput?.minimum === 'number' ? zAbsoluteInput.minimum : 0;
    const zAbsoluteMaximum = typeof zAbsoluteInput?.maximum === 'number' ? zAbsoluteInput.maximum : 160000;
    const zAbsoluteStaticBlocker = zAbsoluteAction?.dependencies.find(
        (dependency) => dependency.key !== 'z_target_oem_envelope' && dependency.met !== true,
    );
    const zAbsoluteTargetInRange = Number.isInteger(absoluteTargets.z)
        && absoluteTargets.z >= zAbsoluteMinimum
        && absoluteTargets.z <= zAbsoluteMaximum;
    const zAbsoluteDisabledReason = !zAbsoluteAction
        ? 'Robot action unavailable.'
        : zAbsoluteAction.provider_available !== true
            ? zAbsoluteAction.provider_unavailable_reason ?? 'Robot action unavailable.'
            : zAbsoluteStaticBlocker
                ? zAbsoluteStaticBlocker.reason ?? zAbsoluteAction.disabled_reason ?? 'Robot action unavailable.'
                : !zAbsoluteTargetInRange
                    ? `Requested Z target must be an integer from ${zAbsoluteMinimum} through ${zAbsoluteMaximum}.`
                    : null;
    const zAbsoluteEnabled = zAbsoluteDisabledReason === null;
    const xAxisDashboard: BioXpOperatorDashboardXAxis | undefined = dashboard?.x_axis;
    const xStatus = xAxisDashboard?.status;
    const xProvider = xAxisDashboard?.provider;
    const xLiveStatus = xProvider?.live_status;
    const xPosition = xStatus?.position_steps ?? xLiveStatus?.position_steps ?? 'unknown';
    const xReference = xStatus?.reference ?? xProvider?.lifecycle?.reference_state ?? xProvider?.reference_state ?? 'unknown';
    const xLifecycle = xProvider?.lifecycle?.state ?? xProvider?.state ?? 'unknown';
    const xAuthority = xProvider?.authority ?? xAxisDashboard?.authority ?? 'unknown';
    const xLeftSwitchState = xStatus?.left_switch_state ?? xLiveStatus?.left_switch_state ?? 'unknown';
    const xRightSwitchState = xStatus?.right_switch_state ?? xLiveStatus?.right_switch_state ?? 'unknown';
    const xLeftSwitchDisabled = xStatus?.left_switch_disabled ?? xLiveStatus?.left_switch_disabled ?? 'unknown';
    const xRightSwitchDisabled = xStatus?.right_switch_disabled ?? xLiveStatus?.right_switch_disabled ?? 'unknown';
    const xProfileVerified = xProvider?.profile?.verified ?? xLiveStatus?.profile_verified;
    const xSwitchMasksVerified = xProvider?.switch_masks?.verified ?? xLiveStatus?.switch_mask_verified;
    const xMaxSpeed = xLiveStatus?.max_speed ?? 'unknown';
    const xMaxAcceleration = xLiveStatus?.max_acceleration ?? 'unknown';
    const xMaxCurrent = xLiveStatus?.max_current ?? 'unknown';
    const xStallGuard = xLiveStatus?.stall_guard ?? 'unknown';
    const xGeneration = xProvider?.current_generation ?? 'unknown';
    const xBoardGeneration = xProvider?.current_board_lifecycle_generation ?? 'unknown';
    const xBoardGenerationFresh = xProvider?.board_generation_fresh;
    const xLastFailure = xAxisDashboard?.last_failure ?? xProvider?.lifecycle?.last_failure;
    const xHistoryReceipt = historyQuery.data?.receipts?.[0]?.action_id?.startsWith('oem.x.')
        ? historyQuery.data.receipts[0]
        : null;
    const xReceipt = xHistoryReceipt
        ?? xAxisDashboard?.latest_receipt
        ?? xProvider?.lifecycle?.latest_receipt
        ?? null;
    const xReceiptActive = bioXpReceiptIsNonTerminal(xReceipt);
    const xQueue = dashboard?.successive_move_queue?.x ?? null;
    const xQueueFull = (xQueue?.depth ?? 0) >= 8;
    const xMotionGateReason = (allowSuccessive = false): string | null => {
        if (!linkConnected) return 'BioXP link is not connected.';
        if (dashboardMotion && dashboardMotion.enabled === false) {
            return dashboardMotion.reason ?? 'Robot motion is blocked.';
        }
        if (xQueueFull) return 'X successive-move queue is full (8 queued moves). Wait for the active move or use Stop to clear the queue.';
        if (xReceiptActive && !allowSuccessive) return `X command ${typeof xReceipt?.action_id === 'string' ? xReceipt.action_id : 'in progress'} is ${String(xReceipt?.status)}.`;
        return null;
    };
    const xLifecycleBlocksMoves = (xLifecycle !== 'prepared_unreferenced' && xLifecycle !== 'referenced_ready')
        ? `Current X lifecycle state '${xLifecycle}'; expected 'prepared_unreferenced' or 'referenced_ready'.`
        : null;
    const xActionStaticBlocker = (actionId: string): string | null => {
        const action = operatorActionById(actionId);
        if (!action) return 'Robot action unavailable.';
        if (action.provider_available !== true) return action.provider_unavailable_reason ?? 'Robot action unavailable.';
        const blocker = (action.dependencies ?? []).find((dependency) =>
            dependency.key !== 'x_relative_oem_envelope'
            && dependency.key !== 'x_target_oem_envelope'
            && dependency.met !== true,
        );
        if (blocker) return blocker.reason ?? action.disabled_reason ?? 'Robot action unavailable.';
        return null;
    };
    const xNegativeDisabledReason = !xRelativeMagnitudeInRange
        ? `Requested X relative magnitude must be an integer from 1 through ${xRelativeMaximum}.`
        : xMotionGateReason(true) ?? xLifecycleBlocksMoves ?? xActionStaticBlocker('oem.x.move_steps');
    const xPositiveDisabledReason = !xRelativeMagnitudeInRange
        ? `Requested X relative magnitude must be an integer from 1 through ${xRelativeMaximum}.`
        : xMotionGateReason(true) ?? xLifecycleBlocksMoves ?? xActionStaticBlocker('oem.x.move_steps');
    const xAbsoluteDisabledReason = !xAbsoluteTargetInRange
        ? `Requested X target must be an integer from ${xAbsoluteMinimum} through ${xAbsoluteMaximum}.`
        : xMotionGateReason(true) ?? xLifecycleBlocksMoves ?? xActionStaticBlocker('oem.x.move_absolute');
    const xHomeDisabledReason = xMotionGateReason() ?? xActionStaticBlocker('oem.x.manual_panel_home');
    const xNegativeEnabled = xNegativeDisabledReason === null;
    const xPositiveEnabled = xPositiveDisabledReason === null;
    const xAbsoluteEnabled = xAbsoluteDisabledReason === null;
    const xHomeEnabled = xHomeDisabledReason === null;

    const invokeAction = (
        actionId: string,
        inputs: Record<string, unknown>,
        mutation = invokeOperatorAction,
    ) => {
        mutation.mutate({ actionId, connectionGeneration: generation, ownershipGeneration, inputs });
    };

    const claimTransport = () => invokeAction('meta.activate_motion', {});

    const recoverMotionNonHoming = () => recoverMotion.mutate({
        generation,
        reason: 'BMS operator requested exact non-homing recovery',
    });

    const operatorPathForControl = (axis: Axis, operation: Operation): string | null => {
        if (operation === 'move-negative' || operation === 'move-positive') return '/motion/oem/manual/relative';
        if (operation === 'home' || operation === 'commission-home') return '/motion/oem/manual/home';
        if (axis === 'g') {
            return ({ open: '/motion/gripper/open', close: '/motion/gripper/close', 'open-wide': '/motion/gripper/open_wide' } as const)[operation as 'open' | 'close' | 'open-wide'] ?? null;
        }
        if (axis === 'door') {
            return ({ open: '/motion/thermal_door/open', close: '/motion/thermal_door/close' } as const)[operation as 'open' | 'close'] ?? null;
        }
        return null;
    };

    const invokeOperatorPath = (path: string, inputs: Record<string, unknown>) => {
        const action = operatorActionForPath(path);
        if (!action) return;
        invokeAction(action.action_id, inputs);
    };

    const runControl = (axis: Axis, operation: Operation) => {
        if (axis === 'x') {
            if (operation === 'move-negative') {
                if (!xNegativeEnabled) return;
                invokeAction('oem.x.move_steps', xNegativeInputs);
            } else if (operation === 'move-positive') {
                if (!xPositiveEnabled) return;
                invokeAction('oem.x.move_steps', xPositiveInputs);
            } else if (operation === 'home' || operation === 'commission-home') {
                if (!xHomeEnabled) return;
                invokeAction('oem.x.manual_panel_home', xHomeInputs);
            }
            return;
        }
        if (axis === 'z') {
            if (operation === 'move-negative') {
                invokeAction('oem.z.move_steps', { steps: -Math.abs(manualSteps.z) });
            } else if (operation === 'move-positive') {
                invokeAction('oem.z.move_steps', { steps: Math.abs(manualSteps.z) });
            } else if (operation === 'home' || operation === 'commission-home') {
                invokeAction('oem.z.manual_home', {});
            }
            return;
        }
        if (operation === 'move-negative' || operation === 'move-positive') {
            if (axis === 'door') return;
            const magnitude = Math.abs(manualSteps[axis]);
            invokeOperatorPath('/motion/oem/manual/relative', {
                axis,
                steps: operation === 'move-negative' ? -magnitude : magnitude,
            });
            return;
        }
        if (operation === 'home' || operation === 'commission-home') {
            invokeOperatorPath('/motion/oem/manual/home', { axis });
            return;
        }
        const path = axis === 'g'
            ? ({ open: '/motion/gripper/open', close: '/motion/gripper/close', 'open-wide': '/motion/gripper/open_wide' } as const)[operation as 'open' | 'close' | 'open-wide']
            : ({ open: '/motion/thermal_door/open', close: '/motion/thermal_door/close' } as const)[operation as 'open' | 'close'];
        if (path) invokeOperatorPath(path, {});
    };

    const runAbsolute = (axis: 'x' | 'y' | 'z' | 'g') => {
        if (axis === 'x') {
            if (!xAbsoluteEnabled) return;
            invokeAction('oem.x.move_absolute', xAbsoluteInputs);
            return;
        }
        if (axis === 'z') {
            invokeAction('oem.z.move_absolute', { position_steps: absoluteTargets.z });
            return;
        }
        invokeOperatorPath('/motion/oem/manual/absolute', { axis, position_steps: absoluteTargets[axis] });
    };

    const stopAxis = (axis: Axis) => axis === 'x'
        ? invokeAction('oem.x.stop', {}, emergencyAction)
        : axis === 'z'
            ? invokeAction('oem.z.stop', {}, emergencyAction)
            : invokeOperatorPath('/motion/diagnostics/stop', { axis });

    const abortXAggregate = () => invokeAction('oem.abort_all', {}, emergencyAction);
    const abortZ = () => invokeAction('oem.z.abort', {}, emergencyAction);

    const error = invokeOperatorAction.error ?? recoverMotion.error ?? updateFreshness.error ?? emergencyAction.error ?? connect.error ?? disconnect.error;

    return (
        <div className="space-y-4 p-4 text-slate-100 md:p-6">
            <header>
                <h1 className="text-2xl font-bold">BioXP 3200</h1>
                <p className="mt-1 text-sm text-slate-400">Direct OEM operator controls</p>
            </header>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold">Connection & Robot State</h2>
                        <p className={`text-sm ${active ? 'text-emerald-300' : 'text-slate-400'}`}>
                            {connectedLabel}
                        </p>
                        {connection?.last_error && <p className="mt-1 break-words text-sm text-red-300">{connection.last_error}</p>}
                    </div>
                    <div className="flex gap-2">
                        <button
                            type="button"
                            disabled={!configured || linkConnected || connect.isPending || disconnect.isPending}
                            onClick={() => connect.mutate(undefined)}
                            className={actionClass}
                        >{linkConnected ? 'BMS Link Connected' : active ? 'Reconnect BMS Link' : 'Connect BMS Link'}</button>
                        <button
                            type="button"
                            disabled={!active || connect.isPending || disconnect.isPending}
                            onClick={() => disconnect.mutate(undefined)}
                            className="rounded bg-slate-700 px-3 py-2 text-sm font-semibold disabled:opacity-35"
                        >Disconnect</button>
                    </div>
                </div>
                <dl className="mt-4 grid gap-2 text-sm sm:grid-cols-2 xl:grid-cols-4">
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Transport / USB / Router</dt>
                        <dd className="mt-1 break-words font-mono text-slate-100">{ownershipLabel}</dd>
                    </div>
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Motion</dt>
                        <dd className={`mt-1 break-words ${dashboardMotion?.enabled === false ? 'text-amber-200' : 'text-slate-100'}`}>{motionLabel}</dd>
                    </div>
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Connection generation</dt>
                        <dd className="mt-1 font-mono text-slate-100">{generation || '—'}</dd>
                    </div>
                    <div className="rounded bg-slate-900/70 p-3">
                        <dt className="text-slate-400">Last robot observation</dt>
                        <dd className="mt-1 text-slate-100">{connection?.observed_at ? new Date(connection.observed_at).toLocaleString() : 'Unavailable'}</dd>
                    </div>
                </dl>
            </section>

            <section className="rounded-xl border border-cyan-800/70 bg-cyan-950/20 p-4">
                <h2 className="text-lg font-semibold">Hardware evidence freshness</h2>
                <p className="mt-1 text-sm text-slate-300">
                    BMS observation expiry defaults to 30 minutes. Disable it completely when desired; this does not fabricate a missing snapshot or override robot-owned hardware validity.
                </p>
                <div className="mt-3 flex flex-wrap items-end gap-3">
                    <label className="flex items-center gap-2 text-sm text-slate-200">
                        <input
                            type="checkbox"
                            checked={freshnessDisabled}
                            onChange={(event) => setFreshnessDisabled(event.target.checked)}
                        />
                        Disable BMS age expiry
                    </label>
                    <label className="text-sm text-slate-300">
                        Window (minutes)
                        <input
                            type="number"
                            min={1}
                            step={1}
                            disabled={freshnessDisabled}
                            value={freshnessMinutes}
                            onChange={(event) => setFreshnessMinutes(event.target.value)}
                            className="ml-2 w-28 rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm disabled:opacity-40"
                        />
                    </label>
                    <button
                        type="button"
                        disabled={!configured || !linkConnected || updateFreshness.isPending || (!freshnessDisabled && !(Number(freshnessMinutes) > 0))}
                        onClick={saveFreshness}
                        className={actionClass}
                    >{updateFreshness.isPending ? 'Saving…' : 'Save freshness policy'}</button>
                </div>
                <p className="mt-2 text-sm text-cyan-100">Current policy: {freshnessSummary}</p>
                <p className="mt-1 text-xs text-slate-400">
                    Automatic OEM snapshots: {connection?.automatic_snapshot_refresh?.published === true ? 'last collection published successfully' : 'collector awaiting its next refresh cycle'}.
                </p>
            </section>

            <BioXpQuickDashboard
                connected={linkConnected}
                data={dashboard}
                isLoading={dashboardQuery.isLoading}
                error={dashboardQuery.error}
            />

            <section className="rounded-xl border border-amber-700/60 bg-amber-950/20 p-4">
                <h2 className="text-lg font-semibold">Controller Activation & Recovery</h2>
                <p className="mt-1 text-sm text-slate-400">Robot-owned serial-206 activation and typed non-homing recovery. BMS does not maintain a second command registry or receipt ledger.</p>
                <div className="mt-3 flex flex-wrap gap-3">
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionById('meta.activate_motion')?.enabled !== true || busy}
                        title={operatorActionById('meta.activate_motion')?.disabled_reason ?? 'Robot-owned OEM activation'}
                        onClick={claimTransport}
                        className="rounded bg-amber-700 px-4 py-2 font-semibold hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-35"
                    >Activate 24 V / Prepare Motion</button>
                    <button
                        type="button"
                        disabled={!linkConnected || maintenance?.recovery_required !== true || busy}
                        title={maintenance?.recovery_required === true ? 'Robot-authoritative non-homing recovery' : 'Recovery is not currently required'}
                        onClick={recoverMotionNonHoming}
                        className="rounded bg-amber-700 px-4 py-2 font-semibold hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-35"
                    >Non-homing Recovery</button>
                </div>
                {operatorActionById('meta.activate_motion')?.enabled !== true && (
                    <p className="mt-2 text-sm text-amber-100">
                        Activate: {operatorActionById('meta.activate_motion')?.disabled_reason ?? 'Robot action unavailable.'}
                    </p>
                )}
                {recoverMotion.error && (
                    <p role="alert" className="mt-2 break-words text-sm text-red-300">Non-homing recovery failed: {bioXpErrorText(recoverMotion.error)}</p>
                )}
                {linkConnected && catalog && !historyQuery.isError && recoverMotion.data && (
                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-amber-800 p-2 text-xs text-cyan-200">{JSON.stringify(recoverMotion.data, null, 2)}</pre>
                )}
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <h2 className="text-lg font-semibold">Exact OEM Manual Controls</h2>
                <p className="mt-1 text-sm text-slate-400">Relative moves use the literal OEM <code>moveSteps(axis, steps)</code> route. Home/Open/Close use the axis-specific OEM mechanisms.</p>
                {operatorCatalog.isError && (
                    <p className="mt-1 break-words text-sm text-red-300">Robot manual-control catalog unavailable: {bioXpErrorText(operatorCatalog.error)}</p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionForPath('/motion/oem/machine_config')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/machine_config', {})}
                        className={actionClass}
                    >Show OEM axis/config tables</button>
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionForPath('/motion/oem/position_table')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/position_table', {})}
                        className={actionClass}
                    >Show OEM position table</button>
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                    {AXES.map(({ axis, label, controls }) => (
                        <article key={axis} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
                            <div className="flex items-center justify-between gap-2">
                                <h3 className="font-semibold">{label}</h3>
                                <div className="flex gap-2">
                                    <button
                                        type="button"
                                        disabled={!linkConnected || (axis === 'x'
                                            ? operatorActionById('oem.x.stop')?.enabled !== true
                                            : axis === 'z'
                                                ? operatorActionById('oem.z.stop')?.enabled !== true
                                                : operatorActionForPath('/motion/diagnostics/stop')?.enabled !== true) || ((axis === 'x' || axis === 'z') ? emergencyAction.isPending : invokeOperatorAction.isPending)}
                                        title={axis === 'x'
                                            ? actionUnavailableReason('oem.x.stop', 'Immediate OEM X motor stop')
                                            : axis === 'z'
                                                ? actionUnavailableReason('oem.z.stop', 'Immediate OEM Z motor stop')
                                                : 'Immediate OEM motor stop for this component'}
                                        onClick={() => stopAxis(axis)}
                                        className="rounded bg-red-800 px-3 py-1.5 text-sm font-semibold hover:bg-red-700 disabled:opacity-35"
                                    >Stop</button>
                                    {axis === 'x' && (
                                        <button
                                            type="button"
                                            disabled={!linkConnected || operatorActionById('oem.abort_all')?.enabled !== true || emergencyAction.isPending}
                                            title={actionUnavailableReason('oem.abort_all', 'Aggregate OEM forceAbortMotion across all present motion boards')}
                                            onClick={abortXAggregate}
                                            className="rounded bg-red-950 px-3 py-1.5 text-sm font-semibold text-red-100 ring-1 ring-red-600 hover:bg-red-900 disabled:opacity-35"
                                        >Aggregate Abort (all OEM boards)</button>
                                    )}
                                    {axis === 'z' && (
                                        <button
                                            type="button"
                                            disabled={!linkConnected || operatorActionById('oem.z.abort')?.enabled !== true || emergencyAction.isPending}
                                            title="OEM full-machine forceAbortMotion; invalidates Z reference"
                                            onClick={abortZ}
                                            className="rounded bg-red-950 px-3 py-1.5 text-sm font-semibold text-red-100 ring-1 ring-red-600 hover:bg-red-900 disabled:opacity-35"
                                        >Abort</button>
                                    )}
                                </div>
                            </div>
                            {axis !== 'door' && (
                                <div className="mt-3 grid gap-2">
                                    <label className="block text-xs text-slate-300">
                                        Relative move steps
                                        <input
                                            type="number"
                                            min={1}
                                            max={axis === 'x' ? xRelativeMaximum : 160000}
                                            step={1}
                                            value={manualSteps[axis]}
                                            onChange={(event) => {
                                                const parsed = Number.parseInt(event.target.value || '1', 10);
                                                const boundedMaximum = axis === 'x' ? xRelativeMaximum : 160000;
                                                const bounded = Number.isFinite(parsed) ? Math.max(1, Math.min(boundedMaximum, Math.abs(parsed))) : 1;
                                                setManualSteps((current) => ({ ...current, [axis]: bounded }));
                                            }}
                                            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm"
                                        />
                                    </label>
                                    {axis === 'z' && (
                                        <div className="flex flex-wrap gap-1" aria-label="Z step presets">
                                            {[1000, 5000, 10000, 25000].map((steps) => (
                                                <button
                                                    key={steps}
                                                    type="button"
                                                    onClick={() => setManualSteps((current) => ({ ...current, z: steps }))}
                                                    className={`rounded px-2 py-1 text-xs ${manualSteps.z === steps ? 'bg-cyan-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
                                                >{steps.toLocaleString()}</button>
                                            ))}
                                        </div>
                                    )}
                                    <label className="block text-xs text-slate-300">
                                        OEM absolute target (steps)
                                        <div className="mt-1 flex gap-2">
                                            <input
                                                type="number"
                                                min={axis === 'x' ? xAbsoluteMinimum : undefined}
                                                max={axis === 'x' ? xAbsoluteMaximum : undefined}
                                                step={1}
                                                value={absoluteTargets[axis]}
                                                onChange={(event) => {
                                                    const parsed = Number.parseInt(event.target.value || '0', 10);
                                                    setAbsoluteTargets((current) => ({
                                                        ...current,
                                                        [axis]: Number.isFinite(parsed) ? parsed : 0,
                                                    }));
                                                }}
                                                className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm"
                                            />
                                            <button
                                                type="button"
                                                disabled={!linkConnected || (axis === 'x' ? !xAbsoluteEnabled : axis === 'z' ? !zAbsoluteEnabled : operatorActionForPath('/motion/oem/manual/absolute')?.enabled !== true) || invokeOperatorAction.isPending}
                                                title={axis === 'x' ? xAbsoluteDisabledReason ?? 'Robot-owned exact OEM X absolute move' : axis === 'z' ? zAbsoluteDisabledReason ?? 'Robot-owned exact OEM absolute move' : undefined}
                                                onClick={() => runAbsolute(axis)}
                                                className={actionClass}
                                            >Go absolute</button>
                                        </div>
                                        </label>
                                        {axis === 'x' && (
                                        <div className="rounded border border-sky-800/70 bg-sky-950/20 p-3 text-xs text-sky-100">
                                            <h4 className="font-semibold text-sky-50">X OEM authority</h4>
                                            <p className="mt-1"><strong>Position:</strong> {xPosition} · <strong>Software reference state (not physical proof):</strong> {xReference}</p>
                                            <p className="mt-1"><strong>Lifecycle:</strong> {xLifecycle} · <strong>Authority:</strong> {xAuthority}</p>
                                            <p className="mt-1"><strong>GAP9/10:</strong> {xLeftSwitchState} / {xRightSwitchState} · <strong>GAP13/12 disabled:</strong> {String(xLeftSwitchDisabled)} / {String(xRightSwitchDisabled)}</p>
                                            <p className="mt-1"><strong>Configured GAP4/5/6/205:</strong> {xMaxSpeed} / {xMaxAcceleration} / {xMaxCurrent} / {xStallGuard}</p>
                                            <p className="mt-1"><strong>Source range:</strong> 0..90263 · <strong>Effective absolute minimum:</strong> 60 · <strong>Relative moves:</strong> 20-step inner margin</p>
                                            <p className="mt-1"><strong>Connection generation:</strong> {xGeneration} · <strong>Board lifecycle generation:</strong> {xBoardGeneration} · <strong>Fresh:</strong> {xBoardGenerationFresh === true ? 'yes' : xBoardGenerationFresh === false ? 'no' : 'unknown'}</p>
                                            <p className="mt-1"><strong>Serial-206 D1 adaptation:</strong> GAP12 right switch disabled and GAP13 left switch enabled. Masks {xSwitchMasksVerified === true ? 'verified' : xSwitchMasksVerified === false ? 'not verified' : 'unknown'}; profile {xProfileVerified === true ? 'verified' : xProfileVerified === false ? 'not verified' : 'unknown'}.</p>
                                            <p className="mt-1 text-sky-200/80">Controller/software reference is reported exactly as published by the robot provider; it is not independent evidence of the physical X location.</p>
                                            <div className="mt-2 flex flex-wrap gap-2">
                                                <button type="button" className="rounded bg-red-800 px-3 py-2 text-sm font-semibold hover:bg-red-700 disabled:opacity-35" disabled={!linkConnected || operatorActionById('oem.x.stop')?.enabled !== true || emergencyAction.isPending} title={actionUnavailableReason('oem.x.stop', 'Immediate OEM X stop unavailable.')} onClick={() => stopAxis('x')}>Stop X</button>
                                                <button type="button" className="rounded bg-red-950 px-3 py-2 text-sm font-semibold text-red-100 ring-1 ring-red-600 hover:bg-red-900 disabled:opacity-35" disabled={!linkConnected || operatorActionById('oem.abort_all')?.enabled !== true || emergencyAction.isPending} title={actionUnavailableReason('oem.abort_all', 'Aggregate OEM abort unavailable.')} onClick={abortXAggregate}>Aggregate Abort (all OEM boards)</button>
                                            </div>
                                            {xLastFailure != null && <details className="mt-2"><summary className="cursor-pointer text-red-200">Last X failure</summary><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-red-200">{JSON.stringify(xLastFailure, null, 2)}</pre></details>}
                                            {xReceipt != null && <details className="mt-2"><summary className="cursor-pointer">Latest X authority receipt</summary><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-sky-200/80">{JSON.stringify(xReceipt, null, 2)}</pre></details>}
                                        </div>
                                        )}
                                        {axis === 'z' && (
                                        <div className="rounded border border-cyan-800/70 bg-cyan-950/20 p-3 text-xs text-cyan-100">
                                            <p><strong>Dynamic OEM pseudo-home floor:</strong> OEM moveZ applies the robot-owned PSUDO_Z_HOME as a dynamic minimum target. A request below the current value is replaced with that value before dispatch. Z does not automatically return to pseudo-home after every movement.</p>
                                            <p className="mt-1"><strong>Clear and Home:</strong> Z Clear returns to the selected pseudo-home. Manual Home follows the OEM homing sequence and establishes controller coordinate 0.</p>
                                            <p className="mt-1"><strong>Position:</strong> {dashboard?.z_axis.status?.position_steps ?? 'unknown'} · <strong>Reference:</strong> {dashboard?.z_axis.status?.reference ?? 'unknown'} · <strong>Authority state:</strong> {dashboard?.z_axis.provider.state ?? 'unknown'}</p>
                                            <p className="mt-1"><strong>GAP9/10:</strong> {dashboard?.z_axis.status?.left_switch_state ?? 'unknown'} / {dashboard?.z_axis.status?.right_switch_state ?? 'unknown'} · <strong>Disable GAP13/12:</strong> {String(dashboard?.z_axis.status?.left_switch_disabled ?? 'unknown')} / {String(dashboard?.z_axis.status?.right_switch_disabled ?? 'unknown')}</p>
                                            <div className="mt-2 flex flex-wrap gap-2">
                                                <button
                                                    type="button"
                                                    className={actionClass}
                                                    disabled={!linkConnected || operatorActionById('oem.z.clear')?.enabled !== true || busy}
                                                    title={operatorActionById('oem.z.clear')?.disabled_reason ?? 'Move to the robot-owned clear position selected from tip and gantry state'}
                                                    onClick={() => invokeAction('oem.z.clear', {})}
                                                >Z Clear (automatic OEM position)</button>
                                            </div>
                                            {dashboard?.z_axis.last_failure != null && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-red-200">{JSON.stringify(dashboard.z_axis.last_failure, null, 2)}</pre>}
                                        </div>
                                    )}
                                </div>
                            )}
                            {Object.entries(dashboard?.successive_move_queue ?? {})
                                .filter(([, queue]) => queue.state !== 'idle' || queue.depth > 0)
                                .length > 0 && (
                                <div className="mt-2 rounded border border-amber-800/60 bg-amber-950/20 p-2 text-xs text-amber-100" data-testid="successive-move-queue">
                                    <p className="font-semibold">Robot successive-move queue</p>
                                    {Object.entries(dashboard?.successive_move_queue ?? {})
                                        .filter(([, queue]) => queue.state !== 'idle' || queue.depth > 0)
                                        .map(([queueAxis, queue]) => (
                                            <p key={queueAxis} className="mt-1">
                                                <strong>{queueAxis.toUpperCase()}:</strong> {queue.state} · {queue.depth} queued{queue.head_action_id ? ` · head ${queue.head_action_id}` : ''}{queue.active_command_id ? ` · active ${queue.active_command_id}` : ''}
                                            </p>
                                        ))}
                                </div>
                            )}
                            <div className="mt-3 flex flex-wrap gap-2">
                                {controls.map(({ label: controlLabel, operation }) => {
                                    const path = operatorPathForControl(axis, operation);
                                    const xActionId = axis === 'x'
                                        ? operation === 'home' || operation === 'commission-home'
                                            ? 'oem.x.manual_panel_home'
                                            : operation === 'move-negative' || operation === 'move-positive'
                                                ? 'oem.x.move_steps'
                                                : null
                                        : null;
                                    const zActionId = axis === 'z'
                                        ? operation === 'home' || operation === 'commission-home'
                                            ? 'oem.z.manual_home'
                                            : operation === 'move-negative' || operation === 'move-positive'
                                                ? 'oem.z.move_steps'
                                                : null
                                        : null;
                                    const action = xActionId
                                        ? operatorActionById(xActionId)
                                        : zActionId
                                            ? operatorActionById(zActionId)
                                            : path
                                                ? operatorActionForPath(path)
                                                : null;
                                    const isXNegative = xActionId === 'oem.x.move_steps' && operation === 'move-negative';
                                    const isXPositive = xActionId === 'oem.x.move_steps' && operation === 'move-positive';
                                    const isXHome = xActionId === 'oem.x.manual_panel_home';
                                    const admissionEnabled = isXNegative ? xNegativeEnabled : isXPositive ? xPositiveEnabled : isXHome ? xHomeEnabled : action?.enabled === true;
                                    const enabled = admissionEnabled;
                                    const unavailableReason = isXNegative
                                        ? xNegativeDisabledReason ?? 'Robot verifies this exact X move at dispatch.'
                                        : isXPositive
                                            ? xPositiveDisabledReason ?? 'Robot verifies this exact X move at dispatch.'
                                            : isXHome
                                                ? xHomeDisabledReason ?? 'Robot verifies this exact X Home at dispatch.'
                                                : action?.disabled_reason ?? action?.unavailable_reason ?? 'Robot action unavailable.';
                                    return (
                                        <button
                                            key={operation}
                                            type="button"
                                            disabled={!linkConnected || operatorCatalog.isLoading || !enabled}
                                            title={enabled ? 'Robot-owned exact OEM action' : unavailableReason}
                                            onClick={() => runControl(axis, operation)}
                                            className={actionClass}
                                        >{controlLabel}</button>
                                    );
                                })}
                            </div>
                        </article>
                    ))}
                </div>
                <BioXpPipetteControlPanel
                    generation={generation}
                    connected={linkConnected && operatorCatalog.data !== undefined}
                    pipettes={operatorCatalog.data?.dashboard.pipettes}
                    freshness={operatorCatalog.data?.dashboard.snapshot.freshness}
                    actions={catalog?.actions}
                    catalogLoading={operatorCatalog.isLoading}
                    invokePending={invokeOperatorAction.isPending}
                    invokeAction={(actionId, inputs) => invokeAction(actionId, inputs)}
                />
                {invokeOperatorAction.error && (
                    <p role="alert" className="mt-3 whitespace-pre-wrap break-words text-sm text-red-300">{bioXpErrorText(invokeOperatorAction.error)}</p>
                )}
                {invokeOperatorAction.isPending && (
                    <p role="status" className="mt-3 rounded border border-cyan-800 bg-cyan-950/30 p-2 text-sm text-cyan-100">Command accepted by BMS; waiting for the robot-owned terminal receipt. Stop and Abort remain available.</p>
                )}
                {linkConnected && catalog && !historyQuery.isError && invokeOperatorAction.data && (
                    <details className="mt-3 rounded border border-slate-800 bg-slate-900/60 p-3" open>
                        <summary className="cursor-pointer text-sm font-semibold">Latest exact-OEM action receipt</summary>
                        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs text-slate-300">{JSON.stringify(invokeOperatorAction.data, null, 2)}</pre>
                    </details>
                )}
            </section>

            <details className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <summary className="cursor-pointer text-lg font-semibold">Advanced Full Command Catalog</summary>
                <p className="mt-1 text-sm text-slate-400">All primitive, service, recovery, and diagnostic routes. Kept collapsed so handler state and exact manual controls remain primary.</p>
                <div className="mt-4">
                    <BioXpOperatorControlTabs generation={generation} connected={linkConnected} />
                </div>
            </details>

            <BioXpCameraPanel
                connected={linkConnected}
                connectionGeneration={linkConnected ? generation : null}
                mutationEnabled={status?.mutation_access?.enabled === true}
            />

            <section className="rounded-xl border border-red-800/70 bg-red-950/30 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold text-red-200">Physical Aggregate Emergency Stop</h2>
                        <p className="max-w-3xl text-sm text-red-200/70">
                            Robot-owned ClassMotor stop across X, Y, Z, gripper, and thermal door with terminal speed readback.
                        </p>
                    </div>
                    <button
                        type="button"
                        disabled={!linkConnected || operatorActionById('meta.emergency_stop')?.enabled !== true || emergencyAction.isPending}
                        title={operatorActionById('meta.emergency_stop')?.disabled_reason ?? 'Robot-owned aggregate emergency stop'}
                        onClick={() => invokeAction('meta.emergency_stop', {}, emergencyAction)}
                        className="rounded bg-red-700 px-5 py-3 font-bold disabled:cursor-not-allowed disabled:opacity-35"
                    >Emergency Stop</button>
                </div>
                {linkConnected && catalog && !historyQuery.isError && emergencyAction.data && (
                    <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-red-800 p-2 text-xs text-red-100">{JSON.stringify(emergencyAction.data, null, 2)}</pre>
                )}
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-lg font-semibold">Recent Robot Actions</h2>
                    <label className="flex items-center gap-2 text-xs text-slate-400">
                        Entries
                        <select
                            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-slate-100"
                            value={historyLimit}
                            onChange={(event) => setHistoryLimit(Number(event.target.value) as 8 | 25 | 50 | 100)}
                            aria-label="Recent robot actions depth"
                        >
                            <option value={8}>8</option>
                            <option value={25}>25</option>
                            <option value={50}>50</option>
                            <option value={100}>100</option>
                        </select>
                    </label>
                </div>
                {recentCommands.length === 0 ? (
                    <p className="mt-2 text-sm text-slate-400">No robot action receipts recorded.</p>
                ) : (
                    <div className="mt-3 space-y-2">
                        {recentCommands.map((record) => (
                            <article key={record.command_id} className="rounded border border-slate-800 bg-slate-900/60 p-3 text-sm">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <strong className="font-mono text-slate-100">{record.action_id}</strong>
                                    <span className={record.status === 'failed' || record.status === 'blocked' ? 'text-red-300' : 'text-slate-300'}>
                                        {record.status.replaceAll('_', ' ')} · {bioXpReceiptTimestampText(record.finished_at)}
                                    </span>
                                </div>
                                <p className="mt-1 whitespace-pre-wrap break-words text-slate-200">{record.error ?? record.machine_assessment}</p>
                                <p className="mt-1 text-xs text-slate-400">
                                    {record.remote_acknowledged ? 'Robot HTTP acknowledged' : 'Robot HTTP did not acknowledge'} · {record.controller_acknowledged ? 'Controller ACK' : 'No controller ACK'} · {record.controller_terminal_state_verified ? 'Terminal proof verified' : 'Terminal proof unverified'} · {record.physical_effect_verified ? 'Physical effect verified' : 'Physical effect unverified'}
                                </p>
                                {(record.response != null || record.stage_receipts.length > 0) && <details className="mt-2"><summary className="cursor-pointer text-xs text-slate-400">Nested robot evidence</summary><pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap text-[11px] text-slate-400">{JSON.stringify({ response: record.response, stage_receipts: record.stage_receipts }, null, 2)}</pre></details>}
                            </article>
                        ))}
                    </div>
                )}
                {historyQuery.isError && <p role="alert" className="mt-2 text-sm text-red-300">Robot action history unavailable: {bioXpErrorText(historyQuery.error)}</p>}
            </section>

            {error && <p role="alert" className="rounded border border-red-800 p-3 text-sm text-red-300">{bioXpErrorText(error)}</p>}
            {statusQuery.isError && <p role="alert" className="text-sm text-red-300">BioXP status unavailable.</p>}
        </div>
    );
}
