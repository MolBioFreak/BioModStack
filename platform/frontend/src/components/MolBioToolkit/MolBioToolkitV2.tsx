/**
 * MolBioToolkit - Seqviz-based sequence editor
 * 
 * Clean rewrite replacing OVE with modern component architecture.
 */

import { useState, useEffect, useCallback } from 'react';
import { SequenceViewer, DEFAULT_VISIBILITY } from './SequenceViewer';
import { SequenceHeader } from './SequenceHeader';
import { VisibilityPanel } from './VisibilityPanel';
import { useSequenceHistory } from './hooks/useSequenceHistory';
import { useSequenceOperations } from './hooks/useSequenceOperations';
import { DigestPanel, PCRPanel, PrimerPanel, FeaturePanel } from './panels';
import type {
    SequenceData,
    VisibilityState,
    SelectionInfo,
    NucleotideSequenceListItem,
    HighlightedRegion,
    ActivePanel,
    Feature,
    Primer
} from './types';
import { EMPTY_SEQUENCE } from './types';

// ═══════════════════════════════════════════════════════════════════════════════
// SEQUENCE LIBRARY SIDEBAR
// ═══════════════════════════════════════════════════════════════════════════════

interface SequenceLibraryProps {
    sequences: NucleotideSequenceListItem[];
    selectedId: string | null;
    onSelect: (id: string) => void;
    onRefresh: () => void;
    loading: boolean;
}

function SequenceLibrary({ sequences, selectedId, onSelect, onRefresh, loading }: SequenceLibraryProps) {
    return (
        <div className="sequence-library w-64 bg-slate-900 border-r border-slate-700 flex flex-col">
            <div className="flex items-center justify-between p-3 border-b border-slate-700">
                <h3 className="font-semibold text-slate-200">Sequence Library</h3>
                <button
                    onClick={onRefresh}
                    disabled={loading}
                    className="p-1 hover:bg-slate-700 rounded transition-colors disabled:opacity-50"
                    title="Refresh"
                >
                    <svg className={`w-4 h-4 text-slate-400 ${loading ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                </button>
            </div>

            <div className="flex-1 overflow-y-auto">
                {sequences.length === 0 ? (
                    <div className="p-4 text-center text-slate-500 text-sm">
                        No sequences yet
                    </div>
                ) : (
                    sequences.map((seq) => (
                        <button
                            key={seq.id}
                            onClick={() => onSelect(seq.id)}
                            className={`w-full text-left p-3 border-b border-slate-800 hover:bg-slate-800 transition-colors ${selectedId === seq.id ? 'bg-slate-700' : ''
                                }`}
                        >
                            <div className="font-medium text-slate-200 truncate">{seq.name}</div>
                            <div className="flex items-center gap-2 mt-1 text-xs text-slate-400">
                                <span>{seq.length.toLocaleString()} bp</span>
                                <span>•</span>
                                <span className="uppercase">{seq.sequence_type}</span>
                                {seq.is_circular && (
                                    <>
                                        <span>•</span>
                                        <span className="text-emerald-400">○</span>
                                    </>
                                )}
                            </div>
                        </button>
                    ))
                )}
            </div>
        </div>
    );
}

// ═══════════════════════════════════════════════════════════════════════════════
// TOOL PANEL TABS
// ═══════════════════════════════════════════════════════════════════════════════

interface PanelTabsProps {
    active: ActivePanel;
    onChange: (panel: ActivePanel) => void;
}

const PANELS: { id: ActivePanel; label: string; icon: string }[] = [
    { id: 'digest', label: 'Digest', icon: '✂️' },
    { id: 'pcr', label: 'PCR', icon: '🔬' },
    { id: 'primers', label: 'Primers', icon: '🧬' },
    { id: 'features', label: 'Features', icon: '📍' },
    { id: 'edit', label: 'Edit', icon: '✏️' },
];

function PanelTabs({ active, onChange }: PanelTabsProps) {
    return (
        <div className="panel-tabs flex border-b border-slate-700 bg-slate-800">
            {PANELS.map(({ id, label, icon }) => (
                <button
                    key={id}
                    onClick={() => onChange(active === id ? null : id)}
                    className={`flex items-center gap-1 px-3 py-2 text-sm transition-colors ${active === id
                        ? 'bg-slate-700 text-slate-100 border-b-2 border-blue-500'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
                        }`}
                >
                    <span>{icon}</span>
                    <span>{label}</span>
                </button>
            ))}
        </div>
    );
}

// ═══════════════════════════════════════════════════════════════════════════════
// EDIT PANEL (MVP - Phase 2.5)
// ═══════════════════════════════════════════════════════════════════════════════

function EditPanel() {
    return (
        <div className="p-4 text-center text-slate-400">
            <p className="text-lg font-medium">Edit Panel</p>
            <p className="text-sm mt-2">Coming in Phase 2.5</p>
        </div>
    );
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════

export function MolBioToolkitV2() {
    // State
    const [sequences, setSequences] = useState<NucleotideSequenceListItem[]>([]);
    const [selectedSequenceId, setSelectedSequenceId] = useState<string | null>(null);
    const [visibility, setVisibility] = useState<VisibilityState>(DEFAULT_VISIBILITY);
    const [activePanel, setActivePanel] = useState<ActivePanel>(null);
    const [selection, setSelection] = useState<SelectionInfo | null>(null);
    const [highlightedRegions, setHighlightedRegions] = useState<HighlightedRegion[]>([]);
    const [isDirty, setIsDirty] = useState(false);

    // History hook for undo/redo
    const {
        sequenceData,
        set: setSequenceData,
        undo,
        redo,
        reset: resetHistory,
        canUndo,
        canRedo
    } = useSequenceHistory(EMPTY_SEQUENCE);

    // API hooks
    const {
        loading,
        error,
        listSequences,
        getSequence,
        updateSequence
    } = useSequenceOperations();

    // Load sequence library on mount
    useEffect(() => {
        loadLibrary();
    }, []);

    const loadLibrary = useCallback(async () => {
        const seqs = await listSequences();
        setSequences(seqs);
    }, [listSequences]);

    // Load selected sequence
    const loadSequence = useCallback(async (id: string) => {
        const seq = await getSequence(id);
        if (seq) {
            const converted: SequenceData = {
                name: seq.name,
                sequence: seq.sequence,
                circular: seq.is_circular,
                sequenceType: seq.sequence_type,
                features: (seq.features || []).map(f => ({
                    id: f.id || String(Math.random()),
                    name: f.name,
                    type: f.type || 'misc_feature',
                    start: f.start,
                    end: f.end,
                    strand: f.strand || 1,
                    color: f.color
                })),
                primers: seq.primers || [],
                translations: []
            };
            resetHistory(converted);
            setSelectedSequenceId(id);
            setIsDirty(false);
        }
    }, [getSequence, resetHistory]);

    // Save sequence
    const saveSequence = useCallback(async () => {
        if (!selectedSequenceId) return;

        await updateSequence(selectedSequenceId, {
            name: sequenceData.name,
            sequence: sequenceData.sequence,
            is_circular: sequenceData.circular,
            sequence_type: sequenceData.sequenceType,
            features: sequenceData.features,
            primers: sequenceData.primers
        });

        setIsDirty(false);
        loadLibrary();
    }, [selectedSequenceId, sequenceData, updateSequence, loadLibrary]);

    // Visibility toggle handler
    const handleVisibilityChange = useCallback((key: keyof VisibilityState) => {
        setVisibility(prev => ({ ...prev, [key]: !prev[key] }));
    }, []);

    // Selection handler
    const handleSelection = useCallback((sel: SelectionInfo) => {
        setSelection(sel);
    }, []);

    // Add feature handler
    const handleAddFeature = useCallback((feature: Feature) => {
        setSequenceData({
            ...sequenceData,
            features: [...sequenceData.features, feature]
        });
    }, [sequenceData, setSequenceData]);

    // Remove feature handler
    const handleRemoveFeature = useCallback((featureId: string) => {
        setSequenceData({
            ...sequenceData,
            features: sequenceData.features.filter(f => f.id !== featureId)
        });
    }, [sequenceData, setSequenceData]);

    // Add primer handler
    const handleAddPrimer = useCallback((primer: Primer) => {
        setSequenceData({
            ...sequenceData,
            primers: [...(sequenceData.primers || []), primer]
        });
    }, [sequenceData, setSequenceData]);

    // Remove primer handler
    const handleRemovePrimer = useCallback((primerId: string) => {
        setSequenceData({
            ...sequenceData,
            primers: (sequenceData.primers || []).filter(p => p.id !== primerId)
        });
    }, [sequenceData, setSequenceData]);

    // Track dirty state
    useEffect(() => {
        if (canUndo) setIsDirty(true);
    }, [canUndo]);

    // Keyboard shortcuts
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
                if (e.shiftKey) {
                    e.preventDefault();
                    redo();
                } else {
                    e.preventDefault();
                    undo();
                }
            }
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                e.preventDefault();
                saveSequence();
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [undo, redo, saveSequence]);

    return (
        <div className="molbio-toolkit h-screen flex bg-slate-900 text-slate-100">
            {/* Left: Sequence Library */}
            <SequenceLibrary
                sequences={sequences}
                selectedId={selectedSequenceId}
                onSelect={loadSequence}
                onRefresh={loadLibrary}
                loading={loading}
            />

            {/* Center: Viewer */}
            <div className="flex-1 flex flex-col min-w-0">
                <SequenceHeader
                    sequenceData={sequenceData}
                    onSave={selectedSequenceId ? saveSequence : undefined}
                    onUndo={undo}
                    onRedo={redo}
                    canUndo={canUndo}
                    canRedo={canRedo}
                    isDirty={isDirty}
                    loading={loading}
                />

                <div className="flex-1 relative">
                    {sequenceData.sequence ? (
                        <SequenceViewer
                            sequenceData={sequenceData}
                            visibility={visibility}
                            onSelection={handleSelection}
                            highlightedRegions={highlightedRegions}
                        />
                    ) : (
                        <div className="flex items-center justify-center h-full text-slate-500">
                            <div className="text-center">
                                <svg className="w-16 h-16 mx-auto mb-4 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                </svg>
                                <p className="text-lg">Select a sequence from the library</p>
                                <p className="text-sm mt-1">or import a new file</p>
                            </div>
                        </div>
                    )}
                </div>

                {/* Selection info bar */}
                {selection && (
                    <div className="px-4 py-1 bg-slate-800 border-t border-slate-700 text-sm text-slate-400">
                        Selected: {selection.start + 1} - {selection.end + 1} ({selection.end - selection.start + 1} bp)
                    </div>
                )}
            </div>

            {/* Right: Tool Panels */}
            <div className="w-80 border-l border-slate-700 bg-slate-800 flex flex-col">
                <PanelTabs active={activePanel} onChange={setActivePanel} />

                <div className="flex-1 overflow-y-auto">
                    {activePanel === null && (
                        <VisibilityPanel
                            visibility={visibility}
                            onChange={handleVisibilityChange}
                        />
                    )}
                    {activePanel === 'digest' && (
                        <DigestPanel
                            sequenceData={sequenceData}
                            sequenceId={selectedSequenceId}
                            onHighlight={setHighlightedRegions}
                        />
                    )}
                    {activePanel === 'pcr' && (
                        <PCRPanel
                            sequenceData={sequenceData}
                            sequenceId={selectedSequenceId}
                            onHighlight={setHighlightedRegions}
                        />
                    )}
                    {activePanel === 'primers' && (
                        <PrimerPanel
                            sequenceData={sequenceData}
                            selection={selection}
                            onHighlight={setHighlightedRegions}
                            onAddPrimer={handleAddPrimer}
                            onRemovePrimer={handleRemovePrimer}
                        />
                    )}
                    {activePanel === 'features' && (
                        <FeaturePanel
                            sequenceData={sequenceData}
                            selection={selection}
                            onHighlight={setHighlightedRegions}
                            onAddFeature={handleAddFeature}
                            onRemoveFeature={handleRemoveFeature}
                        />
                    )}
                    {activePanel === 'edit' && <EditPanel />}
                </div>

                {/* Error display */}
                {error && (
                    <div className="p-3 bg-red-900/50 border-t border-red-800 text-red-300 text-sm">
                        Error: {error}
                    </div>
                )}
            </div>
        </div>
    );
}

// Default export for backwards compatibility
export default MolBioToolkitV2;
