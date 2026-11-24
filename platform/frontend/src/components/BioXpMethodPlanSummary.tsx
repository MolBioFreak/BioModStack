import { useState } from 'react';
import type { MethodCompile, MethodFinding, MethodValue } from '../lib/bioxpMethods';
import { findMethodNode, object } from './BioXpMethodFields';

export const COVERS: Record<string, string> = { OUTPUT_COVER: 'Output cover', REAGENT_COVER: 'Reagent cover', BIO_SECURITY_COVER: 'Biosecurity cover' };
const PLACES: Record<string, string> = {
    LOC_OC_COVER: 'on the output chiller', LOC_OC_COVER_STORAGE: 'output cover storage', LOC_RC_COVER: 'on the reagent chiller',
    LOC_RC_COVER_STORAGE: 'reagent cover storage', LOC_BSC: 'on the thermal cycler', LOC_BSCS: 'biosecurity cover storage', UNKNOWN: 'unknown',
};
export const coverPlace = (location: unknown) => PLACES[String(location)] ?? String(location ?? 'not recorded').replaceAll('_', ' ').toLowerCase();
const rows = (value: unknown): MethodValue[] => Array.isArray(value) ? value.map(object) : [];
const decimal = (value: unknown): number | null => { const n = typeof value === 'number' ? value : typeof value === 'string' && value.trim() ? Number(value) : NaN; return Number.isFinite(n) ? n : null; };
const amount = (n: number) => `${Math.round(n * 100) / 100} µL`;

/** Occurrence → authored step label, for plain-language advisories and summaries. */
export function occurrenceSteps(result: MethodCompile | undefined, method: MethodValue): Map<string, { stepId: string; label: string; path: string }> {
    const steps = Array.isArray(method.steps) ? method.steps as MethodValue[] : [];
    const map = new Map<string, { stepId: string; label: string; path: string }>();
    for (const p of result?.provenance ?? []) {
        const node = findMethodNode(steps, String(p.step_id ?? ''));
        const label = String(node?.label || node?.action || p.step_id || 'Step').replaceAll('_', ' ');
        map.set(String(p.occurrence_id), { stepId: String(p.step_id ?? ''), label, path: String(p.path ?? '').replace(/^\/method/, '') });
    }
    return map;
}

/** Compiler issues plus simulation advisories, addressed to the step they concern. */
export function methodStepFindings(result: MethodCompile | undefined, method: MethodValue): MethodFinding[] {
    if (!result) return [];
    const steps = occurrenceSteps(result, method);
    const simulation = rows(object(result.simulation).issues).map(issue => {
        const step = steps.get(String(issue.occurrence_id));
        return { code: String(issue.code ?? 'advisory'), category: String(issue.category ?? 'advisory'), message: String(issue.message ?? ''),
            ...(step ? { step_id: step.stepId, path: `${step.path}/` } : {}) } as MethodFinding;
    });
    const compiled = (result.issues ?? []).map(issue => ({ ...issue, ...(typeof issue.path === 'string' && issue.path.startsWith('/method/steps/') ? { path: `${issue.path.replace(/^\/method/, '')}/` } : {}) }));
    return [...compiled, ...simulation];
}

/** The planned run in numbers: one model for the summary card and the notes card. */
function planModel(result: MethodCompile, method: MethodValue) {
    const simulation = object(result.simulation), state = object(simulation.state), initial = object(simulation.initial_state);
    const steps = occurrenceSteps(result, method);
    const deck = object(method.deck_plan);
    const materials = new Map(rows(deck.materials).map(m => [String(m.id), String(m.name || m.id)]));
    // Tips per rack.
    const racks = Object.entries(object(state.tip_rack_summary)).map(([tray, raw]) => {
        const summary = object(raw), start = object(object(initial.tip_racks)[tray]);
        const before = Array.isArray(start.occupancy) ? start.occupancy.filter(Boolean).length : null;
        const left = decimal(summary.tips_remaining);
        return { tray, left, used: before !== null && left !== null ? before - left : null };
    });
    // Before the method has runnable steps the simulator reports nothing; show the starting deck as authored.
    const authoredRacks = rows(deck.tip_racks).map(r => ({ tray: String(r.tray), tips: r.tips === 'full' ? 'full' : r.tips === 'empty' ? 'empty' : `${Array.isArray(object(r.tips).available) ? (object(r.tips).available as unknown[]).length : 0} tips`, type: typeof r.tip_type === 'string' ? r.tip_type : null }));
    // Cover sequence.
    const startCovers = Object.entries(Object.keys(object(initial.object_locations)).length ? object(initial.object_locations) : object(deck.movable_objects)).filter(([name]) => COVERS[name]);
    const coverMoves = rows(simulation.after_occurrences).flatMap(row => Object.entries(object(object(row.state_delta).object_locations))
        .filter(([name]) => COVERS[name]).map(([name, location]) => ({ step: steps.get(String(row.occurrence_id))?.label ?? 'Step', name, location })));
    // Volumes moved per material.
    const moved = new Map<string, number>();
    for (const line of rows(simulation.lineage)) {
        const volume = decimal(line.volume_ul), names = Array.isArray(line.materials) ? line.materials.map(String) : [];
        if (line.kind !== 'liquid' || volume === null) continue;
        const key = names.length === 1 ? names[0] : names.length ? 'mixture' : 'unassigned';
        moved.set(key, (moved.get(key) ?? 0) + volume);
    }
    // Liquid moved when wells have no named labware: the recipe totals.
    const acctLiquid = decimal(object(object(simulation.accounting).liquid).known_ul);
    if (!moved.size && acctLiquid !== null && acctLiquid > 0) moved.set('unassigned', acctLiquid);
    const planned = new Map<string, number>();
    for (const a of rows(deck.assignments)) { const v = decimal(a.volume_ul); if (v !== null) planned.set(String(a.material_id), (planned.get(String(a.material_id)) ?? 0) + v); }
    const volumeRows = [...new Set([...planned.keys(), ...moved.keys()])].map(id => ({ id, name: id === 'mixture' ? 'Mixtures' : id === 'unassigned' ? 'Liquid without a declared material' : materials.get(id) ?? id, planned: planned.get(id), moved: moved.get(id) }));
    // Time. Steps the simulator cannot time still have a known plunger time from their recipe.
    const recipes = rows(object(rows(object(result.document).stages)[0]).actions).map(a => object(object(a.params).recipe)).filter(r => Object.keys(r).length);
    const plungerMs = recipes.reduce((t, r) => t + (decimal(object(object(r.liquid_settings).timeout_default).expected_ms) ?? 0), 0);
    const time = object(simulation.time), simMs = decimal(time.known_duration_ms), unknownSteps = Array.isArray(time.unknown_occurrences) ? time.unknown_occurrences.length : 0;
    const ms = (simMs ?? 0) + plungerMs;
    const water = Array.isArray(result.water_substitutions) ? result.water_substitutions.length : 0;
    const unaddressed = methodStepFindings(result, method).filter(f => !f.step_id && !String(f.path ?? '').startsWith('/steps/'));
    // Same message for several settings reads as one line naming each setting.
    const setting = (path: unknown) => String(path ?? '').replace(/^\//, '').replace(/_(ul_s|ul|ms)$/, '').replaceAll('_', ' ');
    const grouped = new Map<string, string[]>();
    for (const f of unaddressed) grouped.set(String(f.message), [...(grouped.get(String(f.message)) ?? []), setting(f.path)]);
    return { racks, authoredRacks, startCovers, coverMoves, volumeRows, ms, plungerMs, unknownSteps, water, grouped };
}

export const methodGeneralNoteCount = (result: MethodCompile, method: MethodValue) => planModel(result, method).grouped.size;

/** Plain summaries of the planned run. Planned values, not robot observations. */
export function BioXpMethodPlanSummary({ result, method, stale }: { result: MethodCompile; method: MethodValue; stale?: boolean }) {
    const { racks, authoredRacks, startCovers, coverMoves, volumeRows, ms, plungerMs, unknownSteps, water } = planModel(result, method);
    const [showStill, setShowStill] = useState(false);
    const moved = volumeRows.filter(v => v.moved !== undefined), still = volumeRows.filter(v => v.moved === undefined);
    const volumeLine = (v: typeof volumeRows[number]) => <li key={v.id}>{v.name}: {v.moved !== undefined ? `${amount(v.moved)} moved` : 'not moved'}{v.planned !== undefined ? ` · ${amount(v.planned)} on the starting deck` : ''}</li>;
    return <section aria-label="Plan summary" className="bioxp-plan-summary">
        <h3>Run plan <small>planned values, not robot measurements</small></h3>
        {stale && <p role="status">Updating for your latest edits…</p>}
        <p>{result.document ? 'Ready to run as written.' : 'Not runnable yet — see the notes.'}</p>
        <h4>Time</h4>
        <p>{ms > 0 ? (ms < 60000 ? `${Math.round(ms / 100) / 10} s` : `${Math.round(ms / 60000 * 10) / 10} min`) : 'No timed steps'}{plungerMs > 0 ? ' pipetting (plunger only; head moves not included)' : ''}{unknownSteps && !plungerMs ? (ms > 0 ? ' of timed holds and waits; robot moves and pipetting are not timed, so the full run takes longer' : '; robot moves and pipetting are not timed') : ''}</p>
        <h4>Tips</h4>
        {racks.length ? <ul>{racks.map(r => <li key={r.tray}>Tip rack {r.tray}: {r.used ?? '?'} used, {r.left ?? '?'} left</li>)}</ul> : authoredRacks.length ? <ul>{authoredRacks.map(r => <li key={r.tray}>Tip rack {r.tray}: {r.tips}{r.type ? ` · ${r.type}` : ''} at start</li>)}</ul> : <p>No tip racks set on the starting deck.</p>}
        <h4>Covers</h4>
        {startCovers.length || coverMoves.length ? <>{startCovers.map(([name, location]) => <p key={`start-${name}`} className="bioxp-plan-start">Start: {COVERS[name]} {coverPlace(location)}</p>)}
            {coverMoves.length > 0 && <ol>{coverMoves.map((m, i) => <li key={i} title={m.step}>{COVERS[m.name]} → {coverPlace(m.location)}</li>)}</ol>}</> : <p>No cover positions set or moved.</p>}
        <h4>Volumes</h4>
        {volumeRows.length ? <>{still.length > 0 && <p>{still.length} material{still.length === 1 ? '' : 's'}: not moved <button type="button" aria-expanded={showStill} onClick={() => setShowStill(!showStill)}>{showStill ? 'Hide' : 'Show all'}</button></p>}
            {showStill && <ul className="bioxp-plan-still">{still.map(volumeLine)}</ul>}
            {moved.length > 0 && <ul>{moved.map(volumeLine)}</ul>}</> : <p>No liquid moves.</p>}
        {water > 0 && <><h4>Liquid settings</h4><p>{water} unspecified setting{water === 1 ? '' : 's'} use the matching Water class.</p></>}
        <small>Notes are advice only; they never block Run.</small>
    </section>;
}

/** General notes, one line each; a note repeated for several settings lists each setting once with its count. */
export function BioXpMethodPlanNotes({ result, method }: { result: MethodCompile; method: MethodValue }) {
    const { grouped } = planModel(result, method);
    return <section aria-label="General notes" className="bioxp-plan-notes">
        <h3>Notes <small>{grouped.size}</small><small>advice only; they never block Run</small></h3>
        {grouped.size === 0 && <p>No general notes.</p>}
        <ul>{[...grouped].map(([message, fields]) => {
            if (fields.length === 1 && fields[0]) return <li key={message}>{fields[0]}: {message}</li>;
            if (fields.length < 2 || !fields.every(Boolean)) return <li key={message}>{message}</li>;
            const counts = new Map<string, number>();
            for (const field of fields) counts.set(field, (counts.get(field) ?? 0) + 1);
            return <li key={message}>{message.replace(/\.$/, '')}<span className="bioxp-plan-chips">{[...counts].map(([field, n]) => <em key={field}>{field}{n > 1 ? ` × ${n}` : ''}</em>)}</span></li>;
        })}</ul>
    </section>;
}
