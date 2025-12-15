import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchInputPresets, fetchPresetDirectories } from '../lib/api';

export interface ChainInfo {
    chainId: string;
    residueCount: number;
    description?: string;
}

export interface StructureInputProps {
    value: string;
    onChange: (value: string) => void;
    onBrowse: () => void;
    onChainParsed?: (chains: ChainInfo[]) => void;
    designChain?: string;
    targetChain?: string;
    onDesignChainChange?: (chain: string) => void;
    onTargetChainChange?: (chain: string) => void;
    showChainSelectors?: boolean;
    enableMultiSelect?: boolean;
    enableDirectory?: boolean;
}

export function StructureInput({
    value,
    onChange,
    onBrowse,
    onChainParsed,
    designChain,
    targetChain,
    onDesignChainChange,
    onTargetChainChange,
    showChainSelectors = true,
    enableMultiSelect = false,
    enableDirectory = false,
}: StructureInputProps) {
    const [mode, setMode] = useState<'presets' | 'browse' | 'multi' | 'batch'>('presets');
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
    const [parsedChains, setParsedChains] = useState<ChainInfo[]>([]);
    const [isParsing, setIsParsing] = useState(false);

    const { data: presetsData } = useQuery({
        queryKey: ['presets', 'pdb'],
        queryFn: () => fetchInputPresets('pdb'),
    });

    const { data: directoriesData } = useQuery({
        queryKey: ['preset-directories'],
        queryFn: () => fetchPresetDirectories(),
        enabled: enableDirectory,
    });

    const presets = presetsData?.data ?? [];
    const directories = directoriesData?.data ?? [];

    // Group presets by category
    const groupedPresets = presets.reduce((groups: Record<string, any[]>, preset: any) => {
        const cat = preset.category || 'General';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(preset);
        return groups;
    }, {});

    // Parse chain info when value changes
    useEffect(() => {
        if (!value || !value.endsWith('.pdb')) {
            setParsedChains([]);
            return;
        }

        // Try to parse chain info from the selected preset
        const preset = presets.find((p: any) => p.path === value);
        if (preset?.chains) {
            setParsedChains(preset.chains);
            onChainParsed?.(preset.chains);
            return;
        }

        // For custom files, we'd need a backend endpoint to parse
        // For now, show placeholder until we have that
        setIsParsing(true);
        // Simulated parse - in reality this would call an API
        setTimeout(() => {
            setIsParsing(false);
            // Check if file is from a known preset
            if (value.includes('trka') || value.includes('1www')) {
                const chains = [
                    { chainId: 'A', residueCount: 345, description: 'TrkA receptor' },
                    { chainId: 'B', residueCount: 112, description: 'NGF ligand' },
                ];
                setParsedChains(chains);
                onChainParsed?.(chains);
            } else if (value.includes('pd-l1') || value.includes('5o45')) {
                const chains = [
                    { chainId: 'A', residueCount: 219, description: 'PD-L1' },
                ];
                setParsedChains(chains);
                onChainParsed?.(chains);
            } else {
                setParsedChains([]);
            }
        }, 300);
    }, [value, presets]);

    // Handle multi-select toggle
    const togglePreset = (preset: any) => {
        const newSelected = new Set(selectedIds);
        if (newSelected.has(preset.id)) {
            newSelected.delete(preset.id);
        } else {
            newSelected.add(preset.id);
        }
        setSelectedIds(newSelected);
        const paths = presets
            .filter((p: any) => newSelected.has(p.id))
            .map((p: any) => p.path)
            .join(',');
        onChange(paths);
    };

    const selectedPreset = presets.find((p: any) => p.path === value);

    // Mode tabs
    const modes = [
        { id: 'presets', label: '📁 Presets', icon: '📁' },
        { id: 'browse', label: '📂 Browse', icon: '📂' },
        ...(enableMultiSelect ? [{ id: 'multi', label: '☑️ Multi', icon: '☑️' }] : []),
        ...(enableDirectory ? [{ id: 'batch', label: '📦 Batch', icon: '📦' }] : []),
    ];

    return (
        <div className="space-y-3">
            {/* Input Section */}
            <div className="bg-slate-900/50 border border-slate-700 rounded-xl p-4">
                {/* Mode Tabs */}
                <div className="flex gap-1 mb-3 border-b border-slate-700 pb-2">
                    {modes.map(m => (
                        <button
                            key={m.id}
                            onClick={() => setMode(m.id as any)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${mode === m.id
                                ? 'bg-cyan-600 text-white shadow-lg shadow-cyan-600/25'
                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700 hover:text-slate-300'
                                }`}
                        >
                            {m.label}
                        </button>
                    ))}
                </div>

                {/* Presets Mode */}
                {mode === 'presets' && (
                    <div className="space-y-3">
                        {/* Categorized Grid */}
                        <div className="space-y-3">
                            {Object.entries(groupedPresets).map(([category, catPresets]) => (
                                <div key={category}>
                                    <div className="text-xs text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-2">
                                        <span className="h-px bg-slate-700 flex-1"></span>
                                        <span>{category}</span>
                                        <span className="h-px bg-slate-700 flex-1"></span>
                                    </div>
                                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                                        {(catPresets as any[]).map((preset: any) => (
                                            <button
                                                key={preset.id}
                                                onClick={() => onChange(preset.path)}
                                                className={`p-2.5 rounded-lg text-left transition-all border ${value === preset.path
                                                    ? 'bg-cyan-600/20 border-cyan-500 text-cyan-300'
                                                    : 'bg-slate-800/50 border-slate-700 text-slate-300 hover:bg-slate-800 hover:border-slate-600'
                                                    }`}
                                            >
                                                <div className="text-sm font-medium truncate">{preset.name}</div>
                                                {preset.pdb_id && (
                                                    <div className="text-xs text-slate-500 font-mono">{preset.pdb_id}</div>
                                                )}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            ))}
                        </div>

                        {/* Fallback dropdown if no presets */}
                        {Object.keys(groupedPresets).length === 0 && (
                            <select
                                value={value}
                                onChange={(e) => onChange(e.target.value)}
                                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm"
                            >
                                <option value="">Select a preset...</option>
                                <optgroup label="Benchmark Targets">
                                    <option value="benchmarkdata/1www_trka.pdb">TrkA Receptor (1www)</option>
                                    <option value="benchmarkdata/5o45_pd-l1.pdb">PD-L1 (5o45)</option>
                                    <option value="benchmarkdata/3di3_il7ra.pdb">IL-7Rα (3di3)</option>
                                </optgroup>
                                <optgroup label="DNA Polymerases">
                                    <option value="rcsb/1kej_tdt.pdb">TdT (1kej)</option>
                                    <option value="rcsb/1kln_klenow.pdb">Klenow (1kln)</option>
                                </optgroup>
                            </select>
                        )}
                    </div>
                )}

                {/* Browse Mode */}
                {mode === 'browse' && (
                    <div className="space-y-3">
                        <div
                            onClick={onBrowse}
                            className="border-2 border-dashed border-slate-600 rounded-lg p-6 text-center cursor-pointer hover:border-cyan-500 hover:bg-cyan-500/5 transition-all"
                        >
                            <div className="text-3xl mb-2">📂</div>
                            <div className="text-sm text-slate-400">
                                Click to browse or drag & drop PDB/CIF file
                            </div>
                        </div>
                        <div className="flex gap-2">
                            <input
                                type="text"
                                value={value}
                                onChange={(e) => onChange(e.target.value)}
                                className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm font-mono outline-none focus:border-cyan-500"
                                placeholder="/path/to/structure.pdb"
                            />
                            <button
                                onClick={onBrowse}
                                className="px-4 py-2.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-white text-sm font-medium transition-colors"
                            >
                                Browse
                            </button>
                        </div>
                    </div>
                )}

                {/* Multi-Select Mode */}
                {mode === 'multi' && enableMultiSelect && (
                    <div className="space-y-2">
                        <div className="flex justify-between items-center mb-2">
                            <span className="text-xs text-slate-400">
                                Selected: {selectedIds.size} structures
                            </span>
                            <button
                                onClick={() => {
                                    if (selectedIds.size === presets.length) {
                                        setSelectedIds(new Set());
                                        onChange('');
                                    } else {
                                        setSelectedIds(new Set(presets.map((p: any) => p.id)));
                                        onChange(presets.map((p: any) => p.path).join(','));
                                    }
                                }}
                                className="text-xs text-cyan-400 hover:text-cyan-300"
                            >
                                {selectedIds.size === presets.length ? 'Deselect All' : 'Select All'}
                            </button>
                        </div>
                        <div className="max-h-64 overflow-y-auto space-y-1 bg-slate-800/50 rounded-lg p-2">
                            {presets.map((preset: any) => (
                                <label
                                    key={preset.id}
                                    className="flex items-center gap-3 p-2 hover:bg-slate-700/50 rounded cursor-pointer"
                                >
                                    <input
                                        type="checkbox"
                                        checked={selectedIds.has(preset.id)}
                                        onChange={() => togglePreset(preset)}
                                        className="w-4 h-4 rounded text-cyan-600 bg-slate-700 border-slate-600"
                                    />
                                    <span className="text-sm text-white">{preset.name}</span>
                                </label>
                            ))}
                        </div>
                    </div>
                )}

                {/* Batch/Directory Mode */}
                {mode === 'batch' && enableDirectory && (
                    <div className="space-y-3">
                        <select
                            value={value}
                            onChange={(e) => onChange(e.target.value)}
                            className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm"
                        >
                            <option value="">Select a batch directory...</option>
                            {directories.map((dir: any) => (
                                <option key={dir.id} value={dir.path}>{dir.name}</option>
                            ))}
                        </select>
                        <div className="flex gap-2">
                            <input
                                type="text"
                                value={value}
                                onChange={(e) => onChange(e.target.value)}
                                className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm font-mono outline-none"
                                placeholder="/path/to/pdb/directory"
                            />
                            <button
                                onClick={onBrowse}
                                className="px-4 py-2.5 bg-slate-700 hover:bg-slate-600 rounded-lg text-white text-sm"
                            >
                                Browse
                            </button>
                        </div>
                    </div>
                )}
            </div>

            {/* Selected Structure Info */}
            {value && (
                <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-xs text-slate-400 uppercase tracking-wider">Selected Structure</span>
                        {isParsing && (
                            <span className="text-xs text-cyan-400 animate-pulse">Parsing...</span>
                        )}
                    </div>
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 bg-gradient-to-br from-cyan-600/30 to-blue-600/30 rounded-lg flex items-center justify-center text-lg">
                            🧬
                        </div>
                        <div className="flex-1 min-w-0">
                            <div className="text-sm font-medium text-white truncate">
                                {selectedPreset?.name || value.split('/').pop()}
                            </div>
                            <div className="text-xs text-slate-500 font-mono truncate">{value}</div>
                        </div>
                        <button
                            onClick={() => onChange('')}
                            className="text-slate-500 hover:text-red-400 transition-colors"
                        >
                            ✕
                        </button>
                    </div>

                    {/* Chain Info */}
                    {parsedChains.length > 0 && showChainSelectors && (
                        <div className="mt-3 pt-3 border-t border-slate-700/50">
                            <div className="text-xs text-slate-400 mb-2">Detected Chains</div>
                            <div className="flex flex-wrap gap-2">
                                {parsedChains.map(chain => (
                                    <div
                                        key={chain.chainId}
                                        className="flex items-center gap-2 bg-slate-800 rounded-lg px-3 py-1.5"
                                    >
                                        <span className="text-sm font-mono text-cyan-400">{chain.chainId}</span>
                                        <span className="text-xs text-slate-500">{chain.residueCount} aa</span>
                                        {onDesignChainChange && (
                                            <button
                                                onClick={() => onDesignChainChange(chain.chainId)}
                                                className={`text-xs px-1.5 py-0.5 rounded transition-colors ${designChain === chain.chainId
                                                    ? 'bg-green-600/30 text-green-400'
                                                    : 'bg-slate-700 text-slate-400 hover:bg-green-600/20 hover:text-green-400'
                                                    }`}
                                            >
                                                Design
                                            </button>
                                        )}
                                        {onTargetChainChange && (
                                            <button
                                                onClick={() => onTargetChainChange(chain.chainId)}
                                                className={`text-xs px-1.5 py-0.5 rounded transition-colors ${targetChain === chain.chainId
                                                    ? 'bg-amber-600/30 text-amber-400'
                                                    : 'bg-slate-700 text-slate-400 hover:bg-amber-600/20 hover:text-amber-400'
                                                    }`}
                                            >
                                                Target
                                            </button>
                                        )}
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
