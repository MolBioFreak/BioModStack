import { useEffect, useRef, useState } from 'react';
import { bioXpErrorText, type BioXpTipTraysV1 } from '../lib/bioxpClient';
import { tipSize } from '../lib/bioxpDeckRecord';
import { compileImmediate, useBioXpImmediateRun } from '../lib/bioxpImmediateRun';
import { LIVE_SPEED_DEFAULTS, contentsAfterRoute, firstTipGroup, liveRouteSteps, routeLetter,
    type DeckContents, type LiveRouteSettings, type LiveRouteStop, type PlateGeometry } from '../lib/bioxpLiveRoute';
import type { MethodValue } from '../lib/bioxpMethods';
import { BioXpPipetteResults } from './BioXpPipetteResults';

const TRAYS = [1, 2, 3, 4];

/** Route built from clicked wells, run through the Workflows compiler and the ordinary protocol owner. */
export function BioXpLiveDeckPipette({ generation, connected, visible, route, onRoute, stopLabel, tipTrays, plateAt, contents, geometry, onFinished }: {
    generation: number; connected: boolean; visible: boolean;
    route: LiveRouteStop[]; onRoute: (route: LiveRouteStop[]) => void; stopLabel: (stop: LiveRouteStop) => string;
    tipTrays: BioXpTipTraysV1 | undefined; plateAt: Record<string, string>; contents: DeckContents; geometry: Record<string, PlateGeometry>;
    /** Called once when a route job completes, with the recorded amounts it changed. */
    onFinished: (changed: Record<string, Record<string, number>>) => void;
}) {
    const runner = useBioXpImmediateRun(generation, connected, visible);
    const row = (tray: number) => tipTrays?.trays.find(item => item.tray === tray);
    const firstStocked = TRAYS.find(tray => firstTipGroup(row(tray)) !== null) ?? null;
    const [trayChoice, setTrayChoice] = useState<number | 'loaded' | null>(null);
    const tray = trayChoice === 'loaded' ? null : trayChoice ?? firstStocked;
    const [channel, setChannel] = useState(0);
    const [detect, setDetect] = useState(true);
    const [after, setAfter] = useState<'eject' | 'keep'>('eject');
    const size = tray === null ? null : tipSize(row(tray));
    const defaults = size === null ? undefined : LIVE_SPEED_DEFAULTS[size];
    const [speeds, setSpeeds] = useState<{ aspirate?: string; dispense?: string }>({});
    const aspirateSpeed = speeds.aspirate ?? (defaults ? String(defaults.aspirate) : '');
    const dispenseSpeed = speeds.dispense ?? (defaults ? String(defaults.dispense) : '');
    const [preview, setPreview] = useState<string[] | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const settings: LiveRouteSettings = { tray, channel, detect, after, aspirateSpeed, dispenseSpeed };
    const build = (): MethodValue[] => liveRouteSteps(route, settings, { tipWell: tray === null ? null : firstTipGroup(row(tray)), plateAt, contents, geometry });
    const update = (index: number, patch: Partial<LiveRouteStop>) => { setPreview(null); onRoute(route.map((stop, i) => i === index ? { ...stop, ...patch } : stop)); };
    const submitted = useRef<{ job: string | null; route: LiveRouteStop[]; contents: DeckContents } | null>(null);
    const run = () => {
        setProblem(null); setPreview(null);
        let steps: MethodValue[];
        try { steps = build(); } catch (cause) { setProblem(cause instanceof Error ? cause.message : String(cause)); return; }
        submitted.current = { job: null, route: route.map(stop => ({ ...stop })), contents };
        void runner.run(() => compileImmediate('Live deck route', steps));
    };
    const showPreview = async () => {
        setProblem(null);
        try {
            const document = await compileImmediate('Live deck route', build());
            const stages = (Array.isArray(document.stages) ? document.stages : []) as { actions?: { kind?: string; params?: MethodValue }[] }[];
            setPreview(stages.flatMap(stage => stage.actions ?? []).map(action => {
                const params = (action.params ?? {}) as { operation?: string; well?: string; location_id?: number; application?: { operations?: { operation?: string }[] } };
                const detail = params.application?.operations?.map(item => item.operation).join(' + ') ?? params.operation ?? action.kind ?? 'action';
                return params.operation === 'move' ? `move to location ${params.location_id} ${params.well ?? ''}`.trim() : detail.replaceAll('_', ' ');
            }));
        } catch (cause) { setPreview(null); setProblem(cause instanceof Error ? cause.message : bioXpErrorText(cause)); }
    };
    const job = runner.job, status = job?.command?.status ?? job?.status;
    useEffect(() => {
        const pending = submitted.current;
        if (!pending || !job?.job_id || job.command?.terminal !== true || pending.job === job.job_id) return;
        pending.job = job.job_id;
        if (status === 'completed') onFinished(contentsAfterRoute(pending.route, pending.contents));
    }, [job?.job_id, job?.command?.terminal, status]);
    const last = route.length ? routeLetter(route.length - 1) : null;
    return <section className="bx-ui bx-card bld-pipette" aria-label="Pipette route" data-testid="live-deck-pipette">
        <header className="bx-card-head"><h3>Pipette</h3><div className="bx-card-actions">
            <select aria-label="Tip rack" value={trayChoice === 'loaded' ? 'loaded' : tray ?? 'loaded'}
                onChange={event => { setSpeeds({}); setPreview(null); setTrayChoice(event.target.value === 'loaded' ? 'loaded' : Number(event.target.value)); }}>
                {TRAYS.map(item => { const tips = tipSize(row(item)); return <option key={item} value={item} disabled={firstTipGroup(row(item)) === null}>Tip rack {item}{tips ? ` · ${tips} µL` : ''}{firstTipGroup(row(item)) === null ? ' · no group of 4' : ''}</option>; })}
                <option value="loaded">Tip already loaded</option>
            </select>
            <select aria-label="Pipette channel" value={channel} onChange={event => { setPreview(null); setChannel(Number(event.target.value)); }}>
                {[0, 1, 2, 3].map(item => <option key={item} value={item}>Channel {item + 1}</option>)}
            </select>
        </div></header>
        {route.length === 0 ? <p className="bld-hint">Set “Click does” to Pipette, then click wells on the deck in order.</p>
            : <ol className="bld-route">{route.map((stop, index) => <li key={index} data-route-stop={routeLetter(index)}>
                <i>{routeLetter(index)}</i><span title={stopLabel(stop)}>{stopLabel(stop)}</span>
                <select aria-label={`Stop ${routeLetter(index)} action`} value={stop.action} onChange={event => update(index, { action: event.target.value as LiveRouteStop['action'] })}>
                    <option value="aspirate">Aspirate</option><option value="dispense">Dispense</option></select>
                <input type="number" min={0} step="any" aria-label={`Stop ${routeLetter(index)} volume (µL)`} placeholder="0" value={stop.volume} onChange={event => update(index, { volume: event.target.value })} />
                <small>µL</small>
                <button type="button" aria-label={`Remove stop ${routeLetter(index)}`} onClick={() => { setPreview(null); onRoute(route.filter((_, i) => i !== index)); }}>×</button>
            </li>)}</ol>}
        <div className="bld-route-options">
            <label><input type="checkbox" checked={detect} onChange={event => { setPreview(null); setDetect(event.target.checked); }} /> Find liquid by pressure</label>
            <label>Then <select aria-label="After the route" value={after} onChange={event => { setPreview(null); setAfter(event.target.value as 'eject' | 'keep'); }}>
                <option value="eject">Eject tip to waste</option><option value="keep">Keep the tip</option></select></label>
            <label>Aspirate <input type="number" min={0} step="any" aria-label="Aspirate speed (µL/s)" value={aspirateSpeed} onChange={event => { setPreview(null); setSpeeds(current => ({ ...current, aspirate: event.target.value })); }} /> µL/s</label>
            <label>Dispense <input type="number" min={0} step="any" aria-label="Dispense speed (µL/s)" value={dispenseSpeed} onChange={event => { setPreview(null); setSpeeds(current => ({ ...current, dispense: event.target.value })); }} /> µL/s</label>
        </div>
        <div className="bld-route-actions">
            <button type="button" disabled={route.length === 0} onClick={() => void showPreview()}>Preview steps</button>
            <button type="button" className="bx-go" disabled={!runner.enabled || route.length === 0} title={connected ? 'Runs on the robot now.' : 'Connect to the robot first.'} onClick={run}>{last ? `Run A → ${last}` : 'Run'}</button>
            <button type="button" disabled={route.length === 0} onClick={() => { setPreview(null); setProblem(null); onRoute([]); }}>Clear</button>
        </div>
        {problem && <p role="alert">{problem}</p>}
        {preview && <ol className="bld-preview" aria-label="Robot steps">{preview.map((line, index) => <li key={index}>{line}</li>)}</ol>}
        {runner.pending && <p role="status">Submitting route…</p>}
        {runner.error && <p role="alert">{runner.error}</p>}
        {runner.mismatch && <p role="alert">Job identity mismatch; check robot status.</p>}
        {!runner.sameConnection && <p role="alert">Connection changed. Check the earlier job.</p>}
        {runner.query.error != null && <p role="alert">Job readback unavailable: {bioXpErrorText(runner.query.error)}</p>}
        {job && <p role={status === 'failed' ? 'alert' : 'status'} data-testid="live-route-status">Route: {status ?? 'submitted'} · {job.execution?.runtime_state.workflow?.phase ?? 'phase unavailable'}</p>}
        {job?.execution?.runtime_state.workflow?.held_reason && <p role="alert">{job.execution.runtime_state.workflow.held_reason}</p>}
        {job?.execution?.runtime_state.action_results?.map((result, index) => <BioXpPipetteResults key={index} value={result} />)}
        {runner.evidence.map((value, index) => <BioXpPipetteResults key={`evidence-${index}`} value={value} />)}
    </section>;
}
