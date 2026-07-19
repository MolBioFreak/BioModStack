export function effectiveVramLimitMb(
    maxVramMb: number,
    threshold: number,
    safetyMarginMb: number,
): number {
    return Math.max(1024, Math.round(maxVramMb * threshold - safetyMarginMb));
}

export function thresholdFromEffectiveVramLimit(
    maxVramMb: number,
    effectiveLimitMb: number,
    safetyMarginMb: number,
    configuredThreshold?: number,
): number {
    if (!Number.isFinite(maxVramMb) || maxVramMb <= 0) return 0;
    if (
        effectiveLimitMb === 1024
        && Number.isFinite(configuredThreshold)
        && effectiveVramLimitMb(maxVramMb, configuredThreshold as number, safetyMarginMb) === 1024
    ) {
        return Math.min(1, Math.max(0, configuredThreshold as number));
    }
    return Math.min(1, Math.max(0, (effectiveLimitMb + safetyMarginMb) / maxVramMb));
}