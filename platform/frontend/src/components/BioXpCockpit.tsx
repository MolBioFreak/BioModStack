import { useMemo, useState } from 'react';

import {
    bioXpErrorText,
    useBioXpStatus,
    useConnectBioXp,
    useDisconnectBioXp,
    useAssessBioXpOperatorAction,
    useBioXpOperatorActionAdmission,
    useBioXpOperatorActionHistory,
    useBioXpOperatorControlCatalog,
    useBioXpOperatorDashboard,
    useInvokeBioXpOperatorAction,
    useRecoverBioXpMotion,
} from '../lib/bioxpClient';
import { BioXpCameraPanel } from './BioXpCameraPanel';
import { BioXpOperatorControlTabs } from './BioXpOperatorControlTabs';
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
    const dashboardQuery = useBioXpOperatorDashboard(true);
    const historyQuery = useBioXpOperatorActionHistory(true);
    const connect = useConnectBioXp();
    const disconnect = useDisconnectBioXp();
    const operatorCatalog = useBioXpOperatorControlCatalog(true);
    const invokeOperatorAction = useInvokeBioXpOperatorAction();
    const emergencyAction = useInvokeBioXpOperatorAction();
    const assessOperatorAction = useAssessBioXpOperatorAction();
    const recoverMotion = useRecoverBioXpMotion();
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

    const status = statusQuery.data;
    const connection = status?.connection;
    const active = connection?.active === true;
    const linkConnected = active && connection?.reachable !== false;
    const configured = connection?.configured === true;
    const generation = connection?.generation ?? 0;
    const ownershipGeneration = operatorCatalog.data?.ownership_generation ?? 0;
    const zMoveNegativeAdmission = useBioXpOperatorActionAdmission(
        'oem.z.move_steps', generation, ownershipGeneration,
        { steps: -Math.abs(manualSteps.z) }, active,
    );
    const zMovePositiveAdmission = useBioXpOperatorActionAdmission(
        'oem.z.move_steps', generation, ownershipGeneration,
        { steps: Math.abs(manualSteps.z) }, active,
    );
    const zAbsoluteAdmission = useBioXpOperatorActionAdmission(
        'oem.z.move_absolute', generation, ownershipGeneration,
        { position_steps: absoluteTargets.z }, active,
    );
    const zHomeAdmission = useBioXpOperatorActionAdmission(
        'oem.z.manual_home', generation, ownershipGeneration, {}, active,
    );
    const zDiagnosticHomeAdmission = useBioXpOperatorActionAdmission(
        'oem.z.diagnostic_home_axis', generation, ownershipGeneration, {}, active,
    );

    const ownership = connection?.ownership;
    const maintenance = connection?.maintenance_state;
    const ownershipLabel = ownership
        ? `${ownership.transport ?? 'unknown'} / ${ownership.usb ?? 'unknown'} / ${ownership.router ?? 'unknown'}`
        : 'Unavailable';
    const dashboardMotion = dashboardQuery.data?.motion;
    const motionLabel = dashboardMotion
        ? dashboardMotion.enabled ? 'Enabled — all robot readiness gates passed' : `Blocked${dashboardMotion.reason ? ` — ${dashboardMotion.reason}` : ''}`
        : 'Unavailable';
    const recentCommands = useMemo(
        () => (historyQuery.data?.receipts ?? []).slice(0, 8),
        [historyQuery.data?.receipts],
    );
    const busy = invokeOperatorAction.isPending || recoverMotion.isPending || assessOperatorAction.isPending;
    const connectedLabel = active
        ? connection?.reachable === false ? 'Connection error' : 'Connected'
        : 'Disconnected';

    const operatorActionForPath = (path: string) => (operatorCatalog.data?.actions ?? []).find(
        (action) => action.kind === 'primitive' && action.informational_path === path,
    );

    const operatorActionById = (actionId: string) => (operatorCatalog.data?.actions ?? []).find(
        (action) => action.action_id === actionId,
    );

    const invokeAction = (
        actionId: string,
        inputs: Record<string, unknown>,
        mutation = invokeOperatorAction,
    ) => {
        const action = operatorActionById(actionId);
        if (action?.requires_confirmation === true && !window.confirm(`Confirm robot action: ${action.label}\n\n${action.description}`)) return;
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
        if (axis === 'z') {
            if (operation === 'move-negative' && zMoveNegativeAdmission.data?.enabled === true) {
                invokeAction('oem.z.move_steps', { steps: -Math.abs(manualSteps.z) });
            } else if (operation === 'move-positive' && zMovePositiveAdmission.data?.enabled === true) {
                invokeAction('oem.z.move_steps', { steps: Math.abs(manualSteps.z) });
            } else if ((operation === 'home' || operation === 'commission-home') && zHomeAdmission.data?.enabled === true) {
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
        if (axis === 'z') {
            if (zAbsoluteAdmission.data?.enabled === true) invokeAction('oem.z.move_absolute', { position_steps: absoluteTargets.z });
            return;
        }
        invokeOperatorPath('/motion/oem/manual/absolute', { axis, position_steps: absoluteTargets[axis] });
    };

    const stopAxis = (axis: Axis) => axis === 'z' ? invokeAction('oem.z.stop', {}) : invokeOperatorPath('/motion/diagnostics/stop', { axis });

    const runZDiagnosticHome = () => {
        if (zDiagnosticHomeAdmission.data?.enabled === true) invokeAction('oem.z.diagnostic_home_axis', {});
    };

    const recordZObservation = (verdict: 'pass' | 'fail') => {
        const commandId = dashboardQuery.data?.z_axis.provider.awaiting_observation_receipt_id;
        if (!commandId) return;
        const note = window.prompt(`Describe the independent physical Z observation for ${verdict.toUpperCase()}:`);
        if (!note?.trim()) return;
        invokeAction('oem.z.observe', { command_id: commandId, verdict, note: note.trim() });
    };

    const assessReceipt = (commandId: string, verdict: 'pass' | 'fail') => {
        const note = window.prompt(`Describe the independent physical observation for ${verdict.toUpperCase()}:`);
        if (!note?.trim()) return;
        assessOperatorAction.mutate({ commandId, connectionGeneration: generation, ownershipGeneration, verdict, note: note.trim() });
    };

    const error = invokeOperatorAction.error ?? assessOperatorAction.error ?? recoverMotion.error ?? emergencyAction.error ?? connect.error ?? disconnect.error;

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

            <BioXpQuickDashboard connected={active} />

            <section className="rounded-xl border border-amber-700/60 bg-amber-950/20 p-4">
                <h2 className="text-lg font-semibold">Controller Activation & Recovery</h2>
                <p className="mt-1 text-sm text-slate-400">Robot-owned serial-206 activation and typed non-homing recovery. BMS does not maintain a second command registry or receipt ledger.</p>
                <div className="mt-3 flex flex-wrap gap-3">
                    <button
                        type="button"
                        disabled={!active || operatorActionById('meta.activate_motion')?.enabled !== true || busy}
                        title={operatorActionById('meta.activate_motion')?.disabled_reason ?? 'Robot-owned OEM activation'}
                        onClick={claimTransport}
                        className="rounded bg-amber-700 px-4 py-2 font-semibold hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-35"
                    >Activate 24 V / Prepare Motion</button>
                    <button
                        type="button"
                        disabled={!active || maintenance?.recovery_required !== true || busy}
                        title={maintenance?.recovery_required === true ? 'Robot-authoritative non-homing recovery' : 'Recovery is not currently required'}
                        onClick={recoverMotionNonHoming}
                        className="rounded bg-amber-700 px-4 py-2 font-semibold hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-35"
                    >Non-homing Recovery</button>
                </div>
                {recoverMotion.error && (
                    <p role="alert" className="mt-2 break-words text-sm text-red-300">Non-homing recovery failed: {bioXpErrorText(recoverMotion.error)}</p>
                )}
                {recoverMotion.data && (
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
                        disabled={!active || operatorActionForPath('/motion/oem/machine_config')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/machine_config', {})}
                        className={actionClass}
                    >Show OEM axis/config tables</button>
                    <button
                        type="button"
                        disabled={!active || operatorActionForPath('/motion/oem/position_table')?.enabled !== true || invokeOperatorAction.isPending}
                        onClick={() => invokeOperatorPath('/motion/oem/position_table', {})}
                        className={actionClass}
                    >Show OEM position table</button>
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                    {AXES.map(({ axis, label, controls }) => (
                        <article key={axis} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
                            <div className="flex items-center justify-between gap-2">
                                <h3 className="font-semibold">{label}</h3>
                                <button
                                    type="button"
                                    disabled={!active || (axis === 'z' ? operatorActionById('oem.z.stop')?.enabled !== true : operatorActionForPath('/motion/diagnostics/stop')?.enabled !== true) || invokeOperatorAction.isPending}
                                    title="Immediate OEM motor stop for this component"
                                    onClick={() => stopAxis(axis)}
                                    className="rounded bg-red-800 px-3 py-1.5 text-sm font-semibold hover:bg-red-700 disabled:opacity-35"
                                >Stop</button>
                            </div>
                            {axis !== 'door' && (
                                <div className="mt-3 grid gap-2">
                                    <label className="block text-xs text-slate-300">
                                        Relative move steps
                                        <input
                                            type="number"
                                            min={1}
                                            max={160000}
                                            step={1}
                                            value={manualSteps[axis]}
                                            onChange={(event) => {
                                                const parsed = Number.parseInt(event.target.value || '1', 10);
                                                const bounded = Number.isFinite(parsed) ? Math.max(1, Math.min(160000, Math.abs(parsed))) : 1;
                                                setManualSteps((current) => ({ ...current, [axis]: bounded }));
                                            }}
                                            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2 font-mono text-sm"
                                        />
                                    </label>
                                    <label className="block text-xs text-slate-300">
                                        OEM absolute target (steps)
                                        <div className="mt-1 flex gap-2">
                                            <input
                                                type="number"
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
                                                disabled={!active || (axis === 'z' ? zAbsoluteAdmission.data?.enabled !== true : operatorActionForPath('/motion/oem/manual/absolute')?.enabled !== true) || invokeOperatorAction.isPending}
                                                onClick={() => runAbsolute(axis)}
                                                className={actionClass}
                                            >Go absolute</button>
                                        </div>
                                    </label>
                                    {axis === 'z' && (
                                        <div className="rounded border border-cyan-800/70 bg-cyan-950/20 p-3 text-xs text-cyan-100">
                                            <p><strong>Robot-owned PSUDO_Z_HOME:</strong> derived from durable tip/plate state; browser selection is disabled.</p>
                                            <p className="mt-1"><strong>Position:</strong> {dashboardQuery.data?.z_axis.status?.position_steps ?? 'unknown'} · <strong>Reference:</strong> {dashboardQuery.data?.z_axis.status?.reference ?? 'unknown'} · <strong>Authority state:</strong> {dashboardQuery.data?.z_axis.provider.state ?? 'unknown'}</p>
                                            <p className="mt-1"><strong>GAP9/10:</strong> {dashboardQuery.data?.z_axis.status?.left_switch_state ?? 'unknown'} / {dashboardQuery.data?.z_axis.status?.right_switch_state ?? 'unknown'} · <strong>Disable GAP13/12:</strong> {String(dashboardQuery.data?.z_axis.status?.left_switch_disabled ?? 'unknown')} / {String(dashboardQuery.data?.z_axis.status?.right_switch_disabled ?? 'unknown')}</p>
                                            <div className="mt-2 flex flex-wrap gap-2">
                                                <button type="button" className={actionClass} disabled={operatorActionById('oem.z.prepare')?.enabled !== true || busy} onClick={() => invokeAction('oem.z.prepare', {})}>Prepare Z</button>
                                                <button type="button" className={actionClass} disabled={operatorActionById('oem.z.reconcile_switch_masks')?.enabled !== true || busy} onClick={() => invokeAction('oem.z.reconcile_switch_masks', { confirm: 'RECONCILE_Z_SWITCH_MASKS' })}>Reconcile GAP12/13</button>
                                                <button type="button" className={actionClass} disabled={zDiagnosticHomeAdmission.data?.enabled !== true || busy} onClick={runZDiagnosticHome}>HomeAxis diagnostic (597)</button>
                                                <button type="button" className={actionClass} disabled={!dashboardQuery.data?.z_axis.provider.awaiting_observation_receipt_id || busy} onClick={() => recordZObservation('pass')}>Record physical pass</button>
                                                <button type="button" className="rounded bg-red-800 px-3 py-2 text-sm font-semibold disabled:opacity-35" disabled={!dashboardQuery.data?.z_axis.provider.awaiting_observation_receipt_id || busy} onClick={() => recordZObservation('fail')}>Record physical fail</button>
                                            </div>
                                            {dashboardQuery.data?.z_axis.last_failure != null && <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-red-200">{JSON.stringify(dashboardQuery.data.z_axis.last_failure, null, 2)}</pre>}
                                        </div>
                                    )}
                                </div>
                            )}
                            <div className="mt-3 flex flex-wrap gap-2">
                                {controls.map(({ label: controlLabel, operation }) => {
                                    const path = operatorPathForControl(axis, operation);
                                    const zActionId = axis === 'z'
                                        ? operation === 'home' || operation === 'commission-home'
                                            ? 'oem.z.manual_home'
                                            : operation === 'move-negative' || operation === 'move-positive'
                                                ? 'oem.z.move_steps'
                                                : null
                                        : null;
                                    const action = zActionId ? operatorActionById(zActionId) : path ? operatorActionForPath(path) : null;
                                    const zAdmission = axis === 'z'
                                        ? operation === 'move-negative' ? zMoveNegativeAdmission.data
                                            : operation === 'move-positive' ? zMovePositiveAdmission.data
                                                : operation === 'home' || operation === 'commission-home' ? zHomeAdmission.data
                                                    : null
                                        : null;
                                    const enabled = axis === 'z' ? zAdmission?.enabled === true : action?.enabled === true;
                                    const unavailableReason = zAdmission?.disabled_reason ?? action?.disabled_reason ?? action?.unavailable_reason ?? 'Robot action unavailable.';
                                    return (
                                        <button
                                            key={operation}
                                            type="button"
                                            disabled={!active || operatorCatalog.isLoading || invokeOperatorAction.isPending || !enabled}
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
                {invokeOperatorAction.error && (
                    <p role="alert" className="mt-3 whitespace-pre-wrap break-words text-sm text-red-300">{bioXpErrorText(invokeOperatorAction.error)}</p>
                )}
                {invokeOperatorAction.data && (
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
                    <BioXpOperatorControlTabs generation={generation} connected={active} />
                </div>
            </details>

            <BioXpCameraPanel
                connected={active}
                connectionGeneration={active ? generation : null}
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
                        disabled={!active || operatorActionById('meta.emergency_stop')?.enabled !== true || emergencyAction.isPending}
                        title={operatorActionById('meta.emergency_stop')?.disabled_reason ?? 'Robot-owned aggregate emergency stop'}
                        onClick={() => invokeAction('meta.emergency_stop', {}, emergencyAction)}
                        className="rounded bg-red-700 px-5 py-3 font-bold disabled:cursor-not-allowed disabled:opacity-35"
                    >Emergency Stop</button>
                </div>
                {emergencyAction.data && (
                    <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-red-800 p-2 text-xs text-red-100">{JSON.stringify(emergencyAction.data, null, 2)}</pre>
                )}
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-lg font-semibold">Recent Robot Actions</h2>
                    <span className="text-xs text-slate-500">Latest 8 robot-owned receipts</span>
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
                                        {record.status.replaceAll('_', ' ')} · {record.finished_at ? new Date(record.finished_at).toLocaleString() : 'in progress'}
                                    </span>
                                </div>
                                <p className="mt-1 whitespace-pre-wrap break-words text-slate-200">{record.error ?? record.machine_assessment}</p>
                                <p className="mt-1 text-xs text-slate-400">
                                    {record.remote_acknowledged ? 'Robot HTTP acknowledged' : 'Robot HTTP did not acknowledge'} · {record.controller_acknowledged ? 'Controller ACK' : 'No controller ACK'} · {record.physical_effect_verified ? 'Physical effect verified' : 'Physical effect unverified'}
                                </p>
                                {record.safety_class === 'motion' && record.operator_assessment == null && (
                                    <div className="mt-2 flex gap-2">
                                        <button type="button" className={actionClass} disabled={assessOperatorAction.isPending} onClick={() => assessReceipt(record.command_id, 'pass')}>Observed pass</button>
                                        <button type="button" className="rounded bg-red-800 px-3 py-2 text-xs font-semibold disabled:opacity-35" disabled={assessOperatorAction.isPending} onClick={() => assessReceipt(record.command_id, 'fail')}>Observed fail</button>
                                    </div>
                                )}
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
