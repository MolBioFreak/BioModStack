import { useState } from 'react';

import {
    bioXpErrorText,
    type BioXpOperatorActionSpec,
    type BioXpOperatorDashboard,
    type BioXpPipetteApplicationOperation,
    type BioXpPipetteApplicationPlanRequest,
    type BioXpPipetteChannel,
    type BioXpPipetteHardwareEvidence,
    useBioXpPipetteApplicationStatus,
    usePlanBioXpPipetteApplication,
    useReadBioXpPipetteReadback,
} from '../lib/bioxpClient';

type SnapshotFreshness = BioXpOperatorDashboard['snapshot']['freshness'];

type Props = {
    generation?: number;
    connected?: boolean;
    pipettes?: BioXpOperatorDashboard['pipettes'];
    freshness?: SnapshotFreshness;
    actions?: BioXpOperatorActionSpec[];
    catalogLoading?: boolean;
    invokePending?: boolean;
    invokeAction?: (actionId: string, inputs: Record<string, unknown>) => void;
};

const OPERATIONS: BioXpPipetteApplicationOperation[] = [
    'load_tip',
    'move_to_waste',
    'detect_fluid',
    'plunger_up',
    'plunger_down',
];

const PHYSICAL_ACTION_IDS = [
    'route.liquid_tip_liquid_tip_post.08698e28',
    'oem.z.scriptmove_to',
    'route.liquid_fluid_detection_liquid_fluid_detection_post.a12feee3',
    'oem.z.lift_pipette',
    'oem.z.lower_pipette',
] as const;

const PHYSICAL_CONTROLS = [
    'Load tip physically',
    'Move to waste physically',
    'Detect fluid physically',
    'Plunger up physically',
    'Plunger down physically',
] as const;

const validHardwareTip = (evidence: BioXpPipetteHardwareEvidence | null | undefined): boolean | null => (
    evidence?.ok === true
    && evidence.hardware_truth_level === 'hardware_query'
    && typeof evidence.tip_loaded === 'boolean'
        ? evidence.tip_loaded
        : null
);

const validHardwarePressure = (evidence: BioXpPipetteHardwareEvidence | null | undefined): number | null => (
    evidence?.ok === true
    && evidence.hardware_truth_level === 'hardware_query'
    && typeof evidence.pressure === 'number'
        ? evidence.pressure
        : null
);

function ChannelCard({ channelId, channel }: { channelId: 0 | 1 | 2 | 3; channel?: BioXpPipetteChannel }) {
    if (!channel) {
        return (
            <article data-pipette-channel={channelId} className="rounded border border-slate-700 bg-slate-950/60 p-3 text-xs">
                <h4 className="font-semibold text-slate-200">Channel {channelId + 1}</h4>
                <p className="mt-2 text-amber-300">Unavailable — channel missing from projection</p>
                <p className="mt-1 text-slate-400">Hardware tip readback: No valid hardware readback</p>
                <p className="text-slate-400">Hardware pressure: No valid hardware readback</p>
            </article>
        );
    }

    const tip = validHardwareTip(channel.hardware_tip_status);
    const pressure = validHardwarePressure(channel.hardware_pressure);
    const diagnosis = channel.oem_diagnosis ?? (channel.oem_error_queue.length > 0 ? channel.oem_error_queue.join(', ') : 'none');
    return (
        <article data-pipette-channel={channelId} className="rounded border border-slate-700 bg-slate-950/60 p-3 text-xs">
            <h4 className="font-semibold text-slate-200">Channel {channelId + 1}</h4>
            <p className={channel.available ? 'text-cyan-200' : 'text-amber-300'}>{channel.available ? 'Transport available' : 'Transport unavailable'}</p>
            <p className="mt-2 text-slate-300">Software shadow: {channel.software_initialized ? 'initialized' : 'not initialized'}; tip {channel.software_tip_loaded ? 'loaded' : 'not loaded'}</p>
            <p className="text-slate-300">Hardware tip readback: {tip === null ? 'No valid hardware readback' : tip ? 'loaded' : 'not loaded'}</p>
            <p className="text-slate-300">Hardware pressure: {pressure === null ? 'No valid hardware readback' : pressure}</p>
            <p className="text-slate-400">Diagnosis/error queue: {diagnosis}</p>
            <p className="text-slate-400">Liquid/front/rear air: {channel.liquid_level_ul} / {channel.front_air_level_ul} / {channel.rear_air_level_ul} µL</p>
            <p className="text-slate-400">Last command: {channel.last_command ?? 'none'}</p>
            <p className="text-slate-400">Projection truth: {channel.hardware_truth_level}</p>
        </article>
    );
}

function DirectLiquidEvidence({ owner }: { owner: Pick<ReturnType<typeof useReadBioXpPipetteReadback>, 'submission' | 'lookup' | 'detached' | 'identityConflict' | 'recoveryError' | 'refreshRecovery' | 'retainedHistory'> }) {
    if (!owner.submission) return null;
    return <div className="mt-2 text-xs text-slate-300">
        <p>Request {owner.submission.idempotencyKey} · connection {owner.submission.expectedConnectionGeneration}</p>
        <p>Lookup: {owner.detached ? 'detached — target changed' : owner.identityConflict ? 'identity conflict' : owner.recoveryError ? 'unavailable' : owner.lookup?.lookup_state ?? 'not requested'}</p>
        <p>Stored evidence only; never permission to resend or proof of physical success.</p>
        {owner.lookup?.record && <p>Command {owner.lookup.record.command_id} · pipette {owner.lookup.record.pipette_operation_id ?? 'missing'} · {owner.lookup.record.command_status} / {owner.lookup.record.pipette_status ?? 'missing'}</p>}
        <button type="button" disabled={owner.detached || owner.identityConflict} onClick={() => void owner.refreshRecovery()} className="mt-1 underline disabled:opacity-50">Refresh stored evidence</button>
        {(owner.retainedHistory ?? []).map((record) => <p key={record.idempotencyKey}>Retained prior request: {record.idempotencyKey} · connection {record.expectedConnectionGeneration}</p>)}
    </div>;
}

export function BioXpPipetteControlPanel({ generation = 0, connected = true, pipettes, freshness, actions = [], catalogLoading = false, invokePending = false, invokeAction }: Props) {
    const status = useBioXpPipetteApplicationStatus(generation, connected);
    const planner = usePlanBioXpPipetteApplication(generation, connected);
    const readback = useReadBioXpPipetteReadback(generation, connected);
    const [includeData, setIncludeData] = useState(false);
    const [operation, setOperation] = useState<BioXpPipetteApplicationOperation>('move_to_waste');
    const [tipTray, setTipTray] = useState('');
    const [tipWell, setTipWell] = useState('');
    const [tipType, setTipType] = useState(201);
    const [tipLocation, setTipLocation] = useState<0 | 1 | 2 | 3>(0);
    const [homeZAfter, setHomeZAfter] = useState(true);
    const [fluidClass, setFluidClass] = useState<'TC' | 'MS' | 'OC' | 'RC' | 'STRIP'>('TC');
    const [plungerLocation, setPlungerLocation] = useState('LOC_TC');
    const [localError, setLocalError] = useState<string | null>(null);

    const actionById = (actionId: string) => actions.find((action) => action.action_id === actionId);
    const physicalActionEnabled = (actionId: string) => actionById(actionId)?.enabled === true;
    const physicalActionReason = (actionId: string, fallback: string) => (
        actionById(actionId)?.disabled_reason ?? actionById(actionId)?.unavailable_reason ?? fallback
    );
    const physicalInputsFor = (control: typeof PHYSICAL_CONTROLS[number]): Record<string, unknown> => {
        switch (control) {
            case 'Load tip physically':
                return { action: 'load' };
            case 'Move to waste physically':
                return { location_id: 'WASTE_BIN' };
            case 'Detect fluid physically':
                return { dry_run: false };
            case 'Plunger up physically':
                return { location_id: plungerLocation };
            case 'Plunger down physically':
                return { location_id: plungerLocation, overpress: false };
        }
    };
    const physicalActionIdFor = (control: typeof PHYSICAL_CONTROLS[number]) => PHYSICAL_ACTION_IDS[PHYSICAL_CONTROLS.indexOf(control)];
    const dispatchPhysical = (control: typeof PHYSICAL_CONTROLS[number]) => {
        if (!invokeAction) return;
        const actionId = physicalActionIdFor(control);
        if (actionById(actionId)?.enabled !== true) return;
        invokeAction(actionId, physicalInputsFor(control));
    };

    const application = pipettes?.application ?? status.data;
    const blocker = application?.blocker ?? 'physical_pipette_execution_not_authorized';
    const transactionOutcome = typeof pipettes?.last_group_transaction?.outcome === 'string'
        ? pipettes.last_group_transaction.outcome
        : null;
    const freshnessState = freshness?.state ?? 'missing';
    const freshnessLabel = `${freshnessState.charAt(0).toUpperCase()}${freshnessState.slice(1)} snapshot · age ${typeof freshness?.age_s === 'number' ? `${freshness.age_s} s` : 'unavailable'}`;

    const submitPlan = (newOperation = false) => {
        setLocalError(null);
        let payload: BioXpPipetteApplicationPlanRequest;
        if (operation === 'load_tip') {
            if (!tipTray.trim() || !tipWell.trim()) {
                setLocalError('Tip tray and well are required for the no-motion load-tip plan.');
                return;
            }
            payload = {
                operation,
                tip_tray: tipTray.trim(),
                tip_well: tipWell.trim(),
                tip_type: tipType,
                tip_location: tipLocation,
                home_z_after: homeZAfter,
            };
        } else if (operation === 'detect_fluid') {
            payload = { operation, fluid_class: fluidClass };
        } else if (operation === 'move_to_waste') {
            payload = { operation };
        } else if (operation === 'plunger_up') {
            payload = { operation };
        } else {
            payload = { operation: 'plunger_down' };
        }
        if (newOperation) planner.newOperation(payload);
        else planner.mutate(payload);
    };

    return (
        <section className="mt-4 rounded border border-amber-800/60 bg-amber-950/20 p-3" data-pipette-application-panel>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h3 className="font-semibold text-amber-100">Four-channel pipette controls</h3>
                    <p className="mt-1 text-xs text-amber-200">Cached/software state and nested hardware-query evidence are shown separately. Physical controls dispatch robot-owned OEM actions through the same admission gate as the X/Y/Z and gripper controls; the no-motion application planner stays plan-only.</p>
                </div>
                <div className="text-right text-xs text-slate-300">
                    <p>Cached projection · live query performed {String(pipettes?.live_query_performed ?? false)}</p>
                    <p>{freshnessLabel}</p>
                </div>
            </div>

            <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {([0, 1, 2, 3] as const).map((channelId) => (
                    <ChannelCard
                        key={channelId}
                        channelId={channelId}
                        channel={pipettes?.channels.find((channel) => channel.channel === channelId)}
                    />
                ))}
            </div>

            <div className="mt-3 rounded border border-cyan-800/60 bg-cyan-950/20 p-3 text-xs" data-pipette-active-readback>
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <h4 className="font-semibold text-cyan-100">Active hardware readback</h4>
                        <p className="text-slate-400">Explicit POST query; separate from the cached dashboard and the no-motion application planner.</p>
                    </div>
                    <div className="flex items-center gap-3">
                        <label className="text-slate-300"><input type="checkbox" checked={includeData} onChange={(event) => setIncludeData(event.target.checked)} /> Include OEM data sweep</label>
                        <button type="button" disabled={!connected || readback.isPending || Boolean(readback.submission)} onClick={() => readback.mutate({ include_data: includeData })} className="rounded bg-cyan-700 px-3 py-1 text-white disabled:opacity-50">
                            {readback.isPending ? 'Reading hardware…' : 'Read live hardware'}
                        </button>
                    </div>
                </div>
                {readback.data && (
                    <div className="mt-2 text-slate-300">
                        <p>Live hardware query · {readback.data.truth_source} · live query performed {String(readback.data.live_query_performed)} · semantic ok {String(readback.data.semantic_ok)} · receipt {readback.data.receipt_id}</p>
                        <p>Delivery/controller/completion/postcondition/physical: {String(readback.data.delivery_verified)} / {String(readback.data.controller_acknowledged)} / {String(readback.data.completion_verified)} / {String(readback.data.hardware_postcondition_verified)} / {String(readback.data.physical_effect_verified)}</p>
                        <ul className="mt-1 grid gap-1 sm:grid-cols-2 xl:grid-cols-4">
                            {readback.data.channels.map((channel) => <li key={channel.channel}>Channel {channel.channel + 1}: semantic ok {String(channel.semantic_ok)}</li>)}
                        </ul>
                    </div>
                )}
                {readback.error && <p className="mt-2 text-red-300">Readback failed: {bioXpErrorText(readback.error)}</p>}
                <DirectLiquidEvidence owner={readback} />
                {readback.submission && <button type="button" disabled={!connected || readback.isPending} onClick={() => readback.newOperation({ include_data: includeData })}>New operation — read hardware</button>}
            </div>

            <dl className="mt-3 grid gap-2 text-xs text-slate-300 sm:grid-cols-2 xl:grid-cols-3">
                <div><dt className="text-slate-500">Liquid mutation gate</dt><dd>{pipettes?.liquid_mutation_enabled === true ? 'enabled by robot' : 'disabled by robot'}</dd></div>
                <div><dt className="text-slate-500">Allow to stop</dt><dd>{pipettes ? String(pipettes.allow_to_stop) : 'unavailable'}</dd></div>
                <div><dt className="text-slate-500">Group error</dt><dd>{pipettes?.last_error ? `Group error: channel ${pipettes.last_error.channel + 1} · code ${pipettes.last_error.error_code}` : 'Group error: none reported'}</dd></div>
                <div><dt className="text-slate-500">Group transaction</dt><dd>Last transaction: {transactionOutcome ?? 'unavailable'}</dd></div>
                <div><dt className="text-slate-500">Receipt evidence</dt><dd>{pipettes?.latest_receipt ? `Latest receipt: ${pipettes.latest_receipt.operation} · ${pipettes.latest_receipt.receipt_id}` : 'Latest receipt: unavailable'}</dd></div>
                <div><dt className="text-slate-500">Application evidence</dt><dd>{application ? 'Application evidence: plan only; physical execution blocked' : 'Application evidence: unavailable'}</dd></div>
            </dl>

            <p className="mt-3 rounded border border-amber-700/60 bg-amber-950/50 px-3 py-2 text-xs text-amber-200">Robot-owned blocker: {blocker}</p>
            {application && application.dependency_blockers.length > 0 && (
                <p className="mt-2 rounded border border-red-800/60 bg-red-950/30 px-3 py-2 text-xs text-red-200">
                    Dependency blockers: {application.dependency_blockers.join(', ')}
                </p>
            )}
            <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                {PHYSICAL_CONTROLS.map((label) => {
                    const actionId = physicalActionIdFor(label);
                    const enabled = physicalActionEnabled(actionId);
                    const reason = physicalActionReason(actionId, 'Robot-owned exact OEM pipette action.');
                    return (
                        <button
                            key={label}
                            type="button"
                            data-physical-pipette-control
                            disabled={!connected || catalogLoading || invokePending || !enabled}
                            title={enabled ? 'Robot-owned exact OEM pipette action' : reason}
                            onClick={() => dispatchPhysical(label)}
                            className={enabled
                                ? 'rounded border border-amber-600 bg-amber-800 px-2 py-2 text-xs text-amber-50 hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-35'
                                : 'rounded border border-slate-700 bg-slate-900 px-2 py-2 text-xs text-slate-500 disabled:cursor-not-allowed'}
                        >{label}</button>
                    );
                })}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-300">
                <label className="flex items-center gap-2">
                    Plunger Z location
                    <input
                        value={plungerLocation}
                        onChange={(event) => setPlungerLocation(event.target.value.trim().toUpperCase())}
                        placeholder="LOC_TC"
                        className="w-40 rounded bg-slate-900 px-2 py-1 font-mono text-xs"
                    />
                </label>
                <span className="text-slate-500">PositionTable location for plunger up/down Z moves (e.g. LOC_TC, WASTE_BIN).</span>
            </div>

            <div className="mt-4 rounded border border-slate-700 bg-slate-950/40 p-3">
                <h4 className="text-sm font-semibold text-slate-200">No-motion application planner</h4>
                <label className="mt-2 block text-xs text-slate-300">Operation
                    <select value={operation} onChange={(event) => setOperation(event.target.value as BioXpPipetteApplicationOperation)} className="ml-2 rounded bg-slate-900 px-2 py-1">
                        {OPERATIONS.map((value) => <option key={value} value={value}>{value}</option>)}
                    </select>
                </label>
                {operation === 'load_tip' && (
                    <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                        <input value={tipTray} onChange={(event) => setTipTray(event.target.value)} placeholder="Tip tray" className="rounded bg-slate-900 px-2 py-1 text-xs" />
                        <input value={tipWell} onChange={(event) => setTipWell(event.target.value)} placeholder="Tip well" className="rounded bg-slate-900 px-2 py-1 text-xs" />
                        <input type="number" value={tipType} onChange={(event) => setTipType(Number(event.target.value))} className="rounded bg-slate-900 px-2 py-1 text-xs" />
                        <select value={tipLocation} onChange={(event) => setTipLocation(Number(event.target.value) as 0 | 1 | 2 | 3)} className="rounded bg-slate-900 px-2 py-1 text-xs">
                            {[0, 1, 2, 3].map((value) => <option key={value} value={value}>Channel {value + 1}</option>)}
                        </select>
                        <label className="text-xs text-slate-300"><input type="checkbox" checked={homeZAfter} onChange={(event) => setHomeZAfter(event.target.checked)} /> Home Z after</label>
                    </div>
                )}
                {operation === 'detect_fluid' && (
                    <label className="mt-2 block text-xs text-slate-300">Fluid class
                        <select value={fluidClass} onChange={(event) => setFluidClass(event.target.value as typeof fluidClass)} className="ml-2 rounded bg-slate-900 px-2 py-1">
                            {['TC', 'MS', 'OC', 'RC', 'STRIP'].map((value) => <option key={value} value={value}>{value}</option>)}
                        </select>
                    </label>
                )}
                <button type="button" disabled={!connected || planner.isPending || Boolean(planner.submission)} onClick={() => submitPlan()} className="mt-3 rounded bg-amber-700 px-3 py-1 text-xs text-white disabled:opacity-50">
                    {planner.isPending ? 'Building plan…' : 'Build no-motion plan'}
                </button>
                <DirectLiquidEvidence owner={planner} />
                {planner.submission && <button type="button" disabled={!connected || planner.isPending} onClick={() => submitPlan(true)}>New operation — build plan</button>}
            </div>

            {localError && <p className="mt-2 text-xs text-red-300">{localError}</p>}
            {status.error && <p className="mt-2 text-xs text-red-300">Status unavailable: {bioXpErrorText(status.error)}</p>}
            {planner.error && <p className="mt-2 text-xs text-red-300">Plan failed: {bioXpErrorText(planner.error)}</p>}
            {planner.data && (
                <div className="mt-3 rounded border border-amber-700/50 bg-slate-950/50 p-2 text-xs text-slate-300">
                    <p><strong>Plan only:</strong> {planner.data.operation} · dependencies satisfied {String(planner.data.dependencies_satisfied)} · motion commanded {String(planner.data.motion_commanded)} · controller acknowledged {String(planner.data.controller_acknowledged)} · physical effect verified {String(planner.data.physical_effect_verified)} · receipt {planner.data.receipt_id}</p>
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap">{JSON.stringify(planner.data, null, 2)}</pre>
                </div>
            )}
        </section>
    );
}
