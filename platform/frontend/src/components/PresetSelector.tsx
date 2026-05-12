import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchInputPresets, fetchPresetDirectories } from '../lib/api';

export interface PresetSelectorProps {
    presetType: 'pdb' | 'sequence' | 'yaml' | 'contig' | 'ntp' | 'ligand';
    value: string;
    onChange: (value: string) => void;
    onBrowse: () => void;
    label?: string;
    placeholder?: string;
    enableMultiSelect?: boolean;  // Show multi-select mode for PDB type
    enableDirectory?: boolean;    // Show directory mode for PDB type
}

export function PresetSelector({ presetType, value, onChange, onBrowse, placeholder, enableMultiSelect = false, enableDirectory = false }: PresetSelectorProps) {
    const { data: presetsData } = useQuery({
        queryKey: ['presets', presetType],
        queryFn: () => fetchInputPresets(presetType),
    });

    const { data: directoriesData } = useQuery({
        queryKey: ['preset-directories'],
        queryFn: () => fetchPresetDirectories(),
        enabled: enableDirectory && presetType === 'pdb',
    });

    const presets = presetsData?.data ?? [];
    const directories = directoriesData?.data ?? [];

    // Mode types: preset (single), multi (checkboxes), directory (batch dirs), manual (raw input)
    const [mode, setMode] = useState<'preset' | 'multi' | 'directory' | 'manual'>('preset');
    const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

    // Group presets by category
    const groupedPresets = presets.reduce((groups: Record<string, UntypedApiValue[]>, preset: UntypedApiValue) => {
        const cat = preset.category || 'general';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(preset);
        return groups;
    }, {});

    // Get display value for the selected preset
    const selectedPreset = presets.find((p: UntypedApiValue) =>
        presetType === 'pdb' ? p.path === value :
            presetType === 'contig' ? p.value === value :
                presetType === 'sequence' ? p.sequence === value : p.id === value
    );

    // Handle multi-select toggle
    const togglePreset = (preset: UntypedApiValue) => {
        const newSelected = new Set(selectedIds);
        if (newSelected.has(preset.id)) {
            newSelected.delete(preset.id);
        } else {
            newSelected.add(preset.id);
        }
        setSelectedIds(newSelected);

        // Update value with comma-separated paths for multi-select
        const selectedPaths = presets
            .filter((p: UntypedApiValue) => newSelected.has(p.id))
            .map((p: UntypedApiValue) => p.path)
            .join(',');
        onChange(selectedPaths);
    };

    // Check if all selected
    const allSelected = selectedIds.size === presets.length && presets.length > 0;
    const toggleAll = () => {
        if (allSelected) {
            setSelectedIds(new Set());
            onChange('');
        } else {
            const allIds = new Set(presets.map((p: UntypedApiValue) => p.id));
            setSelectedIds(allIds);
            onChange(presets.map((p: UntypedApiValue) => p.path).join(','));
        }
    };

    // Mode tabs based on enabled features
    const availableModes = [
        { id: 'preset', label: 'Presets', enabled: true },
        { id: 'multi', label: 'Multi-Select', enabled: enableMultiSelect && presetType === 'pdb' },
        { id: 'directory', label: 'Directory', enabled: enableDirectory && presetType === 'pdb' },
        { id: 'manual', label: 'Manual', enabled: true },
    ].filter(m => m.enabled);

    return (
        <div className="space-y-2">
            {/* Mode Toggle */}
            <div className="flex gap-1 text-xs flex-wrap">
                {availableModes.map(m => (
                    <button
                        key={m.id}
                        onClick={() => setMode(m.id as UntypedApiValue)}
                        className={`px-2 py-1 rounded transition-colors ${mode === m.id ? 'bg-blue-600 text-white' : 'bg-slate-700 text-slate-400 hover:bg-slate-600'}`}
                    >
                        {m.label}
                    </button>
                ))}
            </div>

            {/* Single Preset Mode */}
            {mode === 'preset' && (
                <div className="space-y-2">
                    <select
                        value={selectedPreset ? (presetType === 'pdb' ? selectedPreset.path : presetType === 'contig' ? selectedPreset.value : selectedPreset.id) : ''}
                        onChange={(e) => onChange(e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    >
                        <option value="">{placeholder || 'Select a preset...'}</option>
                        {presets.map((preset: UntypedApiValue) => (
                            <option
                                key={preset.id}
                                value={presetType === 'pdb' ? preset.path : presetType === 'contig' ? preset.value : preset.id}
                            >
                                {preset.name}
                            </option>
                        ))}
                    </select>
                    {selectedPreset && (
                        <p className="text-xs text-slate-500">{selectedPreset.description}</p>
                    )}
                    {/* Editable value field */}
                    {value && (
                        <div className="mt-2">
                            <label className="text-xs text-slate-400 block mb-1">Edit value (modify as needed):</label>
                            <input
                                type="text"
                                value={value}
                                onChange={(e) => onChange(e.target.value)}
                                className="w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm font-mono outline-none focus:ring-2 focus:ring-blue-500"
                            />
                        </div>
                    )}
                </div>
            )}

            {/* Multi-Select Mode (Checkboxes) */}
            {mode === 'multi' && (
                <div className="space-y-2">
                    <div className="bg-slate-900 border border-slate-700 rounded-lg p-3 max-h-64 overflow-y-auto">
                        {/* Select All */}
                        <label className="flex items-center gap-2 p-2 hover:bg-slate-800 rounded cursor-pointer border-b border-slate-700 mb-2">
                            <input
                                type="checkbox"
                                checked={allSelected}
                                onChange={toggleAll}
                                className="w-4 h-4 rounded text-blue-600 bg-slate-700 border-slate-600 focus:ring-blue-500"
                            />
                            <span className="text-sm text-white font-medium">Select All ({presets.length})</span>
                        </label>

                        {/* Grouped by category */}
                        {Object.entries(groupedPresets).map(([category, catPresets]) => (
                            <div key={category} className="mb-2">
                                <div className="text-xs text-slate-400 uppercase tracking-wide mb-1 px-2">{category}</div>
                                {(catPresets as UntypedApiValue[]).map((preset: UntypedApiValue) => (
                                    <label
                                        key={preset.id}
                                        className="flex items-center gap-2 p-2 hover:bg-slate-800 rounded cursor-pointer"
                                    >
                                        <input
                                            type="checkbox"
                                            checked={selectedIds.has(preset.id)}
                                            onChange={() => togglePreset(preset)}
                                            className="w-4 h-4 rounded text-blue-600 bg-slate-700 border-slate-600 focus:ring-blue-500"
                                        />
                                        <span className="text-sm text-white">{preset.name}</span>
                                    </label>
                                ))}
                            </div>
                        ))}
                    </div>
                    <p className="text-xs text-slate-400">Selected: {selectedIds.size} files</p>
                </div>
            )}

            {/* Directory Mode */}
            {mode === 'directory' && (
                <div className="space-y-2">
                    <select
                        value={value}
                        onChange={(e) => onChange(e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    >
                        <option value="">Select a batch directory...</option>
                        {directories.map((dir: UntypedApiValue) => (
                            <option key={dir.id} value={dir.path}>
                                {dir.name}
                            </option>
                        ))}
                    </select>
                    {directories.find((d: UntypedApiValue) => d.path === value)?.description && (
                        <p className="text-xs text-slate-500">
                            {directories.find((d: UntypedApiValue) => d.path === value)?.description}
                        </p>
                    )}
                    {/* Browse button for custom directory */}
                    <div className="flex gap-2 items-center">
                        <input
                            type="text"
                            value={value}
                            onChange={(e) => onChange(e.target.value)}
                            placeholder="/path/to/directory"
                            className="flex-1 bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm font-mono outline-none"
                        />
                        <button
                            onClick={onBrowse}
                            className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-200 text-sm transition-colors"
                        >
                            Browse
                        </button>
                    </div>
                </div>
            )}

            {/* Manual Mode */}
            {mode === 'manual' && (
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={value}
                        onChange={(e) => onChange(e.target.value)}
                        className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono outline-none"
                        placeholder={presetType === 'pdb' ? '/path/to/file.pdb or /path/to/directory' : 'Enter value...'}
                    />
                    {(presetType === 'pdb' || presetType === 'yaml') && (
                        <button
                            onClick={onBrowse}
                            className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-200 text-sm transition-colors"
                        >
                            Browse
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}
