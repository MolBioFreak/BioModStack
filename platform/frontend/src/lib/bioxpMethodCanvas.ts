import type { MethodValue } from './bioxpMethods';
import { deckStations } from './bioxpWorkflowDeck';
import type { WorkflowDeckPlan } from './bioxpWorkflowPlan';
const object = (value: unknown): MethodValue => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as MethodValue : {};

/** Insert beside the selected authored node, never before an inferred preparation prefix. */
export function insertMethodAction(nodes: MethodValue[], added: MethodValue, selected: unknown, position: string): MethodValue[] {
    if (position === 'end' || !selected) return [...nodes, added];
    let found = false;
    const visit = (rows: MethodValue[], path = '/steps'): MethodValue[] => rows.flatMap((node, index) => {
        if (`${path}/${index}` === selected || node.step_id === selected) { found = true; return position === 'before' ? [added, node] : [node, added]; }
        return [{ ...node, ...Object.fromEntries(['steps', 'then', 'else'].filter(key => Array.isArray(node[key])).map(key => [key, visit(node[key] as MethodValue[], `${path}/${index}/${key}`)])) }];
    });
    const next = visit(nodes);
    return found ? next : [...nodes, added];
}

/** Explicit authoring adaptation only. A compiled occurrence snapshot is authoritative
 * for projected location; starting assignments are a separate, explicit context.
 * Never recursively rewrite retained targets or evaluate method expressions here. */
export function plateBoundEndpoint(endpoint: unknown, labwareId: string, plan: WorkflowDeckPlan, occurrenceState?: MethodValue | null): MethodValue | null {
    if (endpoint !== undefined && (endpoint === null || typeof endpoint !== 'object' || Array.isArray(endpoint) || Object.hasOwn(endpoint, 'expr'))) return null;
    const stationId = occurrenceState === undefined
        ? plan.labware.find(l => l.id === labwareId)?.station
        : object(object(occurrenceState?.labware)[labwareId]).station;
    const station = deckStations.find(s => s.id === stationId || s.locationId != null && String(s.locationId) === String(stationId));
    if (!station || station.locationId == null) return null;
    return { ...object(endpoint), labware_id: labwareId, station: station.id, location_id: station.locationId };
}

export function methodCanvasEntries(nodes: MethodValue[], path = '/steps'): { node: MethodValue; path: string }[] {
    return nodes.flatMap((node, index) => [{ node, path: `${path}/${index}` }, ...['steps', 'then', 'else'].flatMap(key => Array.isArray(node[key]) ? methodCanvasEntries(node[key] as MethodValue[], `${path}/${index}/${key}`) : [])]);
}
export function editCanvasNode(nodes: MethodValue[], selected: string, change: (node: MethodValue) => MethodValue): MethodValue[] {
    const visit = (rows: MethodValue[], path = '/steps'): MethodValue[] => rows.map((node, index) => `${path}/${index}` === selected || node.step_id === selected ? change(node) : { ...node, ...Object.fromEntries(['steps', 'then', 'else'].filter(key => Array.isArray(node[key])).map(key => [key, visit(node[key] as MethodValue[], `${path}/${index}/${key}`)])) });
    return visit(nodes);
}
