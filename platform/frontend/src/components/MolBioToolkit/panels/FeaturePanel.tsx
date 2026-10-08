/**
 * FeaturePanel - Feature and qualifier management.
 *
 * Adds a structured qualifier editor so imported GenBank/SnapGene metadata can
 * be inspected and edited instead of collapsing everything into name/type/color.
 */

import { useCallback, useMemo, useState } from 'react';
import type { Feature, HighlightedRegion, SelectionInfo, SequenceData } from '../types';

interface FeaturePanelProps {
    sequenceData: SequenceData;
    selection: SelectionInfo | null;
    onHighlight: (regions: HighlightedRegion[]) => void;
    onAddFeature: (feature: Feature) => void;
    onRemoveFeature: (featureId: string) => void;
    onUpdateFeature?: (feature: Feature) => void;
    onJumpToPosition?: (position: number) => void;
}

type SortOption = 'position' | 'name' | 'type' | 'length';

interface QualifierRow {
    id: string;
    key: string;
    value: string;
}

interface FeatureDraft {
    name: string;
    type: string;
    start: number | '';
    end: number | '';
    strand: 1 | -1;
    color: string;
    description: string;
    qualifiers: QualifierRow[];
}

const FEATURE_TYPES = [
    { value: 'CDS', label: 'CDS', color: '#22c55e', category: 'Coding' },
    { value: 'gene', label: 'Gene', color: '#3b82f6', category: 'Coding' },
    { value: 'exon', label: 'Exon', color: '#10b981', category: 'Coding' },
    { value: 'promoter', label: 'Promoter', color: '#8b5cf6', category: 'Regulatory' },
    { value: 'enhancer', label: 'Enhancer', color: '#a855f7', category: 'Regulatory' },
    { value: 'terminator', label: 'Terminator', color: '#ef4444', category: 'Regulatory' },
    { value: '5UTR', label: "5' UTR", color: '#06b6d4', category: 'Regulatory' },
    { value: '3UTR', label: "3' UTR", color: '#0891b2', category: 'Regulatory' },
    { value: 'rep_origin', label: 'Origin of Replication', color: '#ec4899', category: 'Replication' },
    { value: 'oriT', label: 'oriT', color: '#db2777', category: 'Replication' },
    { value: 'resistance', label: 'Resistance Marker', color: '#dc2626', category: 'Marker' },
    { value: 'reporter', label: 'Reporter', color: '#65a30d', category: 'Marker' },
    { value: 'tag', label: 'Tag', color: '#f59e0b', category: 'Marker' },
    { value: 'primer_bind', label: 'Primer Binding Site', color: '#f59e0b', category: 'Binding' },
    { value: 'protein_bind', label: 'Protein Binding Site', color: '#0ea5e9', category: 'Binding' },
    { value: 'misc_feature', label: 'Misc Feature', color: '#6b7280', category: 'Other' },
    { value: 'misc_difference', label: 'Difference / Variant', color: '#fbbf24', category: 'Other' },
    { value: 'source', label: 'Source', color: '#94a3b8', category: 'Other' },
];

const COLOR_PALETTE = [
    '#ef4444', '#f97316', '#f59e0b', '#eab308', '#84cc16',
    '#22c55e', '#10b981', '#14b8a6', '#06b6d4', '#0ea5e9',
    '#3b82f6', '#6366f1', '#8b5cf6', '#a855f7', '#d946ef',
    '#ec4899', '#f43f5e', '#64748b', '#475569', '#ffffff',
];

const COMMON_QUALIFIERS = [
    'gene',
    'product',
    'note',
    'label',
    'locus_tag',
    'gene_synonym',
    'db_xref',
    'experiment',
    'function',
    'translation',
];

function createEmptyDraft(): FeatureDraft {
    return {
        name: '',
        type: 'misc_feature',
        start: '',
        end: '',
        strand: 1,
        color: '#6b7280',
        description: '',
        qualifiers: [],
    };
}

function nextQualifierId(): string {
    return `qual_${Math.random().toString(36).slice(2, 9)}`;
}

function notesToRows(notes?: Record<string, unknown>): QualifierRow[] {
    if (!notes) return [];
    return Object.entries(notes).flatMap(([key, rawValue]) => {
        if (rawValue == null) return [];
        if (Array.isArray(rawValue)) {
            return rawValue
                .map((value) => String(value).trim())
                .filter(Boolean)
                .map((value) => ({ id: nextQualifierId(), key, value }));
        }
        const value = String(rawValue).trim();
        return value ? [{ id: nextQualifierId(), key, value }] : [];
    });
}

function rowsToNotes(rows: QualifierRow[]): Record<string, unknown> | undefined {
    const grouped = rows.reduce<Record<string, string[]>>((acc, row) => {
        const key = row.key.trim();
        const value = row.value.trim();
        if (!key || !value) return acc;
        if (!acc[key]) acc[key] = [];
        acc[key].push(value);
        return acc;
    }, {});

    const notes = Object.fromEntries(
        Object.entries(grouped).map(([key, values]) => [key, values.length === 1 ? values[0] : values]),
    );
    return Object.keys(notes).length > 0 ? notes : undefined;
}

function qualifierPreview(notes?: Record<string, unknown>): string[] {
    if (!notes) return [];
    return Object.entries(notes)
        .slice(0, 3)
        .map(([key, value]) => {
            const display = Array.isArray(value) ? value.join(' | ') : String(value);
            return `${key}: ${display}`;
        });
}

function typeColor(type: string): string {
    return FEATURE_TYPES.find((entry) => entry.value === type)?.color || '#6b7280';
}

function QualifierEditor({
    rows,
    onChange,
    label = 'Qualifiers',
}: {
    rows: QualifierRow[];
    onChange: (rows: QualifierRow[]) => void;
    label?: string;
}) {
    const addRow = (key = '') => {
        onChange([...rows, { id: nextQualifierId(), key, value: '' }]);
    };

    const updateRow = (id: string, patch: Partial<QualifierRow>) => {
        onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
    };

    const removeRow = (id: string) => {
        onChange(rows.filter((row) => row.id !== id));
    };

    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between">
                <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">{label}</div>
                <button
                    onClick={() => addRow()}
                    className="rounded px-2 py-0.5 text-[11px] text-cyan-300 transition-colors hover:bg-cyan-500/10"
                    type="button"
                >
                    + Add
                </button>
            </div>

            <div className="flex flex-wrap gap-1">
                {COMMON_QUALIFIERS.map((key) => (
                    <button
                        key={key}
                        onClick={() => addRow(key)}
                        className="rounded-full border border-slate-700 bg-slate-800 px-2 py-0.5 text-[10px] text-slate-400 transition-colors hover:border-slate-500 hover:text-slate-200"
                        type="button"
                    >
                        {key}
                    </button>
                ))}
            </div>

            {rows.length === 0 ? (
                <div className="rounded border border-dashed border-slate-700 bg-slate-900/40 px-3 py-2 text-xs text-slate-500">
                    No qualifiers set.
                </div>
            ) : (
                <div className="space-y-2">
                    {rows.map((row) => (
                        <div key={row.id} className="grid grid-cols-[minmax(0,0.9fr)_minmax(0,1.2fr)_auto] gap-2">
                            <input
                                value={row.key}
                                onChange={(event) => updateRow(row.id, { key: event.target.value })}
                                placeholder="Qualifier key"
                                className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                            />
                            <input
                                value={row.value}
                                onChange={(event) => updateRow(row.id, { value: event.target.value })}
                                placeholder="Qualifier value"
                                className="rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-xs"
                            />
                            <button
                                onClick={() => removeRow(row.id)}
                                className="rounded px-2 py-1 text-xs text-red-300 transition-colors hover:bg-red-500/10"
                                type="button"
                            >
                                Remove
                            </button>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

export function FeaturePanel({
    sequenceData,
    selection,
    onHighlight,
    onAddFeature,
    onRemoveFeature,
    onUpdateFeature,
    onJumpToPosition,
}: FeaturePanelProps) {
    const [addDraft, setAddDraft] = useState<FeatureDraft>(createEmptyDraft);
    const [searchQuery, setSearchQuery] = useState('');
    const [filterType, setFilterType] = useState<string>('all');
    const [sortBy, setSortBy] = useState<SortOption>('position');
    const [sortAsc, setSortAsc] = useState(true);
    const [selectedFeatures, setSelectedFeatures] = useState<Set<string>>(new Set());
    const [editingFeatureId, setEditingFeatureId] = useState<string | null>(null);
    const [editDraft, setEditDraft] = useState<FeatureDraft>(createEmptyDraft);

    const features = sequenceData.features || [];

    const usedTypes = useMemo(
        () => [...new Set(features.map((feature) => feature.type))].sort(),
        [features],
    );

    const stats = useMemo(() => {
        const totalBp = features.reduce((sum, feature) => sum + (feature.end - feature.start), 0);
        const coverage = sequenceData.sequence.length > 0
            ? ((totalBp / sequenceData.sequence.length) * 100).toFixed(1)
            : '0.0';
        return { count: features.length, coverage };
    }, [features, sequenceData.sequence.length]);

    const processedFeatures = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        const filtered = features.filter((feature) => {
            if (filterType !== 'all' && feature.type !== filterType) return false;
            if (!query) return true;
            const preview = qualifierPreview(feature.notes).join(' ').toLowerCase();
            return [
                feature.name,
                feature.type,
                feature.description || '',
                preview,
            ].some((value) => value.toLowerCase().includes(query));
        });

        filtered.sort((left, right) => {
            let compare = 0;
            switch (sortBy) {
                case 'name':
                    compare = left.name.localeCompare(right.name);
                    break;
                case 'type':
                    compare = left.type.localeCompare(right.type);
                    break;
                case 'length':
                    compare = (left.end - left.start) - (right.end - right.start);
                    break;
                default:
                    compare = left.start - right.start;
                    break;
            }
            return sortAsc ? compare : -compare;
        });

        return filtered;
    }, [features, filterType, searchQuery, sortAsc, sortBy]);

    const updateDraftType = (draft: FeatureDraft, type: string): FeatureDraft => ({
        ...draft,
        type,
        color: typeColor(type),
    });

    const useSelection = () => {
        if (!selection || selection.start === selection.end) return;
        setAddDraft((current) => ({
            ...current,
            start: Math.min(selection.start, selection.end) + 1,
            end: Math.max(selection.start, selection.end),
        }));
    };

    const resetAddDraft = () => {
        setAddDraft(createEmptyDraft());
    };

    const buildFeature = (draft: FeatureDraft, existingId?: string): Feature | null => {
        const start = Number(draft.start);
        const end = Number(draft.end);
        if (!draft.name.trim() || !Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
            return null;
        }

        return {
            id: existingId || `feature_${Date.now().toString(36)}`,
            name: draft.name.trim(),
            type: draft.type,
            start: start - 1,
            end,
            strand: draft.strand,
            color: draft.color,
            description: draft.description.trim() || undefined,
            notes: rowsToNotes(draft.qualifiers),
        };
    };

    const addFeature = () => {
        const feature = buildFeature(addDraft);
        if (!feature) return;
        onAddFeature(feature);
        resetAddDraft();
    };

    const startEdit = (feature: Feature) => {
        setEditingFeatureId(feature.id);
        setEditDraft({
            name: feature.name,
            type: feature.type,
            start: feature.start + 1,
            end: feature.end,
            strand: feature.strand,
            color: feature.color || typeColor(feature.type),
            description: feature.description || '',
            qualifiers: notesToRows(feature.notes),
        });
    };

    const saveEdit = (feature: Feature) => {
        if (!onUpdateFeature) return;
        const updated = buildFeature(editDraft, feature.id);
        if (!updated) return;
        onUpdateFeature(updated);
        setEditingFeatureId(null);
    };

    const toggleSelection = (id: string) => {
        setSelectedFeatures((current) => {
            const next = new Set(current);
            if (next.has(id)) {
                next.delete(id);
            } else {
                next.add(id);
            }
            return next;
        });
    };

    const clearSelection = () => setSelectedFeatures(new Set());

    const deleteSelected = () => {
        selectedFeatures.forEach((id) => onRemoveFeature(id));
        clearSelection();
    };

    const highlightFeature = useCallback((feature: Feature | null) => {
        if (!feature) {
            onHighlight([]);
            return;
        }
        onHighlight([{
            start: feature.start,
            end: feature.end,
            color: feature.color || typeColor(feature.type),
            label: feature.name,
        }]);
    }, [onHighlight]);

    const jumpToFeature = (feature: Feature) => {
        highlightFeature(feature);
        onJumpToPosition?.(feature.start);
    };

    return (
        <div className="feature-panel space-y-3 p-3 text-sm">
            <div className="flex items-center justify-between">
                <div>
                    <h4 className="font-semibold text-slate-200">Features & Qualifiers</h4>
                    <p className="mt-1 text-xs text-slate-500">
                        {stats.count} features • {stats.coverage}% annotated coverage
                    </p>
                </div>
                {selectedFeatures.size > 0 && (
                    <button
                        onClick={deleteSelected}
                        className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1 text-xs text-red-300 transition-colors hover:bg-red-500/20"
                    >
                        Delete {selectedFeatures.size}
                    </button>
                )}
            </div>

            {selection && selection.start !== selection.end && (
                <div className="flex items-center justify-between rounded-xl border border-cyan-500/20 bg-cyan-500/5 px-3 py-2 text-xs text-cyan-200">
                    <span>
                        Selection {Math.min(selection.start, selection.end) + 1}–{Math.max(selection.start, selection.end)}
                    </span>
                    <button
                        onClick={useSelection}
                        className="rounded bg-cyan-600 px-2 py-1 font-medium text-white transition-colors hover:bg-cyan-500"
                    >
                        Use Range
                    </button>
                </div>
            )}

            <details open className="rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                <summary className="cursor-pointer text-sm font-medium text-slate-200">Add Feature</summary>
                <div className="mt-3 space-y-3">
                    <input
                        value={addDraft.name}
                        onChange={(event) => setAddDraft((current) => ({ ...current, name: event.target.value }))}
                        placeholder="Feature name"
                        className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                    />

                    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                        <select
                            value={addDraft.type}
                            onChange={(event) => setAddDraft((current) => updateDraftType(current, event.target.value))}
                            className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                        >
                            {Object.entries(
                                FEATURE_TYPES.reduce<Record<string, typeof FEATURE_TYPES>>((acc, entry) => {
                                    if (!acc[entry.category]) acc[entry.category] = [];
                                    acc[entry.category].push(entry);
                                    return acc;
                                }, {}),
                            ).map(([category, entries]) => (
                                <optgroup key={category} label={category}>
                                    {entries.map((entry) => (
                                        <option key={entry.value} value={entry.value}>{entry.label}</option>
                                    ))}
                                </optgroup>
                            ))}
                        </select>
                        <div className="flex items-center gap-1 rounded border border-slate-700 bg-slate-800 px-2">
                            {COLOR_PALETTE.slice(0, 8).map((color) => (
                                <button
                                    key={color}
                                    onClick={() => setAddDraft((current) => ({ ...current, color }))}
                                    type="button"
                                    className={`h-5 w-5 rounded-full border ${addDraft.color === color ? 'border-white' : 'border-slate-700'}`}
                                    style={{ backgroundColor: color }}
                                />
                            ))}
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                        <input
                            type="number"
                            value={addDraft.start}
                            onChange={(event) => setAddDraft((current) => ({ ...current, start: event.target.value ? Number(event.target.value) : '' }))}
                            placeholder="Start"
                            min={1}
                            max={sequenceData.sequence.length}
                            className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                        />
                        <input
                            type="number"
                            value={addDraft.end}
                            onChange={(event) => setAddDraft((current) => ({ ...current, end: event.target.value ? Number(event.target.value) : '' }))}
                            placeholder="End"
                            min={1}
                            max={sequenceData.sequence.length}
                            className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                        />
                    </div>

                    <div className="flex items-center gap-4 text-xs text-slate-400">
                        <label className="flex items-center gap-2">
                            <input
                                type="radio"
                                checked={addDraft.strand === 1}
                                onChange={() => setAddDraft((current) => ({ ...current, strand: 1 }))}
                            />
                            Forward
                        </label>
                        <label className="flex items-center gap-2">
                            <input
                                type="radio"
                                checked={addDraft.strand === -1}
                                onChange={() => setAddDraft((current) => ({ ...current, strand: -1 }))}
                            />
                            Reverse
                        </label>
                    </div>

                    <textarea
                        value={addDraft.description}
                        onChange={(event) => setAddDraft((current) => ({ ...current, description: event.target.value }))}
                        rows={2}
                        placeholder="Description"
                        className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                    />

                    <QualifierEditor
                        rows={addDraft.qualifiers}
                        onChange={(qualifiers) => setAddDraft((current) => ({ ...current, qualifiers }))}
                    />

                    <div className="flex gap-2">
                        <button
                            onClick={addFeature}
                            disabled={!addDraft.name || !addDraft.start || !addDraft.end}
                            className="flex-1 rounded-lg bg-blue-600 px-3 py-2 font-medium text-white transition-colors hover:bg-blue-500 disabled:opacity-50"
                        >
                            Add Feature
                        </button>
                        <button
                            onClick={resetAddDraft}
                            type="button"
                            className="rounded-lg border border-slate-600 px-3 py-2 text-slate-300 transition-colors hover:bg-slate-800"
                        >
                            Reset
                        </button>
                    </div>
                </div>
            </details>

            <div className="space-y-2 rounded-xl border border-slate-700 bg-slate-900/50 p-3">
                <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                    <input
                        value={searchQuery}
                        onChange={(event) => setSearchQuery(event.target.value)}
                        placeholder="Search by name, type, description, or qualifier"
                        className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                    />
                    <select
                        value={filterType}
                        onChange={(event) => setFilterType(event.target.value)}
                        className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                    >
                        <option value="all">All types</option>
                        {usedTypes.map((type) => (
                            <option key={type} value={type}>{type}</option>
                        ))}
                    </select>
                </div>

                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                    <span>Sort</span>
                    {(['position', 'name', 'type', 'length'] as SortOption[]).map((option) => (
                        <button
                            key={option}
                            onClick={() => {
                                if (sortBy === option) {
                                    setSortAsc((current) => !current);
                                } else {
                                    setSortBy(option);
                                    setSortAsc(true);
                                }
                            }}
                            className={`rounded px-2 py-1 transition-colors ${sortBy === option ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
                        >
                            {option}
                            {sortBy === option ? (sortAsc ? ' ↑' : ' ↓') : ''}
                        </button>
                    ))}
                    {processedFeatures.length > 0 && (
                        <button
                            onClick={() => setSelectedFeatures(new Set(processedFeatures.map((feature) => feature.id)))}
                            className="ml-auto rounded px-2 py-1 text-slate-300 transition-colors hover:bg-slate-800"
                        >
                            Select all shown
                        </button>
                    )}
                </div>
            </div>

            {processedFeatures.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 px-3 py-8 text-center text-sm text-slate-500">
                    {features.length === 0 ? 'No features yet.' : 'No features match the current filters.'}
                </div>
            ) : (
                <div className="space-y-2 max-h-[34rem] overflow-y-auto pr-1">
                    {processedFeatures.map((feature) => {
                        const isEditing = editingFeatureId === feature.id;
                        const previews = qualifierPreview(feature.notes);
                        return (
                            <div
                                key={feature.id}
                                className={`rounded-xl border p-3 transition-colors ${selectedFeatures.has(feature.id)
                                    ? 'border-blue-500/40 bg-blue-500/5'
                                    : 'border-slate-700 bg-slate-900/40 hover:border-slate-600'
                                    }`}
                            >
                                {isEditing ? (
                                    <div className="space-y-3">
                                        <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-2">
                                            <input
                                                value={editDraft.name}
                                                onChange={(event) => setEditDraft((current) => ({ ...current, name: event.target.value }))}
                                                className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                                            />
                                            <select
                                                value={editDraft.type}
                                                onChange={(event) => setEditDraft((current) => updateDraftType(current, event.target.value))}
                                                className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                                            >
                                                {FEATURE_TYPES.map((entry) => (
                                                    <option key={entry.value} value={entry.value}>{entry.label}</option>
                                                ))}
                                            </select>
                                        </div>

                                        <div className="grid grid-cols-2 gap-2">
                                            <input
                                                type="number"
                                                value={editDraft.start}
                                                onChange={(event) => setEditDraft((current) => ({ ...current, start: event.target.value ? Number(event.target.value) : '' }))}
                                                className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                                            />
                                            <input
                                                type="number"
                                                value={editDraft.end}
                                                onChange={(event) => setEditDraft((current) => ({ ...current, end: event.target.value ? Number(event.target.value) : '' }))}
                                                className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                                            />
                                        </div>

                                        <div className="flex items-center gap-4 text-xs text-slate-400">
                                            <label className="flex items-center gap-2">
                                                <input
                                                    type="radio"
                                                    checked={editDraft.strand === 1}
                                                    onChange={() => setEditDraft((current) => ({ ...current, strand: 1 }))}
                                                />
                                                Forward
                                            </label>
                                            <label className="flex items-center gap-2">
                                                <input
                                                    type="radio"
                                                    checked={editDraft.strand === -1}
                                                    onChange={() => setEditDraft((current) => ({ ...current, strand: -1 }))}
                                                />
                                                Reverse
                                            </label>
                                            <div className="ml-auto flex items-center gap-1">
                                                {COLOR_PALETTE.slice(0, 8).map((color) => (
                                                    <button
                                                        key={color}
                                                        onClick={() => setEditDraft((current) => ({ ...current, color }))}
                                                        type="button"
                                                        className={`h-5 w-5 rounded-full border ${editDraft.color === color ? 'border-white' : 'border-slate-700'}`}
                                                        style={{ backgroundColor: color }}
                                                    />
                                                ))}
                                            </div>
                                        </div>

                                        <textarea
                                            value={editDraft.description}
                                            onChange={(event) => setEditDraft((current) => ({ ...current, description: event.target.value }))}
                                            rows={2}
                                            className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-2 text-sm"
                                        />

                                        <QualifierEditor
                                            rows={editDraft.qualifiers}
                                            onChange={(qualifiers) => setEditDraft((current) => ({ ...current, qualifiers }))}
                                        />

                                        <div className="flex items-center gap-2">
                                            <button
                                                onClick={() => saveEdit(feature)}
                                                className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
                                            >
                                                Save
                                            </button>
                                            <button
                                                onClick={() => setEditingFeatureId(null)}
                                                className="rounded-lg border border-slate-600 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-800"
                                            >
                                                Cancel
                                            </button>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="space-y-2">
                                        <div className="flex items-start gap-3">
                                            <input
                                                type="checkbox"
                                                checked={selectedFeatures.has(feature.id)}
                                                onChange={() => toggleSelection(feature.id)}
                                                className="mt-1"
                                            />
                                            <button
                                                onClick={() => jumpToFeature(feature)}
                                                onMouseEnter={() => highlightFeature(feature)}
                                                onMouseLeave={() => highlightFeature(null)}
                                                className="flex-1 text-left"
                                            >
                                                <div className="flex items-center gap-2">
                                                    <span
                                                        className="inline-block h-3 w-3 rounded-sm"
                                                        style={{ backgroundColor: feature.color || typeColor(feature.type) }}
                                                    />
                                                    <span className="font-medium text-slate-100">{feature.name}</span>
                                                    <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] uppercase text-slate-400">
                                                        {feature.type}
                                                    </span>
                                                    <span className="text-xs text-slate-500">{feature.strand === 1 ? '→' : '←'}</span>
                                                </div>
                                                <div className="mt-1 text-xs text-slate-400">
                                                    {feature.start + 1}–{feature.end} • {feature.end - feature.start} bp
                                                </div>
                                                {feature.description && (
                                                    <div className="mt-2 text-xs text-slate-300">{feature.description}</div>
                                                )}
                                                {previews.length > 0 && (
                                                    <div className="mt-2 flex flex-wrap gap-1">
                                                        {previews.map((preview) => (
                                                            <span
                                                                key={preview}
                                                                className="rounded-full border border-slate-700 bg-slate-800 px-2 py-0.5 text-[10px] text-slate-400"
                                                            >
                                                                {preview}
                                                            </span>
                                                        ))}
                                                    </div>
                                                )}
                                            </button>
                                            <div className="flex gap-1">
                                                {onUpdateFeature && (
                                                    <button
                                                        onClick={() => startEdit(feature)}
                                                        className="rounded p-1.5 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200"
                                                        title="Edit feature"
                                                    >
                                                        ✎
                                                    </button>
                                                )}
                                                <button
                                                    onClick={() => onRemoveFeature(feature.id)}
                                                    className="rounded p-1.5 text-red-300 transition-colors hover:bg-red-500/10"
                                                    title="Delete feature"
                                                >
                                                    🗑
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
