import { useEffect, useRef, useState } from 'react';
import { BioXpPipetteResults } from './BioXpPipetteResults';
import { BioXpCalibrationRun } from './BioXpCalibrationRun';
import { pipetteResults } from '../lib/bioxpPipetteResults';
import { bioXpErrorText, useBioXpWorkflowJob, useSubmitBioXpProtocol,
    type BioXpDeckDestinationV1, type BioXpWorkflowJob } from '../lib/bioxpClient';
import { describeManualStep, manualPipettingDocument, type BioXpManualStep } from '../lib/bioxpManualPipetting';

type Operation = BioXpManualStep['operation'];
const operations: Operation[] = ['move', 'lower', 'lift', 'aspirate', 'dispense', 'mix', 'load_tip', 'measure_fluid_height', 'source_fluid_offset', 'diagnostic_detect_fluid', 'source_calwith_fluid'];
const label = (operation: Operation) => operation === 'load_tip' ? 'Load tip' : operation === 'measure_fluid_height' ? 'Measure fluid height' : operation === 'source_fluid_offset' ? 'OEM fluid offset scan' : operation === 'diagnostic_detect_fluid' ? 'OEM Detect Fluid' : operation === 'source_calwith_fluid' ? 'OEM calibrate with fluid' : operation[0].toUpperCase() + operation.slice(1);
const wells = [...'ABCDEFGH'].flatMap(row => Array.from({ length: 12 }, (_, col) => `${row}${col + 1}`));

export function BioXpWellPipettingPanel({ generation, connected, destinations = [], positionTableRevision }: {
    generation: number; connected: boolean; destinations?: BioXpDeckDestinationV1[]; positionTableRevision?: string | null;
}) {
    const [tray, setTray] = useState('1');
    const [tipWell, setTipWell] = useState('A1');
    const [overpress, setOverpress] = useState(false);
    const [liftZ, setLiftZ] = useState(false);
    const [detectionSpeed, setDetectionSpeed] = useState('300');
    const [scanPlate, setScanPlate] = useState<'TC' | 'MS' | 'OC' | 'RC' | 'STRIP' | 'OCMS'>('TC');
    const [scanPrefill, setScanPrefill] = useState(false);
    const [scanSpacing, setScanSpacing] = useState('4');
    const [location, setLocation] = useState('');
    const [well, setWell] = useState('');
    const [flag, setFlag] = useState('');
    const [liftMode, setLiftMode] = useState('');
    const [height, setHeight] = useState('');
    const [channels, setChannels] = useState<number[]>([]);
    const [volume, setVolume] = useState('');
    const [aspirateSpeed, setAspirateSpeed] = useState('');
    const [dispenseSpeed, setDispenseSpeed] = useState('');
    const [cycles, setCycles] = useState('');
    const [operation, setOperation] = useState<Operation>('move');
    const [steps, setSteps] = useState<BioXpManualStep[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState(false);
    const busy = useRef(false);
    const mounted = useRef(true);
    const connection = useRef({ generation, connected });
    connection.current = { generation, connected };
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const [attempt, setAttempt] = useState<{ id: string; key: string; generation: number } | null>(null);
    const [accepted, setAccepted] = useState<BioXpWorkflowJob | null>(null);
    const submit = useSubmitBioXpProtocol();
    const sameConnection = !attempt || attempt.generation === generation;
    const query = useBioXpWorkflowJob(attempt?.id ?? null, attempt?.generation ?? generation, connected && sameConnection);
    const mismatch = !!query.data && !!attempt && (query.data.job_id !== attempt.id || query.data.command?.idempotency_key !== attempt.key);
    const job = mismatch ? null : query.data ?? accepted;
    // Historical jobs, receipts, unavailable observations and unrelated pending
    // controls are evidence, never a new admission lock. Only our HTTP is reserved.
    const enabled = connected && !pending;
    const number = (value: string, name: string) => {
        if (!value.trim() || !Number.isFinite(Number(value))) throw new Error(`Enter ${name}.`);
        return Number(value);
    };
    const draft = (op: Operation): BioXpManualStep => {
        if (op === 'load_tip') return { operation: op, tray: number(tray, 'tip tray'), well: tipWell, overpress, lift_z: liftZ };
        if (op === 'measure_fluid_height') return { operation: op, speed: number(detectionSpeed, 'detection speed') };
        if (op === 'source_fluid_offset') return { operation: op, plate: scanPlate, speed: number(detectionSpeed, 'detection speed'), transfer_fluid: scanPrefill, skip_steps: number(scanSpacing, 'sample spacing') };
        if (op === 'diagnostic_detect_fluid' || op === 'source_calwith_fluid') return { operation: op };
        if (op === 'move') {
            if (!flag) throw new Error('Select the move Z position.');
            return { operation: op, location_id: number(location, 'locationID'), well, position_flag: Number(flag) as 0 | 1 | 2 };
        }
        if (op === 'lower') return { operation: op, location_id: number(location, 'locationID') };
        if (op === 'lift') {
            if (!liftMode) throw new Error('Select the lift target.');
            return { operation: op, location_id: number(location, 'locationID'), height_steps: liftMode === 'high' ? null : number(height, 'lift height in steps') };
        }
        const common = { channels: [...channels], volume_ul: number(volume, 'volume in µL') };
        if (op === 'mix') return { operation: op, ...common, aspirate_speed: number(aspirateSpeed, 'aspirate speed'),
            dispense_speed: number(dispenseSpeed, 'dispense speed'), cycles: number(cycles, 'mix cycles') };
        return { operation: op, ...common, speed: number(op === 'aspirate' ? aspirateSpeed : dispenseSpeed, `${op} speed`) };
    };
    const append = () => {
        try {
            const step = draft(operation);
            manualPipettingDocument({ protocol_id: 'bms-manual-pipetting', steps: [step] });
            setSteps(current => [...current, step]); setError(null);
        } catch (cause) { setError(bioXpErrorText(cause)); }
    };
    async function run(op?: Operation) {
        if (!enabled || busy.current) return;
        busy.current = true; setPending(true); setError(null);
        try {
            const document = manualPipettingDocument({ protocol_id: 'bms-manual-pipetting', steps: op ? [draft(op)] : steps });
            const key = crypto.randomUUID();
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
            const id = `protocol-live-${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')}`;
            if (!mounted.current) return;
            if (!connection.current.connected || connection.current.generation !== generation) throw new Error('Connection changed before submission.');
            setAttempt({ id, key, generation }); setAccepted(null);
            const result = await submit.mutateAsync({ source_type: 'native', document, dry_run: false,
                live_execution: { live_execution_ack: true }, idempotency_key: key, expected_connection_generation: generation });
            if (!mounted.current) return;
            if (result.job_id !== id || result.command?.idempotency_key !== key) throw new Error('Robot returned a different submission identity.');
            setAccepted(result);
        } catch (cause) { if (mounted.current) setError(bioXpErrorText(cause)); }
        finally { busy.current = false; if (mounted.current) setPending(false); }
    }
    const reorder = (index: number, delta: number) => setSteps(current => {
        const next = [...current]; [next[index], next[index + delta]] = [next[index + delta], next[index]]; return next;
    });
    const edit = (index: number) => {
        const step = steps[index]; setOperation(step.operation);
        if (step.operation === 'load_tip') { setTray(String(step.tray)); setTipWell(step.well); setOverpress(step.overpress); setLiftZ(step.lift_z); }
        if (step.operation === 'measure_fluid_height') setDetectionSpeed(String(step.speed));
        if (step.operation === 'source_fluid_offset') { setScanPlate(step.plate); setDetectionSpeed(String(step.speed)); setScanPrefill(step.transfer_fluid); setScanSpacing(String(step.skip_steps)); }
        if ('location_id' in step) setLocation(String(step.location_id));
        if (step.operation === 'move') { setWell(String(step.well)); setFlag(String(step.position_flag)); }
        if (step.operation === 'lift') { setLiftMode(step.height_steps === null ? 'high' : 'height'); setHeight(step.height_steps === null ? '' : String(step.height_steps)); }
        if ('channels' in step) {
            setChannels([...step.channels]); setVolume(String(step.volume_ul));
            if (step.operation === 'mix') { setCycles(String(step.cycles)); setAspirateSpeed(String(step.aspirate_speed)); setDispenseSpeed(String(step.dispense_speed)); }
            else if (step.operation === 'aspirate') setAspirateSpeed(String(step.speed));
            else setDispenseSpeed(String(step.speed));
        }
    };
    return <section aria-label="Well pipetting" className="mt-4 space-y-3 rounded border border-cyan-700 p-4">
        <h3 className="font-semibold">Well pipetting</h3>
        <p className="text-sm">Move uses the selected block and well. Lower, Lift and liquid strokes act in place; changing a well does not move the head.</p>
        <p className="text-sm text-amber-200">Alignment is owned by the robot’s actual source TipLocation, not the plunger checkboxes. With four tips, the well is the head reference and the other channels retain fixed spacing. Selecting one plunger does not realign it. Tip alignment/presence is not established by this panel.</p>
        <p className="text-xs">Calibration: robot PositionTable {positionTableRevision ?? '(revision unavailable)'}. The grid is an address selector, not proof every well is usable at every station.</p>
        <div className="grid gap-3 sm:grid-cols-3">
            <label>Block<select aria-label="Block" className="block w-full bg-slate-950 p-2" value={destinations.some(d => String(d.location_id) === location) ? location : ''} onChange={e => setLocation(e.target.value)}>
                <option value="">Select catalog block</option>
                {destinations.map(d => <option key={d.target} value={d.location_id}>{d.label} · locationID {d.location_id}</option>)}
            </select></label>
            <label>Canonical locationID<input aria-label="Canonical locationID" className="block w-full bg-slate-950 p-2" type="number" step="1" value={location} onChange={e => setLocation(e.target.value)} /></label>
            <label>Move Z position<select aria-label="Move Z position" className="block w-full bg-slate-950 p-2" value={flag} onChange={e => setFlag(e.target.value)}>
                <option value="">Select source flag</option><option value="0">0 · source pseudo-home</option><option value="1">1 · calibrated high</option><option value="2">2 · calibrated low</option>
            </select></label>
        </div>
        <fieldset><legend>Reference well: {well || 'not selected'}</legend>
            <div className="grid grid-cols-12 gap-1 overflow-x-auto" aria-label="Reference well grid">
                {wells.map(value => <button key={value} type="button" aria-label={`Reference well ${value}`} aria-pressed={well === value}
                    className={`min-w-7 rounded border p-1 text-xs ${well === value ? 'border-cyan-300 bg-cyan-800' : 'border-slate-700 bg-slate-950'}`} onClick={() => setWell(value)}>{value}</button>)}
            </div>
        </fieldset>
        <div className="grid gap-3 sm:grid-cols-3">
            <label>Lift target<select aria-label="Lift target" className="block w-full bg-slate-950 p-2" value={liftMode} onChange={e => setLiftMode(e.target.value)}>
                <option value="">Select calibrated target</option><option value="high">Calibrated zHigh (null height)</option><option value="height">zLow − explicit height steps</option>
            </select></label>
            {liftMode === 'height' && <label>Lift height (steps)<input aria-label="Lift height (steps)" type="number" step="1" value={height} onChange={e => setHeight(e.target.value)} className="block w-full bg-slate-950 p-2" /></label>}
            <p className="text-sm">Lower uses calibrated zLow at the selected locationID. Lift and Lower do not reposition XY.</p>
        </div>
        <fieldset className="space-y-2"><legend>Manual physical pipette actions</legend>
            <label>Tip tray<select aria-label="Tip tray" value={tray} onChange={e => setTray(e.target.value)}>{[1,2,3,4,5].map(n => <option key={n}>{n}</option>)}</select></label>
            <label>Tip well<select aria-label="Tip well" value={tipWell} onChange={e => setTipWell(e.target.value)}>{wells.filter(w => /^[AB]/.test(w)).map(w => <option key={w}>{w}</option>)}</select></label>
            <label><input aria-label="Overpress" type="checkbox" checked={overpress} onChange={e => setOverpress(e.target.checked)} />Overpress</label>
            <label><input aria-label="Lift Z after pickup" type="checkbox" checked={liftZ} onChange={e => setLiftZ(e.target.checked)} />Lift Z after pickup</label>
            <label>Detection speed<input aria-label="Detection speed" type="number" step="1" value={detectionSpeed} onChange={e => setDetectionSpeed(e.target.value)} /></label>
            <label>Offset scan plate<select aria-label="Offset scan plate" value={scanPlate} onChange={e => setScanPlate(e.target.value as typeof scanPlate)}>{(['TC', 'MS', 'OC', 'RC', 'STRIP', 'OCMS'] as const).map(plate => <option key={plate}>{plate}</option>)}</select></label>
            <label><input aria-label="Prefill scan plate" type="checkbox" checked={scanPrefill} onChange={e => setScanPrefill(e.target.checked)} />Prefill from trough (OEM)</label>
            <label>Sample every N wells<input aria-label="Sample every N wells" type="number" min="1" step="1" value={scanSpacing} onChange={e => setScanSpacing(e.target.value)} /></label>
            <p className="text-xs">Load tip performs native XY/Z pickup and query. Measure fluid height acts at the current well. OEM fluid offset scan samples one chosen plate and may transfer liquid when Prefill is selected. OEM Detect Fluid moves the pool plate, scans five stations and Parks on success. Neither diagnostic saves calibration.</p>
            <p className="text-xs">OEM calibrate with fluid saves and applies station calibration in-process through the robot owner, including partial saves. Compare the run below; acceptance is separate from source-body completion. Reject restores the FULL pre-run calibration, replacing any later calibration edits. No restart or home is requested by these controls.</p>
        </fieldset>
        <fieldset><legend>Liquid plunger channels only</legend><div className="flex flex-wrap gap-4">
            {[0, 1, 2, 3].map(channel => <label key={channel}><input type="checkbox" aria-label={`Plunger ${channel + 1}`} checked={channels.includes(channel)} onChange={e => setChannels(current => e.target.checked ? [...current, channel].sort() : current.filter(c => c !== channel))} /> Plunger {channel + 1} (ID {channel})</label>)}
        </div></fieldset>
        <div className="grid gap-3 sm:grid-cols-4">
            {([['Volume (µL)', volume, setVolume], ['Aspirate speed', aspirateSpeed, setAspirateSpeed], ['Dispense speed', dispenseSpeed, setDispenseSpeed], ['Mix cycles', cycles, setCycles]] as const).map(([name, value, setter]) =>
                <label key={name}>{name}<input aria-label={name} className="block w-full bg-slate-950 p-2" type="number" step={name === 'Mix cycles' ? '1' : 'any'} value={value} onChange={e => setter(e.target.value)} /></label>)}
        </div>
        <p className="text-xs">Speeds are native explicit-channel speed values. Mix repeats the chosen aspiration/dispense strokes (1–50 cycles); it is not OEM scientific mmix/mixAll. Basic moves and strokes have no implicit lifecycle; OEM scans run their source-native sequences.</p>
        <div className="flex flex-wrap gap-2">{operations.map(op => <button type="button" key={op} disabled={!enabled} onClick={() => void run(op)} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">{label(op)} now</button>)}</div>
        <fieldset className="space-y-2 rounded border border-slate-700 p-3"><legend>Ordered well-to-well program</legend>
            <p className="text-sm">Author each step explicitly. For a transfer: Move → Lower → Aspirate → Lift, then select the destination and add Move → Lower → Dispense → Lift. Adding, copying and reordering do not move hardware.</p>
            <label>Step to append<select aria-label="Step to append" value={operation} onChange={e => setOperation(e.target.value as Operation)} className="ml-2 bg-slate-950 p-2">{operations.map(op => <option key={op} value={op}>{label(op)}</option>)}</select></label>
            <button type="button" onClick={append} className="ml-2 rounded border px-3 py-2">Append step</button>
            <ol className="space-y-2">{steps.map((step, index) => <li key={index} className="flex flex-wrap items-center gap-2" data-manual-step={index}>
                <span>{index + 1}. {describeManualStep(step)}</span>
                <button type="button" aria-label={`Copy step ${index + 1} to editor`} onClick={() => edit(index)}>Copy to editor</button>
                <button type="button" aria-label={`Move step ${index + 1} up`} disabled={index === 0} onClick={() => reorder(index, -1)}>↑</button>
                <button type="button" aria-label={`Move step ${index + 1} down`} disabled={index === steps.length - 1} onClick={() => reorder(index, 1)}>↓</button>
                <button type="button" aria-label={`Remove step ${index + 1}`} onClick={() => setSteps(current => current.filter((_, i) => i !== index))}>Remove</button>
            </li>)}</ol>
            <button type="button" disabled={!enabled} onClick={() => void run()} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">Run ordered steps</button>
        </fieldset>
        {pending && <p role="status">Submitting native pipetting program…</p>}
        {attempt && <p className="break-all text-xs">Job {attempt.id} · request {attempt.key}</p>}
        {!sameConnection && <p role="alert">Connection changed. Earlier job belongs to connection {attempt?.generation}.</p>}
        {mismatch && <p role="alert">Job identity mismatch; check robot status.</p>}
        {query.error && <p role="alert">Job readback unavailable: {bioXpErrorText(query.error)}</p>}
        {job && <><p role="status">Robot job: {job.command?.status ?? job.status} · {job.execution?.runtime_state.workflow?.phase ?? 'phase unavailable'}. Job acceptance is not physical proof.</p>
            {job.execution?.runtime_state.action_results?.map((result, index) =>
                <BioXpPipetteResults key={index} value={result} />)}</>}
        {[...new Set((job?.execution?.runtime_state.action_results ?? []).flatMap(result =>
            pipetteResults(result).flatMap(value => value.run_id ? [value.run_id] : [])))].map(runId =>
            <BioXpCalibrationRun key={`${attempt?.generation}:${runId}`} runId={runId}
                generation={attempt?.generation ?? generation} connected={connected && sameConnection} />)}
        <BioXpCalibrationRun key={`recover:${generation}`} generation={generation} connected={connected} />
        {error && <p role="alert">{error}</p>}
    </section>;
}
