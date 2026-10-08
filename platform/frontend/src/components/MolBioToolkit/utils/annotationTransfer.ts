export type AnnotationMatchMode =
    | 'exact'
    | 'rotated'
    | 'reverse_complement'
    | 'reverse_complement_rotated';

export interface AnnotationSequenceAlignment {
    length: number;
    mode: AnnotationMatchMode;
    reverseComplement: boolean;
    rotation: number;
}

export interface TransferFeature {
    id: string;
    name: string;
    type: string;
    start: number;
    end: number;
    strand: 1 | -1;
    segments?: Array<{ start: number; end: number }>;
}

function normalizeSequence(sequence: string): string {
    return sequence.replace(/\s+/g, '').toUpperCase();
}

function reverseComplement(sequence: string): string {
    const complement: Record<string, string> = {
        A: 'T', C: 'G', G: 'C', T: 'A', U: 'A',
        R: 'Y', Y: 'R', M: 'K', K: 'M', S: 'S', W: 'W',
        H: 'D', B: 'V', V: 'B', D: 'H', N: 'N',
    };
    return [...sequence].reverse().map((base) => complement[base] || base).join('');
}

function rotationOffsets(source: string, target: string): number[] {
    if (source.length !== target.length || source.length === 0) return [];
    const doubled = source + source.slice(0, -1);
    const offsets: number[] = [];
    let index = doubled.indexOf(target);
    while (index >= 0 && index < source.length) {
        offsets.push(index);
        index = doubled.indexOf(target, index + 1);
    }
    return offsets;
}

export function assertAnnotationTopology(sourceCircular: boolean, targetCircular: boolean): void {
    if (sourceCircular !== targetCircular) {
        throw new Error(
            `Annotated-file topology (${sourceCircular ? 'circular' : 'linear'}) does not match the open construct topology (${targetCircular ? 'circular' : 'linear'}).`,
        );
    }
}

export function resolveAnnotationSequenceAlignment(
    sourceSequence: string,
    targetSequence: string,
    circular: boolean,
): AnnotationSequenceAlignment {
    const source = normalizeSequence(sourceSequence);
    const target = normalizeSequence(targetSequence);
    if (!source || source.length !== target.length) {
        throw new Error('Annotated-file sequence does not match the open construct length.');
    }
    if (source === target) {
        return { length: source.length, mode: 'exact', reverseComplement: false, rotation: 0 };
    }

    const reversed = reverseComplement(source);
    if (reversed === target) {
        return { length: source.length, mode: 'reverse_complement', reverseComplement: true, rotation: 0 };
    }
    if (!circular) {
        throw new Error('Annotated-file sequence does not match the open linear construct.');
    }

    const candidates = [
        ...rotationOffsets(source, target).map((rotation) => ({
            length: source.length,
            mode: 'rotated' as const,
            reverseComplement: false,
            rotation,
        })),
        ...rotationOffsets(reversed, target).map((rotation) => ({
            length: source.length,
            mode: 'reverse_complement_rotated' as const,
            reverseComplement: true,
            rotation,
        })),
    ];

    if (candidates.length === 0) {
        throw new Error('Annotated-file sequence does not match the open circular construct.');
    }
    if (candidates.length > 1) {
        throw new Error('Annotated-file sequence alignment is ambiguous because the construct contains repeated rotational matches.');
    }
    return candidates[0];
}

function modulo(value: number, modulus: number): number {
    return ((value % modulus) + modulus) % modulus;
}

function sourceSegments(feature: TransferFeature, length: number): Array<{ start: number; end: number }> {
    if (feature.segments && feature.segments.length > 0) return feature.segments;
    if (feature.start > feature.end) {
        return [
            { start: feature.start, end: length },
            { start: 0, end: feature.end },
        ];
    }
    return [{ start: feature.start, end: feature.end }];
}

function transformSegment(
    segment: { start: number; end: number },
    alignment: AnnotationSequenceAlignment,
): Array<{ start: number; end: number }> {
    const { length, reverseComplement: reverse, rotation } = alignment;
    const oriented = reverse
        ? { start: length - segment.end, end: length - segment.start }
        : segment;
    const segmentLength = oriented.end - oriented.start;
    if (segmentLength === length) return [{ start: 0, end: length }];

    const start = modulo(oriented.start - rotation, length);
    const end = modulo(oriented.end - rotation, length);
    if (start < end) return [{ start, end }];
    if (start > end) {
        return [
            { start, end: length },
            { start: 0, end },
        ].filter((part) => part.end > part.start);
    }
    return [];
}

export function transformFeatureForAlignment<T extends TransferFeature>(
    feature: T,
    alignment: AnnotationSequenceAlignment,
): T {
    const segments = sourceSegments(feature, alignment.length);
    const orientedSegments = alignment.reverseComplement ? [...segments].reverse() : segments;
    const transformedSegments = orientedSegments.flatMap((segment) => transformSegment(segment, alignment));
    if (transformedSegments.length === 0) {
        throw new Error(`Feature ${feature.name || feature.id} has no transferable coordinate span.`);
    }
    const starts = transformedSegments.map((segment) => segment.start);
    const ends = transformedSegments.map((segment) => segment.end);
    return {
        ...feature,
        start: Math.min(...starts),
        end: Math.max(...ends),
        strand: alignment.reverseComplement ? (feature.strand === 1 ? -1 : 1) : feature.strand,
        segments: transformedSegments,
    };
}
