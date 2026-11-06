import type { MethodValue } from './bioxpMethods';

const object = (value: unknown): MethodValue => value && typeof value === 'object' && !Array.isArray(value) ? value as MethodValue : {};

/** Pure request construction; path is relative to methodsPost's /api/bioxp/methods base. */
export function buildMethodRunRequest(
    frozen: MethodValue,
    pinned: { id: string; revision: number } | null,
    basis: { id: string; revision: number; edited: boolean } | null,
    key: string,
    generation: number,
): { path: string; body: MethodValue } {
    const linkage = object(object(frozen.method).editor_state).recovery_linkage;
    return {
        path: pinned ? `library/${encodeURIComponent(pinned.id)}/runs` : 'quick-runs',
        body: {
            ...(pinned ? { revision: pinned.revision } : { method: frozen.method, ...(basis ? { based_on: basis } : {}) }),
            bindings: frozen.bindings,
            dependencies: frozen.dependencies,
            initial_state: frozen.initial_state,
            ...(linkage ? { recovery: JSON.parse(JSON.stringify(linkage)) } : {}),
            idempotency_key: key,
            expected_generation: generation,
            acknowledge_live: true,
        },
    };
}
