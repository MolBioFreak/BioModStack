import type { MethodValue } from './bioxpMethods';
const object = (v: unknown): MethodValue => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as MethodValue : {};
const keys = { manual: ['aspirate_speed', 'dispense_speed'], class: ['liquid', 'recipe'] };
export function transferMode(node: MethodValue): 'manual' | 'class' {
    const inputs = object(node.inputs);
    return Object.hasOwn(inputs, 'liquid') || Object.hasOwn(inputs, 'recipe') || object(object(node.editor_state).repair_workspace_transfer).active === 'class' ? 'class' : 'manual';
}
/** Node-local editor metadata follows structural copy/removal and bound input ownership. */
export function switchTransferMode(node: MethodValue, mode: 'manual' | 'class'): MethodValue {
    const previous = transferMode(node);
    if (previous === mode) return node;
    const inputs = { ...object(node.inputs) }, editor = object(node.editor_state), stash = object(editor.repair_workspace_transfer);
    const retained = Object.fromEntries(keys[previous].filter(k => Object.hasOwn(inputs, k)).map(k => [k, inputs[k]]));
    keys[previous].forEach(k => delete inputs[k]);
    return { ...node, inputs: { ...inputs, ...object(stash[mode]) }, editor_state: { ...editor, repair_workspace_transfer: { ...stash, [previous]: retained, active: mode } } };
}
/** Same signed mapping as channel_wells; no geometry inference or admission rule. */
export function transferFootprint(reference: unknown, channels: unknown[], geometry: unknown) {
    const g = object(geometry), match = typeof reference === 'string' ? /^([A-Z])([1-9][0-9]*)$/.exec(reference) : null;
    if (!match || !['row_increment', 'column_increment', 'reference_channel'].every(k => Number.isInteger(g[k]))) return null;
    const rows = channels.map(channel => {
        if (!Number.isInteger(channel) || Number(channel) < 0 || Number(channel) > 3) return null;
        const row = match[1].charCodeAt(0) + (Number(channel) - Number(g.reference_channel)) * Number(g.row_increment);
        const column = Number(match[2]) + (Number(channel) - Number(g.reference_channel)) * Number(g.column_increment);
        return row < 65 || row > 90 || column < 1 ? null : { channel: Number(channel), well: `${String.fromCharCode(row)}${column}` };
    });
    return rows.some(row => row === null) ? null : rows as { channel: number; well: string }[];
}
