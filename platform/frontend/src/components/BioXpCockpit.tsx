import { useEffect, useMemo, useState } from 'react';

import {
    type BioXpProtocol,
    bioXpErrorText,
    useConnectBioXp,
    useBioXpCommand,
    useBioXpEmergencyStop,
    useBioXpJobs,
    useBioXpOemFullLifecycleContract,
    useBioXpOemFullLifecycleRun,
    useBioXpStatus,
    useCancelBioXpOemFullLifecycle,
    useCompileBioXpProtocol,
    usePlanBioXpOemFullLifecycle,
    useSubmitBioXpProtocol,
} from '../lib/bioxpClient';
import {
    deriveBioXpNoCommandsMessage,
    deriveBioXpStatus,
    isBioXpControlPlaneFresh,
    isBioXpCommandAvailable,
} from './bioxpInterlinkStatus';

function EvidenceValue({ value }: { value: boolean | null }) {
    if (value === null) return <span className="text-amber-300">UNKNOWN</span>;
    return <span className={value ? 'text-emerald-300' : 'text-red-300'}>{value ? 'YES' : 'NO'}</span>;
}

type CommissioningCommandName =
    | 'activate_usb_for_service'
    | 'collect_hardware_snapshot'
    | 'initialize_oem_environment';

type AxisDiagnosticAxis = 'x' | 'y' | 'z' | 'g' | 'door';
type AxisDiagnosticOperation =
    | 'move-negative'
    | 'move-positive'
    | 'home'
    | 'park-6000'
    | 'commission-home'
    | 'close'
    | 'open'
    | 'open-wide';

type AxisDiagnosticBlock = {
    axis: AxisDiagnosticAxis;
    label: string;
    stopLabel: string;
    detail: string;
    operations: ReadonlyArray<{ operation: AxisDiagnosticOperation; label: string; detail: string }>;
};

const AXIS_DIAGNOSTIC_BLOCKS: ReadonlyArray<AxisDiagnosticBlock> = [
    {
        axis: 'x', label: 'X axis', stopLabel: 'Stop X axis',
        detail: 'OEM X carriage: fixed commissioning jogs, switch-search home/set-home, startup park, live state, and stop.',
        operations: [
            { operation: 'move-negative', label: 'Commissioning jog −100', detail: 'Small fixed OEM MVP relative direction check; no caller-supplied distance.' },
            { operation: 'move-positive', label: 'Commissioning jog +100', detail: 'Small fixed OEM MVP relative direction check; no caller-supplied distance.' },
            { operation: 'home', label: 'Search and set X home', detail: 'OEM negative switch search, stop, and set-home sequence.' },
            { operation: 'park-6000', label: 'Move to OEM park 6000', detail: 'OEM startup absolute park target after reference.' },
        ],
    },
    {
        axis: 'y', label: 'Y axis', stopLabel: 'Stop Y axis',
        detail: 'OEM Y carriage: fixed commissioning jogs, switch-search home/set-home, live state, and stop.',
        operations: [
            { operation: 'move-negative', label: 'Commissioning jog −100', detail: 'Small fixed OEM MVP relative direction check; no caller-supplied distance.' },
            { operation: 'move-positive', label: 'Commissioning jog +100', detail: 'Small fixed OEM MVP relative direction check; no caller-supplied distance.' },
            { operation: 'home', label: 'Search and set Y home', detail: 'OEM negative switch search, stop, and set-home sequence.' },
        ],
    },
    {
        axis: 'z', label: 'Z axis', stopLabel: 'Stop Z axis',
        detail: 'OEM Z head: fixed commissioning jogs, positive-switch reference, live state, and stop.',
        operations: [
            { operation: 'move-negative', label: 'Commissioning jog −100', detail: 'Small fixed OEM MVP relative direction check; verify physical up/down direction.' },
            { operation: 'move-positive', label: 'Commissioning jog +100', detail: 'Small fixed OEM MVP relative direction check; verify physical up/down direction.' },
            { operation: 'home', label: 'Search and set Z reference', detail: 'OEM positive switch search, stop, and reference-zero sequence.' },
        ],
    },
    {
        axis: 'g', label: 'Gripper', stopLabel: 'Stop Gripper',
        detail: 'Calibrated gripper capability. Temporary OEM action current is internal; commissioning must end with verified idle 10/10 readback.',
        operations: [
            { operation: 'commission-home', label: 'OEM clear + home', detail: 'Atomic clear and home transaction with unconditional idle-current cleanup.' },
            { operation: 'close', label: 'Close gripper', detail: 'Move to the robot calibration close position.' },
            { operation: 'open', label: 'Open gripper', detail: 'Move to the robot calibration open position.' },
            { operation: 'open-wide', label: 'Open gripper wide', detail: 'Move to the robot calibration wide-open position.' },
        ],
    },
    {
        axis: 'door', label: 'Thermal door', stopLabel: 'Stop Thermal door',
        detail: 'OEM thermal-cover axis: switch-search home, configured open/closed positions, live state, and stop.',
        operations: [
            { operation: 'home', label: 'Home thermal door', detail: 'OEM switch-search door home.' },
            { operation: 'open', label: 'Open thermal door', detail: 'Move to configured OEM open position.' },
            { operation: 'close', label: 'Close thermal door', detail: 'Move to configured OEM closed position.' },
        ],
    },
];

const OEM_STARTUP_STAGES = [
    'constructor_pipette_stage',
    'initialization_without_motion',
    'initial_check',
] as const;

const COMMISSIONING_COMMANDS: ReadonlyArray<{
    command: CommissioningCommandName;
    label: string;
    detail: string;
    tone: 'query' | 'write';
    lifecycleStage?: string;
}> = [
    {
        command: 'activate_usb_for_service',
        label: 'Activate USB for BioXP Service',
        detail: 'Claims the Novo USB runtime for the managed service. It does not snapshot, initialize, recover motion, home, or move hardware; motion remains blocked after activation.',
        tone: 'write',
    },
    {
        command: 'collect_hardware_snapshot',
        label: 'Collect Hardware Snapshot',
        detail: 'Serialized query-only collection. It never activates, recovers, homes, or moves hardware.',
        tone: 'query',
    },
    {
        command: 'initialize_oem_environment',
        label: 'Initialize BioXP OEM Environment',
        detail: 'One OEM-mirrored startup action: initializes and verifies all four pipettes, configures controllers and thermal boards without motion, then runs initialCheck. It stops before initializeSystem, homing, or axis motion.',
        tone: 'write',
    },
];

export function BioXpCockpit() {
    const statusQuery = useBioXpStatus(true);
    const jobsQuery = useBioXpJobs(true);
    const connect = useConnectBioXp();
    const compileProtocol = useCompileBioXpProtocol();
    const submitProtocol = useSubmitBioXpProtocol();
    const executeCommand = useBioXpCommand();
    const stopCommand = useBioXpCommand();
    const emergencyStop = useBioXpEmergencyStop();
    const fullLifecycleContract = useBioXpOemFullLifecycleContract(true);
    const planFullLifecycle = usePlanBioXpOemFullLifecycle();
    const cancelFullLifecycle = useCancelBioXpOemFullLifecycle();
    const plannedRunId = planFullLifecycle.data?.run_id ?? null;
    const fullLifecycleRun = useBioXpOemFullLifecycleRun(plannedRunId);
    const [protocolName, setProtocolName] = useState('BioXP offline validation');
    const [nowMs, setNowMs] = useState(() => Date.now());

    useEffect(() => {
        const timer = window.setInterval(() => setNowMs(Date.now()), 1_000);
        return () => window.clearInterval(timer);
    }, []);

    const status = statusQuery.isError ? undefined : statusQuery.data;
    const connection = status?.connection;
    const mutationAccessEnabled = status?.mutation_access?.enabled === true;
    const controlPlaneFresh = isBioXpControlPlaneFresh(connection, nowMs);
    const mutationAccessSetting = status?.mutation_access?.server_setting
        ?? 'BIOMODSTACK_BIOXP_ENABLE_MUTATIONS';
    const derived = useMemo(
        () => connection ? deriveBioXpStatus(connection, nowMs) : null,
        [connection, nowMs],
    );
    const noCommandsMessage = deriveBioXpNoCommandsMessage(
        connection?.command_active ?? false,
        status?.available_commands,
    );
    const lifecycleContract = fullLifecycleContract.isError ? undefined : fullLifecycleContract.data;
    const lifecycleRun = cancelFullLifecycle.data ?? fullLifecycleRun.data ?? planFullLifecycle.data;
    const lifecyclePlanAvailable = mutationAccessEnabled
        && controlPlaneFresh
        && lifecycleContract?.plan_available === true
        && lifecycleContract?.evidence_lock_verified === true
        && lifecycleContract?.source_registry_identity_verified === true
        && lifecycleContract?.machine_configuration_verified === true
        && connection?.generation !== undefined;
    const lifecyclePlanBlockedReason = fullLifecycleContract.isError
        ? bioXpErrorText(fullLifecycleContract.error)
        : !mutationAccessEnabled
            ? `BMS mutation gate ${mutationAccessSetting} is disabled.`
            : !controlPlaneFresh
                ? 'The process-local BioXP control plane is not fresh.'
                : lifecycleContract?.evidence_lock_verified !== true
                    || lifecycleContract?.source_registry_identity_verified !== true
                    || lifecycleContract?.machine_configuration_verified !== true
                    ? 'The robot has not verified the selected evidence lock, source registry identity, and machine configuration.'
                    : lifecycleContract?.plan_blockers?.join('; ') || 'The robot has not admitted lifecycle planning.';
    const protocol: BioXpProtocol = {
        name: protocolName,
        steps: [{ action: 'initialize_motors' }],
    };

    const commandPayload = (command: CommissioningCommandName): Record<string, unknown> => {
        const base = {
            command,
            expected_generation: connection?.generation ?? 0,
            idempotency_key: crypto.randomUUID(),
        };
        return command === 'initialize_oem_environment'
            ? { ...base, mode: 'live', operator_ack: 'INITIALIZE' }
            : base;
    };

    const runCommissioningCommand = (command: CommissioningCommandName) => {
        if (command === 'initialize_oem_environment' && !window.confirm(
            'Run the complete OEM non-motion startup sequence? This initializes and verifies all four pipettes, configures controllers and thermal boards, and runs initialCheck. It stops before initializeSystem, homing, or axis motion.',
        )) return;
        executeCommand.mutate(commandPayload(command));
    };

    const collectAxisDiagnostics = () => {
        executeCommand.mutate({
            command: 'collect_axis_diagnostics',
            expected_generation: connection?.generation ?? 0,
            idempotency_key: crypto.randomUUID(),
        });
    };

    const runAxisDiagnostic = (axis: AxisDiagnosticAxis, operation: AxisDiagnosticOperation, label: string) => {
        const operatorReason = window.prompt(
            `Record the supervised test reason for ${label}. Physical motion may occur.`,
            `Supervised ${axis} ${operation} capability test`,
        );
        if (operatorReason === null) return;
        const reason = operatorReason.trim();
        if (!reason) {
            window.alert('A non-empty operator reason is required.');
            return;
        }
        if (!window.confirm(`Run ${label} on ${axis}? Physical motion may occur. Confirm the workspace is clear and an operator is watching the robot.`)) return;
        executeCommand.mutate({
            command: 'run_axis_diagnostic',
            axis,
            operation,
            operator_ack: 'RUN_AXIS_DIAGNOSTIC',
            reason,
            expected_generation: connection?.generation ?? 0,
            idempotency_key: crypto.randomUUID(),
        });
    };

    const runFullLifecyclePlan = () => {
        if (!lifecycleContract || !connection) return;
        planFullLifecycle.mutate({
            generation: connection.generation,
            machineSerial: lifecycleContract.machine_serial,
            registrySha256: lifecycleContract.registry_sha256,
            evidenceLockSha256: lifecycleContract.evidence_lock_sha256,
        });
    };

    const cancelCurrentLifecyclePlan = () => {
        if (!lifecycleRun || !connection || !lifecycleContract) return;
        cancelFullLifecycle.mutate({
            runId: lifecycleRun.run_id,
            generation: connection.generation,
            machineSerial: lifecycleContract.machine_serial,
            registrySha256: lifecycleContract.registry_sha256,
            evidenceLockSha256: lifecycleContract.evidence_lock_sha256,
        });
    };

    const stopAxisDiagnostic = (axis: AxisDiagnosticAxis) => {
        stopCommand.mutate({
            command: 'stop_axis_diagnostic',
            axis,
            operator_ack: 'STOP_AXIS',
            reason: `Operator requested immediate ${axis} axis stop from diagnostics cockpit`,
            expected_generation: connection?.generation ?? 0,
            idempotency_key: crypto.randomUUID(),
        });
    };

    const axisStatusAvailable = mutationAccessEnabled && controlPlaneFresh
        && isBioXpCommandAvailable(status?.available_commands, 'collect_axis_diagnostics', derived?.label);
    const axisRunAvailable = mutationAccessEnabled && controlPlaneFresh
        && isBioXpCommandAvailable(status?.available_commands, 'run_axis_diagnostic', derived?.label);
    const axisStopAvailable = mutationAccessEnabled && connection?.active === true
        && status?.available_commands?.includes('stop_axis_diagnostic') === true;
    const axisStatusBlockedReason = status?.unavailable_commands?.collect_axis_diagnostics
        ?? (statusQuery.isError ? 'Status is unavailable.' : 'Live axis status is not admitted by the robot.');
    const axisRunBlockedReason = status?.unavailable_commands?.run_axis_diagnostic
        ?? (statusQuery.isError ? 'Status is unavailable.' : 'Motion requires fresh reachable, runtime-ready, hardware-ready evidence.');
    const axisStopBlockedReason = status?.unavailable_commands?.stop_axis_diagnostic
        ?? (statusQuery.isError ? 'Status is unavailable.' : 'Axis stop requires an active managed robot connection.');

    return (
        <div className="space-y-6 p-6 text-slate-100">
            <header>
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-400">BioXP control plane</p>
                <h1 className="mt-1 text-2xl font-bold">Status-first operator surface</h1>
                <p className="mt-2 max-w-3xl text-sm text-slate-400">
                    BMS owns profile, connection, admission, and local job truth. Finite robot-owned component diagnostics
                    are mapped; motion requires fresh ready evidence and supervised commissioning, while retired arbitrary OEM controls remain unavailable.
                </p>
            </header>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold">Connection Status</h2>
                        <p className="text-sm text-slate-400">{derived?.detail ?? 'Status request pending.'}</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded border border-slate-700 px-3 py-1 text-sm font-semibold">
                            {derived?.label ?? 'UNKNOWN'}
                        </span>
                        <button
                            type="button"
                            onClick={() => connect.mutate(undefined)}
                            disabled={!connection?.configured || connection?.active === true || connect.isPending}
                            className="rounded bg-cyan-700 px-3 py-1 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
                        >
                            {connect.isPending
                                ? 'Connecting…'
                                : connection?.active
                                    ? 'BioXP Connected'
                                    : 'Connect / Reconnect BioXP'}
                        </button>
                    </div>
                </div>
                <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div><dt className="text-slate-500">configured</dt><dd>{String(connection?.configured ?? false)}</dd></div>
                    <div><dt className="text-slate-500">active</dt><dd>{String(connection?.active ?? false)}</dd></div>
                    <div><dt className="text-slate-500">generation</dt><dd>{connection?.generation ?? 0}</dd></div>
                    <div><dt className="text-slate-500">runtime_fresh</dt><dd><EvidenceValue value={connection?.fresh ?? null} /></dd></div>
                    <div><dt className="text-slate-500">hardware_fresh</dt><dd><EvidenceValue value={connection?.hardware_fresh ?? null} /></dd></div>
                    <div><dt className="text-slate-500">reachable</dt><dd><EvidenceValue value={connection?.reachable ?? null} /></dd></div>
                    <div><dt className="text-slate-500">runtime_ready</dt><dd><EvidenceValue value={connection?.runtime_ready ?? null} /></dd></div>
                    <div><dt className="text-slate-500">hardware_ready</dt><dd><EvidenceValue value={connection?.hardware_ready ?? null} /></dd></div>
                    <div><dt className="text-slate-500">target_url</dt><dd>{connection?.target_url ?? 'not configured'}</dd></div>
                </dl>
                {connection?.last_error && <p className="mt-3 text-sm text-red-300">last_error: {connection.last_error}</p>}
                {connect.error && <p className="mt-3 text-sm text-red-300">Connect failed: {bioXpErrorText(connect.error)}</p>}
                {status && !mutationAccessEnabled && (
                    <p className="mt-3 rounded border border-amber-600/50 bg-amber-500/10 p-3 text-sm text-amber-200">
                        Commissioning writes are disabled or were not advertised by the BMS server. Set <code>{mutationAccessSetting}</code>. No API key or secret is required.
                    </p>
                )}
                <p className="mt-3 text-xs text-slate-500">UNKNOWN or STALE evidence never authorizes controls. Profile changes and connection actions are in the BioXP menu in the top bar. SAVED / DISCONNECTED is expected after an API restart.</p>
                {statusQuery.isError && <p className="mt-3 text-sm text-red-300">Status unavailable; cached readiness and controls are suppressed.</p>}
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-5">
                <h2 className="text-lg font-semibold">Profile</h2>
                <p className="mt-1 text-sm text-slate-400">The saved target is masked on read. Connection activation is process-local and never restored automatically.</p>
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-lg font-semibold">OEM Startup Lifecycle</h2>
                    <span className="text-sm text-slate-400">{connection?.startup_lifecycle?.state ?? 'unavailable'}</span>
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-3">
                    {OEM_STARTUP_STAGES.map((name) => {
                        const stage = connection?.startup_lifecycle?.stages[name];
                        if (!stage) return null;
                        return (
                        <article key={name} className="rounded border border-slate-800 p-3 text-sm">
                            <h3 className="font-mono text-xs text-cyan-300">{name}</h3>
                            <p className="mt-1 font-semibold">{stage.state}</p>
                            <p className="text-xs text-slate-500">attempts={stage.attempt_count ?? 0} · repeatable={String(stage.repeatable ?? false)}</p>
                            {stage.prerequisite && <p className="text-xs text-slate-500">requires={stage.prerequisite}</p>}
                            {stage.error && <p className="mt-1 text-xs text-red-300">{stage.error}</p>}
                        </article>
                        );
                    })}
                </div>
                {!connection?.startup_lifecycle && <p className="mt-3 text-sm text-amber-300">Collect a hardware snapshot or probe the active robot to load lifecycle evidence.</p>}
            </section>

            <section className="rounded-xl border border-violet-700/60 bg-violet-950/20 p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-lg font-semibold">Full OEM Lifecycle · Dry-run Contract</h2>
                    <span className="text-xs text-violet-200">Planning only · no hardware command</span>
                </div>
                <p className="mt-2 text-sm text-slate-300">
                    The robot owns all branch inputs and stage order. This surface creates a persisted selected-path plan;
                    it does not enqueue execution, prove provider binding, or verify any physical effect.
                </p>
                {lifecycleContract && (
                    <>
                        <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
                            <div><dt className="text-slate-500">machine_serial</dt><dd>{lifecycleContract.machine_serial}</dd></div>
                            <div><dt className="text-slate-500">plan_available</dt><dd>{String(lifecycleContract.plan_available)}</dd></div>
                            <div><dt className="text-slate-500">live_creation_enabled</dt><dd>{String(lifecycleContract.live_creation_enabled)}</dd></div>
                            <div><dt className="text-slate-500">commissioned</dt><dd>{String(lifecycleContract.physical_commissioning_complete)}</dd></div>
                            <div><dt className="text-slate-500">evidence_lock_verified</dt><dd>{String(lifecycleContract.evidence_lock_verified)}</dd></div>
                            <div><dt className="text-slate-500">source_registry_identity_verified</dt><dd>{String(lifecycleContract.source_registry_identity_verified)}</dd></div>
                            <div><dt className="text-slate-500">machine_configuration_verified</dt><dd>{String(lifecycleContract.machine_configuration_verified)}</dd></div>
                            <div className="sm:col-span-2 lg:col-span-4"><dt className="text-slate-500">registry_sha256</dt><dd className="break-all font-mono">{lifecycleContract.registry_sha256}</dd></div>
                            <div className="sm:col-span-2 lg:col-span-4"><dt className="text-slate-500">evidence_lock_sha256</dt><dd className="break-all font-mono">{lifecycleContract.evidence_lock_sha256}</dd></div>
                        </dl>
                        <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                            {Object.entries(lifecycleContract.providers).map(([name, provider]) => (
                                <article key={name} className="rounded border border-violet-800/50 bg-slate-950/40 p-3 text-xs">
                                    <h3 className="font-mono text-violet-200">{name}</h3>
                                    <p className="mt-1">implemented={String(provider.implemented)}</p>
                                    <p>live_bound={String(provider.live_bound)} · commissioned={String(provider.commissioned)}</p>
                                </article>
                            ))}
                        </div>
                    </>
                )}
                <button
                    type="button"
                    disabled={!lifecyclePlanAvailable || planFullLifecycle.isPending}
                    onClick={runFullLifecyclePlan}
                    className="mt-4 rounded bg-violet-700 px-4 py-2 text-sm font-semibold disabled:opacity-40"
                >Create persisted dry-run plan</button>
                {!lifecyclePlanAvailable && <p className="mt-2 text-xs text-amber-300">Blocked: {lifecyclePlanBlockedReason}</p>}
                {planFullLifecycle.error && <p className="mt-2 text-sm text-red-300">{bioXpErrorText(planFullLifecycle.error)}</p>}
                {lifecycleRun && (
                    <div className="mt-4 rounded border border-violet-800/50 bg-slate-950/50 p-4">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <div>
                                <h3 className="font-semibold">Persisted lifecycle ledger</h3>
                                <p className="font-mono text-xs text-slate-400">{lifecycleRun.run_id} · {lifecycleRun.run_state}</p>
                            </div>
                            <button
                                type="button"
                                disabled={lifecycleRun.run_state !== 'planned' || !mutationAccessEnabled || !controlPlaneFresh || cancelFullLifecycle.isPending}
                                onClick={cancelCurrentLifecyclePlan}
                                className="rounded bg-slate-700 px-3 py-2 text-xs font-semibold disabled:opacity-40"
                            >Cancel dry-run record</button>
                        </div>
                        <p className="mt-2 text-xs text-slate-400">
                            physical_motion_commanded={String(lifecycleRun.physical_motion_commanded)} · physical_effect_verified={String(lifecycleRun.physical_effect_verified)}
                        </p>
                        <div className="mt-3 max-h-[32rem] space-y-2 overflow-auto">
                            {lifecycleRun.stages.map((stage, stageIndex) => (
                                <article key={`${stageIndex}-${stage.stage_id}`} className="rounded border border-slate-800 p-3 text-xs">
                                    <div className="flex flex-wrap justify-between gap-2"><span className="font-mono text-violet-200">{stageIndex + 1}. {stage.stage_id}</span><span>{stage.status}</span></div>
                                    <p className="mt-1 text-slate-400">{stage.source_anchor}</p>
                                    <p className="mt-1">would_command_hardware={String(stage.would_command_hardware)} · would_command_physical_motion={String(stage.would_command_physical_motion)}</p>
                                    {stage.execution_semantics && <p className="text-slate-400">execution_semantics={stage.execution_semantics}</p>}
                                </article>
                            ))}
                        </div>
                    </div>
                )}
                {cancelFullLifecycle.error && <p className="mt-2 text-sm text-red-300">{bioXpErrorText(cancelFullLifecycle.error)}</p>}
            </section>

            <section className="rounded-xl border border-cyan-700/60 bg-cyan-950/20 p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold">Per-axis OEM capability diagnostics</h2>
                        <p className="mt-2 max-w-4xl text-sm text-slate-300">Each button targets one finite robot-owned movement mechanism. Values are fixed by the robot contract; there are no arbitrary motor, current, or transport controls. Small fixed commissioning jogs, OEM switch-search homing, calibrated component positions, and stop can be verified independently before they are composed into initializeSystem.</p>
                    </div>
                    <button
                        type="button"
                        disabled={!axisStatusAvailable || executeCommand.isPending}
                        onClick={collectAxisDiagnostics}
                        className="rounded bg-cyan-700 px-3 py-2 text-sm font-semibold disabled:opacity-40"
                    >Collect live axis status</button>
                </div>
                {!axisStatusAvailable && <p className="mt-2 text-xs text-slate-500">Status blocked: {axisStatusBlockedReason}</p>}
                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                    {AXIS_DIAGNOSTIC_BLOCKS.map(({ axis, label, stopLabel, detail, operations }) => (
                        <article key={axis} className="rounded border border-cyan-700/50 bg-slate-950/50 p-4">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                                <div>
                                    <h3 className="font-semibold">{label}</h3>
                                    <p className="mt-1 text-sm text-slate-300">{detail}</p>
                                </div>
                                <button
                                    type="button"
                                    disabled={!axisStopAvailable || stopCommand.isPending}
                                    onClick={() => stopAxisDiagnostic(axis)}
                                    className="rounded border border-red-600 bg-red-950 px-3 py-2 text-xs font-semibold text-red-200 disabled:opacity-40"
                                >{stopLabel}</button>
                            </div>
                            <div className="mt-3 grid gap-2 sm:grid-cols-2">
                                {operations.map(({ operation, label: operationLabel, detail: operationDetail }) => (
                                    <div key={operation} className="rounded border border-slate-800 p-3">
                                        <button
                                            type="button"
                                            disabled={!axisRunAvailable || executeCommand.isPending}
                                            onClick={() => runAxisDiagnostic(axis, operation, operationLabel)}
                                            className="w-full rounded bg-amber-700 px-3 py-2 text-sm font-semibold disabled:opacity-40"
                                        >{operationLabel}</button>
                                        <p className="mt-2 text-xs text-slate-400">{operationDetail}</p>
                                    </div>
                                ))}
                            </div>
                            {!axisRunAvailable && <p className="mt-3 text-xs text-slate-500">Motion blocked: {axisRunBlockedReason}</p>}
                            {!axisStopAvailable && <p className="mt-1 text-xs text-slate-500">Stop blocked: {axisStopBlockedReason}</p>}
                        </article>
                    ))}
                </div>
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-5">
                <h2 className="text-lg font-semibold">Normal Commands</h2>
                {noCommandsMessage && (
                    <div className="mt-3 flex gap-2 rounded border border-amber-600/40 bg-amber-500/10 p-3 text-sm text-amber-200">
                        {noCommandsMessage}
                    </div>
                )}
                <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    {COMMISSIONING_COMMANDS.map(({ command, label, detail, tone, lifecycleStage }) => {
                        const available = mutationAccessEnabled && controlPlaneFresh
                            && isBioXpCommandAvailable(status?.available_commands, command, derived?.label);
                        const stage = lifecycleStage ? connection?.startup_lifecycle?.stages[lifecycleStage] : undefined;
                        const blockedReason = status?.unavailable_commands?.[command]
                            ?? (statusQuery.isError ? 'Status is unavailable.' : 'Command is not admitted by the server.');
                        return (
                            <article key={command} className={`rounded border p-4 ${tone === 'query' ? 'border-cyan-700/60 bg-cyan-950/20' : 'border-amber-700/60 bg-amber-950/20'}`}>
                                <h3 className="font-semibold">{label}</h3>
                                <p className="mt-1 text-sm text-slate-300">{detail}</p>
                                {stage && <p className="mt-2 text-xs text-cyan-300">stage={stage.state} · attempts={stage.attempt_count ?? 0}</p>}
                                <button
                                    type="button"
                                    disabled={!available || executeCommand.isPending}
                                    onClick={() => runCommissioningCommand(command)}
                                    className={`mt-3 rounded px-3 py-2 text-sm font-semibold disabled:opacity-40 ${tone === 'query' ? 'bg-cyan-700' : 'bg-amber-700'}`}
                                >{label}</button>
                                {!available && <p className="mt-2 text-xs text-slate-500">Blocked: {blockedReason}</p>}
                            </article>
                        );
                    })}
                </div>
                {executeCommand.error && <p className="mt-2 text-sm text-red-300">{bioXpErrorText(executeCommand.error)}</p>}
                {executeCommand.data && (
                    <section className={`mt-4 rounded border p-4 ${executeCommand.data.status === 'acknowledged' ? 'border-emerald-700/60 bg-emerald-950/20' : executeCommand.data.status === 'queued' ? 'border-amber-700/60 bg-amber-950/20' : 'border-red-700/60 bg-red-950/20'}`}>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                            <h3 className="font-semibold">Latest Delivery Result</h3>
                            <span className="font-mono text-xs">{executeCommand.data.status}</span>
                        </div>
                        <p className="mt-1 text-sm">{executeCommand.data.detail}</p>
                        <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2">
                            <div><dt className="text-slate-500">command_id</dt><dd className="break-all font-mono">{executeCommand.data.command_id}</dd></div>
                            <div><dt className="text-slate-500">command</dt><dd>{executeCommand.data.command}</dd></div>
                            <div><dt className="text-slate-500">idempotency_key</dt><dd className="break-all font-mono">{executeCommand.data.idempotency_key}</dd></div>
                            <div><dt className="text-slate-500">generation</dt><dd>{executeCommand.data.generation}</dd></div>
                            <div><dt className="text-slate-500">started_at</dt><dd>{executeCommand.data.started_at}</dd></div>
                            <div><dt className="text-slate-500">finished_at</dt><dd>{executeCommand.data.finished_at}</dd></div>
                            <div><dt className="text-slate-500">remote_acknowledged</dt><dd>{String(executeCommand.data.remote_acknowledged)}</dd></div>
                            <div><dt className="text-slate-500">physical_effect_verified</dt><dd>{String(executeCommand.data.physical_effect_verified)}</dd></div>
                        </dl>
                        <pre className="mt-3 max-h-96 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-200">{JSON.stringify(executeCommand.data.handler_response, null, 2)}</pre>
                    </section>
                )}
            </section>

            {status?.available_controls.includes('emergency_stop') && connection && (
                <section className="rounded-xl border border-red-700/60 bg-red-950/20 p-5">
                    <h2 className="flex items-center gap-2 text-lg font-semibold text-red-200">Emergency Stop Delivery</h2>
                    <p className="mt-2 text-sm text-red-100">This separate action attempts a short-timeout delivery. A transport response does not prove physical effect.</p>
                    <button type="button" disabled={emergencyStop.isPending} onClick={() => emergencyStop.mutate({ generation: connection.generation })} className="mt-3 block rounded bg-red-700 px-4 py-2 font-semibold disabled:opacity-40">Attempt emergency-stop delivery</button>
                    {emergencyStop.data && (
                        <dl className="mt-3 grid gap-2 text-sm md:grid-cols-3">
                            <div><dt>delivery_attempted</dt><dd>{String(emergencyStop.data.delivery_attempted)}</dd></div>
                            <div><dt>remote_acknowledged</dt><dd>{String(emergencyStop.data.remote_acknowledged)}</dd></div>
                            <div><dt>physical_effect_verified</dt><dd>{String(emergencyStop.data.physical_effect_verified)}</dd></div>
                        </dl>
                    )}
                    {emergencyStop.error && <p className="mt-2 text-sm text-red-300">{bioXpErrorText(emergencyStop.error)}</p>}
                </section>
            )}

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-5">
                <h2 className="flex items-center gap-2 text-lg font-semibold">Offline Protocol Validation</h2>
                <p className="mt-1 text-sm text-slate-400">Validation is deterministic and local. It does not establish robot compatibility or executability.</p>
                <input value={protocolName} onChange={(event) => setProtocolName(event.target.value)} className="mt-3 w-full max-w-lg rounded border border-slate-700 bg-slate-900 px-3 py-2" />
                <div className="mt-3 flex gap-2">
                    <button type="button" onClick={() => compileProtocol.mutate(protocol)} className="rounded bg-slate-700 px-3 py-2 text-sm">Validate offline</button>
                    <button type="button" disabled={submitProtocol.isPending} onClick={() => submitProtocol.mutate({ protocol, idempotencyKey: crypto.randomUUID() })} className="rounded bg-blue-700 px-3 py-2 text-sm disabled:opacity-40">Save blocked local job</button>
                </div>
                {compileProtocol.data && <p className="mt-3 text-sm text-emerald-300">{compileProtocol.data.validation_status}: {compileProtocol.data.compiled_hash.slice(0, 16)}… · executable={String(compileProtocol.data.executable)}</p>}
                {compileProtocol.error && <p className="mt-2 text-sm text-red-300">{bioXpErrorText(compileProtocol.error)}</p>}
                {submitProtocol.data && <p className="mt-3 text-sm text-amber-300">Job {submitProtocol.data.job.job_id}: {submitProtocol.data.job.state}</p>}
                {submitProtocol.error && <p className="mt-2 text-sm text-red-300">{bioXpErrorText(submitProtocol.error)}</p>}
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-5">
                <div className="flex items-center justify-between"><h2 className="text-lg font-semibold">Local Jobs</h2><span aria-hidden="true">↻</span></div>
                <div className="mt-3 space-y-2">
                    {jobsQuery.data?.length ? jobsQuery.data.map((job) => (
                        <div key={job.job_id} className="rounded border border-slate-800 p-3 text-sm">
                            <div className="flex justify-between gap-3"><span>{job.job_id}</span><span>{job.state}</span></div>
                            <p className="mt-1 text-xs text-slate-500">{job.detail ?? 'No detail'}</p>
                        </div>
                    )) : <p className="text-sm text-slate-500">No local BioXP jobs.</p>}
                </div>
            </section>
        </div>
    );
}
