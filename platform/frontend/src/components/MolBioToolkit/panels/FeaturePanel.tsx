/**
 * FeaturePanel - Comprehensive feature/annotation management
 * 
 * Features:
 * - Search/filter by name or type
 * - Sort by position, name, type, or length
 * - Edit existing features (inline)
 * - Bulk selection and delete
 * - Custom color picker
 * - Description/notes field
 * - Jump to feature on click
 * - Expanded feature type list
 */

import { useState, useMemo, useCallback } from 'react';
import type { SequenceData, Feature, SelectionInfo, HighlightedRegion } from '../types';

interface FeaturePanelProps {
    sequenceData: SequenceData;
    selection: SelectionInfo | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onAddFeature: (feature: Feature) => void;
    onRemoveFeature: (featureId: string) => void;
    onUpdateFeature?: (feature: Feature) => void;
    onJumpToPosition?: (position: number) => void;
}

// Comprehensive feature types with colors matching common conventions
const FEATURE_TYPES = [
    // Coding/Structural
    { value: 'CDS', label: 'CDS (Coding Sequence)', color: '#22c55e', category: 'Coding' },
    { value: 'gene', label: 'Gene', color: '#3b82f6', category: 'Coding' },
    { value: 'exon', label: 'Exon', color: '#10b981', category: 'Coding' },
    { value: 'intron', label: 'Intron', color: '#6b7280', category: 'Coding' },
    { value: 'mRNA', label: 'mRNA', color: '#14b8a6', category: 'Coding' },

    // Regulatory
    { value: 'promoter', label: 'Promoter', color: '#8b5cf6', category: 'Regulatory' },
    { value: 'terminator', label: 'Terminator', color: '#ef4444', category: 'Regulatory' },
    { value: 'RBS', label: 'Ribosome Binding Site', color: '#14b8a6', category: 'Regulatory' },
    { value: 'operator', label: 'Operator', color: '#f97316', category: 'Regulatory' },
    { value: 'enhancer', label: 'Enhancer', color: '#a855f7', category: 'Regulatory' },
    { value: '5UTR', label: "5' UTR", color: '#06b6d4', category: 'Regulatory' },
    { value: '3UTR', label: "3' UTR", color: '#0891b2', category: 'Regulatory' },
    { value: 'polyA_signal', label: 'Poly-A Signal', color: '#e11d48', category: 'Regulatory' },

    // Replication/Origin
    { value: 'rep_origin', label: 'Origin of Replication', color: '#ec4899', category: 'Replication' },
    { value: 'oriT', label: 'oriT (Transfer Origin)', color: '#db2777', category: 'Replication' },

    // Selection Markers
    { value: 'resistance', label: 'Antibiotic Resistance', color: '#dc2626', category: 'Marker' },
    { value: 'reporter', label: 'Reporter Gene', color: '#65a30d', category: 'Marker' },
    { value: 'tag', label: 'Protein Tag', color: '#f59e0b', category: 'Marker' },

    // Binding/Interaction
    { value: 'primer_bind', label: 'Primer Binding Site', color: '#f59e0b', category: 'Binding' },
    { value: 'protein_bind', label: 'Protein Binding Site', color: '#0ea5e9', category: 'Binding' },
    { value: 'misc_binding', label: 'Misc Binding Site', color: '#64748b', category: 'Binding' },

    // Recombination
    { value: 'attB', label: 'attB (Gateway)', color: '#7c3aed', category: 'Recombination' },
    { value: 'attP', label: 'attP (Gateway)', color: '#6d28d9', category: 'Recombination' },
    { value: 'attL', label: 'attL (Gateway)', color: '#5b21b6', category: 'Recombination' },
    { value: 'attR', label: 'attR (Gateway)', color: '#4c1d95', category: 'Recombination' },
    { value: 'loxP', label: 'loxP (Cre)', color: '#be185d', category: 'Recombination' },
    { value: 'FRT', label: 'FRT (Flp)', color: '#9d174d', category: 'Recombination' },

    // Misc
    { value: 'misc_feature', label: 'Misc Feature', color: '#6b7280', category: 'Other' },
    { value: 'misc_difference', label: 'Variation/Mutation', color: '#fbbf24', category: 'Other' },
    { value: 'misc_recomb', label: 'Recombination Site', color: '#a78bfa', category: 'Other' },
    { value: 'source', label: 'Source', color: '#94a3b8', category: 'Other' },
];

type SortOption = 'position' | 'name' | 'type' | 'length';

// Color palette for custom colors
const COLOR_PALETTE = [
    '#ef4444', '#f97316', '#f59e0b', '#eab308', '#84cc16',
    '#22c55e', '#10b981', '#14b8a6', '#06b6d4', '#0ea5e9',
    '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7', '#d946ef',
    '#ec4899', '#f43f5e', '#78716c', '#64748b', '#475569'
];

export function FeaturePanel({
    sequenceData,
    selection,
    onHighlight,
    onAddFeature,
    onRemoveFeature,
    onUpdateFeature,
    onJumpToPosition
}: FeaturePanelProps) {
    // Form state
    const [newFeatureName, setNewFeatureName] = useState('');
    const [newFeatureType, setNewFeatureType] = useState('misc_feature');
    const [newFeatureStart, setNewFeatureStart] = useState<number | ''>('');
    const [newFeatureEnd, setNewFeatureEnd] = useState<number | ''>('');
    const [newFeatureStrand, setNewFeatureStrand] = useState<1 | -1>(1);
    const [newFeatureColor, setNewFeatureColor] = useState('#6b7280');
    const [newFeatureDescription, setNewFeatureDescription] = useState('');
    const [showColorPicker, setShowColorPicker] = useState(false);

    // List state
    const [searchQuery, setSearchQuery] = useState('');
    const [filterType, setFilterType] = useState<string>('all');
    const [sortBy, setSortBy] = useState<SortOption>('position');
    const [sortAsc, setSortAsc] = useState(true);
    const [selectedFeatures, setSelectedFeatures] = useState<Set<string>>(new Set());
    const [editingFeature, setEditingFeature] = useState<string | null>(null);

    // Edit form state
    const [editName, setEditName] = useState('');
    const [editStart, setEditStart] = useState<number>(0);
    const [editEnd, setEditEnd] = useState<number>(0);
    const [editStrand, setEditStrand] = useState<1 | -1>(1);
    const [editColor, setEditColor] = useState('#6b7280');

    const features = sequenceData.features || [];

    // Update color when type changes
    const handleTypeChange = (type: string) => {
        setNewFeatureType(type);
        const typeInfo = FEATURE_TYPES.find(t => t.value === type);
        if (typeInfo) {
            setNewFeatureColor(typeInfo.color);
        }
    };

    // Filter and sort features
    const processedFeatures = useMemo(() => {
        let result = [...features];

        // Search filter
        if (searchQuery) {
            const query = searchQuery.toLowerCase();
            result = result.filter(f =>
                f.name.toLowerCase().includes(query) ||
                f.type.toLowerCase().includes(query)
            );
        }

        // Type filter
        if (filterType !== 'all') {
            result = result.filter(f => f.type === filterType);
        }

        // Sort
        result.sort((a, b) => {
            let cmp = 0;
            switch (sortBy) {
                case 'position':
                    cmp = a.start - b.start;
                    break;
                case 'name':
                    cmp = a.name.localeCompare(b.name);
                    break;
                case 'type':
                    cmp = a.type.localeCompare(b.type);
                    break;
                case 'length':
                    cmp = (a.end - a.start) - (b.end - b.start);
                    break;
            }
            return sortAsc ? cmp : -cmp;
        });

        return result;
    }, [features, searchQuery, filterType, sortBy, sortAsc]);

    // Get unique types
    const usedTypes = useMemo(() =>
        [...new Set(features.map(f => f.type))].sort(),
        [features]
    );

    // Use selection to populate range
    const useSelection = () => {
        if (!selection) return;
        setNewFeatureStart(selection.start + 1);
        setNewFeatureEnd(selection.end);
    };

    // Add feature
    const addFeature = () => {
        if (!newFeatureName || !newFeatureStart || !newFeatureEnd) return;

        const feature: Feature = {
            id: `feature_${Date.now()}`,
            name: newFeatureName,
            type: newFeatureType,
            start: Number(newFeatureStart) - 1,
            end: Number(newFeatureEnd),
            strand: newFeatureStrand,
            color: newFeatureColor,
            description: newFeatureDescription || undefined
        };

        onAddFeature(feature);
        setNewFeatureName('');
        setNewFeatureStart('');
        setNewFeatureEnd('');
        setNewFeatureDescription('');
    };

    // Start editing
    const startEdit = (feature: Feature) => {
        setEditingFeature(feature.id);
        setEditName(feature.name);
        setEditStart(feature.start + 1);
        setEditEnd(feature.end);
        setEditStrand(feature.strand);
        setEditColor(feature.color || '#6b7280');
    };

    // Save edit
    const saveEdit = (feature: Feature) => {
        if (onUpdateFeature) {
            onUpdateFeature({
                ...feature,
                name: editName,
                start: editStart - 1,
                end: editEnd,
                strand: editStrand,
                color: editColor
            });
        }
        setEditingFeature(null);
    };

    // Toggle selection
    const toggleSelection = (id: string) => {
        setSelectedFeatures(prev => {
            const next = new Set(prev);
            if (next.has(id)) {
                next.delete(id);
            } else {
                next.add(id);
            }
            return next;
        });
    };

    // Select all visible
    const selectAll = () => {
        setSelectedFeatures(new Set(processedFeatures.map(f => f.id)));
    };

    // Clear selection
    const clearSelection = () => {
        setSelectedFeatures(new Set());
    };

    // Delete selected
    const deleteSelected = () => {
        selectedFeatures.forEach(id => onRemoveFeature(id));
        setSelectedFeatures(new Set());
    };

    // Highlight feature
    const highlightFeature = useCallback((feature: Feature | null) => {
        if (!feature) {
            onHighlight([]);
        } else {
            onHighlight([{
                start: feature.start,
                end: feature.end,
                color: feature.color || '#3b82f6',
                label: feature.name
            }]);
        }
    }, [onHighlight]);

    // Jump to feature
    const jumpToFeature = (feature: Feature) => {
        highlightFeature(feature);
        if (onJumpToPosition) {
            onJumpToPosition(feature.start);
        }
    };

    // Feature statistics
    const stats = useMemo(() => {
        const totalBp = features.reduce((sum, f) => sum + (f.end - f.start), 0);
        const coverage = sequenceData.sequence.length > 0
            ? (totalBp / sequenceData.sequence.length * 100).toFixed(1)
            : '0';
        return { count: features.length, totalBp, coverage };
    }, [features, sequenceData.sequence.length]);

    return (
        <div className="feature-panel p-3 space-y-3 text-sm">
            <div className="flex items-center justify-between">
                <h4 className="font-semibold text-slate-200">Features & Annotations</h4>
                <span className="text-xs text-slate-500">
                    {stats.count} features • {stats.coverage}% coverage
                </span>
            </div>

            {/* Selection helper */}
            {selection && selection.start !== selection.end && (
                <div className="p-2 bg-blue-900/30 border border-blue-700/50 rounded flex items-center justify-between">
                    <span className="text-xs text-blue-300">
                        Selected: {selection.start + 1}–{selection.end} ({selection.end - selection.start} bp)
                    </span>
                    <button
                        onClick={useSelection}
                        className="px-2 py-0.5 bg-blue-600 hover:bg-blue-500 rounded text-xs transition-colors"
                    >
                        Use Range
                    </button>
                </div>
            )}

            {/* Add feature form */}
            <details className="group" open>
                <summary className="cursor-pointer text-xs font-medium text-slate-400 hover:text-slate-300 flex items-center gap-1">
                    <span className="group-open:rotate-90 transition-transform">▶</span>
                    Add New Feature
                </summary>
                <div className="mt-2 space-y-2 p-2 bg-slate-800/50 rounded border border-slate-700">
                    <input
                        type="text"
                        value={newFeatureName}
                        onChange={(e) => setNewFeatureName(e.target.value)}
                        placeholder="Feature name"
                        className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs focus:border-blue-500 focus:outline-none"
                    />

                    <div className="flex gap-2">
                        <select
                            value={newFeatureType}
                            onChange={(e) => handleTypeChange(e.target.value)}
                            className="flex-1 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs focus:border-blue-500 focus:outline-none"
                        >
                            {Object.entries(
                                FEATURE_TYPES.reduce((acc, t) => {
                                    if (!acc[t.category]) acc[t.category] = [];
                                    acc[t.category].push(t);
                                    return acc;
                                }, {} as Record<string, typeof FEATURE_TYPES>)
                            ).map(([category, types]) => (
                                <optgroup key={category} label={category}>
                                    {types.map(type => (
                                        <option key={type.value} value={type.value}>{type.label}</option>
                                    ))}
                                </optgroup>
                            ))}
                        </select>

                        {/* Color picker */}
                        <div className="relative">
                            <button
                                onClick={() => setShowColorPicker(!showColorPicker)}
                                className="w-8 h-8 rounded border border-slate-600"
                                style={{ backgroundColor: newFeatureColor }}
                                title="Pick color"
                            />
                            {showColorPicker && (
                                <div className="absolute right-0 top-full mt-1 p-2 bg-slate-800 border border-slate-600 rounded shadow-lg z-10 grid grid-cols-5 gap-1">
                                    {COLOR_PALETTE.map(color => (
                                        <button
                                            key={color}
                                            onClick={() => {
                                                setNewFeatureColor(color);
                                                setShowColorPicker(false);
                                            }}
                                            className="w-5 h-5 rounded border border-slate-600 hover:scale-110 transition-transform"
                                            style={{ backgroundColor: color }}
                                        />
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="flex gap-2">
                        <input
                            type="number"
                            value={newFeatureStart}
                            onChange={(e) => setNewFeatureStart(e.target.value ? Number(e.target.value) : '')}
                            placeholder="Start"
                            min={1}
                            max={sequenceData.sequence.length}
                            className="flex-1 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs focus:border-blue-500 focus:outline-none"
                        />
                        <input
                            type="number"
                            value={newFeatureEnd}
                            onChange={(e) => setNewFeatureEnd(e.target.value ? Number(e.target.value) : '')}
                            placeholder="End"
                            min={1}
                            max={sequenceData.sequence.length}
                            className="flex-1 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs focus:border-blue-500 focus:outline-none"
                        />
                    </div>

                    <div className="flex items-center gap-4 text-xs">
                        <label className="flex items-center gap-1 text-slate-400 cursor-pointer">
                            <input
                                type="radio"
                                checked={newFeatureStrand === 1}
                                onChange={() => setNewFeatureStrand(1)}
                                className="w-3 h-3"
                            />
                            Forward (+)
                        </label>
                        <label className="flex items-center gap-1 text-slate-400 cursor-pointer">
                            <input
                                type="radio"
                                checked={newFeatureStrand === -1}
                                onChange={() => setNewFeatureStrand(-1)}
                                className="w-3 h-3"
                            />
                            Reverse (-)
                        </label>
                    </div>

                    <textarea
                        value={newFeatureDescription}
                        onChange={(e) => setNewFeatureDescription(e.target.value)}
                        placeholder="Description (optional)"
                        rows={2}
                        className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs focus:border-blue-500 focus:outline-none resize-none"
                    />

                    <button
                        onClick={addFeature}
                        disabled={!newFeatureName || !newFeatureStart || !newFeatureEnd}
                        className="w-full py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-xs font-medium transition-colors"
                    >
                        Add Feature
                    </button>
                </div>
            </details>

            {/* Search and filters */}
            <div className="space-y-2">
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="Search features..."
                        className="flex-1 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs focus:border-blue-500 focus:outline-none"
                    />
                    <select
                        value={filterType}
                        onChange={(e) => setFilterType(e.target.value)}
                        className="px-2 py-1 bg-slate-700 border border-slate-600 rounded text-xs focus:border-blue-500 focus:outline-none"
                    >
                        <option value="all">All types</option>
                        {usedTypes.map(type => (
                            <option key={type} value={type}>{type}</option>
                        ))}
                    </select>
                </div>

                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                        <span>Sort:</span>
                        {(['position', 'name', 'type', 'length'] as SortOption[]).map(opt => (
                            <button
                                key={opt}
                                onClick={() => {
                                    if (sortBy === opt) {
                                        setSortAsc(!sortAsc);
                                    } else {
                                        setSortBy(opt);
                                        setSortAsc(true);
                                    }
                                }}
                                className={`px-1.5 py-0.5 rounded transition-colors ${sortBy === opt
                                        ? 'bg-blue-600 text-white'
                                        : 'hover:bg-slate-600'
                                    }`}
                            >
                                {opt.charAt(0).toUpperCase() + opt.slice(1)}
                                {sortBy === opt && (sortAsc ? ' ↑' : ' ↓')}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            {/* Bulk actions */}
            {selectedFeatures.size > 0 && (
                <div className="flex items-center justify-between p-2 bg-amber-900/30 border border-amber-700/50 rounded">
                    <span className="text-xs text-amber-300">
                        {selectedFeatures.size} selected
                    </span>
                    <div className="flex gap-2">
                        <button
                            onClick={clearSelection}
                            className="px-2 py-0.5 bg-slate-600 hover:bg-slate-500 rounded text-xs"
                        >
                            Clear
                        </button>
                        <button
                            onClick={deleteSelected}
                            className="px-2 py-0.5 bg-red-600 hover:bg-red-500 rounded text-xs"
                        >
                            Delete Selected
                        </button>
                    </div>
                </div>
            )}

            {/* Feature list header */}
            {processedFeatures.length > 0 && (
                <div className="flex items-center justify-between text-xs text-slate-500">
                    <span>{processedFeatures.length} feature{processedFeatures.length !== 1 ? 's' : ''}</span>
                    <button
                        onClick={selectedFeatures.size === processedFeatures.length ? clearSelection : selectAll}
                        className="hover:text-slate-300"
                    >
                        {selectedFeatures.size === processedFeatures.length ? 'Deselect All' : 'Select All'}
                    </button>
                </div>
            )}

            {/* Feature list */}
            {processedFeatures.length === 0 ? (
                <div className="text-center text-slate-500 text-xs py-6">
                    {features.length === 0 ? 'No features yet' : 'No matches'}
                </div>
            ) : (
                <div className="space-y-1 max-h-64 overflow-y-auto">
                    {processedFeatures.map(feature => (
                        <div
                            key={feature.id}
                            className={`group relative rounded transition-colors ${selectedFeatures.has(feature.id)
                                    ? 'bg-blue-900/40 border border-blue-600/50'
                                    : 'bg-slate-700/30 border border-transparent hover:bg-slate-700/60'
                                }`}
                        >
                            {editingFeature === feature.id ? (
                                // Edit mode
                                <div className="p-2 space-y-2">
                                    <input
                                        type="text"
                                        value={editName}
                                        onChange={(e) => setEditName(e.target.value)}
                                        className="w-full px-2 py-1 bg-slate-800 border border-slate-600 rounded text-xs"
                                    />
                                    <div className="flex gap-2">
                                        <input
                                            type="number"
                                            value={editStart}
                                            onChange={(e) => setEditStart(Number(e.target.value))}
                                            className="flex-1 px-2 py-1 bg-slate-800 border border-slate-600 rounded text-xs"
                                        />
                                        <input
                                            type="number"
                                            value={editEnd}
                                            onChange={(e) => setEditEnd(Number(e.target.value))}
                                            className="flex-1 px-2 py-1 bg-slate-800 border border-slate-600 rounded text-xs"
                                        />
                                        <select
                                            value={editStrand}
                                            onChange={(e) => setEditStrand(Number(e.target.value) as 1 | -1)}
                                            className="px-2 py-1 bg-slate-800 border border-slate-600 rounded text-xs"
                                        >
                                            <option value={1}>+</option>
                                            <option value={-1}>-</option>
                                        </select>
                                    </div>
                                    <div className="flex gap-1">
                                        {COLOR_PALETTE.slice(0, 10).map(c => (
                                            <button
                                                key={c}
                                                onClick={() => setEditColor(c)}
                                                className={`w-4 h-4 rounded ${editColor === c ? 'ring-2 ring-white' : ''}`}
                                                style={{ backgroundColor: c }}
                                            />
                                        ))}
                                    </div>
                                    <div className="flex gap-2">
                                        <button
                                            onClick={() => saveEdit(feature)}
                                            className="flex-1 py-1 bg-green-600 hover:bg-green-500 rounded text-xs"
                                        >
                                            Save
                                        </button>
                                        <button
                                            onClick={() => setEditingFeature(null)}
                                            className="flex-1 py-1 bg-slate-600 hover:bg-slate-500 rounded text-xs"
                                        >
                                            Cancel
                                        </button>
                                    </div>
                                </div>
                            ) : (
                                // Display mode
                                <div
                                    className="flex items-center p-2 cursor-pointer"
                                    onClick={() => jumpToFeature(feature)}
                                >
                                    <input
                                        type="checkbox"
                                        checked={selectedFeatures.has(feature.id)}
                                        onChange={(e) => {
                                            e.stopPropagation();
                                            toggleSelection(feature.id);
                                        }}
                                        className="w-3 h-3 mr-2"
                                    />
                                    <span
                                        className="w-3 h-3 rounded-sm flex-shrink-0 mr-2"
                                        style={{ backgroundColor: feature.color || '#6b7280' }}
                                    />
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-1">
                                            <span className="text-xs text-slate-200 truncate font-medium">
                                                {feature.name}
                                            </span>
                                            <span className="text-[10px] text-slate-500">
                                                {feature.strand === 1 ? '→' : '←'}
                                            </span>
                                        </div>
                                        <div className="text-[10px] text-slate-500">
                                            {feature.type} • {feature.start + 1}–{feature.end} ({feature.end - feature.start} bp)
                                        </div>
                                    </div>
                                    <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                        {onUpdateFeature && (
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    startEdit(feature);
                                                }}
                                                className="p-1 hover:bg-slate-600 rounded text-slate-400"
                                                title="Edit"
                                            >
                                                ✏️
                                            </button>
                                        )}
                                        <button
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                onRemoveFeature(feature.id);
                                            }}
                                            className="p-1 hover:bg-red-600/50 rounded text-slate-400"
                                            title="Delete"
                                        >
                                            🗑️
                                        </button>
                                    </div>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
