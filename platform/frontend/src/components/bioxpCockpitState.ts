export interface MutationSnapshot<T> {
    data: T | undefined;
    error: unknown | null;
    isPending: boolean;
    submittedAt: number;
}

export interface CockpitMutationState<T> {
    latestResult: T | undefined;
    latestError: unknown | null;
    normalCommandBlocked: boolean;
    stopBlocked: boolean;
}

export function currentStatusData<T>({
    data,
    isError,
}: {
    data: T | undefined;
    isError: boolean;
}): T | undefined {
    return isError ? undefined : data;
}

export function deriveCockpitMutationState<T>({
    execute,
    stop,
    emergency,
}: {
    execute: MutationSnapshot<T>;
    stop: MutationSnapshot<T>;
    emergency: MutationSnapshot<T>;
}): CockpitMutationState<T> {
    const latest = [execute, stop, emergency]
        .filter((mutation) => mutation.data !== undefined || mutation.error !== null)
        .sort((left, right) => right.submittedAt - left.submittedAt)[0];

    return {
        latestResult: latest?.error === null ? latest.data : undefined,
        latestError: latest?.error ?? null,
        normalCommandBlocked: execute.isPending || stop.isPending || emergency.isPending,
        stopBlocked: stop.isPending || emergency.isPending,
    };
}
