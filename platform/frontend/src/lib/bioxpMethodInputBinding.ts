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

/** Remove a step's inputs only when no retained graph, default or binding refers to them. */
export function removeMethodInputParameters(method: MethodValue, removed: MethodValue, bindings: MethodValue) {
    const candidates = new Set<string>();
    const collect = (node: MethodValue) => {
        const bound = methodInputBinding(method, node, bindings);
        if (bound.id) candidates.add(bound.id);
        for (const key of ['steps', 'then', 'else']) if (Array.isArray(node[key])) (node[key] as MethodValue[]).forEach(collect);
    };
    collect(removed);
    const referenced = new Set<string>();
    const scan = (value: unknown): void => {
        if (Array.isArray(value)) value.forEach(scan);
        else if (objectValue(value)) {
            if (value.version === 1 && value.op === 'param' && typeof value.id === 'string') referenced.add(value.id);
            Object.values(value).forEach(scan);
        }
    };
    scan(method);
    scan(Object.fromEntries(Object.entries(bindings).filter(([key]) => !candidates.has(key))));
    const unused = new Set([...candidates].filter(id => !referenced.has(id)));
    return { method: unused.size ? { ...method, parameters: (Array.isArray(method.parameters) ? method.parameters as MethodValue[] : []).filter(p => !unused.has(String(p.id))) } : method,
        bindings: Object.fromEntries(Object.entries(bindings).filter(([key]) => !unused.has(key))), changed: unused.size > 0 };
}
