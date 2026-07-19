export type SequenceDiagnosticMetric =
    | 'gc'
    | 'restriction_density'
    | 'ambiguity_density'
    | 'homopolymer_burden';

export function shouldComputeRestrictionPositions(metricId: SequenceDiagnosticMetric): boolean {
    return metricId === 'restriction_density';
}