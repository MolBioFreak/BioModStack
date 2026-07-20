import {
    findPatternPositions,
    reverseComplementSequence,
    type SequenceType,
} from './nucleotides.js';

export interface SequenceSearchMatch {
    start: number;
    end: number;
    strand: 1 | -1;
    sequence: string;
    segments: Array<{ start: number; end: number }>;
}

function exactCaseSensitivePositions(sequence: string, query: string, circular: boolean): number[] {
    if (!sequence || !query || query.length > sequence.length) {
        return [];
    }
    const searchSpace = circular ? sequence + sequence.slice(0, query.length - 1) : sequence;
    const limit = circular ? sequence.length : sequence.length - query.length + 1;
    const positions: number[] = [];
    for (let start = 0; start < limit; start += 1) {
        if (searchSpace.slice(start, start + query.length) === query) {
            positions.push(start);
        }
    }
    return positions;
}

function matchAt(
    sequence: string,
    start: number,
    matchLength: number,
    strand: 1 | -1,
): SequenceSearchMatch {
    const sequenceLength = sequence.length;
    const rawEnd = start + matchLength;
    const segments = rawEnd <= sequenceLength
        ? [{ start, end: rawEnd }]
        : [
            { start, end: sequenceLength },
            { start: 0, end: rawEnd % sequenceLength },
        ].filter((segment) => segment.end > segment.start);
    return {
        start,
        end: rawEnd <= sequenceLength ? rawEnd : rawEnd % sequenceLength,
        strand,
        sequence: segments.map((segment) => sequence.slice(segment.start, segment.end)).join(''),
        segments,
    };
}

export function findExactSequenceMatches(
    sequence: string,
    query: string,
    options: {
        circular?: boolean;
        bothStrands?: boolean;
        caseSensitive?: boolean;
        sequenceType?: SequenceType;
    } = {},
): SequenceSearchMatch[] {
    const circular = options.circular ?? false;
    const bothStrands = options.bothStrands ?? true;
    const caseSensitive = options.caseSensitive ?? false;
    const sequenceType = options.sequenceType ?? 'dna';
    const target = caseSensitive ? sequence : sequence.toUpperCase();
    const forwardQuery = caseSensitive ? query : query.toUpperCase();
    if (!target || !forwardQuery || forwardQuery.length > target.length) {
        return [];
    }

    const positionsFor = (pattern: string) => (
        caseSensitive
            ? exactCaseSensitivePositions(target, pattern, circular)
            : findPatternPositions(target, pattern, { circular })
    );
    const matches: SequenceSearchMatch[] = [];
    const seen = new Set<string>();
    for (const position of positionsFor(forwardQuery)) {
        matches.push(matchAt(sequence, position, forwardQuery.length, 1));
        seen.add(`${position}:1`);
    }

    if (bothStrands) {
        const reverseQuery = reverseComplementSequence(forwardQuery, sequenceType);
        const palindromic = reverseQuery === forwardQuery;
        for (const position of positionsFor(reverseQuery)) {
            const key = palindromic ? `${position}:1` : `${position}:-1`;
            if (seen.has(key)) {
                continue;
            }
            matches.push(matchAt(sequence, position, reverseQuery.length, -1));
            seen.add(key);
        }
    }

    return matches.sort((left, right) => left.start - right.start || right.strand - left.strand);
}
