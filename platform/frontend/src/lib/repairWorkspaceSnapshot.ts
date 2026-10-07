import type { MethodValue } from './bioxpMethods';
const obj = (v: unknown): MethodValue => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as MethodValue : {};
const copy = <T>(v: T): T => v === undefined ? v : JSON.parse(JSON.stringify(v));
export type WorkspaceInputs = { bindings: MethodValue; dependencies: MethodValue; initialState: unknown };
/** One raw bundle for unsaved Export and Save; never resolves scientific values. */
export function workspaceSnapshot(method: MethodValue, inputs: WorkspaceInputs): MethodValue {
    const value = copy(method), editor = obj(value.editor_state);
    if (Object.hasOwn(editor, 'run_inputs') || Object.keys(inputs.bindings).length || Object.keys(inputs.dependencies).length || JSON.stringify(inputs.initialState) !== '{}') {
        const retained = copy(obj(editor.run_inputs)), original = workspaceHydrate(method).inputs;
        if (JSON.stringify(inputs) === JSON.stringify(original)) return value;
        for (const [key, field] of [['bindings', 'bindings'], ['dependencies', 'dependencies'], ['initial_state', 'initialState']] as const) {
            if (JSON.stringify(inputs[field]) !== JSON.stringify(original[field])) {
                if (inputs[field] === undefined) delete retained[key]; else retained[key] = copy(inputs[field]);
            }
        }
        value.editor_state = { ...editor, run_inputs: retained };
    }
    return value;
}
/** Native recovery inputs are authoritative, including explicit empty/null and omitted keys. */
export function workspaceRecoverySnapshot(result: MethodValue): MethodValue {
    const method = copy(obj(result.method ?? result.draft));
    const runInputs: MethodValue = {};
    for (const key of ['bindings', 'dependencies', 'initial_state']) {
        if (Object.hasOwn(result, key) && result[key] !== undefined) runInputs[key] = copy(result[key]);
    }
    return { ...method, editor_state: { ...obj(method.editor_state), run_inputs: runInputs, recovery_linkage: copy(result.recovery) } };
}
/** Legacy imports start empty, never inherit the previously selected draft. */
export function workspaceHydrate(raw: MethodValue): { method: MethodValue; inputs: WorkspaceInputs } {
    const method = copy(obj(raw.method ?? raw)), editor = obj(method.editor_state);
    const retained = Object.hasOwn(editor, 'run_inputs') ? obj(editor.run_inputs) : raw.method ? raw : {};
    return { method, inputs: { bindings: copy(obj(retained.bindings)), dependencies: copy(obj(retained.dependencies)), initialState: Object.hasOwn(editor, 'run_inputs') || Object.hasOwn(retained, 'initial_state') ? copy(retained.initial_state) : {} } };
}
