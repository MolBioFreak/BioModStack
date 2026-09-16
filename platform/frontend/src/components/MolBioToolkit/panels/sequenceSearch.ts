import { findExactSequenceMatches } from '../utils/search';
export interface SearchInput {
    sequence: string;
    query: string;
    circular: boolean;
    caseSensitive: boolean;
    regex: boolean;
    bothStrands: boolean;
    sequenceType: 'dna' | 'rna';
}
export interface SearchMatch {
    start: number;
    end: number;
    strand: 1 | -1;
    sequence: string;
    segments: Array<{ start: number; end: number }>;
}
/** Called only in the interruptible worker, never in React render. */
export function searchSequence(input: SearchInput): SearchMatch[] {
    if (!input.query.trim() || input.query.length < 2) return [];
    if (!input.regex) {
        const matches = findExactSequenceMatches(input.sequence, input.query, {
            circular: input.circular, bothStrands: input.bothStrands,
            caseSensitive: input.caseSensitive, sequenceType: input.sequenceType,
        });
        if (matches.length > 10000) throw new Error('Search exceeds 10,000 matches. Narrow the pattern; no partial result was applied.');
        return matches;
    }
    // Preserve escapes (\s, \b, etc.); case insensitivity belongs in flags.
    const regex = new RegExp(input.query, input.caseSensitive ? 'g' : 'gi');
    const length = input.sequence.length;
    const text = input.circular ? input.sequence + input.sequence.slice(0, Math.max(0, length - 1)) : input.sequence;
    const results: SearchMatch[] = [];
    let match: RegExpExecArray | null;
    while ((match = regex.exec(text)) !== null) {
        if (match.index >= length) break;
        if (match[0].length > length) continue;
        const rawEnd = match.index + match[0].length;
        const segments = rawEnd <= length
            ? [{ start: match.index, end: rawEnd }]
            : [{ start: match.index, end: length }, { start: 0, end: rawEnd % length }].filter(segment => segment.end > segment.start);
        results.push({ start: match.index, end: rawEnd <= length ? rawEnd : rawEnd % length,
            strand: 1, sequence: segments.map(segment => input.sequence.slice(segment.start, segment.end)).join(''), segments });
        if (results.length > 10000) throw new Error('Search exceeds 10,000 matches. Narrow the pattern; no partial result was applied.');
        if (match[0].length === 0) regex.lastIndex++;
    }
    return results;
}
