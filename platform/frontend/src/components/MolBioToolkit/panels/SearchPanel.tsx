/**
 * SearchPanel - Find sequences, motifs, and patterns in the current sequence
 */

import { useState, useMemo, useCallback } from 'react';
import type { SequenceData, HighlightedRegion } from '../types';

interface SearchPanelProps {
    sequenceData: SequenceData;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onJumpToPosition?: (position: number) => void;
}

interface SearchResult {
    start: number;
    end: number;
    strand: 1 | -1;
    sequence: string;
}

// Common motif patterns
const COMMON_MOTIFS = [
    { name: 'Kozak', pattern: 'GCCACCATGG', description: 'Translation initiation' },
    { name: 'Start Codon', pattern: 'ATG', description: 'Methionine start' },
    { name: 'Stop Codons', pattern: 'TAA|TAG|TGA', description: 'Translation termination', isRegex: true },
    { name: 'TATA Box', pattern: 'TATAA', description: 'Promoter element' },
    { name: 'Poly-A Signal', pattern: 'AATAAA', description: 'Polyadenylation signal' },
    { name: 'CpG Islands', pattern: 'CG', description: 'Methylation sites' },
    { name: 'ShineDalgarno', pattern: 'AGGAGG', description: 'Ribosome binding (prokaryotic)' },
];

// Generate reverse complement
function reverseComplement(seq: string): string {
    const complement: Record<string, string> = {
        'A': 'T', 'T': 'A', 'G': 'C', 'C': 'G',
        'a': 't', 't': 'a', 'g': 'c', 'c': 'g',
        'N': 'N', 'n': 'n',
    };
    return seq.split('').reverse().map(c => complement[c] || c).join('');
}

export function SearchPanel({
    sequenceData,
    onHighlight,
    onJumpToPosition
}: SearchPanelProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [searchBothStrands, setSearchBothStrands] = useState(true);
    const [caseSensitive, setCaseSensitive] = useState(false);
    const [useRegex, setUseRegex] = useState(false);
    const [selectedResult, setSelectedResult] = useState<number | null>(null);

    // Perform search
    const searchResults = useMemo((): SearchResult[] => {
        if (!searchQuery.trim() || searchQuery.length < 2) return [];

        const results: SearchResult[] = [];
        const sequence = caseSensitive
            ? sequenceData.sequence
            : sequenceData.sequence.toUpperCase();
        const query = caseSensitive ? searchQuery : searchQuery.toUpperCase();

        try {
            if (useRegex) {
                // Regex search
                const regex = new RegExp(query, caseSensitive ? 'g' : 'gi');
                let match;
                while ((match = regex.exec(sequence)) !== null) {
                    results.push({
                        start: match.index,
                        end: match.index + match[0].length,
                        strand: 1,
                        sequence: sequenceData.sequence.substring(match.index, match.index + match[0].length)
                    });
                    // Prevent infinite loops for zero-length matches
                    if (match[0].length === 0) regex.lastIndex++;
                }
            } else {
                // Simple string search
                let pos = sequence.indexOf(query);
                while (pos !== -1) {
                    results.push({
                        start: pos,
                        end: pos + query.length,
                        strand: 1,
                        sequence: sequenceData.sequence.substring(pos, pos + query.length)
                    });
                    pos = sequence.indexOf(query, pos + 1);
                }
            }

            // Search reverse strand if enabled
            if (searchBothStrands && !useRegex) {
                const revQuery = reverseComplement(query);
                let pos = sequence.indexOf(revQuery);
                while (pos !== -1) {
                    results.push({
                        start: pos,
                        end: pos + revQuery.length,
                        strand: -1,
                        sequence: sequenceData.sequence.substring(pos, pos + revQuery.length)
                    });
                    pos = sequence.indexOf(revQuery, pos + 1);
                }
            }
        } catch (e) {
            // Invalid regex - return empty results
            console.warn('Search error:', e);
        }

        // Sort by position
        return results.sort((a, b) => a.start - b.start);
    }, [searchQuery, sequenceData.sequence, searchBothStrands, caseSensitive, useRegex]);

    // Update highlights when results change
    const updateHighlights = useCallback(() => {
        const regions: HighlightedRegion[] = searchResults.map((r, i) => ({
            start: r.start,
            end: r.end,
            color: selectedResult === i ? '#f59e0b' : (r.strand === 1 ? '#22c55e' : '#ef4444'),
            label: `${r.strand === 1 ? '→' : '←'} ${r.start + 1}-${r.end}`
        }));
        onHighlight(regions);
    }, [searchResults, selectedResult, onHighlight]);

    // Navigate to result
    const goToResult = useCallback((index: number) => {
        if (index < 0 || index >= searchResults.length) return;
        setSelectedResult(index);
        updateHighlights();
        if (onJumpToPosition) {
            onJumpToPosition(searchResults[index].start);
        }
    }, [searchResults, onJumpToPosition, updateHighlights]);

    // Quick motif search
    const searchMotif = useCallback((pattern: string, isRegex?: boolean) => {
        setSearchQuery(pattern);
        setUseRegex(isRegex || false);
    }, []);

    // Clear search
    const clearSearch = useCallback(() => {
        setSearchQuery('');
        setSelectedResult(null);
        onHighlight([]);
    }, [onHighlight]);

    return (
        <div className="search-panel p-3 space-y-4 text-sm">
            <h4 className="font-semibold text-slate-200">Find Sequence</h4>

            {/* Search input */}
            <div className="space-y-2">
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Enter sequence or pattern..."
                        className="flex-1 px-2 py-1.5 bg-slate-800 border border-slate-600 rounded text-sm font-mono focus:outline-none focus:border-blue-500"
                    />
                    <button
                        onClick={updateHighlights}
                        disabled={!searchQuery.trim()}
                        className="px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-xs font-medium transition-colors"
                    >
                        Find
                    </button>
                    {searchQuery && (
                        <button
                            onClick={clearSearch}
                            className="px-2 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-xs transition-colors"
                        >
                            ✕
                        </button>
                    )}
                </div>

                {/* Options */}
                <div className="flex flex-wrap gap-3 text-xs text-slate-400">
                    <label className="flex items-center gap-1 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={searchBothStrands}
                            onChange={(e) => setSearchBothStrands(e.target.checked)}
                            className="w-3 h-3"
                        />
                        Both strands
                    </label>
                    <label className="flex items-center gap-1 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={caseSensitive}
                            onChange={(e) => setCaseSensitive(e.target.checked)}
                            className="w-3 h-3"
                        />
                        Case sensitive
                    </label>
                    <label className="flex items-center gap-1 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={useRegex}
                            onChange={(e) => setUseRegex(e.target.checked)}
                            className="w-3 h-3"
                        />
                        Regex
                    </label>
                </div>
            </div>

            {/* Common motifs */}
            <div className="space-y-2">
                <div className="text-xs text-slate-400">Quick motif search:</div>
                <div className="flex flex-wrap gap-1">
                    {COMMON_MOTIFS.map(motif => (
                        <button
                            key={motif.name}
                            onClick={() => searchMotif(motif.pattern, motif.isRegex)}
                            className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded text-xs transition-colors"
                            title={`${motif.description}: ${motif.pattern}`}
                        >
                            {motif.name}
                        </button>
                    ))}
                </div>
            </div>

            {/* Results */}
            {searchResults.length > 0 && (
                <div className="space-y-2">
                    <div className="flex items-center justify-between">
                        <span className="text-xs text-slate-400">
                            {searchResults.length} result{searchResults.length !== 1 ? 's' : ''} found
                        </span>
                        <div className="flex gap-1">
                            <button
                                onClick={() => goToResult((selectedResult ?? 0) - 1 < 0 ? searchResults.length - 1 : (selectedResult ?? 0) - 1)}
                                className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded text-xs"
                            >
                                ← Prev
                            </button>
                            <button
                                onClick={() => goToResult(((selectedResult ?? -1) + 1) % searchResults.length)}
                                className="px-2 py-0.5 bg-slate-700 hover:bg-slate-600 rounded text-xs"
                            >
                                Next →
                            </button>
                        </div>
                    </div>

                    <div className="max-h-48 overflow-y-auto space-y-1">
                        {searchResults.slice(0, 100).map((result, i) => (
                            <div
                                key={`${result.start}-${result.strand}`}
                                onClick={() => goToResult(i)}
                                className={`flex items-center justify-between px-2 py-1 rounded cursor-pointer text-xs transition-colors ${selectedResult === i
                                        ? 'bg-amber-900/50 text-amber-300'
                                        : 'bg-slate-700/50 hover:bg-slate-700 text-slate-300'
                                    }`}
                            >
                                <div className="flex items-center gap-2">
                                    <span className={`w-4 text-center ${result.strand === 1 ? 'text-emerald-400' : 'text-red-400'}`}>
                                        {result.strand === 1 ? '→' : '←'}
                                    </span>
                                    <span className="font-mono">{result.start + 1}</span>
                                </div>
                                <span className="font-mono text-slate-500 truncate max-w-[120px]">
                                    {result.sequence}
                                </span>
                            </div>
                        ))}
                        {searchResults.length > 100 && (
                            <div className="text-xs text-slate-500 text-center py-1">
                                + {searchResults.length - 100} more results
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Empty state */}
            {searchQuery.length >= 2 && searchResults.length === 0 && (
                <div className="text-center text-slate-500 text-xs py-4">
                    No matches found
                </div>
            )}
        </div>
    );
}
