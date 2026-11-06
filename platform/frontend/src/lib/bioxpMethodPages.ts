import type { MethodCompile, MethodValue } from './bioxpMethods';
import { methodInitialState, methodStateAfter } from './bioxpMethodSimulation';

export type MethodPage = { id: string; label: string; paths: string[]; nodes: MethodValue[] };
const record = (value: unknown): MethodValue => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as MethodValue : {};
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;

/** Presentation only: metadata may join adjacent roots, never change their execution. */
export function methodPages(method: MethodValue): MethodPage[] {
    const nodes = Array.isArray(method.steps) ? method.steps as MethodValue[] : [];
    const metadata = record(method.editor_state).workflow_pages;
    const rows = Array.isArray(metadata) ? metadata.map(record) : [];
    const indices = new Map<string, number[]>();
    nodes.forEach((node, index) => {
        const id = record(node).step_id;
        if (text(id)) indices.set(id, [...(indices.get(id) ?? []), index]);
    });
    const candidates = rows.flatMap(row => {
        if (!text(row.id) || row.id === 'starting-deck' || !text(row.label)
            || rows.filter(other => other.id === row.id).length !== 1
            || !Array.isArray(row.step_ids) || !row.step_ids.length) return [];
        const positions = row.step_ids.map(id => text(id) && indices.get(id)?.length === 1 ? indices.get(id)![0] : -1);
        if (positions.some((position, i) => position < 0 || position !== positions[0] + i)) return [];
        return [{ id: row.id, label: row.label, positions }];
    });
    // Reject both sides of an overlap rather than letting metadata order pick a winner.
    const valid = candidates.filter(candidate => !candidates.some(other => other !== candidate && other.positions.some(index => candidate.positions.includes(index))));
    const usedIds = new Set(['starting-deck', ...valid.map(row => row.id)]);
    const pages: MethodPage[] = [{ id: 'starting-deck', label: 'Starting deck', paths: [], nodes: [] }];
    for (let index = 0; index < nodes.length;) {
        const grouped = valid.find(row => row.positions[0] === index);
        const positions = grouped?.positions ?? [index];
        const paths = positions.map(position => `/steps/${position}`);
        let id = grouped?.id ?? `step:${paths[0]}`;
        if (!grouped) {
            while (usedIds.has(id)) id += ':';
            usedIds.add(id);
        }
        const node = record(nodes[index]);
        const label = grouped?.label ?? [node.label, node.action, node.type].find(text) ?? `Step ${index + 1}`;
        pages.push({ id, label, paths, nodes: positions.map(position => nodes[position]) });
        index += positions.length;
    }
    return pages;
}

/** Full compiler paths and slash boundaries avoid matching root 1 against root 10. */
export function pageProvenance(page: MethodPage, result: MethodCompile | null | undefined): MethodValue[] {
    return (result?.provenance ?? []).filter(row => {
        // Procedure bodies retain their own definition paths; their call sites own the page.
        const paths = [row.path, ...(Array.isArray(row.call_path) ? row.call_path.map(call => record(call).path) : [])];
        return paths.some(value => typeof value === 'string' && page.paths.some(path => {
            const full = `/method${path}`;
            return value === full || value.startsWith(`${full}/`);
        }));
    });
}

/** Retain native document order, including every action in a multi-action occurrence. */
export function pageNativeActions(page: MethodPage, result: MethodCompile | null | undefined): MethodValue[] {
    const ids = new Set(pageProvenance(page, result).flatMap(row => Array.isArray(row.native_action_ids) ? row.native_action_ids : []));
    const stages = result?.document?.stages;
    return Array.isArray(stages) ? stages.flatMap(stage => {
        const actions = record(stage).actions;
        return Array.isArray(actions) ? actions.filter(action => ids.has(record(action).action_id)) as MethodValue[] : [];
    }) : [];
}

/** Unknown/empty occurrences remain unknown; never substitute the final simulation. */
export function pageState(page: MethodPage, _pages: MethodPage[], result: MethodCompile | null | undefined, initialState: unknown): MethodValue | null {
    if (page.id === 'starting-deck') {
        const initial = methodInitialState(result?.simulation, initialState);
        return initial !== null && typeof initial === 'object' && !Array.isArray(initial) ? structuredClone(initial as MethodValue) : null;
    }
    const occurrences = pageProvenance(page, result);
    const last = occurrences[occurrences.length - 1];
    return last && text(last.occurrence_id) ? methodStateAfter(result?.simulation, initialState, last.occurrence_id) : null;
}
