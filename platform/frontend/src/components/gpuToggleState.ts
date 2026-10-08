export interface InitialGpuPinningState {
    pinnedGpus: number[];
    lockGpus: boolean;
}

const normalizeBooleanFlag = (value: unknown): boolean => {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (!normalized || normalized === '0' || normalized === 'false' || normalized === 'no' || normalized === 'off') {
            return false;
        }
        if (normalized === '1' || normalized === 'true' || normalized === 'yes' || normalized === 'on') {
            return true;
        }
    }
    return false;
};

export const normalizePinnedGpuIds = (value: unknown): number[] => {
    const rawValues = Array.isArray(value)
        ? value
        : typeof value === 'string'
            ? value.split(',')
            : [];
    const seen = new Set<number>();
    const gpuIds: number[] = [];

    for (const rawValue of rawValues) {
        const gpuId = Number(rawValue);
        if (!Number.isInteger(gpuId) || gpuId < 0 || seen.has(gpuId)) {
            continue;
        }
        seen.add(gpuId);
        gpuIds.push(gpuId);
    }

    return gpuIds.sort((left, right) => left - right);
};

export const resolveInitialGpuPinningState = (initialValues?: Record<string, unknown> | null): InitialGpuPinningState => {
    const pinnedGpus = normalizePinnedGpuIds(initialValues?.pinned_gpus);
    return {
        pinnedGpus,
        lockGpus: pinnedGpus.length > 0 && normalizeBooleanFlag(initialValues?.lock_gpus),
    };
};
