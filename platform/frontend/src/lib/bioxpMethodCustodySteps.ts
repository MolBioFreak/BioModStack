import type { MethodCompile, MethodValue } from './bioxpMethods';
import { methodInitialState } from './bioxpMethodSimulation';
import { deckRegions, deckResources, type DeckBounds } from './bioxpWorkflowDeck';

const object = (v: unknown): MethodValue => v && typeof v === 'object' && !Array.isArray(v) ? v as MethodValue : {};
const rows = (v: unknown): MethodValue[] => Array.isArray(v) ? v.map(object) : [];

export type CustodyStep = { n: number; actionId: string; path: string; kind: 'door' | 'cover' | 'plate' | 'inspect'; object?: string; label?: string; from?: string; to?: string; door?: 'open' | 'closed' };
/** Native action kinds numbered in the stage text and on the deck, in document order. */
export const CUSTODY_KINDS = new Set(['thermal_door', 'move_cover', 'plate_move', 'inspect']);

/** Display-only: ordered door/cover/plate actions with their planned before/after
 * object locations, replayed from the compiler's own simulation deltas. */
export function custodySteps(result: MethodCompile | null | undefined, initialState: unknown, actions: MethodValue[]): CustodyStep[] {
    const simulation = object(result?.simulation);
    const locations: Record<string, unknown> = { ...object(object(methodInitialState(simulation, initialState)).object_locations) };
    const start = object(methodInitialState(simulation, initialState));
    const plates: Record<string, string> = Object.fromEntries(Object.entries(object(start.labware)).map(([id, l]) => [id, String(object(l).station ?? '')]));
    const before = new Map<string, Record<string, unknown>>(), deltas = new Map<string, MethodValue>();
    const plateMoves = new Map<string, { label: string; from?: string; to: string }[]>();
    for (const row of rows(simulation.after_occurrences)) {
        const id = String(row.occurrence_id), delta = object(object(row.state_delta).object_locations);
        before.set(id, { ...locations }); deltas.set(id, delta);
        Object.assign(locations, delta);
        const moved = Object.entries(object(object(row.state_delta).labware)).flatMap(([labware, value]) => {
            const to = object(value).station;
            if (typeof to !== 'string' || plates[labware] === to) return [];
            const from = plates[labware] || undefined; plates[labware] = to;
            return [{ label: String(object(value).name || 'Plate'), from, to }];
        });
        if (moved.length) plateMoves.set(id, moved);
    }
    const steps: CustodyStep[] = [];
    let n = 0;
    for (const action of actions) {
        if (!CUSTODY_KINDS.has(String(action.kind))) continue;
        n++;
        const p = object(action.params), occurrence = String(action.source_occurrence_id ?? '');
        const path = String(object(object(action.metadata).bms_method).path ?? result?.provenance?.find(r => r.occurrence_id === action.source_occurrence_id)?.path ?? '').replace(/^\/method(?=\/)/, '');
        const base = { n, actionId: String(action.action_id), path };
        if (action.kind === 'thermal_door') { steps.push({ ...base, kind: 'door', door: p.door_command === 'DO' ? 'open' : p.door_command === 'DC' ? 'closed' : undefined }); continue; }
        if (action.kind === 'plate_move') {
            const moved = plateMoves.get(occurrence) ?? [];
            if (!moved.length) steps.push({ ...base, kind: 'plate', label: String(p.plate_id ?? 'Plate'), to: typeof p.target_location === 'string' ? p.target_location : undefined });
            for (const m of moved) steps.push({ ...base, kind: 'plate', ...m });
            continue;
        }
        const delta = deltas.get(occurrence) ?? {}, prior = before.get(occurrence) ?? {};
        const moved = Object.keys(delta).filter(k => k.endsWith('_COVER'));
        if (!moved.length) steps.push({ ...base, kind: action.kind === 'inspect' ? 'inspect' : 'cover', ...(action.kind === 'move_cover' && typeof p.target_location === 'string' ? { to: p.target_location } : {}) });
        for (const name of moved) steps.push({ ...base, kind: action.kind === 'inspect' ? 'inspect' : 'cover', object: name, label: COVER_LABELS[name] ?? name, from: prior[name] == null ? undefined : String(prior[name]), to: String(delta[name]) });
    }
    return steps;
}

const ALIAS: Record<string, string> = { LOC_OC_COVER: 'LOC_OC', LOC_P_OC: 'LOC_OC', LOC_P_OC_PRESS: 'LOC_OC', LOC_RC_COVER: 'LOC_RC', LOC_P_RC_PRESS: 'LOC_RC', LOC_P_TC: 'LOC_TC', LOC_P_TC_PRESS: 'LOC_TC', LOC_P_MS: 'LOC_MS', LOC_P_MS_PRESS: 'LOC_MS' };
// The deck has two covers: the output cover (output chiller ↔ output cover storage)
// and the reagent cover (reagent chiller ↔ reagent cover storage).
export const COVER_LABELS: Record<string, string> = { OUTPUT_COVER: 'Output cover', REAGENT_COVER: 'Reagent cover' };
const PLACE_NAMES: Record<string, string> = { LOC_OC_COVER: 'on the output chiller', LOC_RC_COVER: 'on the reagent chiller', LOC_OC_COVER_STORAGE: 'output cover storage', LOC_RC_COVER_STORAGE: 'reagent cover storage' };
export const stationOf = (location: string | undefined) => location ? ALIAS[location] ?? location : undefined;
export function placeName(location: string | undefined): string {
    if (!location) return 'unknown location';
    const station = stationOf(location)!;
    return PLACE_NAMES[location] ?? (deckResources.find(r => r.id === station)?.label ?? deckRegions.find(r => r.id === station)?.label ?? location).toLowerCase();
}

/** Drawing bounds for a native object location token (station housing or cover storage). */
export function placeBounds(location: string | undefined): DeckBounds | undefined {
    if (!location) return undefined;
    const station = ALIAS[location] ?? location;
    const resource = deckResources.find(r => r.id === station);
    if (resource) { const b = resource.bounds, strip = resource.kind === 'strip'; return { x: b.x - (strip ? 10 : 21), y: b.y - (strip ? 17 : 44), width: b.width + (strip ? 20 : 42), height: b.height + (strip ? 34 : 66) }; }
    return deckRegions.find(r => r.id === station)?.bounds;
}

/** Last planned cycler door command through the given actions; unknown before any door step. */
export function doorAfter(actions: MethodValue[]): 'open' | 'closed' | null {
    let state: 'open' | 'closed' | null = null;
    for (const a of actions) if (a.kind === 'thermal_door') { const c = object(a.params).door_command; state = c === 'DO' ? 'open' : c === 'DC' ? 'closed' : state; }
    return state;
}
