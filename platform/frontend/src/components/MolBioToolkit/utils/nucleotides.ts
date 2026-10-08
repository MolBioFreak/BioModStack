export type SequenceType = 'dna' | 'rna';
export type NucleotideMoleculeStrandedness = 'single' | 'double' | 'unknown';
export type NucleotideMoleculeOrientation = 'positive' | 'negative' | 'ambisense' | 'not_applicable' | 'unknown';
export type NucleotideDisplayStrand = 'plus' | 'minus';

export interface NucleotideMoleculeMetadata {
    sequenceType: SequenceType;
    moleculeStrandedness: NucleotideMoleculeStrandedness;
    moleculeOrientation: NucleotideMoleculeOrientation;
    moleculeLabel: string;
}

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

type ParsedSequenceMetadata = Record<string, unknown> & {
    name?: unknown;
    description?: unknown;
    sequence?: string;
    type?: unknown;
    molType?: unknown;
    mol_type?: unknown;
    moleculeType?: unknown;
    sequenceType?: unknown;
    sequence_type?: unknown;
    sequenceTypeFromLocus?: unknown;
    strandedness?: unknown;
    moleculeStrandedness?: unknown;
    molecule_strandedness?: unknown;
    orientation?: unknown;
    moleculeOrientation?: unknown;
    molecule_orientation?: unknown;
    sense?: unknown;
    isRna?: unknown;
    isRNA?: unknown;
    isDNA?: unknown;
    isSingleStranded?: unknown;
    isDoubleStranded?: unknown;
    isSingleStrandedDNA?: unknown;
    isDoubleStrandedDNA?: unknown;
    isDoubleStrandedRNA?: unknown;
    isSingleStrandedRNA?: unknown;
    isPositiveSense?: unknown;
    isNegativeSense?: unknown;
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

function metadataToken(value: unknown): string {
    return typeof value === 'string' ? value.trim().toLowerCase().replace(/_/g, '-') : '';
}

function metadataLooksLikeRna(value: unknown): boolean {
    const token = metadataToken(value);
    return token === 'rna' || token.includes('rna');
}

function metadataLooksLikeDna(value: unknown): boolean {
    const token = metadataToken(value);
    return token === 'dna' || token.includes('dna');
}

function normalizeMetadataKey(key: string): string {
    return key.toLowerCase().replace(/[^a-z0-9]/g, '');
}

const POLYMER_METADATA_KEYS = new Set([
    // Deliberately excludes generic `type`: Teselagen FASTA parsing can fill
    // it from the alphabet, which is not strong molecule metadata.
    'moleculetype',
    'moltype',
    'sequencetype',
    'sequencetypefromlocus',
]);

const STRANDEDNESS_METADATA_KEYS = new Set([
    'type',
    'moleculetype',
    'moltype',
    'sequencetype',
    'sequencetypefromlocus',
    'strandedness',
    'moleculestrandedness',
    'strandtype',
]);

const ORIENTATION_METADATA_KEYS = new Set([
    'type',
    'moleculetype',
    'moltype',
    'sequencetype',
    'sequencetypefromlocus',
    'strandedness',
    'moleculestrandedness',
    'strandtype',
    'orientation',
    'moleculeorientation',
    'sense',
    'strandorientation',
]);

function collectStringLeaves(value: unknown, output: string[], depth = 0): void {
    if (value == null || depth > 5) {
        return;
    }
    if (typeof value === 'string') {
        output.push(value);
        return;
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
        output.push(String(value));
        return;
    }
    if (Array.isArray(value)) {
        value.forEach((entry) => collectStringLeaves(entry, output, depth + 1));
        return;
    }
    if (typeof value === 'object') {
        Object.values(value as Record<string, unknown>).forEach((entry) => collectStringLeaves(entry, output, depth + 1));
    }
}

function collectMetadataValues(
    value: unknown,
    keys: Set<string>,
    output: string[] = [],
    depth = 0,
): string[] {
    if (value == null || depth > 6) {
        return output;
    }
    if (Array.isArray(value)) {
        value.forEach((entry) => collectMetadataValues(entry, keys, output, depth + 1));
        return output;
    }
    if (typeof value !== 'object') {
        return output;
    }

    for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
        if (keys.has(normalizeMetadataKey(key))) {
            collectStringLeaves(entry, output);
        }
        collectMetadataValues(entry, keys, output, depth + 1);
    }

    return output;
}

function metadataTextLooksSingleStranded(value: unknown): boolean {
    const token = metadataToken(value);
    return /(^|[^a-z0-9])ss([^a-z0-9]|$)/.test(token)
        || token.includes('ssrna')
        || token.includes('ss-rna')
        || token.includes('ssdna')
        || token.includes('ss-dna')
        || token.includes('single-stranded')
        || token.includes('single-strand')
        || token.includes('single strand')
        || token.includes('(+)ss')
        || token.includes('(-)ss');
}

function metadataTextLooksDoubleStranded(value: unknown): boolean {
    const token = metadataToken(value);
    return /(^|[^a-z0-9])ds([^a-z0-9]|$)/.test(token)
        || token.includes('dsrna')
        || token.includes('ds-rna')
        || token.includes('dsdna')
        || token.includes('ds-dna')
        || token.includes('double-stranded')
        || token.includes('double-strand')
        || token.includes('double strand');
}

function metadataTextLooksPositiveSense(value: unknown): boolean {
    const token = metadataToken(value);
    return token === '+'
        || token.includes('(+)')
        || token.includes('positive-sense')
        || token.includes('positive sense')
        || token.includes('positive-strand')
        || token.includes('positive strand')
        || token.includes('plus-sense')
        || token.includes('plus sense')
        || token.includes('plus-strand')
        || token.includes('plus strand')
        || token.includes('+sense')
        || token === 'sense'
        || token === 'coding';
}

function metadataTextLooksNegativeSense(value: unknown): boolean {
    const token = metadataToken(value);
    return token === '-'
        || token.includes('(-)')
        || token.includes('negative-sense')
        || token.includes('negative sense')
        || token.includes('negative-strand')
        || token.includes('negative strand')
        || token.includes('minus-sense')
        || token.includes('minus sense')
        || token.includes('minus-strand')
        || token.includes('minus strand')
        || token.includes('-sense')
        || token.includes('antisense')
        || token.includes('anti-sense');
}

function metadataTextLooksAmbisense(value: unknown): boolean {
    const token = metadataToken(value);
    return token.includes('ambisense') || token.includes('ambi-sense') || token.includes('+/-') || token.includes('-/+');
}

export function normalizeNucleotideMoleculeStrandedness(
    strandedness: unknown,
    sequenceType: SequenceType,
): NucleotideMoleculeStrandedness {
    const token = metadataToken(strandedness);
    if (token === 'unknown' || token === 'not-known') {
        return 'unknown';
    }
    if (metadataTextLooksSingleStranded(strandedness)) {
        return 'single';
    }
    if (metadataTextLooksDoubleStranded(strandedness)) {
        return 'double';
    }
    return sequenceType === 'rna' ? 'single' : 'double';
}

export function normalizeNucleotideMoleculeOrientation(
    orientation: unknown,
    strandedness: NucleotideMoleculeStrandedness,
): NucleotideMoleculeOrientation {
    if (strandedness === 'double') {
        return 'not_applicable';
    }

    const token = metadataToken(orientation);
    if (token === 'unknown' || token === 'not-known' || token === '') {
        return 'unknown';
    }
    if (token === 'not-applicable' || token === 'n/a' || token === 'na') {
        return 'not_applicable';
    }
    if (metadataTextLooksAmbisense(orientation)) {
        return 'ambisense';
    }
    if (metadataTextLooksPositiveSense(orientation)) {
        return 'positive';
    }
    if (metadataTextLooksNegativeSense(orientation)) {
        return 'negative';
    }
    return 'unknown';
}

export function moleculeLabelForNucleotide(
    sequenceType: SequenceType,
    strandedness: NucleotideMoleculeStrandedness = sequenceType === 'rna' ? 'single' : 'double',
    orientation: NucleotideMoleculeOrientation = strandedness === 'double' ? 'not_applicable' : 'unknown',
): string {
    const polymer = sequenceType === 'rna' ? 'RNA' : 'DNA';
    if (strandedness === 'double') {
        return `ds${polymer}`;
    }
    if (strandedness === 'single') {
        if (orientation === 'positive') {
            return `(+)ss${polymer}`;
        }
        if (orientation === 'negative') {
            return `(-)ss${polymer}`;
        }
        if (orientation === 'ambisense') {
            return `ambisense ss${polymer}`;
        }
        return `ss${polymer}`;
    }
    return polymer;
}

export function displayStrandForMoleculeOrientation(
    orientation: NucleotideMoleculeOrientation | null | undefined,
): NucleotideDisplayStrand {
    return orientation === 'negative' ? 'minus' : 'plus';
}

export function displayStrandSymbol(strand: NucleotideDisplayStrand): '+' | '-' {
    return strand === 'minus' ? '-' : '+';
}

export function shouldReverseComplementForDisplay(
    sourceStrand: NucleotideDisplayStrand,
    displayStrand: NucleotideDisplayStrand,
): boolean {
    return sourceStrand !== displayStrand;
}

export function transformRangeForDisplayStrand(
    start: number,
    end: number,
    sequenceLength: number,
    sourceStrand: NucleotideDisplayStrand,
    displayStrand: NucleotideDisplayStrand,
): { start: number; end: number } {
    const lower = Math.max(0, Math.min(start, end, sequenceLength));
    const upper = Math.max(0, Math.min(Math.max(start, end), sequenceLength));

    if (!shouldReverseComplementForDisplay(sourceStrand, displayStrand)) {
        return { start: lower, end: upper };
    }

    return {
        start: sequenceLength - upper,
        end: sequenceLength - lower,
    };
}

export function transformDirectionForDisplayStrand<T extends 1 | -1>(
    direction: T,
    sourceStrand: NucleotideDisplayStrand,
    displayStrand: NucleotideDisplayStrand,
): T {
    return shouldReverseComplementForDisplay(sourceStrand, displayStrand)
        ? (-direction as T)
        : direction;
}

function hasExplicitPolymerMetadata(parsed: ParsedSequenceMetadata): boolean {
    return parsed.isRna === true
        || parsed.isRNA === true
        || parsed.isDNA === true
        || parsed.isDoubleStrandedRNA === true
        || parsed.isSingleStrandedRNA === true
        || parsed.isDoubleStrandedDNA === true
        || parsed.isSingleStrandedDNA === true
        // `type` is often a parser-inferred alphabet default for FASTA. Treat
        // locus/molecule fields as stronger evidence so known ssRNA viral FASTA
        // records with T-coded sequence can still import as RNA genomes.
        || metadataLooksLikeRna(parsed.molType)
        || metadataLooksLikeDna(parsed.molType)
        || metadataLooksLikeRna(parsed.mol_type)
        || metadataLooksLikeDna(parsed.mol_type)
        || metadataLooksLikeRna(parsed.moleculeType)
        || metadataLooksLikeDna(parsed.moleculeType)
        || metadataLooksLikeRna(parsed.sequenceType)
        || metadataLooksLikeDna(parsed.sequenceType)
        || metadataLooksLikeRna(parsed.sequence_type)
        || metadataLooksLikeDna(parsed.sequence_type)
        || metadataLooksLikeRna(parsed.sequenceTypeFromLocus)
        || metadataLooksLikeDna(parsed.sequenceTypeFromLocus)
        || metadataValuesFor(parsed, POLYMER_METADATA_KEYS).some((value) => metadataLooksLikeRna(value) || metadataLooksLikeDna(value));
}

function recordTextForHeuristics(parsed: ParsedSequenceMetadata): string {
    const directValues = [
        parsed.name,
        parsed.description,
        parsed.organism,
        parsed.accession,
        parsed.source_file,
        parsed.sourceFile,
    ];
    return directValues
        .filter((value): value is string => typeof value === 'string' && value.trim().length > 0)
        .join(' ')
        .toLowerCase();
}

function metadataLooksLikeNegativeStrandRnaVirus(parsed: ParsedSequenceMetadata): boolean {
    const text = recordTextForHeuristics(parsed);
    return /(^|[^a-z0-9])(andv|andes\s+virus|orthohantavirus|hantavirus)([^a-z0-9]|$)/i.test(text);
}

function firstMatchingMetadataValue(values: unknown[], predicate: (value: unknown) => boolean): unknown | undefined {
    return values.find((value) => predicate(value));
}

function metadataValuesFor(parsed: ParsedSequenceMetadata, keys: Set<string>): string[] {
    return collectMetadataValues(parsed, keys)
        .map((value) => value.trim())
        .filter((value) => value.length > 0);
}

export function inferNucleotideMoleculeMetadataFromParsedRecord(
    parsed: ParsedSequenceMetadata | null | undefined,
): NucleotideMoleculeMetadata {
    if (!parsed) {
        const sequenceType: SequenceType = 'dna';
        const moleculeStrandedness = normalizeNucleotideMoleculeStrandedness(undefined, sequenceType);
        const moleculeOrientation = normalizeNucleotideMoleculeOrientation(undefined, moleculeStrandedness);
        return {
            sequenceType,
            moleculeStrandedness,
            moleculeOrientation,
            moleculeLabel: moleculeLabelForNucleotide(sequenceType, moleculeStrandedness, moleculeOrientation),
        };
    }

    const explicitPolymerMetadata = hasExplicitPolymerMetadata(parsed);
    const knownNegativeStrandRnaVirus = metadataLooksLikeNegativeStrandRnaVirus(parsed);
    const useNegativeStrandRnaVirusHeuristic = !explicitPolymerMetadata && knownNegativeStrandRnaVirus;
    const sequenceType: SequenceType = useNegativeStrandRnaVirusHeuristic
        ? 'rna'
        : inferSequenceTypeFromParsedRecord(parsed);

    const strandednessCandidates = [
        parsed.moleculeStrandedness,
        parsed.molecule_strandedness,
        parsed.strandedness,
        parsed.type,
        parsed.molType,
        parsed.mol_type,
        parsed.moleculeType,
        parsed.sequenceType,
        parsed.sequence_type,
        parsed.sequenceTypeFromLocus,
        ...metadataValuesFor(parsed, STRANDEDNESS_METADATA_KEYS),
    ];

    const explicitStrandedness = parsed.isSingleStranded === true
        || parsed.isSingleStrandedDNA === true
        || parsed.isSingleStrandedRNA === true
        || firstMatchingMetadataValue(strandednessCandidates, metadataTextLooksSingleStranded)
        || (useNegativeStrandRnaVirusHeuristic ? 'ssRNA' : undefined);
    const explicitDoubleStrandedness = parsed.isDoubleStranded === true
        || parsed.isDoubleStrandedDNA === true
        || parsed.isDoubleStrandedRNA === true
        || firstMatchingMetadataValue(strandednessCandidates, metadataTextLooksDoubleStranded);

    const moleculeStrandedness = explicitStrandedness && !explicitDoubleStrandedness
        ? 'single'
        : explicitDoubleStrandedness
            ? 'double'
            : normalizeNucleotideMoleculeStrandedness(undefined, sequenceType);

    const orientationCandidates = [
        parsed.moleculeOrientation,
        parsed.molecule_orientation,
        parsed.orientation,
        parsed.sense,
        parsed.type,
        parsed.molType,
        parsed.mol_type,
        parsed.moleculeType,
        parsed.sequenceType,
        parsed.sequence_type,
        parsed.sequenceTypeFromLocus,
        ...metadataValuesFor(parsed, ORIENTATION_METADATA_KEYS),
    ];

    const explicitOrientation = parsed.isPositiveSense === true
        ? 'positive'
        : parsed.isNegativeSense === true
            ? 'negative'
            : firstMatchingMetadataValue(orientationCandidates, metadataTextLooksAmbisense)
                || firstMatchingMetadataValue(orientationCandidates, metadataTextLooksPositiveSense)
                || firstMatchingMetadataValue(orientationCandidates, metadataTextLooksNegativeSense)
                || (useNegativeStrandRnaVirusHeuristic ? 'negative-sense' : undefined);

    const moleculeOrientation = normalizeNucleotideMoleculeOrientation(explicitOrientation, moleculeStrandedness);

    return {
        sequenceType,
        moleculeStrandedness,
        moleculeOrientation,
        moleculeLabel: moleculeLabelForNucleotide(sequenceType, moleculeStrandedness, moleculeOrientation),
    };
}

export function hasExplicitNucleotideStrandednessMetadata(
    parsed: ParsedSequenceMetadata | null | undefined,
): boolean {
    if (!parsed) return false;
    if (
        parsed.isSingleStranded === true
        || parsed.isDoubleStranded === true
        || parsed.isSingleStrandedDNA === true
        || parsed.isDoubleStrandedDNA === true
        || parsed.isSingleStrandedRNA === true
        || parsed.isDoubleStrandedRNA === true
    ) {
        return true;
    }
    const candidates = [
        parsed.moleculeStrandedness,
        parsed.molecule_strandedness,
        parsed.strandedness,
        parsed.sequenceTypeFromLocus,
        ...metadataValuesFor(parsed, STRANDEDNESS_METADATA_KEYS),
    ];
    return candidates.some((value) => (
        metadataTextLooksSingleStranded(value) || metadataTextLooksDoubleStranded(value)
    ));
}

export function inferSequenceTypeFromParsedRecord(parsed: ParsedSequenceMetadata | null | undefined): SequenceType {
    if (!parsed) {
        return 'dna';
    }

    const explicitRna = parsed.isRna === true
        || parsed.isRNA === true
        || parsed.isDoubleStrandedRNA === true
        || parsed.isSingleStrandedRNA === true
        || metadataLooksLikeRna(parsed.type)
        || metadataLooksLikeRna(parsed.molType)
        || metadataLooksLikeRna(parsed.mol_type)
        || metadataLooksLikeRna(parsed.moleculeType)
        || metadataLooksLikeRna(parsed.sequenceType)
        || metadataLooksLikeRna(parsed.sequence_type)
        || metadataLooksLikeRna(parsed.sequenceTypeFromLocus);

    const explicitDna = parsed.isDNA === true
        || parsed.isDoubleStrandedDNA === true
        || parsed.isSingleStrandedDNA === true
        || metadataLooksLikeDna(parsed.type)
        || metadataLooksLikeDna(parsed.molType)
        || metadataLooksLikeDna(parsed.mol_type)
        || metadataLooksLikeDna(parsed.moleculeType)
        || metadataLooksLikeDna(parsed.sequenceType)
        || metadataLooksLikeDna(parsed.sequence_type)
        || metadataLooksLikeDna(parsed.sequenceTypeFromLocus);

    if (explicitRna && !explicitDna) {
        return 'rna';
    }

    if (explicitDna) {
        return 'dna';
    }

    if (explicitRna) {
        return 'rna';
    }

    return inferSequenceTypeFromSequence(parsed.sequence || '');
}

function canonicalizeAlphabetForType(sequence: string, sequenceType: SequenceType): string {
    const compact = sequence.toUpperCase().replace(/\s+/g, '');
    return sequenceType === 'rna'
        ? compact.replace(/T/g, 'U')
        : compact.replace(/U/g, 'T');
}

export function normalizeSequenceForType(sequence: string, sequenceType: SequenceType): string {
    const canonical = canonicalizeAlphabetForType(sequence, sequenceType);
    const valid = sequenceType === 'rna' ? RNA_VALID : DNA_VALID;
    return canonical
        .split('')
        .filter((char) => valid.has(char))
        .join('');
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
    const canonical = canonicalizeAlphabetForType(compact, sequenceType);
    const valid = sequenceType === 'rna' ? RNA_VALID : DNA_VALID;

    const invalidCharacters = Array.from(
        new Set(canonical.split('').filter((char) => !valid.has(char)))
    ).sort();

    const sequence = canonical
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

export function sequenceForDisplayStrand(
    sequence: string,
    sequenceType: SequenceType,
    sourceStrand: NucleotideDisplayStrand,
    displayStrand: NucleotideDisplayStrand,
): string {
    const normalized = normalizeSequenceForType(sequence, sequenceType);
    return sourceStrand === displayStrand
        ? normalized
        : reverseComplementSequence(normalized, sequenceType);
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
