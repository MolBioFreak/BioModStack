export interface OpenReadingFrame {
    start: number;
    end: number;
    strand: 1 | -1;
    frame?: 1 | 2 | 3;
    length: number;
    segments: Array<{ start: number; end: number }>;
}

const CODING_COMPLEMENT: Record<string, string> = {
    A: 'T',
    T: 'A',
    U: 'A',
    G: 'C',
    C: 'G',
    R: 'Y',
    Y: 'R',
    S: 'S',
    W: 'W',
    K: 'M',
    M: 'K',
    B: 'V',
    D: 'H',
    H: 'D',
    V: 'B',
    N: 'N',
};

const STOP_CODONS = new Set(['TAA', 'TAG', 'TGA']);

function normalizeCodingSequence(sequence: string): string {
    return sequence.toUpperCase().replace(/U/g, 'T');
}

export function reverseComplementCodingSequence(sequence: string): string {
    return normalizeCodingSequence(sequence)
        .split('')
        .reverse()
        .map((base) => CODING_COMPLEMENT[base] || base)
        .join('');
}

function splitForwardRange(start: number, length: number, sequenceLength: number) {
    const rawEnd = start + length;
    if (rawEnd <= sequenceLength) {
        return [{ start, end: rawEnd }];
    }
    const wrappedEnd = rawEnd % sequenceLength;
    return [
        { start, end: sequenceLength },
        ...(wrappedEnd > 0 ? [{ start: 0, end: wrappedEnd }] : []),
    ];
}

function mapWorkSegmentsToSource(
    workSegments: Array<{ start: number; end: number }>,
    strand: 1 | -1,
    sequenceLength: number,
) {
    if (strand === 1) {
        return workSegments.map((segment) => ({ ...segment }));
    }
    return workSegments.map((segment) => ({
        start: sequenceLength - segment.end,
        end: sequenceLength - segment.start,
    }));
}

/**
 * Find start-to-first-stop ORFs on both strands. Circular scans may cross the
 * origin but are bounded to one revolution, and all returned segments remain
 * in zero-based half-open source coordinates.
 */
export function findOpenReadingFrames(
    sequence: string,
    minLength: number = 100,
    circular = false,
): OpenReadingFrame[] {
    const seq = normalizeCodingSequence(sequence);
    const sequenceLength = seq.length;
    if (sequenceLength < 3) {
        return [];
    }

    const orfs: OpenReadingFrame[] = [];
    for (const strand of [1, -1] as const) {
        const workSeq = strand === 1 ? seq : reverseComplementCodingSequence(seq);
        const scanSeq = circular ? workSeq + workSeq.slice(0, Math.max(0, sequenceLength - 1)) : workSeq;

        for (let start = 0; start <= sequenceLength - 3; start += 1) {
            if (scanSeq.slice(start, start + 3) !== 'ATG') {
                continue;
            }
            const maximumEnd = circular ? start + sequenceLength : sequenceLength;
            for (let stop = start + 3; stop + 3 <= maximumEnd; stop += 3) {
                if (!STOP_CODONS.has(scanSeq.slice(stop, stop + 3))) {
                    continue;
                }
                const length = stop + 3 - start;
                if (length >= minLength) {
                    const workSegments = splitForwardRange(start, length, sequenceLength);
                    const segments = mapWorkSegmentsToSource(workSegments, strand, sequenceLength);
                    const boundsStart = Math.min(...segments.map((segment) => segment.start));
                    const boundsEnd = Math.max(...segments.map((segment) => segment.end));
                    orfs.push({
                        start: boundsStart,
                        end: boundsEnd,
                        strand,
                        frame: ((start % 3) + 1) as 1 | 2 | 3,
                        length,
                        segments,
                    });
                }
                break;
            }
        }
    }

    const deduplicated = new Map<string, OpenReadingFrame>();
    for (const orf of orfs) {
        const key = `${orf.strand}:${orf.frame}:${orf.segments.map((segment) => `${segment.start}-${segment.end}`).join(',')}`;
        if (!deduplicated.has(key)) {
            deduplicated.set(key, orf);
        }
    }
    return [...deduplicated.values()]
        .sort((left, right) => right.length - left.length || left.start - right.start || left.strand - right.strand)
        .slice(0, 20);
}
