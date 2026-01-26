import React, { useState, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob, uploadFile, extractChain } from '../lib/api';
import { useNavigate } from 'react-router-dom';
import { parsePDBFile, type Chain } from '../utils/pdbUtils';
import { EpitopeSelector } from './EpitopeSelector';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { TargetAntigenSelector } from './TargetAntigenSelector';
import { DesignModeSelector } from './DesignModeSelector';
import { PPIFlowSettingsFields, QualitySettingsPanel, PRESETS, type QualitySettings, type QualityPreset } from './QualitySettingsPanel';
import { TemplateManagerModal } from './TemplateManagerModal';
import { FrameworkBrowser, type SelectedFramework } from './FrameworkBrowser';
import { FrameworkEditor, type FrameworkEditorState } from './FrameworkEditor';
import { PhysicsRefinementPanel, type PhysicsRefinementSettings, DEFAULT_SETTINGS as PHYSICS_DEFAULTS } from './PhysicsRefinementPanel';

interface AntibodyDenovoTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, any>;
}

export const AntibodyDenovoTemplate: React.FC<AntibodyDenovoTemplateProps> = ({ onBack, initialValues }) => {
    const [jobName, setJobName] = useState('antibody_design');
    const [pinnedGpus, setPinnedGpus] = useState<number[]>(initialValues?.pinned_gpus ?? []);
    const [lockGpus, setLockGpus] = useState(false);
    const [targetPdb, setTargetPdb] = useState<File | null>(null);
    const [targetSource, setTargetSource] = useState<{ type: string; url?: string; path?: string; designId?: string; pdbId?: string; name?: string } | null>(null);
    const [numDesigns, setNumDesigns] = useState(10);
    const [seqDesigner, setSeqDesigner] = useState<'fampnn' | 'antifold' | 'proteinmpnn'>('fampnn');
    const [fampnnConstraintMode, setFampnnConstraintMode] = useState<'generic' | 'antibody'>('antibody');
    const [useAntiberty, setUseAntiberty] = useState(false);  // Disabled by default, planned for removal
    const [useThermoMPNN, setUseThermoMPNN] = useState(true);  // Controlled via qualitySettings.run_thermompnn
    const [runFrustrampnn, setRunFrustrampnn] = useState(false);
    // explorationMode is now always true - parallelism controlled via parallelMode
    const [seqsPerDesign, setSeqsPerDesign] = useState(8); // Number of sequence variants per backbone

    // Orchestrator parallelism settings
    const [parallelMode, setParallelMode] = useState<'standard' | 'full_orchestrator'>('standard');
    const [designsPerJob, setDesignsPerJob] = useState(5); // Backbones per child job
    const [pdBsPerJob, setPdBsPerJob] = useState(5); // FAMPNN PDBs per child job
    const [seqsPerBoltzJob, setSeqsPerBoltzJob] = useState(10); // Sequences per Boltz validation job

    // Template manager
    const [showTemplateManager, setShowTemplateManager] = useState(false);

    // Design mode settings
    type DesignMode = 'cdr_only' | 'cdr_selective' | 'framework_allowed' | 'full_design';
    const [designMode, setDesignMode] = useState<DesignMode>('cdr_only');
    const [selectedCDRLoops, setSelectedCDRLoops] = useState<Set<string>>(new Set(['H1', 'H2', 'H3', 'L1', 'L2', 'L3']));
    const [protectTetrad, setProtectTetrad] = useState(true);

    // Quality settings
    const [qualityPreset, setQualityPreset] = useState<QualityPreset>('balanced');
    const [qualitySettings, setQualitySettings] = useState<QualitySettings>(PRESETS.balanced);

    // Physics refinement settings (OpenMM)
    const [physicsSettings, setPhysicsSettings] = useState<PhysicsRefinementSettings>(PHYSICS_DEFAULTS);

    // Framework selection - preset, custom, or SAbDab
    type FrameworkType = 'standard-fv' | 'nanobody' | 'custom' | 'sabdab';
    const [frameworkType, setFrameworkType] = useState<FrameworkType>('standard-fv');
    const [customFrameworkFile, setCustomFrameworkFile] = useState<File | null>(null);
    const [customFrameworkPath, setCustomFrameworkPath] = useState<string | null>(null);
    const [sabdabFramework, setSabdabFramework] = useState<SelectedFramework | null>(null);

    // Framework protection settings
    const [frameworkProtection, setFrameworkProtection] = useState<FrameworkEditorState>({
        protectedPositions: [],
        protectTetrad: true,
        protectDisulfides: true,
        protectFrContacts: false
    });

    const [isUploading, setIsUploading] = useState(false);
    const [uploadedPath, setUploadedPath] = useState<string | null>(null);

    const [parsedChains, setParsedChains] = useState<Chain[]>([]);
    const [selectedChain, setSelectedChain] = useState<string | null>(null);
    const [selectedResidues, setSelectedResidues] = useState<Set<string>>(new Set());
    const [isParsing, setIsParsing] = useState(false);
    const [pdbBlobUrl, setPdbBlobUrl] = useState<string | null>(null);
    const [show3DViewer, setShow3DViewer] = useState(false);  // 3D viewer toggle, off by default

    // Viewer mode - toggle between target and framework preview
    type ViewerMode = 'target' | 'framework';
    const [viewerMode, setViewerMode] = useState<ViewerMode>('target');
    const [frameworkPdbUrl, setFrameworkPdbUrl] = useState<string | null>(null);

    // Optional DNA/RNA sequence for complex prediction (when protein binds nucleic acid)
    const [targetDnaSeq, setTargetDnaSeq] = useState<string>('');
    const [showDnaInput, setShowDnaInput] = useState(false);

    // Debug mode settings - hidden by default
    const [showDebugSettings, setShowDebugSettings] = useState(false);
    const [skipRFantibody, setSkipRFantibody] = useState(false);
    const [rfantibodyInputPdbs, setRfantibodyInputPdbs] = useState<string>('');
    const [skipFampnn, setSkipFampnn] = useState(false);
    const [fampnnCollectedPdbs, setFampnnCollectedPdbs] = useState<string>('');
    const [customOutputDir, setCustomOutputDir] = useState<string>('');

    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const submitMutation = useMutation({
        mutationFn: async (data: any) => submitJob(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        }
    });

    // Initialize from initialValues (Clone Job)
    useEffect(() => {
        if (initialValues) {
            console.log('[ANTIBODY_DENOVO] Initializing from values:', initialValues);

            // Basic params
            if (initialValues.name) setJobName(initialValues.name); // Job name usually comes from wrapper but might be passed
            if (initialValues.rfantibody_num_designs) setNumDesigns(initialValues.rfantibody_num_designs);
            if (initialValues.seqs_per_design) setSeqsPerDesign(initialValues.seqs_per_design);
            // exploration_mode is now always true - controlled via parallel_mode instead

            // Booleans
            if (initialValues.run_immunogenicity_scoring !== undefined) setUseAntiberty(initialValues.run_immunogenicity_scoring);
            if (initialValues.run_stability_scoring !== undefined) setUseThermoMPNN(initialValues.run_stability_scoring);
            if (initialValues.run_frustrampnn !== undefined) setRunFrustrampnn(initialValues.run_frustrampnn);
            // Handling renamed/mapped boolean params if any
            if (initialValues.use_antiberty !== undefined) setUseAntiberty(initialValues.use_antiberty);
            if (initialValues.use_thermompnn !== undefined) setUseThermoMPNN(initialValues.use_thermompnn);

            // Sequence Designer
            if (initialValues.seq_design_fampnn) setSeqDesigner('fampnn');
            else if (initialValues.seq_design_antifold) setSeqDesigner('antifold');
            else if (initialValues.seq_design_proteinmpnn) setSeqDesigner('proteinmpnn');
            else if (initialValues.seq_designer) setSeqDesigner(initialValues.seq_designer); // Direct name
            if (initialValues.fampnn_constraint_mode) {
                setFampnnConstraintMode(initialValues.fampnn_constraint_mode);
            }

            // Framework
            if (initialValues.framework_type) setFrameworkType(initialValues.framework_type);

            // Target PDB
            // If checking target_pdb path, we can try to set it as a "preset" or "run" source so it fetches
            if (initialValues.target_pdb) {
                const path = initialValues.target_pdb;
                const name = path.split('/').pop() || 'target.pdb';

                // Check if this is an RCSB cached file (e.g., /path/to/rcsb/6pax.pdb)
                const rcsbMatch = path.match(/\/rcsb\/([a-z0-9]{4})\.pdb$/i);
                let fetchUrl: string;
                let sourceType: string;

                if (rcsbMatch) {
                    // RCSB cached file - use RCSB API endpoint
                    const pdbId = rcsbMatch[1].toUpperCase();
                    fetchUrl = `/api/rcsb/${pdbId}/file`;
                    sourceType = 'rcsb';
                    console.log(`[CLONE] Detected RCSB PDB: ${pdbId}`);
                } else {
                    // Regular uploaded/preset file
                    fetchUrl = `/api/files/download?path=${encodeURIComponent(path)}`;
                    sourceType = 'preset';
                }

                setTargetSource({
                    type: sourceType,
                    path: path,
                    url: fetchUrl,
                    name: name
                });
                // Trigger fetch
                fetch(fetchUrl)
                    .then(res => res.blob())
                    .then(blob => {
                        const file = new File([blob], name, { type: 'chemical/x-pdb' });
                        setTargetPdb(file);
                        setUploadedPath(path); // It's already on server
                    })
                    .catch(e => console.error("Failed to load target PDB from clone", e));
            }

            // Epitopes & Chain
            if (initialValues.antigen_chains) setSelectedChain(initialValues.antigen_chains);
            if (initialValues.epitope_residues) {
                const residues = new Set((initialValues.epitope_residues as string).split(','));
                setSelectedResidues(residues);
            }
        }
    }, [initialValues]);

    // Auto-parse PDB when file is selected
    useEffect(() => {
        // Clean up old blob URL
        if (pdbBlobUrl) {
            URL.revokeObjectURL(pdbBlobUrl);
            setPdbBlobUrl(null);
        }

        if (targetPdb) {
            setIsParsing(true);

            // Create blob URL for Molstar viewer
            const blobUrl = URL.createObjectURL(targetPdb);
            setPdbBlobUrl(blobUrl);

            parsePDBFile(targetPdb)
                .then(result => {
                    setParsedChains(result.chains);
                    // Auto-select first chain with most residues IF NOT ALREADY SELECTED (e.g. by clone)
                    // If clone set selectedChain, verify it exists, otherwise fallback
                    if (result.chains.length > 0) {
                        const chainIds = result.chains.map(c => c.id);
                        if (!selectedChain || !chainIds.includes(selectedChain)) {
                            const longestChain = result.chains.reduce((a, b) =>
                                a.length > b.length ? a : b
                            );
                            setSelectedChain(longestChain.id);
                            // Clear selection only if we CHANGED the chain automatically
                            if (!initialValues) setSelectedResidues(new Set());
                        }
                    }
                    if (!uploadedPath && !initialValues) setUploadedPath(null); // Only clear if new upload
                    console.log('[ANTIBODY_DENOVO] Parsed PDB:', result.chains.map(c => `${c.id}:${c.length}aa`));
                })
                .catch(err => {
                    console.error('[ANTIBODY_DENOVO] Failed to parse PDB:', err);
                    setParsedChains([]);
                })
                .finally(() => setIsParsing(false));
        } else {
            setParsedChains([]);
            setSelectedChain(null);
            setSelectedResidues(new Set());
        }
    }, [targetPdb]);

    const handleFileUpload = async (file: File) => {
        setIsUploading(true);
        try {
            const response = await uploadFile('inputs/antibody', file);
            const path = `inputs/antibody/${file.name}`;
            setUploadedPath(path);
            console.log('[ANTIBODY_DENOVO] File uploaded:', path, response);
            return path;
        } catch (error) {
            console.error('[ANTIBODY_DENOVO] Upload failed:', error);
            alert('Failed to upload PDB file. Please try again.');
            throw error;
        } finally {
            setIsUploading(false);
        }
    };

    const handleSubmit = async () => {
        // When skipping early steps, target PDB and epitope are not required
        const skippingEarlySteps = skipRFantibody || skipFampnn;

        if (!skippingEarlySteps && !targetPdb) {
            alert('Please upload a target PDB file');
            return;
        }
        if (!skippingEarlySteps && selectedResidues.size === 0) {
            alert('Please select at least one epitope residue');
            return;
        }

        // Validate skip inputs have paths
        if (skipRFantibody && !rfantibodyInputPdbs.trim()) {
            alert('Please provide a path to backbone PDBs for Skip RFantibody');
            return;
        }
        if (skipFampnn && !fampnnCollectedPdbs.trim()) {
            alert('Please provide a path to sequenced PDBs for Skip FAMPNN');
            return;
        }

        try {
            // Step 1: Determine PDB path based on source
            // - targetSource.path: file from previous run, preset, or RCSB PDB  
            // - uploadedPath: manually uploaded file (already on server)
            // - handleFileUpload: new file upload (needs to be uploaded first)
            // - When skipping, use a placeholder or the input dir path
            let pdbPath = targetSource?.path || uploadedPath;
            if (!pdbPath && targetPdb) {
                pdbPath = await handleFileUpload(targetPdb);
            }
            // When skipping, don't require a target PDB
            if (!pdbPath && skippingEarlySteps) {
                pdbPath = skipRFantibody ? rfantibodyInputPdbs : fampnnCollectedPdbs;
            }

            if (!pdbPath) {
                alert('Failed to determine PDB file path');
                return;
            }

            // Step 1b: Extract selected chain if multi-chain PDB with specific chain selected
            // This ensures only the target chain is sent to design pipelines
            if (selectedChain && parsedChains.length > 1) {
                console.log(`[ANTIBODY_DENOVO] Extracting chain ${selectedChain} from multi-chain PDB`);
                try {
                    const extractResult = await extractChain(pdbPath, selectedChain);
                    pdbPath = extractResult.data.output_path;
                    console.log(`[ANTIBODY_DENOVO] Extracted chain to: ${pdbPath}`);
                } catch (err) {
                    console.error('[ANTIBODY_DENOVO] Chain extraction failed:', err);
                    alert(`Failed to extract chain ${selectedChain}: ${err}`);
                    return;
                }
            }

            // Format selected residues for backend
            const epitopeString = Array.from(selectedResidues).sort().join(',');

            // Determine pipeline steps
            const pipelineSteps = ['rfantibody', seqDesigner];
            if (qualitySettings.run_maturation) pipelineSteps.push('ppiflow');
            if (useAntiberty) pipelineSteps.push('antiberty');
            if (useThermoMPNN) pipelineSteps.push('thermompnn');
            pipelineSteps.push('boltz2'); // Boltz2 is always run last for structure validation

            // Step 2: Upload custom framework if provided
            let frameworkPath = customFrameworkPath;
            if (frameworkType === 'custom' && customFrameworkFile && !frameworkPath) {
                const response = await uploadFile('inputs/antibody', customFrameworkFile);
                frameworkPath = `inputs/antibody/${customFrameworkFile.name}`;
                setCustomFrameworkPath(frameworkPath);
                console.log('[ANTIBODY_DENOVO] Custom framework uploaded:', frameworkPath, response);
            }

            // Step 3: Submit job with uploaded file path
            const jobData = {
                name: jobName,
                model_id: 'template_antibody_denovo',
                mode: 'antibody_denovo_pipeline', // Matches main.nf logic
                pinned_gpu: pinnedGpus.length === 1 ? pinnedGpus[0] : null,
                params: {
                    target_pdb: pdbPath,
                    pdb_source: 'upload',
                    epitope_residues: epitopeString,
                    antigen_chains: selectedChain || undefined, // Send selected chain
                    pinned_gpus: pinnedGpus.length > 0 ? pinnedGpus : undefined,
                    lock_gpus: lockGpus && pinnedGpus.length > 0, // GPU locking
                    // Framework configuration
                    framework_type: frameworkType,
                    framework_pdb: frameworkPath || undefined, // Only if custom
                    // Pipeline configuration
                    rfd_mode: 'antibody_denovo_pipeline', // Explicitly set for backend mapping
                    antibody_pipeline_steps: pipelineSteps,
                    rfantibody_num_designs: numDesigns,
                    seq_design_fampnn: seqDesigner === 'fampnn',
                    seq_design_antifold: seqDesigner === 'antifold',
                    seq_design_proteinmpnn: seqDesigner === 'proteinmpnn',
                    run_immunogenicity_scoring: useAntiberty,
                    run_stability_scoring: useThermoMPNN,
                    run_structure_validation: true, // Boltz2 is always run
                    run_frustrampnn: runFrustrampnn,
                    exploration_mode: true, // Always parallel - granularity controlled via parallel_mode
                    seqs_per_design: seqsPerDesign, // Number of sequence variants per backbone
                    // Optional DNA sequence for complex prediction
                    target_dna_seq: targetDnaSeq.trim() || undefined,
                    // Design mode settings
                    antibody_design_mode: designMode,
                    antibody_design_loops: Array.from(selectedCDRLoops).sort().join(','),
                    protect_vhh_tetrad: protectTetrad,
                    antibody_chains: frameworkType === 'nanobody' ? 'H' : 'H,L',
                    // Quality settings - RFantibody (backbone diffusion)
                    rfantibody_diffusion_steps: qualitySettings.rfantibody_diffusion_steps,
                    rfantibody_noise_scale_ca: qualitySettings.rfantibody_noise_scale_ca,
                    rfantibody_noise_scale_frame: qualitySettings.rfantibody_noise_scale_frame,
                    rfantibody_guide_scale: qualitySettings.rfantibody_guide_scale,
                    // Quality settings - Boltz-2 (structure validation)
                    boltz_sampling_steps: qualitySettings.boltz_sampling_steps,
                    boltz_recycling_steps: qualitySettings.boltz_recycling_steps,
                    boltz_num_samples: qualitySettings.boltz_num_samples,
                    boltz_use_potentials: qualitySettings.boltz_use_potentials,
                    boltz_use_msa: qualitySettings.boltz_use_msa,
                    // Boltz-2 affinity prediction
                    boltz_predict_affinity: qualitySettings.boltz_predict_affinity,
                    boltz_diffusion_samples_affinity: qualitySettings.boltz_diffusion_samples_affinity,
                    // Quality settings - FAMPNN (sequence design)
                    fampnn_temperature: qualitySettings.fampnn_temperature,
                    fampnn_num_steps: qualitySettings.fampnn_num_steps,
                    fampnn_psce_threshold: qualitySettings.fampnn_psce_threshold,
                    fampnn_constraint_mode: seqDesigner === 'fampnn' ? fampnnConstraintMode : undefined,
                    // PPIFlow maturation settings
                    run_maturation: qualitySettings.run_maturation,
                    ppiflow_start_t: qualitySettings.ppiflow_start_t,
                    ppiflow_samples_per_target: qualitySettings.ppiflow_samples_per_target,
                    ppiflow_retry_limit: qualitySettings.ppiflow_retry_limit,
                    ppiflow_config: qualitySettings.ppiflow_config,
                    ppiflow_weights_dir: qualitySettings.ppiflow_weights_dir,
                    ppiflow_checkpoint_path: qualitySettings.ppiflow_checkpoint_path,
                    maturation_anchor_threshold: qualitySettings.maturation_anchor_threshold,
                    maturation_anchor_distance_cutoff: qualitySettings.maturation_anchor_distance_cutoff,
                    maturation_min_improvement: qualitySettings.maturation_min_improvement,
                    maturation_redesign_temp: qualitySettings.maturation_redesign_temp,
                    maturation_redesign_steps: qualitySettings.maturation_redesign_steps,
                    maturation_design_mode: qualitySettings.maturation_design_mode,
                    maturation_designs_per_job: qualitySettings.maturation_designs_per_job,
                    maturation_filter_percentile: qualitySettings.maturation_filter_percentile,
                    ppiflow_checkpoint: qualitySettings.ppiflow_checkpoint,
                    ppiflow_antigen_chain: qualitySettings.ppiflow_antigen_chain,
                    ppiflow_heavy_chain: qualitySettings.ppiflow_heavy_chain,
                    ppiflow_light_chain: qualitySettings.ppiflow_light_chain,
                    // Pre-Boltz filtering (saves compute)
                    fampnn_max_psce: qualitySettings.fampnn_max_psce,
                    fampnn_max_residue_psce: qualitySettings.fampnn_max_residue_psce,
                    // ThermoMPNN stability scoring (before Boltz when enabled)
                    run_thermompnn: qualitySettings.run_thermompnn,
                    thermompnn_max_ddg: qualitySettings.thermompnn_max_ddg,
                    // AF2 Backprop CDR refinement (after ThermoMPNN, before Boltz)
                    run_af2_backprop: qualitySettings.run_af2_backprop,
                    af2_backprop_soft_iters: qualitySettings.af2_backprop_soft_iters,
                    af2_backprop_temp_iters: qualitySettings.af2_backprop_temp_iters,
                    af2_backprop_hard_iters: qualitySettings.af2_backprop_hard_iters,
                    af2_backprop_num_recycles: qualitySettings.af2_backprop_num_recycles,
                    af2_backprop_learning_rate: qualitySettings.af2_backprop_learning_rate,
                    af2_backprop_use_multimer: qualitySettings.af2_backprop_use_multimer,
                    af2_backprop_num_models: qualitySettings.af2_backprop_num_models,
                    af2_backprop_loss_plddt: qualitySettings.af2_backprop_loss_plddt,
                    af2_backprop_loss_pae: qualitySettings.af2_backprop_loss_pae,
                    af2_backprop_loss_contact: qualitySettings.af2_backprop_loss_contact,
                    // Post-Boltz validation filtering (applied after Boltz-2 structure prediction)
                    boltz_max_binder_rmsd: qualitySettings.boltz_max_binder_rmsd,
                    boltz_min_ptm_interface: qualitySettings.boltz_min_ptm_interface,
                    // Orchestrator parallelism mode
                    parallel_mode: parallelMode,
                    designs_per_job: designsPerJob,
                    seqs_per_job: pdBsPerJob,
                    seqs_per_boltz_job: seqsPerBoltzJob,
                    // Debug: Skip step settings
                    skip_rfantibody: skipRFantibody || undefined,
                    rfantibody_input_pdbs: rfantibodyInputPdbs.trim() || undefined,
                    fampnn_collected_pdbs: fampnnCollectedPdbs.trim() || undefined,
                    // Debug: Custom output directory
                    out_dir: customOutputDir.trim() || undefined,
                    // Physics refinement (OpenMM)
                    openmm_enabled: physicsSettings.enabled,
                    openmm_compute_tier: physicsSettings.computeTier,
                    openmm_cdr_only: physicsSettings.cdrOnly,
                    openmm_restraint_mode: physicsSettings.restraintMode,
                    openmm_mmgbsa_mode: physicsSettings.mmgbsaMode,
                    openmm_force_field: physicsSettings.forceField,
                    openmm_top_n_percentage: physicsSettings.topNPercentage,
                    openmm_max_iterations: physicsSettings.maxIterations,
                    openmm_tolerance: physicsSettings.tolerance,
                    openmm_restraint_strength: physicsSettings.restraintStrength,
                    openmm_implicit_solvent: physicsSettings.implicitSolvent,
                    openmm_platform: physicsSettings.platform,
                }
            };

            await submitMutation.mutateAsync(jobData);
        } catch (error) {
            console.error('[ANTIBODY_DENOVO] Submission failed', error);
        }
    };

    return (
        <div className="bg-slate-800/30 border border-slate-700 rounded-xl p-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
            {/* Header */}
            <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                    <button
                        onClick={onBack}
                        className="p-2 hover:bg-slate-700 rounded-lg transition-colors"
                    >
                        ← Back
                    </button>
                    <div>
                        <h2 className="text-lg font-semibold text-slate-200">De Novo Antibody Design</h2>
                        <p className="text-sm text-slate-500">Generate novel antibodies targeting an antigen</p>
                    </div>
                </div>
            </div>

            {/* Pipeline Visualization */}
            <div className="mb-6 p-4 bg-slate-900/50 rounded-lg border border-slate-700/50">
                <h3 className="text-sm font-medium text-slate-400 mb-3">Workflow Pipeline</h3>
                <div className="flex items-center gap-2 flex-wrap">
                    {(() => {
                        // Color classes must be complete strings for Tailwind purging
                        const colorClasses: Record<string, string> = {
                            emerald: 'bg-emerald-500/20 text-emerald-400',
                            blue: 'bg-blue-500/20 text-blue-400',
                            purple: 'bg-purple-500/20 text-purple-400',
                            amber: 'bg-amber-500/20 text-amber-400',
                            rose: 'bg-rose-500/20 text-rose-400',
                            teal: 'bg-teal-500/20 text-teal-400',
                        };

                        const steps: Array<{ name: string; colorKey: string }> = [
                            { name: 'RFantibody', colorKey: 'emerald' },
                            { name: seqDesigner.toUpperCase(), colorKey: 'blue' },
                        ];
                        if (qualitySettings.run_maturation) {
                            steps.push({ name: 'PPIFlow', colorKey: 'teal' });
                        }
                        steps.push({ name: 'Boltz2', colorKey: 'purple' });
                        if (useAntiberty) steps.push({ name: 'AntiBERTy', colorKey: 'amber' });
                        if (useThermoMPNN) steps.push({ name: 'ThermoMPNN', colorKey: 'rose' });

                        return steps.map((step, idx) => (
                            <React.Fragment key={step.name}>
                                {idx > 0 && <span className="text-slate-600">→</span>}
                                <div className={`${colorClasses[step.colorKey]} px-3 py-1.5 rounded-lg text-sm font-medium`}>
                                    {idx + 1}. {step.name}
                                </div>
                            </React.Fragment>
                        ));
                    })()}
                </div>
            </div>

            {/* Form */}
            <div className="space-y-6">
                {/* Job Name & GPU Pinning */}
                <div className="flex gap-6">
                    <div className="flex-1">
                        <label className="block text-sm font-medium text-slate-400 mb-2">Job Name</label>
                        <input
                            type="text"
                            value={jobName}
                            onChange={(e) => setJobName(e.target.value)}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                            placeholder="antibody_design"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-400 mb-2">
                            GPU Pinning {pinnedGpus.length > 0 && <span className="text-purple-400">({pinnedGpus.length} selected)</span>}
                        </label>
                        <div className="flex gap-2">
                            <button
                                onClick={() => setPinnedGpus([])}
                                className={`px-3 py-2 rounded-lg font-medium text-sm transition-all ${pinnedGpus.length === 0
                                    ? 'bg-slate-600 text-white ring-2 ring-slate-400'
                                    : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                    }`}
                            >
                                Auto
                            </button>
                            {[
                                { id: 0, name: '5090' },
                                { id: 1, name: '5060Ti' },
                                { id: 2, name: '3090#1' },
                                { id: 3, name: '3090#2' },
                            ].map(gpu => (
                                <button
                                    key={gpu.id}
                                    onClick={() => {
                                        setPinnedGpus(prev =>
                                            prev.includes(gpu.id)
                                                ? prev.filter(g => g !== gpu.id)
                                                : [...prev, gpu.id].sort()
                                        );
                                    }}
                                    className={`px-3 py-2 rounded-lg font-medium text-sm transition-all ${pinnedGpus.includes(gpu.id)
                                        ? 'bg-purple-600 text-white ring-2 ring-purple-400'
                                        : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                        }`}
                                >
                                    {gpu.name}
                                </button>
                            ))}
                        </div>
                        {pinnedGpus.length > 0 && (
                            <label className="flex items-center gap-2 mt-3 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={lockGpus}
                                    onChange={e => setLockGpus(e.target.checked)}
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-purple-500 focus:ring-purple-500"
                                />
                                <span className="text-sm text-slate-400">Lock selected GPU(s) exclusively during workflow</span>
                            </label>
                        )}
                    </div>
                </div>

                {/* Target PDB Selection - Now with multiple sources */}
                <TargetAntigenSelector
                    onSelect={(target) => {
                        if (target) {
                            if (target.type === 'upload' && target.file) {
                                setTargetPdb(target.file);
                                setTargetSource({ type: 'upload' });
                            } else if (target.url) {
                                // For URL-based sources (runs, presets, rcsb), we need to fetch and parse
                                setTargetSource({
                                    type: target.type,
                                    url: target.url,
                                    path: target.path,
                                    designId: target.designId,
                                    pdbId: target.pdbId
                                });
                                // Fetch the PDB content and create a File object for parsing
                                fetch(target.url)
                                    .then(res => res.blob())
                                    .then(blob => {
                                        const file = new File([blob], target.name + '.pdb', { type: 'chemical/x-pdb' });
                                        setTargetPdb(file);
                                    })
                                    .catch(err => {
                                        console.error('[ANTIBODY_DENOVO] Failed to fetch PDB:', err);
                                        alert('Failed to load PDB from source');
                                    });
                            }
                        } else {
                            setTargetPdb(null);
                            setTargetSource(null);
                        }
                    }}
                    selectedTarget={targetPdb ? { type: (targetSource?.type || 'upload') as 'upload' | 'run' | 'preset' | 'rcsb', name: targetPdb.name } : undefined}
                />

                {/* Framework Selection */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Antibody Framework</label>
                    <div className="grid grid-cols-4 gap-3 mb-3">
                        {[
                            { id: 'standard-fv', name: 'Standard Fv', desc: 'hu-4D5-8 (Herceptin)', color: 'blue' },
                            { id: 'nanobody', name: 'Nanobody', desc: 'VHH single-domain', color: 'purple' },
                            { id: 'sabdab', name: 'SAbDab', desc: 'Browse database', color: 'emerald' },
                            { id: 'custom', name: 'Custom', desc: 'Upload HLT PDB', color: 'amber' },
                        ].map((fw) => (
                            <button
                                key={fw.id}
                                onClick={() => setFrameworkType(fw.id as FrameworkType)}
                                className={`p-3 rounded-lg border transition-all ${frameworkType === fw.id
                                    ? fw.id === 'standard-fv'
                                        ? 'bg-blue-600/20 border-blue-500 text-blue-400'
                                        : fw.id === 'nanobody'
                                            ? 'bg-purple-600/20 border-purple-500 text-purple-400'
                                            : fw.id === 'sabdab'
                                                ? 'bg-emerald-600/20 border-emerald-500 text-emerald-400'
                                                : 'bg-amber-600/20 border-amber-500 text-amber-400'
                                    : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
                                    }`}
                            >
                                <div className="text-sm font-medium">{fw.name}</div>
                                <div className="text-xs opacity-75">{fw.desc}</div>
                            </button>
                        ))}
                    </div>

                    {/* SAbDab Framework Browser */}
                    {frameworkType === 'sabdab' && (
                        <div className="mt-3 p-3 bg-slate-900/50 rounded-lg border border-slate-700">
                            <FrameworkBrowser
                                onSelect={(fw) => {
                                    setSabdabFramework(fw);
                                    // Set framework PDB URL for 3D preview if pdbCode available
                                    if (fw?.pdbCode) {
                                        // Use RCSB PDB download URL for Mol* viewer
                                        setFrameworkPdbUrl(`https://files.rcsb.org/download/${fw.pdbCode.toUpperCase()}.pdb`);
                                        setViewerMode('framework');
                                        setShow3DViewer(true);
                                    } else {
                                        setFrameworkPdbUrl(null);
                                    }
                                }}
                                selectedFramework={sabdabFramework}
                                showCustomUpload={false}
                            />
                        </div>
                    )}

                    {/* Custom framework upload */}
                    {frameworkType === 'custom' && (
                        <div className="mt-3">
                            <input
                                type="file"
                                accept=".pdb"
                                onChange={(e) => {
                                    const file = e.target.files?.[0] || null;
                                    setCustomFrameworkFile(file);
                                    setCustomFrameworkPath(null);
                                }}
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-amber-500 outline-none file:mr-4 file:py-1 file:px-4 file:rounded-lg file:border-0 file:bg-amber-600 file:text-white file:cursor-pointer"
                            />
                            <p className="mt-1 text-xs text-slate-500">Upload HLT-formatted framework PDB with chain H (Heavy) and L (Light)</p>
                        </div>
                    )}

                    <p className="mt-1 text-xs text-slate-500">
                        {frameworkType === 'standard-fv' && 'Standard humanized Fv framework - good for most applications'}
                        {frameworkType === 'nanobody' && 'Single-domain VHH antibody - smaller, better tissue penetration'}
                        {frameworkType === 'sabdab' && 'Browse VHH structures from SAbDab database (CC-BY 4.0)'}
                        {frameworkType === 'custom' && 'Use your own HLT-formatted antibody framework'}
                    </p>
                </div>

                {/* Chain Selector (when PDB is parsed) */}
                {parsedChains.length > 1 && (
                    <div>
                        <label className="block text-sm font-medium text-slate-400 mb-2">Antigen Chain</label>
                        <div className="flex gap-2 flex-wrap">
                            {parsedChains.map(chain => (
                                <button
                                    key={chain.id}
                                    onClick={() => {
                                        setSelectedChain(chain.id);
                                        setSelectedResidues(new Set()); // Clear selection when chain changes
                                    }}
                                    className={`px-4 py-2 rounded-lg font-medium transition-all ${selectedChain === chain.id
                                        ? 'bg-blue-600 text-white'
                                        : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                        }`}
                                >
                                    Chain {chain.id} ({chain.length} aa)
                                </button>
                            ))}
                        </div>
                        <p className="mt-1 text-xs text-slate-500">Select the chain representing the antigen/target</p>
                    </div>
                )}

                {/* Interactive Epitope Selector with 3D Viewer */}
                {parsedChains.length > 0 && (
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <label className="block text-sm font-medium text-slate-400">
                                Epitope Selection
                                <span className="ml-2 text-xs text-slate-500 font-normal">
                                    (Select hotspot residues the antibody should target)
                                </span>
                            </label>

                            {/* Explicit Toggle Buttons for Target and Framework Viewers */}
                            <div className="flex gap-2">
                                {pdbBlobUrl && (
                                    <button
                                        type="button"
                                        onClick={() => {
                                            setViewerMode('target');
                                            setShow3DViewer(show3DViewer && viewerMode === 'target' ? false : true);
                                        }}
                                        className={`px-3 py-1.5 text-xs rounded-lg transition-all flex items-center gap-2 ${show3DViewer && viewerMode === 'target'
                                            ? 'bg-emerald-600/20 text-emerald-400 border border-emerald-500/50'
                                            : 'bg-slate-700 text-slate-400 hover:bg-slate-600 border border-slate-600/40'
                                            }`}
                                    >
                                        🎯 Target 3D
                                    </button>
                                )}
                                {frameworkPdbUrl && (
                                    <button
                                        type="button"
                                        onClick={() => {
                                            setViewerMode('framework');
                                            setShow3DViewer(show3DViewer && viewerMode === 'framework' ? false : true);
                                        }}
                                        className={`px-3 py-1.5 text-xs rounded-lg transition-all flex items-center gap-2 ${show3DViewer && viewerMode === 'framework'
                                            ? 'bg-purple-600/20 text-purple-400 border border-purple-500/50'
                                            : 'bg-slate-700 text-slate-400 hover:bg-slate-600 border border-slate-600/40'
                                            }`}
                                    >
                                        🧬 Framework 3D
                                    </button>
                                )}
                            </div>
                        </div>

                        {/* 3D Molstar Viewer for visualization - toggled */}
                        {(pdbBlobUrl || frameworkPdbUrl) && show3DViewer && (
                            <div className="animate-in fade-in slide-in-from-top-2 duration-300">
                                {/* Label showing current view */}
                                <div className="text-xs text-slate-500 mb-2">
                                    {viewerMode === 'framework' ? '🧬 Framework Template Preview' : '🎯 Target Antigen Preview'}
                                </div>
                                <EpitopeMolstarViewer
                                    structureUrl={viewerMode === 'framework' && frameworkPdbUrl ? frameworkPdbUrl : pdbBlobUrl || ''}
                                    height={400}
                                    selectedResidues={viewerMode === 'target' ? selectedResidues : new Set<string>()}
                                />
                            </div>
                        )}

                        {/* 2D Sequence Grid */}
                        <div>
                            <div className="text-xs text-slate-500 mb-1">2D Sequence View (shift+click for range)</div>
                            <EpitopeSelector
                                chains={parsedChains}
                                selectedResidues={selectedResidues}
                                onSelectionChange={setSelectedResidues}
                                activeChain={selectedChain || undefined}
                            />
                        </div>
                    </div>
                )}

                {/* Fallback text input if no PDB */}
                {parsedChains.length === 0 && targetPdb && !isParsing && (
                    <div className="p-4 bg-amber-500/10 border border-amber-500/30 rounded-lg text-amber-400 text-sm">
                        ⚠️ Could not parse PDB file. Please ensure it's a valid PDB format.
                    </div>
                )}

                {/* Optional DNA/RNA Sequence for Complex Prediction */}
                <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-3">
                        <div>
                            <h4 className="text-sm font-medium text-slate-300">DNA/RNA Binding Partner (Optional)</h4>
                            <p className="text-xs text-slate-500">For proteins that form optimal structures when bound to nucleic acid</p>
                        </div>
                        <button
                            onClick={() => setShowDnaInput(!showDnaInput)}
                            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${showDnaInput
                                ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30'
                                : 'bg-slate-700/50 text-slate-400 hover:bg-slate-600/50'
                                }`}
                        >
                            {showDnaInput ? '🧬 Enabled' : '+ Add DNA/RNA'}
                        </button>
                    </div>
                    {showDnaInput && (
                        <div className="mt-3">
                            <textarea
                                value={targetDnaSeq}
                                onChange={(e) => setTargetDnaSeq(e.target.value.toUpperCase().replace(/[^ATGCU\s]/gi, ''))}
                                placeholder="Enter DNA (ATGC) or RNA (AUGC) sequence..."
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white font-mono text-sm focus:ring-2 focus:ring-cyan-500 outline-none h-24 resize-none"
                            />
                            <div className="flex items-center justify-between mt-2">
                                <p className="text-xs text-slate-500">
                                    {targetDnaSeq.replace(/\s/g, '').length > 0
                                        ? `${targetDnaSeq.replace(/\s/g, '').length} nucleotides`
                                        : 'DNA sequence for protein-DNA complex prediction'
                                    }
                                </p>
                                {targetDnaSeq && (
                                    <span className="text-xs text-cyan-400">Complex prediction will precede antibody design</span>
                                )}
                            </div>
                        </div>
                    )}
                </div>

                {/* Design Mode Selector */}
                <DesignModeSelector
                    mode={designMode}
                    onModeChange={setDesignMode}
                    selectedLoops={selectedCDRLoops}
                    onLoopsChange={setSelectedCDRLoops}
                    protectTetrad={protectTetrad}
                    onProtectTetradChange={setProtectTetrad}
                    frameworkType={frameworkType}
                />

                {/* Framework Editor - shown for framework_allowed and full_design modes */}
                {(designMode === 'framework_allowed' || designMode === 'full_design') && (
                    <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
                        <FrameworkEditor
                            state={frameworkProtection}
                            onChange={setFrameworkProtection}
                            frameworkType={frameworkType}
                            compact={true}
                        />
                        <p className="mt-2 text-xs text-slate-500">
                            Configure which framework positions should remain fixed during sequence design.
                            Protected positions will not be mutated by FAMPNN/ProteinMPNN.
                        </p>
                    </div>
                )}

                {/* PPIFlow Maturation (Main Panel) */}
                <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
                    <PPIFlowSettingsFields
                        settings={qualitySettings}
                        onSettingsChange={setQualitySettings}
                    />
                    <p className="mt-2 text-xs text-slate-500">
                        These settings apply when PPIFlow maturation is enabled.
                    </p>
                </div>

                {/* Quality Settings Panel */}
                <QualitySettingsPanel
                    settings={qualitySettings}
                    onSettingsChange={setQualitySettings}
                    preset={qualityPreset}
                    onPresetChange={setQualityPreset}
                />

                {/* Physics Refinement Panel (OpenMM) */}
                <PhysicsRefinementPanel
                    settings={physicsSettings}
                    onSettingsChange={setPhysicsSettings}
                    isAntibody={true}
                />

                {/* FrustraMPNN QC */}
                <div className="bg-slate-900/30 border border-slate-700/50 rounded-lg p-4">
                    <div className="flex items-center justify-between">
                        <div>
                            <h3 className="text-sm font-semibold text-slate-200">FrustraMPNN QC</h3>
                            <p className="text-xs text-slate-500 mt-1">
                                Annotate final candidates with local frustration (post‑pipeline, FIO only).
                            </p>
                        </div>
                        <label className="flex items-center gap-2 text-sm text-slate-300">
                            <input
                                type="checkbox"
                                checked={runFrustrampnn}
                                onChange={(e) => setRunFrustrampnn(e.target.checked)}
                                className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-amber-600"
                            />
                            Enable
                        </label>
                    </div>
                </div>

                {/* Number of Backbones */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Number of Backbones</label>
                    <input
                        type="number"
                        value={numDesigns}
                        onChange={(e) => setNumDesigns(parseInt(e.target.value) || 10)}
                        min={1}
                        max={100}
                        className="w-32 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                    />
                </div>

                {/* Sequences per Design */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">
                        Sequences per Design
                        <span className="ml-2 text-xs text-slate-500 font-normal">({seqsPerDesign})</span>
                    </label>
                    <div className="flex items-center gap-4">
                        <input
                            type="range"
                            value={seqsPerDesign}
                            onChange={(e) => setSeqsPerDesign(parseInt(e.target.value))}
                            min={1}
                            max={64}
                            step={1}
                            className="flex-1 h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                        />
                        <input
                            type="number"
                            value={seqsPerDesign}
                            onChange={(e) => setSeqsPerDesign(Math.max(1, Math.min(64, parseInt(e.target.value) || 8)))}
                            min={1}
                            max={64}
                            className="w-16 bg-slate-900 border border-slate-700 rounded-lg px-2 py-1 text-white text-center"
                        />
                    </div>
                    <p className="mt-1 text-xs text-slate-500">Number of sequence variants to generate per backbone design</p>
                </div>

                {/* Sequence Designer */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Sequence Designer</label>
                    <div className="flex gap-3">
                        {(['fampnn', 'antifold', 'proteinmpnn'] as const).map((designer) => (
                            <button
                                key={designer}
                                onClick={() => setSeqDesigner(designer)}
                                className={`px-4 py-2 rounded-lg font-medium transition-all ${seqDesigner === designer
                                    ? 'bg-blue-600 text-white'
                                    : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                    }`}
                            >
                                {designer.toUpperCase()}
                            </button>
                        ))}
                    </div>
                </div>

                {seqDesigner === 'fampnn' && (
                    <div>
                        <label className="block text-sm font-medium text-slate-400 mb-2">FAMPNN Constraints</label>
                        <div className="flex gap-3">
                            {(['generic', 'antibody'] as const).map((mode) => (
                                <button
                                    key={mode}
                                    onClick={() => setFampnnConstraintMode(mode)}
                                    className={`px-4 py-2 rounded-lg font-medium transition-all ${fampnnConstraintMode === mode
                                        ? 'bg-emerald-600 text-white'
                                        : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                        }`}
                                >
                                    {mode === 'generic' ? 'GENERIC' : 'ANTIBODY (CDR)'}
                                </button>
                            ))}
                        </div>
                        <p className="mt-1 text-xs text-slate-500">
                            Generic applies no fixed positions; Antibody uses CDR-aware constraints.
                        </p>
                    </div>
                )}

                {/* Validation Options - removed, now controlled via QualitySettingsPanel */}

                {/* Orchestrator Parallelism Settings */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Orchestrator Mode</label>
                    <div className="flex gap-3 mb-3">
                        <button
                            onClick={() => setParallelMode('standard')}
                            className={`px-4 py-2 rounded-lg font-medium transition-all ${parallelMode === 'standard'
                                ? 'bg-blue-600 text-white'
                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                }`}
                        >
                            Nextflow Split
                        </button>
                        <button
                            onClick={() => setParallelMode('full_orchestrator')}
                            className={`px-4 py-2 rounded-lg font-medium transition-all ${parallelMode === 'full_orchestrator'
                                ? 'bg-orange-600 text-white'
                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                }`}
                        >
                            Orchestrator Jobs
                        </button>
                    </div>
                    <p className="text-xs text-slate-500 mb-3">
                        {parallelMode === 'standard'
                            ? "Standard: Split work across pinned GPUs within Nextflow"
                            : "Orchestrator: Spawn child jobs that go through GPU queue"}
                    </p>

                    {parallelMode === 'full_orchestrator' && (
                        <div className="grid grid-cols-2 gap-4 mt-3">
                            <div>
                                <label className="text-xs text-slate-500">Backbones per job</label>
                                <input
                                    type="range"
                                    min="1"
                                    max="500"
                                    value={designsPerJob}
                                    onChange={(e) => setDesignsPerJob(parseInt(e.target.value))}
                                    className="w-full accent-orange-500"
                                />
                                <span className="text-sm text-slate-300">{designsPerJob}</span>
                            </div>
                            <div>
                                <label className="text-xs text-slate-500">PDBs per FAMPNN job</label>
                                <input
                                    type="range"
                                    min="1"
                                    max="500"
                                    value={pdBsPerJob}
                                    onChange={(e) => setPdBsPerJob(parseInt(e.target.value))}
                                    className="w-full accent-orange-500"
                                />
                                <span className="text-sm text-slate-300">{pdBsPerJob}</span>
                            </div>
                            <div>
                                <label className="text-xs text-slate-500">Sequences per Boltz job</label>
                                <input
                                    type="range"
                                    min="1"
                                    max="500"
                                    value={seqsPerBoltzJob}
                                    onChange={(e) => setSeqsPerBoltzJob(parseInt(e.target.value))}
                                    className="w-full accent-orange-500"
                                />
                                <span className="text-sm text-slate-300">{seqsPerBoltzJob}</span>
                            </div>
                        </div>
                    )}
                </div>

                {/* Debug Settings Panel - Hidden by default */}
                <div className="mt-6 border border-amber-600/30 rounded-lg overflow-hidden">
                    <button
                        onClick={() => setShowDebugSettings(!showDebugSettings)}
                        className={`w-full px-4 py-3 flex items-center justify-between text-left transition-colors ${showDebugSettings
                            ? 'bg-amber-600/20 text-amber-400'
                            : 'bg-slate-900/50 text-slate-500 hover:bg-slate-800/50'
                            }`}
                    >
                        <div className="flex items-center gap-2">
                            <span>🔧</span>
                            <span className="font-medium">Debug Settings</span>
                            {(skipRFantibody || skipFampnn || customOutputDir) && (
                                <span className="px-2 py-0.5 text-xs bg-amber-600 text-white rounded">ACTIVE</span>
                            )}
                        </div>
                        <span className="text-lg">{showDebugSettings ? '−' : '+'}</span>
                    </button>

                    {showDebugSettings && (
                        <div className="p-4 bg-slate-900/30 space-y-4">
                            <div className="text-xs text-amber-500/80 mb-3">
                                ⚠️ Debug settings allow skipping workflow steps. Use with caution.
                            </div>

                            {/* Skip RFantibody */}
                            <div className="space-y-2">
                                <label className="flex items-center gap-2 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={skipRFantibody}
                                        onChange={e => {
                                            setSkipRFantibody(e.target.checked);
                                            if (!e.target.checked) setRfantibodyInputPdbs('');
                                        }}
                                        className="w-4 h-4 rounded border-amber-600 bg-slate-800 text-amber-500 focus:ring-amber-500"
                                    />
                                    <span className="text-sm text-slate-300">Skip RFantibody (use pre-existing backbone PDBs)</span>
                                </label>
                                {skipRFantibody && (
                                    <input
                                        type="text"
                                        value={rfantibodyInputPdbs}
                                        onChange={e => setRfantibodyInputPdbs(e.target.value)}
                                        placeholder="/path/to/backbone/pdbs"
                                        className="w-full bg-slate-900 border border-amber-600/50 rounded-lg px-4 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none font-mono"
                                    />
                                )}
                            </div>

                            {/* Skip FAMPNN */}
                            <div className="space-y-2">
                                <label className="flex items-center gap-2 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={skipFampnn}
                                        onChange={e => {
                                            setSkipFampnn(e.target.checked);
                                            if (!e.target.checked) setFampnnCollectedPdbs('');
                                        }}
                                        className="w-4 h-4 rounded border-amber-600 bg-slate-800 text-amber-500 focus:ring-amber-500"
                                    />
                                    <span className="text-sm text-slate-300">Skip FAMPNN (use pre-existing sequenced PDBs)</span>
                                </label>
                                {skipFampnn && (
                                    <input
                                        type="text"
                                        value={fampnnCollectedPdbs}
                                        onChange={e => setFampnnCollectedPdbs(e.target.value)}
                                        placeholder="/path/to/fampnn/output/pdbs"
                                        className="w-full bg-slate-900 border border-amber-600/50 rounded-lg px-4 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none font-mono"
                                    />
                                )}
                            </div>

                            {/* Custom Output Directory */}
                            <div className="space-y-2">
                                <label className="text-sm text-slate-400">Custom Output Directory (optional)</label>
                                <input
                                    type="text"
                                    value={customOutputDir}
                                    onChange={e => setCustomOutputDir(e.target.value)}
                                    placeholder="/mnt/BioModStack/results/custom_run"
                                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2 text-white text-sm focus:ring-2 focus:ring-slate-500 outline-none font-mono"
                                />
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {/* Submit Button */}
            <div className="mt-8 flex justify-end gap-3">
                {/* Template Manager Button */}
                <button
                    type="button"
                    onClick={() => setShowTemplateManager(true)}
                    className="px-6 py-3 text-purple-400 bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30 font-medium rounded-lg transition-colors flex items-center gap-2"
                >
                    📋 Save Template
                </button>
                <button
                    onClick={handleSubmit}
                    disabled={
                        submitMutation.isPending ||
                        isUploading ||
                        // When skipping, don't require target PDB or hotspots
                        (!(skipRFantibody || skipFampnn) && (!targetPdb || selectedResidues.size === 0)) ||
                        // When skipping, require the skip paths
                        (skipRFantibody && !rfantibodyInputPdbs.trim()) ||
                        (skipFampnn && !fampnnCollectedPdbs.trim())
                    }
                    className="px-6 py-3 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-700 disabled:text-slate-500 text-white font-medium rounded-lg transition-colors flex items-center gap-2"
                >
                    {isUploading ? (
                        <>
                            <span className="animate-spin">⏳</span>
                            Uploading PDB...
                        </>
                    ) : submitMutation.isPending ? (
                        <>
                            <span className="animate-spin">⚙️</span>
                            Submitting...
                        </>
                    ) : (skipRFantibody || skipFampnn) ? (
                        <>
                            🔧 Run Skipped Workflow
                        </>
                    ) : (
                        <>
                            🧬 Generate Antibodies ({selectedResidues.size} hotspots)
                        </>
                    )}
                </button>
            </div>

            {/* Template Manager Modal */}
            <TemplateManagerModal
                isOpen={showTemplateManager}
                onClose={() => setShowTemplateManager(false)}
                onSelect={(template) => {
                    console.log('[TEMPLATE_LOAD] Loading template:', template.name, template.params);
                    try {
                        // Load template params into state
                        const p = template.params || {};
                        // Core settings (check both new and old field names for backward compatibility)
                        if (p.job_name) setJobName(p.job_name);
                        if (p.framework_type) setFrameworkType(p.framework_type);
                        if (p.seq_designer) setSeqDesigner(p.seq_designer);
                        if (p.rfantibody_num_designs) setNumDesigns(p.rfantibody_num_designs);
                        if (p.seqs_per_design) setSeqsPerDesign(p.seqs_per_design);
                        if (typeof p.run_immunogenicity_scoring === 'boolean') setUseAntiberty(p.run_immunogenicity_scoring);
                        if (typeof p.run_stability_scoring === 'boolean') setUseThermoMPNN(p.run_stability_scoring);
                        if (typeof p.run_frustrampnn === 'boolean') setRunFrustrampnn(p.run_frustrampnn);
                        if (p.parallel_mode) setParallelMode(p.parallel_mode);
                        if (p.designs_per_job) setDesignsPerJob(p.designs_per_job);
                        if (p.pdbs_per_job) setPdBsPerJob(p.pdbs_per_job);
                        // Design mode
                        if (p.design_mode) setDesignMode(p.design_mode);
                        if (Array.isArray(p.selected_cdr_loops)) setSelectedCDRLoops(new Set(p.selected_cdr_loops));
                        if (typeof p.protect_tetrad === 'boolean') setProtectTetrad(p.protect_tetrad);
                        // Target (path only - user must re-upload if file no longer at path)
                        if (p.uploaded_path) setUploadedPath(p.uploaded_path);
                        if (p.selected_chain) setSelectedChain(p.selected_chain);
                        if (Array.isArray(p.selected_residues)) setSelectedResidues(new Set(p.selected_residues));
                        // Quality settings - check both old and new field names
                        const qualityS = p.quality_settings || p.qualitySettings;
                        if (qualityS) {
                            setQualitySettings({ ...PRESETS.balanced, ...qualityS });
                        }
                        if (p.quality_preset) setQualityPreset(p.quality_preset);
                        console.log('[TEMPLATE_LOAD] Successfully loaded template');
                    } catch (err) {
                        console.error('[TEMPLATE_LOAD] Error loading template:', err);
                    }
                }}
                currentParams={{
                    // Core settings
                    job_name: jobName,
                    framework_type: frameworkType,
                    seq_designer: seqDesigner,
                    rfantibody_num_designs: numDesigns,
                    seqs_per_design: seqsPerDesign,
                    run_immunogenicity_scoring: useAntiberty,
                    run_stability_scoring: useThermoMPNN,
                    run_frustrampnn: runFrustrampnn,
                    parallel_mode: parallelMode,
                    designs_per_job: designsPerJob,
                    pdbs_per_job: pdBsPerJob,
                    seqs_per_boltz_job: seqsPerBoltzJob,
                    // Design mode
                    design_mode: designMode,
                    selected_cdr_loops: Array.from(selectedCDRLoops),
                    protect_tetrad: protectTetrad,
                    // Framework protection (for framework_allowed and full_design modes)
                    protected_positions: frameworkProtection.protectedPositions.join(','),
                    protect_disulfides: frameworkProtection.protectDisulfides,
                    protect_fr_contacts: frameworkProtection.protectFrContacts,
                    // Target info (path only - file must exist at path)
                    uploaded_path: uploadedPath,
                    selected_chain: selectedChain,
                    selected_residues: Array.from(selectedResidues),
                    // Quality settings
                    quality_preset: qualityPreset,
                    quality_settings: qualitySettings,
                }}
                currentModelId="template_antibody_denovo"
                currentMode="antibody_denovo_pipeline"
            />
        </div>
    );
};

export default AntibodyDenovoTemplate;
