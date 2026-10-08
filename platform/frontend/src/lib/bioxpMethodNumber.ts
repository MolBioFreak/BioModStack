/** Explicit author-selected numeric kind, persisted in the compiler's literal AST.
 * Do not infer numbers from strings or convert through JavaScript Number.
 */
export type MethodNumber = { expr: { version: 1; op: 'literal'; type: 'number'; value: string } };
export function methodNumber(raw: string): MethodNumber {
    return { expr: { version: 1, op: 'literal', type: 'number', value: raw } };
}
export function isMethodNumber(value: unknown): value is MethodNumber {
    if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).join() !== 'expr') return false;
    const expr = (value as { expr?: unknown }).expr;
    if (!expr || typeof expr !== 'object' || Array.isArray(expr)) return false;
    const e = expr as Record<string, unknown>;
    return Object.keys(e).length === 4 && e.version === 1 && e.op === 'literal' && e.type === 'number' && typeof e.value === 'string';
}
