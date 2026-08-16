export type ImportTopology = 'preserve' | 'linear' | 'circular';

interface SegmentLike {
    start: number;
    end: number;
}

interface FeatureLike {
    start?: number;
    end?: number;
    segments?: SegmentLike[];
}

interface PrimerLike {
    start?: number;
    end?: number;
    sites?: SegmentLike[];
}

export interface TopologySequenceLike {
    sequence: string;
    circular: boolean;
    features?: FeatureLike[];
    primers?: PrimerLike[];
}

export function applyImportedTopology(
    parsedCircular: boolean,
    topology: ImportTopology,
): boolean {
    if (topology === 'circular') return true;
    if (topology === 'linear') return false;
    return parsedCircular;
}

function traversesOrigin(
    start: number | undefined,
    end: number | undefined,
    segments: SegmentLike[] | undefined,
    sequenceLength: number,
): boolean {
    if (typeof start === 'number' && typeof end === 'number' && start > end) {
        return true;
    }
    if (!segments || segments.length < 2 || sequenceLength <= 0) {
        return false;
    }

    return segments.some((segment, index) => {
        const next = segments[(index + 1) % segments.length];
        return (segment.end === sequenceLength && next.start === 0)
            || (segment.start === 0 && next.end === sequenceLength);
    });
}

function blockerLabel(count: number, singular: string): string {
    return `${count} origin-spanning ${singular}${count === 1 ? '' : 's'}`;
}

export function findLinearizationBlockers(sequenceData: TopologySequenceLike): string[] {
    if (!sequenceData.circular) return [];

    const sequenceLength = sequenceData.sequence.length;
    const featureCount = (sequenceData.features || []).filter((feature) => traversesOrigin(
        feature.start,
        feature.end,
        feature.segments,
        sequenceLength,
    )).length;
    const primerCount = (sequenceData.primers || []).filter((primer) => traversesOrigin(
        primer.start,
        primer.end,
        primer.sites,
        sequenceLength,
    )).length;

    const blockers: string[] = [];
    if (featureCount > 0) blockers.push(blockerLabel(featureCount, 'feature'));
    if (primerCount > 0) blockers.push(blockerLabel(primerCount, 'primer'));
    return blockers;
}

export function setSequenceTopology<T extends TopologySequenceLike>(
    sequenceData: T,
    circular: boolean,
): T {
    if (sequenceData.circular === circular) return sequenceData;
    if (!circular) {
        const blockers = findLinearizationBlockers(sequenceData);
        if (blockers.length > 0) {
            throw new Error(
                `Cannot linearize at the current origin: ${blockers.join(', ')}. Rotate the origin before linearizing.`,
            );
        }
    }
    return { ...sequenceData, circular };
}
