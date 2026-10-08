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

    // ============================================================================
    // State: UI
    // ============================================================================
    const [showAdvanced, setShowAdvanced] = useState<boolean>(false);
    const [showFilters, setShowFilters] = useState<boolean>(false);
    const [showStorage, setShowStorage] = useState<boolean>(false);
    const [showTemplateManager, setShowTemplateManager] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);

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

    const handleSubmit = async () => {
        if (!targetPdbPath) {
            setError('Please upload a target PDB file');
            return;
        }

        const binderLengths = `${binderLengthMin}-${binderLengthMax}`;

        const params = {
            workflow: 'bindcraft',
            name: jobName || `BindCraft_${new Date().toISOString().slice(0, 10)}`,
            rfd_mode: 'bindcraft',
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
        <div className="max-w-4xl mx-auto p-6 space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <button onClick={onBack} className="p-2 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg">
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
                        </svg>
                    </button>
                    <div>
                        <h1 className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">BindCraft</h1>
                        <p className="text-sm text-gray-500">De novo minibinder design with ~50% success rate</p>
                    </div>
                </div>
                <button
                    onClick={() => setShowTemplateManager(true)}
                    className="px-3 py-2 text-sm bg-gray-100 dark:bg-gray-800 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700"
                >
                    Templates
                </button>
            </div>

            {error && (
                <div className="p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400">
                    {error}
                </div>
            )}

            {/* Job Name */}
            <div className="space-y-2">
                <label className="block text-sm font-medium">Job Name</label>
                <input
                    type="text"
                    value={jobName}
                    onChange={(e) => setJobName(e.target.value)}
                    placeholder="BindCraft_PDL1_binder"
                    className="w-full px-3 py-2 border rounded-lg dark:bg-gray-800 dark:border-gray-700"
                />
            </div>

            {/* Section 1: Target Configuration */}
            <div className="bg-white dark:bg-gray-900 rounded-xl shadow-lg p-6 space-y-4">
                <h2 className="text-lg font-semibold flex items-center gap-2">
                    <span className="w-6 h-6 rounded-full bg-emerald-100 dark:bg-emerald-900 text-emerald-600 dark:text-emerald-400 flex items-center justify-center text-sm">1</span>
                    Target Configuration
                </h2>

                {/* PDB Upload with Molstar Viewer */}
                <div className="space-y-2">
                    <label className="block text-sm font-medium">Target PDB</label>
                    <p className="text-xs text-gray-500">Trim to binding region for faster design. 32GB GPU fits ~550 residues.</p>
                    <input
                        type="file"
                        accept=".pdb"
                        onChange={(e) => e.target.files?.[0] && handleFileUpload(e.target.files[0])}
                        className="w-full px-3 py-2 border rounded-lg dark:bg-gray-800 dark:border-gray-700"
                    />

                    {targetPdbPath && (
                        <div className="mt-4">
                            <EpitopeMolstarViewer
                                structureUrl={`/api/files/read?path=${encodeURIComponent(targetPdbPath)}`}
                                selectedResidues={hotspotSet}
                            />
                        </div>
                    )}
                </div>

                {/* Hotspot Residues */}
                <div className="space-y-2">
                    <label className="block text-sm font-medium">Hotspot Residues (optional)</label>
                    <p className="text-xs text-gray-500">Enter: A45,A46,A52 or A45-60 or leave blank for auto-detection</p>
                    <input
                        type="text"
                        value={hotspotResidues}
                        onChange={(e) => setHotspotResidues(e.target.value)}
                        placeholder="A45,A46,A52 or leave blank"
                        className="w-full px-3 py-2 border rounded-lg dark:bg-gray-800 dark:border-gray-700"
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
                        className="w-32 px-3 py-2 border rounded-lg dark:bg-gray-800 dark:border-gray-700"
                    />
                </div>
            </div>

            {/* Section 2: Binder Settings */}
            <div className="bg-white dark:bg-gray-900 rounded-xl shadow-lg p-6 space-y-4">
                <h2 className="text-lg font-semibold flex items-center gap-2">
                    <span className="w-6 h-6 rounded-full bg-emerald-100 dark:bg-emerald-900 text-emerald-600 dark:text-emerald-400 flex items-center justify-center text-sm">2</span>
                    Binder Settings
                </h2>

                {/* Design Mode */}
                <div className="flex gap-4">
                    <button
                        onClick={() => setDesignMode('minibinder')}
                        className={`flex-1 p-4 rounded-lg border-2 transition-colors ${designMode === 'minibinder'
                                ? 'border-emerald-500 bg-emerald-50 dark:bg-emerald-900/20'
                                : 'border-gray-200 dark:border-gray-700'
                            }`}
                    >
                        <div className="font-medium">Minibinder</div>
                        <div className="text-sm text-gray-500">60-180 AA globular proteins</div>
                    </button>
                    <button
                        onClick={() => setDesignMode('peptide')}
                        className={`flex-1 p-4 rounded-lg border-2 transition-colors ${designMode === 'peptide'
                                ? 'border-emerald-500 bg-emerald-50 dark:bg-emerald-900/20'
                                : 'border-gray-200 dark:border-gray-700'
                            }`}
                    >
                        <div className="font-medium">Peptide</div>
                        <div className="text-sm text-gray-500">8-25 AA linear peptides</div>
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
                            className="w-full px-3 py-2 border rounded-lg dark:bg-gray-800 dark:border-gray-700"
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
                            className="w-full px-3 py-2 border rounded-lg dark:bg-gray-800 dark:border-gray-700"
                        />
                    </div>
                </div>

                {/* Number of Designs */}
                <div className="space-y-2">
                    <label className="block text-sm font-medium">Final Designs to Generate</label>
                    <p className="text-xs text-gray-500">Recommend 100+ for diverse candidates. Script stops when this many pass filters.</p>
                    <input
                        type="number"
                        value={numFinalDesigns}
                        onChange={(e) => setNumFinalDesigns(parseInt(e.target.value) || 100)}
                        min={5}
                        max={1000}
                        className="w-32 px-3 py-2 border rounded-lg dark:bg-gray-800 dark:border-gray-700"
                    />
                </div>
            </div>

            {/* Section 3: Design Algorithm */}
            <div className="bg-white dark:bg-gray-900 rounded-xl shadow-lg p-6 space-y-4">
                <h2 className="text-lg font-semibold flex items-center gap-2">
                    <span className="w-6 h-6 rounded-full bg-emerald-100 dark:bg-emerald-900 text-emerald-600 dark:text-emerald-400 flex items-center justify-center text-sm">3</span>
                    Design Algorithm
                </h2>

                <div className="grid grid-cols-1 gap-2">
                    {DESIGN_ALGORITHMS.map((algo) => (
                        <button
                            key={algo.id}
                            onClick={() => setDesignAlgorithm(algo.id)}
                            className={`p-3 rounded-lg border text-left transition-colors ${designAlgorithm === algo.id
                                    ? 'border-emerald-500 bg-emerald-50 dark:bg-emerald-900/20'
                                    : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800'
                                }`}
                        >
                            <div className="font-medium">{algo.name}</div>
                            <div className="text-sm text-gray-500">{algo.description}</div>
                        </button>
                    ))}
                </div>

                {/* Advanced Settings Toggle */}
                <button
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="text-sm text-emerald-600 hover:text-emerald-700"
                >
                    {showAdvanced ? '▼ Hide' : '▶ Show'} Advanced Settings
                </button>

                {showAdvanced && (
                    <div className="grid grid-cols-2 gap-4 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
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
                                className="w-20 px-2 py-1 border rounded dark:bg-gray-700 dark:border-gray-600"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="block text-sm">MPNN Weights</label>
                            <select
                                value={mpnnWeights}
                                onChange={(e) => setMpnnWeights(e.target.value as 'original' | 'soluble')}
                                className="px-2 py-1 border rounded dark:bg-gray-700 dark:border-gray-600"
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
                                className="w-20 px-2 py-1 border rounded dark:bg-gray-700 dark:border-gray-600"
                            />
                        </div>
                    </div>
                )}
            </div>

            {/* Section 4: Filters */}
            <div className="bg-white dark:bg-gray-900 rounded-xl shadow-lg p-6 space-y-4">
                <div className="flex items-center justify-between">
                    <h2 className="text-lg font-semibold flex items-center gap-2">
                        <span className="w-6 h-6 rounded-full bg-emerald-100 dark:bg-emerald-900 text-emerald-600 dark:text-emerald-400 flex items-center justify-center text-sm">4</span>
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
                                    : 'bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700'
                                }`}
                        >
                            {preset.name}
                        </button>
                    ))}
                </div>

                {showFilters && (
                    <div className="grid grid-cols-3 gap-4 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
                        <div className="space-y-2">
                            <label className="block text-sm">Min i_pTM</label>
                            <input
                                type="number"
                                value={minIptm}
                                onChange={(e) => setMinIptm(parseFloat(e.target.value) || 0.6)}
                                min={0}
                                max={1}
                                step={0.05}
                                className="w-20 px-2 py-1 border rounded dark:bg-gray-700 dark:border-gray-600"
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
                                className="w-20 px-2 py-1 border rounded dark:bg-gray-700 dark:border-gray-600"
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
                                className="w-20 px-2 py-1 border rounded dark:bg-gray-700 dark:border-gray-600"
                            />
                        </div>
                    </div>
                )}
            </div>

            {/* Section 5: Parallelism */}
            <div className="bg-white dark:bg-gray-900 rounded-xl shadow-lg p-6 space-y-4">
                <h2 className="text-lg font-semibold flex items-center gap-2">
                    <span className="w-6 h-6 rounded-full bg-emerald-100 dark:bg-emerald-900 text-emerald-600 dark:text-emerald-400 flex items-center justify-center text-sm">5</span>
                    GPU Parallelism
                </h2>

                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="block text-sm font-medium">Total Trajectories</label>
                        <p className="text-xs text-gray-500">100-10000 typical. More = better sampling.</p>
                        <input
                            type="number"
                            value={totalTrajectories}
                            onChange={(e) => setTotalTrajectories(parseInt(e.target.value) || 100)}
                            min={10}
                            max={10000}
                            className="w-32 px-3 py-2 border rounded-lg dark:bg-gray-800 dark:border-gray-700"
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="block text-sm font-medium">Trajectories per Job</label>
                        <p className="text-xs text-gray-500">Each job runs on one GPU</p>
                        <input
                            type="number"
                            value={trajectoriesPerJob}
                            onChange={(e) => setTrajectoriesPerJob(parseInt(e.target.value) || 25)}
                            min={5}
                            max={100}
                            className="w-32 px-3 py-2 border rounded-lg dark:bg-gray-800 dark:border-gray-700"
                        />
                    </div>
                </div>

                <div className="text-sm text-gray-500">
                    This will spawn <strong>{Math.ceil(totalTrajectories / trajectoriesPerJob)}</strong> parallel jobs
                </div>
            </div>

            {/* Section 6: Storage Optimization */}
            <div className="bg-white dark:bg-gray-900 rounded-xl shadow-lg p-6 space-y-4">
                <div className="flex items-center justify-between">
                    <h2 className="text-lg font-semibold flex items-center gap-2">
                        <span className="w-6 h-6 rounded-full bg-emerald-100 dark:bg-emerald-900 text-emerald-600 dark:text-emerald-400 flex items-center justify-center text-sm">6</span>
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
                        className="px-3 py-1 rounded-lg bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 text-sm hover:bg-emerald-200"
                    >
                        Minimal Storage
                    </button>
                    <button
                        onClick={() => applyStoragePreset('full_debug')}
                        className="px-3 py-1 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 text-sm hover:bg-gray-200"
                    >
                        Full Debug Output
                    </button>
                </div>

                {showStorage && (
                    <div className="grid grid-cols-2 gap-2 p-4 bg-gray-50 dark:bg-gray-800 rounded-lg">
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
                    className="px-6 py-3 border border-gray-300 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800"
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
