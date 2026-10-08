/**
 * FrameworkBrowser - Browse and select antibody frameworks from SAbDab
 * 
 * Enhanced with:
 * - CDR-H3 length range slider (now works with local SQLite!)
 * - Debounced auto-search (500ms)
 * - Filter options from database
 * - Collapsible advanced filters
 * - Pagination support
 */

import { useState, useEffect, useMemo, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    searchSabdabFrameworks,
    downloadSabdabFramework,
    listCachedFrameworks,
    touchCachedFramework,
    getSabdabAttribution,
    getSabdabFilterOptions,
    getSabdabDatabaseStats,
    type SAbDabSearchResult,
    type CachedFramework,
    type SAbDabFilterOptions,
    type SAbDabDatabaseStats
} from '../lib/api';

// Built-in framework presets - curated VHH scaffolds for therapeutic development
const FRAMEWORK_PRESETS = [
    // Standard Fv (for comparison)
    {
        id: 'standard-fv',
        name: 'Standard Fv (hu-4D5-8)',
        description: 'Humanized 4D5-8 (Herceptin-like) heavy/light chain',
        type: 'fab',
        pdbCode: null,
        sequence: null,
        humanized: true
    },
    // Llama VHH frameworks
    {
        id: 'vhh-cablys3',
        name: 'Cablys-3 VHH',
        description: 'Anti-lysozyme VHH with classic camelid framework (PDB: 3DWT)',
        type: 'vhh',
        pdbCode: '3DWT',
        sequence: 'QVQLVESGGGLVQPGGSLRLSCAASGGSEYSYSTFSLGWFRQAPGQGLEAVAAIASMGGLTYYADSVKGRFTISRDNSKNTLYLQMNSLRAEDTAVYYCAAVRGYFMRLPSSHNFRYWGQGTLVTVS',
        humanized: false,
        recommended: true
    },
    // Human VH3-like
    {
        id: 'vhh28-vh3',
        name: 'VHH-28 (VH3-like)',
        description: 'Extensively homologous to human VH3 genes - low immunogenicity (PDB: 5U64)',
        type: 'vhh',
        pdbCode: '5U64',
        sequence: null,
        humanized: false,
        humanLike: true
    },
    // FDA-approved
    {
        id: 'ozoralizumab',
        name: 'Ozoralizumab Framework',
        description: 'FDA-approved humanized anti-TNFα nanobody (PDB: 8Z8M)',
        type: 'vhh',
        pdbCode: '8Z8M',
        sequence: null,
        humanized: true,
        approved: true
    }
];

export interface SelectedFramework {
    type: 'preset' | 'sabdab' | 'cached' | 'custom';
    id: string;
    name: string;
    pdbCode?: string;
    sequence?: string;
    filePath?: string;
    pdbContent?: string | null;
    cdrH3Length?: number;
    // Chain info for frameworks with antigens (from SAbDab)
    hChain?: string;      // Antibody heavy chain ID
    lChain?: string | null;      // Antibody light chain ID (null for VHH)
    antigenChain?: string;  // Antigen chain ID (set if user chose to include)
}

interface FrameworkBrowserProps {
    onSelect: (framework: SelectedFramework | null) => void;
    selectedFramework?: SelectedFramework | null;
    showCustomUpload?: boolean;
}

// Debounce hook
function useDebounce<T>(value: T, delay: number): T {
    const [debouncedValue, setDebouncedValue] = useState<T>(value);

    useEffect(() => {
        const timer = setTimeout(() => setDebouncedValue(value), delay);
        return () => clearTimeout(timer);
    }, [value, delay]);

    return debouncedValue;
}

export function FrameworkBrowser({
    onSelect,
    selectedFramework,
    showCustomUpload: _showCustomUpload = true
}: FrameworkBrowserProps) {
    const [activeTab, setActiveTab] = useState<'presets' | 'sabdab' | 'cached'>('presets');

    // Filter states
    const [species, setSpecies] = useState('');
    const [resolutionMax, setResolutionMax] = useState(2.5);
    const [cdrH3Min, setCdrH3Min] = useState<number | null>(null);
    const [cdrH3Max, setCdrH3Max] = useState<number | null>(null);
    const [selectedMethods, setSelectedMethods] = useState<string[]>([]);
    const [hasAntigen, setHasAntigen] = useState<boolean | null>(null);
    const [showAdvanced, setShowAdvanced] = useState(false);

    // Sort & pagination
    const [sortBy, setSortBy] = useState<'resolution' | 'cdr_h3_length' | 'pdb_code' | 'date'>('resolution');
    const [sortDesc, setSortDesc] = useState(false);
    const [cachedSortBy, setCachedSortBy] = useState<'last_used_at' | 'cached_at' | 'pdb_code'>('last_used_at');
    const [page, setPage] = useState(0);
    const pageSize = 50;

    const [downloadingPdb, setDownloadingPdb] = useState<string | null>(null);
    // Chain selector state for frameworks with antigens
    const [pendingDownload, setPendingDownload] = useState<{
        data: any;
        cdrH3Length?: number | null;
    } | null>(null);

    const queryClient = useQueryClient();

    // Fetch filter options from database
    const { data: filterOptionsData } = useQuery({
        queryKey: ['sabdab-filter-options'],
        queryFn: () => getSabdabFilterOptions(),
        staleTime: 1000 * 60 * 60, // 1 hour
    });
    const filterOptions: SAbDabFilterOptions | undefined = (filterOptionsData as any)?.data;

    // Fetch database stats
    const { data: statsData } = useQuery({
        queryKey: ['sabdab-stats'],
        queryFn: () => getSabdabDatabaseStats(),
        staleTime: 1000 * 60 * 5, // 5 min
    });
    const stats: SAbDabDatabaseStats | undefined = (statsData as any)?.data;

    // Build search params object for debouncing
    const searchParams = useMemo(() => ({
        species: species || undefined,
        resolution_max: resolutionMax || undefined,
        cdr_h3_min: cdrH3Min ?? undefined,
        cdr_h3_max: cdrH3Max ?? undefined,
        methods: selectedMethods.length > 0 ? selectedMethods.join(',') : undefined,
        has_antigen: hasAntigen ?? undefined,
        sort_by: sortBy,
        sort_desc: sortDesc,
        limit: pageSize,
        offset: page * pageSize,
    }), [species, resolutionMax, cdrH3Min, cdrH3Max, selectedMethods, hasAntigen, sortBy, sortDesc, page]);

    // Debounce search params
    const debouncedParams = useDebounce(searchParams, 500);

    // Search SAbDab with debounced params
    const { data: searchData, isLoading: searchLoading, error: searchError } = useQuery({
        queryKey: ['sabdab-search', debouncedParams],
        queryFn: () => searchSabdabFrameworks(debouncedParams),
        enabled: activeTab === 'sabdab',
    });
    const searchResponse = (searchData as any)?.data;
    const frameworks: SAbDabSearchResult[] = searchResponse?.results ?? [];
    const totalResults = searchResponse?.total ?? 0;

    // Reset page when filters change
    useEffect(() => {
        setPage(0);
    }, [species, resolutionMax, cdrH3Min, cdrH3Max, selectedMethods, hasAntigen, sortBy, sortDesc]);

    // List cached frameworks
    const { data: cachedData, isLoading: cachedLoading } = useQuery({
        queryKey: ['cached-frameworks'],
        queryFn: listCachedFrameworks,
        enabled: activeTab === 'cached',
    });
    const cached: CachedFramework[] = (cachedData as any)?.data?.frameworks ?? [];
    const sortedCached = useMemo(() => {
        const entries = [...cached];
        entries.sort((a, b) => {
            if (cachedSortBy === 'pdb_code') {
                return a.pdb_code.localeCompare(b.pdb_code);
            }

            const aTime = Date.parse(a[cachedSortBy] || '') || 0;
            const bTime = Date.parse(b[cachedSortBy] || '') || 0;
            return bTime - aTime;
        });
        return entries;
    }, [cached, cachedSortBy]);

    // Get attribution
    const { data: attributionData } = useQuery({
        queryKey: ['sabdab-attribution'],
        queryFn: getSabdabAttribution,
        staleTime: Infinity,
    });
    const attribution = (attributionData as any)?.data;

    // Download mutation
    const downloadMutation = useMutation({
        mutationFn: async ({ pdbCode, cdrH3Length }: { pdbCode: string; cdrH3Length?: number | null }) => {
            setDownloadingPdb(pdbCode);
            const response = await downloadSabdabFramework(pdbCode, {
                scheme: 'imgt',
                convert_hlt: true,
                include_content: true
            });
            return { ...response.data, cdrH3Length };
        },
        onSuccess: (data) => {
            queryClient.invalidateQueries({ queryKey: ['cached-frameworks'] });
            setDownloadingPdb(null);

            // If framework has antigen chain, show chain selector
            if (data.antigen_chain) {
                setPendingDownload({ data, cdrH3Length: data.cdrH3Length });
            } else {
                // No antigen - select directly
                onSelect({
                    type: 'sabdab',
                    id: data.pdb_code,
                    name: `SAbDab: ${data.pdb_code}`,
                    pdbCode: data.pdb_code,
                    filePath: data.file_path || undefined,
                    pdbContent: data.pdb_content ?? null,
                    cdrH3Length: data.cdrH3Length ?? undefined,
                    hChain: data.h_chain || undefined,
                    lChain: data.l_chain ?? null
                });
            }
        },
        onError: () => {
            setDownloadingPdb(null);
        }
    });

    const touchCachedMutation = useMutation({
        mutationFn: ({ pdbCode, scheme }: { pdbCode: string; scheme: string }) =>
            touchCachedFramework(pdbCode, scheme),
        onSettled: () => {
            queryClient.invalidateQueries({ queryKey: ['cached-frameworks'] });
        }
    });

    // Confirm chain selection for frameworks with antigens
    const handleConfirmChainSelection = useCallback((useFullComplex: boolean) => {
        if (!pendingDownload) return;
        const { data, cdrH3Length } = pendingDownload;

        onSelect({
            type: 'sabdab',
            id: data.pdb_code,
            name: `SAbDab: ${data.pdb_code}`,
            pdbCode: data.pdb_code,
            filePath: data.file_path || undefined,
            pdbContent: data.pdb_content ?? null,
            cdrH3Length: cdrH3Length ?? undefined,
            // Pass chain info for downstream processing
            hChain: data.h_chain || undefined,
            lChain: data.l_chain ?? null,
            antigenChain: useFullComplex ? data.antigen_chain : undefined
        });

        setPendingDownload(null);
    }, [pendingDownload, onSelect]);

    const handlePresetSelect = useCallback((preset: typeof FRAMEWORK_PRESETS[0]) => {
        onSelect({
            type: 'preset',
            id: preset.id,
            name: preset.name,
            pdbCode: preset.pdbCode || undefined,
            sequence: preset.sequence || undefined
        });
    }, [onSelect]);

    const handleCachedSelect = useCallback((framework: CachedFramework) => {
        touchCachedMutation.mutate({
            pdbCode: framework.pdb_code,
            scheme: framework.scheme
        });
        onSelect({
            type: 'cached',
            id: framework.pdb_code,
            name: `Cached: ${framework.pdb_code}`,
            pdbCode: framework.pdb_code,
            filePath: framework.file_path
        });
    }, [onSelect, touchCachedMutation]);

    const toggleMethod = useCallback((method: string) => {
        setSelectedMethods(prev =>
            prev.includes(method)
                ? prev.filter(m => m !== method)
                : [...prev, method]
        );
    }, []);

    const tabs = [
        { id: 'presets', label: 'Presets' },
        { id: 'sabdab', label: 'SAbDab' },
        { id: 'cached', label: 'Cached' },
    ] as const;

    // CDR-H3 length range from filter options
    const cdrRange = filterOptions?.cdr_h3_length_range ?? [5, 25];
    const formatCacheTimestamp = (value?: string | null) => {
        if (!value) return 'n/a';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return 'n/a';
        return parsed.toLocaleString([], {
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit'
        });
    };

    return (
        <div className="space-y-3">
            <label className="block text-sm font-medium text-slate-400">Framework Selection</label>

            {/* Selected indicator */}
            {selectedFramework && (
                <div className="flex items-center justify-between px-3 py-2 bg-accent/10 border border-accent/30 rounded-lg">
                    <div className="flex items-center gap-2 text-sm">
                        <span className="w-2 h-2 bg-accent rounded-full" />
                        <span className="text-accent truncate">
                            {selectedFramework.name}
                        </span>
                    </div>
                    <button
                        onClick={() => onSelect(null)}
                        className="text-xs text-red-400 hover:text-red-300"
                    >
                        Clear
                    </button>
                </div>
            )}

            {/* Tabs */}
            <div className="flex gap-1 border-b border-slate-700">
                {tabs.map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={`px-3 py-2 text-xs font-medium transition-colors ${activeTab === tab.id
                            ? 'text-accent border-b-2 border-accent -mb-px'
                            : 'text-slate-400 hover:text-slate-200'
                            }`}
                    >
                        {tab.label}
                        {tab.id === 'sabdab' && stats && (
                            <span className="ml-1 text-[10px] text-slate-500">
                                ({stats.total_entries.toLocaleString()})
                            </span>
                        )}
                    </button>
                ))}
            </div>

            {/* Tab Content */}
            <div className="bg-slate-800/30 rounded-lg p-3">
                {/* Presets Tab */}
                {activeTab === 'presets' && (
                    <div className="space-y-2">
                        {FRAMEWORK_PRESETS.map(preset => (
                            <button
                                key={preset.id}
                                onClick={() => handlePresetSelect(preset)}
                                className={`w-full text-left px-3 py-2.5 rounded-lg transition-colors ${selectedFramework?.id === preset.id
                                    ? 'bg-accent/20 border border-accent/50 text-accent'
                                    : 'bg-slate-900/50 hover:bg-slate-700/50 text-slate-300'
                                    }`}
                            >
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <span className="font-medium">{preset.name}</span>
                                        {'recommended' in preset && preset.recommended && (
                                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400">
                                                RECOMMENDED
                                            </span>
                                        )}
                                        {'approved' in preset && preset.approved && (
                                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400">
                                                FDA
                                            </span>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-1">
                                        {preset.humanized && (
                                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">
                                                Humanized
                                            </span>
                                        )}
                                        {'humanLike' in preset && preset.humanLike && (
                                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-teal-500/20 text-teal-400">
                                                VH3-like
                                            </span>
                                        )}
                                        <span className={`text-xs px-2 py-0.5 rounded ${preset.type === 'vhh' ? 'bg-amber-500/20 text-amber-400' : 'bg-blue-500/20 text-blue-400'
                                            }`}>
                                            {preset.type.toUpperCase()}
                                        </span>
                                    </div>
                                </div>
                                <p className="text-xs text-slate-500 mt-1">{preset.description}</p>
                            </button>
                        ))}
                    </div>
                )}

                {/* SAbDab Tab */}
                {activeTab === 'sabdab' && (
                    <div className="space-y-3">
                        {/* Basic filters */}
                        <div className="grid grid-cols-2 gap-2">
                            <div>
                                <label className="text-xs text-slate-500">Species</label>
                                <input
                                    type="text"
                                    value={species}
                                    onChange={e => setSpecies(e.target.value)}
                                    placeholder="e.g., camel, llama"
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-500">Max Resolution (Å)</label>
                                <input
                                    type="number"
                                    value={resolutionMax}
                                    onChange={e => setResolutionMax(parseFloat(e.target.value) || 2.5)}
                                    step={0.5}
                                    min={0.5}
                                    max={5}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                                />
                            </div>
                        </div>

                        {/* CDR-H3 Length Range */}
                        <div>
                            <label className="text-xs text-slate-500 flex items-center justify-between">
                                <span>CDR-H3 Length</span>
                                <span className="text-accent">
                                    {cdrH3Min ?? cdrRange[0]} - {cdrH3Max ?? cdrRange[1]}
                                </span>
                            </label>
                            <div className="flex items-center gap-2 mt-1">
                                <input
                                    type="range"
                                    min={cdrRange[0]}
                                    max={cdrRange[1]}
                                    value={cdrH3Min ?? cdrRange[0]}
                                    onChange={e => setCdrH3Min(parseInt(e.target.value))}
                                    className="flex-1 accent-accent"
                                />
                                <input
                                    type="range"
                                    min={cdrRange[0]}
                                    max={cdrRange[1]}
                                    value={cdrH3Max ?? cdrRange[1]}
                                    onChange={e => setCdrH3Max(parseInt(e.target.value))}
                                    className="flex-1 accent-accent"
                                />
                            </div>
                        </div>

                        {/* Advanced filters toggle */}
                        <button
                            onClick={() => setShowAdvanced(!showAdvanced)}
                            className="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1"
                        >
                            <span>{showAdvanced ? '▼' : '▶'}</span>
                            Advanced Filters
                        </button>

                        {/* Advanced filters */}
                        {showAdvanced && (
                            <div className="space-y-2 p-2 bg-slate-900/50 rounded-lg">
                                {/* Methods */}
                                {filterOptions?.methods && filterOptions.methods.length > 0 && (
                                    <div>
                                        <label className="text-xs text-slate-500">Method</label>
                                        <div className="flex flex-wrap gap-1 mt-1">
                                            {filterOptions.methods.slice(0, 4).map(method => (
                                                <button
                                                    key={method}
                                                    onClick={() => toggleMethod(method)}
                                                    className={`text-[10px] px-2 py-1 rounded ${selectedMethods.includes(method)
                                                        ? 'bg-accent/30 text-accent border border-accent/50'
                                                        : 'bg-slate-800 text-slate-400 border border-slate-700'
                                                        }`}
                                                >
                                                    {method.replace('X-RAY DIFFRACTION', 'X-ray').replace('ELECTRON MICROSCOPY', 'Cryo-EM')}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* Has antigen toggle */}
                                <div className="flex items-center gap-2">
                                    <label className="text-xs text-slate-500">Bound/Unbound:</label>
                                    <div className="flex gap-1">
                                        {[
                                            { value: null, label: 'All' },
                                            { value: true, label: 'Bound' },
                                            { value: false, label: 'Unbound' }
                                        ].map(opt => (
                                            <button
                                                key={String(opt.value)}
                                                onClick={() => setHasAntigen(opt.value)}
                                                className={`text-[10px] px-2 py-1 rounded ${hasAntigen === opt.value
                                                    ? 'bg-accent/30 text-accent'
                                                    : 'bg-slate-800 text-slate-400'
                                                    }`}
                                            >
                                                {opt.label}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Sort controls */}
                        <div className="flex items-center gap-2">
                            <label className="text-xs text-slate-500">Sort by:</label>
                            <select
                                value={sortBy}
                                onChange={e => setSortBy(e.target.value as any)}
                                className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                            >
                                <option value="resolution">Resolution (best first)</option>
                                <option value="cdr_h3_length">CDR-H3 Length</option>
                                <option value="pdb_code">PDB Code</option>
                                <option value="date">Date</option>
                            </select>
                            <button
                                onClick={() => setSortDesc(!sortDesc)}
                                className={`px-2 py-1.5 text-xs rounded border ${sortDesc
                                    ? 'bg-accent/20 border-accent/50 text-accent'
                                    : 'bg-slate-900 border-slate-700 text-slate-400'
                                    }`}
                                title={sortDesc ? 'Sort descending' : 'Sort ascending'}
                            >
                                {sortDesc ? '↓ DESC' : '↑ ASC'}
                            </button>
                        </div>

                        {/* Results count & loading */}
                        <div className="flex items-center justify-between text-xs text-slate-500">
                            <span>
                                {searchLoading ? 'Searching...' : `${totalResults.toLocaleString()} results`}
                                {stats && stats.entries_with_cdr_h3 > 0 && (
                                    <span className="ml-1 text-green-400">
                                        ({Math.round(stats.entries_with_cdr_h3 / stats.total_entries * 100)}% with CDR-H3)
                                    </span>
                                )}
                            </span>
                            {totalResults > pageSize && (
                                <div className="flex items-center gap-1">
                                    <button
                                        onClick={() => setPage(p => Math.max(0, p - 1))}
                                        disabled={page === 0}
                                        className="px-2 py-0.5 bg-slate-800 rounded disabled:opacity-50"
                                    >
                                        ←
                                    </button>
                                    <span>{page + 1} / {Math.ceil(totalResults / pageSize)}</span>
                                    <button
                                        onClick={() => setPage(p => p + 1)}
                                        disabled={(page + 1) * pageSize >= totalResults}
                                        className="px-2 py-0.5 bg-slate-800 rounded disabled:opacity-50"
                                    >
                                        →
                                    </button>
                                </div>
                            )}
                        </div>

                        {searchError && (
                            <div className="text-xs text-red-400 p-2 bg-red-500/10 rounded">
                                Search failed. Please try again.
                            </div>
                        )}

                        {/* Results */}
                        {frameworks.length > 0 && (
                            <div className="space-y-1 max-h-48 overflow-y-auto">
                                {frameworks.map((fw) => (
                                    <button
                                        key={`${fw.pdb_code}-${fw.h_chain}`}
                                        onClick={() => downloadMutation.mutate({ pdbCode: fw.pdb_code, cdrH3Length: fw.cdr_h3_length })}
                                        disabled={downloadingPdb !== null}
                                        className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${selectedFramework?.pdbCode === fw.pdb_code
                                            ? 'bg-accent/20 border border-accent/50'
                                            : 'bg-slate-900/50 hover:bg-slate-700/50'
                                            }`}
                                    >
                                        <div className="flex items-center justify-between">
                                            <span className="font-mono text-sm text-accent">
                                                {fw.pdb_code.toUpperCase()}
                                            </span>
                                            <div className="flex items-center gap-2 text-xs text-slate-500">
                                                {fw.resolution && <span>{fw.resolution.toFixed(1)}Å</span>}
                                                {fw.cdr_h3_length && (
                                                    <span className="text-amber-400">H3:{fw.cdr_h3_length}</span>
                                                )}
                                                {fw.has_antigen && (
                                                    <span className="text-green-400">•bound</span>
                                                )}
                                                {downloadingPdb === fw.pdb_code && (
                                                    <span className="text-accent">Downloading...</span>
                                                )}
                                            </div>
                                        </div>
                                        {fw.species && (
                                            <div className="text-xs text-slate-400 truncate">{fw.species}</div>
                                        )}
                                    </button>
                                ))}
                            </div>
                        )}

                        {/* Attribution */}
                        {attribution && (
                            <div className="text-xs text-slate-500 pt-2 border-t border-slate-700">
                                Data from <a href={attribution.website} target="_blank" rel="noopener noreferrer"
                                    className="text-blue-400 hover:underline">SAbDab</a> ({attribution.license})
                                {attribution.local_mirror && (
                                    <span className="ml-1 text-green-400">• Local DB</span>
                                )}
                            </div>
                        )}
                    </div>
                )}

                {/* Cached Tab */}
                {activeTab === 'cached' && (
                    <div className="space-y-2">
                        {cachedLoading ? (
                            <div className="text-sm text-slate-500">Loading cached frameworks...</div>
                        ) : cached.length === 0 ? (
                            <div className="text-sm text-slate-500">
                                No cached frameworks. Search SAbDab to download some.
                            </div>
                        ) : (
                            <div className="space-y-2">
                                <div className="flex items-center gap-2">
                                    <label className="text-xs text-slate-500">Sort cached:</label>
                                    <select
                                        value={cachedSortBy}
                                        onChange={e => setCachedSortBy(e.target.value as 'last_used_at' | 'cached_at' | 'pdb_code')}
                                        className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                                    >
                                        <option value="last_used_at">Recently Used</option>
                                        <option value="cached_at">Recently Cached</option>
                                        <option value="pdb_code">PDB Code</option>
                                    </select>
                                    <span className="text-xs text-slate-500">
                                        {cached.length} cached
                                    </span>
                                </div>
                                <div className="space-y-1 max-h-48 overflow-y-auto">
                                    {sortedCached.map(fw => (
                                    <button
                                        key={`${fw.pdb_code}-${fw.scheme}`}
                                        onClick={() => handleCachedSelect(fw)}
                                        className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${selectedFramework?.pdbCode === fw.pdb_code
                                            ? 'bg-accent/20 border border-accent/50'
                                            : 'bg-slate-900/50 hover:bg-slate-700/50'
                                            }`}
                                    >
                                        <div className="flex items-center justify-between">
                                            <span className="font-mono text-sm text-accent">
                                                {fw.pdb_code.toUpperCase()}
                                            </span>
                                            <span className="text-xs text-slate-500">
                                                {fw.scheme} • {(fw.size_bytes / 1024).toFixed(1)} KB
                                            </span>
                                        </div>
                                        <div className="mt-1 text-[11px] text-slate-500 flex items-center justify-between">
                                            <span>Used {formatCacheTimestamp(fw.last_used_at)}</span>
                                            <span>Cached {formatCacheTimestamp(fw.cached_at)}</span>
                                        </div>
                                    </button>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Chain Selector Dialog for frameworks with antigens */}
            {pendingDownload && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <div className="bg-slate-800 border border-slate-600 rounded-xl p-5 max-w-md shadow-2xl">
                        <h3 className="text-lg font-semibold text-white mb-2">
                            Framework Contains Antigen
                        </h3>
                        <p className="text-sm text-slate-400 mb-4">
                            <span className="font-mono text-accent">{pendingDownload.data.pdb_code}</span>
                            {' '}contains both antibody and antigen chains.
                        </p>

                        <div className="bg-slate-900/50 rounded-lg p-3 mb-4 space-y-1 text-sm">
                            <div className="flex justify-between">
                                <span className="text-slate-500">Antibody Chain:</span>
                                <span className="font-mono text-emerald-400">
                                    {pendingDownload.data.h_chain || '?'}
                                </span>
                            </div>
                            <div className="flex justify-between">
                                <span className="text-slate-500">Antigen Chain(s):</span>
                                <span className="font-mono text-amber-400">
                                    {pendingDownload.data.antigen_chain || '?'}
                                </span>
                            </div>
                            {pendingDownload.data.antigen_name && (
                                <div className="flex justify-between">
                                    <span className="text-slate-500">Antigen:</span>
                                    <span className="text-slate-300 truncate max-w-48">
                                        {pendingDownload.data.antigen_name}
                                    </span>
                                </div>
                            )}
                        </div>

                        <div className="flex gap-2">
                            <button
                                onClick={() => handleConfirmChainSelection(false)}
                                className="flex-1 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium"
                            >
                                Antibody Only
                            </button>
                            <button
                                onClick={() => handleConfirmChainSelection(true)}
                                className="flex-1 px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg text-sm font-medium"
                            >
                                Include Antigen
                            </button>
                        </div>
                        <button
                            onClick={() => setPendingDownload(null)}
                            className="w-full mt-2 px-4 py-1.5 text-slate-400 hover:text-slate-200 text-sm"
                        >
                            Cancel
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}

export default FrameworkBrowser;
