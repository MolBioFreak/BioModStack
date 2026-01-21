/**
 * BindCraftTemplate - De Novo Minibinder Design Workflow
 * 
 * Uses AlphaFold2 backpropagation, ProteinMPNN, and PyRosetta for
 * automated binder design with ~50% experimental success rate.
 * 
 * Reference: https://github.com/martinpacesa/BindCraft
 */

import { useState, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob, uploadFile } from '../lib/api';
import { useNavigate } from 'react-router-dom';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { TemplateManagerModal } from './TemplateManagerModal';
import { FrameworkBrowser, type SelectedFramework } from './FrameworkBrowser';
import { PhysicsRefinementPanel, type PhysicsRefinementSettings, DEFAULT_SETTINGS as PHYSICS_DEFAULTS } from './PhysicsRefinementPanel';

// Design algorithm options
const DESIGN_ALGORITHMS = [
    { id: '2stage', name: '2-Stage (Fast)', description: 'logits→pssm_semigreedy - fastest, less diverse' },
    { id: '3stage', name: '3-Stage (Standard)', description: 'logits→softmax→one-hot - balanced' },
    { id: '4stage', name: '4-Stage (Extensive)', description: 'logits→softmax→one-hot→pssm_semigreedy - most thorough' },
    { id: 'greedy', name: 'Greedy', description: 'Random mutations that decrease loss - less memory, slower' },
    { id: 'mcmc', name: 'MCMC', description: 'Monte Carlo sampling - less memory, slower' },
];

// Filter presets
const FILTER_PRESETS = [
    { id: 'default', name: 'Default', description: 'BindCraft recommended settings', min_iptm: 0.6, max_hotspot_rmsd: 3.0, min_plddt: 0.8 },
    { id: 'stringent', name: 'Stringent', description: 'Higher quality, fewer designs', min_iptm: 0.75, max_hotspot_rmsd: 2.0, min_plddt: 0.85 },
    { id: 'permissive', name: 'Permissive', description: 'More designs, higher false positives', min_iptm: 0.5, max_hotspot_rmsd: 5.0, min_plddt: 0.7 },
    { id: 'peptide', name: 'Peptide', description: 'Optimized for 8-25 AA peptides', min_iptm: 0.5, max_hotspot_rmsd: 5.0, min_plddt: 0.7 },
];

// Storage optimization presets
const STORAGE_PRESETS = {
    minimal: {
        zip_animations: true,
        zip_plots: true,
        remove_unrelaxed_trajectory: true,
        remove_unrelaxed_complex: true,
        remove_binder_monomer: true,
        save_trajectory_pickle: false,
    },
    full_debug: {
        zip_animations: false,
        zip_plots: false,
        remove_unrelaxed_trajectory: false,
        remove_unrelaxed_complex: false,
        remove_binder_monomer: false,
        save_trajectory_pickle: true,
    },
};

interface BindCraftTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
}

export function BindCraftTemplate({ onBack, initialValues }: BindCraftTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    // ============================================================================
    // State: Target Configuration
    // ============================================================================
    const [targetPdbPath, setTargetPdbPath] = useState<string>('');
    const [hotspotResidues, setHotspotResidues] = useState<string>('');
    const [chains, setChains] = useState<string>('A');
    const [jobName, setJobName] = useState<string>('');

    // ============================================================================
    // State: Design Approach (De Novo vs Scaffold Redesign)
    // ============================================================================
    const [designApproach, setDesignApproach] = useState<'denovo' | 'scaffold_redesign' | 'cdr_hallucination'>('denovo');
    const [scaffoldPdbPath, setScaffoldPdbPath] = useState<string>('');
    const [binderChain, setBinderChain] = useState<string>('B');

    // CDR Hallucination specific settings
    const [cdrLengthMode, setCdrLengthMode] = useState<'fixed' | 'sample'>('fixed');
    const [cdrH1Range, setCdrH1Range] = useState<string>('5-12');
    const [cdrH2Range, setCdrH2Range] = useState<string>('6-10');
    const [cdrH3Range, setCdrH3Range] = useState<string>('10-18');

    // ============================================================================
    // State: Binder Configuration
    // ============================================================================
    const [designMode, setDesignMode] = useState<'minibinder' | 'peptide'>('minibinder');
    const [binderLengthMin, setBinderLengthMin] = useState<number>(80);
    const [binderLengthMax, setBinderLengthMax] = useState<number>(120);
    const [numFinalDesigns, setNumFinalDesigns] = useState<number>(100);

    // ============================================================================
    // State: Design Algorithm
    // ============================================================================
    const [designAlgorithm, setDesignAlgorithm] = useState<string>('4stage');
    const [useMultimerDesign, setUseMultimerDesign] = useState<boolean>(true);
    const [numRecyclesDesign, setNumRecyclesDesign] = useState<number>(3);
    const [numRecyclesValidation] = useState<number>(3);

    // ============================================================================
    // State: MPNN Settings
    // ============================================================================
    const [mpnnWeights, setMpnnWeights] = useState<'original' | 'soluble'>('soluble');
    const [numMpnnSequences, setNumMpnnSequences] = useState<number>(8);
    const [mpnnFixInterface] = useState<boolean>(false);

    // ============================================================================
    // State: Filter Configuration
    // ============================================================================
    const [filterPreset, setFilterPreset] = useState<string>('default');
    const [minIptm, setMinIptm] = useState<number>(0.6);
    const [maxHotspotRmsd, setMaxHotspotRmsd] = useState<number>(3.0);
    const [minPlddt, setMinPlddt] = useState<number>(0.8);

    // ============================================================================
    // State: Storage Optimization
    // ============================================================================
    const [zipAnimations, setZipAnimations] = useState<boolean>(true);
    const [zipPlots, setZipPlots] = useState<boolean>(true);
    const [removeUnrelaxedTrajectory, setRemoveUnrelaxedTrajectory] = useState<boolean>(true);
    const [removeUnrelaxedComplex, setRemoveUnrelaxedComplex] = useState<boolean>(true);
    const [removeBinderMonomer, setRemoveBinderMonomer] = useState<boolean>(true);
    const [saveTrajectoryPickle, setSaveTrajectoryPickle] = useState<boolean>(false);

    // ============================================================================
    // State: Parallelism (SWA)
    // ============================================================================
    const [totalTrajectories, setTotalTrajectories] = useState<number>(100);
    const [trajectoriesPerJob, setTrajectoriesPerJob] = useState<number>(25);
    const [useSwa] = useState<boolean>(true);

    // ============================================================================
    // State: Post-processing
    // ============================================================================
    const [budget] = useState<number | null>(null);
    const [alpha] = useState<number>(0.01);
    const [boltzValidation] = useState<boolean>(false);

    // Physics refinement settings (OpenMM)
    const [physicsSettings, setPhysicsSettings] = useState<PhysicsRefinementSettings>(PHYSICS_DEFAULTS);

    // ============================================================================
    // State: UI
    // ============================================================================
    const [showAdvanced, setShowAdvanced] = useState<boolean>(false);
    const [showFilters, setShowFilters] = useState<boolean>(false);
    const [showStorage, setShowStorage] = useState<boolean>(false);
    const [showTemplateManager, setShowTemplateManager] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);

    // ============================================================================
    // State: Phase 1 - Framework Selection & 3D Viewer
    // ============================================================================
    const [selectedFramework, setSelectedFramework] = useState<SelectedFramework | null>(null);
    const [showFrameworkBrowser, setShowFrameworkBrowser] = useState<boolean>(false);
    const [frameworkPdbUrl, setFrameworkPdbUrl] = useState<string | null>(null);
    const [viewerMode, setViewerMode] = useState<'target' | 'framework'>('target');
    const [show3DViewer, setShow3DViewer] = useState<boolean>(true); // Default to showing viewer

    // ============================================================================
    // State: Phase 2 - Residue Mask (Fixed vs Redesign positions)
    // ============================================================================
    const [maskMode, setMaskMode] = useState<'none' | 'imgt_auto' | 'manual' | 'range'>('none');
    const [redesignRanges, setRedesignRanges] = useState<string>(''); // e.g., "26-35,50-65,95-115"

    // ============================================================================
    // State: Phase 3 - Template Flexibility Controls
    // ============================================================================
    const [rmTemplateSeqDesign, setRmTemplateSeqDesign] = useState<boolean>(false);
    const [rmTemplateScDesign, setRmTemplateScDesign] = useState<boolean>(false);
    const [predictInitialGuess, setPredictInitialGuess] = useState<boolean>(true);
    const [useTerminiDistanceLoss, setUseTerminiDistanceLoss] = useState<boolean>(false);
    const [showTemplateOptions, setShowTemplateOptions] = useState<boolean>(false);

    // ============================================================================
    // State: Phase 4 - CDR Length Sampling
    // ============================================================================
    const [cdrSamplingEnabled, setCdrSamplingEnabled] = useState<boolean>(false);
    const [cdrSamplingCount, setCdrSamplingCount] = useState<number>(5); // Generate N configs with different lengths

    // Update binder length defaults based on design mode
    useEffect(() => {
        if (designMode === 'peptide') {
            setBinderLengthMin(12);
            setBinderLengthMax(20);
            setFilterPreset('peptide');
        } else {
            setBinderLengthMin(80);
            setBinderLengthMax(120);
            setFilterPreset('default');
        }
    }, [designMode]);

    // Apply filter preset
    useEffect(() => {
        const preset = FILTER_PRESETS.find(p => p.id === filterPreset);
        if (preset) {
            setMinIptm(preset.min_iptm);
            setMaxHotspotRmsd(preset.max_hotspot_rmsd);
            setMinPlddt(preset.min_plddt);
        }
    }, [filterPreset]);

    // Load initial values
    useEffect(() => {
        if (initialValues) {
            if (initialValues.target_pdb) setTargetPdbPath(initialValues.target_pdb as string);
            if (initialValues.hotspot_residues) setHotspotResidues(initialValues.hotspot_residues as string);
            if (initialValues.chains) setChains(initialValues.chains as string);
            if (initialValues.job_name) setJobName(initialValues.job_name as string);
            if (initialValues.design_algorithm) setDesignAlgorithm(initialValues.design_algorithm as string);
            if (initialValues.num_final_designs) setNumFinalDesigns(initialValues.num_final_designs as number);
        }
    }, [initialValues]);

    // ============================================================================
    // Job Submission
    // ============================================================================
    const submitMutation = useMutation({
        mutationFn: async (data: Record<string, unknown>) => submitJob(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        },
        onError: (err: Error) => {
            setError(err.message || 'Failed to submit job');
        },
    });

    const handleFileUpload = async (file: File) => {
        setError(null);

        try {
            // Upload to bindcraft directory
            const result = await uploadFile('bindcraft', file);
            setTargetPdbPath(result.data.path);
        } catch (err: unknown) {
            const errorMessage = err instanceof Error ? err.message : 'Unknown error';
            setError(`Failed to upload file: ${errorMessage}`);
        }
    };

    const handleScaffoldUpload = async (file: File) => {
        setError(null);
        try {
            const result = await uploadFile('bindcraft/scaffolds', file);
            setScaffoldPdbPath(result.data.path);
        } catch (err: unknown) {
            const errorMessage = err instanceof Error ? err.message : 'Unknown error';
            setError(`Failed to upload scaffold file: ${errorMessage}`);
        }
    };

    const handleSubmit = async () => {
        // Validate based on design approach
        if (designApproach === 'scaffold_redesign') {
            if (!scaffoldPdbPath) {
                setError('Please upload a scaffold PDB file (target + binder complex)');
                return;
            }
        } else if (designApproach === 'cdr_hallucination') {
            if (!targetPdbPath) {
                setError('Please upload a target PDB file for CDR hallucination');
                return;
            }
            // Validate CDR length ranges format
            const rangePattern = /^\d+-\d+$/;
            if (!rangePattern.test(cdrH1Range) || !rangePattern.test(cdrH2Range) || !rangePattern.test(cdrH3Range)) {
                setError('CDR length ranges must be in format "min-max" (e.g., "10-18")');
                return;
            }
        } else {
            if (!targetPdbPath) {
                setError('Please upload a target PDB file');
                return;
            }
        }

        const binderLengths = `${binderLengthMin}-${binderLengthMax}`;

        const params = {
            workflow: 'bindcraft',
            name: jobName || `BindCraft_${new Date().toISOString().slice(0, 10)}`,
            rfd_mode: 'bindcraft',
            // Design approach
            bindcraft_design_mode: designApproach,
            bindcraft_scaffold_pdb: designApproach === 'scaffold_redesign' ? scaffoldPdbPath :
                (designApproach === 'cdr_hallucination' && scaffoldPdbPath ? scaffoldPdbPath : null),
            bindcraft_binder_chain: designApproach === 'scaffold_redesign' ? binderChain : null,
            // CDR Hallucination specific
            bindcraft_cdr_length_mode: designApproach === 'cdr_hallucination' ? cdrLengthMode : null,
            bindcraft_cdr_h1_range: designApproach === 'cdr_hallucination' ? cdrH1Range : null,
            bindcraft_cdr_h2_range: designApproach === 'cdr_hallucination' ? cdrH2Range : null,
            bindcraft_cdr_h3_range: designApproach === 'cdr_hallucination' ? cdrH3Range : null,
            // Phase 2: Residue Mask
            bindcraft_mask_mode: designApproach === 'cdr_hallucination' ? maskMode : null,
            bindcraft_redesign_ranges: designApproach === 'cdr_hallucination' && maskMode === 'range' ? redesignRanges : null,
            // Phase 3: Template Flexibility
            bindcraft_rm_template_seq_design: rmTemplateSeqDesign,
            bindcraft_rm_template_sc_design: rmTemplateScDesign,
            bindcraft_predict_initial_guess: predictInitialGuess,
            bindcraft_use_termini_distance_loss: useTerminiDistanceLoss,
            // Phase 4: CDR Sampling
            bindcraft_cdr_sampling_enabled: cdrSamplingEnabled,
            bindcraft_cdr_sampling_count: cdrSamplingEnabled ? cdrSamplingCount : null,
            // Target
            bindcraft_target_pdb: targetPdbPath,
            bindcraft_hotspot_residues: hotspotResidues || null,
            bindcraft_chains: chains,
            bindcraft_binder_lengths: binderLengths,
            bindcraft_num_final_designs: numFinalDesigns,
            // Algorithm
            bindcraft_design_algorithm: designAlgorithm,
            bindcraft_use_multimer_design: useMultimerDesign,
            bindcraft_num_recycles_design: numRecyclesDesign,
            bindcraft_num_recycles_validation: numRecyclesValidation,
            // MPNN
            bindcraft_mpnn_weights: mpnnWeights,
            bindcraft_num_mpnn_sequences: numMpnnSequences,
            bindcraft_mpnn_fix_interface: mpnnFixInterface,
            // Filters
            bindcraft_min_iptm: minIptm,
            bindcraft_max_hotspot_rmsd: maxHotspotRmsd,
            bindcraft_min_plddt: minPlddt,
            // Storage
            bindcraft_zip_animations: zipAnimations,
            bindcraft_zip_plots: zipPlots,
            bindcraft_remove_unrelaxed_trajectory: removeUnrelaxedTrajectory,
            bindcraft_remove_unrelaxed_complex: removeUnrelaxedComplex,
            bindcraft_remove_binder_monomer: removeBinderMonomer,
            bindcraft_save_trajectory_pickle: saveTrajectoryPickle,
            // SWA
            bindcraft_total_trajectories: totalTrajectories,
            bindcraft_trajectories_per_job: trajectoriesPerJob,
            bindcraft_use_swa: useSwa,
            // Post-processing
            bindcraft_budget: budget,
            bindcraft_alpha: alpha,
            bindcraft_boltz_validation: boltzValidation,
            // Physics refinement (OpenMM)
            openmm_enabled: physicsSettings.enabled,
            openmm_compute_tier: physicsSettings.computeTier,
            openmm_cdr_only: physicsSettings.cdrOnly,
            openmm_restraint_mode: physicsSettings.restraintMode,
            openmm_mmgbsa_mode: physicsSettings.mmgbsaMode,
            openmm_force_field: physicsSettings.forceField,
            openmm_top_n_percentage: physicsSettings.topNPercentage,
        };

        submitMutation.mutate(params);
    };

    const applyStoragePreset = (presetId: 'minimal' | 'full_debug') => {
        const preset = STORAGE_PRESETS[presetId];
        setZipAnimations(preset.zip_animations);
        setZipPlots(preset.zip_plots);
        setRemoveUnrelaxedTrajectory(preset.remove_unrelaxed_trajectory);
        setRemoveUnrelaxedComplex(preset.remove_unrelaxed_complex);
        setRemoveBinderMonomer(preset.remove_binder_monomer);
        setSaveTrajectoryPickle(preset.save_trajectory_pickle);
    };

    // Helper to convert hotspot string to Set for viewer
    const hotspotSet = new Set(hotspotResidues.split(',').map(s => s.trim()).filter(Boolean));

    // ============================================================================
    // Render
    // ============================================================================
    return (
        <div className="bg-slate-800/30 border border-slate-700 rounded-xl p-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                    <button onClick={onBack} className="p-2 hover:bg-slate-700 rounded-lg transition-colors">
                        <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
                        </svg>
                    </button>
                    <div>
                        <h2 className="text-lg font-semibold text-slate-200">BindCraft Minibinder Design</h2>
                        <p className="text-sm text-slate-500">AF2 hallucination → ProteinMPNN → PyRosetta filtering</p>
                    </div>
                </div>
                <button
                    onClick={() => setShowTemplateManager(true)}
                    className="px-3 py-2 text-sm bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-300 transition-colors"
                >
                    Templates
                </button>
            </div>

            {error && (
                <div className="p-4 bg-red-900/20 border border-red-800 rounded-lg text-red-400 mb-6">
                    {error}
                </div>
            )}

            {/* Job Name */}
            <div className="space-y-2 mb-6">
                <label className="block text-sm font-medium text-slate-400">Job Name</label>
                <input
                    type="text"
                    value={jobName}
                    onChange={(e) => setJobName(e.target.value)}
                    placeholder="BindCraft_PDL1_binder"
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-emerald-500 outline-none"
                />
            </div>

            {/* Design Approach Toggle */}
            <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 mb-6">
                <h3 className="text-sm font-medium text-slate-300 mb-4">Design Approach</h3>

                <div className="flex gap-4 mb-4">
                    <button
                        onClick={() => setDesignApproach('denovo')}
                        className={`flex-1 p-3 rounded-lg border-2 transition-colors ${designApproach === 'denovo'
                            ? 'border-emerald-500 bg-emerald-600/20'
                            : 'border-slate-700 hover:bg-slate-800'
                            }`}
                    >
                        <div className="font-medium text-slate-200">De Novo Design</div>
                        <div className="text-xs text-slate-500">Hallucinate new binder from scratch</div>
                    </button>
                    <button
                        onClick={() => setDesignApproach('scaffold_redesign')}
                        className={`flex-1 p-3 rounded-lg border-2 transition-colors ${designApproach === 'scaffold_redesign'
                            ? 'border-emerald-500 bg-emerald-600/20'
                            : 'border-slate-700 hover:bg-slate-800'
                            }`}
                    >
                        <div className="font-medium text-slate-200">Scaffold Redesign</div>
                        <div className="text-xs text-slate-500">Sequence optimization of scaffold</div>
                    </button>
                    <button
                        onClick={() => setDesignApproach('cdr_hallucination')}
                        className={`flex-1 p-3 rounded-lg border-2 transition-colors ${designApproach === 'cdr_hallucination'
                            ? 'border-purple-500 bg-purple-600/20'
                            : 'border-slate-700 hover:bg-slate-800'
                            }`}
                    >
                        <div className="font-medium text-slate-200">CDR Hallucination</div>
                        <div className="text-xs text-slate-500">Redesign CDR loops de novo</div>
                    </button>
                </div>

                {designApproach === 'scaffold_redesign' && (
                    <div className="space-y-4 p-4 bg-slate-800/50 rounded-lg border border-emerald-500/30">
                        <div className="text-xs text-emerald-400 mb-2">
                            ℹ️ Upload a PDB containing the target protein and existing binder complex. The binder backbone will be preserved while the sequence is optimized.
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">
                                Complex PDB (Target + Binder)
                            </label>
                            <input
                                type="file"
                                accept=".pdb"
                                onChange={(e) => e.target.files?.[0] && handleScaffoldUpload(e.target.files[0])}
                                className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg focus:ring-2 focus:ring-emerald-500 outline-none"
                            />
                            {scaffoldPdbPath && (
                                <p className="mt-1 text-xs text-emerald-400">✓ Uploaded: {scaffoldPdbPath.split('/').pop()}</p>
                            )}
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">
                                Binder Chain ID
                            </label>
                            <input
                                type="text"
                                value={binderChain}
                                onChange={(e) => setBinderChain(e.target.value.toUpperCase())}
                                maxLength={1}
                                placeholder="B"
                                className="w-20 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-center focus:ring-2 focus:ring-emerald-500 outline-none"
                            />
                            <p className="mt-1 text-xs text-slate-500">Chain ID of the binder/VHH to redesign</p>
                        </div>
                    </div>
                )}

                {designApproach === 'cdr_hallucination' && (
                    <div className="space-y-4 p-4 bg-slate-800/50 rounded-lg border border-purple-500/30">
                        <div className="text-xs text-purple-400 mb-2">
                            🧬 VHH-optimized CDR design. Select a framework template and configure CDR regions to redesign.
                        </div>

                        {/* VHH Framework Selection */}
                        <div className="space-y-3">
                            <div className="flex items-center justify-between">
                                <label className="block text-sm font-medium text-slate-300">
                                    VHH Framework Template
                                </label>
                                <div className="flex items-center gap-2">
                                    <button
                                        type="button"
                                        onClick={() => setShowFrameworkBrowser(!showFrameworkBrowser)}
                                        className={`px-3 py-1.5 text-xs rounded-lg transition-all flex items-center gap-1.5 ${showFrameworkBrowser
                                            ? 'bg-purple-500/20 text-purple-300 border border-purple-500/50'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700 border border-slate-700'
                                            }`}
                                    >
                                        📚 {showFrameworkBrowser ? 'Hide Browser' : 'Browse SAbDab'}
                                    </button>
                                    {selectedFramework && (
                                        <span className="px-2 py-1 bg-purple-500/20 text-purple-300 rounded text-xs flex items-center gap-1">
                                            🧬 {selectedFramework.pdbCode || selectedFramework.name}
                                            <button
                                                type="button"
                                                onClick={() => {
                                                    setSelectedFramework(null);
                                                    setFrameworkPdbUrl(null);
                                                    setScaffoldPdbPath('');
                                                }}
                                                className="ml-1 text-purple-400 hover:text-purple-200"
                                            >✕</button>
                                        </span>
                                    )}
                                </div>
                            </div>

                            {/* FrameworkBrowser */}
                            {showFrameworkBrowser && (
                                <div className="bg-slate-900/50 rounded-lg border border-slate-700 p-3">
                                    <FrameworkBrowser
                                        onSelect={(fw) => {
                                            setSelectedFramework(fw);
                                            // Set scaffold path if file downloaded
                                            if (fw?.filePath) {
                                                setScaffoldPdbPath(fw.filePath);
                                            }
                                            // Set framework PDB URL for 3D preview
                                            if (fw?.pdbCode) {
                                                setFrameworkPdbUrl(`https://files.rcsb.org/download/${fw.pdbCode.toUpperCase()}.pdb`);
                                                setViewerMode('framework');
                                                setShow3DViewer(true);
                                            }
                                            // Auto-populate CDR-H3 length from SAbDab
                                            if (fw?.cdrH3Length) {
                                                const min = Math.max(8, fw.cdrH3Length - 3);
                                                const max = fw.cdrH3Length + 3;
                                                setCdrH3Range(`${min}-${max}`);
                                            }
                                            setShowFrameworkBrowser(false);
                                        }}
                                        selectedFramework={selectedFramework}
                                        showCustomUpload={false}
                                    />
                                </div>
                            )}
                        </div>

                        {/* CDR Length Mode */}
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">CDR Length Mode</label>
                            <div className="flex gap-3">
                                <button
                                    onClick={() => setCdrLengthMode('fixed')}
                                    className={`px-4 py-2 rounded-lg border transition-colors ${cdrLengthMode === 'fixed'
                                        ? 'border-purple-500 bg-purple-600/20 text-purple-300'
                                        : 'border-slate-700 text-slate-400 hover:bg-slate-800'
                                        }`}
                                >
                                    Fixed (use median)
                                </button>
                                <button
                                    onClick={() => setCdrLengthMode('sample')}
                                    className={`px-4 py-2 rounded-lg border transition-colors ${cdrLengthMode === 'sample'
                                        ? 'border-purple-500 bg-purple-600/20 text-purple-300'
                                        : 'border-slate-700 text-slate-400 hover:bg-slate-800'
                                        }`}
                                >
                                    Sample (randomize)
                                </button>
                            </div>
                        </div>

                        {/* CDR Length Ranges */}
                        <div className="grid grid-cols-3 gap-4">
                            <div>
                                <label className="block text-sm font-medium text-slate-400 mb-1">CDR-H1 Length</label>
                                <input
                                    type="text"
                                    value={cdrH1Range}
                                    onChange={(e) => setCdrH1Range(e.target.value)}
                                    placeholder="5-12"
                                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-center focus:ring-2 focus:ring-purple-500 outline-none"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-slate-400 mb-1">CDR-H2 Length</label>
                                <input
                                    type="text"
                                    value={cdrH2Range}
                                    onChange={(e) => setCdrH2Range(e.target.value)}
                                    placeholder="6-10"
                                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-center focus:ring-2 focus:ring-purple-500 outline-none"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-slate-400 mb-1">CDR-H3 Length</label>
                                <input
                                    type="text"
                                    value={cdrH3Range}
                                    onChange={(e) => setCdrH3Range(e.target.value)}
                                    placeholder="10-18"
                                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-center focus:ring-2 focus:ring-purple-500 outline-none"
                                />
                                <p className="mt-1 text-xs text-slate-500">Most variable loop</p>
                            </div>
                        </div>

                        {/* Phase 4: CDR Sampling Option */}
                        {cdrLengthMode === 'sample' && (
                            <div className="mt-3 p-3 bg-slate-900/40 rounded-lg border border-purple-500/20">
                                <div className="flex items-center justify-between mb-2">
                                    <label className="text-sm font-medium text-slate-400">
                                        Generate Multiple CDR Length Configurations
                                    </label>
                                    <button
                                        type="button"
                                        onClick={() => setCdrSamplingEnabled(!cdrSamplingEnabled)}
                                        className={`px-3 py-1 text-xs rounded-lg transition-all ${cdrSamplingEnabled
                                            ? 'bg-purple-500/20 text-purple-300 border border-purple-500/50'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700 border border-slate-700'
                                            }`}
                                    >
                                        {cdrSamplingEnabled ? '✓ Enabled' : 'Enable'}
                                    </button>
                                </div>
                                {cdrSamplingEnabled && (
                                    <div className="flex items-center gap-3">
                                        <span className="text-xs text-slate-500">Number of configurations:</span>
                                        <input
                                            type="number"
                                            value={cdrSamplingCount}
                                            onChange={(e) => setCdrSamplingCount(parseInt(e.target.value) || 5)}
                                            min={2}
                                            max={20}
                                            className="w-16 bg-slate-900 border border-slate-700 rounded px-2 py-1 text-center text-sm"
                                        />
                                        <span className="text-xs text-slate-500">Each with different CDR lengths sampled from ranges</span>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Phase 2: Residue Mask Mode */}
                        <div className="mt-4 space-y-3">
                            <div className="flex items-center justify-between">
                                <label className="block text-sm font-medium text-slate-400">
                                    Residue Mask Mode
                                </label>
                                <div className="flex gap-2">
                                    {(['none', 'imgt_auto', 'range'] as const).map((mode) => (
                                        <button
                                            key={mode}
                                            onClick={() => setMaskMode(mode)}
                                            className={`px-3 py-1 text-xs rounded-lg transition-all ${maskMode === mode
                                                ? 'bg-purple-500/20 text-purple-300 border border-purple-500/50'
                                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700 border border-slate-700'
                                                }`}
                                        >
                                            {mode === 'none' ? 'Default' : mode === 'imgt_auto' ? 'IMGT Auto' : 'Custom Range'}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            {maskMode === 'imgt_auto' && (
                                <div className="text-xs text-purple-400 bg-purple-500/10 rounded p-2">
                                    ✓ IMGT positions will be used: CDR-H1 (26-35), CDR-H2 (50-65), CDR-H3 (95-115) → Redesign<br />
                                    Framework regions → Fixed
                                </div>
                            )}
                            {maskMode === 'range' && (
                                <div>
                                    <label className="block text-xs text-slate-500 mb-1">Custom redesign positions (e.g., "26-35,50-65,95-115")</label>
                                    <input
                                        type="text"
                                        value={redesignRanges}
                                        onChange={(e) => setRedesignRanges(e.target.value)}
                                        placeholder="26-35,50-65,95-115"
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-purple-500 outline-none"
                                    />
                                </div>
                            )}
                        </div>

                        {/* Phase 3: Template Flexibility Options */}
                        <button
                            type="button"
                            onClick={() => setShowTemplateOptions(!showTemplateOptions)}
                            className="w-full text-left text-xs text-slate-500 hover:text-slate-400 flex items-center gap-2 mt-4"
                        >
                            <span>{showTemplateOptions ? '▼' : '▶'}</span>
                            Advanced Template Options
                        </button>
                        {showTemplateOptions && (
                            <div className="space-y-2 p-3 bg-slate-900/40 rounded-lg border border-slate-700">
                                <label className="flex items-center gap-3 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={rmTemplateSeqDesign}
                                        onChange={(e) => setRmTemplateSeqDesign(e.target.checked)}
                                        className="rounded border-slate-600 bg-slate-900 text-purple-500"
                                    />
                                    <span className="text-sm text-slate-400">Remove target template sequence (more flexible)</span>
                                </label>
                                <label className="flex items-center gap-3 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={rmTemplateScDesign}
                                        onChange={(e) => setRmTemplateScDesign(e.target.checked)}
                                        className="rounded border-slate-600 bg-slate-900 text-purple-500"
                                    />
                                    <span className="text-sm text-slate-400">Remove target sidechains</span>
                                </label>
                                <label className="flex items-center gap-3 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={predictInitialGuess}
                                        onChange={(e) => setPredictInitialGuess(e.target.checked)}
                                        className="rounded border-slate-600 bg-slate-900 text-purple-500"
                                    />
                                    <span className="text-sm text-slate-400">Use scaffold as initial geometry guess</span>
                                </label>
                                <label className="flex items-center gap-3 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={useTerminiDistanceLoss}
                                        onChange={(e) => setUseTerminiDistanceLoss(e.target.checked)}
                                        className="rounded border-slate-600 bg-slate-900 text-purple-500"
                                    />
                                    <span className="text-sm text-slate-400">Minimize N/C terminus distance (for grafting)</span>
                                </label>
                            </div>
                        )}
                    </div>
                )}
            </div>

            {/* Section 1: Target Configuration */}
            <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 mb-6">
                <h3 className="text-sm font-medium text-slate-300 flex items-center gap-2 mb-4">
                    <span className="w-6 h-6 rounded-full bg-emerald-600/20 text-emerald-400 flex items-center justify-center text-xs font-bold">1</span>
                    {designApproach === 'scaffold_redesign' ? 'Target Chain Configuration' : 'Target Configuration'}
                </h3>

                {/* PDB Upload with Molstar Viewer */}
                <div className="space-y-2">
                    <label className="block text-sm font-medium">Target PDB</label>
                    <p className="text-xs text-slate-500">Trim to binding region for faster design. 32GB GPU fits ~550 residues.</p>
                    <input
                        type="file"
                        accept=".pdb"
                        onChange={(e) => e.target.files?.[0] && handleFileUpload(e.target.files[0])}
                        className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg focus:ring-2 focus:ring-emerald-500 outline-none"
                    />

                    {(targetPdbPath || frameworkPdbUrl) && (
                        <div className="mt-4 space-y-2">
                            {/* Toggle Buttons */}
                            <div className="flex items-center gap-2">
                                {targetPdbPath && (
                                    <button
                                        type="button"
                                        onClick={() => {
                                            if (viewerMode === 'target' && show3DViewer) {
                                                setShow3DViewer(false);
                                            } else {
                                                setViewerMode('target');
                                                setShow3DViewer(true);
                                            }
                                        }}
                                        className={`text-xs px-3 py-1.5 rounded-lg transition-all flex items-center gap-1.5 ${viewerMode === 'target' && show3DViewer
                                            ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/50'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700 border border-slate-700'
                                            }`}
                                    >
                                        🎯 Target 3D
                                    </button>
                                )}
                                {frameworkPdbUrl && (
                                    <button
                                        type="button"
                                        onClick={() => {
                                            if (viewerMode === 'framework' && show3DViewer) {
                                                setShow3DViewer(false);
                                            } else {
                                                setViewerMode('framework');
                                                setShow3DViewer(true);
                                            }
                                        }}
                                        className={`text-xs px-3 py-1.5 rounded-lg transition-all flex items-center gap-1.5 ${viewerMode === 'framework' && show3DViewer
                                            ? 'bg-purple-500/20 text-purple-400 border border-purple-500/50'
                                            : 'bg-slate-800 text-slate-400 hover:bg-slate-700 border border-slate-700'
                                            }`}
                                    >
                                        🧬 Framework 3D
                                    </button>
                                )}
                            </div>
                            {/* 3D Viewer */}
                            {show3DViewer && (
                                <div className="animate-in fade-in slide-in-from-top-2 duration-300">
                                    <div className="text-xs text-slate-500 mb-2">
                                        {viewerMode === 'target' ? '🎯 Target Antigen Preview' : '🧬 VHH Framework Template Preview'}
                                    </div>
                                    <EpitopeMolstarViewer
                                        structureUrl={viewerMode === 'target'
                                            ? `/api/files/read?path=${encodeURIComponent(targetPdbPath)}`
                                            : frameworkPdbUrl!
                                        }
                                        selectedResidues={viewerMode === 'target' ? hotspotSet : new Set<string>()}
                                    />
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Hotspot Residues */}
                <div className="space-y-2">
                    <label className="block text-sm font-medium">Hotspot Residues (optional)</label>
                    <p className="text-xs text-slate-500">Enter: A45,A46,A52 or A45-60 or leave blank for auto-detection</p>
                    <input
                        type="text"
                        value={hotspotResidues}
                        onChange={(e) => setHotspotResidues(e.target.value)}
                        placeholder="A45,A46,A52 or leave blank"
                        className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg focus:ring-2 focus:ring-emerald-500 outline-none"
                    />
                </div>

                {/* Chain Selection */}
                <div className="space-y-2">
                    <label className="block text-sm font-medium">Target Chain(s)</label>
                    <input
                        type="text"
                        value={chains}
                        onChange={(e) => setChains(e.target.value)}
                        placeholder="A"
                        className="w-32 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg focus:ring-2 focus:ring-emerald-500 outline-none"
                    />
                </div>
            </div>

            {/* Section 2: Binder Settings */}
            <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 mb-6">
                <h2 className="text-lg font-semibold flex items-center gap-2">
                    <span className="w-6 h-6 rounded-full bg-emerald-600/20 text-emerald-400 flex items-center justify-center text-sm">2</span>
                    Binder Settings
                </h2>

                {/* Design Mode */}
                <div className="flex gap-4">
                    <button
                        onClick={() => setDesignMode('minibinder')}
                        className={`flex-1 p-4 rounded-lg border-2 transition-colors ${designMode === 'minibinder'
                            ? 'border-emerald-500 bg-emerald-600/20'
                            : 'border-slate-700'
                            }`}
                    >
                        <div className="font-medium">Minibinder</div>
                        <div className="text-sm text-slate-500">60-180 AA globular proteins</div>
                    </button>
                    <button
                        onClick={() => setDesignMode('peptide')}
                        className={`flex-1 p-4 rounded-lg border-2 transition-colors ${designMode === 'peptide'
                            ? 'border-emerald-500 bg-emerald-600/20'
                            : 'border-slate-700'
                            }`}
                    >
                        <div className="font-medium">Peptide</div>
                        <div className="text-sm text-slate-500">8-25 AA linear peptides</div>
                    </button>
                </div>

                {/* Length Range */}
                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="block text-sm font-medium">Min Length (AA)</label>
                        <input
                            type="number"
                            value={binderLengthMin}
                            onChange={(e) => setBinderLengthMin(parseInt(e.target.value) || 60)}
                            min={designMode === 'peptide' ? 4 : 40}
                            max={250}
                            className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg focus:ring-2 focus:ring-emerald-500 outline-none"
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="block text-sm font-medium">Max Length (AA)</label>
                        <input
                            type="number"
                            value={binderLengthMax}
                            onChange={(e) => setBinderLengthMax(parseInt(e.target.value) || 120)}
                            min={designMode === 'peptide' ? 8 : 60}
                            max={250}
                            className="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg focus:ring-2 focus:ring-emerald-500 outline-none"
                        />
                    </div>
                </div>

                {/* Number of Designs */}
                <div className="space-y-2">
                    <label className="block text-sm font-medium">Final Designs to Generate</label>
                    <p className="text-xs text-slate-500">Recommend 100+ for diverse candidates. Script stops when this many pass filters.</p>
                    <input
                        type="number"
                        value={numFinalDesigns}
                        onChange={(e) => setNumFinalDesigns(parseInt(e.target.value) || 100)}
                        min={5}
                        max={1000}
                        className="w-32 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg focus:ring-2 focus:ring-emerald-500 outline-none"
                    />
                </div>
            </div>

            {/* Section 3: Design Algorithm */}
            <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 mb-6">
                <h2 className="text-lg font-semibold flex items-center gap-2">
                    <span className="w-6 h-6 rounded-full bg-emerald-600/20 text-emerald-400 flex items-center justify-center text-sm">3</span>
                    Design Algorithm
                </h2>

                <div className="grid grid-cols-1 gap-2">
                    {DESIGN_ALGORITHMS.map((algo) => (
                        <button
                            key={algo.id}
                            onClick={() => setDesignAlgorithm(algo.id)}
                            className={`p-3 rounded-lg border text-left transition-colors ${designAlgorithm === algo.id
                                ? 'border-emerald-500 bg-emerald-600/20'
                                : 'border-slate-700 hover:bg-slate-800'
                                }`}
                        >
                            <div className="font-medium">{algo.name}</div>
                            <div className="text-sm text-slate-500">{algo.description}</div>
                        </button>
                    ))}
                </div>

                {/* Physics Refinement Panel (OpenMM) */}
                <PhysicsRefinementPanel
                    settings={physicsSettings}
                    onSettingsChange={setPhysicsSettings}
                    isAntibody={designApproach === 'cdr_hallucination'}
                />

                {/* Advanced Settings Toggle */}
                <button
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="text-sm text-emerald-600 hover:text-emerald-700"
                >
                    {showAdvanced ? '▼ Hide' : '▶ Show'} Advanced Settings
                </button>

                {showAdvanced && (
                    <div className="grid grid-cols-2 gap-4 p-4 bg-slate-800/50 rounded-lg">
                        <div className="space-y-2">
                            <label className="flex items-center gap-2">
                                <input
                                    type="checkbox"
                                    checked={useMultimerDesign}
                                    onChange={(e) => setUseMultimerDesign(e.target.checked)}
                                    className="rounded"
                                />
                                <span className="text-sm">Use AF2-Multimer for design</span>
                            </label>
                        </div>
                        <div className="space-y-2">
                            <label className="block text-sm">Design Recycles</label>
                            <input
                                type="number"
                                value={numRecyclesDesign}
                                onChange={(e) => setNumRecyclesDesign(parseInt(e.target.value) || 3)}
                                min={1}
                                max={12}
                                className="w-20 px-2 py-1 bg-slate-800 border border-slate-600 rounded focus:ring-2 focus:ring-emerald-500 outline-none"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="block text-sm">MPNN Weights</label>
                            <select
                                value={mpnnWeights}
                                onChange={(e) => setMpnnWeights(e.target.value as 'original' | 'soluble')}
                                className="px-2 py-1 bg-slate-800 border border-slate-600 rounded focus:ring-2 focus:ring-emerald-500 outline-none"
                            >
                                <option value="soluble">Soluble (recommended)</option>
                                <option value="original">Original</option>
                            </select>
                        </div>
                        <div className="space-y-2">
                            <label className="block text-sm">MPNN Sequences/Trajectory</label>
                            <input
                                type="number"
                                value={numMpnnSequences}
                                onChange={(e) => setNumMpnnSequences(parseInt(e.target.value) || 8)}
                                min={1}
                                max={32}
                                className="w-20 px-2 py-1 bg-slate-800 border border-slate-600 rounded focus:ring-2 focus:ring-emerald-500 outline-none"
                            />
                        </div>
                    </div>
                )}
            </div>

            {/* Section 4: Filters */}
            <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 mb-6">
                <div className="flex items-center justify-between">
                    <h2 className="text-lg font-semibold flex items-center gap-2">
                        <span className="w-6 h-6 rounded-full bg-emerald-600/20 text-emerald-400 flex items-center justify-center text-sm">4</span>
                        Filter Configuration
                    </h2>
                    <button
                        onClick={() => setShowFilters(!showFilters)}
                        className="text-sm text-emerald-600 hover:text-emerald-700"
                    >
                        {showFilters ? 'Hide Details' : 'Show Details'}
                    </button>
                </div>

                {/* Filter Presets */}
                <div className="flex gap-2 flex-wrap">
                    {FILTER_PRESETS.map((preset) => (
                        <button
                            key={preset.id}
                            onClick={() => setFilterPreset(preset.id)}
                            className={`px-3 py-1 rounded-full text-sm transition-colors ${filterPreset === preset.id
                                ? 'bg-emerald-500 text-white'
                                : 'bg-slate-700 hover:bg-slate-600'
                                }`}
                        >
                            {preset.name}
                        </button>
                    ))}
                </div>

                {showFilters && (
                    <div className="grid grid-cols-3 gap-4 p-4 bg-slate-800/50 rounded-lg">
                        <div className="space-y-2">
                            <label className="block text-sm">Min i_pTM</label>
                            <input
                                type="number"
                                value={minIptm}
                                onChange={(e) => setMinIptm(parseFloat(e.target.value) || 0.6)}
                                min={0}
                                max={1}
                                step={0.05}
                                className="w-20 px-2 py-1 bg-slate-800 border border-slate-600 rounded focus:ring-2 focus:ring-emerald-500 outline-none"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="block text-sm">Max Hotspot RMSD (Å)</label>
                            <input
                                type="number"
                                value={maxHotspotRmsd}
                                onChange={(e) => setMaxHotspotRmsd(parseFloat(e.target.value) || 3.0)}
                                min={0}
                                max={10}
                                step={0.5}
                                className="w-20 px-2 py-1 bg-slate-800 border border-slate-600 rounded focus:ring-2 focus:ring-emerald-500 outline-none"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="block text-sm">Min pLDDT</label>
                            <input
                                type="number"
                                value={minPlddt}
                                onChange={(e) => setMinPlddt(parseFloat(e.target.value) || 0.8)}
                                min={0}
                                max={1}
                                step={0.05}
                                className="w-20 px-2 py-1 bg-slate-800 border border-slate-600 rounded focus:ring-2 focus:ring-emerald-500 outline-none"
                            />
                        </div>
                    </div>
                )}
            </div>

            {/* Section 5: Parallelism */}
            <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 mb-6">
                <h2 className="text-lg font-semibold flex items-center gap-2">
                    <span className="w-6 h-6 rounded-full bg-emerald-600/20 text-emerald-400 flex items-center justify-center text-sm">5</span>
                    GPU Parallelism
                </h2>

                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="block text-sm font-medium">Total Trajectories</label>
                        <p className="text-xs text-slate-500">100-10000 typical. More = better sampling.</p>
                        <input
                            type="number"
                            value={totalTrajectories}
                            onChange={(e) => setTotalTrajectories(parseInt(e.target.value) || 100)}
                            min={10}
                            max={10000}
                            className="w-32 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg focus:ring-2 focus:ring-emerald-500 outline-none"
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="block text-sm font-medium">Trajectories per Job</label>
                        <p className="text-xs text-slate-500">Each job runs on one GPU</p>
                        <input
                            type="number"
                            value={trajectoriesPerJob}
                            onChange={(e) => setTrajectoriesPerJob(parseInt(e.target.value) || 25)}
                            min={5}
                            max={100}
                            className="w-32 px-3 py-2 bg-slate-900 border border-slate-700 rounded-lg focus:ring-2 focus:ring-emerald-500 outline-none"
                        />
                    </div>
                </div>

                <div className="text-sm text-slate-500">
                    This will spawn <strong>{Math.ceil(totalTrajectories / trajectoriesPerJob)}</strong> parallel jobs
                </div>
            </div>

            {/* Section 6: Storage Optimization */}
            <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4 mb-6">
                <div className="flex items-center justify-between">
                    <h2 className="text-lg font-semibold flex items-center gap-2">
                        <span className="w-6 h-6 rounded-full bg-emerald-600/20 text-emerald-400 flex items-center justify-center text-sm">6</span>
                        Storage Optimization
                    </h2>
                    <button
                        onClick={() => setShowStorage(!showStorage)}
                        className="text-sm text-emerald-600 hover:text-emerald-700"
                    >
                        {showStorage ? 'Hide' : 'Show'}
                    </button>
                </div>

                <div className="flex gap-2">
                    <button
                        onClick={() => applyStoragePreset('minimal')}
                        className="px-3 py-1 rounded-lg bg-emerald-600/20 text-emerald-400 text-sm hover:bg-emerald-200"
                    >
                        Minimal Storage
                    </button>
                    <button
                        onClick={() => applyStoragePreset('full_debug')}
                        className="px-3 py-1 rounded-lg bg-slate-700 text-slate-300 text-sm hover:bg-gray-200"
                    >
                        Full Debug Output
                    </button>
                </div>

                {showStorage && (
                    <div className="grid grid-cols-2 gap-2 p-4 bg-slate-800/50 rounded-lg">
                        <label className="flex items-center gap-2">
                            <input type="checkbox" checked={zipAnimations} onChange={(e) => setZipAnimations(e.target.checked)} className="rounded" />
                            <span className="text-sm">Zip animations</span>
                        </label>
                        <label className="flex items-center gap-2">
                            <input type="checkbox" checked={zipPlots} onChange={(e) => setZipPlots(e.target.checked)} className="rounded" />
                            <span className="text-sm">Zip plots</span>
                        </label>
                        <label className="flex items-center gap-2">
                            <input type="checkbox" checked={removeUnrelaxedTrajectory} onChange={(e) => setRemoveUnrelaxedTrajectory(e.target.checked)} className="rounded" />
                            <span className="text-sm">Remove unrelaxed trajectories</span>
                        </label>
                        <label className="flex items-center gap-2">
                            <input type="checkbox" checked={removeUnrelaxedComplex} onChange={(e) => setRemoveUnrelaxedComplex(e.target.checked)} className="rounded" />
                            <span className="text-sm">Remove unrelaxed complexes</span>
                        </label>
                        <label className="flex items-center gap-2">
                            <input type="checkbox" checked={removeBinderMonomer} onChange={(e) => setRemoveBinderMonomer(e.target.checked)} className="rounded" />
                            <span className="text-sm">Remove binder monomers</span>
                        </label>
                        <label className="flex items-center gap-2 text-amber-600">
                            <input type="checkbox" checked={saveTrajectoryPickle} onChange={(e) => setSaveTrajectoryPickle(e.target.checked)} className="rounded" />
                            <span className="text-sm">Save trajectory pickles (large!)</span>
                        </label>
                    </div>
                )}
            </div>

            {/* Submit Button */}
            <div className="flex justify-end gap-4">
                <button
                    onClick={onBack}
                    className="px-6 py-3 border border-slate-700 rounded-lg hover:bg-slate-800"
                >
                    Cancel
                </button>
                <button
                    onClick={handleSubmit}
                    disabled={!targetPdbPath || submitMutation.isPending}
                    className="px-6 py-3 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                >
                    {submitMutation.isPending ? (
                        <>
                            <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                            </svg>
                            Submitting...
                        </>
                    ) : (
                        <>Design Minibinders</>
                    )}
                </button>
            </div>

            {/* Template Manager Modal */}
            <TemplateManagerModal
                isOpen={showTemplateManager}
                onClose={() => setShowTemplateManager(false)}
                currentParams={{
                    target_pdb: targetPdbPath,
                    hotspot_residues: hotspotResidues,
                    chains,
                    binder_lengths: `${binderLengthMin}-${binderLengthMax}`,
                    num_final_designs: numFinalDesigns,
                    design_algorithm: designAlgorithm,
                    job_name: jobName,
                }}
                currentModelId="bindcraft"
                currentMode="minibinder"
                onSelect={(template) => {
                    if (template.params.target_pdb) setTargetPdbPath(template.params.target_pdb);
                    if (template.params.hotspot_residues) setHotspotResidues(template.params.hotspot_residues);
                    if (template.params.chains) setChains(template.params.chains);
                    if (template.params.design_algorithm) setDesignAlgorithm(template.params.design_algorithm);
                    if (template.params.num_final_designs) setNumFinalDesigns(template.params.num_final_designs);
                    if (template.params.job_name) setJobName(template.params.job_name);
                    setShowTemplateManager(false);
                }}
            />
        </div>
    );
}

export default BindCraftTemplate;
