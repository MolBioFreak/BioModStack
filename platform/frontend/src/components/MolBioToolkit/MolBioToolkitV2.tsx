/**
 * MolBioToolkit - Seqviz-based sequence editor
 * 
 * Clean rewrite replacing OVE with modern component architecture.
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { anyToJson } from '@teselagen/bio-parsers';
import { SequenceViewer, DEFAULT_VISIBILITY, type ColorPaletteName } from './SequenceViewer';
import { SequenceHeader } from './SequenceHeader';
import { VisibilityPanel } from './VisibilityPanel';
import { useSequenceHistory } from './hooks/useSequenceHistory';
import { useSequenceOperations } from './hooks/useSequenceOperations';
import { DigestPanel, PCRPanel, PrimerPanel, FeaturePanel, EditPanel, SearchPanel } from './panels';
import { AutoAnnotatePanel, type AutoAnnotateSettings } from './AutoAnnotatePanel';
import { GCContentTrack } from './GCContentTrack';
import { MolecularInputModal } from './MolecularInputModal';
import { DEMO_PLASMIDS } from './demoConstructs';
import {
    fetchPrimerTmOptions,
    type PrimerTmOptionsResponse,
    type PrimerTmSettings,
} from '../../lib/api';
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
import {
    inferSequenceTypeFromSequence,
    sequenceUnitLabel,
} from './utils/nucleotides';

// ═══════════════════════════════════════════════════════════════════════════════
// SEQUENCE LIBRARY SIDEBAR WITH IMPORT
// ═══════════════════════════════════════════════════════════════════════════════

interface SequenceLibraryProps {
    sequences: NucleotideSequenceListItem[];
    selectedId: string | null;
    onSelect: (id: string) => void;
    onRefresh: () => void;
    onOpenModal: () => void;
    onLoadDemo: (demo: SequenceData) => void;
    loading: boolean;
}

function SequenceLibrary({
    sequences,
    selectedId,
    onSelect,
    onRefresh,
    onOpenModal,
    onLoadDemo,
    loading
}: SequenceLibraryProps) {
    const [showDemos, setShowDemos] = useState(false);

    return (
        <div className="sequence-library w-64 flex-shrink-0 bg-slate-900 border-r border-slate-700 flex flex-col overflow-hidden">
            <div className="flex items-center justify-between p-3 border-b border-slate-700">
                <div>
                    <h3 className="font-semibold text-slate-200">Construct Shelf</h3>
                    <p className="text-xs text-slate-500">Recent constructs and clearly labeled synthetic demos</p>
                </div>
                <button
                    onClick={onRefresh}
                    disabled={loading}
                    className="p-1.5 hover:bg-slate-700 rounded transition-colors disabled:opacity-50"
                    title="Refresh recent constructs"
                >
                    <svg className={`w-4 h-4 text-slate-400 ${loading ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                    </svg>
                </button>
            </div>

            <div className="p-3 border-b border-slate-700 space-y-3">
                <button
                    onClick={onOpenModal}
                    className="w-full rounded-xl bg-cyan-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-cyan-500"
                >
                    Open Molecular Input
                </button>
                <div className="rounded-xl border border-slate-700 bg-slate-800/50 p-3 text-xs text-slate-400">
                    Search saved constructs, import GenBank or SnapGene files, build DNA or RNA from paste, or pull primers from the library.
                </div>
            </div>

            <div className="flex-1 overflow-y-auto">
                <div className="border-b border-slate-700">
                    <button
                        onClick={() => setShowDemos(!showDemos)}
                        className="w-full flex items-center justify-between p-2 text-xs text-slate-400 hover:bg-slate-800"
                    >
                        <span>Synthetic Demo Constructs ({DEMO_PLASMIDS.length})</span>
                        <svg className={`w-3 h-3 transition-transform ${showDemos ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                        </svg>
                    </button>
                    {showDemos && (
                        <div className="bg-slate-800/50">
                            {DEMO_PLASMIDS.map((demo, i) => (
                                <button
                                    key={i}
                                    onClick={() => onLoadDemo(demo)}
                                    className="w-full text-left p-2 pl-4 text-sm text-slate-300 hover:bg-slate-700 transition-colors"
                                >
                                    <span className="mr-2">{demo.circular ? '○' : '─'}</span>
                                    {demo.name}
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                {sequences.length === 0 ? (
                    <div className="p-4 text-center text-slate-500 text-sm">
                        <p>No recent constructs</p>
                        <p className="mt-1 text-xs">Use the molecular input modal to search or create one</p>
                    </div>
                ) : (
                    sequences.map((seq) => (
                        <button
                            key={seq.id}
                            onClick={() => onSelect(seq.id)}
                            className={`w-full text-left p-3 border-b border-slate-800 hover:bg-slate-800 transition-colors ${selectedId === seq.id ? 'bg-slate-700' : ''}`}
                        >
                            <div className="font-medium text-slate-200 truncate">{seq.name}</div>
                            <div className="flex items-center gap-2 mt-1 text-xs text-slate-400">
                                <span>{seq.length.toLocaleString()} {sequenceUnitLabel(seq.sequence_type === 'rna' ? 'rna' : 'dna')}</span>
                                <span>•</span>
                                <span className="uppercase">{seq.sequence_type}</span>
                                {seq.is_circular && (
                                    <>
                                        <span>•</span>
                                        <span className="text-emerald-400">○</span>
                                    </>
                                )}
                            </div>
                            <div className="mt-1 text-[11px] text-slate-500">
                                {seq.feature_count} features{seq.updated_at ? ` • ${new Date(seq.updated_at).toLocaleDateString()}` : ''}
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

const PANELS: { id: ActivePanel; label: string }[] = [
    { id: 'view', label: 'View' },
    { id: 'search', label: 'Find' },
    { id: 'edit', label: 'Edit' },
    { id: 'digest', label: 'Digest' },
    { id: 'pcr', label: 'PCR' },
    { id: 'primers', label: 'Primers' },
    { id: 'features', label: 'Features' },
];

const DEFAULT_DNA_TM_SETTINGS: PrimerTmSettings = {
    algorithm: 'nn_santalucia_hicks_2004',
    salt_correction: 'owczarzy_2008',
    primer_concentration_nM: 250,
    template_concentration_nM: 0,
    na_mM: 50,
    k_mM: 0,
    tris_mM: 0,
    mg_mM: 1.5,
    dntps_mM: 0.6,
    dmso_percent: 0,
    formamide_percent: 0,
    self_complementary: false,
};

function PanelTabs({ active, onChange }: PanelTabsProps) {
    return (
        <div className="panel-tabs flex flex-wrap border-b border-slate-700 bg-slate-800">
            {PANELS.map(({ id, label }) => (
                <button
                    key={id}
                    onClick={() => onChange(active === id ? 'view' : id)}
                    className={`flex items-center gap-1 px-3 py-1.5 text-xs transition-colors ${active === id
                        ? 'bg-slate-700 text-slate-100 border-b-2 border-blue-500'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-slate-700/50'
                        }`}
                >
                    <span>{label}</span>
                </button>
            ))}
        </div>
    );
}

// ═══════════════════════════════════════════════════════════════════════════════
// FEATURE COLORS
// ═══════════════════════════════════════════════════════════════════════════════

function getFeatureColor(type: string): string {
    const colors: Record<string, string> = {
        CDS: '#22c55e',
        gene: '#3b82f6',
        promoter: '#8b5cf6',
        terminator: '#ef4444',
        rep_origin: '#ec4899',
        primer_bind: '#f59e0b',
        misc_feature: '#6b7280'
    };
    return colors[type] || colors.misc_feature;
}

// ═══════════════════════════════════════════════════════════════════════════════
// ORF FINDER UTILITY
// ═══════════════════════════════════════════════════════════════════════════════

interface Translation {
    start: number;
    end: number;
    strand: 1 | -1;
    frame?: 1 | 2 | 3;
}

function reverseComplementSeq(seq: string): string {
    const complement: Record<string, string> = {
        'A': 'T', 'T': 'A', 'G': 'C', 'C': 'G',
        'a': 't', 't': 'a', 'g': 'c', 'c': 'g',
        'N': 'N', 'n': 'n',
    };
    return seq.split('').reverse().map(c => complement[c] || c).join('');
}

/**
 * Find all Open Reading Frames in a sequence.
 * Scans all 6 reading frames (3 forward, 3 reverse).
 * Returns ORFs sorted by length (longest first).
 */
function findORFs(sequence: string, minLength: number = 100): Translation[] {
    const orfs: Translation[] = [];
    const seq = sequence.toUpperCase();
    const startCodon = 'ATG';
    const stopCodons = ['TAA', 'TAG', 'TGA'];

    // Search all 6 reading frames (3 forward, 3 reverse)
    for (const strand of [1, -1] as const) {
        const workSeq = strand === 1 ? seq : reverseComplementSeq(seq);

        for (let frame = 0; frame < 3; frame++) {
            let i = frame;
            while (i < workSeq.length - 2) {
                const codon = workSeq.substring(i, i + 3);
                if (codon === startCodon) {
                    // Look for stop codon in same frame
                    for (let j = i + 3; j < workSeq.length - 2; j += 3) {
                        const testCodon = workSeq.substring(j, j + 3);
                        if (stopCodons.includes(testCodon)) {
                            const orfLen = j + 3 - i;
                            if (orfLen >= minLength) {
                                // Convert positions back to original strand coordinates
                                const start = strand === 1 ? i : seq.length - (j + 3);
                                const end = strand === 1 ? j + 3 : seq.length - i;
                                // Frame is 1, 2, or 3 (1-indexed from frame loop 0-2)
                                const frameNum = (frame + 1) as 1 | 2 | 3;
                                orfs.push({ start, end, strand, frame: frameNum });
                            }
                            break;
                        }
                    }
                }
                i += 3;
            }
        }
    }

    // Sort by length descending, limit to top 20 for performance
    return orfs
        .sort((a, b) => (b.end - b.start) - (a.end - a.start))
        .slice(0, 20);
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN COMPONENT
// ═══════════════════════════════════════════════════════════════════════════════

export function MolBioToolkitV2() {
    // State
    const [sequences, setSequences] = useState<NucleotideSequenceListItem[]>([]);
    const [selectedSequenceId, setSelectedSequenceId] = useState<string | null>(null);
    const [showInputModal, setShowInputModal] = useState(false);
    const [visibility, setVisibility] = useState<VisibilityState>(DEFAULT_VISIBILITY);
    const [activePanel, setActivePanel] = useState<ActivePanel>('view');
    const [selection, setSelection] = useState<SelectionInfo | null>(null);
    const [highlightedRegions, setHighlightedRegions] = useState<HighlightedRegion[]>([]);
    const [isDirty, setIsDirty] = useState(false);
    const [colorPalette, setColorPalette] = useState<ColorPaletteName>('classic');
    const [visibleFrames, setVisibleFrames] = useState<Set<1 | 2 | 3 | -1 | -2 | -3>>(new Set([1]));
    const [derivedTranslations, setDerivedTranslations] = useState<SequenceData['translations']>([]);

    // Enzymes currently displayed on the viewer - controlled by DigestPanel
    const [selectedEnzymes, setSelectedEnzymes] = useState<string[]>([
        // Default: Common 6-cutters for cloning
        'EcoRI', 'BamHI', 'HindIII', 'XbaI', 'SalI', 'PstI', 'SmaI', 'KpnI', 'SacI', 'XhoI',
        'NotI', 'NdeI', 'NcoI', 'BglII', 'SpeI', 'MluI', 'ApaI', 'ClaI', 'EcoRV', 'NheI',
        // Golden Gate / MoClo enzymes
        'BsaI', 'BbsI', 'SapI',
        // Other frequently used
        'AgeI', 'AscI', 'PacI', 'SfiI', 'FseI', 'PmeI'
    ]);

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
        createSequence,
        updateSequence
    } = useSequenceOperations();

    // Load sequence library on mount
    const loadLibrary = useCallback(async () => {
        const seqs = await listSequences({
            limit: 24,
            sort_by: 'updated_at',
            sort_desc: true,
        });
        setSequences(seqs);
    }, [listSequences]);

    useEffect(() => {
        loadLibrary();
    }, [loadLibrary]);

    // Auto-compute ORFs for display only. Keep them out of persisted undo history.
    useEffect(() => {
        if (sequenceData.sequence && sequenceData.sequence.length > 100) {
            setDerivedTranslations(findORFs(sequenceData.sequence, 100));
        } else {
            setDerivedTranslations([]);
        }
    }, [sequenceData.sequence]);

    const viewerSequenceData = useMemo(() => ({
        ...sequenceData,
        translations: derivedTranslations,
    }), [sequenceData, derivedTranslations]);

    // Load selected sequence
    const loadSequence = useCallback(async (id: string) => {
        const seq = await getSequence(id);
        if (seq) {
            const converted: SequenceData = {
                name: seq.name,
                description: seq.description ?? undefined,
                sequence: seq.sequence,
                circular: seq.is_circular,
                sequenceType: seq.sequence_type,
                features: (seq.features || []).map((f: Feature) => ({
                    id: f.id || String(Math.random()),
                    name: f.name,
                    type: f.type || 'misc_feature',
                    start: f.start,
                    end: f.end,
                    strand: f.strand || 1,
                    color: f.color,
                    description: f.description,
                    notes: f.notes
                })),
                primers: (seq.primers || []).map((p: Primer) => ({
                    ...p,
                    sequenceType: p.sequenceType ?? (p as Primer & { sequence_type?: 'dna' | 'rna' }).sequence_type ?? inferSequenceTypeFromSequence(p.sequence),
                    strand: p.strand === -1 ? -1 : 1,
                })),
                translations: []
            };
            resetHistory(converted);
            setSelectedSequenceId(id);
            setSelection(null);
            setHighlightedRegions([]);
            setIsDirty(false);
        }
    }, [getSequence, resetHistory]);

    // Load demo plasmid (no API, direct)
    const loadDemo = useCallback((demo: SequenceData) => {
        resetHistory(demo);
        setSelectedSequenceId(null); // Not a saved sequence
        setSelection(null);
        setHighlightedRegions([]);
        setIsDirty(false);
    }, [resetHistory]);

    // Create a new in-memory sequence from pasted text (can be saved afterward)
    const handlePasteSequence = useCallback((data: {
        name: string;
        sequence: string;
        sequenceType: 'dna' | 'rna';
        circular: boolean;
        description?: string;
    }) => {
        const newSequence: SequenceData = {
            name: data.name,
            description: data.description,
            sequence: data.sequence,
            circular: data.circular,
            sequenceType: data.sequenceType,
            features: [],
            primers: [],
            translations: []
        };

        resetHistory(newSequence);
        setSelectedSequenceId(null);
        setSelection(null);
        setHighlightedRegions([]);
        setIsDirty(true);
    }, [resetHistory]);

    const handleOpenPrimerAsConstruct = useCallback((data: {
        name: string;
        sequence: string;
        description?: string;
    }) => {
        handlePasteSequence({
            name: data.name,
            description: data.description,
            sequence: data.sequence.toUpperCase(),
            sequenceType: inferSequenceTypeFromSequence(data.sequence),
            circular: false,
        });
    }, [handlePasteSequence]);

    // Import file using Teselagen bio-parsers
    const handleImport = useCallback(async (file: File) => {
        try {
            const result = await anyToJson(file, {
                fileName: file.name,
                parseOptions: { inclusive1BasedStart: false, jsonType: 'json' }
            });
            const results = Array.isArray(result) ? result : [result];

            if (results.length === 0 || !results[0]?.parsedSequence) {
                alert('Failed to parse file. Supported formats: GenBank, FASTA, SnapGene, etc.');
                return;
            }

            const parsed = results[0].parsedSequence;
            const normalizedSequence = (parsed.sequence || '').toUpperCase();
            const inferredType = parsed.isProtein
                ? 'protein'
                : parsed.type === 'RNA'
                    ? 'rna'
                    : inferSequenceTypeFromSequence(normalizedSequence);

            if (inferredType === 'protein') {
                alert('Protein records are not supported in the molecular toolkit yet. Import a DNA or RNA construct instead.');
                return;
            }

            const sequenceData: SequenceData = {
                name: parsed.name || file.name.replace(/\.[^.]+$/, ''),
                description: parsed.description || undefined,
                sequence: normalizedSequence,
                circular: parsed.circular ?? false,
                sequenceType: inferredType,
                features: (parsed.features || []).map((f: any, i: number) => ({
                    id: f.id || `f_${i}`,
                    name: f.name || f.type || 'feature',
                    type: f.type || 'misc_feature',
                    start: f.start,
                    end: f.end,
                    strand: f.strand === -1 ? -1 : 1,
                    color: f.color || getFeatureColor(f.type || 'misc_feature'),
                    description: f.description,
                    notes: f.notes
                })),
                primers: (parsed.primers || []).map((p: any, i: number) => ({
                    id: p.id || `p_${i}`,
                    name: p.name || `Primer ${i + 1}`,
                    sequence: (p.sequence || '').toUpperCase(),
                    sequenceType: p.sequenceType || p.sequence_type || inferSequenceTypeFromSequence(p.sequence || ''),
                    start: p.start ?? 0,
                    end: p.end ?? 0,
                    strand: p.strand === -1 ? -1 : 1,
                    tm: p.tm,
                    gc_percent: p.gc_percent,
                    tm_algorithm: p.tm_algorithm,
                    tm_salt_correction: p.tm_salt_correction,
                    tm_settings: p.tm_settings,
                })),
                translations: []
            };

            console.log('Imported sequence:', sequenceData.name, 'length:', sequenceData.sequence.length);
            resetHistory(sequenceData);
            setSelectedSequenceId(null);
            setSelection(null);
            setHighlightedRegions([]);
            setIsDirty(true);
        } catch (error) {
            console.error('Import error:', error);
            alert(`Failed to parse file: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    }, [resetHistory]);

    // Save sequence
    const saveSequence = useCallback(async () => {
        if (!sequenceData.sequence.trim()) return;

        const normalizedType = sequenceData.sequenceType === 'protein' ? 'dna' : sequenceData.sequenceType;
        const payload = {
            name: sequenceData.name.trim() || 'Untitled sequence',
            description: sequenceData.description?.trim() || undefined,
            sequence: sequenceData.sequence,
            is_circular: sequenceData.circular,
            sequence_type: normalizedType,
            features: sequenceData.features,
            primers: sequenceData.primers?.map((primer) => ({
                ...primer,
                sequence_type: primer.sequenceType || inferSequenceTypeFromSequence(primer.sequence),
            }))
        };

        let saved = false;
        if (selectedSequenceId) {
            const updated = await updateSequence(selectedSequenceId, payload);
            saved = Boolean(updated);
        } else {
            const created = await createSequence(payload);
            if (created) {
                setSelectedSequenceId(created.id);
                saved = true;
            }
        }

        if (saved) {
            setIsDirty(false);
            loadLibrary();
        }
    }, [selectedSequenceId, sequenceData, updateSequence, createSequence, loadLibrary]);

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
        setIsDirty(true);
    }, [sequenceData, setSequenceData]);

    // Remove feature handler
    const handleRemoveFeature = useCallback((featureId: string) => {
        setSequenceData({
            ...sequenceData,
            features: sequenceData.features.filter(f => f.id !== featureId)
        });
        setIsDirty(true);
    }, [sequenceData, setSequenceData]);

    // Update feature handler (for inline edit)
    const handleUpdateFeature = useCallback((updatedFeature: Feature) => {
        setSequenceData({
            ...sequenceData,
            features: sequenceData.features.map(f =>
                f.id === updatedFeature.id ? updatedFeature : f
            )
        });
        setIsDirty(true);
    }, [sequenceData, setSequenceData]);

    // Add primer handler
    const handleAddPrimer = useCallback((primer: Primer) => {
        setSequenceData({
            ...sequenceData,
            primers: [...(sequenceData.primers || []), primer]
        });
        setIsDirty(true);
    }, [sequenceData, setSequenceData]);

    // Remove primer handler
    const handleRemovePrimer = useCallback((primerId: string) => {
        setSequenceData({
            ...sequenceData,
            primers: (sequenceData.primers || []).filter(p => p.id !== primerId)
        });
        setIsDirty(true);
    }, [sequenceData, setSequenceData]);

    // Auto-annotation state
    const [isAnnotating, setIsAnnotating] = useState(false);
    const [showAnnotatePanel, setShowAnnotatePanel] = useState(false);

    // View mode state (for circular view toggle)
    type ViewMode = 'linear' | 'circular' | 'both';
    const [viewMode, setViewMode] = useState<ViewMode>('both');

    // GC track visibility state
    const [showGCTrack, setShowGCTrack] = useState(true);
    const [primerTmOptions, setPrimerTmOptions] = useState<PrimerTmOptionsResponse | null>(null);
    const [primerTmSettings, setPrimerTmSettings] = useState<PrimerTmSettings>(DEFAULT_DNA_TM_SETTINGS);

    useEffect(() => {
        let cancelled = false;

        const loadPrimerTmOptions = async () => {
            try {
                const response = await fetchPrimerTmOptions();
                if (cancelled) {
                    return;
                }
                setPrimerTmOptions(response.data);
                const preferredSequenceType = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
                const supported = response.data.algorithms.some(
                    (option) =>
                        option.id === primerTmSettings.algorithm &&
                        option.sequence_types.includes(preferredSequenceType),
                );
                if (!supported) {
                    setPrimerTmSettings(response.data.defaults[preferredSequenceType]);
                }
            } catch (tmError) {
                console.error('Failed to load primer Tm options:', tmError);
            }
        };

        loadPrimerTmOptions();
        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => {
        if (!primerTmOptions) {
            return;
        }
        const preferredSequenceType = sequenceData.sequenceType === 'rna' ? 'rna' : 'dna';
        const supported = primerTmOptions.algorithms.some(
            (option) =>
                option.id === primerTmSettings.algorithm &&
                option.sequence_types.includes(preferredSequenceType),
        );
        if (!supported) {
            setPrimerTmSettings(primerTmOptions.defaults[preferredSequenceType]);
        }
    }, [primerTmOptions, primerTmSettings.algorithm, sequenceData.sequenceType]);

    // Open auto-annotate settings panel
    const handleAutoAnnotate = useCallback(() => {
        setShowAnnotatePanel(true);
    }, []);

    // Run auto-annotation with user settings
    const runAutoAnnotate = useCallback(async (settings: AutoAnnotateSettings) => {
        if (!sequenceData.sequence) return;
        if (sequenceData.sequenceType === 'rna') {
            alert('Auto-annotation currently targets plasmid-centric DNA constructs. RNA feature annotation needs a separate database pass.');
            return;
        }

        setShowAnnotatePanel(false);
        setIsAnnotating(true);

        try {
            const response = await fetch('/api/molbio/auto-annotate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sequence: sequenceData.sequence,
                    is_linear: !sequenceData.circular,
                    detailed: settings.detailed,
                    min_identity: settings.minIdentity
                })
            });

            if (!response.ok) {
                const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
                throw new Error(error.detail || `HTTP ${response.status}`);
            }

            const { features: detectedFeatures, message } = await response.json();

            // Apply fragment filter if enabled
            let filteredFeatures = detectedFeatures;
            if (settings.filterFragments) {
                filteredFeatures = detectedFeatures.filter((f: any) => !f.is_fragment);
            }

            if (filteredFeatures.length === 0) {
                alert('No features detected matching your criteria.');
                return;
            }

            // Color palette for different feature types
            const typeColors: Record<string, string> = {
                'CDS': '#22c55e',
                'gene': '#16a34a',
                'promoter': '#8b5cf6',
                'terminator': '#ef4444',
                'rep_origin': '#ec4899',
                'misc_feature': '#3b82f6',
                'primer_bind': '#f59e0b'
            };

            // Convert detected features to our Feature format
            const newFeatures: Feature[] = filteredFeatures.map((f: any, i: number) => ({
                id: `auto_${Date.now()}_${i}`,
                name: f.name,
                type: f.type,
                start: f.start,
                end: f.end,
                strand: f.strand,
                color: typeColors[f.type] || '#6b7280',
                description: f.description || undefined,
                notes: {
                    source: 'pLannotate',
                    identity_pct: Number(f.identity_pct.toFixed(1)),
                    match_length_pct: Number(f.match_length_pct.toFixed(1)),
                    database: f.database,
                    is_fragment: Boolean(f.is_fragment),
                }
            }));

            // Deduplicate: filter out features that already exist
            // A feature is considered duplicate if it has same name and overlapping position (>80% overlap)
            const existingFeatures = sequenceData.features;
            const uniqueNewFeatures = newFeatures.filter(newF => {
                return !existingFeatures.some(existingF => {
                    // Same name check (case-insensitive)
                    const sameName = existingF.name.toLowerCase() === newF.name.toLowerCase();
                    const sameType = existingF.type === newF.type;
                    if (!sameName || !sameType) return false;

                    // Calculate position overlap
                    const overlapStart = Math.max(existingF.start, newF.start);
                    const overlapEnd = Math.min(existingF.end, newF.end);
                    const overlapLength = Math.max(0, overlapEnd - overlapStart);
                    const newLength = newF.end - newF.start;
                    const existingLength = existingF.end - existingF.start;
                    const minLength = Math.min(newLength, existingLength);

                    // If >80% overlap, consider it duplicate
                    return minLength > 0 && (overlapLength / minLength) > 0.8;
                });
            });

            const skippedCount = newFeatures.length - uniqueNewFeatures.length;

            // Merge with existing features
            const mergedFeatures = [...sequenceData.features, ...uniqueNewFeatures].sort((a, b) =>
                a.start - b.start || a.end - b.end || a.name.localeCompare(b.name)
            );
            setSequenceData({
                ...sequenceData,
                features: mergedFeatures
            });
            setIsDirty(true);

            const skippedMsg = skippedCount > 0 ? ` (${skippedCount} duplicates skipped)` : '';
            alert(`Added ${uniqueNewFeatures.length} new features!${skippedMsg} ${message}`);
        } catch (error) {
            console.error('Auto-annotation failed:', error);
            alert(`Auto-annotation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        } finally {
            setIsAnnotating(false);
        }
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
        <>
            <div className="molbio-toolkit h-full w-full flex bg-slate-900 text-slate-100 overflow-hidden">
                {/* Left: Sequence Library */}
                <SequenceLibrary
                    sequences={sequences}
                    selectedId={selectedSequenceId}
                    onSelect={loadSequence}
                    onRefresh={loadLibrary}
                    onOpenModal={() => setShowInputModal(true)}
                    onLoadDemo={loadDemo}
                    loading={loading}
                />

                {/* Center: Viewer */}
                <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                    <SequenceHeader
                        sequenceData={sequenceData}
                        onSave={saveSequence}
                        onUndo={undo}
                        onRedo={redo}
                        onAutoAnnotate={handleAutoAnnotate}
                        canUndo={canUndo}
                        canRedo={canRedo}
                        isDirty={isDirty}
                        loading={loading}
                        isAnnotating={isAnnotating}
                        viewMode={viewMode}
                        onViewModeChange={setViewMode}
                        showGCTrack={showGCTrack}
                        onGCTrackToggle={() => setShowGCTrack(prev => !prev)}
                        onOpenLibrary={() => setShowInputModal(true)}
                    />


                    <div className="flex-1 overflow-hidden flex flex-col">
                        {sequenceData.sequence ? (
                            <>
                                {/* GC Content Track */}
                                {showGCTrack && (
                                    <GCContentTrack
                                        sequence={sequenceData.sequence}
                                        circular={sequenceData.circular}
                                        selectedEnzymes={selectedEnzymes}
                                        selection={selection}
                                        onSelectionChange={handleSelection}
                                        windowSize={Math.max(20, Math.min(100, Math.floor(sequenceData.sequence.length / 50)))}
                                        height={120}
                                    />
                                )}

                                {/* Sequence Viewer */}
                                <div className="flex-1 overflow-hidden">
                                    <SequenceViewer
                                        sequenceData={viewerSequenceData}
                                        visibility={visibility}
                                        selectedEnzymes={selectedEnzymes}
                                        onSelection={handleSelection}
                                        highlightedRegions={highlightedRegions}
                                        viewMode={viewMode}
                                        colorPalette={colorPalette}
                                        visibleFrames={visibleFrames}
                                    />
                                </div>
                            </>
                        ) : (
                            <div className="flex items-center justify-center h-full text-slate-500">
                                <div className="text-center">
                                    <svg className="w-16 h-16 mx-auto mb-4 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                    </svg>
                                    <p className="text-lg">Select a sequence from the library</p>
                                    <p className="text-sm mt-1">or expand "Demo Plasmids" to try one</p>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Selection info bar */}
                    {selection && (
                        <div className="px-4 py-1 bg-slate-800 border-t border-slate-700 text-sm text-slate-400 flex-shrink-0">
                            {Math.abs(selection.end - selection.start) === 0
                                ? `Cursor: ${selection.start + 1}`
                                : `Selected: ${Math.min(selection.start, selection.end) + 1} - ${Math.max(selection.start, selection.end)} (${Math.abs(selection.end - selection.start)} ${sequenceUnitLabel(sequenceData.sequenceType === 'rna' ? 'rna' : 'dna')})`}
                        </div>
                    )}
                </div>

                {/* Right: Tool Panels */}
                <div className="w-72 flex-shrink-0 border-l border-slate-700 bg-slate-800 flex flex-col overflow-hidden">
                    <PanelTabs active={activePanel} onChange={setActivePanel} />

                    <div className="flex-1 overflow-y-auto">
                        {(activePanel === 'view' || activePanel === null) && (
                            <VisibilityPanel
                                visibility={visibility}
                                onChange={handleVisibilityChange}
                                colorPalette={colorPalette}
                                onColorPaletteChange={setColorPalette}
                                visibleFrames={visibleFrames}
                                onVisibleFramesChange={setVisibleFrames}
                            />
                        )}
                        {activePanel === 'search' && (
                            <SearchPanel
                                sequenceData={sequenceData}
                                onHighlight={setHighlightedRegions}
                                onOrfsFound={setDerivedTranslations}
                            />
                        )}
                        {activePanel === 'edit' && (
                            <EditPanel
                                sequenceData={sequenceData}
                                selection={selection}
                                onSequenceChange={(newData) => {
                                    setSequenceData(newData);
                                    setIsDirty(true);
                                }}
                            />
                        )}
                        {activePanel === 'digest' && (
                            <DigestPanel
                                sequenceData={sequenceData}
                                sequenceId={selectedSequenceId}
                                selection={selection}
                                onHighlight={setHighlightedRegions}
                                selectedEnzymes={selectedEnzymes}
                                onEnzymesChange={setSelectedEnzymes}
                            />
                        )}
                        {activePanel === 'pcr' && (
                            <PCRPanel
                                sequenceData={sequenceData}
                                sequenceId={selectedSequenceId}
                                onHighlight={setHighlightedRegions}
                                tmOptions={primerTmOptions}
                                tmSettings={primerTmSettings}
                                onTmSettingsChange={setPrimerTmSettings}
                            />
                        )}
                        {activePanel === 'primers' && (
                            <PrimerPanel
                                sequenceData={sequenceData}
                                selection={selection}
                                onHighlight={setHighlightedRegions}
                                onAddPrimer={handleAddPrimer}
                                onRemovePrimer={handleRemovePrimer}
                                tmOptions={primerTmOptions}
                                tmSettings={primerTmSettings}
                                onTmSettingsChange={setPrimerTmSettings}
                            />
                        )}
                        {activePanel === 'features' && (
                            <FeaturePanel
                                sequenceData={sequenceData}
                                selection={selection}
                                onHighlight={setHighlightedRegions}
                                onAddFeature={handleAddFeature}
                                onRemoveFeature={handleRemoveFeature}
                                onUpdateFeature={handleUpdateFeature}
                            />
                        )}
                    </div>

                    {/* Error display */}
                    {error && (
                        <div className="p-3 bg-red-900/50 border-t border-red-800 text-red-300 text-sm flex-shrink-0">
                            Error: {error}
                        </div>
                    )}
                </div>
            </div>

            {/* Auto-Annotate Settings Panel */}
            <AutoAnnotatePanel
                isOpen={showAnnotatePanel}
                onClose={() => setShowAnnotatePanel(false)}
                onAnnotate={runAutoAnnotate}
                isAnnotating={isAnnotating}
                hasSequence={!!sequenceData.sequence}
                sequenceLength={sequenceData.sequence.length}
                isCircular={sequenceData.circular}
            />

            <MolecularInputModal
                isOpen={showInputModal}
                onClose={() => setShowInputModal(false)}
                onSelectSequence={loadSequence}
                onImportFile={handleImport}
                onCreateSequence={handlePasteSequence}
                onLoadDemo={loadDemo}
                onAddPrimerToCurrentSequence={handleAddPrimer}
                onOpenPrimerAsConstruct={handleOpenPrimerAsConstruct}
                hasOpenSequence={Boolean(sequenceData.sequence)}
                currentSequenceData={sequenceData.sequence ? sequenceData : null}
                demos={DEMO_PLASMIDS}
            />
        </>
    );
}

// Default export for backwards compatibility
export default MolBioToolkitV2;
