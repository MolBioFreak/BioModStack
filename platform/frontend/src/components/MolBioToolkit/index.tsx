/**
 * MolBioToolkit - Molecular Biology Toolkit
 * 
 * OVE-based sequence editor with full functionality
 * Compatible with React 19
 */

import { useState, useCallback, useMemo, useEffect } from 'react';

// Import OVE from ESM bundle (aliased via vite.config.ts)
// Import OVE directly from workspace package
import { Editor } from '@biomodstack/ove';
import { Provider } from 'react-redux';
import { store } from './store';
import { anyToJson, jsonToGenbank } from '@teselagen/bio-parsers';
// Import styles directly
import '@biomodstack/ove/style.css';
// Desert tan theme for OVE
import './ove-theme.css';
// Note: OVE CSS is loaded via link tag in index.html from /ove/ove.css

// Types for sequence data
interface SequenceFeature {
    id: string;
    name: string;
    type?: string;
    start: number;
    end: number;
    strand?: number;
    color?: string;
}

interface SequenceData {
    name: string;
    circular: boolean;
    sequence: string;
    features: SequenceFeature[];
    primers?: SequenceFeature[]; // Support primers
}

// Sample sequence for demo - pUC19 plasmid backbone snippet
const SAMPLE_SEQUENCE: SequenceData = {
    name: "Sample Plasmid",
    circular: true,
    sequence: "GAATTCGAGCTCGGTACCCGGGGATCCTCTAGAGTCGACCTGCAGGCATGCAAGCTTGGCGTAATCATGGTCATAGCTGTTTCCTGTGTGAAATTGTTATCCGCTCACAATTCCACACAACATACGAGCCGGAAGCATAAAGTGTAAAGCCTGGGGTGCCTAATGAGTGAGCTAACTCACATTAATTGCGTTGCGCTCACTGCCCGCTTTCCAGTCGGGAAACCTGTCGTGCCAGCTGCATTAATGAATCGGCCAACGCGCGGGGAGAGGCGGTTTGCGTATTGGGCGCTCTTCCGCTTCCTCGCTCACTGACTCGCTGCGCTCGGTCGTTCGGCTGCGGCGAGCGGTATCAGCTCACTCAAAGGCGGTAATACGGTTATCCACAGAATCAGGGGATAACGCAGGAAAGAACATGTGAGCAAAAGGCCAGCAAAAGGCCAGGAACCGTAAAAAGGCCGCGTTGCTGGCGTTTTTCCATAGGCTCCGCCCCCCTGACGAGCATCACAAAAATCGACGCTCAAGTCAGAGGTGGCGAAACCCGACAGGACTATAAAGATACCAGGCGTTTCCCCCTGGAAGCTCCCTCGTGCGCTCTCCTGTTCCGACCCTGCCGCTTACCGGATACCTGTCCGCCTTTCTCCCTTCGGGAAGCGTGGCGCTTTCTCATAGCTCACGCTGTAGGTATCTCAGTTCGGTGTAGGTCGTTCGCTCCAAGCTGGGCTGTGTGCACGAACCCCCCGTTCAGCCCGACCGCTGCGCCTTATCCGGTAACTATCGTCTTGAGTCCAACCCGGTAAGACACGACTTATCGCCACTGGCAGCAGCCACTGGTAACAGGATTAGCAGAGCGAGGTATGTAGGCGGTGCTACAGAGTTCTTGAAGTGGTGGCCTAACTACGGCTACACTAGAAGGACAGTATTTGGTATCTGCGCTCTGCTGAAGCCAGTTACCTTCGGAAAAAGAGTTGGTAGCTCTTGATCCGGCAAACAAACCACCGCTGGTAGCGGTGGTTTTTTTGTTTGCAAGCAGCAGATTACGCGCAGAAAAAAAGGATCTCAAGAAGATCCTTTGATCTTTTCTACGGGGTCTGACGCTCAGTGGAACGAAAACTCACGTTAAGGGATTTTGGTCATGAGATTATCAAAAAGGATCTTCACCTAGATCCTTTTAAATTAAAAATGAAGTTTTAAATCAATCTAAAGTATATATGAGTAAACTTGGTCTGACAGTTACCAATGCTTAATCAGTGAGGCACCTATCTCAGCGATCTGTCTATTTCGTTCATCCATAGTTGCCTGACTCCCCGTCGTGTAGATAACTACGATACGGGAGGGCTTACCATCTGGCCCCAGTGCTGCAATGATACCGCGAGACCCACGCTCACCGGCTCCAGATTTATCAGCAATAAACCAGCCAGCCGGAAGGGCCGAGCGCAGAAGTGGTCCTGCAACTTTATCCGCCTCCATCCAGTCTATTAATTGTTGCCGGGAAGCTAGAGTAAGTAGTTCGCCAGTTAATAGTTTGCGCAACGTTGTTGCCATTGCTACAGGCATCGTGGTGTCACGCTCGTCGTTTGGTATGGCTTCATTCAGCTCCGGTTCCCAACGATCAAGGCGAGTTACATGATCCCCCATGTTGTGCAAAAAAGCGGTTAGCTCCTTCGGTCCTCCGATCGTTGTCAGAAGTAAGTTGGCCGCAGTGTTATCACTCATGGTTATGGCAGCACTGCATAATTCTCTTACTGTCATGCCATCCGTAAGATGCTTTTCTGTGACTGGTGAGTACTCAACCAAGTCATTCTGAGAATAGTGTATGCGGCGACCGAGTTGCTCTTGCCCGGCGTCAACACGGGATAATACCGCGCCACATAGCAGAACTTTAAAAGTGCTCATCATTGGAAAACGTTCTTCGGGGCGAAAACTCTCAAGGATCTTACCGCTGTTGAGATCCAGTTCGATGTAACCCACTCGTGCACCCAACTGATCTTCAGCATCTTTTACTTTCACCAGCGTTTCTGGGTGAGCAAAAACAGGAAGGCAAAATGCCGCAAAAAAGGGAATAAGGGCGACACGGAAATGTTGAATACTCATACTCTTCCTTTTTCAATATTATTGAAGCATTTATCAGGGTTATTGTCTCATGAGCGGATACATATTTGAATGTATTTAGAAAAATAAACAAATAGGGGTTCCGCGCACATTTCCCCGAAAAGTGCCACCTGACGTCTAAGAAACCATTATTATCATGACATTAACCTATAAAAATAGGCGTATCACGAGGCCCTTTCGTCTTCAA",
    features: [
        { id: "f1", name: "lac promoter", type: "promoter", start: 0, end: 50, strand: 1, color: "#31B440" },
        { id: "f2", name: "MCS", type: "misc_feature", start: 51, end: 100, strand: 1, color: "#C6C9D1" },
        { id: "f3", name: "lacZ alpha", type: "CDS", start: 101, end: 400, strand: 1, color: "#EF6500" },
        { id: "f4", name: "ori", type: "rep_origin", start: 800, end: 1200, strand: 1, color: "#FFCC00" },
        { id: "f5", name: "AmpR", type: "CDS", start: 1500, end: 2200, strand: -1, color: "#F74F4F" },
    ],
    primers: []
};

// OVE Wrapper Component using ESM import
interface OVEWrapperProps {
    sequenceData: SequenceData;
    onSave?: (data: any) => void;
}

// Import updateEditor from OVE package
import { updateEditor } from '@biomodstack/ove';

const EDITOR_NAME = 'MolBioToolkitEditor';

// OVE Wrapper now uses the ESM-bundled BioDesigner component
function OVEWrapper({ sequenceData, onSave }: OVEWrapperProps) {
    // Transform sequence data for OVE format
    const oveSequenceData = useMemo(() => ({
        name: sequenceData.name,
        circular: sequenceData.circular,
        sequence: sequenceData.sequence,
        features: sequenceData.features.map(f => ({
            id: f.id,
            name: f.name,
            type: f.type || 'misc_feature',
            start: f.start,
            end: f.end,
            strand: f.strand || 1,
            forward: (f.strand || 1) === 1
        })),
        primers: (sequenceData.primers || []).map(p => ({
            id: p.id,
            name: p.name,
            type: p.type || 'primer_bind',
            start: p.start,
            end: p.end,
            strand: p.strand || 1,
            forward: (p.strand || 1) === 1
        }))
    }), [sequenceData]);

    // Use updateEditor to push sequence data and panel configuration into Redux store
    useEffect(() => {
        updateEditor(store, EDITOR_NAME, {
            readOnly: false, // Enable editing mode by default so users can add primers/annotations
            sequenceData: oveSequenceData,
            // Panel layout configuration - include all tools
            panelsShown: [
                [
                    { active: true, id: "circular", name: "Circular Map" },
                    { id: "digestTool", name: "Digest" },
                    { id: "pcrTool", name: "PCR" }
                ],
                [
                    { id: "sequence", name: "Sequence Map", active: true },
                    { id: "rail", name: "Linear Map" },
                    { id: "properties", name: "Properties" }
                ]
            ],
            // Enable all annotation types
            annotationsToSupport: {
                features: true,
                translations: true,
                parts: true,
                orfs: true,
                cutsites: true,
                primers: true,
                warnings: true,
                lineageAnnotations: true,
                assemblyPieces: true
            },
            // Initial visibility settings - show cutsites by default for digest
            annotationVisibility: {
                features: true,
                parts: true,
                primers: true,
                cutsites: true,
                orfs: false,
                orfTranslations: false,
                translations: true,
                axis: true,
                axisNumbers: true,
                reverseSequence: true,
                dnaColors: false,
                sequence: true,
                caret: true
            }
        });
    }, [oveSequenceData]);

    return (
        <div className="ove-editor-container w-full h-full">
            <Provider store={store}>
                <Editor
                    editorName={EDITOR_NAME}
                    showMenuBar={true}
                    onSave={onSave}
                    PropertiesProps={{
                        propertiesList: [
                            "general",
                            "features",
                            "parts",
                            "primers",
                            "translations",
                            "cutsites",
                            "orfs",
                            "genbank"
                        ]
                    }}
                    ToolBarProps={{
                        toolList: [
                            "saveTool",
                            "downloadTool",
                            "importTool",
                            "undoTool",
                            "redoTool",
                            "cutsiteTool",
                            "featureTool",
                            "partTool",
                            "primerTool",
                            "oligoTool",
                            "orfTool",
                            "editTool",
                            "findTool",
                            "alignmentTool",
                            "visibilityTool"
                        ]
                    }}
                />
            </Provider>
        </div>
    );
}

export function MolBioToolkit() {
    const [sequenceData, setSequenceData] = useState<SequenceData>(SAMPLE_SEQUENCE);
    const [isLoading, setIsLoading] = useState(false);
    const [showImportModal, setShowImportModal] = useState(false);

    // Handle file import (GenBank, FASTA, etc.)
    const handleFileImport = useCallback(async (file: File) => {
        setIsLoading(true);
        try {
            const text = await file.text();
            // Use bio-parsers to correctly parse GenBank, FASTA, etc.
            const results = await anyToJson(text, {
                fileName: file.name,
                parseOptions: {
                    inclusive1BasedStart: false, // OVE uses 0-based exclusive
                    // Map generic types to OVE-compatible versions
                    jsonType: 'json',
                }
            });

            // anyToJson returns an array of results or a single object. Take the first valid one.
            const parsedSeq = Array.isArray(results) ? results[0] : results;

            if (parsedSeq && parsedSeq.parsedSequence) {
                const seq = parsedSeq.parsedSequence;

                // Map to our local SequenceData format
                setSequenceData({
                    name: seq.name || file.name.replace(/\.[^/.]+$/, ''),
                    circular: seq.circular ?? true,
                    sequence: (seq.sequence || '').toUpperCase(),
                    features: (seq.features || []).map((f: any) => ({
                        id: f.id || Math.random().toString(36).substr(2, 9),
                        name: f.name || 'Untitled Feature',
                        type: f.type || 'misc_feature',
                        start: f.start,
                        end: f.end,
                        strand: f.strand,
                        color: f.color
                    })),
                    primers: (seq.primers || []).map((p: any) => ({
                        id: p.id || Math.random().toString(36).substr(2, 9),
                        name: p.name || 'Untitled Primer',
                        type: p.type || 'primer_bind',
                        start: p.start,
                        end: p.end,
                        strand: p.strand,
                        color: p.color
                    }))
                });

                // Also load any primers if present
                // Note: SequenceData interface might need 'primers' field update to fully support this locally
                // But OVEWrapper transforms it anyway.
            } else {
                alert('Could not parse sequence from file.');
            }
            setShowImportModal(false);
        } catch (error) {
            console.error('Failed to import file:', error);
            alert('Error parsing file. See console for details.');
        } finally {
            setIsLoading(false);
        }
    }, []);

    // Handle file drop
    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        const file = e.dataTransfer.files[0];
        if (file) {
            handleFileImport(file);
        }
    }, [handleFileImport]);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
    }, []);

    // Sequence statistics
    const stats = useMemo(() => {
        const seq = sequenceData.sequence;
        const gc = seq.replace(/[^GCgc]/g, '').length;
        return {
            length: seq.length,
            gcContent: ((gc / seq.length) * 100).toFixed(1),
        };
    }, [sequenceData.sequence]);

    return (
        <div
            className="h-screen flex flex-col bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900"
            onDrop={handleDrop}
            onDragOver={handleDragOver}
        >
            {/* Compact Header Bar */}
            <div className="flex-shrink-0 h-14 border-b border-slate-700/50 bg-slate-900/80 backdrop-blur-sm">
                <div className="h-full px-4 flex items-center justify-between">
                    {/* Left: Logo + Sequence Info */}
                    <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center shadow-lg shadow-emerald-500/20">
                            <span className="text-white font-bold text-xs">SEQ</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <span className="text-white font-semibold">{sequenceData.name}</span>
                            <span className="text-slate-500">•</span>
                            <span className="text-slate-400 text-sm">{stats.length.toLocaleString()} bp</span>
                            <span className="text-slate-500">•</span>
                            <span className="text-slate-400 text-sm">GC: {stats.gcContent}%</span>
                            <span className="text-slate-500">•</span>
                            <span className={`text-xs px-2 py-0.5 rounded-full ${sequenceData.circular
                                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
                                : 'bg-blue-500/20 text-blue-300 border border-blue-500/30'
                                }`}>
                                {sequenceData.circular ? 'Circular' : 'Linear'}
                            </span>
                        </div>
                    </div>

                    {/* Right: Actions */}
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => setShowImportModal(true)}
                            className="px-4 py-1.5 bg-slate-700/80 hover:bg-slate-600 text-white rounded-lg text-sm font-medium transition-all border border-slate-600/50 hover:border-slate-500"
                        >
                            Import
                        </button>
                        <button
                            onClick={() => {
                                // Get the current editor state from Redux store
                                const state = store.getState() as any;
                                const editorState = state.VectorEditor[EDITOR_NAME];
                                if (!editorState || !editorState.sequenceData) {
                                    alert('No sequence data to save');
                                    return;
                                }

                                try {
                                    // Convert sequence data to GenBank format
                                    // OVE sequence data matches the format expected by jsonToGenBank
                                    const genbankString = jsonToGenbank(editorState.sequenceData);

                                    // Trigger file download
                                    const blob = new Blob([genbankString], { type: 'text/plain' });
                                    const url = URL.createObjectURL(blob);
                                    const link = document.createElement('a');
                                    link.href = url;
                                    link.download = `${editorState.sequenceData.name || 'sequence'}.gb`;
                                    document.body.appendChild(link);
                                    link.click();
                                    document.body.removeChild(link);
                                    URL.revokeObjectURL(url);
                                } catch (e) {
                                    console.error('Failed to generate GenBank file:', e);
                                    alert('Failed to save file');
                                }
                            }}
                            className="px-4 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium transition-all shadow-lg shadow-emerald-600/20"
                        >
                            Save .gb
                        </button>
                    </div>
                </div>
            </div>

            {/* Main OVE Editor - Takes remaining space (80%+ of viewport) */}
            <div className="flex-1 min-h-0 overflow-hidden">
                <OVEWrapper
                    sequenceData={{
                        name: sequenceData.name,
                        circular: sequenceData.circular,
                        sequence: sequenceData.sequence,
                        features: sequenceData.features.map(f => ({
                            id: f.id,
                            name: f.name,
                            type: f.type || 'misc_feature',
                            start: f.start,
                            end: f.end,
                            strand: f.strand || 1,
                            forward: (f.strand || 1) === 1
                        }))
                    }}
                    onSave={(data: unknown) => {
                        console.log('OVE save:', data);
                    }}
                />
            </div>

            {/* Import Modal */}
            {showImportModal && (
                <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
                    <div className="bg-slate-800 rounded-2xl border border-slate-700 p-6 w-full max-w-md shadow-2xl">
                        <h2 className="text-xl font-bold text-white mb-4">Import Sequence</h2>
                        <div
                            className="border-2 border-dashed border-slate-600 rounded-xl p-8 text-center cursor-pointer hover:border-emerald-500/50 hover:bg-slate-700/30 transition-all"
                            onClick={() => document.getElementById('fileInput')?.click()}
                        >
                            <div className="w-12 h-12 mx-auto mb-3 rounded-full bg-slate-700 flex items-center justify-center">
                                <span className="text-2xl">📁</span>
                            </div>
                            <p className="text-white font-medium">Drop files here or click to browse</p>
                            <p className="text-sm text-slate-400 mt-1">
                                Supports GenBank, FASTA, SnapGene, SBOL
                            </p>
                        </div>
                        <input
                            id="fileInput"
                            type="file"
                            className="hidden"
                            accept=".gb,.gbk,.genbank,.fasta,.fa,.fna,.dna,.sbol,.txt"
                            onChange={(e) => {
                                const file = e.target.files?.[0];
                                if (file) handleFileImport(file);
                            }}
                        />
                        <div className="flex justify-end gap-3 mt-6">
                            <button
                                onClick={() => setShowImportModal(false)}
                                className="px-4 py-2 text-slate-400 hover:text-white transition-all"
                            >
                                Cancel
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Loading Overlay */}
            {isLoading && (
                <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
                    <div className="bg-slate-800 rounded-xl p-6 flex items-center gap-4">
                        <div className="w-8 h-8 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin" />
                        <span className="text-white">Loading sequence...</span>
                    </div>
                </div>
            )}
        </div>
    );
}

export default MolBioToolkit;
