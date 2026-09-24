/**
 * TargetAntigenSelector - Select a target PDB for antibody design
 *
 * Tabs: Upload | Your Runs | Presets | RCSB Fetch
 * Allows selecting PDBs from previous job results, presets, or RCSB
 */

import React, { useCallback, useState, useEffect, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchInputPresets, fetchDesigns, listCachedRcsbPdbs, type CachedRcsbEntry } from '../lib/api';
import type { Job, ProjectStructureQuery, StructureMaterialization } from '../lib/api';
import { createBC2SourceSession, preparePdbStructureSource } from '../lib/bindcraft2StructureInputs';
import { JobBrowser } from './JobBrowser';
import { StructuralSourceFiles, ProjectStructureSources } from './StructuralSourceFiles';
import { fetchDiagnosticSelectionContext } from '../lib/binderDiagnosticSelection';

/** Producer-bound metadata travels with bytes; never infer identity by basename. */
export interface SelectedSourceDocument {
    artifact_id?: string;
    target_state?: string;
    logical_path?: string;
    download_url?: string;
    sha256?: string;
    primary?: boolean;
    format?: string;
}

export interface SelectedTarget {
    type: 'upload' | 'run' | 'preset' | 'rcsb' | 'project';
    file?: File;
    url?: string;
    path?: string;
    name: string;
    designId?: string;
    pdbId?: string;
    jobId?: string;
    document?: SelectedSourceDocument;
    projectSource?: ProjectStructureQuery;
    materialization?: StructureMaterialization;
    modelNumber?: number;
}

interface TargetAntigenSelectorProps {
    onSelect: (target: SelectedTarget | null) => void;
    selectedTarget?: SelectedTarget | null;
    initialTab?: 'upload' | 'runs' | 'presets' | 'rcsb' | 'project';
    label?: string;
    requiredFormat?: 'native' | 'pdb';
    onInspect?: (source: SelectedTarget) => void;
}

interface PdbPreset {
    id: string;
    name: string;
    path: string;
    description: string;
    category: string;
}

export function TargetAntigenSelector({ onSelect, selectedTarget, initialTab, label = 'Target Antigen PDB', onInspect, requiredFormat = 'native' }: TargetAntigenSelectorProps) {
    const selectionEpoch = useRef(0);
    const alive = useRef(true);
    useEffect(() => { alive.current = true; return () => { alive.current = false; selectionEpoch.current++; }; }, []);
    useEffect(() => { selectionEpoch.current++; }, [selectedTarget]);
    const [sourceSession] = useState(createBC2SourceSession);
    const [preparationError, setPreparationError] = useState('');
    const [pendingConformation, setPendingConformation] = useState<{ source: SelectedTarget; models: number[] }>();
    const select = (source: SelectedTarget | null) => {
        const token = ++selectionEpoch.current; setPreparationError(''); setPendingConformation(undefined);
        if (!source || requiredFormat === 'native') { onSelect(source); return; }
        void (async () => {
            const native = await sourceSession.acquire(source);
            if (!alive.current || token !== selectionEpoch.current) return;
            if (native.document.format === 'cif' && native.document.models.length > 1 && source.modelNumber === undefined) {
                setPendingConformation({ source: { ...source, ...native.document.source, path: native.path }, models: native.document.models.map(model => model.number) });
                return;
            }
            const result = await preparePdbStructureSource(source, sourceSession);
            if (!alive.current || token !== selectionEpoch.current) return;
            onSelect({ ...source, ...result.document.source, path: result.path, file: undefined, url: `/api/files/download/${encodeURIComponent(result.path)}` });
        })().catch(error => {
            if (!alive.current || token !== selectionEpoch.current) return;
            setPreparationError(error instanceof Error ? error.message : String(error));
            onSelect(null); // Never retain an older source as an implicit fallback.
        });
    };
    const [documentDesign, setDocumentDesign] = useState<{ id: string; name: string; jobId: string } | null>(null);
    const documentsQuery = useQuery({
        queryKey: ['source-documents', documentDesign?.jobId],
        queryFn: () => fetchDiagnosticSelectionContext(documentDesign!.jobId),
        enabled: !!documentDesign,
    });
    const documents: SelectedSourceDocument[] = documentDesign ? documentsQuery.data?.candidate_documents[documentDesign.id] ?? [] : [];
    const [activeTab, setActiveTab] = useState<'upload' | 'runs' | 'presets' | 'rcsb' | 'project'>(initialTab ?? 'upload');
    const [pdbIdInput, setPdbIdInput] = useState('');
    const [fetchError, setFetchError] = useState<string | null>(null);
    const [selectedJob, setSelectedJob] = useState<Job | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [designsPage, setDesignsPage] = useState(0);
    const [sortBy, setSortBy] = useState<'plddt' | 'iptm' | 'ptm' | 'pae' | 'conf_score' | 'created_at'>('plddt');
    const [sortDesc, setSortDesc] = useState(true);
    const [reingestStatus, setReingestStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
    const [reingestMessage, setReingestMessage] = useState<string | null>(null);
    const [cachedRcsbSortBy, setCachedRcsbSortBy] = useState<'last_used_at' | 'cached_at' | 'pdb_id'>('last_used_at');
    const reingestAttempted = useRef<Set<string>>(new Set());
    const DESIGNS_PER_PAGE = 50;
    const queryClient = useQueryClient();

    // Reset page when job changes
    useEffect(() => {
        setDesignsPage(0);
        setDocumentDesign(null);
    }, [selectedJob]);

    useEffect(() => {
        if (initialTab) {
            setActiveTab(initialTab);
        }
    }, [initialTab]);

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

    const { data: cachedRcsbData, isLoading: cachedRcsbLoading } = useQuery({
        queryKey: ['rcsb-cached'],
        queryFn: listCachedRcsbPdbs,
        enabled: activeTab === 'rcsb',
    });
    const cachedRcsb: CachedRcsbEntry[] = useMemo(() => (cachedRcsbData as UntypedApiValue)?.data?.cached ?? [], [cachedRcsbData]);
    const sortedCachedRcsb = useMemo(() => {
        const entries = [...cachedRcsb];
        entries.sort((a, b) => {
            if (cachedRcsbSortBy === 'pdb_id') {
                return a.pdb_id.localeCompare(b.pdb_id);
            }

            const aTime = Date.parse(a[cachedRcsbSortBy] || '') || 0;
            const bTime = Date.parse(b[cachedRcsbSortBy] || '') || 0;
            if (bTime !== aTime) return bTime - aTime;

            const aCached = Date.parse(a.cached_at || '') || 0;
            const bCached = Date.parse(b.cached_at || '') || 0;
            if (bCached !== aCached) return bCached - aCached;

            return a.pdb_id.localeCompare(b.pdb_id);
        });
        return entries;
    }, [cachedRcsb, cachedRcsbSortBy]);

    // Fetch designs for selected job
    const { data: designsData, isLoading: designsLoading } = useQuery({
        queryKey: ['designs', selectedJob?.id, designsPage, sortBy, sortDesc],
        queryFn: () => fetchDesigns({
            job_id: selectedJob?.id,
            include_children: true,
            limit: DESIGNS_PER_PAGE,
            offset: designsPage * DESIGNS_PER_PAGE,
            sort_by: sortBy === 'created_at' ? undefined : sortBy,
            sort_desc: sortDesc
        }),
        enabled: !!selectedJob,
    });
    const designsResponse = (designsData as UntypedApiValue)?.data;
    const designs = designsResponse?.designs ?? (designsData as UntypedApiValue)?.designs ?? [];
    const totalDesigns = designsResponse?.total ?? 0;
    const totalPages = Math.ceil(totalDesigns / DESIGNS_PER_PAGE);

    const triggerReingest = useCallback(async (jobId: string) => {
        try {
            setReingestStatus('running');
            setReingestMessage('Re-ingesting designs…');
            const res = await fetch(`/api/jobs/${jobId}/reingest?include_children=true`, { method: 'POST' });
            const rawText = await res.text();
            let data: UntypedApiValue = null;
            try {
                data = rawText ? JSON.parse(rawText) : null;
            } catch {
                data = { message: rawText };
            }
            if (!res.ok) {
                throw new Error(data?.detail || data?.message || 'Re-ingest failed');
            }
            setReingestStatus('done');
            setReingestMessage(data?.message || 'Re-ingest complete');
            queryClient.invalidateQueries({ queryKey: ['designs'] });
        } catch (err: UntypedApiValue) {
            setReingestStatus('error');
            setReingestMessage(err?.message || 'Re-ingest failed');
        }
    }, [queryClient]);

    useEffect(() => {
        if (!selectedJob || designsLoading) return;
        if (designs.length === 0 && selectedJob.design_count > 0 && !reingestAttempted.current.has(selectedJob.id)) {
            reingestAttempted.current.add(selectedJob.id);
            triggerReingest(selectedJob.id);
        }
    }, [selectedJob, designsLoading, designs.length, triggerReingest]);

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
        mutationFn: async ({ pdbId, epoch }: { pdbId: string; epoch: number }) => {
            const res = await fetch(`/api/rcsb/${pdbId.toUpperCase()}`);
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'Fetch failed');
            }
            return { data: await res.json(), epoch };
        },
        onSuccess: ({ data, epoch }) => {
            queryClient.invalidateQueries({ queryKey: ['rcsb-cached'] });
            if (!alive.current || epoch !== selectionEpoch.current) return;
            setFetchError(null);
            select({
                type: 'rcsb',
                url: data.url,
                path: data.path,
                name: `RCSB: ${data.pdb_id}`,
                pdbId: data.pdb_id
            });
            setPdbIdInput('');
        },
        onError: (err: Error, variables) => {
            if (!alive.current || variables.epoch !== selectionEpoch.current) return;
            setFetchError(err.message);
        }
    });

    const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0] || null;
        if (file) {
            select({
                type: 'upload',
                file,
                name: file.name
            });
        }
    };

    const handleDesignSelect = (design: UntypedApiValue) => {
        select({
            type: 'run',
            url: `/api/designs/${design.id}/pdb`,
            name: design.name,
            designId: design.id,
            jobId: design.job_id || selectedJob?.id,
        });
    };

    const handlePresetSelect = (preset: PdbPreset) => {
        select({
            type: 'preset',
            path: preset.path,
            url: `/api/files/pdb/${preset.path}`,
            name: preset.name,
        });
    };

    const handleClearSelection = () => {
        select(null);
        setDocumentDesign(null);
    };

    const formatCacheTimestamp = (value?: string | null) => {
        if (!value) return 'unknown';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return 'unknown';
        return parsed.toLocaleString([], {
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit'
        });
    };

    const tabs = [
        { id: 'upload', label: 'Upload' },
        { id: 'runs', label: 'Your Runs' },
        { id: 'project', label: 'Project resources' },
        { id: 'presets', label: 'Presets' },
        { id: 'rcsb', label: 'RCSB' },
    ] as const;

    return (
        <div className="space-y-3">
            <label className="block text-sm font-medium text-slate-400">{label}</label>

            {preparationError && <p role="alert">{preparationError}</p>}
            {pendingConformation && <label className="block text-sm">Select the exact CIF conformation for PDB consumption
                <select aria-label="Source conformation" value="" onChange={event => select({ ...pendingConformation.source, modelNumber: Number(event.target.value) })}>
                    <option value="" disabled>Choose source model</option>
                    {pendingConformation.models.map(number => <option key={number} value={number}>Model {number}</option>)}
                </select>
                <button type="button" onClick={() => select(null)}>Clear pending source</button>
            </label>}
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
                            {selectedTarget.document && <span className="block text-xs">{selectedTarget.document.target_state ?? 'State not recorded'} · {selectedTarget.document.logical_path ?? selectedTarget.document.artifact_id}</span>}
                        </span>
                    </div>
                    {onInspect && <button type="button" className="text-xs text-accent" onClick={() => onInspect(selectedTarget)}>Inspect source</button>}
                    <button type="button"
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
                    <button type="button"
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
                            accept=".pdb,.cif,.mmcif"
                            aria-label={`${label} upload`}
                            onChange={handleFileUpload}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none file:mr-4 file:py-1 file:px-4 file:rounded-lg file:border-0 file:bg-blue-600 file:text-white file:cursor-pointer"
                        />
                        <p className="text-xs text-slate-500">Upload a PDB or CIF file from your computer</p>
                        <StructuralSourceFiles onSelect={source => select({ type: 'upload', ...source, url: `/api/files/download/${encodeURIComponent(source.path)}` })} />
                    </div>
                )}

                {activeTab === 'project' && <ProjectStructureSources onSelect={select} />}
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
                                <button type="button"
                                    onClick={() => setSelectedJob(null)}
                                    className="text-sm text-blue-400 hover:text-blue-300 flex items-center gap-1"
                                >
                                    ← Back to Job Browser
                                </button>

                                <div className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white">
                                    <span className="ml-2 text-slate-400 text-xs">({selectedJob.design_count} designs)</span>
                                </div>
                                {reingestMessage && (
                                    <div className={`text-xs ${reingestStatus === 'error' ? 'text-red-400' : 'text-slate-400'}`}>
                                        {reingestMessage}
                                    </div>
                                )}

                                {/* Sort Controls */}
                                <div className="flex gap-2 text-xs">
                                    <select
                                        value={sortBy}
                                        onChange={(e) => setSortBy(e.target.value as UntypedApiValue)}
                                        className="bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-300 outline-none focus:border-blue-500"
                                    >
                                        <option value="plddt">Sort by pLDDT</option>
                                        <option value="iptm">Sort by iPTM</option>
                                        <option value="pae">Sort by pAE</option>
                                        <option value="ptm">Sort by pTM</option>
                                        <option value="conf_score">Sort by Confidence</option>
                                        <option value="created_at">Sort by Date</option>
                                    </select>
                                    <button type="button"
                                        onClick={() => setSortDesc(!sortDesc)}
                                        className="px-2 py-1 bg-slate-900 border border-slate-600 rounded text-slate-300 hover:bg-slate-800"
                                    >
                                        {sortDesc ? 'Desc' : 'Asc'}
                                    </button>
                                </div>

                                <div className="space-y-1 max-h-64 overflow-y-auto">
                                    {designsLoading ? (
                                        <div className="text-center py-2 text-slate-500 text-sm">Loading designs...</div>
                                    ) : designs.length === 0 ? (
                                        <div className="text-center py-2 text-slate-500 text-sm space-y-2">
                                            <div>No designs in this job</div>
                                            <button type="button"
                                                onClick={() => selectedJob && triggerReingest(selectedJob.id)}
                                                className="px-3 py-1.5 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded"
                                                disabled={reingestStatus === 'running'}
                                            >
                                                {reingestStatus === 'running' ? 'Re-ingesting…' : 'Re-ingest Designs'}
                                            </button>
                                        </div>
                                    ) : (
                                        designs.map((design: UntypedApiValue) => (
                                            <div key={design.id}>
                                            <button type="button"
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
                                            <button type="button" className="px-3 py-1 text-xs text-blue-400 hover:text-blue-300" onClick={() => setDocumentDesign({ id: design.id, name: design.name, jobId: design.job_id || selectedJob.id })}>Documents / states for {design.name}</button>
                                            </div>
                                        ))
                                    )}
                                </div>

                                {documentDesign && <div className="space-y-2 rounded-lg border border-[var(--border-color)] p-3" aria-label="Saved structure documents">
                                    <div className="flex justify-between gap-2"><span className="text-sm">{documentDesign.name} · exact documents / states</span><button type="button" onClick={() => setDocumentDesign(null)}>Close documents</button></div>
                                    {documentsQuery.isFetching && <p role="status">Loading documents…</p>}
                                    {documentsQuery.error && <p role="alert">{documentsQuery.error.message}</p>}
                                    {!documentsQuery.isFetching && !documentsQuery.error && !documents.length && <p className="text-xs">No alternate documents are recorded. The Design primary source remains available above.</p>}
                                    {documents.map((document, index) => <div key={document.artifact_id ?? index} className="text-xs">
                                        {document.download_url ? <button type="button" className="w-full rounded border border-[var(--border-color)] p-2 text-left hover:text-accent" onClick={() => select({ type: 'run', name: `${documentDesign.name} · ${document.target_state ?? 'document'}`, designId: documentDesign.id, jobId: documentDesign.jobId, url: document.download_url, document: { ...document } })}>{document.target_state ?? 'State not recorded'} · {document.logical_path ?? document.artifact_id} {document.primary ? '· primary' : ''}</button> : <span>{document.target_state ?? 'State not recorded'} · {document.logical_path ?? document.artifact_id} · no download supplied</span>}
                                    </div>)}
                                </div>}

                                {/* Pagination Controls */}
                                {selectedJob && totalPages > 1 && (
                                    <div className="flex items-center justify-between pt-2 border-t border-slate-700/50">
                                        <button type="button"
                                            onClick={() => setDesignsPage(p => Math.max(0, p - 1))}
                                            disabled={designsPage === 0}
                                            className="px-2 py-1 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded disabled:opacity-50 disabled:cursor-not-allowed"
                                        >
                                            Previous
                                        </button>
                                        <span className="text-xs text-slate-500">
                                            Page {designsPage + 1} of {totalPages}
                                        </span>
                                        <button type="button"
                                            onClick={() => setDesignsPage(p => Math.min(totalPages - 1, p + 1))}
                                            disabled={designsPage >= totalPages - 1}
                                            className="px-2 py-1 text-xs bg-slate-800 hover:bg-slate-700 text-slate-300 rounded disabled:opacity-50 disabled:cursor-not-allowed"
                                        >
                                            Next
                                        </button>
                                    </div>
                                )}
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
                                            <button type="button"
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
                                <button type="button"
                                    onClick={() => fetchRcsbMutation.mutate({ pdbId: pdbIdInput, epoch: ++selectionEpoch.current })}
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
                                        <button type="button"
                                            key={result.pdb_id}
                                            onClick={() => fetchRcsbMutation.mutate({ pdbId: result.pdb_id, epoch: ++selectionEpoch.current })}
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

                        <div className="pt-2 border-t border-slate-700/50 space-y-2">
                            <div className="flex items-center gap-2">
                                <div className="text-xs text-slate-500">Cached PDBs</div>
                                <select
                                    value={cachedRcsbSortBy}
                                    onChange={(e) => setCachedRcsbSortBy(e.target.value as 'last_used_at' | 'cached_at' | 'pdb_id')}
                                    className="flex-1 bg-slate-900 border border-slate-600 rounded-lg px-2 py-1.5 text-sm text-white"
                                >
                                    <option value="last_used_at">Recently Used</option>
                                    <option value="cached_at">Recently Cached</option>
                                    <option value="pdb_id">PDB ID</option>
                                </select>
                                <span className="text-xs text-slate-500">
                                    {cachedRcsb.length} cached
                                </span>
                            </div>

                            {cachedRcsbLoading ? (
                                <div className="text-xs text-slate-400">Loading cached PDBs...</div>
                            ) : sortedCachedRcsb.length === 0 ? (
                                <div className="text-xs text-slate-500">
                                    No cached RCSB PDBs yet.
                                </div>
                            ) : (
                                <div className="space-y-1 max-h-40 overflow-y-auto">
                                    {sortedCachedRcsb.map((entry) => (
                                        <button type="button"
                                            key={entry.pdb_id}
                                            onClick={() => fetchRcsbMutation.mutate({ pdbId: entry.pdb_id, epoch: ++selectionEpoch.current })}
                                            disabled={fetchRcsbMutation.isPending}
                                            className="w-full text-left px-2 py-1.5 rounded-lg bg-slate-900/50 hover:bg-slate-700/50 transition-colors"
                                        >
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="font-mono text-xs text-blue-400 font-medium">
                                                    {entry.pdb_id}
                                                </span>
                                                <span className="text-xs text-slate-500">
                                                    {(entry.size_bytes / 1024).toFixed(1)} KB
                                                </span>
                                            </div>
                                            <div className="mt-1 flex items-center justify-between text-[11px] text-slate-500">
                                                <span>Used {formatCacheTimestamp(entry.last_used_at)}</span>
                                                <span>Cached {formatCacheTimestamp(entry.cached_at)}</span>
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div >
    );
}

export default TargetAntigenSelector;
