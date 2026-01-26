/**
 * FrameworkBrowser - Browse and select antibody frameworks from SAbDab
 * 
 * Tabs: Presets | SAbDab | Cached
 * Provides search, download, and selection of VHH frameworks
 */

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    searchSabdabFrameworks,
    downloadSabdabFramework,
    listCachedFrameworks,
    getSabdabAttribution,
    type SAbDabSearchResult,
    type CachedFramework
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
    // Humanized VHH - RECOMMENDED for therapeutics
    {
        id: 'h-nbbcii10-fgla',
        name: 'h-NbBCII10 FGLA (Humanized)',
        description: 'Universal humanized VHH scaffold - gold standard for therapeutic nanobodies (PDB: 3EAK)',
        type: 'vhh',
        pdbCode: '3EAK',
        sequence: 'EVQLVESGGGLVQPGGSLRLSCAASGGSEYSYSTFSLGWFRQAPGKEREFVAAIASMGGLTYYADSVKGRFTISRDNSKNTLYLQMNSLRAEDTAVYYCAAVRGYFMRLPSSHNFRYWGQGTLVTVSS',
        humanized: true,
        recommended: true
    },
    // Original camelid VHH
    {
        id: 'cabbcii10',
        name: 'cAbBCII10 (Camelid)',
        description: 'Original camelid VHH framework - well characterized (PDB: 3DWT)',
        type: 'vhh',
        pdbCode: '3DWT',
        sequence: 'QVQLVESGGGLVQPGGSLRLSCAASGGSEYSYSTFSLGWFRQAPGQGLEAVAAIASMGGLTYYADSVKGRFTISRDNSKNTLYLQMNSLRAEDTAVYYCAAVRGYFMRLPSSHNFRYWGQGTLVTVS',
        humanized: false
    },
    // Human VH3-like
    {
        id: 'vhh28-vh3',
        name: 'VHH-28 (VH3-like)',
        description: 'Extensively homologous to human VH3 genes - low immunogenicity (PDB: 5U64)',
        type: 'vhh',
        pdbCode: '5U64',
        sequence: null, // Will fetch from SAbDab
        humanized: false,
        humanLike: true
    },
    // Llama-derived therapeutic
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
    cdrH3Length?: number;  // CDR-H3 length from SAbDab for auto-population
}

interface FrameworkBrowserProps {
    onSelect: (framework: SelectedFramework | null) => void;
    selectedFramework?: SelectedFramework | null;
    showCustomUpload?: boolean;
}

export function FrameworkBrowser({
    onSelect,
    selectedFramework,
    showCustomUpload: _showCustomUpload = true
}: FrameworkBrowserProps) {
    const [activeTab, setActiveTab] = useState<'presets' | 'sabdab' | 'cached'>('presets');
    const [species, setSpecies] = useState('');
    const [resolutionMax, setResolutionMax] = useState(2.5);
    const [cdrH3Min, setCdrH3Min] = useState<number | ''>('');
    const [cdrH3Max, setCdrH3Max] = useState<number | ''>('');
    const [sortBy, setSortBy] = useState<'resolution' | 'cdr_h3_length' | 'species' | 'pdb_code'>('resolution');
    const [sortDesc, setSortDesc] = useState(false);
    const [searchTriggered, setSearchTriggered] = useState(false);
    const [downloadingPdb, setDownloadingPdb] = useState<string | null>(null);

    const queryClient = useQueryClient();

    // Search SAbDab
    const { data: searchResults, isLoading: searchLoading, error: searchError } = useQuery({
        queryKey: ['sabdab-search', species, resolutionMax, cdrH3Min, cdrH3Max, sortBy, sortDesc],
        queryFn: () => searchSabdabFrameworks({
            species: species || undefined,
            resolution_max: resolutionMax,
            cdr_h3_min: cdrH3Min || undefined,
            cdr_h3_max: cdrH3Max || undefined,
            limit: 50,
            sort_by: sortBy,
            sort_desc: sortDesc
        }),
        enabled: searchTriggered && activeTab === 'sabdab',
    });
    const frameworks: SAbDabSearchResult[] = (searchResults as any)?.data ?? [];

    // List cached frameworks
    const { data: cachedData, isLoading: cachedLoading } = useQuery({
        queryKey: ['cached-frameworks'],
        queryFn: listCachedFrameworks,
        enabled: activeTab === 'cached',
    });
    const cached: CachedFramework[] = (cachedData as any)?.data?.frameworks ?? [];

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
                convert_hlt: true
            });
            return { ...response.data, cdrH3Length };
        },
        onSuccess: (data) => {
            queryClient.invalidateQueries({ queryKey: ['cached-frameworks'] });
            onSelect({
                type: 'sabdab',
                id: data.pdb_code,
                name: `SAbDab: ${data.pdb_code}`,
                pdbCode: data.pdb_code,
                filePath: data.file_path || undefined,
                cdrH3Length: data.cdrH3Length ?? undefined
            });
            setDownloadingPdb(null);
        },
        onError: () => {
            setDownloadingPdb(null);
        }
    });

    const handlePresetSelect = (preset: typeof FRAMEWORK_PRESETS[0]) => {
        onSelect({
            type: 'preset',
            id: preset.id,
            name: preset.name,
            pdbCode: preset.pdbCode || undefined,
            sequence: preset.sequence || undefined
        });
    };

    const handleCachedSelect = (framework: CachedFramework) => {
        onSelect({
            type: 'cached',
            id: framework.pdb_code,
            name: `Cached: ${framework.pdb_code}`,
            pdbCode: framework.pdb_code,
            filePath: framework.file_path
        });
    };

    const handleSearch = () => {
        setSearchTriggered(true);
        queryClient.invalidateQueries({ queryKey: ['sabdab-search'] });
    };

    const tabs = [
        { id: 'presets', label: 'Presets' },
        { id: 'sabdab', label: 'SAbDab' },
        { id: 'cached', label: 'Cached' },
    ] as const;

    return (
        <div className="space-y-3">
            <label className="block text-sm font-medium text-slate-400">Framework Selection</label>

            {/* Selected indicator */}
            {selectedFramework && (
                <div className="flex items-center justify-between px-3 py-2 bg-purple-500/10 border border-purple-500/30 rounded-lg">
                    <div className="flex items-center gap-2 text-sm">
                        <span className="w-2 h-2 bg-purple-400 rounded-full" />
                        <span className="text-purple-300 truncate">
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
                            ? 'text-purple-400 border-b-2 border-purple-400 -mb-px'
                            : 'text-slate-400 hover:text-slate-200'
                            }`}
                    >
                        {tab.label}
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
                                    ? 'bg-purple-500/20 border border-purple-500/50 text-purple-300'
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
                        {/* Search filters */}
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
                            <div>
                                <label className="text-xs text-slate-500">CDR-H3 Min Length</label>
                                <input
                                    type="number"
                                    value={cdrH3Min}
                                    onChange={e => setCdrH3Min(e.target.value ? parseInt(e.target.value) : '')}
                                    placeholder="Any"
                                    min={5}
                                    max={30}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-500">CDR-H3 Max Length</label>
                                <input
                                    type="number"
                                    value={cdrH3Max}
                                    onChange={e => setCdrH3Max(e.target.value ? parseInt(e.target.value) : '')}
                                    placeholder="Any"
                                    min={5}
                                    max={30}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-sm text-white"
                                />
                            </div>
                        </div>

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
                                <option value="species">Species</option>
                                <option value="pdb_code">PDB Code</option>
                            </select>
                            <button
                                onClick={() => setSortDesc(!sortDesc)}
                                className={`px-2 py-1.5 text-xs rounded border ${sortDesc
                                        ? 'bg-purple-500/20 border-purple-500/50 text-purple-300'
                                        : 'bg-slate-900 border-slate-700 text-slate-400'
                                    }`}
                                title={sortDesc ? 'Sort descending' : 'Sort ascending'}
                            >
                                {sortDesc ? '↓ DESC' : '↑ ASC'}
                            </button>
                        </div>

                        <button
                            onClick={handleSearch}
                            disabled={searchLoading}
                            className="w-full px-4 py-2 bg-purple-600 hover:bg-purple-500 text-white rounded-lg text-sm font-medium disabled:opacity-50"
                        >
                            {searchLoading ? 'Searching NanoSAbDab...' : 'Search VHH Frameworks'}
                        </button>

                        {searchError && (
                            <div className="text-xs text-red-400 p-2 bg-red-500/10 rounded">
                                Search failed. Rate limit may apply (2s between requests).
                            </div>
                        )}

                        {/* Results */}
                        {frameworks.length > 0 && (
                            <div className="space-y-1 max-h-48 overflow-y-auto">
                                {frameworks.map((fw) => (
                                    <button
                                        key={fw.pdb_code}
                                        onClick={() => downloadMutation.mutate({ pdbCode: fw.pdb_code, cdrH3Length: fw.cdr_h3_length })}
                                        disabled={downloadingPdb !== null}
                                        className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${selectedFramework?.pdbCode === fw.pdb_code
                                            ? 'bg-purple-500/20 border border-purple-500/50'
                                            : 'bg-slate-900/50 hover:bg-slate-700/50'
                                            }`}
                                    >
                                        <div className="flex items-center justify-between">
                                            <span className="font-mono text-sm text-purple-400">
                                                {fw.pdb_code.toUpperCase()}
                                            </span>
                                            <div className="flex items-center gap-2 text-xs text-slate-500">
                                                {fw.resolution && <span>{fw.resolution.toFixed(1)}Å</span>}
                                                {fw.cdr_h3_length && <span>H3:{fw.cdr_h3_length}</span>}
                                                {downloadingPdb === fw.pdb_code && (
                                                    <span className="text-purple-400">Downloading...</span>
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
                            <div className="space-y-1 max-h-48 overflow-y-auto">
                                {cached.map(fw => (
                                    <button
                                        key={`${fw.pdb_code}-${fw.scheme}`}
                                        onClick={() => handleCachedSelect(fw)}
                                        className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${selectedFramework?.pdbCode === fw.pdb_code
                                            ? 'bg-purple-500/20 border border-purple-500/50'
                                            : 'bg-slate-900/50 hover:bg-slate-700/50'
                                            }`}
                                    >
                                        <div className="flex items-center justify-between">
                                            <span className="font-mono text-sm text-purple-400">
                                                {fw.pdb_code.toUpperCase()}
                                            </span>
                                            <span className="text-xs text-slate-500">
                                                {fw.scheme} • {(fw.size_bytes / 1024).toFixed(1)} KB
                                            </span>
                                        </div>
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

export default FrameworkBrowser;
