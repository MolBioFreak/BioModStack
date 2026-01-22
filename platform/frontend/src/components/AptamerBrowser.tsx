/**
 * AptamerBrowser - Browse and select aptamers from curated presets and Ribocentre database.
 * 
 * Pattern follows TargetAntigenSelector and FrameworkBrowser for consistency.
 */

import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../lib/api';

interface Aptamer {
    id: string;
    name: string;
    sequence: string;
    target?: string;
    kd_value?: number;
    kd_unit?: string;
    aptamer_type: string;  // DNA or RNA
    description?: string;
    source: string;
    length?: number;
}

interface AptamerBrowserProps {
    onSelect: (aptamer: Aptamer | null) => void;
    selectedAptamer?: Aptamer | null;
    aptamerType?: 'DNA' | 'RNA';  // Filter by type
}

export function AptamerBrowser({ onSelect, selectedAptamer, aptamerType }: AptamerBrowserProps) {
    const [searchQuery, setSearchQuery] = useState('');
    const [activeTab, setActiveTab] = useState<'presets' | 'search'>('presets');

    // Fetch curated presets
    const { data: presetsData, isLoading: presetsLoading } = useQuery({
        queryKey: ['aptamer-presets'],
        queryFn: async () => {
            const res = await api.get('/api/ribocentre/presets');
            return res.data;
        }
    });

    // Search aptamers
    const { data: searchData, isLoading: searchLoading, refetch: doSearch } = useQuery({
        queryKey: ['aptamer-search', searchQuery, aptamerType],
        queryFn: async () => {
            if (!searchQuery || searchQuery.length < 2) return { results: [] };
            const params = new URLSearchParams({ q: searchQuery, max_results: '20' });
            if (aptamerType) params.append('aptamer_type', aptamerType);
            const res = await api.get(`/api/ribocentre/search?${params}`);
            return res.data;
        },
        enabled: searchQuery.length >= 2
    });

    // Filter presets by type
    const filteredPresets = useMemo(() => {
        const presets = presetsData?.presets || [];
        if (!aptamerType) return presets;
        return presets.filter((a: Aptamer) => a.aptamer_type.toUpperCase() === aptamerType);
    }, [presetsData, aptamerType]);

    const handleSelect = (apt: Aptamer) => {
        if (selectedAptamer?.id === apt.id) {
            onSelect(null);  // Deselect
        } else {
            onSelect(apt);
        }
    };

    const formatKd = (apt: Aptamer) => {
        if (!apt.kd_value) return null;
        return `Kd: ${apt.kd_value} ${apt.kd_unit || 'nM'}`;
    };

    return (
        <div className="aptamer-browser bg-slate-800 rounded-lg p-4">
            {/* Tab Selector */}
            <div className="flex gap-2 mb-4">
                <button
                    onClick={() => setActiveTab('presets')}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${activeTab === 'presets'
                        ? 'bg-emerald-600 text-white'
                        : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                >
                    Curated Presets
                </button>
                <button
                    onClick={() => setActiveTab('search')}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${activeTab === 'search'
                        ? 'bg-emerald-600 text-white'
                        : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                        }`}
                >
                    Search Database
                </button>
            </div>

            {/* Search Tab */}
            {activeTab === 'search' && (
                <div className="space-y-3">
                    <div className="flex gap-2">
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder="Search by target, name, or sequence..."
                            className="flex-1 bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-white"
                        />
                        <button
                            onClick={() => doSearch()}
                            disabled={searchQuery.length < 2}
                            className="px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-500 disabled:opacity-50"
                        >
                            Search
                        </button>
                    </div>
                    {searchLoading && <div className="text-slate-400 text-sm">Searching...</div>}
                    {searchQuery.length >= 2 && searchData?.results?.length === 0 && (
                        <div className="text-slate-400 text-sm">No results found</div>
                    )}
                </div>
            )}

            {/* Presets Grid */}
            {activeTab === 'presets' && (
                <div className="space-y-2">
                    {presetsLoading && <div className="text-slate-400 text-sm">Loading presets...</div>}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-64 overflow-y-auto">
                        {filteredPresets.map((apt: Aptamer) => (
                            <button
                                key={apt.id}
                                onClick={() => handleSelect(apt)}
                                className={`p-3 rounded-lg border-2 text-left transition-all ${selectedAptamer?.id === apt.id
                                    ? 'border-emerald-500 bg-emerald-500/10'
                                    : 'border-slate-600 bg-slate-700/50 hover:border-slate-500'
                                    }`}
                            >
                                <div className="flex justify-between items-start">
                                    <div className="font-medium text-white text-sm">{apt.name}</div>
                                    <span className={`text-xs px-2 py-0.5 rounded ${apt.aptamer_type === 'RNA'
                                        ? 'bg-purple-500/20 text-purple-300'
                                        : 'bg-blue-500/20 text-blue-300'
                                        }`}>
                                        {apt.aptamer_type}
                                    </span>
                                </div>
                                {apt.target && (
                                    <div className="text-xs text-slate-400 mt-1">Target: {apt.target}</div>
                                )}
                                <div className="text-xs text-slate-500 mt-1 font-mono truncate">
                                    {apt.sequence.slice(0, 30)}{apt.sequence.length > 30 ? '...' : ''}
                                </div>
                                <div className="flex gap-2 mt-1 text-xs text-slate-400">
                                    <span>{apt.length || apt.sequence.length} nt</span>
                                    {formatKd(apt) && <span>{formatKd(apt)}</span>}
                                </div>
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {/* Search Results */}
            {activeTab === 'search' && searchData?.results?.length > 0 && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-2 max-h-64 overflow-y-auto mt-3">
                    {searchData.results.map((apt: Aptamer) => (
                        <button
                            key={apt.id}
                            onClick={() => handleSelect(apt)}
                            className={`p-3 rounded-lg border-2 text-left transition-all ${selectedAptamer?.id === apt.id
                                ? 'border-emerald-500 bg-emerald-500/10'
                                : 'border-slate-600 bg-slate-700/50 hover:border-slate-500'
                                }`}
                        >
                            <div className="flex justify-between items-start">
                                <div className="font-medium text-white text-sm">{apt.name}</div>
                                <span className={`text-xs px-2 py-0.5 rounded ${apt.aptamer_type === 'RNA'
                                    ? 'bg-purple-500/20 text-purple-300'
                                    : 'bg-blue-500/20 text-blue-300'
                                    }`}>
                                    {apt.aptamer_type}
                                </span>
                            </div>
                            {apt.target && (
                                <div className="text-xs text-slate-400 mt-1">Target: {apt.target}</div>
                            )}
                            <div className="text-xs text-slate-500 mt-1 font-mono truncate">
                                {apt.sequence.slice(0, 30)}{apt.sequence.length > 30 ? '...' : ''}
                            </div>
                        </button>
                    ))}
                </div>
            )}

            {/* Selected Aptamer Display */}
            {selectedAptamer && (
                <div className="mt-4 p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-lg">
                    <div className="flex justify-between items-start">
                        <div>
                            <div className="font-medium text-emerald-300">{selectedAptamer.name}</div>
                            {selectedAptamer.description && (
                                <div className="text-xs text-slate-400 mt-1">{selectedAptamer.description}</div>
                            )}
                        </div>
                        <button
                            onClick={() => onSelect(null)}
                            className="text-slate-400 hover:text-white text-sm"
                        >
                            Clear
                        </button>
                    </div>
                    <div className="mt-2 p-2 bg-slate-800 rounded text-xs font-mono text-white break-all">
                        {selectedAptamer.sequence}
                    </div>
                </div>
            )}
        </div>
    );
}

export type { Aptamer };
export default AptamerBrowser;
