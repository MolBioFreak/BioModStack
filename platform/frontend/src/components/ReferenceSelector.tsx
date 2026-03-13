/**
 * ReferenceSelector - Select a reference PDB for structure comparison
 * 
 * Tabs: Your Runs | Presets | RCSB Fetch | Cached
 */

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchInputPresets, fetchJobs, fetchDesigns } from '../lib/api';

export interface ReferenceStructure {
    url: string;
    format: 'pdb' | 'cif';
    name: string;
    pdbId?: string;
    designId?: string;  // For comparing against other predictions
}

interface ReferenceSelectorProps {
    onSelect: (ref: ReferenceStructure | null) => void;
    selectedRef?: ReferenceStructure | null;
    currentDesignId?: string;  // Exclude current design from list
}

interface CachedPdb {
    pdb_id: string;
    url: string;
    path: string;
    size_bytes: number;
}

interface PdbPreset {
    id: string;
    name: string;
    path: string;
    description: string;
    category: string;
}

export function ReferenceSelector({ onSelect, selectedRef, currentDesignId }: ReferenceSelectorProps) {
    const [activeTab, setActiveTab] = useState<'runs' | 'presets' | 'rcsb' | 'cached'>('runs');
    const [pdbIdInput, setPdbIdInput] = useState('');
    const [fetchError, setFetchError] = useState<string | null>(null);
    const [selectedJobId, setSelectedJobId] = useState<string>('');
    const [searchQuery, setSearchQuery] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const queryClient = useQueryClient();

    // Debounce search input
    useEffect(() => {
        const timer = setTimeout(() => {
            if (searchQuery.length >= 3) {
                setDebouncedSearch(searchQuery);
            } else {
                setDebouncedSearch('');
            }
        }, 500);
        return () => clearTimeout(timer);
    }, [searchQuery]);

    // Search RCSB
    interface SearchResult {
        pdb_id: string;
        title: string;
        resolution?: number;
        organism?: string;
        method?: string;
    }
    const { data: searchData, isLoading: searchLoading } = useQuery({
        queryKey: ['rcsb-search', debouncedSearch],
        queryFn: async () => {
            const res = await fetch(`/api/rcsb/search?q=${encodeURIComponent(debouncedSearch)}&max_results=15`);
            if (!res.ok) throw new Error('Search failed');
            return res.json();
        },
        enabled: debouncedSearch.length >= 3,
    });
    const searchResults: SearchResult[] = searchData?.results ?? [];

    // Fetch jobs for "Your Runs" tab
    const { data: jobsData } = useQuery({
        queryKey: ['jobs'],
        queryFn: () => fetchJobs(),
    });
    const jobs = (jobsData as any)?.data?.jobs ?? [];
    const completedJobs = jobs.filter((j: any) => j.status === 'completed');

    // Fetch designs for selected job
    const { data: designsData, isLoading: designsLoading } = useQuery({
        queryKey: ['designs', selectedJobId],
        queryFn: () => fetchDesigns({ job_id: selectedJobId }),
        enabled: !!selectedJobId,
    });
    const designs = (designsData as any)?.data?.designs ?? [];
    // Filter out current design from the comparison list
    const filteredDesigns = designs.filter((d: any) => d.id !== currentDesignId);

    // Fetch preset PDBs
    const { data: presetsData } = useQuery({
        queryKey: ['presets', 'pdb'],
        queryFn: () => fetchInputPresets('pdb'),
    });
    const presets: PdbPreset[] = presetsData?.data ?? [];

    // Group presets by category
    const groupedPresets = presets.reduce((acc, p) => {
        const cat = p.category || 'Other';
        if (!acc[cat]) acc[cat] = [];
        acc[cat].push(p);
        return acc;
    }, {} as Record<string, PdbPreset[]>);

    // Fetch cached RCSB PDBs
    const { data: cachedData, isLoading: cachedLoading } = useQuery({
        queryKey: ['rcsb-cached'],
        queryFn: async () => {
            const res = await fetch('/api/rcsb');
            if (!res.ok) throw new Error('Failed to fetch cached PDBs');
            return res.json();
        },
    });
    const cachedPdbs: CachedPdb[] = cachedData?.cached ?? [];

    // Mutation to fetch from RCSB
    const fetchRcsbMutation = useMutation({
        mutationFn: async (pdbId: string) => {
            const res = await fetch(`/api/rcsb/${pdbId.toUpperCase()}`);
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Fetch failed');
            }
            return res.json();
        },
        onSuccess: (data) => {
            queryClient.invalidateQueries({ queryKey: ['rcsb-cached'] });
            setFetchError(null);
            // Auto-select the fetched structure
            onSelect({
                url: data.url,
                format: 'pdb',
                name: `RCSB: ${data.pdb_id}`,
                pdbId: data.pdb_id
            });
            setPdbIdInput('');
        },
        onError: (err: Error) => {
            setFetchError(err.message);
        }
    });

    const handlePresetSelect = (preset: PdbPreset) => {
        onSelect({
            url: `/api/files/pdb/${preset.path}`,
            format: 'pdb',
            name: preset.name,
        });
    };

    const handleCachedSelect = (cached: CachedPdb) => {
        onSelect({
            url: cached.url,
            format: 'pdb',
            name: `RCSB: ${cached.pdb_id}`,
            pdbId: cached.pdb_id
        });
    };

    const handleDesignSelect = (design: any) => {
        onSelect({
            url: `/api/designs/${design.id}/pdb`,
            format: 'pdb',
            name: design.name,
            designId: design.id
        });
    };

    const handleClearSelection = () => {
        onSelect(null);
    };

    const tabs = [
        { id: 'runs', label: 'Your Runs' },
        { id: 'presets', label: 'Presets' },
        { id: 'rcsb', label: 'RCSB Fetch' },
        { id: 'cached', label: `Cached (${cachedPdbs.length})` },
    ] as const;

    return (
        <div className="bg-slate-800/60 border border-slate-700/50 rounded-xl p-4 space-y-4">
            <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium text-slate-200">Compare to Reference</h4>
                {selectedRef && (
                    <button
                        onClick={handleClearSelection}
                        className="text-xs text-red-400 hover:text-red-300"
                    >
                        Clear
                    </button>
                )}
            </div>

            {/* Selected reference indicator */}
            {selectedRef && (
                <div className="flex items-center gap-2 px-3 py-2 bg-blue-500/10 border border-blue-500/30 rounded-lg text-sm">
                    <span className="w-2 h-2 bg-blue-400 rounded-full" />
                    <span className="text-blue-300 truncate">{selectedRef.name}</span>
                </div>
            )}

            {/* Tabs */}
            <div className="flex gap-1 border-b border-slate-700">
                {tabs.map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={`px-3 py-1.5 text-xs font-medium transition-colors ${activeTab === tab.id
                            ? 'text-blue-400 border-b-2 border-blue-400 -mb-px'
                            : 'text-slate-400 hover:text-slate-200'
                            }`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Tab Content */}
            <div className="max-h-64 overflow-y-auto">
                {/* Your Runs Tab - Compare to other predictions */}
                {activeTab === 'runs' && (
                    <div className="space-y-3">
                        {/* Job selector */}
                        <select
                            value={selectedJobId}
                            onChange={(e) => setSelectedJobId(e.target.value)}
                            className="w-full bg-slate-900/50 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white focus:ring-2 focus:ring-blue-500"
                        >
                            <option value="">Select a job...</option>
                            {completedJobs.map((job: any) => (
                                <option key={job.id} value={job.id}>
                                    {job.name} ({job.design_count ?? '?'} designs)
                                </option>
                            ))}
                        </select>

                        {/* Designs from selected job */}
                        {selectedJobId && (
                            <div className="space-y-1">
                                {designsLoading ? (
                                    <div className="text-center py-2 text-slate-500 text-sm">Loading designs...</div>
                                ) : filteredDesigns.length === 0 ? (
                                    <div className="text-center py-2 text-slate-500 text-sm">
                                        No other designs in this job
                                    </div>
                                ) : (
                                    filteredDesigns.map((design: any) => (
                                        <button
                                            key={design.id}
                                            onClick={() => handleDesignSelect(design)}
                                            className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors ${selectedRef?.designId === design.id
                                                ? 'bg-blue-500/20 text-blue-300'
                                                : 'bg-slate-900/50 text-slate-300 hover:bg-slate-700/50'
                                                }`}
                                        >
                                            <span className="truncate">{design.name}</span>
                                            {design.plddt_overall && (
                                                <span className="text-xs text-slate-500 ml-2">
                                                    pLDDT: {design.plddt_overall.toFixed(1)}
                                                </span>
                                            )}
                                        </button>
                                    ))
                                )}
                            </div>
                        )}

                        {!selectedJobId && (
                            <p className="text-xs text-slate-500">
                                Select a job to compare against other predictions from that run.
                            </p>
                        )}
                    </div>
                )}

                {/* Presets Tab */}
                {activeTab === 'presets' && (
                    <div className="space-y-3">
                        {Object.entries(groupedPresets).map(([category, items]) => (
                            <div key={category}>
                                <div className="text-xs text-slate-500 uppercase tracking-wider mb-1.5">{category}</div>
                                <div className="flex flex-wrap gap-1.5">
                                    {items.map(preset => (
                                        <button
                                            key={preset.id}
                                            onClick={() => handlePresetSelect(preset)}
                                            title={preset.description}
                                            className={`px-2 py-1 text-xs rounded-md transition-colors ${selectedRef?.name === preset.name
                                                ? 'bg-blue-500/20 text-blue-300 border border-blue-500/50'
                                                : 'bg-slate-700/50 text-slate-300 hover:bg-slate-600/50'
                                                }`}
                                        >
                                            {preset.name}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                {/* RCSB Fetch Tab */}
                {activeTab === 'rcsb' && (
                    <div className="space-y-4">
                        {/* Manual PDB ID */}
                        <div>
                            <div className="text-xs text-slate-500 mb-1.5">Direct PDB ID</div>
                            <div className="flex gap-2">
                                <input
                                    type="text"
                                    value={pdbIdInput}
                                    onChange={(e) => setPdbIdInput(e.target.value.toUpperCase())}
                                    placeholder="4I27"
                                    maxLength={4}
                                    className="w-24 bg-slate-900/50 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:ring-2 focus:ring-blue-500 font-mono"
                                />
                                <button
                                    onClick={() => fetchRcsbMutation.mutate(pdbIdInput)}
                                    disabled={pdbIdInput.length !== 4 || fetchRcsbMutation.isPending}
                                    className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    {fetchRcsbMutation.isPending ? '...' : 'Fetch'}
                                </button>
                            </div>
                            {fetchError && (
                                <div className="text-xs text-red-400 mt-1">{fetchError}</div>
                            )}
                        </div>

                        {/* Keyword Search */}
                        <div>
                            <div className="text-xs text-slate-500 mb-1.5">Search by keyword</div>
                            <input
                                type="text"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                placeholder="e.g., terminal deoxynucleotidyl transferase"
                                className="w-full bg-slate-900/50 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:ring-2 focus:ring-blue-500"
                            />

                            {/* Search Results */}
                            {searchLoading && (
                                <div className="text-xs text-slate-400 mt-2">Searching...</div>
                            )}
                            {searchResults.length > 0 && (
                                <div className="mt-2 space-y-1 max-h-40 overflow-y-auto">
                                    {searchResults.map(result => (
                                        <button
                                            key={result.pdb_id}
                                            onClick={() => fetchRcsbMutation.mutate(result.pdb_id)}
                                            disabled={fetchRcsbMutation.isPending}
                                            className="w-full text-left px-2 py-1.5 rounded-lg bg-slate-900/50 hover:bg-slate-700/50 transition-colors"
                                        >
                                            <div className="flex items-center gap-2">
                                                <span className="font-mono text-xs text-blue-400 font-medium">
                                                    {result.pdb_id}
                                                </span>
                                                {result.resolution && (
                                                    <span className="text-xs text-slate-500">
                                                        {result.resolution.toFixed(1)}Å
                                                    </span>
                                                )}
                                            </div>
                                            <div className="text-xs text-slate-300 truncate">
                                                {result.title}
                                            </div>
                                            {result.organism && (
                                                <div className="text-xs text-slate-500 italic truncate">
                                                    {result.organism}
                                                </div>
                                            )}
                                        </button>
                                    ))}
                                </div>
                            )}
                            {debouncedSearch && searchResults.length === 0 && !searchLoading && (
                                <div className="text-xs text-slate-500 mt-2">No results found</div>
                            )}
                        </div>
                    </div>
                )}

                {/* Cached Tab */}
                {activeTab === 'cached' && (
                    <div>
                        {cachedLoading ? (
                            <div className="text-center py-4 text-slate-500 text-sm">Loading...</div>
                        ) : cachedPdbs.length === 0 ? (
                            <div className="text-center py-4 text-slate-500 text-sm">
                                No cached PDBs. Fetch some from RCSB!
                            </div>
                        ) : (
                            <div className="space-y-1">
                                {cachedPdbs.map(pdb => (
                                    <button
                                        key={pdb.pdb_id}
                                        onClick={() => handleCachedSelect(pdb)}
                                        className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors ${selectedRef?.pdbId === pdb.pdb_id
                                            ? 'bg-blue-500/20 text-blue-300'
                                            : 'bg-slate-900/50 text-slate-300 hover:bg-slate-700/50'
                                            }`}
                                    >
                                        <span className="font-mono">{pdb.pdb_id}</span>
                                        <span className="text-xs text-slate-500">
                                            {(pdb.size_bytes / 1024).toFixed(0)} KB
                                        </span>
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

export default ReferenceSelector;
