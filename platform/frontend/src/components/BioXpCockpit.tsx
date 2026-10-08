import { useEffect, useMemo, useState } from 'react';

import {
    type BioXpProtocol,
    bioXpErrorText,
    useBioXpCommand,
    useBioXpEmergencyStop,
    useBioXpJobs,
    useBioXpStatus,
    useCompileBioXpProtocol,
    useSubmitBioXpProtocol,
} from '../lib/bioxpClient';
import { deriveBioXpStatus, isBioXpCommandAvailable } from './bioxpInterlinkStatus';

function EvidenceValue({ value }: { value: boolean | null }) {
    if (value === null) return <span className="text-amber-300">UNKNOWN</span>;
    return <span className={value ? 'text-emerald-300' : 'text-red-300'}>{value ? 'YES' : 'NO'}</span>;
}

type CommissioningCommandName =
    | 'collect_hardware_snapshot'
    | 'construct_pipettes'
    | 'initialize_without_motion'
    | 'run_initial_check';

const COMMISSIONING_COMMANDS: ReadonlyArray<{
    command: CommissioningCommandName;
    label: string;
    detail: string;
    tone: 'query' | 'write';
}> = [
    {
        command: 'collect_hardware_snapshot',
        label: 'Collect Hardware Snapshot',
        detail: 'Serialized query-only collection. It never activates, recovers, homes, or moves hardware.',
        tone: 'query',
    },
    {
        command: 'construct_pipettes',
        label: 'Construct Four Pipettes',
        detail: 'Runs the current four-channel constructor stage with no motion. Requires fresh CAN_READY=true evidence.',
        tone: 'write',
    },
    {
        command: 'initialize_without_motion',
        label: 'Initialize Without Motion',
        detail: 'Programs OEM motor-current state and the red LED stage. The robot contract forbids physical motion.',
        tone: 'write',
    },
    {
        command: 'run_initial_check',
        label: 'Run OEM Initial Check',
        detail: 'Live OEM check: LED white, door/latch reads, board deactivate/activate, and a final read. No axis motion.',
        tone: 'write',
    },
];

export function BioXpCockpit() {
    const statusQuery = useBioXpStatus(true);
    const jobsQuery = useBioXpJobs(true);
    const compileProtocol = useCompileBioXpProtocol();
    const submitProtocol = useSubmitBioXpProtocol();
    const executeCommand = useBioXpCommand();
    const emergencyStop = useBioXpEmergencyStop();
    const [protocolName, setProtocolName] = useState('BioXP offline validation');
    const [initialCheckAck, setInitialCheckAck] = useState('');
    const [nowMs, setNowMs] = useState(() => Date.now());

    useEffect(() => {
        const timer = window.setInterval(() => setNowMs(Date.now()), 1_000);
        return () => window.clearInterval(timer);
    }, []);

    const status = statusQuery.isError ? undefined : statusQuery.data;
    const connection = status?.connection;
    const derived = useMemo(
        () => connection ? deriveBioXpStatus(connection, nowMs) : null,
        [connection, nowMs],
    );
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
        return command === 'run_initial_check'
            ? { ...base, mode: 'live', operator_ack: initialCheckAck }
            : base;
    };

    return (
        <div className="space-y-6 p-6 text-slate-100">
            <header>
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-400">BioXP control plane</p>
                <h1 className="mt-1 text-2xl font-bold">Status-first operator surface</h1>
                <p className="mt-2 max-w-3xl text-sm text-slate-400">
                    BMS owns profile, connection, admission, and local job truth. Only current-tranche commissioning
                    contracts are mapped; motion and retired OEM controls remain unavailable pending online contract verification.
                </p>
            </header>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-semibold">Connection Status</h2>
                        <p className="text-sm text-slate-400">{derived?.detail ?? 'Status request pending.'}</p>
                    </div>
                    <span className="rounded border border-slate-700 px-3 py-1 text-sm font-semibold">
                        {derived?.label ?? 'UNKNOWN'}
                    </span>
                </div>
                <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                    <div><dt className="text-slate-500">configured</dt><dd>{String(connection?.configured ?? false)}</dd></div>
                    <div><dt className="text-slate-500">active</dt><dd>{String(connection?.active ?? false)}</dd></div>
                    <div><dt className="text-slate-500">generation</dt><dd>{connection?.generation ?? 0}</dd></div>
                    <div><dt className="text-slate-500">fresh</dt><dd><EvidenceValue value={connection?.fresh ?? null} /></dd></div>
                    <div><dt className="text-slate-500">reachable</dt><dd><EvidenceValue value={connection?.reachable ?? null} /></dd></div>
                    <div><dt className="text-slate-500">runtime_ready</dt><dd><EvidenceValue value={connection?.runtime_ready ?? null} /></dd></div>
                    <div><dt className="text-slate-500">hardware_ready</dt><dd><EvidenceValue value={connection?.hardware_ready ?? null} /></dd></div>
                    <div><dt className="text-slate-500">target_url</dt><dd>{connection?.target_url ?? 'not configured'}</dd></div>
                </dl>
                {connection?.last_error && <p className="mt-3 text-sm text-red-300">last_error: {connection.last_error}</p>}
                <p className="mt-3 text-xs text-slate-500">UNKNOWN or STALE evidence never authorizes controls. Profile changes and connection actions are in the BioXP menu in the top bar. SAVED / DISCONNECTED is expected after an API restart.</p>
                {statusQuery.isError && <p className="mt-3 text-sm text-red-300">Status unavailable; cached readiness and controls are suppressed.</p>}
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-5">
                <h2 className="text-lg font-semibold">Profile</h2>
                <p className="mt-1 text-sm text-slate-400">The saved target is masked on read. Connection activation is process-local and never restored automatically.</p>
            </section>

            <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-5">
                <h2 className="text-lg font-semibold">Normal Commands</h2>
                {!status?.available_commands.length && (
                    <div className="mt-3 flex gap-2 rounded border border-amber-600/40 bg-amber-500/10 p-3 text-sm text-amber-200">
                        No normal OEM commands are available. The current robot runtime has not yet advertised an admitted command capability.
                    </div>
                )}
                <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    {COMMISSIONING_COMMANDS.map(({ command, label, detail, tone }) => {
                        const available = isBioXpCommandAvailable(status?.available_commands, command, derived?.label);
                        const ackReady = command !== 'run_initial_check' || initialCheckAck === 'INITIALIZE';
                        const blockedReason = status?.unavailable_commands[command]
                            ?? (statusQuery.isError ? 'Status is unavailable.' : 'Command is not admitted by the server.');
                        return (
                            <article key={command} className={`rounded border p-4 ${tone === 'query' ? 'border-cyan-700/60 bg-cyan-950/20' : 'border-amber-700/60 bg-amber-950/20'}`}>
                                <h3 className="font-semibold">{label}</h3>
                                <p className="mt-1 text-sm text-slate-300">{detail}</p>
                                {command === 'run_initial_check' && (
                                    <label className="mt-3 block text-xs text-amber-200">Type INITIALIZE to acknowledge the live board-cycle stage
                                        <input value={initialCheckAck} onChange={(event) => setInitialCheckAck(event.target.value)} autoComplete="off" className="mt-1 w-full rounded border border-amber-700 bg-slate-950 px-2 py-1.5 text-sm" />
                                    </label>
                                )}
                                <button
                                    type="button"
                                    disabled={!available || !ackReady || executeCommand.isPending}
                                    onClick={() => executeCommand.mutate(commandPayload(command))}
                                    className={`mt-3 rounded px-3 py-2 text-sm font-semibold disabled:opacity-40 ${tone === 'query' ? 'bg-cyan-700' : 'bg-amber-700'}`}
                                >{label}</button>
                                {!available && <p className="mt-2 text-xs text-slate-500">Blocked: {blockedReason}</p>}
                            </article>
                        );
                    })}
                </div>
                {executeCommand.error && <p className="mt-2 text-sm text-red-300">{bioXpErrorText(executeCommand.error)}</p>}
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
