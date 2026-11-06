import { splitAtLinearization } from './setOrigin.js';

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

/** Linearization no longer blocks: crossing entries are kept as split two-segment entries. */
export function findLinearizationBlockers(_sequenceData: TopologySequenceLike): string[] {
    return [];
}

export function setSequenceTopology<T extends TopologySequenceLike>(
    sequenceData: T,
    circular: boolean,
): T {
    if (sequenceData.circular === circular) return sequenceData;
    if (!circular) return { ...splitAtLinearization(sequenceData as never) as T, circular };
    return { ...sequenceData, circular };
}
