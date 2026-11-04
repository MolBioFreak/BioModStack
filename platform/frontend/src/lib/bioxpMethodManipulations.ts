import type { MethodCompile, MethodValue } from './bioxpMethods';
import { deckRegions, deckResources, deckStations } from './bioxpWorkflowDeck';

export type ManipulatedWell = { station: string; well: string; channel: number };
export type WellManipulation = { actionId: string; path: string; operation: string; wells: ManipulatedWell[]; notes: string[]; liquid?: 'aspirate' | 'dispense'; hazard?: string };
export type ManipulationLink = { actionId: string; path: string; channel: number; source: ManipulatedWell; destination: ManipulatedWell };
export type MethodManipulations = { events: WellManipulation[]; links: ManipulationLink[] };
const object = (v: unknown): MethodValue => v && typeof v === 'object' && !Array.isArray(v) ? v as MethodValue : {};
const rows = (v: unknown): MethodValue[] => Array.isArray(v) ? v.map(object) : [];
const channels = (v: unknown): number[] | null => Array.isArray(v) && v.every(c => Number.isInteger(c) && c >= 0 && c < 4) ? [...new Set(v as number[])] : null;
const allChannels = [0, 1, 2, 3];
const validAlignment = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v) && v >= -1 && v <= 3;

/** Native move addresses are row-major IDs 0..95, or trimmed A1..H12 labels. */
export function nativeWellAddress(value: unknown): { row: number; column: number } | null {
    const raw = typeof value === 'string' ? value.trim().toUpperCase() : value;
    if (typeof raw === 'number' || typeof raw === 'string' && /^\d+$/.test(raw)) {
        const n = Number(raw);
        return Number.isInteger(n) && n >= 0 && n < 96 ? { row: Math.floor(n / 12), column: n % 12 } : null;
    }
    const match = typeof raw === 'string' ? /^([A-H])([1-9]|1[0-2])$/.exec(raw) : null;
    return match ? { row: match[1].charCodeAt(0) - 65, column: Number(match[2]) - 1 } : null;
}

/** Display-only source scriptmoveTo mapping. Channel selection is NOT alignment.
 * Native 3f1275a: oem_compat/position_table.py, pathing.py; manual pickup uses unadjusted moveTo.
 * Keep this independent of vessel profiles: fixed-head XY is not liquid-volume geometry.
 */
export function nativeChannelFootprint(location: number, well: unknown, selected: number[], alignment: number | null, unadjusted = false) {
    const reference = nativeWellAddress(well), resource = deckResources.find(r => r.locationId === location);
    const exempt = unadjusted || [6, 7, 8, 9, 10, 16].includes(location);
    const a = exempt ? 0 : validAlignment(alignment) ? Math.max(0, alignment) : null;
    const found: ManipulatedWell[] = [], notes: string[] = [];
    if (!reference || a === null) return { wells: found, notes: [`${reference ? 'Head reference is unknown' : 'Native well address is unavailable'} at location ${location}, reference ${String(well)}; selected ${selected.map(c => `P${c + 1}`).join(', ')}.`] };
    for (const channel of selected) {
        const row = reference.row + 2 * (channel - a), column = reference.column;
        const label = row >= 0 && row < 8 ? `${String.fromCharCode(65 + row)}${column + 1}` : null;
        if (label && resource?.wells.includes(label)) found.push({ channel, station: resource.id, well: label });
        else notes.push(`P${channel + 1}: location ${location}, native row ${row}, column ${column + 1} is outside the displayed well grid.`);
    }
    return { wells: found, notes };
}

/** Walk emitted operations in order. No compilation, runtime mutation, liquid accounting or admission. */
export function methodManipulations(result: MethodCompile | null | undefined, alignment: number | null): MethodManipulations {
    const events: WellManipulation[] = [], links: ManipulationLink[] = [], linkKeys = new Set<string>();
    let position: { location: number; well: unknown; alignment: number | null; unadjusted?: boolean } | null = null;
    let loaded: number[] | null = null;
    let motionReference: number | null = alignment, transportReference: number | null = null;
    const tipRacks = object(object(object(result?.simulation).initial_state).tip_racks);
    const aspirated = new Map<number, { wells: ManipulatedWell[]; dispensed: boolean }>();
    const actions = rows(result?.document?.stages).flatMap(s => rows(s.actions));
    for (const action of actions) {
        const actionId = String(action.action_id), metadata = object(object(action.metadata).bms_method);
        const provenance = result?.provenance?.find(p => p.occurrence_id === action.source_occurrence_id);
        const sourcePath = String(metadata.path ?? provenance?.path ?? '');
        const call = rows(provenance?.call_path).find(c => String(c.path).startsWith('/method/steps/'));
        const path = String(call?.path ?? sourcePath).replace(/^\/method(?=\/)/, '');
        const emit = (operation: string, selected: number[] | null, liquid?: 'aspirate' | 'dispense') => {
            const footprint = selected && position ? nativeChannelFootprint(position.location, position.well, selected, position.alignment, position.unadjusted) : { wells: [], notes: [selected === null ? `${operation}: active/loaded channel footprint is unresolved.` : `${operation}: XY well position is not established.`] };
            if (selected?.length === 0) return;
            events.push({ actionId, path, operation, ...footprint, ...(liquid ? { liquid } : {}) });
            for (const point of footprint.wells) {
                if (liquid === 'aspirate') {
                    const previous = aspirated.get(point.channel);
                    const values = previous && !previous.dispensed ? previous.wells : [];
                    aspirated.set(point.channel, { wells: [...values, point], dispensed: false });
                } else if (liquid === 'dispense') {
                    const previous = aspirated.get(point.channel);
                    for (const source of previous?.wells ?? []) {
                        if (source.station === point.station && source.well === point.well) continue;
                        const key = JSON.stringify([actionId, point.channel, source.station, source.well, point.station, point.well]);
                        if (!linkKeys.has(key)) { linkKeys.add(key); links.push({ actionId, path, channel: point.channel, source, destination: point }); }
                    }
                    if (previous) previous.dispensed = true;
                }
            }
        };
        const move = (p: MethodValue, _selected: number[] | null) => {
            const op = String(p.operation);
            if (op === 'move') {
                const location = Number(p.location_id);
                position = p.location_id != null && Number.isInteger(location) ? { location, well: p.well, alignment: motionReference } : null;
                // Travel itself is not a liquid stroke. Lower/lift use the retained XY, not their Z-calibration location.
            } else if (op === 'lower' || op === 'lift') emit(op, loaded);
            else if (op === 'park') position = null;
            else events.push({ actionId, path, operation: op, wells: [], notes: [`Position operation ${op} has no well projection.`] });
        };
        const instruction = (p: MethodValue, selected: number[] | null) => {
            const active = channels(p.channels) ?? selected, op = String(p.operation);
            if (op === 'position' && p.rack_transform != null || op === 'profile_pickup') {
                // Native rack geometry/profile pickup may replace the plan with absolute XY.
                position = null;
                if (op === 'profile_pickup') { loaded = null; transportReference = null; }
                events.push({ actionId, path, operation: op, wells: [], notes: ['Geometry-positioned XY is not projected onto the fixed station grid; subsequent in-place wells remain unknown.'] });
            } else if (op === 'position') move(object(p.positioning), active);
            else if (['move', 'lower', 'lift'].includes(op)) move(p, active);
            else if (['aspirate', 'tracked_aspirate', 'surface_aspirate'].includes(op)) { if (op !== 'aspirate') emit(`${op} shared-Z exposure`, loaded); emit(op, active, 'aspirate'); }
            else if (op === 'dispense') emit(op, active, 'dispense');
            else if (op === 'liquid_search') { emit('liquid search shared-Z exposure', loaded); emit(op, active); if (p.on_contact) emit('aspirate on contact', active, 'aspirate'); }
            else if (['leading_air', 'trailing_air', 'reaspirate', 'empty', 'plld', 'device_command', 'device_program'].includes(op)) { if (op === 'plld') emit('pLLD shared-Z exposure', loaded); emit(op, active); }
            else if (op === 'z_move' || op === 'surface_move') emit(`${op} shared-Z exposure`, loaded);
            else if (!['delay', 'settings', 'pressure_capture'].includes(op)) events.push({ actionId, path, operation: op, wells: [], notes: [`Native operation ${op} is retained in the exact text; its well effects are not projected.`] });
        };
        const recipe = (r: MethodValue) => {
            const active = channels(r.channels), multi = object(r.multi);
            const hooks = (value: unknown) => rows(value).forEach(p => instruction(p, active));
            const dispense = (value: unknown) => rows(value).forEach(p => emit('dispense', channels(p.channels) ?? active, 'dispense'));
            // pipette/cavro_liquid.py compile_liquid_recipe:220-290, preserving hooks/conditioning/aliquot order.
            hooks(r.before_leading_air);
            if (r.leading_air != null) emit('leading_air', active);
            hooks(r.before_liquid);
            if (r.liquid_detection != null || r.aspiration_tracking != null) emit('aspiration shared-Z exposure', loaded);
            if (r.liquid_detection != null) emit('liquid_search', active);
            emit(r.aspiration_tracking ? 'tracked_aspirate' : r.liquid_detection ? 'surface/contact aspiration' : 'aspirate', active, 'aspirate');
            hooks(r.after_liquid);
            if (r.trailing_air != null) emit('trailing_air', active);
            if (r.multi == null) { hooks(r.before_dispense); dispense(r.dispense_segments); hooks(r.after_dispense); }
            else {
                hooks(multi.conditioning_before);
                if (Number(multi.conditioning_back_to_source_count) > 0) emit(`conditioning dispense × ${String(multi.conditioning_back_to_source_count)}`, active, 'dispense');
                hooks(multi.conditioning_after); hooks(r.before_dispense);
                for (const aliquot of rows(multi.aliquots)) { hooks(aliquot.before); dispense(aliquot.segments); if (multi.reaspiration != null) emit('reaspirate', active); hooks(aliquot.after); }
                if (multi.excess != null) { const excess = object(multi.excess); hooks(excess.before); dispense(excess.segments); hooks(excess.after); }
                hooks(r.after_dispense);
            }
            hooks(r.final_empty_before);
            if (r.final_empty_speed_ul_s != null) emit('empty', active);
        };
        const p = object(action.params), operation = String(p.operation);
        if (action.kind === 'pipette_manual_physical' && operation === 'load_tip') {
            const tray = Number(p.tray), location = [7, 8, 9, 10, 15][tray - 1];
            position = location === undefined ? null : { location, well: p.well, alignment: 0, unadjusted: true };
            emit('pick up tip', allChannels);
            const keep = p.keep_channels === undefined || p.keep_channels === null ? allChannels : channels(p.keep_channels);
            if (keep) emit('return unused tip to rack', allChannels.filter(c => !keep.includes(c)));
            loaded = keep; aspirated.clear();
            transportReference = ['T10', 'T50', 'T200', 'T201', 10, 50, 200, 201].includes(object(tipRacks[String(tray)]).tip_type as string | number) ? -1 : null;
            // Manual loadTip(-1) updates transport bookkeeping, not canonical motion alignment.
        } else if (action.kind === 'tip_eject' || action.kind === 'pipette_tip' && p.action === 'eject') {
            const settings = action.kind === 'pipette_tip' ? object(p.metadata) : p;
            const requested = channels(settings.channels) ?? allChannels;
            const selected: number[] | null = loaded === null ? null : requested.filter(c => (loaded as number[]).includes(c));
            emit(`eject tip in place (requested ${requested.map(c => `P${c + 1}`).join(', ')})`, selected);
            // Native TIP_EJECT has no gantry move: tips fall wherever the head already is.
            const at = position as { location: number } | null;
            const where = at && at.location !== 6 ? deckResources.find(r => r.locationId === at.location)?.label ?? deckRegions.find(r => r.locationId === at.location)?.label : undefined;
            if (where) events.push({ actionId, path, operation: 'ejection position', wells: [], hazard: `Tips are ejected where the head already is, over the ${where}, not over the waste bin.`, notes: [`Tip eject has no move to the waste bin; the head is over the ${where}.`] });
            if (loaded !== null && settings.check_missing_tip !== false && requested.some(c => !(loaded as number[]).includes(c))) {
                const held = (loaded as number[]).map(c => `P${c + 1}`).join(', ') || 'no channel';
                const asked = requested.map(c => `P${c + 1}`).join(', ');
                events.push({ actionId, path, operation: 'ejection selection', wells: [], hazard: `Tip eject asks for ${asked}, but only ${held} holds a tip. The robot's missing-tip check is on, so this eject is expected to stop with a missing-tip error.`, notes: [`Tip eject requests ${asked}, but only ${held} should hold a tip here; the robot's missing-tip check is on.`] });
            }
            if (selected) { loaded = (loaded as number[] | null)?.filter(c => !selected.includes(c)) ?? null; selected.forEach(c => aspirated.delete(c)); }
            transportReference = -1;
        } else if (action.kind === 'pipette_tip' && p.action === 'load') {
            const reference = object(p.metadata).tip_location ?? -1;
            transportReference = validAlignment(reference) ? reference : null;
            // Host transport bookkeeping does not move XY or publish canonical alignment.
        } else if (action.kind === 'pipette_init') { loaded = null; transportReference = null;
        } else if (action.kind === 'pipette_position') move(p, loaded);
        else if (action.kind === 'pipette_aspirate' || action.kind === 'pipette_dispense') {
            const selected = p.speed != null ? channels(p.channels) : transportReference === null ? null : transportReference === -1 ? allChannels : [transportReference];
            const operation = action.kind === 'pipette_aspirate' ? 'aspirate' : 'dispense';
            emit(operation, selected, operation);
        } else if (action.kind === 'pipette_mix') emit('native one-channel mix', transportReference !== null && transportReference !== -1 ? [transportReference] : null);
        else if (action.kind === 'pipette_manual_physical' && operation === 'cavro_liquid_recipe') recipe(object(p.recipe));
        else if (action.kind === 'pipette_manual_physical' && operation === 'cavro_application') rows(object(p.application).operations).forEach(i => instruction(i, null));
        else if (action.kind === 'pipette_manual_physical') instruction(p, loaded);
        else if (['park', 'inspect', 'thermal_door'].includes(String(action.kind))) {
            // Door/inspection can no-op, or park and eject/query loaded tips. Fold only common outcomes.
            // A successful loaded-tip park query publishes canonical -1; a no-op preserves its input.
            if (loaded === null || loaded.length > 0) {
                loaded = null;
                if (motionReference !== -1) motionReference = null;
                if (transportReference !== -1) transportReference = null;
            }
            position = null; aspirated.clear();
        } else if (['plate_move', 'move_cover', 'plate_catch', 'plate_release', 'home'].includes(String(action.kind))) position = null;
    }
    return { events, links };
}

export function manipulationText(projection: MethodManipulations, actionIds?: Set<string>): string {
    return projection.events.filter(e => !actionIds || actionIds.has(e.actionId)).map(e => {
        const wells = e.wells.map(w => `P${w.channel + 1} ${deckStations.find(s => s.id === w.station)?.label ?? w.station} ${w.well}`).join('; ');
        return `${e.actionId} · ${e.operation}: ${[wells, ...e.notes].filter(Boolean).join(' ')}`;
    }).join('\n');
}
