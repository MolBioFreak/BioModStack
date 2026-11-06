import type { MethodCompile, MethodValue } from './bioxpMethods';
import type { WorkflowDeckPlan } from './bioxpWorkflowPlan';
import type { MethodManipulations } from './bioxpMethodManipulations';
import { methodInitialState } from './bioxpMethodSimulation';
import { deckStations } from './bioxpWorkflowDeck';

const object = (v: unknown): MethodValue => v && typeof v === 'object' && !Array.isArray(v) ? v as MethodValue : {};
const rows = (v: unknown): MethodValue[] => Array.isArray(v) ? v.map(object) : [];
type Well = { names: string[]; volume: string | null };

/** Display-only deck contents and tip occupancy after the given native actions.
 * Starts from the planned deck, then replays the projected liquid links and tip
 * pickups/returns in document order. Never measured liquid, never a run input. */
export function stageDeck(plan: WorkflowDeckPlan, result: MethodCompile | null | undefined, initialState: unknown, projection: MethodManipulations, through: Set<string>) {
    // Wells are keyed by labware id, or by `@station` where no single labware is planned there.
    const wells = new Map<string, Map<string, Well>>();
    const at = (owner: string, well: string) => { const plate = wells.get(owner) ?? new Map<string, Well>(); wells.set(owner, plate); const w = plate.get(well) ?? { names: [], volume: null }; plate.set(well, w); return w; };
    for (const a of plan.assignments) {
        const w = at(a.labware_id, a.well), name = plan.materials.find(m => m.id === a.material_id)?.name || 'Material';
        if (!w.names.includes(name)) w.names.push(name);
        w.volume = w.names.length === 1 && a.volume_ul !== null && a.volume_ul !== '' ? String(a.volume_ul) : null;
    }
    const simulation = object(result?.simulation), start = object(methodInitialState(simulation, initialState));
    const station = new Map<string, string>(plan.labware.map(l => [l.id, String(object(object(start.labware)[l.id]).station ?? l.station ?? '')]));
    const moves = new Map(rows(simulation.after_occurrences).map(o => [String(o.occurrence_id), object(object(o.state_delta).labware)]));
    const tips: Record<number, boolean[]> = {};
    for (const [tray, rack] of Object.entries(object(start.tip_racks))) { const occupancy = object(rack).occupancy; if (Array.isArray(occupancy) && occupancy.length === 96) tips[Number(tray)] = occupancy.map(Boolean); }
    const owner = (stationId: string) => { const found = [...station].filter(([, s]) => s === stationId); return found.length === 1 ? found[0][0] : `@${stationId}`; };
    for (const action of rows(result?.document?.stages).flatMap(s => rows(s.actions))) {
        const id = String(action.action_id);
        if (!through.has(id)) continue;
        for (const link of projection.links.filter(l => l.actionId === id)) {
            const from = wells.get(owner(link.source.station))?.get(link.source.well);
            if (from) from.volume = null;
            const names = from?.names.length ? from.names : [`${deckStations.find(s => s.id === link.source.station)?.label ?? link.source.station} ${link.source.well}`];
            const w = at(owner(link.destination.station), link.destination.well);
            for (const n of names) if (!w.names.includes(n)) w.names.push(n);
            w.volume = null;
        }
        for (const event of projection.events.filter(e => e.actionId === id)) {
            const present = event.operation === 'return unused tip to rack' ? true : event.operation === 'pick up tip' ? false : null;
            if (present === null) continue;
            for (const w of event.wells) {
                const tray = deckStations.find(s => s.id === w.station)?.tipTray;
                const index = (w.well.charCodeAt(0) - 65) * 12 + Number(w.well.slice(1)) - 1;
                if (tray && tips[tray]) tips[tray][index] = present;
            }
        }
        for (const [labware, value] of Object.entries(moves.get(String(action.source_occurrence_id)) ?? {})) { const s = object(value).station; if (typeof s === 'string') station.set(labware, s); }
    }
    const contents: Record<string, Record<string, string>> = {};
    for (const [key, plate] of wells) {
        const where = key.startsWith('@') ? key.slice(1) : station.get(key);
        if (!where) continue;
        for (const [well, w] of plate) {
            if (!w.names.length) continue;
            const list = w.names.length > 4 ? `${w.names.slice(0, 3).join(' + ')} + ${w.names.length - 3} more` : w.names.join(' + ');
            (contents[where] ??= {})[well] = `${list}${w.volume !== null ? ` · ${w.volume} µL` : ''}`;
        }
    }
    return { contents, tips: Object.keys(tips).length ? tips : undefined };
}
