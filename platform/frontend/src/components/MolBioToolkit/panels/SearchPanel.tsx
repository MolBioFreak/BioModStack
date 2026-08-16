/**
 * SearchPanel - Find sequences, motifs, ORFs, and patterns in the current sequence
 */

import { useState, useMemo, useCallback, useEffect } from 'react';
import type { SequenceData, HighlightedRegion } from '../types';
import type { Translation } from '../SequenceViewer';
import { findOpenReadingFrames, type OpenReadingFrame } from '../utils/orfs';
import { findExactSequenceMatches } from '../utils/search';

interface SearchPanelProps {
    sequenceData: SequenceData;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onJumpToPosition?: (position: number) => void;
    onOrfsFound?: (orfs: Translation[]) => void;
}

interface SearchResult {
    start: number;
    end: number;
    strand: 1 | -1;
    sequence: string;
    segments: Array<{ start: number; end: number }>;
}

type ORF = OpenReadingFrame;

// Expanded motif patterns organized by category
const MOTIF_CATEGORIES = [
    {
        name: 'Translation',
        motifs: [
            { name: 'Kozak', pattern: 'GCCACCATGG', description: 'Optimal translation initiation (vertebrate)' },
            { name: 'Kozak (min)', pattern: '[AG]CCATGG', description: 'Minimal Kozak consensus', isRegex: true },
            { name: 'ATG', pattern: 'ATG', description: 'Start codon (Methionine)' },
            { name: 'Stop', pattern: 'TAA|TAG|TGA', description: 'Stop codons', isRegex: true },
            { name: 'Shine-Dalgarno', pattern: 'AGGAGG', description: 'Ribosome binding site (prokaryotic)' },
            { name: 'RBS Consensus', pattern: 'AGGAG[AG]', description: 'RBS extended consensus', isRegex: true },
        ]
    },
    {
        name: 'Promoters',
        motifs: [
            { name: 'TATA Box', pattern: 'TATAAA', description: 'TATA box promoter (-25 to -30)' },
            { name: 'TATA (var)', pattern: 'TATA[AT]A[AT]', description: 'TATA box degenerate', isRegex: true },
            { name: 'CAAT Box', pattern: 'CCAAT', description: 'CAAT box (-75 to -80)' },
            { name: 'GC Box', pattern: 'GGGCGG', description: 'SP1 binding (GC-rich promoter)' },
            { name: '-10 Box', pattern: 'TATAAT', description: 'Pribnow box (prokaryotic -10)' },
            { name: '-35 Box', pattern: 'TTGACA', description: 'Prokaryotic -35 consensus' },
            { name: 'T7 Promoter', pattern: 'TAATACGACTCACTATA', description: 'T7 RNA polymerase' },
            { name: 'SP6 Promoter', pattern: 'ATTTAGGTGACACTATAG', description: 'SP6 RNA polymerase' },
        ]
    },
    {
        name: 'Terminators',
        motifs: [
            { name: 'Poly-A Signal', pattern: 'AATAAA', description: 'Polyadenylation signal (canonical)' },
            { name: 'Poly-A (alt)', pattern: 'ATTAAA', description: 'Alternate poly-A signal' },
            { name: 'BGH pA', pattern: 'AATAAA.{10,30}CA', description: 'BGH polyadenylation', isRegex: true },
        ]
    },
    {
        name: 'Regulatory',
        motifs: [
            { name: 'CpG', pattern: 'CG', description: 'CpG dinucleotide (methylation site)' },
            { name: 'E-box', pattern: 'CANNTG', description: 'Enhancer box (bHLH binding)', isRegex: true },
            { name: 'AP-1', pattern: 'TGA[CG]TCA', description: 'AP-1 binding site', isRegex: true },
            { name: 'NF-κB', pattern: 'GGG[AG][AT]T[TC]CC', description: 'NF-κB consensus', isRegex: true },
            { name: 'Oct-1', pattern: 'ATGCAAAT', description: 'Octamer motif' },
        ]
    },
    {
        name: 'Restriction',
        motifs: [
            { name: 'EcoRI', pattern: 'GAATTC', description: 'EcoRI recognition' },
            { name: 'BamHI', pattern: 'GGATCC', description: 'BamHI recognition' },
            { name: 'HindIII', pattern: 'AAGCTT', description: 'HindIII recognition' },
            { name: 'XhoI', pattern: 'CTCGAG', description: 'XhoI recognition' },
            { name: 'NotI', pattern: 'GCGGCCGC', description: 'NotI recognition (8-cutter)' },
            { name: 'SacI', pattern: 'GAGCTC', description: 'SacI recognition' },
        ]
    },
    {
        name: 'Cloning',
        motifs: [
            { name: 'MCS', pattern: 'GAATTC.{0,200}GGATCC', description: 'Common MCS region', isRegex: true },
            { name: 'attB1', pattern: 'ACAAGTTTGTACAAAAAAGCAGGCT', description: 'Gateway attB1' },
            { name: 'attB2', pattern: 'ACCCAGCTTTCTTGTACAAAGTGGT', description: 'Gateway attB2' },
            { name: 'loxP', pattern: 'ATAACTTCGTATAATGTATGCTATACGAAGTTAT', description: 'loxP site (Cre)' },
            { name: 'FRT', pattern: 'GAAGTTCCTATTC.{8}GAATAGGAACTTC', description: 'FRT site (Flp)', isRegex: true },
        ]
    },
    {
        name: 'Tags',
        motifs: [
            { name: 'His6', pattern: 'CATCA[TC]CA[TC]CA[TC]CA[TC]CA[TC]CA[TC]', description: '6x Histidine tag', isRegex: true },
            { name: 'FLAG', pattern: 'GACTACAAAGAC', description: 'FLAG tag (partial)' },
            { name: 'HA', pattern: 'TACCCATACGATGTTCC', description: 'HA epitope tag' },
            { name: 'Myc', pattern: 'GAACAAAAACTCATC', description: 'Myc tag coding sequence' },
            { name: 'TEV site', pattern: 'GAGAACCTGTACTTCCAG', description: 'TEV protease cleavage' },
        ]
    },
];

export function SearchPanel({
    sequenceData,
    onHighlight,
    onJumpToPosition,
    onOrfsFound
}: SearchPanelProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [searchBothStrands, setSearchBothStrands] = useState(true);
    const [caseSensitive, setCaseSensitive] = useState(false);
    const [useRegex, setUseRegex] = useState(false);
    const [selectedResult, setSelectedResult] = useState<number | null>(null);
    const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set(['Translation']));
    const [activeTab, setActiveTab] = useState<'search' | 'motifs' | 'orfs'>('search');

    // ORF finder state
    const [minOrfLength, setMinOrfLength] = useState(100);
    const [orfs, setOrfs] = useState<ORF[]>([]);
    const [selectedOrf, setSelectedOrf] = useState<number | null>(null);
    const sequenceType = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';

    const buildSearchHighlights = useCallback((results: SearchResult[], selectedIndex: number | null): HighlightedRegion[] => {
        return results.flatMap((result, index) => {
            const color = selectedIndex === index ? '#f59e0b' : (result.strand === 1 ? '#22c55e' : '#ef4444');
            const label = `${result.strand === 1 ? '→' : '←'} ${result.segments
                .map((segment) => `${segment.start + 1}-${segment.end}`)
                .join(' + ')}${result.segments.length > 1 ? ' (wrap)' : ''}`;
            return result.segments.map((segment) => ({
                start: segment.start,
                end: segment.end,
                color,
                label,
            }));
        });
    }, [sequenceData.sequence.length]);

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
                const sequenceLength = sequence.length;
                const regexSequence = sequenceData.circular
                    ? sequence + sequence.slice(0, Math.max(0, sequenceLength - 1))
                    : sequence;
                let match;
                while ((match = regex.exec(regexSequence)) !== null) {
                    if (match.index >= sequenceLength) break;
                    if (match[0].length > sequenceLength) {
                        regex.lastIndex = match.index + Math.max(1, match[0].length);
                        continue;
                    }
                    const rawEnd = match.index + match[0].length;
                    const segments = rawEnd <= sequenceLength
                        ? [{ start: match.index, end: rawEnd }]
                        : [
                            { start: match.index, end: sequenceLength },
                            { start: 0, end: rawEnd % sequenceLength },
                        ].filter((segment) => segment.end > segment.start);
                    results.push({
                        start: match.index,
                        end: rawEnd <= sequenceLength ? rawEnd : rawEnd % sequenceLength,
                        strand: 1,
                        sequence: segments
                            .map((segment) => sequenceData.sequence.slice(segment.start, segment.end))
                            .join(''),
                        segments,
                    });
                    // Prevent infinite loops for zero-length matches
                    if (match[0].length === 0) regex.lastIndex++;
                }
            } else {
                results.push(...findExactSequenceMatches(sequenceData.sequence, query, {
                    circular: sequenceData.circular,
                    bothStrands: searchBothStrands,
                    caseSensitive,
                    sequenceType,
                }));
            }
        } catch (e) {
            // Invalid regex - return empty results
            console.warn('Search error:', e);
        }

        // Sort by position
        return results.sort((a, b) => a.start - b.start);
    }, [searchQuery, caseSensitive, sequenceData.sequence, sequenceData.circular, useRegex, searchBothStrands, sequenceType]);

    useEffect(() => {
        if (activeTab !== 'search') return;
        if (!searchQuery.trim() || searchResults.length === 0) {
            onHighlight([]);
            return;
        }
        onHighlight(buildSearchHighlights(searchResults, selectedResult));
    }, [activeTab, searchQuery, searchResults, selectedResult, onHighlight, buildSearchHighlights]);

    useEffect(() => {
        if (selectedResult === null) return;
        if (selectedResult >= searchResults.length) {
            setSelectedResult(searchResults.length > 0 ? 0 : null);
        }
    }, [selectedResult, searchResults.length]);

    // Navigate to result
    const goToResult = useCallback((index: number) => {
        if (index < 0 || index >= searchResults.length) return;
        setSelectedResult(index);
        if (onJumpToPosition) {
            onJumpToPosition(searchResults[index].start);
        }
    }, [searchResults, onJumpToPosition]);

    // Quick motif search
    const searchMotif = useCallback((pattern: string, isRegex?: boolean) => {
        setSearchQuery(pattern);
        setUseRegex(isRegex || false);
        setActiveTab('search');
    }, []);

    // Clear search
    const clearSearch = useCallback(() => {
        setSearchQuery('');
        setSelectedResult(null);
        onHighlight([]);
    }, [onHighlight]);

    // Toggle category expansion
    const toggleCategory = (name: string) => {
        setExpandedCategories(prev => {
            const next = new Set(prev);
            if (next.has(name)) {
                next.delete(name);
            } else {
                next.add(name);
            }
            return next;
        });
    };

    // Find ORFs
    const runOrfFinder = useCallback(() => {
            const foundOrfs = findOpenReadingFrames(
                sequenceData.sequence,
                minOrfLength,
                sequenceData.circular,
            );
            setOrfs(foundOrfs);

        // Notify parent to display ORFs
            if (onOrfsFound) {
                const translations = foundOrfs.map(orf => ({
                    start: orf.start,
                    end: orf.end,
                    strand: orf.strand,
                    frame: orf.frame as 1 | 2 | 3,
                    length: orf.length,
                    segments: orf.segments,
                }));
                onOrfsFound(translations);
            }

        // Highlight ORFs
        const regions: HighlightedRegion[] = foundOrfs.flatMap((orf, i) => (
            orf.segments.map((segment) => ({
                start: segment.start,
                end: segment.end,
                color: selectedOrf === i ? '#f59e0b' : (orf.strand === 1 ? '#8b5cf6' : '#ec4899'),
                label: `ORF ${i + 1}: ${orf.length} bp`,
            }))
        ));
        onHighlight(regions);
    }, [sequenceData.circular, sequenceData.sequence, minOrfLength, onOrfsFound, selectedOrf, onHighlight]);

    // Highlight specific ORF
    const highlightOrf = useCallback((index: number) => {
        setSelectedOrf(index);
        const regions: HighlightedRegion[] = orfs.flatMap((orf, i) => (
            orf.segments.map((segment) => ({
                start: segment.start,
                end: segment.end,
                color: i === index ? '#f59e0b' : (orf.strand === 1 ? '#8b5cf6' : '#ec4899'),
                label: `ORF ${i + 1}: ${orf.length} bp`,
            }))
        ));
        onHighlight(regions);
        if (onJumpToPosition && orfs[index]) {
            onJumpToPosition(orfs[index].segments[0]?.start ?? orfs[index].start);
        }
    }, [orfs, onHighlight, onJumpToPosition]);

    return (
        <div className="search-panel p-3 space-y-3 text-sm">
            <h4 className="font-semibold text-slate-200">Find & Analyze</h4>

            {/* Tab switcher */}
            <div className="flex gap-1 text-xs">
                <button
                    onClick={() => setActiveTab('search')}
                    className={`px-3 py-1.5 rounded transition-colors ${activeTab === 'search'
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                        }`}
                >
                    Search
                </button>
                <button
                    onClick={() => setActiveTab('motifs')}
                    className={`px-3 py-1.5 rounded transition-colors ${activeTab === 'motifs'
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                        }`}
                >
                    Motifs
                </button>
                <button
                    onClick={() => setActiveTab('orfs')}
                    className={`px-3 py-1.5 rounded transition-colors ${activeTab === 'orfs'
                        ? 'bg-blue-600 text-white'
                        : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                        }`}
                >
                    ORF Finder
                </button>
            </div>

            {activeTab === 'search' && (
                <>
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
                                onClick={() => {
                                    if (searchResults.length > 0) {
                                        setSelectedResult((current) => current ?? 0);
                                    } else {
                                        onHighlight([]);
                                    }
                                }}
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
                </>
            )}

            {activeTab === 'motifs' && (
                <div className="space-y-2 max-h-80 overflow-y-auto">
                    {MOTIF_CATEGORIES.map(category => (
                        <div key={category.name} className="border border-slate-700 rounded">
                            <button
                                onClick={() => toggleCategory(category.name)}
                                className="w-full flex items-center justify-between px-2 py-1.5 bg-slate-800 hover:bg-slate-700 text-xs font-medium transition-colors"
                            >
                                <span>{category.name}</span>
                                <span className="text-slate-400">
                                    {expandedCategories.has(category.name) ? '▼' : '▶'}
                                </span>
                            </button>
                            {expandedCategories.has(category.name) && (
                                <div className="p-1 space-y-0.5">
                                    {category.motifs.map(motif => (
                                        <button
                                            key={motif.name}
                                            onClick={() => searchMotif(motif.pattern, motif.isRegex)}
                                            className="w-full text-left px-2 py-1 bg-slate-700/50 hover:bg-slate-600 rounded text-xs transition-colors"
                                            title={`${motif.description}\nPattern: ${motif.pattern}`}
                                        >
                                            <div className="flex justify-between items-center">
                                                <span className="text-slate-200">{motif.name}</span>
                                                {motif.isRegex && (
                                                    <span className="text-accent text-[10px]">regex</span>
                                                )}
                                            </div>
                                            <div className="text-[10px] text-slate-500 truncate">
                                                {motif.description}
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}

            {activeTab === 'orfs' && (
                <div className="space-y-3">
                    <div className="p-2 bg-slate-800 rounded border border-slate-700 space-y-2">
                        <div className="flex items-center gap-2">
                            <label className="text-xs text-slate-400">Min Length:</label>
                            <input
                                type="number"
                                value={minOrfLength}
                                onChange={(e) => setMinOrfLength(parseInt(e.target.value) || 30)}
                                min={30}
                                max={1000}
                                step={10}
                                className="w-20 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs text-center"
                            />
                            <span className="text-xs text-slate-500">bp</span>
                        </div>
                        <button
                            onClick={runOrfFinder}
                            className="w-full py-1.5 bg-accent hover:bg-accent rounded text-xs font-medium transition-colors"
                        >
                            Find Open Reading Frames
                        </button>
                    </div>

                    {orfs.length > 0 && (
                        <div className="space-y-1">
                            <div className="text-xs text-slate-400">
                                Found {orfs.length} ORF{orfs.length !== 1 ? 's' : ''}
                            </div>
                            <div className="max-h-48 overflow-y-auto space-y-1">
                                {orfs.map((orf, i) => (
                                    <div
                                        key={`${orf.start}-${orf.strand}`}
                                        onClick={() => highlightOrf(i)}
                                        className={`flex items-center justify-between px-2 py-1.5 rounded cursor-pointer text-xs transition-colors ${selectedOrf === i
                                            ? 'bg-amber-900/50 text-amber-300'
                                            : 'bg-slate-700/50 hover:bg-slate-700 text-slate-300'
                                            }`}
                                    >
                                        <div className="flex items-center gap-2">
                                            <span className={`w-4 text-center ${orf.strand === 1 ? 'text-accent' : 'text-accent-secondary'}`}>
                                                {orf.strand === 1 ? '→' : '←'}
                                            </span>
                                            <span>ORF {i + 1}</span>
                                        </div>
                                        <div className="flex items-center gap-2 text-slate-400">
                                            <span>{orf.length} bp</span>
                                            <span className="text-slate-500">|</span>
                                            <span>{orf.segments.map((segment) => `${segment.start + 1}..${segment.end}`).join(' + ')}</span>
                                            <span className="text-slate-500">F{orf.frame}</span>
                                        </div>
                                    </div>
                                ))}
                            </div>
                            <div className="text-[10px] text-slate-500 pt-1">
                                → = forward strand (purple) • ← = reverse strand (pink)
                            </div>
                        </div>
                    )}

                    {orfs.length === 0 && (
                        <div className="text-center text-slate-500 text-xs py-4">
                            Click "Find ORFs" to scan for open reading frames (ATG → Stop)
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
