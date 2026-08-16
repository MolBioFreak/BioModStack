import { useState } from 'react';

import {
    type BioXpOperatorDashboard,
    type BioXpPipetteApplicationOperation,
    type BioXpPipetteApplicationPlanRequest,
    bioXpErrorText,
    useBioXpPipetteApplicationStatus,
    usePlanBioXpPipetteApplication,
} from '../lib/bioxpClient';

const operations: Array<{ value: BioXpPipetteApplicationOperation; label: string }> = [
    { value: 'load_tip', label: 'Load tip workflow' },
    { value: 'move_to_waste', label: 'Move to waste' },
    { value: 'detect_fluid', label: 'Detect fluid' },
    { value: 'plunger_up', label: 'Plunger up' },
    { value: 'plunger_down', label: 'Plunger down' },
];

const boolText = (value: boolean | null | undefined) => value === true ? 'true' : value === false ? 'false' : 'unknown';
const valueText = (value: unknown) => value === null || value === undefined ? 'unknown' : String(value);

export function BioXpPipetteControlPanel({
    generation,
    connected,
    pipettes,
}: {
    generation: number;
    connected: boolean;
    pipettes: BioXpOperatorDashboard['pipettes'] | undefined;
}) {
    const status = useBioXpPipetteApplicationStatus(generation, connected);
    const planner = usePlanBioXpPipetteApplication();
    const [operation, setOperation] = useState<BioXpPipetteApplicationOperation>('load_tip');
    const [tipTray, setTipTray] = useState('');
    const [tipWell, setTipWell] = useState('');
    const [tipType, setTipType] = useState('0');
    const [tipLocation, setTipLocation] = useState('0');
    const [fluidClass, setFluidClass] = useState<'TC' | 'MS' | 'OC' | 'RC' | 'STRIP'>('RC');

    const buildPlan = () => {
        const request: BioXpPipetteApplicationPlanRequest = { operation };
        if (operation === 'load_tip') {
            request.tip_tray = tipTray;
            request.tip_well = tipWell;
            request.tip_type = Number(tipType);
            request.tip_location = Number(tipLocation);
            request.home_z_after = true;
        }
        if (operation === 'detect_fluid') request.fluid_class = fluidClass;
        planner.mutate(request);
    };

    const unavailable = !connected || status.data?.execution_admitted !== false;
    const blocker = status.data?.blocker ?? 'Pipette application status is unavailable.';

    return (
        <section className="mt-4 rounded border border-fuchsia-800/60 bg-fuchsia-950/15 p-3" data-bioxp-pipette-control-panel>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h3 className="font-semibold text-fuchsia-100">Four-Channel Pipette Control</h3>
                    <p className="mt-1 text-xs text-slate-400">Controlsuite application workflow planner. Physical execution remains closed in this tranche.</p>
                </div>
                <span className="rounded border border-amber-700/60 bg-amber-950/30 px-2 py-1 text-xs text-amber-200">plan only · no motion</span>
            </div>

            <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                {(pipettes?.channels ?? []).map((channel) => (
                    <article key={channel.channel} className="rounded border border-slate-700 bg-slate-950/70 p-2 text-xs" data-pipette-channel={channel.channel}>
                        <strong className="text-cyan-100">Channel {channel.channel}</strong>
                        <dl className="mt-1 grid grid-cols-2 gap-x-2 text-slate-300">
                            <dt>available</dt><dd>{boolText(channel.available)}</dd>
                            <dt>initialized</dt><dd>{boolText(channel.initialized)}</dd>
                            <dt>tip loaded</dt><dd>{boolText(channel.tip_loaded)}</dd>
                            <dt>tip location</dt><dd>{valueText(channel.tip_location)}</dd>
                            <dt>liquid µL</dt><dd>{valueText(channel.liquid_level_ul)}</dd>
                            <dt>front air µL</dt><dd>{valueText(channel.front_air_level_ul)}</dd>
                            <dt>rear air µL</dt><dd>{valueText(channel.rear_air_level_ul)}</dd>
                            <dt>pressure</dt><dd>{valueText(channel.pressure)}</dd>
                        </dl>
                        {channel.last_error && <p className="mt-1 text-red-300">{channel.last_error}</p>}
                    </article>
                ))}
                {(pipettes?.channels ?? []).length === 0 && <p className="text-xs text-slate-400">No pipette channel projection is available.</p>}
            </div>

            <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
                <div><dt className="text-slate-500">truth source</dt><dd>{pipettes?.truth_source ?? 'unknown'}</dd></div>
                <div><dt className="text-slate-500">live query</dt><dd>{boolText(pipettes?.live_query_performed)}</dd></div>
                <div><dt className="text-slate-500">controller ACK</dt><dd>{boolText(pipettes?.controller_acknowledged)}</dd></div>
                <div><dt className="text-slate-500">completion</dt><dd>{boolText(pipettes?.completion_verified)}</dd></div>
            </dl>

            <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <label className="text-xs text-slate-300">Workflow
                    <select value={operation} onChange={(event) => setOperation(event.target.value as BioXpPipetteApplicationOperation)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2">
                        {operations.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
                    </select>
                </label>
                {operation === 'load_tip' && <>
                    <label className="text-xs text-slate-300">Tip tray<input value={tipTray} onChange={(event) => setTipTray(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2" /></label>
                    <label className="text-xs text-slate-300">Tip well<input value={tipWell} onChange={(event) => setTipWell(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2" /></label>
                    <label className="text-xs text-slate-300">Tip type<input type="number" value={tipType} onChange={(event) => setTipType(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2" /></label>
                    <label className="text-xs text-slate-300">Tip location<select value={tipLocation} onChange={(event) => setTipLocation(event.target.value)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2">{[0, 1, 2, 3].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
                </>}
                {operation === 'detect_fluid' && <label className="text-xs text-slate-300">Fluid offset class<select value={fluidClass} onChange={(event) => setFluidClass(event.target.value as typeof fluidClass)} className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2">{['TC', 'MS', 'OC', 'RC', 'STRIP'].map((value) => <option key={value}>{value}</option>)}</select></label>}
            </div>

            <button type="button" disabled={unavailable || planner.isPending} onClick={buildPlan} className="mt-3 rounded bg-fuchsia-700 px-3 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50">Build no-motion plan</button>
            <p className="mt-2 text-xs text-amber-200">Safety gate: {blocker}</p>
            {status.error && <p className="mt-2 text-xs text-red-300">Status error: {bioXpErrorText(status.error)}</p>}
            {planner.error && <p className="mt-2 text-xs text-red-300">Plan error: {bioXpErrorText(planner.error)}</p>}
            {planner.data && (
                <article className="mt-3 rounded border border-slate-700 bg-slate-950/70 p-3 text-xs" data-pipette-application-plan>
                    <h4 className="font-semibold text-fuchsia-100">{planner.data.operation} plan</h4>
                    <p className="mt-1">motion_commanded={String(planner.data.motion_commanded)} · controller_acknowledged={String(planner.data.controller_acknowledged)} · completion_verified={String(planner.data.completion_verified)} · physical_effect_verified={String(planner.data.physical_effect_verified)}</p>
                    <ol className="mt-2 list-decimal space-y-1 pl-5">{planner.data.steps.map((step, index) => <li key={`${index}:${String(step.action ?? '')}`}>{String(step.action ?? 'step')}</li>)}</ol>
                    <details className="mt-2"><summary>OEM plan evidence</summary><pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap text-[11px] text-slate-400">{JSON.stringify(planner.data, null, 2)}</pre></details>
                </article>
            )}
        </section>
    );
}
