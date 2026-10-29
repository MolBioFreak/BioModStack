import type { MethodCompile, MethodFinding, MethodValue } from '../lib/bioxpMethods';
import { findMethodNode, object } from './BioXpMethodFields';

const COVERS: Record<string, string> = { OUTPUT_COVER: 'Output cover', REAGENT_COVER: 'Reagent cover', BIO_SECURITY_COVER: 'Biosecurity cover' };
const PLACES: Record<string, string> = {
    LOC_OC_COVER: 'on the output chiller', LOC_OC_COVER_STORAGE: 'output cover storage', LOC_RC_COVER: 'on the reagent chiller',
    LOC_RC_COVER_STORAGE: 'reagent cover storage', LOC_BSC: 'on the thermal cycler', LOC_BSCS: 'biosecurity cover storage', UNKNOWN: 'unknown',
};
const place = (location: unknown) => PLACES[String(location)] ?? String(location ?? 'not recorded').replaceAll('_', ' ').toLowerCase();
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

/** Plain summaries of the planned run. Planned values, not robot observations. */
export function BioXpMethodPlanSummary({ result, method, stale }: { result: MethodCompile; method: MethodValue; stale?: boolean }) {
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
    // Cover sequence.
    const startCovers = Object.entries(object(initial.object_locations)).filter(([name]) => COVERS[name]);
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
    const planned = new Map<string, number>();
    for (const a of rows(deck.assignments)) { const v = decimal(a.volume_ul); if (v !== null) planned.set(String(a.material_id), (planned.get(String(a.material_id)) ?? 0) + v); }
    const volumeRows = [...new Set([...planned.keys(), ...moved.keys()])].map(id => ({ id, name: id === 'mixture' ? 'Mixtures' : id === 'unassigned' ? 'Liquid without a declared material' : materials.get(id) ?? id, planned: planned.get(id), moved: moved.get(id) }));
    // Time.
    const time = object(simulation.time), ms = decimal(time.known_duration_ms), unknownSteps = Array.isArray(time.unknown_occurrences) ? time.unknown_occurrences.length : 0;
    const water = Array.isArray(result.water_substitutions) ? result.water_substitutions.length : 0;
    const unaddressed = methodStepFindings(result, method).filter(f => !f.step_id && !String(f.path ?? '').startsWith('/steps/'));
    return <section aria-label="Plan summary" className="bioxp-plan-summary">
        <h3>Plan summary</h3>
        {stale && <p role="status">Updating for your latest edits…</p>}
        <p>{result.document ? 'Ready to run as written.' : 'Not runnable yet — see the notes below.'} Planned values, not robot measurements.</p>
        <dl>
            <div><dt>Estimated time</dt><dd>{ms !== null && ms > 0 ? `${Math.round(ms / 60000 * 10) / 10} min` : 'No timed steps'}{unknownSteps ? ` · ${unknownSteps} step${unknownSteps === 1 ? '' : 's'} without an estimate` : ''}</dd></div>
            <div><dt>Tips</dt><dd>{racks.length ? <ul>{racks.map(r => <li key={r.tray}>Tip rack {r.tray}: {r.used ?? '?'} used, {r.left ?? '?'} left</li>)}</ul> : 'No tip racks set on the starting deck.'}</dd></div>
            <div><dt>Covers</dt><dd>{startCovers.length || coverMoves.length ? <ol>{startCovers.map(([name, location]) => <li key={`start-${name}`}>Start: {COVERS[name]} {place(location)}</li>)}{coverMoves.map((m, i) => <li key={i}>{m.step}: {COVERS[m.name]} → {place(m.location)}</li>)}</ol> : 'No cover positions set or moved.'}</dd></div>
            <div><dt>Volumes</dt><dd>{volumeRows.length ? <ul>{volumeRows.map(v => <li key={v.id}>{v.name}: {v.moved !== undefined ? `${amount(v.moved)} moved` : 'not moved'}{v.planned !== undefined ? ` · ${amount(v.planned)} on the starting deck` : ''}</li>)}</ul> : 'No liquid moves.'}</dd></div>
            {water > 0 && <div><dt>Liquid settings</dt><dd>{water} unspecified setting{water === 1 ? '' : 's'} use the matching Water class.</dd></div>}
        </dl>
        {unaddressed.length > 0 && <section aria-label="General notes"><h4>Notes</h4><ul>{unaddressed.map((f, i) => <li key={i}>{f.message}</li>)}</ul></section>}
        <small>Notes are advice only; they never block Run.</small>
    </section>;
}
