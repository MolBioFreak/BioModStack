export type SequenceDiagnosticMetric =
    | 'gc'
    | 'restriction_density'
    | 'ambiguity_density'
    | 'homopolymer_burden';

export function shouldComputeRestrictionPositions(metricId: SequenceDiagnosticMetric): boolean {
    return metricId === 'restriction_density';
}
export function countPositionsInRange(positions: number[], start: number, end: number, includeEnd = false): number {
    const bound = (value: number, inclusive: boolean) => {
        let low = 0;
        let high = positions.length;
        while (low < high) {
            const mid = low + Math.floor((high - low) / 2);
            if (positions[mid] < value || (inclusive && positions[mid] === value)) low = mid + 1;
            else high = mid;
        }
        return low;
    };
    return Math.max(0, bound(end, includeEnd) - bound(start, false));
}
