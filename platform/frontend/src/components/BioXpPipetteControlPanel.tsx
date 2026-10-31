import { useState } from 'react';
import { PipetteValue } from './BioXpPipetteResults';

import {
    bioXpErrorText,
    type BioXpOperatorActionSpec,
    type BioXpOperatorDashboard,
    type BioXpPipetteApplicationOperation,
    type BioXpPipetteApplicationPlanRequest,
    type BioXpPipetteChannel,
    type BioXpPipetteReadbackChannel,
    type BioXpPipetteHardwareEvidence,
    useBioXpPipetteApplicationStatus,
    usePlanBioXpPipetteApplication,
    useReadBioXpPipetteReadback,
} from '../lib/bioxpClient';

type SnapshotFreshness = BioXpOperatorDashboard['snapshot']['freshness'];
type Pipettes = BioXpOperatorDashboard['pipettes'];

type Props = {
    visible?: boolean;
    generation?: number;
    connected?: boolean;
    pipettes?: Pipettes;
    freshness?: SnapshotFreshness;
};

const OPERATIONS: BioXpPipetteApplicationOperation[] = ['load_tip', 'move_to_waste', 'detect_fluid', 'plunger_up', 'plunger_down'];

const PHYSICAL_ACTION_IDS = [
    'route.liquid_tip_liquid_tip_post.08698e28',
    'oem.z.scriptmove_to',
    'route.liquid_fluid_detection_liquid_fluid_detection_post.a12feee3',
    'oem.z.lift_pipette',
    'oem.z.lower_pipette',
] as const;

const PHYSICAL_CONTROLS = [
    'Assign source tip type / location (host only)',
    'Move to waste physically',
    'Detect fluid physically',
    'Lift pipette head (Z)',
    'Lower pipette head (Z)',
] as const;

const validHardwareTip = (evidence: BioXpPipetteHardwareEvidence | null | undefined): boolean | null => (
    evidence?.ok === true && evidence.hardware_truth_level === 'hardware_query' && typeof evidence.tip_loaded === 'boolean'
        ? evidence.tip_loaded : null);

const validHardwarePressure = (evidence: BioXpPipetteHardwareEvidence | null | undefined): number | null => (
    evidence?.ok === true && evidence.hardware_truth_level === 'hardware_query' && typeof evidence.pressure === 'number'
        ? evidence.pressure : null);

/** One table row per channel: what software records and what hardware last returned. */
function ChannelRow({ channelId, channel, readback }: { channelId: 0 | 1 | 2 | 3; channel?: BioXpPipetteChannel; readback?: BioXpPipetteReadbackChannel }) {
    const tip = readback ? (readback.tip.ok === true && typeof readback.tip.tip_loaded === 'boolean' ? readback.tip.tip_loaded : null) : validHardwareTip(channel?.hardware_tip_status);
    const pressure = readback ? (readback.pressure?.ok === true && typeof readback.pressure.pressure === 'number' ? readback.pressure.pressure : null) : validHardwarePressure(channel?.hardware_pressure);
    return <tr data-pipette-channel={channelId}>
        <th scope="row">P{channelId + 1}</th>
        <td>{!channel ? 'no report' : channel.available ? (channel.software_initialized ? 'ready' : 'not initialized') : 'unavailable'}</td>
        <td>{!channel ? '—' : channel.software_tip_loaded ? 'tip loaded' : 'no tip'}</td>
        <td>{tip === null ? 'not read' : tip ? 'tip present' : 'no tip'}</td>
        <td>{pressure === null ? 'not read' : pressure}</td>
        <td>{channel?.liquid_level_ul ?? '—'}</td>
    </tr>;
}

function DirectLiquidEvidence({ owner }: { owner: Pick<ReturnType<typeof useReadBioXpPipetteReadback>, 'submission' | 'lookup' | 'detached' | 'identityConflict' | 'recoveryError' | 'refreshRecovery' | 'retainedHistory'> }) {
    if (!owner.submission) return null;
    return <div className="mt-2 text-xs text-slate-300">
        <p>Request {owner.submission.idempotencyKey} · connection {owner.submission.expectedConnectionGeneration}</p>
        <p>Lookup: {owner.detached ? 'detached — target changed' : owner.identityConflict ? 'identity conflict' : owner.recoveryError ? 'unavailable' : owner.lookup?.lookup_state ?? 'not requested'}</p>
        {owner.lookup?.record && <p>Command {owner.lookup.record.command_id} · pipette {owner.lookup.record.pipette_operation_id ?? 'missing'} · {owner.lookup.record.command_status} / {owner.lookup.record.pipette_status ?? 'missing'}</p>}
        <button type="button" disabled={owner.detached || owner.identityConflict} onClick={() => void owner.refreshRecovery()} className="mt-1 underline disabled:opacity-50">Refresh stored evidence</button>
        {(owner.retainedHistory ?? []).map((record) => <p key={record.idempotencyKey}>Retained prior request: {record.idempotencyKey} · connection {record.expectedConnectionGeneration}</p>)}
    </div>;
}

/** Compact status: one row per channel, one read button, debug fields in a single fold. */
export function BioXpPipetteControlPanel({ generation = 0, connected = true, pipettes, freshness }: Props) {
    const readback = useReadBioXpPipetteReadback(generation, connected);
    const [includeData, setIncludeData] = useState(false);
    const returned = !readback.detached && !readback.identityConflict ? readback.data : undefined;
    const age = typeof freshness?.age_s === 'number' ? `${freshness.age_s} s ago` : 'not read since connect';
    const transactionOutcome = typeof pipettes?.last_group_transaction?.outcome === 'string' ? pipettes.last_group_transaction.outcome : null;
    return (
        <section aria-label="Pipette status" className="mt-4 rounded border border-slate-700 bg-slate-950/40 p-3 text-sm" data-pipette-application-panel>
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h3 className="font-semibold">Pipettes</h3>
                    <p className="text-xs text-slate-400">Tip type in software: {pipettes?.tip_type ?? 'unknown'} · last report {age}{returned ? ' · hardware read' : ''}</p>
                </div>
                <div className="flex items-center gap-3 text-xs">
                    <label><input type="checkbox" checked={includeData} onChange={(event) => setIncludeData(event.target.checked)} /> Include data sweep</label>
                    <button type="button" disabled={!connected || readback.isPending} onClick={() => readback.submission ? readback.newOperation({ include_data: includeData }) : readback.mutate({ include_data: includeData })} className="rounded bg-cyan-700 px-3 py-1 text-white disabled:opacity-50">
                        {readback.isPending ? 'Reading hardware…' : readback.submission ? 'Read hardware again' : 'Read live hardware'}
                    </button>
                </div>
            </div>
            <table aria-label="Pipette channels" className="mt-2 w-full text-left text-xs [&_td]:px-2 [&_td]:py-1 [&_th]:px-2 [&_th]:py-1">
                <thead className="text-slate-400"><tr><th>Channel</th><th>Software</th><th>Tip (record)</th><th>Tip (hardware)</th><th>Pressure (raw)</th><th>Liquid µL</th></tr></thead>
                <tbody>{([0, 1, 2, 3] as const).map((channelId) => <ChannelRow key={channelId} channelId={channelId}
                    channel={pipettes?.channels.find((channel) => channel.channel === channelId)}
                    readback={returned?.channels.find(channel => channel.channel === channelId)} />)}</tbody>
            </table>
            <p className="mt-1 text-xs text-slate-500">Tip records are software state; only a hardware read confirms a tip.</p>
            {pipettes?.last_error && <p role="alert" className="mt-2 text-xs text-red-300">Pipette error on channel {pipettes.last_error.channel + 1} · code {pipettes.last_error.error_code}</p>}
            {readback.error && <p role="alert" className="mt-2 text-xs text-red-300">Readback failed: {bioXpErrorText(readback.error)}</p>}
            <details className="mt-2 text-xs text-slate-300"><summary className="cursor-pointer">Details</summary>
                <dl className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                    <div><dt className="text-slate-500">Allow to stop</dt><dd>{pipettes?.allow_to_stop == null ? 'unavailable' : String(pipettes.allow_to_stop)}</dd></div>
                    <div><dt className="text-slate-500">Last group transaction</dt><dd>{transactionOutcome ?? 'unavailable'}</dd></div>
                    <div><dt className="text-slate-500">Latest receipt</dt><dd>{pipettes?.latest_receipt ? `${pipettes.latest_receipt.operation} · ${pipettes.latest_receipt.receipt_id}` : 'unavailable'}</dd></div>
                    <div><dt className="text-slate-500">Snapshot</dt><dd>{pipettes?.live_query_performed ? 'includes hardware query' : 'cached'} · {freshness?.state ?? 'missing'}</dd></div>
                </dl>
                {pipettes?.channels.map(channel => <p key={channel.channel}>P{channel.channel + 1}: errors {channel.oem_diagnosis ?? (channel.oem_error_queue.length ? channel.oem_error_queue.join(', ') : 'none')} · air front/rear {channel.front_air_level_ul ?? '—'}/{channel.rear_air_level_ul ?? '—'} µL · last command {channel.last_command ?? 'none'} · {channel.hardware_truth_level ?? 'no report type'}</p>)}
                {returned && <div data-returned-hardware className="mt-2">
                    <p>Hardware read · {returned.truth_source} · semantic ok {String(returned.semantic_ok)} · receipt {returned.receipt_id}</p>
                    {returned.channels.map(channel => <details key={channel.channel}><summary>P{channel.channel + 1} returned fields</summary>
                        {(['firmware', 'status', 'tip', 'pressure', 'data'] as const).map(field => <div key={field}><strong>{field}</strong><PipetteValue value={channel[field]} /></div>)}
                    </details>)}
                </div>}
                <DirectLiquidEvidence owner={readback} />
            </details>
        </section>
    );
}

/** Software tip type: records the type for offsets and volumes; never picks up a tip. */
export function BioXpSoftwareTipType({ connected = true, pipettes, actions = [], catalogLoading = false, invokePending = false, invokeAction }: {
    connected?: boolean; pipettes?: Pipettes; actions?: BioXpOperatorActionSpec[]; catalogLoading?: boolean; invokePending?: boolean;
    invokeAction?: (actionId: string, inputs: Record<string, unknown>) => void;
}) {
    const [selected, setSelected] = useState<10 | 50 | 200 | 201>(50);
    const action = actions.find((row) => row.informational_method === 'POST' && row.informational_path === '/liquid/pipette/tip-type');
    return <div className="rounded border border-slate-700 p-3 text-sm" data-software-tip-type-control>
        <h4 className="font-semibold">Tip type in software</h4>
        <p className="text-xs text-slate-400">Currently {pipettes?.tip_type ?? 'unknown'}. Sets which tip offset and volume limits apply. Does not pick up a tip.</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
            <label>Tip type<select aria-label="Software tip type" value={selected} onChange={(event) => setSelected(Number(event.target.value) as 10 | 50 | 200 | 201)} className="ml-2 rounded bg-slate-900 px-2 py-1">
                {[10, 50, 200, 201].map((value) => <option key={value} value={value}>{value === 201 ? '200 µL (201)' : `${value} µL`}</option>)}
            </select></label>
            <button type="button" disabled={!connected || catalogLoading || invokePending || !action?.enabled || !invokeAction}
                title={action?.disabled_reason ?? action?.unavailable_reason ?? undefined}
                onClick={() => { if (action?.enabled && invokeAction) invokeAction(action.action_id, { tip_type: selected }); }}
                className="rounded bg-cyan-800 px-3 py-1 text-white disabled:opacity-50">Set tip type</button>
        </div>
    </div>;
}

/** Robot-owned primitives and the no-motion planner; kept for service work. */
export function BioXpPipetteAdvancedTools({ visible = true, generation = 0, connected = true, actions = [], catalogLoading = false, invokePending = false, invokeAction }: {
    visible?: boolean; generation?: number; connected?: boolean; actions?: BioXpOperatorActionSpec[]; catalogLoading?: boolean; invokePending?: boolean;
    invokeAction?: (actionId: string, inputs: Record<string, unknown>) => void;
}) {
    const status = useBioXpPipetteApplicationStatus(generation, connected && visible);
    const planner = usePlanBioXpPipetteApplication(generation, connected);
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
    const inputsFor = (control: typeof PHYSICAL_CONTROLS[number]): Record<string, unknown> => {
        switch (control) {
            case 'Assign source tip type / location (host only)': return { action: 'load' };
            case 'Move to waste physically': return { location_id: 'WASTE_BIN' };
            case 'Detect fluid physically': return { dry_run: false };
            case 'Lift pipette head (Z)': return { location_id: plungerLocation };
            case 'Lower pipette head (Z)': return { location_id: plungerLocation, overpress: false };
        }
    };
    const idFor = (control: typeof PHYSICAL_CONTROLS[number]) => PHYSICAL_ACTION_IDS[PHYSICAL_CONTROLS.indexOf(control)];
    const application = status.data;
    const submitPlan = (newOperation = false) => {
        setLocalError(null);
        let payload: BioXpPipetteApplicationPlanRequest;
        if (operation === 'load_tip') {
            if (!tipTray.trim() || !tipWell.trim()) { setLocalError('Tip tray and well are required for the no-motion load-tip plan.'); return; }
            payload = { operation, tip_tray: tipTray.trim(), tip_well: tipWell.trim(), tip_type: tipType, tip_location: tipLocation, home_z_after: homeZAfter };
        } else if (operation === 'detect_fluid') payload = { operation, fluid_class: fluidClass };
        else payload = { operation } as BioXpPipetteApplicationPlanRequest;
        if (newOperation) planner.newOperation(payload); else planner.mutate(payload);
    };
    return <div className="space-y-3 text-xs text-slate-300">
        <section aria-label="Robot primitives">
            <h4 className="font-semibold text-slate-200">Robot primitives</h4>
            <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                {PHYSICAL_CONTROLS.map((label) => {
                    const actionId = idFor(label);
                    const enabled = actionById(actionId)?.enabled === true;
                    const reason = actionById(actionId)?.disabled_reason ?? actionById(actionId)?.unavailable_reason ?? 'Robot-owned physical primitive.';
                    return <button key={label} type="button" data-physical-pipette-control disabled={!connected || catalogLoading || invokePending || !enabled}
                        title={enabled ? label === 'Assign source tip type / location (host only)' ? 'Host-only source type/location assignment; no physical pickup' : 'Robot-owned physical primitive' : reason}
                        onClick={() => { if (invokeAction && enabled) invokeAction(actionId, inputsFor(label)); }}
                        className={enabled ? 'rounded border border-amber-600 bg-amber-800 px-2 py-2 text-amber-50 hover:bg-amber-700 disabled:opacity-35' : 'rounded border border-slate-700 bg-slate-900 px-2 py-2 text-slate-500'}>{label}</button>;
                })}
            </div>
            <label className="mt-2 flex items-center gap-2">Pipette head Z location
                <input value={plungerLocation} onChange={(event) => setPlungerLocation(event.target.value.trim().toUpperCase())} placeholder="LOC_TC" className="w-40 rounded bg-slate-900 px-2 py-1 font-mono" />
            </label>
        </section>
        <details className="rounded border border-slate-700 p-3">
            <summary className="cursor-pointer font-semibold">No-motion planner</summary>
            {application && application.dependency_blockers.length > 0 && <p className="mt-2 text-red-200">Planner dependencies: {application.dependency_blockers.join(', ')}</p>}
            <label className="mt-2 block">Operation
                <select value={operation} onChange={(event) => setOperation(event.target.value as BioXpPipetteApplicationOperation)} className="ml-2 rounded bg-slate-900 px-2 py-1">
                    {OPERATIONS.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
            </label>
            {operation === 'load_tip' && <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                <input value={tipTray} onChange={(event) => setTipTray(event.target.value)} placeholder="Tip tray" className="rounded bg-slate-900 px-2 py-1" />
                <input value={tipWell} onChange={(event) => setTipWell(event.target.value)} placeholder="Tip well" className="rounded bg-slate-900 px-2 py-1" />
                <input type="number" value={tipType} onChange={(event) => setTipType(Number(event.target.value))} className="rounded bg-slate-900 px-2 py-1" />
                <select value={tipLocation} onChange={(event) => setTipLocation(Number(event.target.value) as 0 | 1 | 2 | 3)} className="rounded bg-slate-900 px-2 py-1">
                    {[0, 1, 2, 3].map((value) => <option key={value} value={value}>Channel {value + 1}</option>)}
                </select>
                <label><input type="checkbox" checked={homeZAfter} onChange={(event) => setHomeZAfter(event.target.checked)} /> Home Z after</label>
            </div>}
            {operation === 'detect_fluid' && <label className="mt-2 block">Fluid class
                <select value={fluidClass} onChange={(event) => setFluidClass(event.target.value as typeof fluidClass)} className="ml-2 rounded bg-slate-900 px-2 py-1">
                    {['TC', 'MS', 'OC', 'RC', 'STRIP'].map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
            </label>}
            <button type="button" disabled={!connected || planner.isPending || Boolean(planner.submission)} onClick={() => submitPlan()} className="mt-3 rounded bg-amber-700 px-3 py-1 text-white disabled:opacity-50">
                {planner.isPending ? 'Building plan…' : 'Build no-motion plan'}
            </button>
            {planner.submission && <button type="button" disabled={!connected || planner.isPending} onClick={() => submitPlan(true)}>New operation — build plan</button>}
            <DirectLiquidEvidence owner={planner} />
            {localError && <p className="mt-2 text-red-300">{localError}</p>}
            {status.error && <p className="mt-2 text-red-300">Status unavailable: {bioXpErrorText(status.error)}</p>}
            {planner.error && <p className="mt-2 text-red-300">Plan failed: {bioXpErrorText(planner.error)}</p>}
            {planner.data && <p className="mt-2"><strong>Plan only:</strong> {planner.data.operation} · dependencies satisfied {String(planner.data.dependencies_satisfied)} · motion commanded {String(planner.data.motion_commanded)} · controller acknowledged {String(planner.data.controller_acknowledged)} · physical effect verified {String(planner.data.physical_effect_verified)} · receipt {planner.data.receipt_id}</p>}
        </details>
    </div>;
}
