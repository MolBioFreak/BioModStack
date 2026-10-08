export type SequenceType = 'dna' | 'rna';

const DNA_VALID = new Set('ATCGNRYMKSWHBVD'.split(''));
const RNA_VALID = new Set('AUCGNRYMKSWHBVD'.split(''));
const NUCLEOTIDE_VALID = new Set('ATUCGNRYMKSWHBVD'.split(''));

const MATCH_BASES: Record<string, string> = {
    A: 'A',
    C: 'C',
    G: 'G',
    T: 'T',
    U: 'T',
    R: 'AG',
    Y: 'CT',
    S: 'GC',
    W: 'AT',
    K: 'GT',
    M: 'AC',
    B: 'CGT',
    D: 'AGT',
    H: 'ACT',
    V: 'ACG',
    N: 'ACGT',
};

const DNA_COMPLEMENT: Record<string, string> = {
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

const RNA_COMPLEMENT: Record<string, string> = {
    ...DNA_COMPLEMENT,
    A: 'U',
    T: 'A',
    U: 'A',
};

export interface ParsedSequenceInput {
    name: string;
    sequence: string;
    sequenceType: SequenceType;
    invalidCharacters: string[];
}

export interface PrimerBindingMatch {
    start: number;
    end: number;
    annealLength: number;
    overhangLength: number;
    matchedSequence: string;
}

export function inferSequenceTypeFromSequence(sequence: string): SequenceType {
    const upper = sequence.toUpperCase();
    if (upper.includes('U') && !upper.includes('T')) {
        return 'rna';
    }
    return 'dna';
}

export function stripFastaHeaders(raw: string): { name: string; sequenceText: string } {
    const lines = raw.split(/\r?\n/).map((line) => line.trim());
    const header = lines.find((line) => line.startsWith('>')) || '';
    const name = header.replace(/^>\s*/, '').trim() || 'Untitled Sequence';
    const sequenceText = lines
        .filter((line) => line.length > 0 && !line.startsWith('>'))
        .join('');
    return { name, sequenceText };
}

export function parseSequenceInput(raw: string, preferredType?: SequenceType): ParsedSequenceInput {
    const { name, sequenceText } = stripFastaHeaders(raw);
    const compact = sequenceText.replace(/\s+/g, '').toUpperCase();
    const sequenceType = preferredType || inferSequenceTypeFromSequence(compact);
    const valid = sequenceType === 'rna' ? RNA_VALID : DNA_VALID;

    const invalidCharacters = Array.from(
        new Set(compact.split('').filter((char) => !valid.has(char)))
    ).sort();

    const sequence = compact
        .split('')
        .filter((char) => valid.has(char))
        .join('');

    return {
        name,
        sequence,
        sequenceType,
        invalidCharacters,
    };
}

export function cleanSequenceForType(sequence: string, sequenceType: SequenceType): string {
    return parseSequenceInput(sequence, sequenceType).sequence;
}

export function isValidSequenceForType(sequence: string, sequenceType: SequenceType): boolean {
    return parseSequenceInput(sequence, sequenceType).invalidCharacters.length === 0;
}

export function cleanNucleotideSequence(sequence: string): string {
    return sequence
        .toUpperCase()
        .replace(/\s+/g, '')
        .split('')
        .filter((char) => NUCLEOTIDE_VALID.has(char))
        .join('');
}

export function isValidNucleotideSequence(sequence: string): boolean {
    return Array.from(
        new Set(
            sequence
                .toUpperCase()
                .replace(/\s+/g, '')
                .split('')
                .filter((char) => !NUCLEOTIDE_VALID.has(char)),
        ),
    ).length === 0;
}

export function calculateGcPercent(sequence: string): number {
    if (!sequence) return 0;
    const upper = sequence.toUpperCase();
    const gc = (upper.match(/[GC]/g) || []).length;
    return Math.round((gc / upper.length) * 100);
}

export function sequenceUnitLabel(sequenceType: SequenceType): 'bp' | 'nt' {
    return sequenceType === 'rna' ? 'nt' : 'bp';
}

export function reverseComplementSequence(sequence: string, sequenceType: SequenceType = 'dna'): string {
    const complement = sequenceType === 'rna' ? RNA_COMPLEMENT : DNA_COMPLEMENT;
    return sequence
        .toUpperCase()
        .split('')
        .reverse()
        .map((base) => complement[base] || base)
        .join('');
}

export function complementSequence(sequence: string, sequenceType: SequenceType = 'dna'): string {
    const complement = sequenceType === 'rna' ? RNA_COMPLEMENT : DNA_COMPLEMENT;
    return sequence
        .toUpperCase()
        .split('')
        .map((base) => complement[base] || base)
        .join('');
}

function normalizeForMatching(sequence: string): string {
    return sequence
        .toUpperCase()
        .replace(/\s+/g, '')
        .split('')
        .filter((char) => Boolean(MATCH_BASES[char]))
        .map((char) => (char === 'U' ? 'T' : char))
        .join('');
}

function baseMatches(sequenceBase: string, patternBase: string): boolean {
    const allowed = MATCH_BASES[patternBase] || patternBase;
    return allowed.includes(sequenceBase);
}

export function findPatternPositions(
    sequence: string,
    pattern: string,
    options: { circular?: boolean } = {},
): number[] {
    const target = normalizeForMatching(sequence);
    const query = normalizeForMatching(pattern);
    if (!target || !query || query.length > target.length) {
        return [];
    }

    const circular = options.circular ?? false;
    const searchSpace = circular
        ? target + target.slice(0, query.length - 1)
        : target;
    const searchLimit = circular
        ? target.length
        : target.length - query.length + 1;

    const positions: number[] = [];
    for (let start = 0; start < searchLimit; start += 1) {
        let matches = true;
        for (let offset = 0; offset < query.length; offset += 1) {
            if (!baseMatches(searchSpace[start + offset], query[offset])) {
                matches = false;
                break;
            }
        }
        if (matches) {
            positions.push(start);
        }
    }

    return positions;
}

export function resolvePrimerBindings(
    sequence: string,
    primer: string,
    options: {
        reverse?: boolean;
        sequenceType?: SequenceType;
        circular?: boolean;
        minAnnealLength?: number;
    } = {},
): PrimerBindingMatch[] {
    const sequenceType = options.sequenceType ?? 'dna';
    const primerSequence = cleanNucleotideSequence(primer);
    if (!primerSequence) {
        return [];
    }

    const circular = options.circular ?? false;
    const reverse = options.reverse ?? false;
    const minAnnealLength = Math.max(
        1,
        Math.min(primerSequence.length, options.minAnnealLength ?? 8),
    );

    for (let annealLength = primerSequence.length; annealLength >= minAnnealLength; annealLength -= 1) {
        const annealSequence = primerSequence.slice(primerSequence.length - annealLength);
        const matchedSequence = reverse
            ? reverseComplementSequence(annealSequence, sequenceType)
            : annealSequence;
        const positions = findPatternPositions(sequence, matchedSequence, { circular });
        if (positions.length > 0) {
            return positions.map((start) => ({
                start,
                end: start + annealLength,
                annealLength,
                overhangLength: primerSequence.length - annealLength,
                matchedSequence,
            }));
        }
    }

    return [];
}
