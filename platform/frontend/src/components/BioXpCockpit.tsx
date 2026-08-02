import { useMemo, useState } from 'react';

import {
    type BioXpActiveCommandName,
    type BioXpCommandRecord,
    type BioXpCommandPayload,
    bioXpCommandRecordText,
    bioXpErrorText,
    useBioXpCommand,
    useBioXpCommandHistory,
    useBioXpEmergencyStop,
    useBioXpStatus,
    useConnectBioXp,
    useDisconnectBioXp,
    useBioXpOperatorControlCatalog,
    useInvokeBioXpOperatorAction,
} from '../lib/bioxpClient';
import { BioXpCameraPanel } from './BioXpCameraPanel';
import { BioXpOperatorControlTabs } from './BioXpOperatorControlTabs';
import { BioXpQuickDashboard } from './BioXpQuickDashboard';
import { currentStatusData, deriveCockpitMutationState } from './bioxpCockpitState';

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
    const historyQuery = useBioXpCommandHistory(true);
    const connect = useConnectBioXp();
    const disconnect = useDisconnectBioXp();
    const executeCommand = useBioXpCommand();
    const stopCommand = useBioXpCommand();
    const emergencyStop = useBioXpEmergencyStop();
    const operatorCatalog = useBioXpOperatorControlCatalog(true);
    const invokeOperatorAction = useInvokeBioXpOperatorAction();
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
    const [zPseudoHome, setZPseudoHome] = useState<500 | 65000>(65000);
    const [controllerAction, setControllerAction] = useState<'claim' | 'recovery' | null>(null);

    const status = currentStatusData(statusQuery);
    const connection = status?.connection;
    const active = connection?.active === true;
    const linkConnected = active && connection?.reachable !== false;
    const configured = connection?.configured === true;
    const generation = connection?.generation ?? 0;
    const available = useMemo(
        () => new Set(status?.available_commands ?? []),
        [status?.available_commands],
    );
    const isAvailable = (command: BioXpActiveCommandName) => active && available.has(command);
    const unavailable = status?.unavailable_commands ?? {};
    const ownership = connection?.ownership;
    const maintenance = connection?.maintenance_state;
    const ownershipLabel = ownership
        ? `${ownership.transport ?? 'unknown'} / ${ownership.usb ?? 'unknown'} / ${ownership.router ?? 'unknown'}`
        : 'Unavailable';
    const motionLabel = maintenance?.motion_blocked === true
        ? `Blocked${maintenance.block_reason ? ` — ${maintenance.block_reason}` : ''}`
        : maintenance?.motion_blocked === false ? 'Enabled' : 'Unavailable';
    const recentCommands = useMemo(
        () => [...(historyQuery.data?.commands ?? [])].slice(-8).reverse(),
        [historyQuery.data?.commands],
    );
    const cockpitState = deriveCockpitMutationState<{ detail: string; remote_acknowledged: boolean; status?: string }>({
        execute: executeCommand,
        stop: stopCommand,
        emergency: emergencyStop,
    });
    const controllerReceipt = controllerAction === 'claim'
        && executeCommand.data?.command === 'activate_usb_for_service'
        ? executeCommand.data
        : controllerAction === 'recovery'
            && executeCommand.data?.command === 'recover_motion_non_homing'
            ? executeCommand.data
            : null;
    const busy = cockpitState.normalCommandBlocked;
    const connectedLabel = active
        ? connection?.reachable === false ? 'Connection error' : 'Connected'
        : 'Disconnected';

    const send = (payload: BioXpCommandPayload) => executeCommand.mutate(payload);

    const claimTransport = () => {
        setControllerAction('claim');
        send({
            command: 'activate_usb_for_service',
            expected_generation: generation,
            idempotency_key: crypto.randomUUID(),
        });
    };

    const recoverMotion = () => {
        setControllerAction('recovery');
        send({
            command: 'recover_motion_non_homing',
            expected_generation: generation,
            idempotency_key: crypto.randomUUID(),
        });
    };

    const operatorActionForPath = (path: string) => (operatorCatalog.data?.actions ?? []).find(
        (action) => action.kind === 'primitive' && action.informational_path === path,
    );

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
        invokeOperatorAction.mutate({
            actionId: action.action_id,
            connectionGeneration: generation,
            ownershipGeneration: operatorCatalog.data?.ownership_generation ?? 0,
            inputs,
        });
    };

    const runControl = (axis: Axis, operation: Operation) => {
        setControllerAction(null);
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

    const runAbsolute = (axis: 'x' | 'y' | 'z' | 'g') => invokeOperatorPath(
        '/motion/oem/manual/absolute',
        {
            axis,
            position_steps: absoluteTargets[axis],
            ...(axis === 'z' ? { z_pseudo_home: zPseudoHome } : {}),
        },
    );

    const stopAxis = (axis: Axis) => invokeOperatorPath('/motion/diagnostics/stop', { axis });

    const latestResult = cockpitState.latestResult;
    const latestResultText = latestResult && 'command' in latestResult
        ? bioXpCommandRecordText(latestResult as BioXpCommandRecord)
        : latestResult?.detail;
    const error = cockpitState.latestError ?? connect.error ?? disconnect.error;

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
                        <dd className={`mt-1 break-words ${maintenance?.motion_blocked === true ? 'text-amber-200' : 'text-slate-100'}`}>{motionLabel}</dd>
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
                <h2 className="text-lg font-semibold">Controller Transport & Recovery</h2>
                <p className="mt-1 text-sm text-slate-400">Claim the robot USB transport first, then clear the non-homing motion latch.</p>
                <div className="mt-3 flex flex-wrap gap-3">
                    <button
                        type="button"
                        disabled={!isAvailable('activate_usb_for_service') || busy}
                        title={unavailable.activate_usb_for_service}
                        onClick={claimTransport}
                        className="rounded bg-amber-700 px-4 py-2 font-semibold hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-35"
                    >Claim USB Transport</button>
                    <button
                        type="button"
                        disabled={!isAvailable('recover_motion_non_homing') || busy}
                        title={unavailable.recover_motion_non_homing}
                        onClick={recoverMotion}
                        className="rounded bg-amber-700 px-4 py-2 font-semibold hover:bg-amber-600 disabled:cursor-not-allowed disabled:opacity-35"
                    >Non-homing Recovery</button>
                </div>
                {unavailable.recover_motion_non_homing && (
                    <p className="mt-2 break-words text-sm text-amber-200">Recovery unavailable: {unavailable.recover_motion_non_homing}</p>
                )}
                {unavailable.activate_usb_for_service && (
                    <p className="mt-2 break-words text-sm text-amber-200">USB claim unavailable: {unavailable.activate_usb_for_service}</p>
                )}
                {controllerAction === 'claim' && executeCommand.isPending && (
                    <p className="mt-2 text-sm text-amber-200">Claiming USB transport…</p>
                )}
                {controllerAction === 'claim' && executeCommand.error && (
                    <p role="alert" className="mt-2 break-words text-sm text-red-300">USB claim failed: {bioXpErrorText(executeCommand.error)}</p>
                )}
                {controllerAction === 'recovery' && executeCommand.error && (
                    <p role="alert" className="mt-2 break-words text-sm text-red-300">Non-homing recovery failed: {bioXpErrorText(executeCommand.error)}</p>
                )}
                {controllerReceipt && (
                    <p className="mt-2 break-words text-sm text-cyan-200">
                        {controllerAction === 'claim' ? 'USB claim result' : 'Non-homing recovery result'}: {bioXpCommandRecordText(controllerReceipt)}
                    </p>
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
                                    disabled={!active || operatorActionForPath('/motion/diagnostics/stop')?.enabled !== true || invokeOperatorAction.isPending}
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
                                                disabled={!active || operatorActionForPath('/motion/oem/manual/absolute')?.enabled !== true || invokeOperatorAction.isPending}
                                                onClick={() => runAbsolute(axis)}
                                                className={actionClass}
                                            >Go absolute</button>
                                        </div>
                                    </label>
                                    {axis === 'z' && (
                                        <label className="block text-xs text-amber-200">
                                            OEM Z context / PSUDO_Z_HOME
                                            <select
                                                value={zPseudoHome}
                                                onChange={(event) => setZPseudoHome(Number(event.target.value) === 500 ? 500 : 65000)}
                                                className="mt-1 w-full rounded border border-amber-700 bg-slate-950 p-2 font-mono text-sm"
                                            >
                                                <option value={65000}>65000 — low-home context</option>
                                                <option value={500}>500 — high-home context</option>
                                            </select>
                                        </label>
                                    )}
                                </div>
                            )}
                            <div className="mt-3 flex flex-wrap gap-2">
                                {controls.map(({ label: controlLabel, operation }) => {
                                    const path = operatorPathForControl(axis, operation);
                                    const action = path ? operatorActionForPath(path) : null;
                                    const unavailableReason = action?.disabled_reason ?? action?.unavailable_reason ?? 'Robot action unavailable.';
                                    return (
                                        <button
                                            key={operation}
                                            type="button"
                                            disabled={!active || operatorCatalog.isLoading || invokeOperatorAction.isPending || action?.enabled !== true}
                                            title={action?.enabled === true ? 'Robot-owned exact OEM action' : unavailableReason}
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
                        <h2 className="text-lg font-semibold text-red-200">Physical Emergency Abort Unavailable</h2>
                        <p className="max-w-3xl text-sm text-red-200/70">
                            {status?.emergency_stop.reason ?? 'No source-grounded OEM physical aggregate abort is mounted.'}
                        </p>
                    </div>
                    <button
                        type="button"
                        disabled
                        className="rounded bg-red-700 px-5 py-3 font-bold disabled:cursor-not-allowed disabled:opacity-35"
                    >Emergency Abort Unavailable</button>
                </div>
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-lg font-semibold">Recent Commands</h2>
                    <span className="text-xs text-slate-500">Latest 8 BMS relay receipts</span>
                </div>
                {recentCommands.length === 0 ? (
                    <p className="mt-2 text-sm text-slate-400">No commands recorded in this API process.</p>
                ) : (
                    <div className="mt-3 space-y-2">
                        {recentCommands.map((record) => (
                            <article key={record.command_id} className="rounded border border-slate-800 bg-slate-900/60 p-3 text-sm">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <strong className="font-mono text-slate-100">{record.command}</strong>
                                    <span className={record.status === 'delivery_failed' ? 'text-red-300' : 'text-slate-300'}>
                                        {record.status.replaceAll('_', ' ')} · {new Date(record.finished_at).toLocaleString()}
                                    </span>
                                </div>
                                <p className="mt-1 whitespace-pre-wrap break-words text-slate-200">{bioXpCommandRecordText(record)}</p>
                                <p className="mt-1 text-xs text-slate-400">
                                    {record.remote_acknowledged ? 'Robot acknowledged' : 'Robot did not acknowledge'} · Effect not verified
                                </p>
                            </article>
                        ))}
                    </div>
                )}
                {historyQuery.isError && <p role="alert" className="mt-2 text-sm text-red-300">Command history unavailable: {bioXpErrorText(historyQuery.error)}</p>}
            </section>

            {latestResult && (
                <p className={`whitespace-pre-wrap break-words rounded border p-3 text-sm ${latestResult.status === 'delivery_failed' ? 'border-red-800 text-red-300' : latestResult.remote_acknowledged ? 'border-emerald-700 text-emerald-200' : 'border-amber-700 text-amber-200'}`}>
                    {latestResultText}
                </p>
            )}
            {error && <p role="alert" className="rounded border border-red-800 p-3 text-sm text-red-300">{bioXpErrorText(error)}</p>}
            {statusQuery.isError && <p role="alert" className="text-sm text-red-300">BioXP status unavailable.</p>}
        </div>
    );
}
