import { BioXpPipetteNativeTools } from './BioXpPipetteNativeTools';
import { methodsPost, newMethod, type MethodCompile, type MethodValue } from '../lib/bioxpMethods';
import { useInvalidateSettings } from '../lib/bioxpSettingsObservations';
import { useBioXpDocumentVisible } from './BioXpObservationVisibility';
import { useEffect, useRef, useState } from 'react';
import { BioXpPipetteResults } from './BioXpPipetteResults';
import { BioXpCalibrationRun } from './BioXpCalibrationRun';
import { pipetteResults } from '../lib/bioxpPipetteResults';
import { bioXpErrorText, useBioXpWorkflowJob, useSubmitBioXpProtocol,
    type BioXpDeckDestinationV1, type BioXpWorkflowJob } from '../lib/bioxpClient';
import { BioXpSourcePipettingEditor, sourceDefaults, sourceLabels } from './BioXpSourcePipettingEditor';
import { type BioXpSourceStep, manualPipettingDocument, type BioXpManualStep } from '../lib/bioxpManualPipetting';
import { nativeIntent, type DraftObject, type NativeDraft } from '../lib/bioxpWorkflowDraft';

type Operation = BioXpManualStep['operation'];
const operations: Operation[] = ['move', 'lower', 'lift', 'aspirate', 'dispense', 'mix', 'load_tip', 'measure_fluid_height', 'source_fluid_offset', 'diagnostic_detect_fluid', 'source_calwith_fluid'];
const label = (operation: Operation) => operation in sourceLabels ? sourceLabels[operation as BioXpSourceStep['operation']] : operation === 'load_tip' ? 'Load tip' : operation === 'measure_fluid_height' ? 'Measure fluid height' : operation === 'source_fluid_offset' ? 'OEM fluid offset scan' : operation === 'diagnostic_detect_fluid' ? 'OEM Detect Fluid' : operation === 'source_calwith_fluid' ? 'OEM calibrate with fluid' : operation[0].toUpperCase() + operation.slice(1);
const wells = [...'ABCDEFGH'].flatMap(row => Array.from({ length: 12 }, (_, col) => `${row}${col + 1}`));

export const PIPETTE_SECTIONS = ['Move & pipette', 'Tips', 'Calibration', 'Advanced'] as const;
export type PipetteSection = typeof PIPETTE_SECTIONS[number];

export function BioXpWellPipettingPanel({ generation, connected, destinations = [], positionTableRevision, visible = true, section }: { visible?: boolean;
    controlsEnabled?: boolean; generation: number; connected: boolean; destinations?: BioXpDeckDestinationV1[]; positionTableRevision?: string | null;
    /** Controlled by the Pipettes tab; uncontrolled renders its own section switcher. */
    section?: PipetteSection;
}) {
    const invalidateSettings = useInvalidateSettings(generation);
    const [sourceDrafts, setSourceDrafts] = useState<Record<BioXpSourceStep['operation'], NativeDraft<BioXpSourceStep>>>(sourceDefaults);
    const updateSource = (step: NativeDraft<BioXpSourceStep>) => setSourceDrafts(current => ({ ...current, [step.operation]: step }));
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
    const documentVisible = useBioXpDocumentVisible();
    const [settledJob, setSettledJob] = useState<string | null>(null);
    const observationId = attempt?.id ?? null;
    const observeJob = documentVisible && (visible || (observationId !== null && settledJob !== observationId));
    const query = useBioXpWorkflowJob(observationId, attempt?.generation ?? generation, connected && sameConnection && observeJob);
    useEffect(() => {
        if (query.data?.command?.terminal && query.data.job_id === observationId) {
            setSettledJob(observationId);
            if (query.data.execution?.runtime_state.action_results?.some(result => pipetteResults(result).some(value => value.saved_revision_id !== undefined))) void invalidateSettings();
        }
    }, [query.data, observationId]);
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
        if (op in sourceDefaults) return nativeIntent(sourceDrafts[op as BioXpSourceStep['operation']] as unknown as DraftObject);
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
        if (op !== 'aspirate' && op !== 'dispense') throw new Error('Choose a supported operation.');
        return { operation: op, ...common, speed: number(op === 'aspirate' ? aspirateSpeed : dispenseSpeed, `${op} speed`) };
    };
    async function run(op?: Operation, explicitStep?: BioXpManualStep, nativeTool?: { action: string; inputs: MethodValue }) {
        if (!enabled || busy.current) return;
        busy.current = true; setPending(true); setError(null);
        try {
            let document: MethodValue;
            if (nativeTool) {
                const compiled = await methodsPost<MethodCompile>('compile', { method: { ...newMethod(), name: 'Pipettes immediate action', steps: [{ step_id: 'immediate', type: 'action', action: nativeTool.action, inputs: nativeTool.inputs }] } });
                if (!compiled.document) throw new Error(compiled.issues.map(issue => issue.message).join('; ') || 'Native compiler returned no document.');
                document = compiled.document;
            } else document = manualPipettingDocument({ protocol_id: 'bms-manual-pipetting', steps: explicitStep ? [explicitStep] : op ? [draft(op)] : [] });
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
    const [ownSection, setOwnSection] = useState<PipetteSection>('Move & pipette');
    const category = section ?? ownSection;
    const categories: Record<PipetteSection, Operation[]> = {
        'Move & pipette': ['move', 'lower', 'lift', 'aspirate', 'dispense', 'mix'],
        Tips: ['load_tip', 'source_load_tips'],
        Calibration: ['measure_fluid_height', 'source_fluid_offset', 'diagnostic_detect_fluid', 'source_calwith_fluid'],
        Advanced: ['diagnostic_pipette', 'source_mix', 'source_aspirate_air', 'source_dispense_air', 'source_purge'],
    };
    const show = (...ops: Operation[]) => ops.some(op => categories[category].includes(op));
    return <section aria-label="Well pipetting" className={'min-w-0 space-y-3 [&_select]:max-w-full [&_select]:rounded [&_select]:bg-slate-950 [&_select]:p-2 [&_input[type=number]]:min-w-0 [&_input[type=number]]:rounded [&_input[type=number]]:bg-slate-950 [&_input[type=number]]:p-2'}>
        <>
            {section === undefined && <div role="tablist" className="flex flex-wrap gap-2" aria-label="Pipette sections">{PIPETTE_SECTIONS.map(name => <button type="button" role="tab" aria-selected={category === name} aria-pressed={category === name} key={name} onClick={() => setOwnSection(name)}>{name}</button>)}</div>}
            <section hidden={category !== 'Tips'} aria-label="Tip ejection" className="space-y-2 rounded border border-slate-700 p-3">
                <h3 className="font-semibold">Tip ejection</h3>
                <p className="text-sm">Releases tips at the current head position. Move over waste first; these buttons do not move the head.</p>
                <fieldset><legend className="text-sm">Pipettes to eject</legend><div className="flex flex-wrap gap-4">
                    {[0, 1, 2, 3].map(channel => <label key={channel}><input type="checkbox" aria-label={`Eject pipette ${channel + 1}`} checked={channels.includes(channel)}
                        onChange={e => setChannels(current => e.target.checked ? [...current, channel].sort() : current.filter(c => c !== channel))} /> Pipette {channel + 1}</label>)}
                </div></fieldset>
                <p className="text-xs">Shared with the liquid-stroke selection below. Only tips detected on the requested pipettes receive an eject command.</p>
                <div className="flex flex-wrap gap-2">
                    <button type="button" disabled={!enabled} onClick={() => void run(undefined, { operation: 'diagnostic_pipette', diagnostic: { action: 'eject', channels: [0, 1, 2, 3] } })}
                        className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">Eject all tips</button>
                    <button type="button" disabled={!enabled || channels.length === 0} onClick={() => void run(undefined, { operation: 'diagnostic_pipette', diagnostic: { action: 'eject', channels: [...channels] } })}
                        title={channels.length === 0 ? 'Choose pipettes above, or use Eject all tips.' : undefined}
                        className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">Eject selected tips</button>
                </div>
            </section>
        </>
        <section className='space-y-3' aria-label="Step settings">
        {category === 'Move & pipette' && <p className="text-sm text-slate-400">Move puts the head over a station and reference well. Lower and Lift act in place. Strokes act on the ticked pipettes; tips keep their fixed spacing.</p>}
        <div hidden={!show('move', 'lower', 'lift')} className="grid gap-3 sm:grid-cols-4">
            <label>Station<select aria-label="Station" className="block w-full bg-slate-950 p-2" value={destinations.some(d => String(d.location_id) === location) ? location : ''} onChange={e => setLocation(e.target.value)}>
                <option value="">Select a station</option>
                {destinations.map(d => <option key={d.target} value={d.location_id}>{d.label}</option>)}
            </select></label>
            <label hidden={!show('move')}>Reference well<select aria-label="Reference well" className="block w-full bg-slate-950 p-2" value={well} onChange={e => setWell(e.target.value)}>
                <option value="">Select a well</option>{wells.map(value => <option key={value}>{value}</option>)}
            </select></label>
            <label hidden={!show('move')}>Move Z position<select aria-label="Move Z position" className="block w-full bg-slate-950 p-2" value={flag} onChange={e => setFlag(e.target.value)}>
                <option value="">Select height</option><option value="0">Clearance height</option><option value="1">Calibrated high</option><option value="2">Calibrated low</option>
            </select></label>
            <details className="text-xs text-slate-300"><summary>Location number</summary>
                <label>Location number<input aria-label="Location number" className="block w-full bg-slate-950 p-2" type="number" step="1" value={location} onChange={e => setLocation(e.target.value)} /></label>
                <p>Calibration revision: {positionTableRevision ?? 'unavailable'}. Not every well is usable at every station.</p>
            </details>
        </div>
        <div hidden={!show('lift')} className="grid gap-3 sm:grid-cols-3">
            <label>Lift target<select aria-label="Lift target" className="block w-full bg-slate-950 p-2" value={liftMode} onChange={e => setLiftMode(e.target.value)}>
                <option value="">Select calibrated target</option><option value="high">Calibrated high</option><option value="height">Height above calibrated low</option>
            </select></label>
            {liftMode === 'height' && <label>Lift height (steps)<input aria-label="Lift height (steps)" type="number" step="1" value={height} onChange={e => setHeight(e.target.value)} className="block w-full bg-slate-950 p-2" /></label>}
            <p className="text-sm">Lower and Lift move vertically at the current position, not to the selected well.</p>
        </div>
        <fieldset hidden={!show('aspirate', 'dispense', 'mix')}><legend>Pipettes for strokes</legend><div className="flex flex-wrap gap-4">
            {[0, 1, 2, 3].map(channel => <label key={channel}><input type="checkbox" aria-label={`Plunger ${channel + 1}`} checked={channels.includes(channel)} onChange={e => setChannels(current => e.target.checked ? [...current, channel].sort() : current.filter(c => c !== channel))} /> Pipette {channel + 1}</label>)}
        </div></fieldset>
        <div hidden={!show('aspirate', 'dispense', 'mix')} className="grid gap-3 sm:grid-cols-4">
            {([['Volume (µL)', volume, setVolume], ['Aspirate speed', aspirateSpeed, setAspirateSpeed], ['Dispense speed', dispenseSpeed, setDispenseSpeed], ['Mix cycles', cycles, setCycles]] as const).map(([name, value, setter]) =>
                <label key={name}>{name}<input aria-label={name} className="block w-full bg-slate-950 p-2" type="number" step={name === 'Mix cycles' ? '1' : 'any'} value={value} onChange={e => setter(e.target.value)} /></label>)}
        </div>
        <p hidden={!show('mix')} className="text-xs">Mix repeats the selected aspiration and dispense strokes (1–50 cycles). Speeds use controller units.</p>
        <div className="flex flex-wrap gap-2">{operations.filter(op => ['move', 'lower', 'lift', 'aspirate', 'dispense', 'mix'].includes(op) && show(op)).map(op => <button type="button" key={op} disabled={!enabled} onClick={() => void run(op)} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">{label(op)} now</button>)}</div>
        <section hidden={!show('load_tip')} aria-label="Tray pickup" className="rounded border border-slate-700 p-3">
            <h3 className="font-semibold">Pick up tips</h3>
            <p className="my-2 text-sm">Picks up a tip from the selected tray and well. This moves the head.</p>
            <div className="my-3 grid gap-3 sm:grid-cols-2">
            <label>Tip tray<select aria-label="Tip tray" value={tray} onChange={e => setTray(e.target.value)}>{[1,2,3,4].map(n => <option key={n}>{n}</option>)}</select></label>
            <label>Tip well<select aria-label="Tip well" value={tipWell} onChange={e => setTipWell(e.target.value)}>{wells.filter(w => /^[AB]/.test(w)).map(w => <option key={w}>{w}</option>)}</select></label>
            <label><input aria-label="Overpress" type="checkbox" checked={overpress} onChange={e => setOverpress(e.target.checked)} />Overpress</label>
            <label><input aria-label="Lift Z after pickup" type="checkbox" checked={liftZ} onChange={e => setLiftZ(e.target.checked)} />Lift Z after pickup</label>
            </div>
        <div className="flex flex-wrap gap-2">{(['load_tip'] as Operation[]).map(op => <button type="button" key={op} disabled={!enabled} onClick={() => void run(op)} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">{op === 'load_tip' ? 'Pick up from tray' : `${label(op)} now`}</button>)}</div>
        </section>
        <details hidden={!show('measure_fluid_height','source_fluid_offset','diagnostic_detect_fluid','source_calwith_fluid')} className="rounded border border-slate-700 p-3">
            <summary className="cursor-pointer font-semibold">OEM fluid calibration routines</summary>
            <div className="my-3 grid gap-3 sm:grid-cols-2">
            <label hidden={!show('measure_fluid_height','source_fluid_offset')}>Detection speed<input aria-label="Detection speed" type="number" step="1" value={detectionSpeed} onChange={e => setDetectionSpeed(e.target.value)} /></label>
            <label hidden={!show('source_fluid_offset')}>Offset scan plate<select aria-label="Offset scan plate" value={scanPlate} onChange={e => setScanPlate(e.target.value as typeof scanPlate)}>{(['TC', 'MS', 'OC', 'RC', 'STRIP', 'OCMS'] as const).map(plate => <option key={plate}>{plate}</option>)}</select></label>
            <label hidden={!show('source_fluid_offset')}><input aria-label="Prefill scan plate" type="checkbox" checked={scanPrefill} onChange={e => setScanPrefill(e.target.checked)} />Prefill from trough (OEM)</label>
            <label hidden={!show('source_fluid_offset')}>Sample every N wells<input aria-label="Sample every N wells" type="number" min="1" step="1" value={scanSpacing} onChange={e => setScanSpacing(e.target.value)} /></label>
            </div>
            <p className="my-2 text-sm">Measure height at the current well, or scan the selected plate. Prefill transfers liquid. Detect Fluid moves the pool plate, scans five stations and parks on success. Neither diagnostic saves calibration.</p>
            <p className="my-2 text-sm">Calibrate with fluid saves and applies calibration, including partial results. Reject restores the FULL pre-run calibration, replacing later edits. These controls do not restart or home the robot.</p>
        <div className="flex flex-wrap gap-2">{(['measure_fluid_height', 'source_fluid_offset', 'diagnostic_detect_fluid', 'source_calwith_fluid'] as Operation[]).map(op => <button type="button" key={op} disabled={!enabled} onClick={() => void run(op)} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">{label(op)} now</button>)}</div>
        </details>
        <BioXpSourcePipettingEditor drafts={sourceDrafts} onChange={updateSource} enabled={enabled} run={op => void run(op)} visibleOperations={categories[category]} />
        <BioXpPipetteNativeTools visible={visible && category === 'Advanced'} enabled={enabled} run={(action, inputs) => void run(undefined, undefined, { action, inputs })} />
        </section>
        {pending && <p role="status">Submitting pipetting program…</p>}
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
        {category === 'Calibration' && <details className="rounded border border-slate-700 p-3 text-sm"><summary className="cursor-pointer font-semibold">Earlier calibration run</summary>
            <BioXpCalibrationRun key={`recover:${generation}`} generation={generation} connected={connected} /></details>}
        {error && <p role="alert">{error}</p>}
    </section>;
}
