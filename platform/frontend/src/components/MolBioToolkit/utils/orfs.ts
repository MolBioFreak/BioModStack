export interface OpenReadingFrame {
    start: number;
    end: number;
    strand: 1 | -1;
    frame?: 1 | 2 | 3;
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

/**
 * Find open reading frames across all six coding frames.
 * RNA input is normalized to DNA codon symbols internally so AUG/UAA/UAG/UGA work.
 */
export function findOpenReadingFrames(sequence: string, minLength: number = 100): OpenReadingFrame[] {
    const orfs: OpenReadingFrame[] = [];
    const seq = normalizeCodingSequence(sequence);
    const startCodon = 'ATG';
    const stopCodons = ['TAA', 'TAG', 'TGA'];

    for (const strand of [1, -1] as const) {
        const workSeq = strand === 1 ? seq : reverseComplementCodingSequence(seq);

        for (let frame = 0; frame < 3; frame += 1) {
            let index = frame;
            while (index < workSeq.length - 2) {
                const codon = workSeq.substring(index, index + 3);
                if (codon === startCodon) {
                    for (let stopIndex = index + 3; stopIndex < workSeq.length - 2; stopIndex += 3) {
                        const testCodon = workSeq.substring(stopIndex, stopIndex + 3);
                        if (stopCodons.includes(testCodon)) {
                            const orfLength = stopIndex + 3 - index;
                            if (orfLength >= minLength) {
                                const start = strand === 1 ? index : seq.length - (stopIndex + 3);
                                const end = strand === 1 ? stopIndex + 3 : seq.length - index;
                                const frameNum = (frame + 1) as 1 | 2 | 3;
                                orfs.push({ start, end, strand, frame: frameNum });
                            }
                            break;
                        }
                    }
                }
                index += 3;
            }
        }
    }

    return orfs
        .sort((left, right) => (right.end - right.start) - (left.end - left.start))
        .slice(0, 20);
}
