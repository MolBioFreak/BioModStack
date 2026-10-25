type Request = Record<string, unknown>;
export type NativeTaskDrafts = Record<string, Request>;
const object = (value: unknown): Request => value && typeof value === 'object' && !Array.isArray(value) ? value as Request : {};

/** Editor identity only; never normalizes or translates scientific inputs. */
export function nativeTaskKey(model: string, mode: string, contract: string, request: Request, recipe?: string): string {
    const task = model === 'rfd4_proteina' ? object(request.input).kind : request.task;
    // Legacy keys are unchanged. Explicit recipes have independent editor identity,
    // even when they share condition_spec; this token never enters native science.
    return JSON.stringify([model, mode, contract, ...(recipe ? ['recipe', recipe] : [task ?? null])]);
}

export function changeNativeTask(
    model: string, mode: string, contract: string,
    previous: Request, next: Request, inactive: NativeTaskDrafts,
): { request: Request; inactive: NativeTaskDrafts } {
    const before = nativeTaskKey(model, mode, contract, previous);
    const after = nativeTaskKey(model, mode, contract, next);
    if (before === after) return { request: next, inactive };
    const drafts = { ...inactive, [before]: previous };
    const restored = drafts[after];
    delete drafts[after]; // Active input is stored once, outside inactive history.
    if (restored) return { request: restored, inactive: drafts };
    // Selecting a different native task is not permission to copy that task's
    // source roles or scientific settings. Seed only explicit routing/tag fields.
    const routing = Object.fromEntries(['version', 'mode', 'schema_version', 'operation', 'task']
        .filter(key => Object.hasOwn(next, key)).map(key => [key, next[key]]));
    return { request: model === 'rfd4_proteina' ? { ...routing, input: next.input } : routing, inactive: drafts };
}
