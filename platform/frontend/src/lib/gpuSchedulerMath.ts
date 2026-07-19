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
): number {
    if (!Number.isFinite(maxVramMb) || maxVramMb <= 0) return 0;
    return Math.min(1, Math.max(0, (effectiveLimitMb + safetyMarginMb) / maxVramMb));
}