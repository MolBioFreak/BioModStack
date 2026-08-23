import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import {
    cmApiError,
    searchCmRcsb,
    type CmRcsbEntry,
} from './conformationalMapping/conformationalMappingApi';

export interface Rfd3SelectedSource {
    type: 'upload' | 'run' | 'rcsb' | 'cached' | 'preset';
    file?: File;
    url?: string;
    path?: string;
    name: string;
    designId?: string;
    pdbId?: string;
    sourceId?: string;
    modelNumber?: number;
    designChainId?: string;
}

interface ReusableStructure {
    design_id: string;
    design_name: string;
    job_id: string;
    job_name: string;
    model_id: string;
    completed_at: string | null;
    structure_url: string;
}

interface CachedRcsbStructure {
    pdb_id: string;
    url: string;
    size_bytes: number;
    cached_at: string;
    last_used_at: string | null;
}

interface Rfd3SourceSelectorProps {
    selectedSource: Rfd3SelectedSource | null;
    onSelect: (source: Rfd3SelectedSource | null) => void;
}

type SourceTab = 'upload' | 'runs' | 'rcsb' | 'cached';

const tabs: Array<{ value: SourceTab; label: string }> = [
    { value: 'upload', label: 'Upload' },
    { value: 'runs', label: 'Your Runs' },
    { value: 'rcsb', label: 'RCSB' },
    { value: 'cached', label: 'Cached' },
];

const inputClass = 'w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)] outline-none';
const panelClass = 'rounded-xl border border-[var(--border-primary)] bg-[color-mix(in_srgb,var(--bg-tertiary)_42%,transparent)] p-3';

export function Rfd3SourceSelector({ selectedSource, onSelect }: Rfd3SourceSelectorProps) {
    const [activeTab, setActiveTab] = useState<SourceTab>('upload');
    const [rcsbQuery, setRcsbQuery] = useState('');
    const [rcsbResults, setRcsbResults] = useState<CmRcsbEntry[]>([]);
    const [selectedEntry, setSelectedEntry] = useState<CmRcsbEntry | null>(null);
    const [modelId, setModelId] = useState('');
    const [sampleId, setSampleId] = useState('');
    const [chainId, setChainId] = useState('');
    const [entityId, setEntityId] = useState('');
    const [pastedStructure, setPastedStructure] = useState('');
    const [error, setError] = useState<string | null>(null);

    const reusableStructures = useQuery({
        queryKey: ['rfd3-reusable-structures'],
        queryFn: async (): Promise<ReusableStructure[]> => {
            const response = await fetch('/api/designs/reusable-structures?limit=24');
            if (!response.ok) throw new Error(`Reusable structures failed (${response.status})`);
            const payload = await response.json() as { structures?: ReusableStructure[] };
            return payload.structures ?? [];
        },
        enabled: activeTab === 'runs',
        retry: false,
    });

    const cachedSources = useQuery({
        queryKey: ['rfd3-cached-structure-sources'],
        queryFn: async (): Promise<CachedRcsbStructure[]> => {
            const response = await fetch('/api/rcsb');
            if (!response.ok) throw new Error(`Cached RCSB structures failed (${response.status})`);
            const payload = await response.json() as { cached?: CachedRcsbStructure[] };
            return payload.cached ?? [];
        },
        enabled: activeTab === 'cached',
        retry: false,
    });

    const searchMutation = useMutation({
        mutationFn: () => searchCmRcsb(rcsbQuery, 'full_structure_context'),
        onSuccess: (response) => {
            setRcsbResults(response.results);
            setSelectedEntry(null);
            setError(null);
        },
        onError: (value) => setError(cmApiError(value, 'RCSB search failed.')),
    });

    const fetchRcsbMutation = useMutation({
        mutationFn: async () => {
            if (!selectedEntry) throw new Error('Select an RCSB entry.');
            const response = await fetch(`/api/rcsb/${selectedEntry.accession}`);
            if (!response.ok) {
                const payload = await response.json().catch(() => null) as { detail?: string } | null;
                throw new Error(payload?.detail || `RCSB structure fetch failed (${response.status})`);
            }
            return response.json() as Promise<{ pdb_id: string; url: string }>;
        },
        onSuccess: (source) => {
            onSelect({
                type: 'rcsb',
                url: source.url,
                name: `RCSB_${source.pdb_id}.pdb`,
                pdbId: source.pdb_id,
                modelNumber: Number.parseInt(modelId, 10),
                designChainId: chainId,
            });
            setError(null);
        },
        onError: (value) => setError(cmApiError(value, 'Full RCSB structure loading failed.')),
    });

    const chooseEntry = (entry: CmRcsbEntry) => {
        const firstChain = entry.chains[0];
        setSelectedEntry(entry);
        setModelId(entry.models[0]?.model_id ?? '');
        setSampleId(entry.samples[0]?.sample_id ?? '');
        setChainId(firstChain?.chain_id ?? '');
        setEntityId(firstChain?.entity_id ?? '');
        setError(null);
    };

    const selectedChain = selectedEntry?.chains.find((chain) => chain.chain_id === chainId);
    const rcsbReady = Boolean(
        selectedEntry
        && modelId
        && sampleId
        && selectedChain
        && entityId
        && selectedChain.entity_id === entityId,
    );

    return (
        <div className="space-y-4" data-bms-rfd3-source-selector="bounded">
            <div className="flex flex-wrap gap-2" role="tablist" aria-label="RFD3 structure sources">
                {tabs.map((tab) => (
                    <button
                        key={tab.value}
                        type="button"
                        role="tab"
                        aria-selected={activeTab === tab.value}
                        onClick={() => { setActiveTab(tab.value); setError(null); }}
                        className={`rounded-lg border px-3 py-2 text-xs font-medium ${activeTab === tab.value ? 'border-[var(--accent-primary)] text-[var(--text-primary)]' : 'border-[var(--border-primary)] text-[var(--text-secondary)]'}`}
                    >
                        {tab.label}
                    </button>
                ))}
            </div>

            {activeTab === 'upload' && (
                <div className={`${panelClass} space-y-4`}>
                    <label className="block text-xs text-[var(--text-secondary)]">
                        PDB or mmCIF structure
                        <input
                            type="file"
                            accept=".pdb,.cif,.mmcif,chemical/x-pdb,chemical/x-cif"
                            className={`${inputClass} mt-2`}
                            onChange={(event) => {
                                const file = event.target.files?.[0] ?? null;
                                onSelect(file ? { type: 'upload', file, name: file.name } : null);
                            }}
                        />
                    </label>
                    <label className="block text-xs text-[var(--text-secondary)]">
                        Paste PDB or mmCIF text
                        <textarea
                            value={pastedStructure}
                            onChange={(event) => setPastedStructure(event.target.value)}
                            className={`${inputClass} mt-2 min-h-32 font-mono text-[11px]`}
                            placeholder="ATOM ... or data_entry"
                        />
                    </label>
                    <button
                        type="button"
                        disabled={!pastedStructure.trim()}
                        onClick={() => {
                            const text = pastedStructure.trim();
                            const isMmcif = text.startsWith('data_') || text.includes('_atom_site.');
                            const extension = isMmcif ? 'cif' : 'pdb';
                            const file = new File(
                                [`${text}\n`],
                                `pasted-structure.${extension}`,
                                { type: isMmcif ? 'chemical/x-cif' : 'chemical/x-pdb' },
                            );
                            onSelect({ type: 'upload', file, name: file.name });
                            setError(null);
                        }}
                        className="rounded-lg border border-[var(--accent-primary)] px-3 py-2 text-sm disabled:opacity-40"
                    >
                        Use pasted structure in Mol*
                    </button>
                </div>
            )}

            {activeTab === 'runs' && (
                <div className="space-y-2">
                    {reusableStructures.isLoading && <div className={panelClass}>Loading reusable structures…</div>}
                    {reusableStructures.isError && <div role="alert" className={panelClass}>{cmApiError(reusableStructures.error, 'Unable to load reusable structures.')}</div>}
                    {!reusableStructures.isLoading && !reusableStructures.isError && !reusableStructures.data?.length && (
                        <div className={panelClass}>No completed PDB-backed designs are available.</div>
                    )}
                    {reusableStructures.data?.map((structure) => (
                        <button
                            key={structure.design_id}
                            type="button"
                            aria-pressed={selectedSource?.designId === structure.design_id}
                            onClick={() => onSelect({
                                type: 'run',
                                url: structure.structure_url,
                                name: structure.design_name,
                                designId: structure.design_id,
                            })}
                            className={`${panelClass} w-full text-left`}
                        >
                            <span className="block text-sm font-medium text-[var(--text-primary)]">{structure.design_name}</span>
                            <span className="mt-1 block text-xs text-[var(--text-secondary)]">{structure.job_name} · {structure.model_id} · {structure.completed_at || 'completed'}</span>
                        </button>
                    ))}
                </div>
            )}

            {activeTab === 'rcsb' && (
                <div className={`${panelClass} space-y-4`}>
                    <div>
                        <div className="text-sm font-medium">RCSB exact structure context</div>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">Resolve the exact model and design-chain context, then load the full deposited complex into Mol*.</p>
                    </div>
                    <div className="flex gap-2">
                        <input
                            aria-label="RCSB accession or keyword"
                            value={rcsbQuery}
                            onChange={(event) => setRcsbQuery(event.target.value)}
                            onKeyDown={(event) => { if (event.key === 'Enter' && rcsbQuery.trim().length >= 2) searchMutation.mutate(); }}
                            className={inputClass}
                            placeholder="2GLV or terminal deoxynucleotidyl transferase"
                        />
                        <button type="button" disabled={rcsbQuery.trim().length < 2 || searchMutation.isPending} onClick={() => searchMutation.mutate()} className="rounded-lg border border-violet-400/50 px-3 py-2 text-sm disabled:opacity-40">
                            {searchMutation.isPending ? 'Searching…' : 'Search'}
                        </button>
                    </div>
                    <div className="space-y-2">
                        {rcsbResults.map((entry) => (
                            <button key={entry.accession} type="button" onClick={() => chooseEntry(entry)} className={`${panelClass} w-full text-left`}>
                                <span className="block text-sm font-medium">{entry.accession} · {entry.title}</span>
                                <span className="mt-1 block text-xs text-[var(--text-secondary)]">{entry.method || 'method unavailable'} · {entry.resolution ?? '—'} Å · {entry.organism || 'organism unavailable'}</span>
                            </button>
                        ))}
                    </div>
                    {selectedEntry && (
                        <div className="grid gap-3 md:grid-cols-2">
                            <label className="text-xs text-[var(--text-secondary)]">Model<select aria-label="RCSB model" value={modelId} onChange={(event) => setModelId(event.target.value)} className={`${inputClass} mt-1`}><option value="">Select model…</option>{selectedEntry.models.map((item) => <option key={item.model_id} value={item.model_id}>{item.label}</option>)}</select></label>
                            <label className="text-xs text-[var(--text-secondary)]">Sample<select aria-label="RCSB sample" value={sampleId} onChange={(event) => setSampleId(event.target.value)} className={`${inputClass} mt-1`}><option value="">Select sample…</option>{selectedEntry.samples.map((item) => <option key={item.sample_id} value={item.sample_id}>{item.label}</option>)}</select></label>
                            <label className="text-xs text-[var(--text-secondary)]">Chain<select aria-label="RCSB chain" value={chainId} onChange={(event) => { const next = event.target.value; setChainId(next); setEntityId(selectedEntry.chains.find((item) => item.chain_id === next)?.entity_id ?? ''); }} className={`${inputClass} mt-1`}><option value="">Select chain…</option>{selectedEntry.chains.map((item) => <option key={item.chain_id} value={item.chain_id}>{item.label} · {item.entity_type}</option>)}</select></label>
                            <label className="text-xs text-[var(--text-secondary)]">Entity<select aria-label="RCSB entity" value={entityId} onChange={(event) => setEntityId(event.target.value)} className={`${inputClass} mt-1`}><option value="">Select entity…</option>{selectedEntry.entities.map((item) => <option key={item.entity_id} value={item.entity_id}>{item.label} · {item.entity_type}</option>)}</select></label>
                            <button type="button" disabled={!rcsbReady || fetchRcsbMutation.isPending} onClick={() => fetchRcsbMutation.mutate()} className="rounded-lg border border-violet-400/50 px-3 py-2 text-sm disabled:opacity-40 md:col-span-2">
                                {fetchRcsbMutation.isPending ? 'Loading full deposited complex…' : `Use full ${selectedEntry.accession} complex in Mol*`}
                            </button>
                        </div>
                    )}
                </div>
            )}

            {activeTab === 'cached' && (
                <div className="space-y-2">
                    {cachedSources.isLoading && <div className={panelClass}>Loading cached structures…</div>}
                    {cachedSources.isError && <div role="alert" className={panelClass}>{cmApiError(cachedSources.error, 'Unable to load cached structures.')}</div>}
                    {cachedSources.data?.map((source) => (
                        <button
                            key={source.pdb_id}
                            type="button"
                            aria-pressed={selectedSource?.pdbId === source.pdb_id}
                            onClick={() => onSelect({ type: 'cached', url: source.url, name: `RCSB_${source.pdb_id}.pdb`, pdbId: source.pdb_id })}
                            className={`${panelClass} w-full text-left`}
                        >
                            <span className="block text-sm font-medium">RCSB {source.pdb_id}</span>
                            <span className="mt-1 block text-xs text-[var(--text-secondary)]">{source.size_bytes} bytes · used {source.last_used_at || source.cached_at}</span>
                        </button>
                    ))}
                </div>
            )}

            {error && <div role="alert" className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-100">{error}</div>}
        </div>
    );
}

export default Rfd3SourceSelector;
