import type { MethodValue, MethodCatalog } from './bioxpMethods';
import { methodInputBinding } from './bioxpMethodInputBinding';
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

/** Group only adjacent siblings: wrapping must not reorder intervening actions. */
export function groupCanvasSteps(nodes: MethodValue[], ids: string[], type: 'group' | 'repeat', stepId: string): MethodValue[] | null {
    const indices = nodes.flatMap((n, index) => ids.includes(String(n.step_id)) ? [index] : []);
    if (!indices.length || indices.some((index, i) => index !== indices[0] + i)) return null;
    const first = indices[0], children = nodes.slice(first, first + indices.length);
    const frame = { step_id: stepId, type, label: type === 'repeat' ? 'Repeated steps' : 'Grouped steps', ...(type === 'repeat' ? { count: '' } : {}), steps: children };
    return [...nodes.slice(0, first), frame, ...nodes.slice(first + indices.length)];
}

/** Removing a visual group must not discard inherited behavior or extensions. */
export function canUngroupCanvasNode(node: MethodValue): boolean {
    return node.type === 'group' && Array.isArray(node.steps) && Object.keys(node).every(key => ['step_id', 'type', 'label', 'steps'].includes(key));
}

export type MethodCarryLink = { path: string; node: MethodValue; labwareId: string; source?: string; destination?: string };
/** Authored placement projection, not observed custody or a second compiler.
 * Conditional/repeated/called effects are left unknown; the real occurrence preview
 * remains the owner of their evaluated location. Never change native endpoints here. */
export function methodCarryLinks(method: MethodValue, bindings: MethodValue, plan: WorkflowDeckPlan, catalog: MethodCatalog): MethodCarryLink[] {
    const destinations = object(object(catalog.authoring).custody).destinations;
    const declared = Array.isArray(destinations) ? destinations as MethodValue[] : [];
    const placement = new Map<string, string | undefined>(plan.labware.map(l => [l.id, l.station]));
    const links: MethodCarryLink[] = [];
    const visit = (rows: MethodValue[], path: string, uncertain = false) => {
        for (const [index, node] of rows.entries()) {
            const here = `${path}/${index}`, input = object(methodInputBinding(method, node, bindings).inputs);
            const retainedPlacement = node.enabled === false ? new Map(placement) : undefined;
            if (node.action === 'plate_move') {
                const id = typeof input.labware_id === 'string' ? input.labware_id : '';
                const destination = declared.find(d => d.token === input.target_location && d.plate_destination != null)?.station;
                links.push({ path: here, node, labwareId: id, source: uncertain || node.enabled === false ? undefined : placement.get(id), destination: typeof destination === 'string' ? destination : undefined });
                if (node.enabled !== false) {
                    if (id) placement.set(id, uncertain || typeof destination !== 'string' ? undefined : destination);
                    else for (const key of placement.keys()) placement.set(key, undefined);
                }
            } else if (node.type === 'group') {
                visit(Array.isArray(node.steps) ? node.steps as MethodValue[] : [], `${here}/steps`, uncertain || node.enabled === false);
            } else if (['repeat', 'if', 'call'].includes(String(node.type))) {
                if (node.enabled !== false) for (const key of placement.keys()) placement.set(key, undefined);
                for (const key of ['steps', 'then', 'else']) if (Array.isArray(node[key])) visit(node[key] as MethodValue[], `${here}/${key}`, true);
            } else if (['plate_catch', 'plate_release', 'plate_prepare', 'native_intent'].includes(String(node.action)) && node.enabled !== false) {
                for (const key of placement.keys()) placement.set(key, undefined);
            }
            if (retainedPlacement) { placement.clear(); for (const [key, value] of retainedPlacement) placement.set(key, value); }
        }
    };
    visit(Array.isArray(method.steps) ? method.steps as MethodValue[] : [], '/steps');
    return links;
}

/** Clone the selected authored subtree, not shared procedure definitions. Parameters
 * referenced by this copy (including count/condition/arguments and dependent defaults)
 * receive independent declarations/bindings. Other consumers retain their originals. */
export function duplicateCanvasNode(method: MethodValue, node: MethodValue, bindings: MethodValue, cloneParameters = true) {
    const parameters = [...(Array.isArray(method.parameters) ? method.parameters as MethodValue[] : [])];
    const nextBindings = { ...bindings }, ids = new Map<string, string>();
    const values = (value: unknown): unknown => {
        if (Array.isArray(value)) return value.map(values);
        if (value === null || typeof value !== 'object') return value;
        const source = object(value), expr = object(source.expr);
        if (cloneParameters && expr.version === 1 && expr.op === 'param' && typeof expr.id === 'string') {
            const declaration = parameters.find(p => p.id === expr.id);
            if (declaration) {
                let id = ids.get(expr.id);
                if (!id) {
                    id = crypto.randomUUID(); ids.set(expr.id, id);
                    parameters.push({ ...object(values(declaration)), id });
                    if (Object.hasOwn(bindings, expr.id)) nextBindings[id] = values(bindings[expr.id]);
                }
                return { ...source, expr: { ...expr, id } };
            }
        }
        return Object.fromEntries(Object.entries(source).map(([key, field]) => [key, values(field)]));
    };
    const copy = (original: MethodValue): MethodValue => {
        const result = object(values(original)); result.step_id = crypto.randomUUID();
        for (const key of ['steps', 'then', 'else']) if (Array.isArray(original[key])) result[key] = (original[key] as MethodValue[]).map(copy);
        return result;
    };
    return { node: copy(node), parameters, bindings: nextBindings, changed: ids.size > 0 };
}
