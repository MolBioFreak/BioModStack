import { isSequenceAnnotation } from '../featureCatalog';

export function clearFeatureAnnotations<T extends { features: unknown[] }>(sequenceData: T): T {
    const metadata = sequenceData.features.filter((feature) => !isSequenceAnnotation(feature));
    if (metadata.length === sequenceData.features.length) return sequenceData;
    return { ...sequenceData, features: metadata };
}
