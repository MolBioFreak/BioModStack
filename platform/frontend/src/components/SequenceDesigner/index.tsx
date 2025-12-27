/**
 * SequenceDesigner - BioDesigner main component
 * 
 * Custom plasmid visualization using pure React/SVG
 * Compatible with React 19
 */

import { useState, useCallback, useMemo } from 'react';

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
}

// Feature type colors
const FEATURE_COLORS: Record<string, string> = {
    promoter: '#31B440',
    CDS: '#EF6500',
    rep_origin: '#FFCC00',
    terminator: '#FF6B6B',
    misc_feature: '#C6C9D1',
    primer: '#00BFFF',
    gene: '#9B59B6',
};

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
    ]
};

// Circular Plasmid Map SVG Component
function CircularPlasmidMap({ sequenceData, size = 400 }: { sequenceData: SequenceData; size?: number }) {
    const { sequence, features } = sequenceData;
    const totalLength = sequence.length;
    const center = size / 2;
    const radius = size * 0.35;
    const featureRadius = size * 0.42;
    const labelRadius = size * 0.48;

    // Convert base position to angle (radians), starting from top (12 o'clock)
    const posToAngle = (pos: number) => ((pos / totalLength) * 2 * Math.PI) - (Math.PI / 2);

    // Calculate arc path for a feature
    const getArcPath = (start: number, end: number, r: number) => {
        const startAngle = posToAngle(start);
        const endAngle = posToAngle(end);
        const largeArc = (end - start) / totalLength > 0.5 ? 1 : 0;

        const x1 = center + r * Math.cos(startAngle);
        const y1 = center + r * Math.sin(startAngle);
        const x2 = center + r * Math.cos(endAngle);
        const y2 = center + r * Math.sin(endAngle);

        return `M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`;
    };

    // Get label position for a feature
    const getLabelPosition = (start: number, end: number) => {
        const midPos = (start + end) / 2;
        const angle = posToAngle(midPos);
        return {
            x: center + labelRadius * Math.cos(angle),
            y: center + labelRadius * Math.sin(angle),
            angle: (angle * 180 / Math.PI) + 90, // Tangent to circle
        };
    };

    return (
        <svg viewBox={`0 0 ${size} ${size}`} className="w-full h-full">
            {/* Background */}
            <circle cx={center} cy={center} r={radius + 30} fill="#1e293b" opacity="0.5" />

            {/* Main backbone circle */}
            <circle
                cx={center}
                cy={center}
                r={radius}
                fill="none"
                stroke="#64748b"
                strokeWidth={4}
            />

            {/* Features as colored arcs */}
            {features.map((feature) => {
                const color = feature.color || FEATURE_COLORS[feature.type || 'misc_feature'] || '#888';
                const label = getLabelPosition(feature.start, feature.end);
                const isLeftSide = label.x < center;

                return (
                    <g key={feature.id}>
                        {/* Feature arc */}
                        <path
                            d={getArcPath(feature.start, feature.end, featureRadius)}
                            fill="none"
                            stroke={color}
                            strokeWidth={12}
                            strokeLinecap="round"
                            className="cursor-pointer hover:opacity-80 transition-opacity"
                        />
                        {/* Feature direction arrow */}
                        {feature.strand === -1 && (
                            <path
                                d={getArcPath(feature.start, feature.end, featureRadius)}
                                fill="none"
                                stroke={color}
                                strokeWidth={12}
                                strokeDasharray="4 8"
                                strokeLinecap="round"
                                opacity={0.5}
                            />
                        )}
                        {/* Feature label */}
                        <text
                            x={label.x}
                            y={label.y}
                            fill="white"
                            fontSize={10}
                            textAnchor={isLeftSide ? "end" : "start"}
                            dominantBaseline="middle"
                            className="pointer-events-none"
                        >
                            {feature.name}
                        </text>
                    </g>
                );
            })}

            {/* Center text */}
            <text x={center} y={center - 10} fill="white" fontSize={14} textAnchor="middle" fontWeight="bold">
                {sequenceData.name}
            </text>
            <text x={center} y={center + 10} fill="#94a3b8" fontSize={12} textAnchor="middle">
                {totalLength.toLocaleString()} bp
            </text>
        </svg>
    );
}

// Linear Sequence View Component
function LinearSequenceView({ sequenceData }: { sequenceData: SequenceData }) {
    const { sequence, features } = sequenceData;
    const totalLength = sequence.length;

    return (
        <div className="relative w-full h-32 bg-slate-900/50 rounded-lg p-4 overflow-x-auto">
            {/* Sequence backbone line */}
            <div className="relative h-full min-w-[800px]">
                <div className="absolute top-1/2 left-0 right-0 h-1 bg-slate-600 transform -translate-y-1/2" />

                {/* Tick marks */}
                {[0, 0.25, 0.5, 0.75, 1].map((frac) => (
                    <div
                        key={frac}
                        className="absolute top-1/2 transform -translate-y-1/2"
                        style={{ left: `${frac * 100}%` }}
                    >
                        <div className="w-0.5 h-4 bg-slate-500" />
                        <span className="absolute top-6 left-1/2 transform -translate-x-1/2 text-xs text-slate-400">
                            {Math.round(frac * totalLength).toLocaleString()}
                        </span>
                    </div>
                ))}

                {/* Features */}
                {features.map((feature) => {
                    const left = (feature.start / totalLength) * 100;
                    const width = ((feature.end - feature.start) / totalLength) * 100;
                    const color = feature.color || FEATURE_COLORS[feature.type || 'misc_feature'] || '#888';

                    return (
                        <div
                            key={feature.id}
                            className="absolute top-1/2 transform -translate-y-1/2 h-6 rounded cursor-pointer hover:opacity-80 transition-opacity flex items-center justify-center text-xs text-white font-medium overflow-hidden"
                            style={{
                                left: `${left}%`,
                                width: `${Math.max(width, 2)}%`,
                                backgroundColor: color,
                            }}
                            title={`${feature.name} (${feature.start}..${feature.end})`}
                        >
                            {width > 5 && <span className="truncate px-1">{feature.name}</span>}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

export function SequenceDesigner() {
    const [sequenceData, setSequenceData] = useState<SequenceData>(SAMPLE_SEQUENCE);
    const [viewMode, setViewMode] = useState<'circular' | 'linear' | 'both'>('circular');
    const [isLoading, setIsLoading] = useState(false);
    const [showImportModal, setShowImportModal] = useState(false);
    const [selectedFeature, setSelectedFeature] = useState<string | null>(null);

    // Handle file import (GenBank, FASTA, etc.)
    const handleFileImport = useCallback(async (file: File) => {
        setIsLoading(true);
        try {
            const text = await file.text();
            // Basic sequence extraction - TODO: add proper GenBank/FASTA parsing
            const cleanedSeq = text.replace(/[^ATCGNatcgn]/g, '').toUpperCase();
            if (cleanedSeq.length > 0) {
                setSequenceData({
                    name: file.name.replace(/\.[^/.]+$/, ''),
                    circular: true,
                    sequence: cleanedSeq,
                    features: []
                });
            }
            setShowImportModal(false);
        } catch (error) {
            console.error('Failed to import file:', error);
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
        <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
            {/* Header */}
            <div className="border-b border-slate-700/50 bg-slate-900/50 backdrop-blur-sm">
                <div className="max-w-7xl mx-auto px-4 py-4">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-4">
                            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center">
                                <span className="text-white font-bold text-sm">SEQ</span>
                            </div>
                            <div>
                                <h1 className="text-xl font-bold text-white">BioDesigner</h1>
                                <p className="text-sm text-slate-400">
                                    {sequenceData.name} • {stats.length.toLocaleString()} bp • GC: {stats.gcContent}% •
                                    {sequenceData.circular ? ' Circular' : ' Linear'}
                                </p>
                            </div>
                        </div>

                        {/* Toolbar */}
                        <div className="flex items-center gap-2">
                            {/* View Mode Toggle */}
                            <div className="flex bg-slate-800 rounded-lg p-1">
                                <button
                                    onClick={() => setViewMode('circular')}
                                    className={`px-3 py-1.5 text-sm rounded-md transition-all ${viewMode === 'circular'
                                        ? 'bg-emerald-500/20 text-emerald-300'
                                        : 'text-slate-400 hover:text-white'
                                        }`}
                                >
                                    Circular
                                </button>
                                <button
                                    onClick={() => setViewMode('linear')}
                                    className={`px-3 py-1.5 text-sm rounded-md transition-all ${viewMode === 'linear'
                                        ? 'bg-emerald-500/20 text-emerald-300'
                                        : 'text-slate-400 hover:text-white'
                                        }`}
                                >
                                    Linear
                                </button>
                                <button
                                    onClick={() => setViewMode('both')}
                                    className={`px-3 py-1.5 text-sm rounded-md transition-all ${viewMode === 'both'
                                        ? 'bg-emerald-500/20 text-emerald-300'
                                        : 'text-slate-400 hover:text-white'
                                        }`}
                                >
                                    Both
                                </button>
                            </div>

                            {/* Action Buttons */}
                            <button
                                onClick={() => setShowImportModal(true)}
                                className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg text-sm transition-all"
                            >
                                Import
                            </button>
                            <button
                                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm transition-all"
                            >
                                Save
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            {/* Main Content */}
            <div
                className="flex-1 p-6"
                onDrop={handleDrop}
                onDragOver={handleDragOver}
            >
                <div className="max-w-7xl mx-auto">
                    {/* Sequence Viewer Grid */}
                    <div className={`grid gap-6 ${viewMode === 'both' ? 'grid-cols-2' : 'grid-cols-1'}`}>

                        {/* Circular View */}
                        {(viewMode === 'circular' || viewMode === 'both') && (
                            <div className="bg-slate-800/50 rounded-2xl border border-slate-700/50 p-6">
                                <h3 className="text-lg font-semibold text-white mb-4">Circular Map</h3>
                                <div className="aspect-square flex items-center justify-center">
                                    <CircularPlasmidMap sequenceData={sequenceData} size={400} />
                                </div>
                            </div>
                        )}

                        {/* Linear View */}
                        {(viewMode === 'linear' || viewMode === 'both') && (
                            <div className="bg-slate-800/50 rounded-2xl border border-slate-700/50 p-6">
                                <h3 className="text-lg font-semibold text-white mb-4">Linear View</h3>
                                <LinearSequenceView sequenceData={sequenceData} />
                            </div>
                        )}
                    </div>

                    {/* Features Table */}
                    <div className="mt-6 bg-slate-800/50 rounded-2xl border border-slate-700/50 p-6">
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="text-lg font-semibold text-white">Features ({sequenceData.features.length})</h3>
                            <button className="px-3 py-1.5 bg-emerald-600/20 text-emerald-300 rounded-lg text-sm hover:bg-emerald-600/30 transition-all">
                                + Add Feature
                            </button>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full text-sm">
                                <thead>
                                    <tr className="border-b border-slate-700">
                                        <th className="text-left py-3 px-4 text-slate-400 font-medium">Name</th>
                                        <th className="text-left py-3 px-4 text-slate-400 font-medium">Type</th>
                                        <th className="text-left py-3 px-4 text-slate-400 font-medium">Location</th>
                                        <th className="text-left py-3 px-4 text-slate-400 font-medium">Strand</th>
                                        <th className="text-left py-3 px-4 text-slate-400 font-medium">Length</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {sequenceData.features.map((feature) => (
                                        <tr
                                            key={feature.id}
                                            className={`border-b border-slate-700/50 hover:bg-slate-700/30 cursor-pointer ${selectedFeature === feature.id ? 'bg-slate-700/50' : ''
                                                }`}
                                            onClick={() => setSelectedFeature(feature.id)}
                                        >
                                            <td className="py-3 px-4">
                                                <div className="flex items-center gap-2">
                                                    <div
                                                        className="w-3 h-3 rounded-full"
                                                        style={{ backgroundColor: feature.color || FEATURE_COLORS[feature.type || 'misc_feature'] || '#888' }}
                                                    />
                                                    <span className="text-white">{feature.name}</span>
                                                </div>
                                            </td>
                                            <td className="py-3 px-4 text-slate-300">{feature.type || 'misc_feature'}</td>
                                            <td className="py-3 px-4 text-slate-300">{feature.start}..{feature.end}</td>
                                            <td className="py-3 px-4 text-slate-300">{feature.strand === 1 ? '+' : '-'}</td>
                                            <td className="py-3 px-4 text-slate-300">{feature.end - feature.start} bp</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    {/* Quick Actions */}
                    <div className="mt-6 grid grid-cols-4 gap-4">
                        <button className="bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700/50 rounded-xl p-4 text-left transition-all group">
                            <div className="text-white font-medium">Restriction Digest</div>
                            <div className="text-sm text-slate-400">Find enzyme sites</div>
                        </button>
                        <button className="bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700/50 rounded-xl p-4 text-left transition-all group">
                            <div className="text-white font-medium">Design Primers</div>
                            <div className="text-sm text-slate-400">PCR primer design</div>
                        </button>
                        <button className="bg-slate-800/50 hover:bg-slate-700/50 border border-slate-700/50 rounded-xl p-4 text-left transition-all group">
                            <div className="text-white font-medium">Simulate Cloning</div>
                            <div className="text-sm text-slate-400">Gibson, Golden Gate</div>
                        </button>
                        <button className="bg-gradient-to-br from-purple-600/20 to-pink-600/20 hover:from-purple-600/30 hover:to-pink-600/30 border border-purple-500/30 rounded-xl p-4 text-left transition-all group">
                            <div className="text-white font-medium">Predict Structure</div>
                            <div className="text-sm text-purple-300">Send to Boltz2/RF3</div>
                        </button>
                    </div>
                </div>
            </div>

            {/* Import Modal */}
            {showImportModal && (
                <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50">
                    <div className="bg-slate-800 rounded-2xl border border-slate-700 p-6 w-full max-w-md">
                        <h2 className="text-xl font-bold text-white mb-4">Import Sequence</h2>
                        <div
                            className="border-2 border-dashed border-slate-600 rounded-xl p-8 text-center cursor-pointer hover:border-emerald-500/50 transition-all"
                            onClick={() => document.getElementById('fileInput')?.click()}
                        >
                            <div className="text-slate-400 mb-3 text-sm">[ FILE ]</div>
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

export default SequenceDesigner;
