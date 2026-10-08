/**
 * OligoDesignerTemplate - Comprehensive Multi-Polymer Design (DNA, RNA, Protein)
 * 
 * Uses RFDpoly for de novo structure generation with Boltz-2 validation.
 * Supports: RNA aptamers, DNA aptamers, protein-DNA complexes, RNP complexes,
 * and protein-binding aptamer design with target specification.
 * 
 * RFDpoly Size Limits (experimentally validated):
 * - RNA: ≤120 nt (safe), up to 240 nt validated via cryo-EM
 * - DNA: ≤120 nt (safe), similar limits expected
 * - Protein: ≤400 AA high accuracy, up to 600 AA with reduced quality
 * 
 * Pattern follows BindCraftTemplate for consistency and comprehensiveness.
 */

import React, { useState, useMemo, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { submitJob } from '../lib/api';
import { PhysicsRefinementPanel, type PhysicsRefinementSettings, DEFAULT_SETTINGS as PHYSICS_DEFAULTS } from './PhysicsRefinementPanel';
import { TargetAntigenSelector, type SelectedTarget } from './TargetAntigenSelector';
import { AptamerBrowser, type Aptamer } from './AptamerBrowser';
import MolstarViewer from './MolstarViewer';

// ============================================================================
// Type Definitions
// ============================================================================
type DesignMode = 'rna_aptamer' | 'dna_aptamer' | 'protein_dna' | 'protein_rna' | 'protein_binding_aptamer' | 'custom';
type PolymerType = 'dna' | 'rna' | 'protein';
type QualityPreset = 'fast' | 'standard' | 'high_quality';
type DesignApproach = 'denovo' | 'scaffold';
type FilterPreset = 'default' | 'permissive' | 'strict';
type StoragePreset = 'minimal' | 'standard' | 'full_debug';

interface ChainConfig {
    id: string;
    type: PolymerType;
    length: number;
    lengthMin?: number;
    lengthMax?: number;
    useRange: boolean;
    sequence?: string;
    useSequence: boolean;
}

interface OligoDesignerTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
}

// ============================================================================
// Constants: Size Limits (from RFDpoly documentation)
// ============================================================================
const SIZE_LIMITS = {
    rna: { safe: 120, max: 240, unit: 'nucleotides' },
    dna: { safe: 120, max: 200, unit: 'nucleotides' },
    protein: { safe: 400, max: 600, unit: 'residues' },
};

// ============================================================================
// Constants: Modified Nucleotides
// ============================================================================
const MODIFIED_NUCLEOTIDES = {
    rna: ['Gm', 'Am', 'Cm', 'Um', 'Ψ', 'm6A', 'm5C', 's2U', '2OMe'],
    dna: ['5mC', '5hmC', '5fC', '5caC', '8oxoG', 'IdU'],
};

// ============================================================================
// Constants: Filter Presets
// ============================================================================
const FILTER_PRESETS: Record<FilterPreset, { label: string; description: string; min_plddt: number; min_ptm: number }> = {
    default: { label: 'Default', description: 'Balanced thresholds for general use', min_plddt: 70, min_ptm: 0.5 },
    permissive: { label: 'Permissive', description: 'Lower thresholds for experimental designs', min_plddt: 60, min_ptm: 0.4 },
    strict: { label: 'Publication-Grade', description: 'Stringent thresholds for high-confidence designs', min_plddt: 80, min_ptm: 0.7 },
};

// ============================================================================
// Constants: Quality Presets
// ============================================================================
const QUALITY_PRESETS: Record<QualityPreset, { steps: number; label: string; description: string }> = {
    fast: { steps: 25, label: 'Fast', description: '25 diffusion steps - quick exploration' },
    standard: { steps: 50, label: 'Standard', description: '50 diffusion steps - balanced quality' },
    high_quality: { steps: 100, label: 'High Quality', description: '100 diffusion steps - best results' },
};

// ============================================================================
// Constants: Storage Presets
// ============================================================================
const STORAGE_PRESETS: Record<StoragePreset, { label: string; description: string; keep_intermediates: boolean; compress_outputs: boolean }> = {
    minimal: { label: 'Minimal', description: 'Keep only final PDBs (saves disk space)', keep_intermediates: false, compress_outputs: true },
    standard: { label: 'Standard', description: 'Keep PDBs and metrics', keep_intermediates: false, compress_outputs: false },
    full_debug: { label: 'Full Debug', description: 'Keep all intermediates (large storage!)', keep_intermediates: true, compress_outputs: false },
};

// ============================================================================
// Constants: Design Mode Definitions
// ============================================================================
const DESIGN_MODE_ICONS: Record<DesignMode, React.ReactNode> = {
    rna_aptamer: (
        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
            <circle cx="12" cy="12" r="3" />
        </svg>
    ),
    dna_aptamer: (
        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M2 12h2M20 12h2M6 12a6 6 0 0112 0M6 12a6 6 0 000 0" />
            <path d="M9 8v8M15 8v8" strokeDasharray="2 2" />
        </svg>
    ),
    protein_dna: (
        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="8" width="8" height="8" rx="1" />
            <path d="M14 10h7M14 14h7" />
            <path d="M11 12h3" strokeDasharray="1 1" />
        </svg>
    ),
    protein_rna: (
        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="8" width="8" height="8" rx="1" />
            <circle cx="17" cy="12" r="4" />
            <path d="M11 12h2" strokeDasharray="1 1" />
        </svg>
    ),
    protein_binding_aptamer: (
        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="8" cy="12" r="5" />
            <path d="M13 12h2" strokeDasharray="1 1" />
            <path d="M17 8c2 0 4 1.5 4 4s-2 4-4 4" strokeLinecap="round" />
            <path d="M19 10v4" />
        </svg>
    ),
    custom: (
        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 15a3 3 0 100-6 3 3 0 000 6z" />
            <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z" />
        </svg>
    ),
};

const DESIGN_MODE_INFO: Record<DesignMode, { label: string; description: string; requiresTarget: boolean; defaultChains: ChainConfig[] }> = {
    rna_aptamer: {
        label: 'RNA Aptamer',
        description: 'Design RNA molecules with specific 3D structures (riboswitches, aptamers)',
        requiresTarget: false,
        defaultChains: [{ id: '1', type: 'rna', length: 40, useRange: false, useSequence: false }]
    },
    dna_aptamer: {
        label: 'DNA Aptamer',
        description: 'Design DNA sequences with target-binding capability',
        requiresTarget: false,
        defaultChains: [{ id: '1', type: 'dna', length: 40, useRange: false, useSequence: false }]
    },
    protein_dna: {
        label: 'Protein-DNA Complex',
        description: 'Design transcription factor-like proteins with cognate DNA',
        requiresTarget: false,
        defaultChains: [
            { id: '1', type: 'dna', length: 20, useRange: false, useSequence: false },
            { id: '2', type: 'protein', length: 80, useRange: false, useSequence: false }
        ]
    },
    protein_rna: {
        label: 'Protein-RNA Complex',
        description: 'Design ribonucleoprotein assemblies (RNA-binding proteins)',
        requiresTarget: false,
        defaultChains: [
            { id: '1', type: 'rna', length: 30, useRange: false, useSequence: false },
            { id: '2', type: 'protein', length: 100, useRange: false, useSequence: false }
        ]
    },
    protein_binding_aptamer: {
        label: 'Protein-Binding Aptamer',
        description: 'Design RNA/DNA that binds a specific target protein',
        requiresTarget: true,
        defaultChains: [{ id: '1', type: 'rna', length: 40, useRange: false, useSequence: false }]
    },
    custom: {
        label: 'Custom Multi-Polymer',
        description: 'Define custom chain configurations (DNA + RNA + Protein)',
        requiresTarget: false,
        defaultChains: [{ id: '1', type: 'protein', length: 100, useRange: false, useSequence: false }]
    }
};

// ============================================================================
// Utility Functions
// ============================================================================
const validateSequence = (sequence: string, type: PolymerType): { valid: boolean; error?: string } => {
    const cleanSeq = sequence.toUpperCase().replace(/\s/g, '');
    if (!cleanSeq) return { valid: false, error: 'Sequence is empty' };

    const patterns: Record<PolymerType, RegExp> = {
        dna: /^[ATGCNWSMKRY]+$/i,
        rna: /^[AUGCNWSMKRY]+$/i,
        protein: /^[ACDEFGHIKLMNPQRSTVWY*]+$/i,
    };

    if (!patterns[type].test(cleanSeq)) {
        return { valid: false, error: `Invalid characters for ${type.toUpperCase()} sequence` };
    }
    return { valid: true };
};

const getChainLength = (chain: ChainConfig): number => {
    if (chain.useSequence && chain.sequence) {
        return chain.sequence.replace(/\s/g, '').length;
    }
    if (chain.useRange && chain.lengthMax) {
        return chain.lengthMax;  // Use max for limit checking
    }
    return chain.length;
};

const checkSizeLimit = (chain: ChainConfig): { exceed: boolean; warning: boolean; message?: string } => {
    const length = getChainLength(chain);
    const limits = SIZE_LIMITS[chain.type];

    if (length > limits.max) {
        return { exceed: true, warning: true, message: `Exceeds maximum ${limits.max} ${limits.unit} for ${chain.type.toUpperCase()}` };
    }
    if (length > limits.safe) {
        return { exceed: false, warning: true, message: `Above recommended ${limits.safe} ${limits.unit} - may reduce quality` };
    }
    return { exceed: false, warning: false };
};

// ============================================================================
// Main Component
// ============================================================================
export function OligoDesignerTemplate({ onBack, initialValues }: OligoDesignerTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    // ============================================================================
    // State: Core Configuration
    // ============================================================================
    const [designName, setDesignName] = useState(initialValues?.designName as string || '');
    const [designMode, setDesignMode] = useState<DesignMode>(initialValues?.designMode as DesignMode || 'rna_aptamer');
    const [chains, setChains] = useState<ChainConfig[]>(DESIGN_MODE_INFO[designMode].defaultChains);

    // ============================================================================
    // State: Target Protein (for protein-binding aptamer mode)
    // ============================================================================
    const [selectedTarget, setSelectedTarget] = useState<SelectedTarget | null>(null);
    const [bindingChains, setBindingChains] = useState<string>('A');
    const [hotspotResidues, setHotspotResidues] = useState<string>('');

    // ============================================================================
    // State: Aptamer Database Selection
    // ============================================================================
    const [selectedAptamer, setSelectedAptamer] = useState<Aptamer | null>(null);
    const [showAptamerBrowser, setShowAptamerBrowser] = useState(false);

    // ============================================================================
    // State: Design Approach
    // ============================================================================
    const [designApproach, setDesignApproach] = useState<DesignApproach>('denovo');
    const [scaffoldPdbPath, setScaffoldPdbPath] = useState<string>('');

    // ============================================================================
    // State: Generation Settings
    // ============================================================================
    const [numDesigns, setNumDesigns] = useState(4);
    const [qualityPreset, setQualityPreset] = useState<QualityPreset>('standard');
    const [checkpoint, setCheckpoint] = useState<'generalized' | 'rna_optimized'>('generalized');

    // ============================================================================
    // State: Validation & Filtering
    // ============================================================================
    const [validateWithBoltz, setValidateWithBoltz] = useState(true);
    const [filterPreset, setFilterPreset] = useState<FilterPreset>('default');
    const [physicsSettings, setPhysicsSettings] = useState<PhysicsRefinementSettings>(PHYSICS_DEFAULTS);

    // ============================================================================
    // State: Storage Optimization
    // ============================================================================
    const [storagePreset, setStoragePreset] = useState<StoragePreset>('standard');

    // ============================================================================
    // State: Parallelism (SWA)
    // ============================================================================
    const [totalDesigns, setTotalDesigns] = useState(16);
    const [designsPerJob, setDesignsPerJob] = useState(4);
    const [useSwa, setUseSwa] = useState(false);

    // ============================================================================
    // State: Advanced Options (collapsed by default)
    // ============================================================================
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [showFilters, setShowFilters] = useState(false);
    const [showStorage, setShowStorage] = useState(false);
    const [showParallelism, setShowParallelism] = useState(false);
    const [temperature, setTemperature] = useState(1.0);
    const [seed, setSeed] = useState<number | null>(null);
    const [noiseSchedule, setNoiseSchedule] = useState<'linear' | 'cosine'>('linear');
    const [bindingGuidance, setBindingGuidance] = useState(false);

    // ============================================================================
    // State: UI
    // ============================================================================
    const [error, setError] = useState<string | null>(null);

    // ============================================================================
    // State: 3D Viewer & Scaffold Browser
    // ============================================================================
    const [showScaffoldBrowser, setShowScaffoldBrowser] = useState(false);
    const [scaffoldPdbUrl, setScaffoldPdbUrl] = useState<string | null>(null);
    const [show3DViewer, setShow3DViewer] = useState(true);
    const [rcsbSearchQuery, setRcsbSearchQuery] = useState('');
    const [selectedRcsbEntry, setSelectedRcsbEntry] = useState<{ id: string; title: string; type: string } | null>(null);

    // ============================================================================
    // Computed Values
    // ============================================================================
    const requiresTarget = DESIGN_MODE_INFO[designMode].requiresTarget;

    // Check for size limit warnings
    const sizeWarnings = useMemo(() => {
        return chains.map(chain => ({
            chain,
            ...checkSizeLimit(chain)
        })).filter(w => w.warning);
    }, [chains]);

    const hasSizeErrors = sizeWarnings.some(w => w.exceed);

    // Build contigs string for Nextflow
    const contigsString = useMemo(() => {
        return chains.map(c => {
            if (c.useSequence && c.sequence) {
                return String(c.sequence.replace(/\s/g, '').length);
            }
            if (c.useRange && c.lengthMin && c.lengthMax) {
                return `${c.lengthMin}-${c.lengthMax}`;
            }
            return String(c.length);
        }).join(' ');
    }, [chains]);

    const polymerChainsString = useMemo(() => {
        return chains.map(c => c.type).join(',');
    }, [chains]);

    // Build chain sequences object for backend
    const chainSequences = useMemo(() => {
        const seqs: Record<string, string> = {};
        chains.forEach((c, idx) => {
            if (c.useSequence && c.sequence) {
                seqs[`chain_${idx + 1}`] = c.sequence.replace(/\s/g, '').toUpperCase();
            }
        });
        return Object.keys(seqs).length > 0 ? seqs : null;
    }, [chains]);

    // ============================================================================
    // Effects
    // ============================================================================

    // Update chains when mode changes
    useEffect(() => {
        setChains(DESIGN_MODE_INFO[designMode].defaultChains.map((c, i) => ({ ...c, id: String(i + 1) })));
        if (designMode === 'protein_binding_aptamer') {
            setBindingGuidance(true);
        }
        // Auto-select checkpoint based on mode
        if (designMode === 'rna_aptamer' || designMode === 'protein_rna') {
            setCheckpoint('rna_optimized');
        } else {
            setCheckpoint('generalized');
        }
    }, [designMode]);

    // Apply aptamer sequence when selected
    useEffect(() => {
        if (selectedAptamer && chains.length > 0) {
            const aptamerType = selectedAptamer.aptamer_type.toLowerCase() as PolymerType;
            setChains([{
                id: '1',
                type: aptamerType,
                length: selectedAptamer.sequence.length,
                useRange: false,
                useSequence: true,
                sequence: selectedAptamer.sequence,
            }]);
        }
    }, [selectedAptamer]);

    // ============================================================================
    // Handlers
    // ============================================================================
    const handleModeChange = (mode: DesignMode) => {
        setDesignMode(mode);
        setSelectedAptamer(null);
    };

    const addChain = () => {
        const newId = String(chains.length + 1);
        setChains([...chains, { id: newId, type: 'protein', length: 50, useRange: false, useSequence: false }]);
    };

    const removeChain = (id: string) => {
        if (chains.length > 1) {
            setChains(chains.filter(c => c.id !== id));
        }
    };

    const updateChain = (id: string, updates: Partial<ChainConfig>) => {
        setChains(chains.map(c => c.id === id ? { ...c, ...updates } : c));
    };

    // ============================================================================
    // Job Submission
    // ============================================================================
    const submitMutation = useMutation({
        mutationFn: async () => {
            const filterSettings = FILTER_PRESETS[filterPreset];
            const storageSettings = STORAGE_PRESETS[storagePreset];

            const effectiveNumDesigns = useSwa ? totalDesigns : numDesigns;

            const jobPayload = {
                name: designName || `oligo_${Date.now()}`,
                model_id: 'oligo_design',
                mode: 'oligo_design',
                params: {
                    rfdpoly_enabled: true,
                    rfdpoly_contigs: contigsString,
                    rfdpoly_polymer_chains: polymerChainsString,
                    rfdpoly_num_designs: effectiveNumDesigns,
                    rfdpoly_diffusion_steps: QUALITY_PRESETS[qualityPreset].steps,
                    rfdpoly_checkpoint: checkpoint,
                    rfdpoly_noise_schedule: noiseSchedule,
                    // Target binding (for protein-binding aptamer mode)
                    ...(selectedTarget && {
                        target_pdb: selectedTarget.path || selectedTarget.url,
                        target_chains: bindingChains,
                        hotspot_residues: hotspotResidues || undefined,
                        binding_guidance: bindingGuidance,
                    }),
                    // Scaffold (for scaffold-constrained design)
                    ...(designApproach === 'scaffold' && scaffoldPdbPath && {
                        scaffold_pdb: scaffoldPdbPath,
                    }),
                    // Custom sequences
                    ...(chainSequences && { chain_sequences: chainSequences }),
                    // Validation & filtering
                    oligo_validate_boltz: validateWithBoltz,
                    oligo_min_plddt: filterSettings.min_plddt,
                    oligo_min_ptm: filterSettings.min_ptm,
                    boltz_min_plddt: filterSettings.min_plddt,
                    // Physics refinement
                    openmm_enabled: physicsSettings.enabled,
                    openmm_compute_tier: physicsSettings.computeTier,
                    // Storage
                    keep_intermediates: storageSettings.keep_intermediates,
                    compress_outputs: storageSettings.compress_outputs,
                    // SWA Parallelism
                    ...(useSwa && {
                        use_swa: true,
                        designs_per_job: designsPerJob,
                    }),
                    // Advanced options
                    ...(temperature !== 1.0 && { rfdpoly_temperature: temperature }),
                    ...(seed !== null && { rfdpoly_seed: seed }),
                }
            };

            return submitJob(jobPayload);
        },
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate(`/results/${response.data.job_id}`);
        },
        onError: (err: Error) => {
            setError(err.message);
        }
    });

    const handleSubmit = () => {
        setError(null);
        if (!designName.trim()) {
            setError('Please enter a design name');
            return;
        }
        if (requiresTarget && !selectedTarget) {
            setError('This design mode requires a target protein. Please select one.');
            return;
        }
        if (hasSizeErrors) {
            setError('One or more chains exceed the maximum size limits. Please reduce chain lengths.');
            return;
        }
        submitMutation.mutate();
    };

    // ============================================================================
    // Render
    // ============================================================================
    return (
        <div className="oligo-designer-template p-6 space-y-6 max-w-4xl mx-auto">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <button
                        onClick={onBack}
                        className="p-2 hover:bg-slate-700 rounded-lg transition-colors text-slate-400 hover:text-white"
                    >
                        ← Back
                    </button>
                    <div>
                        <h1 className="text-2xl font-bold text-white">Oligo Designer</h1>
                        <p className="text-sm text-slate-500">RNA/DNA aptamer design with RFDpoly</p>
                    </div>
                </div>
            </div>

            {/* Size Limit Info Banner */}
            <div className="bg-purple-500/10 border border-purple-500/30 rounded-lg p-3 text-sm">
                <div className="font-medium text-purple-300 mb-1">RFDpoly Size Limits</div>
                <div className="text-slate-400 text-xs grid grid-cols-3 gap-2">
                    <span>RNA: ≤120 nt (safe) / 240 nt max</span>
                    <span>DNA: ≤120 nt (safe) / 200 nt max</span>
                    <span>Protein: ≤400 AA (safe) / 600 AA max</span>
                </div>
            </div>

            {/* Design Name */}
            <div className="bg-slate-800 rounded-lg p-4">
                <label className="block text-sm font-medium text-slate-300 mb-2">Design Name *</label>
                <input
                    type="text"
                    value={designName}
                    onChange={(e) => setDesignName(e.target.value)}
                    placeholder="my_rna_aptamer"
                    className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                />
            </div>

            {/* Design Mode Selector */}
            <div className="bg-slate-800 rounded-lg p-4">
                <label className="block text-sm font-medium text-slate-300 mb-3">Design Mode</label>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                    {(Object.entries(DESIGN_MODE_INFO) as [DesignMode, typeof DESIGN_MODE_INFO[DesignMode]][]).map(([mode, info]) => (
                        <button
                            key={mode}
                            onClick={() => handleModeChange(mode)}
                            className={`p-4 rounded-lg border-2 transition-all text-left ${designMode === mode
                                ? 'border-emerald-500 bg-emerald-500/10'
                                : 'border-slate-600 hover:border-slate-500 bg-slate-700/50'
                                }`}
                        >
                            <div className="text-emerald-400 mb-2">{DESIGN_MODE_ICONS[mode]}</div>
                            <div className="font-medium text-white text-sm">{info.label}</div>
                            <div className="text-xs text-slate-400 mt-1">{info.description}</div>
                            {info.requiresTarget && (
                                <div className="text-xs text-amber-400 mt-1">Requires target protein</div>
                            )}
                        </button>
                    ))}
                </div>
            </div>

            {/* Aptamer Database Browser */}
            {(designMode === 'rna_aptamer' || designMode === 'dna_aptamer') && (
                <div className="bg-slate-800 rounded-lg p-4">
                    <div className="flex justify-between items-center mb-3">
                        <label className="text-sm font-medium text-slate-300">Start from Known Aptamer (Optional)</label>
                        <button
                            onClick={() => setShowAptamerBrowser(!showAptamerBrowser)}
                            className="text-sm text-emerald-400 hover:text-emerald-300"
                        >
                            {showAptamerBrowser ? 'Hide Browser' : 'Browse Aptamers'}
                        </button>
                    </div>
                    {showAptamerBrowser && (
                        <AptamerBrowser
                            onSelect={setSelectedAptamer}
                            selectedAptamer={selectedAptamer}
                            aptamerType={designMode === 'rna_aptamer' ? 'RNA' : 'DNA'}
                        />
                    )}
                    {selectedAptamer && !showAptamerBrowser && (
                        <div className="p-2 bg-emerald-500/10 border border-emerald-500/30 rounded text-sm">
                            <span className="text-emerald-300">Using: {selectedAptamer.name}</span>
                            <button onClick={() => setSelectedAptamer(null)} className="ml-2 text-slate-400 hover:text-white text-xs">Clear</button>
                        </div>
                    )}
                </div>
            )}

            {/* Target Protein Input (for protein-binding modes) */}
            {requiresTarget && (
                <div className="bg-slate-800 rounded-lg p-4">
                    <label className="block text-sm font-medium text-slate-300 mb-3">
                        Target Protein *
                    </label>
                    <TargetAntigenSelector
                        onSelect={setSelectedTarget}
                        selectedTarget={selectedTarget}
                    />
                    {selectedTarget && (
                        <div className="mt-4 grid grid-cols-2 gap-4">
                            <div>
                                <label className="text-xs text-slate-400 mb-1 block">Target Chains</label>
                                <input
                                    type="text"
                                    value={bindingChains}
                                    onChange={(e) => setBindingChains(e.target.value.toUpperCase())}
                                    placeholder="A"
                                    className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white font-mono"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 mb-1 block">Hotspot Residues (optional)</label>
                                <input
                                    type="text"
                                    value={hotspotResidues}
                                    onChange={(e) => setHotspotResidues(e.target.value)}
                                    placeholder="A25,A30-35,A50"
                                    className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white font-mono"
                                />
                            </div>
                        </div>
                    )}
                </div>
            )}

            {/* Design Approach Toggle */}
            <div className="bg-slate-800 rounded-lg p-4">
                <label className="block text-sm font-medium text-slate-300 mb-3">Design Approach</label>
                <div className="flex gap-4">
                    <button
                        onClick={() => setDesignApproach('denovo')}
                        className={`flex-1 py-2 px-4 rounded-lg border-2 transition-all ${designApproach === 'denovo'
                            ? 'border-emerald-500 bg-emerald-500/10 text-white'
                            : 'border-slate-600 text-slate-400 hover:border-slate-500'
                            }`}
                    >
                        De Novo Design
                    </button>
                    <button
                        onClick={() => setDesignApproach('scaffold')}
                        className={`flex-1 py-2 px-4 rounded-lg border-2 transition-all ${designApproach === 'scaffold'
                            ? 'border-emerald-500 bg-emerald-500/10 text-white'
                            : 'border-slate-600 text-slate-400 hover:border-slate-500'
                            }`}
                    >
                        Scaffold Redesign
                    </button>
                </div>
                {designApproach === 'scaffold' && (
                    <div className="mt-3">
                        <label className="text-xs text-slate-400 mb-1 block">Scaffold PDB Path</label>
                        <input
                            type="text"
                            value={scaffoldPdbPath}
                            onChange={(e) => setScaffoldPdbPath(e.target.value)}
                            placeholder="/path/to/scaffold.pdb"
                            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white font-mono"
                        />
                    </div>
                )}
            </div>

            {/* RNA/DNA Structure Browser & 3D Viewer */}
            <div className="bg-slate-800 rounded-lg p-4">
                <div className="flex justify-between items-center mb-3">
                    <label className="text-sm font-medium text-slate-300">
                        RNA/DNA Structure Browser
                    </label>
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => setShow3DViewer(!show3DViewer)}
                            className={`text-xs px-2 py-1 rounded ${show3DViewer ? 'bg-purple-500/20 text-purple-300' : 'bg-slate-700 text-slate-400'}`}
                        >
                            {show3DViewer ? '📐 Hide 3D' : '📐 Show 3D'}
                        </button>
                        <button
                            onClick={() => setShowScaffoldBrowser(!showScaffoldBrowser)}
                            className="text-sm text-emerald-400 hover:text-emerald-300"
                        >
                            {showScaffoldBrowser ? 'Hide Browser' : 'Browse Structures'}
                        </button>
                    </div>
                </div>

                {showScaffoldBrowser && (
                    <div className="space-y-4">
                        {/* RCSB Search */}
                        <div className="flex gap-2">
                            <input
                                type="text"
                                value={rcsbSearchQuery}
                                onChange={(e) => setRcsbSearchQuery(e.target.value)}
                                placeholder="Search RCSB PDB (e.g., 'tRNA', 'riboswitch', 'aptamer')..."
                                className="flex-1 bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white text-sm"
                            />
                            <button
                                onClick={() => {
                                    if (rcsbSearchQuery.trim()) {
                                        window.open(`https://www.rcsb.org/search?q=${encodeURIComponent(rcsbSearchQuery + ' AND (entity_poly.rcsb_entity_polymer_type:RNA OR entity_poly.rcsb_entity_polymer_type:DNA)')}`, '_blank');
                                    }
                                }}
                                className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded"
                            >
                                Search RCSB
                            </button>
                        </div>

                        {/* Curated RNA/DNA Scaffolds */}
                        <div>
                            <label className="text-xs text-slate-400 mb-2 block">Curated RNA/DNA Scaffolds</label>
                            <div className="grid grid-cols-2 gap-2">
                                {[
                                    { id: '1EHZ', name: 'tRNA (Phe)', type: 'RNA', length: 76 },
                                    { id: '4RNE', name: 'Ribonuclease P', type: 'RNA', length: 377 },
                                    { id: '2TOB', name: 'Tobramycin Aptamer', type: 'RNA', length: 27 },
                                    { id: '1AW4', name: 'Theophylline Riboswitch', type: 'RNA', length: 33 },
                                    { id: '1D65', name: 'DNA Crystal', type: 'DNA', length: 24 },
                                    { id: '1BNA', name: 'B-DNA Dodecamer', type: 'DNA', length: 12 },
                                    { id: '7R6R', name: 'Fluorogenic Aptamer', type: 'RNA', length: 53 },
                                    { id: '6E8U', name: 'CRISPR gRNA', type: 'RNA', length: 98 },
                                ].map(scaffold => (
                                    <button
                                        key={scaffold.id}
                                        onClick={() => {
                                            setSelectedRcsbEntry({ id: scaffold.id, title: scaffold.name, type: scaffold.type });
                                            setScaffoldPdbUrl(`https://files.rcsb.org/download/${scaffold.id}.pdb`);
                                            setDesignApproach('scaffold');
                                            setScaffoldPdbPath(`https://files.rcsb.org/download/${scaffold.id}.pdb`);
                                        }}
                                        className={`p-2 text-left rounded border transition-all ${selectedRcsbEntry?.id === scaffold.id
                                            ? 'border-purple-500 bg-purple-500/10'
                                            : 'border-slate-600 hover:border-slate-500 bg-slate-700/50'
                                            }`}
                                    >
                                        <div className="font-medium text-white text-sm">{scaffold.id}</div>
                                        <div className="text-xs text-slate-400">{scaffold.name}</div>
                                        <div className="text-xs text-purple-400">{scaffold.type} • {scaffold.length} nt</div>
                                    </button>
                                ))}
                            </div>
                        </div>

                        {/* Direct PDB ID Entry */}
                        <div className="flex gap-2 items-center">
                            <input
                                type="text"
                                placeholder="Enter PDB ID (e.g., 1EHZ)"
                                className="flex-1 bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white font-mono text-sm uppercase"
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter') {
                                        const pdbId = (e.target as HTMLInputElement).value.trim().toUpperCase();
                                        if (pdbId.length === 4) {
                                            setSelectedRcsbEntry({ id: pdbId, title: `PDB ${pdbId}`, type: 'Unknown' });
                                            setScaffoldPdbUrl(`https://files.rcsb.org/download/${pdbId}.pdb`);
                                            setDesignApproach('scaffold');
                                            setScaffoldPdbPath(`https://files.rcsb.org/download/${pdbId}.pdb`);
                                        }
                                    }
                                }}
                            />
                            <span className="text-xs text-slate-500">Press Enter to load</span>
                        </div>
                    </div>
                )}

                {/* 3D Structure Viewer */}
                {show3DViewer && scaffoldPdbUrl && (
                    <div className="mt-4">
                        <div className="flex items-center justify-between mb-2">
                            <span className="text-xs text-slate-400">
                                Viewing: <span className="text-purple-300">{selectedRcsbEntry?.title || 'Scaffold'}</span>
                            </span>
                            <button
                                onClick={() => {
                                    setScaffoldPdbUrl(null);
                                    setSelectedRcsbEntry(null);
                                }}
                                className="text-xs text-red-400 hover:text-red-300"
                            >
                                Clear
                            </button>
                        </div>
                        <MolstarViewer
                            structureUrl={scaffoldPdbUrl}
                            format="pdb"
                            height={300}
                            alphafoldView={false}
                            hideControls={false}
                            backgroundColor="#1e293b"
                        />
                    </div>
                )}

                {!showScaffoldBrowser && selectedRcsbEntry && (
                    <div className="p-2 bg-purple-500/10 border border-purple-500/30 rounded text-sm">
                        <span className="text-purple-300">Using scaffold: {selectedRcsbEntry.id} - {selectedRcsbEntry.title}</span>
                        <button
                            onClick={() => {
                                setSelectedRcsbEntry(null);
                                setScaffoldPdbUrl(null);
                                setScaffoldPdbPath('');
                            }}
                            className="ml-2 text-slate-400 hover:text-white text-xs"
                        >
                            Clear
                        </button>
                    </div>
                )}
            </div>

            {/* Chain Configuration */}
            <div className="bg-slate-800 rounded-lg p-4">
                <div className="flex justify-between items-center mb-3">
                    <label className="text-sm font-medium text-slate-300">Chain Configuration</label>
                    <button
                        onClick={addChain}
                        className="text-sm text-emerald-400 hover:text-emerald-300"
                    >
                        + Add Chain
                    </button>
                </div>

                {/* Size Warnings */}
                {sizeWarnings.length > 0 && (
                    <div className="mb-3 p-2 bg-amber-500/10 border border-amber-500/30 rounded">
                        {sizeWarnings.map((w, i) => (
                            <div key={i} className={`text-xs ${w.exceed ? 'text-red-400' : 'text-amber-400'}`}>
                                Chain {chains.indexOf(w.chain) + 1}: {w.message}
                            </div>
                        ))}
                    </div>
                )}

                <div className="space-y-3">
                    {chains.map((chain, index) => (
                        <div key={chain.id} className="bg-slate-700/50 rounded p-3 space-y-2">
                            <div className="flex items-center gap-3">
                                <span className="text-slate-400 w-16">Chain {index + 1}:</span>
                                <select
                                    value={chain.type}
                                    onChange={(e) => updateChain(chain.id, { type: e.target.value as PolymerType })}
                                    className="bg-slate-600 border border-slate-500 rounded px-2 py-1 text-white"
                                >
                                    <option value="dna">DNA</option>
                                    <option value="rna">RNA</option>
                                    <option value="protein">Protein</option>
                                </select>

                                <label className="flex items-center gap-1 text-xs text-slate-400">
                                    <input
                                        type="checkbox"
                                        checked={chain.useSequence}
                                        onChange={(e) => updateChain(chain.id, { useSequence: e.target.checked })}
                                        className="rounded"
                                    />
                                    Use Sequence
                                </label>

                                {chains.length > 1 && (
                                    <button
                                        onClick={() => removeChain(chain.id)}
                                        className="text-red-400 hover:text-red-300 text-sm ml-auto"
                                    >
                                        Remove
                                    </button>
                                )}
                            </div>

                            {chain.useSequence ? (
                                <div className="pl-16">
                                    <textarea
                                        value={chain.sequence || ''}
                                        onChange={(e) => updateChain(chain.id, { sequence: e.target.value })}
                                        placeholder={chain.type === 'protein'
                                            ? 'MVLSPADKTN...'
                                            : chain.type === 'rna'
                                                ? 'AUGCAUGCAUGC...'
                                                : 'ATGCATGCATGC...'
                                        }
                                        className="w-full bg-slate-600 border border-slate-500 rounded px-2 py-1 text-white font-mono text-sm h-16 resize-none"
                                    />
                                    {chain.sequence && !validateSequence(chain.sequence, chain.type).valid && (
                                        <div className="text-xs text-red-400 mt-1">
                                            {validateSequence(chain.sequence, chain.type).error}
                                        </div>
                                    )}
                                    {chain.sequence && (
                                        <div className="text-xs text-slate-400 mt-1">
                                            Length: {chain.sequence.replace(/\s/g, '').length} {chain.type === 'protein' ? 'residues' : 'nt'}
                                        </div>
                                    )}
                                    {chain.type !== 'protein' && (
                                        <div className="text-xs text-slate-500 mt-1">
                                            Modified nucleotides: {MODIFIED_NUCLEOTIDES[chain.type].join(', ')}
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="flex items-center gap-3 pl-16">
                                    <span className="text-slate-400">Length:</span>
                                    {chain.useRange ? (
                                        <>
                                            <input
                                                type="number"
                                                value={chain.lengthMin || chain.length}
                                                onChange={(e) => updateChain(chain.id, { lengthMin: parseInt(e.target.value) || 20 })}
                                                className="w-16 bg-slate-600 border border-slate-500 rounded px-2 py-1 text-white"
                                            />
                                            <span className="text-slate-500">-</span>
                                            <input
                                                type="number"
                                                value={chain.lengthMax || chain.length + 20}
                                                onChange={(e) => updateChain(chain.id, { lengthMax: parseInt(e.target.value) || 60 })}
                                                className="w-16 bg-slate-600 border border-slate-500 rounded px-2 py-1 text-white"
                                            />
                                        </>
                                    ) : (
                                        <input
                                            type="number"
                                            value={chain.length}
                                            onChange={(e) => updateChain(chain.id, { length: parseInt(e.target.value) || 50 })}
                                            className="w-20 bg-slate-600 border border-slate-500 rounded px-2 py-1 text-white"
                                        />
                                    )}
                                    <span className="text-slate-500 text-sm">{chain.type === 'protein' ? 'residues' : 'bases'}</span>
                                    <label className="flex items-center gap-1 text-xs text-slate-400 ml-auto">
                                        <input
                                            type="checkbox"
                                            checked={chain.useRange}
                                            onChange={(e) => updateChain(chain.id, {
                                                useRange: e.target.checked,
                                                lengthMin: chain.length - 10,
                                                lengthMax: chain.length + 10
                                            })}
                                            className="rounded"
                                        />
                                        Range
                                    </label>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
                <div className="mt-2 text-xs text-slate-500">
                    Contigs: <code className="bg-slate-700 px-1 rounded">{contigsString}</code> |
                    Chains: <code className="bg-slate-700 px-1 rounded">{polymerChainsString}</code>
                </div>
            </div>

            {/* Generation Settings */}
            <div className="bg-slate-800 rounded-lg p-4">
                <label className="block text-sm font-medium text-slate-300 mb-3">Generation Settings</label>
                <div className="grid grid-cols-2 gap-4">
                    <div>
                        <label className="text-xs text-slate-400 mb-1 block">Number of Designs</label>
                        <input
                            type="number"
                            value={numDesigns}
                            onChange={(e) => setNumDesigns(parseInt(e.target.value) || 4)}
                            min={1}
                            max={64}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                        />
                    </div>
                    <div>
                        <label className="text-xs text-slate-400 mb-1 block">Quality Preset</label>
                        <select
                            value={qualityPreset}
                            onChange={(e) => setQualityPreset(e.target.value as QualityPreset)}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                        >
                            {Object.entries(QUALITY_PRESETS).map(([key, preset]) => (
                                <option key={key} value={key}>{preset.label} ({preset.steps} steps)</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="text-xs text-slate-400 mb-1 block">Model Checkpoint</label>
                        <select
                            value={checkpoint}
                            onChange={(e) => setCheckpoint(e.target.value as 'generalized' | 'rna_optimized')}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                        >
                            <option value="generalized">Generalized (All Polymers)</option>
                            <option value="rna_optimized">RNA-Optimized</option>
                        </select>
                    </div>
                    <div>
                        <label className="text-xs text-slate-400 mb-1 block">Filter Preset</label>
                        <select
                            value={filterPreset}
                            onChange={(e) => setFilterPreset(e.target.value as FilterPreset)}
                            className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                        >
                            {Object.entries(FILTER_PRESETS).map(([key, preset]) => (
                                <option key={key} value={key}>
                                    {preset.label} (pLDDT≥{preset.min_plddt}, pTM≥{preset.min_ptm})
                                </option>
                            ))}
                        </select>
                    </div>
                </div>
            </div>

            {/* Validation Settings */}
            <div className="bg-slate-800 rounded-lg p-4">
                <label className="block text-sm font-medium text-slate-300 mb-3">Validation</label>
                <label className="flex items-center gap-2">
                    <input
                        type="checkbox"
                        checked={validateWithBoltz}
                        onChange={(e) => setValidateWithBoltz(e.target.checked)}
                        className="rounded"
                    />
                    <span className="text-white">Validate with Boltz-2 (Structural Refolding)</span>
                </label>
            </div>

            {/* Physics Refinement Panel */}
            <PhysicsRefinementPanel
                settings={physicsSettings}
                onSettingsChange={setPhysicsSettings}
                isAntibody={false}
            />

            {/* Collapsible: Filter Details */}
            <div className="bg-slate-800 rounded-lg p-4">
                <button
                    onClick={() => setShowFilters(!showFilters)}
                    className="text-sm text-slate-400 hover:text-white flex items-center gap-2 w-full"
                >
                    {showFilters ? '▼' : '▸'} Filter Details
                </button>
                {showFilters && (
                    <div className="mt-4 grid grid-cols-2 gap-4">
                        <div>
                            <label className="text-xs text-slate-400 mb-1 block">Min pLDDT</label>
                            <input
                                type="number"
                                value={FILTER_PRESETS[filterPreset].min_plddt}
                                disabled
                                className="w-full bg-slate-600 border border-slate-500 rounded px-3 py-2 text-white opacity-60"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-slate-400 mb-1 block">Min pTM</label>
                            <input
                                type="number"
                                value={FILTER_PRESETS[filterPreset].min_ptm}
                                disabled
                                className="w-full bg-slate-600 border border-slate-500 rounded px-3 py-2 text-white opacity-60"
                            />
                        </div>
                        <div className="col-span-2 text-xs text-slate-500">
                            {FILTER_PRESETS[filterPreset].description}
                        </div>
                    </div>
                )}
            </div>

            {/* Collapsible: Storage Options */}
            <div className="bg-slate-800 rounded-lg p-4">
                <button
                    onClick={() => setShowStorage(!showStorage)}
                    className="text-sm text-slate-400 hover:text-white flex items-center gap-2 w-full"
                >
                    {showStorage ? '▼' : '▸'} Storage Options
                </button>
                {showStorage && (
                    <div className="mt-4 space-y-3">
                        {Object.entries(STORAGE_PRESETS).map(([key, preset]) => (
                            <label key={key} className="flex items-center gap-2">
                                <input
                                    type="radio"
                                    name="storage"
                                    checked={storagePreset === key}
                                    onChange={() => setStoragePreset(key as StoragePreset)}
                                    className="rounded"
                                />
                                <span className="text-white">{preset.label}</span>
                                <span className="text-xs text-slate-400">- {preset.description}</span>
                            </label>
                        ))}
                    </div>
                )}
            </div>

            {/* Collapsible: Parallelism (SWA) */}
            <div className="bg-slate-800 rounded-lg p-4">
                <button
                    onClick={() => setShowParallelism(!showParallelism)}
                    className="text-sm text-slate-400 hover:text-white flex items-center gap-2 w-full"
                >
                    {showParallelism ? '▼' : '▸'} Multi-GPU Parallelism (SWA)
                </button>
                {showParallelism && (
                    <div className="mt-4 space-y-3">
                        <label className="flex items-center gap-2">
                            <input
                                type="checkbox"
                                checked={useSwa}
                                onChange={(e) => setUseSwa(e.target.checked)}
                                className="rounded"
                            />
                            <span className="text-white">Enable Spawn-Wait-Aggregate</span>
                            <span className="text-xs text-slate-400">(split across multiple GPUs)</span>
                        </label>
                        {useSwa && (
                            <div className="grid grid-cols-2 gap-4 pl-6">
                                <div>
                                    <label className="text-xs text-slate-400 mb-1 block">Total Designs</label>
                                    <input
                                        type="number"
                                        value={totalDesigns}
                                        onChange={(e) => setTotalDesigns(parseInt(e.target.value) || 16)}
                                        min={4}
                                        max={256}
                                        className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                                    />
                                </div>
                                <div>
                                    <label className="text-xs text-slate-400 mb-1 block">Designs Per Job</label>
                                    <input
                                        type="number"
                                        value={designsPerJob}
                                        onChange={(e) => setDesignsPerJob(parseInt(e.target.value) || 4)}
                                        min={1}
                                        max={16}
                                        className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                                    />
                                </div>
                                <div className="col-span-2 text-xs text-slate-500">
                                    Will spawn {Math.ceil(totalDesigns / designsPerJob)} parallel jobs
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Collapsible: Advanced Options */}
            <div className="bg-slate-800 rounded-lg p-4">
                <button
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="text-sm text-slate-400 hover:text-white flex items-center gap-2 w-full"
                >
                    {showAdvanced ? '▼' : '▸'} Advanced Options
                </button>
                {showAdvanced && (
                    <div className="mt-4 grid grid-cols-2 gap-4">
                        <div>
                            <label className="text-xs text-slate-400 mb-1 block">Temperature</label>
                            <input
                                type="number"
                                step={0.1}
                                value={temperature}
                                onChange={(e) => setTemperature(parseFloat(e.target.value) || 1.0)}
                                min={0.1}
                                max={2.0}
                                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                            />
                            <div className="text-xs text-slate-500 mt-1">Higher = more diverse</div>
                        </div>
                        <div>
                            <label className="text-xs text-slate-400 mb-1 block">Random Seed (optional)</label>
                            <input
                                type="number"
                                value={seed ?? ''}
                                onChange={(e) => setSeed(e.target.value ? parseInt(e.target.value) : null)}
                                placeholder="Auto"
                                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                            />
                        </div>
                        <div>
                            <label className="text-xs text-slate-400 mb-1 block">Noise Schedule</label>
                            <select
                                value={noiseSchedule}
                                onChange={(e) => setNoiseSchedule(e.target.value as 'linear' | 'cosine')}
                                className="w-full bg-slate-700 border border-slate-600 rounded px-3 py-2 text-white"
                            >
                                <option value="linear">Linear</option>
                                <option value="cosine">Cosine</option>
                            </select>
                        </div>
                        <div className="flex items-center">
                            <label className="flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={bindingGuidance}
                                    onChange={(e) => setBindingGuidance(e.target.checked)}
                                    className="rounded"
                                />
                                <span className="text-white text-sm">Enable Binding Guidance</span>
                            </label>
                        </div>
                    </div>
                )}
            </div>

            {/* Error Display */}
            {(error || submitMutation.isError) && (
                <div className="bg-red-500/20 border border-red-500 rounded p-3 text-red-300">
                    Error: {error || (submitMutation.error as Error)?.message || 'Failed to submit job'}
                </div>
            )}

            {/* Submit Button */}
            <div className="flex justify-end gap-4">
                <button
                    onClick={onBack}
                    className="px-6 py-2 bg-slate-700 text-white rounded hover:bg-slate-600"
                >
                    Cancel
                </button>
                <button
                    onClick={handleSubmit}
                    disabled={submitMutation.isPending || !designName.trim() || (requiresTarget && !selectedTarget) || hasSizeErrors}
                    className="px-6 py-2 bg-emerald-600 text-white rounded hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    {submitMutation.isPending ? 'Submitting...' : 'Submit Design Job'}
                </button>
            </div>
        </div>
    );
}

export default OligoDesignerTemplate;
