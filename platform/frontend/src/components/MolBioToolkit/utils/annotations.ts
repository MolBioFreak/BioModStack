export function clearFeatureAnnotations<T extends { features: unknown[] }>(sequenceData: T): T {
    if (sequenceData.features.length === 0) return sequenceData;
    return { ...sequenceData, features: [] };
}
