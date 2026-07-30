import { useMemo } from 'react';

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
} from '../lib/bioxpClient';
import { BioXpCameraPanel } from './BioXpCameraPanel';
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
            { label: 'Home', operation: 'home' },
            { label: 'Move +', operation: 'move-positive' },
        ],
    },
    {
        axis: 'g',
        label: 'Gripper',
        controls: [
            { label: 'Home', operation: 'commission-home' },
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

    const status = currentStatusData(statusQuery);
    const connection = status?.connection;
    const active = connection?.active === true;
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
    const busy = cockpitState.normalCommandBlocked;
    const connectedLabel = active
        ? connection?.reachable === false ? 'Connection error' : 'Connected'
        : 'Disconnected';

    const send = (payload: BioXpCommandPayload) => executeCommand.mutate(payload);

    const claimTransport = () => send({
        command: 'activate_usb_for_service',
        expected_generation: generation,
        idempotency_key: crypto.randomUUID(),
    });

    const recoverMotion = () => send({
        command: 'recover_motion_non_homing',
        expected_generation: generation,
        idempotency_key: crypto.randomUUID(),
    });

    const runControl = (axis: Axis, operation: Operation) => send({
        command: 'run_axis_diagnostic',
        expected_generation: generation,
        idempotency_key: crypto.randomUUID(),
        axis,
        operation,
    });

    const stopAxis = (axis: Axis) => stopCommand.mutate({
        command: 'stop_axis_diagnostic',
        expected_generation: generation,
        idempotency_key: crypto.randomUUID(),
        axis,
    });

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
                            disabled={!configured || connect.isPending || disconnect.isPending}
                            onClick={() => connect.mutate(undefined)}
                            className={actionClass}
                        >{active ? 'Reconnect BMS Link' : 'Connect BMS Link'}</button>
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
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <h2 className="text-lg font-semibold">Manual Controls</h2>
                {unavailable.run_axis_diagnostic && (
                    <p className="mt-1 break-words text-sm text-amber-200">Motion unavailable: {unavailable.run_axis_diagnostic}</p>
                )}
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                    {AXES.map(({ axis, label, controls }) => (
                        <article key={axis} className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
                            <div className="flex items-center justify-between gap-2">
                                <h3 className="font-semibold">{label}</h3>
                                <button
                                    type="button"
                                    disabled={!isAvailable('stop_axis_diagnostic') || cockpitState.stopBlocked}
                                    title={unavailable.stop_axis_diagnostic}
                                    onClick={() => stopAxis(axis)}
                                    className="rounded bg-red-800 px-3 py-1.5 text-sm font-semibold hover:bg-red-700 disabled:opacity-35"
                                >Stop</button>
                            </div>
                            <div className="mt-3 flex flex-wrap gap-2">
                                {controls.map(({ label: controlLabel, operation }) => (
                                    <button
                                        key={operation}
                                        type="button"
                                        disabled={!isAvailable('run_axis_diagnostic') || busy}
                                        title={unavailable.run_axis_diagnostic}
                                        onClick={() => runControl(axis, operation)}
                                        className={actionClass}
                                    >{controlLabel}</button>
                                ))}
                            </div>
                        </article>
                    ))}
                </div>
            </section>

            <BioXpCameraPanel
                connected={active}
                connectionGeneration={active ? generation : null}
                mutationEnabled={status?.mutation_access?.enabled === true}
            />

            <section className="rounded-xl border border-red-800/70 bg-red-950/30 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold text-red-200">Emergency Stop</h2>
                        <p className="text-sm text-red-200/70">Stop all motion.</p>
                    </div>
                    <button
                        type="button"
                        disabled={!active || status?.emergency_stop.delivery_available !== true || emergencyStop.isPending}
                        onClick={() => emergencyStop.mutate({ generation })}
                        className="rounded bg-red-700 px-5 py-3 font-bold hover:bg-red-600 disabled:cursor-not-allowed disabled:opacity-35"
                    >Emergency Stop</button>
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
