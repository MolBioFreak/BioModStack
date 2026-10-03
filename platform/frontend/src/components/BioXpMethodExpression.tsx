import type { ReactNode } from 'react';
type Value = Record<string, unknown>;
const obj = (v: unknown): Value => v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Value : {};
/** Versioned JSON AST only. No executable text or measurement references. */
export function BioXpMethodExpression({ label, value, onChange, literal }: { label: string; value: unknown; onChange: (v: unknown) => void; literal?: (value: unknown, onChange: (v: unknown) => void) => ReactNode }) {
    const expression = obj(obj(value).expr);
    const op = String(expression.op ?? 'literal');
    const update = (changes: Value) => onChange({ ...obj(value), expr: { ...expression, ...changes } });
    return <fieldset aria-label={`${label} expression`} className="space-y-2 rounded border border-cyan-800 p-2"><legend>{label} expression</legend>
        <label>Operator<select aria-label={`${label} operator`} value={op} onChange={e => update({ version: 1, op: e.target.value })}>
            {['literal', 'param', 'arg', 'loop_index', 'loop_item', 'add', 'sub', 'mul', 'div', 'eq', 'ne', 'lt', 'le', 'gt', 'ge', 'and', 'or', 'not'].map(name => <option key={name}>{name}</option>)}
        </select></label>
        {op === 'literal' ? <>{literal ? literal(expression.value, v => update({ value: v })) : <label>Literal<input aria-label={`${label} literal`} value={String(expression.value ?? '')} onChange={e => update({ value: e.target.value })} /></label>}<label>Unit<input aria-label={`${label} unit`} value={String(expression.unit ?? '')} onChange={e => update({ unit: e.target.value })} /></label></> : ['param', 'arg'].includes(op) ? <label>Stable parameter ID<input aria-label={`${label} reference`} value={String(expression.id ?? '')} onChange={e => update({ id: e.target.value })} /></label> : ['loop_index', 'loop_item'].includes(op) ? <p>Nearest lexical loop; index is zero-based. Duplicate items have distinct occurrences.</p> : <>
            {(Array.isArray(expression.args) ? expression.args : []).map((arg, i, args) => <div key={i}><BioXpMethodExpression label={`${label} operand ${i + 1}`} value={arg} onChange={v => update({ args: args.map((old, n) => n === i ? v : old) })} literal={literal} /><button type="button" onClick={() => update({ args: args.filter((_, n) => i !== n) })}>Remove operand {i + 1}</button></div>)}
            <button type="button" onClick={() => update({ args: [...(Array.isArray(expression.args) ? expression.args : []), { expr: { version: 1, op: 'literal', value: '' } }] })}>Add operand</button>
        </>}
        <p className="text-xs">Authored quantities retain units; compiler resolves lexical scope and dimensional arithmetic. Missing values remain missing.</p>
    </fieldset>;
}
