import type { MethodValue } from './bioxpMethods';
const objectValue = (v: unknown): v is MethodValue => v !== null && typeof v === 'object' && !Array.isArray(v);

/** Project only a direct, declared whole-input parameter. Never evaluate an AST. */
export function methodInputBinding(method: MethodValue, node: MethodValue, bindings: MethodValue) {
    const raw = node.inputs;
    const expr = objectValue(raw) && objectValue(raw.expr) ? raw.expr : undefined;
    const parameter = expr?.version === 1 && expr.op === 'param' && typeof expr.id === 'string'
        ? (Array.isArray(method.parameters) ? method.parameters : []).find(p => objectValue(p) && p.id === expr.id && p.type === 'object') as MethodValue | undefined : undefined;
    if (!parameter) return { inputs: raw, editable: raw === undefined || objectValue(raw) && !Object.hasOwn(raw, 'expr') };
    const id = String(parameter.id), explicit = Object.hasOwn(bindings, id);
    const value = explicit ? bindings[id] : parameter.default;
    const editable = value === undefined || objectValue(value) && !Object.hasOwn(value, 'expr');
    return { id, inputs: value, editable, inherited: !explicit && Object.hasOwn(parameter, 'default'), label: String(parameter.label ?? id) };
}
