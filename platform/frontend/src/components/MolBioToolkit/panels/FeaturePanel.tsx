/**
 * FeaturePanel - Add/edit sequence annotations
 */

import { useState } from 'react';
import type { SequenceData, Feature, SelectionInfo, HighlightedRegion } from '../types';

interface FeaturePanelProps {
    sequenceData: SequenceData;
    selection: SelectionInfo | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onAddFeature: (feature: Feature) => void;
    onRemoveFeature: (featureId: string) => void;
}

const FEATURE_TYPES = [
    { value: 'CDS', label: 'CDS (Coding Sequence)', color: '#22c55e' },
    { value: 'promoter', label: 'Promoter', color: '#8b5cf6' },
    { value: 'terminator', label: 'Terminator', color: '#ef4444' },
    { value: 'gene', label: 'Gene', color: '#3b82f6' },
    { value: 'primer_bind', label: 'Primer Binding Site', color: '#f59e0b' },
    { value: 'rep_origin', label: 'Origin of Replication', color: '#ec4899' },
    { value: 'misc_feature', label: 'Misc Feature', color: '#6b7280' },
    { value: 'RBS', label: 'Ribosome Binding Site', color: '#14b8a6' },
    { value: 'operator', label: 'Operator', color: '#f97316' },
    { value: 'enhancer', label: 'Enhancer', color: '#a855f7' },
];

export function FeaturePanel({
    sequenceData,
    selection,
    onHighlight,
    onAddFeature,
    onRemoveFeature
}: FeaturePanelProps) {
    const [newFeatureName, setNewFeatureName] = useState('');
    const [newFeatureType, setNewFeatureType] = useState('misc_feature');
    const [newFeatureStart, setNewFeatureStart] = useState<number | ''>('');
    const [newFeatureEnd, setNewFeatureEnd] = useState<number | ''>('');
    const [newFeatureStrand, setNewFeatureStrand] = useState<1 | -1>(1);
    const [hoveredFeatureId, setHoveredFeatureId] = useState<string | null>(null);
    const [filterType, setFilterType] = useState<string>('all');

    // Use selection to populate range
    const useSelection = () => {
        if (!selection) return;
        setNewFeatureStart(selection.start + 1);
        setNewFeatureEnd(selection.end);
    };

    // Add feature
    const addFeature = () => {
        if (!newFeatureName || !newFeatureStart || !newFeatureEnd) return;

        const typeInfo = FEATURE_TYPES.find(t => t.value === newFeatureType);

        const feature: Feature = {
            id: `feature_${Date.now()}`,
            name: newFeatureName,
            type: newFeatureType,
            start: Number(newFeatureStart) - 1, // Convert to 0-indexed
            end: Number(newFeatureEnd),
            strand: newFeatureStrand,
            color: typeInfo?.color || '#6b7280'
        };

        onAddFeature(feature);
        setNewFeatureName('');
        setNewFeatureStart('');
        setNewFeatureEnd('');
    };

    // Highlight feature
    const highlightFeature = (feature: Feature | null) => {
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
    };

    const features = sequenceData.features || [];
    const filteredFeatures = filterType === 'all'
        ? features
        : features.filter(f => f.type === filterType);

    // Get unique types in current features
    const usedTypes = [...new Set(features.map(f => f.type))];

    return (
        <div className="feature-panel p-3 space-y-4">
            <h4 className="font-semibold text-slate-200">Features & Annotations</h4>

            {/* Selection helper */}
            {selection && selection.start !== selection.end && (
                <div className="p-2 bg-slate-700/50 rounded flex items-center justify-between">
                    <span className="text-sm text-slate-300">
                        Selected: {selection.start + 1}–{selection.end}
                    </span>
                    <button
                        onClick={useSelection}
                        className="px-2 py-1 bg-blue-600 hover:bg-blue-500 rounded text-xs transition-colors"
                    >
                        Use Range
                    </button>
                </div>
            )}

            {/* Add feature form */}
            <div className="space-y-2 p-3 bg-slate-800 rounded border border-slate-700">
                <div className="text-sm font-medium text-slate-300">Add Feature</div>

                <input
                    type="text"
                    value={newFeatureName}
                    onChange={(e) => setNewFeatureName(e.target.value)}
                    placeholder="Feature name"
                    className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                />

                <select
                    value={newFeatureType}
                    onChange={(e) => setNewFeatureType(e.target.value)}
                    className="w-full px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                >
                    {FEATURE_TYPES.map(type => (
                        <option key={type.value} value={type.value}>{type.label}</option>
                    ))}
                </select>

                <div className="flex gap-2">
                    <input
                        type="number"
                        value={newFeatureStart}
                        onChange={(e) => setNewFeatureStart(e.target.value ? Number(e.target.value) : '')}
                        placeholder="Start"
                        min={1}
                        max={sequenceData.sequence.length}
                        className="flex-1 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                    />
                    <input
                        type="number"
                        value={newFeatureEnd}
                        onChange={(e) => setNewFeatureEnd(e.target.value ? Number(e.target.value) : '')}
                        placeholder="End"
                        min={1}
                        max={sequenceData.sequence.length}
                        className="flex-1 px-2 py-1 bg-slate-700 border border-slate-600 rounded text-sm focus:border-blue-500 focus:outline-none"
                    />
                </div>

                <div className="flex items-center gap-4">
                    <label className="flex items-center gap-2 text-sm text-slate-400">
                        <input
                            type="radio"
                            checked={newFeatureStrand === 1}
                            onChange={() => setNewFeatureStrand(1)}
                            className="w-3 h-3"
                        />
                        Forward (+)
                    </label>
                    <label className="flex items-center gap-2 text-sm text-slate-400">
                        <input
                            type="radio"
                            checked={newFeatureStrand === -1}
                            onChange={() => setNewFeatureStrand(-1)}
                            className="w-3 h-3"
                        />
                        Reverse (-)
                    </label>
                </div>

                <button
                    onClick={addFeature}
                    disabled={!newFeatureName || !newFeatureStart || !newFeatureEnd}
                    className="w-full py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-sm transition-colors"
                >
                    Add Feature
                </button>
            </div>

            {/* Feature list */}
            <div className="space-y-2">
                <div className="flex items-center justify-between">
                    <span className="text-sm text-slate-400">
                        Features ({filteredFeatures.length})
                    </span>
                    <select
                        value={filterType}
                        onChange={(e) => setFilterType(e.target.value)}
                        className="px-2 py-0.5 bg-slate-700 border border-slate-600 rounded text-xs focus:border-blue-500 focus:outline-none"
                    >
                        <option value="all">All types</option>
                        {usedTypes.map(type => (
                            <option key={type} value={type}>{type}</option>
                        ))}
                    </select>
                </div>

                {filteredFeatures.length === 0 ? (
                    <div className="text-center text-slate-500 text-sm py-4">
                        No features
                    </div>
                ) : (
                    <div className="space-y-1 max-h-60 overflow-y-auto">
                        {filteredFeatures.map(feature => (
                            <div
                                key={feature.id}
                                className={`flex items-center justify-between p-2 rounded transition-colors cursor-pointer ${hoveredFeatureId === feature.id ? 'bg-slate-600' : 'bg-slate-700/50'
                                    }`}
                                onMouseEnter={() => {
                                    setHoveredFeatureId(feature.id);
                                    highlightFeature(feature);
                                }}
                                onMouseLeave={() => {
                                    setHoveredFeatureId(null);
                                    highlightFeature(null);
                                }}
                            >
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center gap-2">
                                        <span
                                            className="w-3 h-3 rounded-sm flex-shrink-0"
                                            style={{ backgroundColor: feature.color || '#6b7280' }}
                                        />
                                        <span className="text-sm text-slate-200 truncate">{feature.name}</span>
                                        <span className="text-xs text-slate-500">{feature.strand === 1 ? '→' : '←'}</span>
                                    </div>
                                    <div className="text-xs text-slate-400 mt-0.5">
                                        {feature.type} • {feature.start + 1}–{feature.end} ({feature.end - feature.start} bp)
                                    </div>
                                </div>
                                <button
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        onRemoveFeature(feature.id);
                                    }}
                                    className="p-1 hover:bg-slate-500 rounded ml-2"
                                    title="Remove feature"
                                >
                                    <svg className="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                    </svg>
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
}
