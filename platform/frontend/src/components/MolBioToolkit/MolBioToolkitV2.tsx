/**
 * MolBioToolkit - Seqviz-based sequence editor
 * 
 * Clean rewrite replacing OVE with modern component architecture.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { anyToJson } from '@teselagen/bio-parsers';
import { SequenceViewer, DEFAULT_VISIBILITY } from './SequenceViewer';
import { SequenceHeader } from './SequenceHeader';
import { VisibilityPanel } from './VisibilityPanel';
import { useSequenceHistory } from './hooks/useSequenceHistory';
import { useSequenceOperations } from './hooks/useSequenceOperations';
import { DigestPanel, PCRPanel, PrimerPanel, FeaturePanel, EditPanel, SearchPanel } from './panels';
import { AutoAnnotatePanel, type AutoAnnotateSettings } from './AutoAnnotatePanel';
import { GCContentTrack } from './GCContentTrack';
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
// DEMO PLASMIDS - For testing when no sequences exist
// ═══════════════════════════════════════════════════════════════════════════════

const DEMO_PLASMIDS: SequenceData[] = [
    {
        name: 'pUC19',
        sequence: 'TCGCGCGTTTCGGTGATGACGGTGAAAACCTCTGACACATGCAGCTCCCGGAGACGGTCACAGCTTGTCTGTAAGCGGATGCCGGGAGCAGACAAGCCCGTCAGGGCGCGTCAGCGGGTGTTGGCGGGTGTCGGGGCTGGCTTAACTATGCGGCATCAGAGCAGATTGTACTGAGAGTGCACCATATGCGGTGTGAAATACCGCACAGATGCGTAAGGAGAAAATACCGCATCAGGCGCCATTCGCCATTCAGGCTGCGCAACTGTTGGGAAGGGCGATCGGTGCGGGCCTCTTCGCTATTACGCCAGCTGGCGAAAGGGGGATGTGCTGCAAGGCGATTAAGTTGGGTAACGCCAGGGTTTTCCCAGTCACGACGTTGTAAAACGACGGCCAGTGAATTCGAGCTCGGTACCCGGGGATCCTCTAGAGTCGACCTGCAGGCATGCAAGCTTGGCGTAATCATGGTCATAGCTGTTTCCTGTGTGAAATTGTTATCCGCTCACAATTCCACACAACATACGAGCCGGAAGCATAAAGTGTAAAGCCTGGGGTGCCTAATGAGTGAGCTAACTCACATTAATTGCGTTGCGCTCACTGCCCGCTTTCCAGTCGGGAAACCTGTCGTGCCAGCTGCATTAATGAATCGGCCAACGCGCGGGGAGAGGCGGTTTGCGTATTGGGCGCTCTTCCGCTTCCTCGCTCACTGACTCGCTGCGCTCGGTCGTTCGGCTGCGGCGAGCGGTATCAGCTCACTCAAAGGCGGTAATACGGTTATCCACAGAATCAGGGGATAACGCAGGAAAGAACATGTGAGCAAAAGGCCAGCAAAAGGCCAGGAACCGTAAAAAGGCCGCGTTGCTGGCGTTTTTCCATAGGCTCCGCCCCCCTGACGAGCATCACAAAAATCGACGCTCAAGTCAGAGGTGGCGAAACCCGACAGGACTATAAAGATACCAGGCGTTTCCCCCTGGAAGCTCCCTCGTGCGCTCTCCTGTTCCGACCCTGCCGCTTACCGGATACCTGTCCGCCTTTCTCCCTTCGGGAAGCGTGGCGCTTTCTCATAGCTCACGCTGTAGGTATCTCAGTTCGGTGTAGGTCGTTCGCTCCAAGCTGGGCTGTGTGCACGAACCCCCCGTTCAGCCCGACCGCTGCGCCTTATCCGGTAACTATCGTCTTGAGTCCAACCCGGTAAGACACGACTTATCGCCACTGGCAGCAGCCACTGGTAACAGGATTAGCAGAGCGAGGTATGTAGGCGGTGCTACAGAGTTCTTGAAGTGGTGGCCTAACTACGGCTACACTAGAAGAACAGTATTTGGTATCTGCGCTCTGCTGAAGCCAGTTACCTTCGGAAAAAGAGTTGGTAGCTCTTGATCCGGCAAACAAACCACCGCTGGTAGCGGTGGTTTTTTTGTTTGCAAGCAGCAGATTACGCGCAGAAAAAAAGGATCTCAAGAAGATCCTTTGATCTTTTCTACGGGGTCTGACGCTCAGTGGAACGAAAACTCACGTTAAGGGATTTTGGTCATGAGATTATCAAAAAGGATCTTCACCTAGATCCTTTTAAATTAAAAATGAAGTTTTAAATCAATCTAAAGTATATATGAGTAAACTTGGTCTGACAGTTACCAATGCTTAATCAGTGAGGCACCTATCTCAGCGATCTGTCTATTTCGTTCATCCATAGTTGCCTGACTCCCCGTCGTGTAGATAACTACGATACGGGAGGGCTTACCATCTGGCCCCAGTGCTGCAATGATACCGCGAGACCCACGCTCACCGGCTCCAGATTTATCAGCAATAAACCAGCCAGCCGGAAGGGCCGAGCGCAGAAGTGGTCCTGCAACTTTATCCGCCTCCATCCAGTCTATTAATTGTTGCCGGGAAGCTAGAGTAAGTAGTTCGCCAGTTAATAGTTTGCGCAACGTTGTTGCCATTGCTACAGGCATCGTGGTGTCACGCTCGTCGTTTGGTATGGCTTCATTCAGCTCCGGTTCCCAACGATCAAGGCGAGTTACATGATCCCCCATGTTGTGCAAAAAAGCGGTTAGCTCCTTCGGTCCTCCGATCGTTGTCAGAAGTAAGTTGGCCGCAGTGTTATCACTCATGGTTATGGCAGCACTGCATAATTCTCTTACTGTCATGCCATCCGTAAGATGCTTTTCTGTGACTGGTGAGTACTCAACCAAGTCATTCTGAGAATAGTGTATGCGGCGACCGAGTTGCTCTTGCCCGGCGTCAATACGGGATAATACCGCGCCACATAGCAGAACTTTAAAAGTGCTCATCATTGGAAAACGTTCTTCGGGGCGAAAACTCTCAAGGATCTTACCGCTGTTGAGATCCAGTTCGATGTAACCCACTCGTGCACCCAACTGATCTTCAGCATCTTTTACTTTCACCAGCGTTTCTGGGTGAGCAAAAACAGGAAGGCAAAATGCCGCAAAAAAGGGAATAAGGGCGACACGGAAATGTTGAATACTCATACTCTTCCTTTTTCAATATTATTGAAGCATTTATCAGGGTTATTGTCTCATGAGCGGATACATATTTGAATGTATTTAGAAAAATAAACAAATAGGGGTTCCGCGCACATTTCCCCGAAAAGTGCCACCTGACGTC',
        circular: true,
        sequenceType: 'dna',
        features: [
            { id: 'f1', name: 'lac promoter', type: 'promoter', start: 430, end: 495, strand: 1, color: '#8b5cf6' },
            { id: 'f2', name: 'MCS', type: 'misc_feature', start: 496, end: 545, strand: 1, color: '#3b82f6' },
            { id: 'f3', name: 'lacZ alpha', type: 'CDS', start: 545, end: 795, strand: 1, color: '#22c55e' },
            { id: 'f4', name: 'AmpR', type: 'CDS', start: 1830, end: 2690, strand: -1, color: '#ef4444' },
            { id: 'f5', name: 'ori', type: 'rep_origin', start: 995, end: 1580, strand: 1, color: '#ec4899' }
        ],
        primers: [],
        translations: []
    },
    {
        name: 'pET-28a',
        sequence: 'ATCCGGATATATTTCTGTCTCTGAATCAGAAACATCTCGATTGAAATCCCCTGCGCCAGGAGTGTCTCCGAACTTTAATAGCAAGGTTCAGAATTTGATGCCGAAGGATTTCGATCAGCTCGCTGATGATTTTCAGCAACATGATTGGCGCTCAGACCGCCTGGCCACCGCAGGCGGTGGAGTGCAATGTCGTGCAATGCCACGCAAGCTTGTCGAGAAGTACTAGAGCCACCATGCGGTCCGGCAGATCTGAATTCGAGCTCCGTCGACAAGCTTGCGGCCGCACTCGAGCACCACCACCACCACCACTGAGATCCGGCTGCTAACAAAGCCCGAAAGGAAGCTGAGTTGGCTGCTGCCACCGCTGAGCAATAACTAGCATAACCCCTTGGGGCCTCTAAACGGGTCTTGAGGGGTTTTTTGCTGAAAGGAGGAACTATATCCGGATTGGCGAATGGGACGCGCCCTGTAGCGGCGCATTAAGCGCGGCGGGTGTGGTGGTTACGCGCAGCGTGACCGCTACACTTGCCAGCGCCCTAGCGCCCGCTCCTTTCGCTTTCTTCCCTTCCTTTCTCGCCACGTTCGCCGGCTTTCCCCGTCAAGCTCTAAATCGGGGGCTCCCTTTAGGGTTCCGATTTAGTGCTTTACGGCACCTCGACCCCAAAAAACTTGATTAGGGTGATGGTTCACGTAGTGGGCCATCGCCCTGATAGACGGTTTTTCGCCCTTTGACGTTGGAGTCCACGTTCTTTAATAGTGGACTCTTGTTCCAAACTGGAACAACACTCAACCCTATCTCGGTCTATTCTTTTGATTTATAAGGGATTTTGCCGATTTCGGCCTATTGGTTAAAAAATGAGCTGATTTAACAAAAATTTAACGCGAATTTTAACAAAATATTAACGTTTACAATTTCAGGTGGCACTTTTCGGGGAAATGTGCGCGGAACCCCTATTTGTTTATTTTTCTAAATACATTCAAATATGTATCCGCTCATGAATTAATTCTTAGAAAAACTCATCGAGCATCAAATGAAACTGCAATTTATTCATATCAGGATTATCAATACCATATTTTTGAAAAAGCCGTTTCTGTAATGAAGGAGAAAACTCACCGAGGCAGTTCCATAGGATGGCAAGATCCTGGTATCGGTCTGCGATTCCGACTCGTCCAACATCAATACAACCTATTAATTTCCCCTCGTCAAAAATAAGGTTATCAAGTGAGAAATCACCATGAGTGACGACTGAATCCGGTGAGAATGGCAAAAGTTTATGCATTTCTTTCCAGACTTGTTCAACAGGCCAGCCATTACGCTCGTCATCAAAATCACTCGCATCAACCAAACCGTTATTCATTCGTGATTGCGCCTGAGCGAGACGAAATACGCGATCGCTGTTAAAAGGACAATTACAAACAGGAATCGAATGCAACCGGCGCAGGAACACTGCCAGCGCATCAACAATATTTTCACCTGAATCAGGATATTCTTCTAATACCTGGAATGCTGTTTTCCCGGGGATCGCAGTGGTGAGTAACCATGCATCATCAGGAGTACGGATAAAATGCTTGATGGTCGGAAGAGGCATAAATTCCGTCAGCCAGTTTAGTCTGACCATCTCATCTGTAACATCATTGGCAACGCTACCTTTGCCATGTTTCAGAAACAACTCTGGCGCATCGGGCTTCCCATACAATCGATAGATTGTCGCACCTGATTGCCCGACATTATCGCGAGCCCATTTATACCCATATAAATCAGCATCCATGTTGGAATTTAATCGCGGCCTAGAGCAAGACGTTTCCCGTTGAATATGGCTCATAACACCCCTTGTATTACTGTTTATGTAAGCAGACAGTTTTATTGTTCATGACCAAAATCCCTTAACGTGAGTTTTCGTTCCACTGAGCGTCAGACCCCGTAGAAAAGATCAAAGGATCTTCTTGAGATCCTTTTTTTCTGCGCGTAATCTGCTGCTTGCAAACAAAAAAACCACCGCTACCAGCGGTGGTTTGTTTGCCGGATCAAGAGCTACCAACTCTTTTTCCGAAGGTAACTGGCTTCAGCAGAGCGCAGATACCAAATACTGTCCTTCTAGTGTAGCCGTAGTTAGGCCACCACTTCAAGAACTCTGTAGCACCGCCTACATACCTCGCTCTGCTAATCCTGTTACCAGTGGCTGCTGCCAGTGGCGATAAGTCGTGTCTTACCGGGTTGGACTCAAGACGATAGTTACCGGATAAGGCGCAGCGGTCGGGCTGAACGGGGGGTTCGTGCACACAGCCCAGCTTGGAGCGAACGACCTACACCGAACT',
        circular: true,
        sequenceType: 'dna',
        features: [
            { id: 'f1', name: 'T7 promoter', type: 'promoter', start: 205, end: 225, strand: 1, color: '#8b5cf6' },
            { id: 'f2', name: '6xHis tag', type: 'misc_feature', start: 270, end: 288, strand: 1, color: '#f59e0b' },
            { id: 'f3', name: 'T7 terminator', type: 'terminator', start: 313, end: 360, strand: 1, color: '#ef4444' },
            { id: 'f4', name: 'KanR', type: 'CDS', start: 470, end: 1280, strand: -1, color: '#22c55e' },
            { id: 'f5', name: 'ori', type: 'rep_origin', start: 1500, end: 2100, strand: 1, color: '#ec4899' }
        ],
        primers: [
            { id: 'p1', name: 'T7_Fwd', sequence: 'TAATACGACTCACTATAGGG', start: 205, end: 225, strand: 1, tm: 52.2, gc_percent: 40 },
            { id: 'p2', name: 'T7_Rev', sequence: 'GCTAGTTATTGCTCAGCGG', start: 313, end: 332, strand: -1, tm: 56.7, gc_percent: 52.6 }
        ],
        translations: []
    },
    {
        name: 'GFP Insert (Linear)',
        sequence: 'ATGAGTAAAGGAGAAGAACTTTTCACTGGAGTTGTCCCAATTCTTGTTGAATTAGATGGTGATGTTAATGGGCACAAATTTTCTGTCAGTGGAGAGGGTGAAGGTGATGCAACATACGGAAAACTTACCCTTAAATTTATTTGCACTACTGGAAAACTACCTGTTCCATGGCCAACACTTGTCACTACTTTCGGTTATGGTGTTCAATGCTTTGCGAGATACCCAGATCATATGAAACAGCATGACTTTTTCAAGAGTGCCATGCCCGAAGGTTATGTACAGGAAAGAACTATATTTTTCAAAGATGACGGGAACTACAAGACACGTGCTGAAGTCAAGTTTGAAGGTGATACCCTTGTTAATAGAATCGAGTTAAAAGGTATTGATTTTAAAGAAGATGGAAACATTCTTGGACACAAATTGGAATACAACTATAACTCACACAATGTATACATCATGGCAGACAAACAAAAGAATGGAATCAAAGTTAACTTCAAAATTAGACACAACATTGAAGATGGAAGCGTTCAACTAGCAGACCATTATCAACAAAATACTCCAATTGGCGATGGCCCTGTCCTTTTACCAGACAACCATTACCTGTCCACACAATCTGCCCTTTCGAAAGATCCCAACGAAAAGAGAGACCACATGGTCCTTCTTGAGTTTGTAACAGCTGCTGGGATTACACATGGCATGGATGAACTATACAAATAA',
        circular: false,
        sequenceType: 'dna',
        features: [
            { id: 'f1', name: 'GFP CDS', type: 'CDS', start: 0, end: 717, strand: 1, color: '#22c55e' },
            { id: 'f2', name: 'Start codon', type: 'misc_feature', start: 0, end: 3, strand: 1, color: '#3b82f6' },
            { id: 'f3', name: 'Stop codon', type: 'misc_feature', start: 714, end: 717, strand: 1, color: '#ef4444' }
        ],
        primers: [
            { id: 'p1', name: 'GFP_Fwd', sequence: 'ATGAGTAAAGGAGAAGAACTTTTC', start: 0, end: 24, strand: 1, tm: 54.3, gc_percent: 33.3 },
            { id: 'p2', name: 'GFP_Rev', sequence: 'TTATTTGTATAGTTCATCCATGCC', start: 693, end: 717, strand: -1, tm: 53.2, gc_percent: 33.3 }
        ],
        translations: []
    }
];

// ═══════════════════════════════════════════════════════════════════════════════
// SEQUENCE LIBRARY SIDEBAR WITH IMPORT
// ═══════════════════════════════════════════════════════════════════════════════

interface SequenceLibraryProps {
    sequences: NucleotideSequenceListItem[];
    selectedId: string | null;
    onSelect: (id: string) => void;
    onRefresh: () => void;
    onImport: (file: File) => void;
    onLoadDemo: (demo: SequenceData) => void;
    loading: boolean;
}

function SequenceLibrary({ sequences, selectedId, onSelect, onRefresh, onImport, onLoadDemo, loading }: SequenceLibraryProps) {
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [showDemos, setShowDemos] = useState(false);

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) {
            onImport(file);
            e.target.value = ''; // Reset for re-upload
        }
    };

    return (
        <div className="sequence-library w-64 flex-shrink-0 bg-slate-900 border-r border-slate-700 flex flex-col overflow-hidden">
            <div className="flex items-center justify-between p-3 border-b border-slate-700">
                <h3 className="font-semibold text-slate-200">Library</h3>
                <div className="flex items-center gap-1">
                    <button
                        onClick={() => fileInputRef.current?.click()}
                        className="p-1.5 hover:bg-slate-700 rounded transition-colors"
                        title="Import file (GenBank/FASTA)"
                    >
                        <svg className="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                        </svg>
                    </button>
                    <button
                        onClick={onRefresh}
                        disabled={loading}
                        className="p-1.5 hover:bg-slate-700 rounded transition-colors disabled:opacity-50"
                        title="Refresh"
                    >
                        <svg className={`w-4 h-4 text-slate-400 ${loading ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                        </svg>
                    </button>
                </div>
                <input
                    ref={fileInputRef}
                    type="file"
                    accept=".gb,.gbk,.genbank,.fasta,.fa,.fna"
                    onChange={handleFileChange}
                    className="hidden"
                />
            </div>

            <div className="flex-1 overflow-y-auto">
                {/* Demo plasmids section */}
                <div className="border-b border-slate-700">
                    <button
                        onClick={() => setShowDemos(!showDemos)}
                        className="w-full flex items-center justify-between p-2 text-xs text-slate-400 hover:bg-slate-800"
                    >
                        <span>Demo Plasmids ({DEMO_PLASMIDS.length})</span>
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

                {/* Saved sequences */}
                {sequences.length === 0 ? (
                    <div className="p-4 text-center text-slate-500 text-sm">
                        <p>No saved sequences</p>
                        <p className="mt-1 text-xs">Import a file or try demos</p>
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

const PANELS: { id: ActivePanel; label: string }[] = [
    { id: 'search', label: 'Find' },
    { id: 'edit', label: 'Edit' },
    { id: 'digest', label: 'Digest' },
    { id: 'pcr', label: 'PCR' },
    { id: 'primers', label: 'Primers' },
    { id: 'features', label: 'Features' },
];

function PanelTabs({ active, onChange }: PanelTabsProps) {
    return (
        <div className="panel-tabs flex flex-wrap border-b border-slate-700 bg-slate-800">
            {PANELS.map(({ id, label }) => (
                <button
                    key={id}
                    onClick={() => onChange(active === id ? null : id)}
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
                features: (seq.features || []).map((f: Feature) => ({
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

    // Load demo plasmid (no API, direct)
    const loadDemo = useCallback((demo: SequenceData) => {
        resetHistory(demo);
        setSelectedSequenceId(null); // Not a saved sequence
        setIsDirty(false);
    }, [resetHistory]);

    // Import file using Teselagen bio-parsers
    const handleImport = useCallback(async (file: File) => {
        try {
            // Read file content as text first (required by bio-parsers)
            const text = await file.text();

            const result = await anyToJson(text, {
                fileName: file.name,
                parseOptions: { inclusive1BasedStart: false, jsonType: 'json' }
            });
            const results = Array.isArray(result) ? result : [result];

            if (results.length === 0 || !results[0]?.parsedSequence) {
                alert('Failed to parse file. Supported formats: GenBank, FASTA, SnapGene, etc.');
                return;
            }

            const parsed = results[0].parsedSequence;
            const sequenceData: SequenceData = {
                name: parsed.name || file.name.replace(/\.[^.]+$/, ''),
                sequence: (parsed.sequence || '').toUpperCase(),
                circular: parsed.circular ?? false,
                sequenceType: 'dna',
                features: (parsed.features || []).map((f: any, i: number) => ({
                    id: f.id || `f_${i}`,
                    name: f.name || f.type || 'feature',
                    type: f.type || 'misc_feature',
                    start: f.start,
                    end: f.end,
                    strand: f.strand === -1 ? -1 : 1,
                    color: f.color || getFeatureColor(f.type || 'misc_feature')
                })),
                primers: [],
                translations: []
            };

            console.log('Imported sequence:', sequenceData.name, 'length:', sequenceData.sequence.length);
            resetHistory(sequenceData);
            setSelectedSequenceId(null);
            setIsDirty(true);
        } catch (error) {
            console.error('Import error:', error);
            alert(`Failed to parse file: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    }, [resetHistory]);

    // Save sequence
    const saveSequence = useCallback(async () => {
        if (!selectedSequenceId) return;

        await updateSequence(selectedSequenceId, {
            name: sequenceData.name,
            sequence: sequenceData.sequence,
            is_circular: sequenceData.circular,
            sequence_type: sequenceData.sequenceType === 'protein' ? 'dna' : sequenceData.sequenceType,
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

    // Auto-annotation state
    const [isAnnotating, setIsAnnotating] = useState(false);
    const [showAnnotatePanel, setShowAnnotatePanel] = useState(false);

    // View mode state (for circular view toggle)
    type ViewMode = 'linear' | 'circular' | 'both';
    const [viewMode, setViewMode] = useState<ViewMode>('both');

    // GC track visibility state
    const [showGCTrack, setShowGCTrack] = useState(true);

    // Open auto-annotate settings panel
    const handleAutoAnnotate = useCallback(() => {
        setShowAnnotatePanel(true);
    }, []);

    // Run auto-annotation with user settings
    const runAutoAnnotate = useCallback(async (settings: AutoAnnotateSettings) => {
        if (!sequenceData.sequence) return;

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
                notes: `Detected by pLannotate (${f.identity_pct.toFixed(1)}% identity)${f.is_fragment ? ' [fragment]' : ''}`
            }));

            // Merge with existing features
            setSequenceData({
                ...sequenceData,
                features: [...sequenceData.features, ...newFeatures]
            });

            alert(`Detected ${filteredFeatures.length} features! ${message}`);
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
                    onImport={handleImport}
                    onLoadDemo={loadDemo}
                    loading={loading}
                />

                {/* Center: Viewer */}
                <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
                    <SequenceHeader
                        sequenceData={sequenceData}
                        onSave={selectedSequenceId ? saveSequence : undefined}
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
                    />


                    <div className="flex-1 overflow-hidden flex flex-col">
                        {sequenceData.sequence ? (
                            <>
                                {/* GC Content Track */}
                                {showGCTrack && (
                                    <GCContentTrack
                                        sequence={sequenceData.sequence}
                                        selection={selection}
                                        windowSize={Math.max(20, Math.min(100, Math.floor(sequenceData.sequence.length / 50)))}
                                        height={120}
                                    />
                                )}

                                {/* Sequence Viewer */}
                                <div className="flex-1 overflow-hidden">
                                    <SequenceViewer
                                        sequenceData={sequenceData}
                                        visibility={visibility}
                                        selectedEnzymes={selectedEnzymes}
                                        onSelection={handleSelection}
                                        highlightedRegions={highlightedRegions}
                                        viewMode={viewMode}
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
                            Selected: {selection.start + 1} - {selection.end + 1} ({selection.end - selection.start + 1} bp)
                        </div>
                    )}
                </div>

                {/* Right: Tool Panels */}
                <div className="w-72 flex-shrink-0 border-l border-slate-700 bg-slate-800 flex flex-col overflow-hidden">
                    <PanelTabs active={activePanel} onChange={setActivePanel} />

                    <div className="flex-1 overflow-y-auto">
                        {activePanel === null && (
                            <VisibilityPanel
                                visibility={visibility}
                                onChange={handleVisibilityChange}
                            />
                        )}
                        {activePanel === 'search' && (
                            <SearchPanel
                                sequenceData={sequenceData}
                                onHighlight={setHighlightedRegions}
                                onOrfsFound={(orfs) => {
                                    setSequenceData({
                                        ...sequenceData,
                                        translations: orfs
                                    });
                                }}
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
        </>
    );
}

// Default export for backwards compatibility
export default MolBioToolkitV2;
