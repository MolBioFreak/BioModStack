import type { MethodValue } from './bioxpMethods';
export type ThermalIdFactory = () => string;
const freshId = () => crypto.randomUUID();
function literalInputs(node: MethodValue): MethodValue {
    const value = node.inputs;
    if (value === undefined) return {};
    if (!value || typeof value !== 'object' || Array.isArray(value) || 'expr' in value) throw new Error('Compose through the input binding owner; retained inputs cannot be detached.');
    return value as MethodValue;
}
/** Replace a profile with ordinary AST children; never repeat the outside holds. */
export function composeThermalRepeat(node: MethodValue, first: number, last: number, repeat: unknown, id: ThermalIdFactory = freshId): MethodValue {
    const inputs = literalInputs(node);
    if (node.action !== 'thermal_profile' || !Array.isArray(inputs.segments)) throw new Error('Select a literal thermal profile.');
    const segments = inputs.segments;
    if (!Number.isInteger(first) || !Number.isInteger(last) || first < 0 || last < first || last >= segments.length) throw new Error('Select a contiguous step range.');
    const hold = (segment: unknown): MethodValue => ({ ...structuredClone(node), type: 'action', step_id: id(), action: 'thermal_hold', inputs: structuredClone(segment) });
    const profile = { ...node, step_id: id(), inputs: { ...inputs, repeat, segments: structuredClone(segments.slice(first, last + 1)) } };
    // Keep all original action extensions on the profile; original scope stays on group.
    const { action: _action, inputs: _inputs, required_capability: _capability, ...scope } = node;
    return { ...scope, type: 'group', steps: [...segments.slice(0, first).map(hold), profile, ...segments.slice(last + 1).map(hold)] };
}
export function thermalTimerWait(timerId: string, id: ThermalIdFactory = freshId): MethodValue {
    return { type: 'action', step_id: id(), action: 'timer_wait', inputs: { timer_id: timerId } };
}
/** Elapsed conditioning only; target attainment and automatic Off are not implied. */
export function composeChillerTimer(node: MethodValue, seconds: unknown, timerId: string, waitHere: boolean, id: ThermalIdFactory = freshId): MethodValue {
    literalInputs(node);
    if (node.action !== 'chiller_setpoint') throw new Error('Select a chiller setpoint.');
    const { action: _action, inputs: _inputs, required_capability: _capability, ...scope } = node;
    return { ...scope, type: 'group', steps: [
        { ...structuredClone(node), step_id: id() },
        { type: 'action', step_id: id(), action: 'timer_start', inputs: { timer_id: timerId, seconds } },
        ...(waitHere ? [thermalTimerWait(timerId, id)] : []),
    ] };
}
