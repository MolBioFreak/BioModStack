/**
 * TargetAntigenSelector - Select a target PDB for antibody design
 * 
 * Tabs: Upload | Your Runs | Presets | RCSB Fetch
 * Allows selecting PDBs from previous job results, presets, or RCSB
 */

import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchInputPresets, fetchDesigns } from '../lib/api';
import type { Job } from '../lib/api';
import { JobBrowser } from './JobBrowser';

interface SelectedTarget {
    type: 'upload' | 'run' | 'preset' | 'rcsb';
    file?: File;
    url?: string;
    path?: string;
    name: string;
    designId?: string;
    pdbId?: string;
}

interface TargetAntigenSelectorProps {
    onSelect: (target: SelectedTarget | null) => void;
    selectedTarget?: SelectedTarget | null;
}

interface PdbPreset {
    id: string;
    name: string;
    path: string;
    description: string;
    category: string;
}

export function TargetAntigenSelector({ onSelect, selectedTarget }: TargetAntigenSelectorProps) {
    const [activeTab, setActiveTab] = useState<'upload' | 'runs' | 'presets' | 'rcsb'>('upload');
    const [pdbIdInput, setPdbIdInput] = useState('');
    const [fetchError, setFetchError] = useState<string | null>(null);
    const [selectedJob, setSelectedJob] = useState<Job | null>(null);
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
    }
    const { data: searchData, isLoading: searchLoading } = useQuery({
        queryKey: ['rcsb-search', debouncedSearch],
        queryFn: async () => {
            const res = await fetch(`/api/rcsb/search?q=${encodeURIComponent(debouncedSearch)}&max_results=10`);
            if (!res.ok) throw new Error('Search failed');
            return res.json();
        },
        enabled: debouncedSearch.length >= 3,
    });
    const searchResults: SearchResult[] = searchData?.results ?? [];

    // Fetch designs for selected job
    const { data: designsData, isLoading: designsLoading } = useQuery({
        queryKey: ['designs', selectedJob?.id],
        queryFn: () => fetchDesigns({ job_id: selectedJob?.id }),
        enabled: !!selectedJob,
    });
    const designs = (designsData as any)?.data?.designs ?? (designsData as any)?.designs ?? [];

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
            onSelect({
                type: 'rcsb',
                url: data.url,
                path: data.path,
                name: `RCSB: ${data.pdb_id}`,
                pdbId: data.pdb_id
            });
            setPdbIdInput('');
        },
        onError: (err: Error) => {
            setFetchError(err.message);
        }
    });

    const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0] || null;
        if (file) {
            onSelect({
                type: 'upload',
                file,
                name: file.name
            });
        }
    };

    const handleDesignSelect = (design: any) => {
        onSelect({
            type: 'run',
            url: `/api/designs/${design.id}/pdb`,
            name: design.name,
            designId: design.id
        });
    };

    const handlePresetSelect = (preset: PdbPreset) => {
        onSelect({
            type: 'preset',
            path: preset.path,
            url: `/api/files/pdb/${preset.path}`,
            name: preset.name,
        });
    };

    const handleClearSelection = () => {
        onSelect(null);
    };

    const tabs = [
        { id: 'upload', label: 'Upload' },
        { id: 'runs', label: 'Your Runs' },
        { id: 'presets', label: 'Presets' },
        { id: 'rcsb', label: 'RCSB' },
    ] as const;

    return (
        <div className="space-y-3">
            <label className="block text-sm font-medium text-slate-400">Target Antigen PDB</label>

            {/* Selected target indicator */}
            {selectedTarget && (
                <div className="flex items-center justify-between px-3 py-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg">
                    <div className="flex items-center gap-2 text-sm">
                        <span className="w-2 h-2 bg-emerald-400 rounded-full" />
                        <span className="text-emerald-300 truncate">
                            {selectedTarget.type === 'upload' && 'Upload: '}
                            {selectedTarget.type === 'run' && 'Run: '}
                            {selectedTarget.type === 'preset' && 'Preset: '}
                            {selectedTarget.type === 'rcsb' && 'RCSB: '}
                            {selectedTarget.name}
                        </span>
                    </div>
                    <button
                        onClick={handleClearSelection}
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
                            ? 'text-blue-400 border-b-2 border-blue-400 -mb-px'
                            : 'text-slate-400 hover:text-slate-200'
                            }`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* Tab Content */}
            <div className="bg-slate-800/30 rounded-lg p-3">
                {/* Upload Tab */}
                {activeTab === 'upload' && (
                    <div className="space-y-2">
                        <input
                            type="file"
                            accept=".pdb,.cif"
                            onChange={handleFileUpload}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none file:mr-4 file:py-1 file:px-4 file:rounded-lg file:border-0 file:bg-blue-600 file:text-white file:cursor-pointer"
                        />
                        <p className="text-xs text-slate-500">Upload a PDB or CIF file from your computer</p>
                    </div>
                )}

                {/* Your Runs Tab */}
                {activeTab === 'runs' && (
                    <div className="space-y-3">
                        {!selectedJob ? (
                            <div className="h-96">
                                <JobBrowser
                                    onSelect={(job) => setSelectedJob(job)}
                                    className="h-full"
                                />
                            </div>
                        ) : (
                            <div className="space-y-3">
                                <button
                                    onClick={() => setSelectedJob(null)}
                                    className="text-sm text-blue-400 hover:text-blue-300 flex items-center gap-1"
                                >
                                    ← Back to Job Browser
                                </button>

                                <div className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white">
                                    Selected Job: <span className="font-semibold text-blue-300">{selectedJob.name}</span>
                                    <span className="ml-2 text-slate-400 text-xs">({selectedJob.design_count} designs)</span>
                                </div>

                                <div className="space-y-1 max-h-64 overflow-y-auto">
                                    {designsLoading ? (
                                        <div className="text-center py-2 text-slate-500 text-sm">Loading designs...</div>
                                    ) : designs.length === 0 ? (
                                        <div className="text-center py-2 text-slate-500 text-sm">
                                            No designs in this job
                                        </div>
                                    ) : (
                                        designs.map((design: any) => (
                                            <button
                                                key={design.id}
                                                onClick={() => handleDesignSelect(design)}
                                                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm transition-colors ${selectedTarget?.designId === design.id
                                                    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
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
                            </div>
                        )}
                    </div>
                )}

                {/* Presets Tab */}
                {activeTab === 'presets' && (
                    <div className="space-y-3 max-h-48 overflow-y-auto">
                        {Object.keys(groupedPresets).length === 0 ? (
                            <p className="text-xs text-slate-500">No presets configured.</p>
                        ) : (
                            Object.entries(groupedPresets).map(([category, items]) => (
                                <div key={category}>
                                    <div className="text-xs text-slate-500 uppercase tracking-wider mb-1.5">{category}</div>
                                    <div className="flex flex-wrap gap-1.5">
                                        {items.map(preset => (
                                            <button
                                                key={preset.id}
                                                onClick={() => handlePresetSelect(preset)}
                                                title={preset.description}
                                                className={`px-2 py-1 text-xs rounded-md transition-colors ${selectedTarget?.path === preset.path
                                                    ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/50'
                                                    : 'bg-slate-700/50 text-slate-300 hover:bg-slate-600/50'
                                                    }`}
                                            >
                                                {preset.name}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                )}

                {/* RCSB Tab */}
                {activeTab === 'rcsb' && (
                    <div className="space-y-3">
                        {/* Direct PDB ID */}
                        <div>
                            <div className="text-xs text-slate-500 mb-1">Direct PDB ID</div>
                            <div className="flex gap-2">
                                <input
                                    type="text"
                                    value={pdbIdInput}
                                    onChange={(e) => setPdbIdInput(e.target.value.toUpperCase())}
                                    placeholder="4I27"
                                    maxLength={4}
                                    className="w-24 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:ring-2 focus:ring-blue-500 font-mono"
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
                            <div className="text-xs text-slate-500 mb-1">Search by keyword</div>
                            <input
                                type="text"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                placeholder="e.g., CD20 antigen"
                                className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:ring-2 focus:ring-blue-500"
                            />

                            {searchLoading && (
                                <div className="text-xs text-slate-400 mt-2">Searching...</div>
                            )}
                            {searchResults.length > 0 && (
                                <div className="mt-2 space-y-1 max-h-32 overflow-y-auto">
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
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}

export default TargetAntigenSelector;
