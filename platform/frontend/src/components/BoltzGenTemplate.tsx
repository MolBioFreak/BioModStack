/**
 * BoltzGenTemplate - Guided workflow for ligand-aware binder design
 * 
 * Modes:
 * - Ligand Binder: Design protein binding to custom SMILES
 * - NTP Binder: Design protein binding nucleotides (polymerase-like)
 * - Scaffold Around Ligand: Build scaffold around fixed ligand pose
 * - Backbone Docking: Dock ligand to existing structure
 */

import { useState, useMemo, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob } from '../lib/api';
import { StructureInput } from './StructureInput';
import { TargetAntigenSelector } from './TargetAntigenSelector';
import { EpitopeSelector } from './EpitopeSelector';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { parsePDBFile, parsePDB, type Chain, formatSelectedResidues } from '../utils/pdbUtils';

// NTP Templates - pre-defined nucleotide SMILES
const NTP_TEMPLATES = [
    { id: 'dATP', name: 'dATP (DNA)', smiles: 'Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3' },
    { id: 'dTTP', name: 'dTTP (DNA)', smiles: 'Cc1cn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)[nH]c1=O' },
    { id: 'dGTP', name: 'dGTP (DNA)', smiles: 'Nc1nc2c(ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3)c(=O)[nH]1' },
    { id: 'dCTP', name: 'dCTP (DNA)', smiles: 'Nc1ccn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)n1' },
    { id: 'ATP', name: 'ATP (RNA/Energy)', smiles: 'Nc1ncnc2c1ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O' },
    { id: 'UTP', name: 'UTP (RNA)', smiles: 'O=c1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)[nH]1' },
    { id: 'GTP', name: 'GTP (RNA)', smiles: 'Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1' },
    { id: 'CTP', name: 'CTP (RNA)', smiles: 'Nc1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)n1' },
];

// Default VHH camelid framework: cAbBCII10 (h-NbBCII10.pdb, PDB: 3DWT)
// This is the same framework used by RFantibody for nanobody design
// 127 residues - well-characterized humanized VHH
const DEFAULT_VHH_FRAMEWORK = `QVQLVESGGGLVQPGGSLRLSCAASGGSEYSYSTFSLGWFRQAPGQGLEAVAAIASMGGLTYYADSVKGRFTISRDNSKNTLYLQMNSLRAEDTAVYYCAAVRGYFMRLPSSHNFRYWGQGTLVTVS`;
// CDR regions (IMGT numbering, approximate):
// CDR-H1: 26-35 (GGSEYSY)
// CDR-H2: 50-65 (AIASMGGLT)
// CDR-H3: 95-102 (VRGYFMRLPSSHNFRY)

type DesignMode = 'ligand_binder' | 'ntp_binder' | 'scaffold_around_ligand' | 'backbone_docking' | 'peptide_binder' | 'nanobody_binder';
type DockingMethod = 'none' | 'diffdock' | 'unidock' | 'both';
type Protocol = 'protein-anything' | 'peptide-anything' | 'protein-small_molecule' | 'nanobody-anything' | 'antibody-anything';
type CheckpointMode = 'both' | 'diverse' | 'adherence';

interface BoltzGenTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, any>;
}

export function BoltzGenTemplate({ onBack, initialValues }: BoltzGenTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    // Mode selection
    const [mode, setMode] = useState<DesignMode>(initialValues?.mode || 'ligand_binder');

    // Job metadata
    const [jobName, setJobName] = useState(initialValues?.name || 'boltzgen_design');
    const [pinnedGpus, setPinnedGpus] = useState<number[]>(initialValues?.pinned_gpus ?? []);
    const [lockGpus, setLockGpus] = useState(false);

    // Ligand inputs
    const [ligandSmiles, setLigandSmiles] = useState(initialValues?.ligand_smiles || initialValues?.boltzgen_ligand_smiles || '');
    const [selectedNtp, setSelectedNtp] = useState(initialValues?.ntp_type || initialValues?.boltzgen_ntp_type || '');
    const [ligandPdb, setLigandPdb] = useState(initialValues?.ligand_pdb || '');

    // Structure inputs
    const [inputPdb, setInputPdb] = useState(initialValues?.input_pdb || initialValues?.boltzgen_input_pdb || '');

    // Design parameters
    const [scaffoldLength, setScaffoldLength] = useState(initialValues?.scaffold_length || initialValues?.boltzgen_scaffold_length || '80-120');
    const [numDesigns, setNumDesigns] = useState(initialValues?.num_designs || initialValues?.boltzgen_num_designs || 10);
    const [batchSize, setBatchSize] = useState(initialValues?.batch_size || initialValues?.boltzgen_batch_size || 1);

    // Advanced options
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [bindingSiteResidues, setBindingSiteResidues] = useState(initialValues?.binding_site_residues || initialValues?.boltzgen_binding_site_residues || '');
    const [catalyticSite, setCatalyticSite] = useState(initialValues?.catalytic_site || initialValues?.boltzgen_catalytic_site || false);
    const [dockingMethod, setDockingMethod] = useState<DockingMethod>(
        initialValues?.docking_method || 'none'
    );

    // Nanobody-specific parameters
    const [useFrameworkTemplate, setUseFrameworkTemplate] = useState(true);
    const [nanobodyFramework, setNanobodyFramework] = useState(initialValues?.boltzgen_nanobody_framework || DEFAULT_VHH_FRAMEWORK);
    const [cdrH3Length, setCdrH3Length] = useState(initialValues?.boltzgen_cdr_h3_length || '12-18');
    const [cdrH1Length, setCdrH1Length] = useState(initialValues?.boltzgen_cdr_h1_length || '5-8');
    const [cdrH2Length, setCdrH2Length] = useState(initialValues?.boltzgen_cdr_h2_length || '6-10');

    // Target selection (for nanobody/antibody modes)
    const [targetSource, setTargetSource] = useState<{ type: string; url?: string; path?: string; designId?: string; pdbId?: string; name?: string; file?: File } | null>(null);
    const [targetPdb, setTargetPdb] = useState<File | null>(null);
    const [parsedChains, setParsedChains] = useState<Chain[]>([]);
    const [selectedChain, setSelectedChain] = useState<string | null>(null);
    const [selectedEpitopeResidues, setSelectedEpitopeResidues] = useState<Set<string>>(new Set());
    const [show3DViewer, setShow3DViewer] = useState(false);
    const [pdbBlobUrl, setPdbBlobUrl] = useState<string | null>(null);

    // Effect: Create blob URL for Molstar viewer when target file changes
    useEffect(() => {
        if (targetPdb) {
            const url = URL.createObjectURL(targetPdb);
            setPdbBlobUrl(url);
            return () => URL.revokeObjectURL(url);
        } else if (targetSource?.url) {
            // For RCSB/run selections, use the API URL directly
            setPdbBlobUrl(targetSource.url);
        } else {
            setPdbBlobUrl(null);
        }
    }, [targetPdb, targetSource?.url]);

    // Effect: Fetch PDB from URL when targetSource has a URL (RCSB/run selection)
    useEffect(() => {
        if (targetSource?.url && !targetPdb) {
            // Fetch PDB from URL and convert to File for parsing
            fetch(targetSource.url)
                .then(res => {
                    if (!res.ok) throw new Error('Failed to fetch PDB');
                    return res.text();
                })
                .then(pdbContent => {
                    // Parse PDB content directly
                    const parsed = parsePDB(pdbContent);
                    setParsedChains(parsed.chains);
                    if (parsed.chains.length > 0 && !selectedChain) {
                        setSelectedChain(parsed.chains[0].id);
                    }
                })
                .catch(err => {
                    console.error('Failed to fetch/parse PDB from URL:', err);
                    setParsedChains([]);
                });
        }
    }, [targetSource?.url, targetPdb]);

    // Effect: Parse PDB when target file changes (upload)
    useEffect(() => {
        if (targetPdb) {
            parsePDBFile(targetPdb).then(parsed => {
                setParsedChains(parsed.chains);
                if (parsed.chains.length > 0 && !selectedChain) {
                    setSelectedChain(parsed.chains[0].id);
                }
            }).catch(err => {
                console.error('Failed to parse PDB:', err);
                setParsedChains([]);
            });
        } else if (!targetSource?.url) {
            setParsedChains([]);
            setSelectedChain(null);
        }
    }, [targetPdb]);

    // Effect: Update framework when toggle changes
    useEffect(() => {
        if (useFrameworkTemplate && !nanobodyFramework) {
            setNanobodyFramework(DEFAULT_VHH_FRAMEWORK);
        }
    }, [useFrameworkTemplate]);

    // Filtering parameters (new)
    const [budget, setBudget] = useState<number | ''>(initialValues?.boltzgen_budget || 50);
    const [customBudget, setCustomBudget] = useState(false);
    const [alpha, setAlpha] = useState(initialValues?.boltzgen_alpha || 0.01);
    const [maxRmsd, setMaxRmsd] = useState<number | ''>(initialValues?.boltzgen_max_rmsd || 2.0);
    const [customRmsd, setCustomRmsd] = useState(false);
    const [minPlddt, setMinPlddt] = useState<number | ''>(initialValues?.boltzgen_min_plddt || 70);
    const [minConfScore, setMinConfScore] = useState<number | ''>(initialValues?.boltzgen_min_conf_score || '');
    const [filterBiased, setFilterBiased] = useState(initialValues?.boltzgen_filter_biased !== false); // default true

    // Metric weights (slider-based)
    const [hbondWeight, setHbondWeight] = useState(1.0);
    const [sasaWeight, setSasaWeight] = useState(1.0);
    const [contactWeight, setContactWeight] = useState(1.0);
    const [customMetrics, setCustomMetrics] = useState(false);
    const [metricsOverride, setMetricsOverride] = useState(initialValues?.boltzgen_metrics_override || '');

    // AA composition limits (slider-based)
    const [maxCys, setMaxCys] = useState(0.05);
    const [maxMet, setMaxMet] = useState(0.10);
    const [maxGly, setMaxGly] = useState(0.20);
    const [customFilters, setCustomFilters] = useState(false);
    const [additionalFilters, setAdditionalFilters] = useState(initialValues?.boltzgen_additional_filters || '');

    // Size buckets
    const [sizeMin, setSizeMin] = useState(10);
    const [sizeMax, setSizeMax] = useState(30);
    const [customSizeBuckets, setCustomSizeBuckets] = useState(false);
    const [sizeBuckets, setSizeBuckets] = useState(initialValues?.boltzgen_size_buckets || '');

    // Inverse folding parameters
    const [inverseFoldAvoid, setInverseFoldAvoid] = useState(initialValues?.boltzgen_inverse_fold_avoid || '');
    const [inverseFoldNumSeqs, setInverseFoldNumSeqs] = useState<number>(initialValues?.boltzgen_inverse_fold_num_sequences || 1);

    // Diffusion parameters
    const [stepScale, setStepScale] = useState<number | ''>(initialValues?.boltzgen_step_scale || 1.8);
    const [customStepScale, setCustomStepScale] = useState(false);
    const [noiseScale, setNoiseScale] = useState<number | ''>(initialValues?.boltzgen_noise_scale || 0.98);
    const [customNoiseScale, setCustomNoiseScale] = useState(false);

    // Secondary structure elements (interactive builder)
    interface SSElement {
        id: string;
        type: 'helix' | 'sheet' | 'loop';
        start: number;
        end: number;
    }
    const [ssElements, setSSElements] = useState<SSElement[]>([]);
    const [customSS, setCustomSS] = useState(false);
    const [secondaryStructure, setSecondaryStructure] = useState(initialValues?.boltzgen_secondary_structure || '');
    const [protocol, setProtocol] = useState<Protocol>(initialValues?.boltzgen_protocol || 'protein-anything');

    // Sync ssElements to secondaryStructure string
    useEffect(() => {
        if (!customSS && ssElements.length > 0) {
            const ssStr = ssElements.map(el => `${el.type}:${el.start}-${el.end}`).join(',');
            setSecondaryStructure(ssStr);
        }
    }, [ssElements, customSS]);

    // Additional advanced options (new)
    const [checkpointMode, setCheckpointMode] = useState<CheckpointMode>(initialValues?.boltzgen_checkpoint_mode || 'both');
    const [skipInverseFolding, setSkipInverseFolding] = useState(initialValues?.boltzgen_skip_inverse_folding || false);
    const [reuseExisting, setReuseExisting] = useState(initialValues?.boltzgen_reuse || false);

    // Production Mode - unlocks high design counts (10k-60k)
    const [productionMode, setProductionMode] = useState(initialValues?.boltzgen_production_mode || false);

    // Covalent bond constraints (disulfide, WHL staple, custom)
    interface CovalentBond {
        id: string;
        type: 'disulfide' | 'whl_staple' | 'custom';
        atom1_chain: string;
        atom1_residue: number;
        atom1_atom: string;
        atom2_chain: string;
        atom2_residue: number;
        atom2_atom: string;
    }
    const [covalentBonds, setCovalentBonds] = useState<CovalentBond[]>(
        initialValues?.boltzgen_covalent_bonds || []
    );

    // Derived state
    const effectiveSmiles = useMemo(() => {
        if (mode === 'ntp_binder' && selectedNtp) {
            return NTP_TEMPLATES.find(ntp => ntp.id === selectedNtp)?.smiles || '';
        }
        return ligandSmiles;
    }, [mode, selectedNtp, ligandSmiles]);

    // Validation
    const isValid = useMemo(() => {
        if (!jobName.trim()) return false;
        if (mode === 'ligand_binder' && !ligandSmiles.trim()) return false;
        if (mode === 'ntp_binder' && !selectedNtp) return false;
        if (mode === 'scaffold_around_ligand' && !ligandPdb) return false;
        if (mode === 'backbone_docking' && !inputPdb) return false;
        // nanobody_binder and peptide_binder don't require specific inputs for de novo design
        return true;
    }, [jobName, mode, ligandSmiles, selectedNtp, ligandPdb, inputPdb]);

    // Submit mutation
    const submitMutation = useMutation({
        mutationFn: submitJob,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        },
        onError: (error: any) => {
            const detail = error.response?.data?.detail;
            const message = typeof detail === 'object' ? JSON.stringify(detail, null, 2) : (detail || error.message);
            window.alert('Job submission failed:\n' + message);
        }
    });

    const handleSubmit = () => {
        if (!isValid) return;

        const params: Record<string, any> = {
            diffusion_method: 'boltzgen',
            run_boltzgen_only: dockingMethod === 'none',
            run_docking: dockingMethod !== 'none',
            run_diffdock: dockingMethod === 'diffdock' || dockingMethod === 'both',
            run_unidock: dockingMethod === 'unidock' || dockingMethod === 'both',
            boltzgen_scaffold_length: scaffoldLength,
            boltzgen_num_designs: numDesigns,
            boltzgen_batch_size: batchSize,
        };

        // Mode-specific params
        if (mode === 'ntp_binder') {
            params.boltzgen_ntp_type = selectedNtp;
            params.boltzgen_catalytic_site = catalyticSite;
        } else if (mode === 'ligand_binder') {
            params.boltzgen_ligand_smiles = ligandSmiles;
        } else if (mode === 'scaffold_around_ligand') {
            params.boltzgen_ligand_pdb = ligandPdb;
        } else if (mode === 'backbone_docking') {
            params.boltzgen_input_pdb = inputPdb;
            if (effectiveSmiles) params.boltzgen_ligand_smiles = effectiveSmiles;
        } else if (mode === 'nanobody_binder') {
            // Nanobody-specific params
            if (useFrameworkTemplate && nanobodyFramework.trim()) {
                params.boltzgen_nanobody_framework = nanobodyFramework;
            }
            params.boltzgen_cdr_h1_length = cdrH1Length;
            params.boltzgen_cdr_h2_length = cdrH2Length;
            params.boltzgen_cdr_h3_length = cdrH3Length;
            // Target antigen
            if (targetSource?.path) {
                params.boltzgen_target_pdb_path = targetSource.path;
            } else if (targetSource?.url) {
                params.boltzgen_target_pdb_url = targetSource.url;
            }
            // Ligand SMILES as fallback if no PDB target
            if (!targetSource && ligandSmiles.trim()) {
                params.boltzgen_ligand_smiles = ligandSmiles;
            }
        } else if (mode === 'peptide_binder') {
            // Peptide mode - may use ligand SMILES
            if (ligandSmiles.trim()) {
                params.boltzgen_ligand_smiles = ligandSmiles;
            }
        }

        // Advanced options
        if (bindingSiteResidues.trim()) {
            params.boltzgen_binding_site_residues = bindingSiteResidues;
        }

        // Filtering parameters
        if (budget) params.boltzgen_budget = budget;
        params.boltzgen_alpha = alpha;
        if (maxRmsd) params.boltzgen_max_rmsd = maxRmsd;
        if (minPlddt) params.boltzgen_min_plddt = minPlddt;
        if (minConfScore) params.boltzgen_min_conf_score = minConfScore;
        params.boltzgen_filter_biased = filterBiased;

        // Inverse folding parameters
        if (inverseFoldAvoid.trim()) {
            params.boltzgen_inverse_fold_avoid = inverseFoldAvoid;
        }
        if (inverseFoldNumSeqs > 1) {
            params.boltzgen_inverse_fold_num_sequences = inverseFoldNumSeqs;
        }

        // Diffusion parameters
        if (stepScale) params.boltzgen_step_scale = stepScale;
        if (noiseScale) params.boltzgen_noise_scale = noiseScale;

        // Advanced filtering parameters
        if (metricsOverride.trim()) {
            params.boltzgen_metrics_override = metricsOverride;
        }
        if (additionalFilters.trim()) {
            params.boltzgen_additional_filters = additionalFilters;
        }
        if (sizeBuckets.trim()) {
            params.boltzgen_size_buckets = sizeBuckets;
        }

        // Secondary structure and protocol
        if (secondaryStructure.trim()) {
            params.boltzgen_secondary_structure = secondaryStructure;
        }
        params.boltzgen_protocol = protocol;

        // Additional advanced options
        if (checkpointMode !== 'both') {
            params.boltzgen_checkpoint_mode = checkpointMode;
        }
        if (skipInverseFolding) {
            params.boltzgen_skip_inverse_folding = true;
        }
        if (reuseExisting) {
            params.boltzgen_reuse = true;
        }

        // Covalent bond constraints
        if (covalentBonds.length > 0) {
            params.boltzgen_covalent_bonds = JSON.stringify(covalentBonds);
        }

        submitMutation.mutate({
            name: jobName,
            model_id: 'boltzgen',
            mode: mode,
            params: {
                ...params,
                pinned_gpus: pinnedGpus.length > 0 ? pinnedGpus : undefined,
                lock_gpus: lockGpus && pinnedGpus.length > 0
            },
            pinned_gpu: pinnedGpus.length === 1 ? pinnedGpus[0] : null  // Single GPU for legacy compat
        });
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center gap-4">
                <button
                    onClick={onBack}
                    className="p-2 hover:bg-slate-700 rounded-lg text-slate-400 hover:text-white transition-colors"
                >
                    ← Back
                </button>
                <div>
                    <h2 className="text-2xl font-bold text-white">Ligand-Aware Binder Design</h2>
                    <p className="text-slate-400 text-sm">Design proteins that bind small molecules using BoltzGen</p>
                </div>
            </div>

            {/* Job Name & GPU Pinning */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6">
                <div className="flex gap-6">
                    <div className="flex-1">
                        <label className="block text-sm font-medium text-slate-300 mb-2">Job Name</label>
                        <input
                            type="text"
                            value={jobName}
                            onChange={e => setJobName(e.target.value)}
                            placeholder="e.g., atp_binder_design_01"
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 focus:border-transparent outline-none"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">
                            GPU Pinning {pinnedGpus.length > 0 && <span className="text-amber-400">({pinnedGpus.length} selected)</span>}
                        </label>
                        <div className="flex gap-2">
                            <button
                                onClick={() => setPinnedGpus([])}
                                className={`px-3 py-2.5 rounded-lg font-medium text-sm transition-all ${pinnedGpus.length === 0
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
                                    className={`px-3 py-2.5 rounded-lg font-medium text-sm transition-all ${pinnedGpus.includes(gpu.id)
                                        ? 'bg-amber-600 text-white ring-2 ring-amber-400'
                                        : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                        }`}
                                >
                                    {gpu.name}
                                </button>
                            ))}
                        </div>
                        {/* GPU Lock Checkbox */}
                        {pinnedGpus.length > 0 && (
                            <label className="flex items-center gap-2 mt-3 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={lockGpus}
                                    onChange={e => setLockGpus(e.target.checked)}
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-amber-500 focus:ring-amber-500"
                                />
                                <span className="text-sm text-slate-400">Lock selected GPU(s) exclusively during workflow</span>
                            </label>
                        )}
                    </div>
                </div>
            </div>

            {/* Mode Selection */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6">
                <label className="block text-sm font-medium text-slate-300 mb-3">Design Mode</label>
                <div className="grid grid-cols-3 gap-3">
                    {[
                        { id: 'nanobody_binder', name: 'Nanobody Binder', desc: 'VHH single-domain antibody design' },
                        { id: 'ligand_binder', name: 'Ligand Binder', desc: 'Custom SMILES input' },
                        { id: 'ntp_binder', name: 'NTP Binder', desc: 'Nucleotide binding (DNA/RNA polymerases)' },
                        { id: 'peptide_binder', name: 'Peptide Binder', desc: 'Short peptide design' },
                        { id: 'scaffold_around_ligand', name: 'Scaffold Around', desc: 'Build around fixed ligand pose' },
                        { id: 'backbone_docking', name: 'Backbone Docking', desc: 'Dock to existing structure' },
                    ].map(m => (
                        <button
                            key={m.id}
                            onClick={() => {
                                setMode(m.id as DesignMode);
                                // Auto-set protocol for specific modes
                                if (m.id === 'nanobody_binder') setProtocol('nanobody-anything');
                                else if (m.id === 'peptide_binder') setProtocol('peptide-anything');
                                else if (m.id === 'ligand_binder' || m.id === 'scaffold_around_ligand') setProtocol('protein-small_molecule');
                                else setProtocol('protein-anything');
                            }}
                            className={`p-4 rounded-xl border text-left transition-all ${mode === m.id
                                ? 'bg-amber-500/20 border-amber-500 shadow-lg shadow-amber-500/10'
                                : 'bg-slate-900/50 border-slate-700 hover:border-slate-600'
                                }`}
                        >
                            <div className="font-medium text-white">{m.name}</div>
                            <div className="text-xs text-slate-400 mt-1">{m.desc}</div>
                        </button>
                    ))}
                </div>
            </div>

            {/* Ligand Input - varies by mode */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6">
                <h3 className="text-lg font-semibold text-white mb-4">
                    {mode === 'nanobody_binder' ? 'Nanobody Framework & Target' :
                        mode === 'ntp_binder' ? 'Nucleotide Selection' :
                            mode === 'scaffold_around_ligand' ? 'Ligand Structure' :
                                mode === 'backbone_docking' ? 'Target Structure' : 'Ligand Definition'}
                </h3>

                {mode === 'nanobody_binder' ? (
                    <div className="space-y-6">
                        {/* Framework Mode Toggle */}
                        <div className="flex items-center gap-4 mb-4">
                            <button
                                type="button"
                                onClick={() => {
                                    setUseFrameworkTemplate(true);
                                    setNanobodyFramework(DEFAULT_VHH_FRAMEWORK);
                                }}
                                className={`px-4 py-2 rounded-lg font-medium text-sm transition-all ${useFrameworkTemplate
                                    ? 'bg-amber-500/20 border border-amber-500 text-amber-300'
                                    : 'bg-slate-800 border border-slate-700 text-slate-400 hover:border-slate-600'
                                    }`}
                            >
                                Use VHH Template
                            </button>
                            <button
                                type="button"
                                onClick={() => {
                                    setUseFrameworkTemplate(false);
                                    setNanobodyFramework('');
                                }}
                                className={`px-4 py-2 rounded-lg font-medium text-sm transition-all ${!useFrameworkTemplate
                                    ? 'bg-purple-500/20 border border-purple-500 text-purple-300'
                                    : 'bg-slate-800 border border-slate-700 text-slate-400 hover:border-slate-600'
                                    }`}
                            >
                                Full De Novo
                            </button>
                        </div>

                        {/* Framework Sequence (when using template) */}
                        {useFrameworkTemplate && (
                            <div>
                                <label className="block text-sm font-medium text-slate-300 mb-2">
                                    VHH Framework Sequence
                                </label>
                                <textarea
                                    value={nanobodyFramework}
                                    onChange={e => setNanobodyFramework(e.target.value)}
                                    rows={3}
                                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white font-mono text-xs focus:ring-2 focus:ring-amber-500 outline-none"
                                />
                                <p className="text-xs text-slate-500 mt-1">
                                    X marks CDR loop positions that BoltzGen will design. Edit framework if needed.
                                </p>
                            </div>
                        )}

                        {/* CDR Loop Lengths */}
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">CDR Loop Lengths</label>
                            <div className="grid grid-cols-3 gap-4">
                                <div className="bg-slate-900/50 rounded-lg p-3">
                                    <div className="flex items-center justify-between mb-1">
                                        <span className="text-xs text-slate-400">CDR-H1</span>
                                        <span className="text-xs text-slate-500">Framework</span>
                                    </div>
                                    <input
                                        type="text"
                                        value={cdrH1Length}
                                        onChange={e => setCdrH1Length(e.target.value)}
                                        className="w-full bg-transparent border-b border-slate-700 text-white font-mono text-sm py-1 focus:border-amber-500 outline-none"
                                    />
                                </div>
                                <div className="bg-slate-900/50 rounded-lg p-3">
                                    <div className="flex items-center justify-between mb-1">
                                        <span className="text-xs text-slate-400">CDR-H2</span>
                                        <span className="text-xs text-slate-500">Variable</span>
                                    </div>
                                    <input
                                        type="text"
                                        value={cdrH2Length}
                                        onChange={e => setCdrH2Length(e.target.value)}
                                        className="w-full bg-transparent border-b border-slate-700 text-white font-mono text-sm py-1 focus:border-amber-500 outline-none"
                                    />
                                </div>
                                <div className="bg-slate-900/50 rounded-lg p-3 border border-amber-500/30">
                                    <div className="flex items-center justify-between mb-1">
                                        <span className="text-xs text-amber-400">CDR-H3</span>
                                        <span className="text-xs text-amber-500/70">Most Variable</span>
                                    </div>
                                    <input
                                        type="text"
                                        value={cdrH3Length}
                                        onChange={e => setCdrH3Length(e.target.value)}
                                        className="w-full bg-transparent border-b border-amber-500/50 text-white font-mono text-sm py-1 focus:border-amber-500 outline-none"
                                    />
                                </div>
                            </div>
                        </div>

                        {/* Divider */}
                        <div className="border-t border-slate-700 my-4" />

                        {/* Target Antigen Selection */}
                        <div>
                            <TargetAntigenSelector
                                onSelect={(target) => {
                                    setTargetSource(target);
                                    if (target?.type === 'upload' && target.file) {
                                        setTargetPdb(target.file);
                                    } else {
                                        setTargetPdb(null);
                                    }
                                }}
                                selectedTarget={targetSource ? {
                                    type: targetSource.type as 'upload' | 'run' | 'preset' | 'rcsb',
                                    name: targetSource.name || 'Selected',
                                    file: targetPdb || undefined,
                                    path: targetSource.path,
                                    url: targetSource.url,
                                    pdbId: targetSource.pdbId,
                                    designId: targetSource.designId
                                } : undefined}
                            />
                        </div>

                        {/* Epitope Selection (when target is loaded) */}
                        {parsedChains.length > 0 && (
                            <div className="space-y-4">
                                {/* Header with 3D Viewer Toggle */}
                                <div className="flex items-center justify-between">
                                    <label className="block text-sm font-medium text-slate-300">
                                        Epitope Selection
                                    </label>
                                    {pdbBlobUrl && (
                                        <button
                                            type="button"
                                            onClick={() => setShow3DViewer(!show3DViewer)}
                                            className={`text-xs px-3 py-1.5 rounded-lg transition-all flex items-center gap-2 ${show3DViewer
                                                ? 'bg-amber-500/20 text-amber-300 border border-amber-500/50'
                                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700 border border-slate-700'
                                                }`}
                                        >
                                            <span>{show3DViewer ? '🔍' : '🧬'}</span>
                                            3D Structure View
                                        </button>
                                    )}
                                </div>

                                {/* 3D Molstar Viewer (toggled) */}
                                {pdbBlobUrl && show3DViewer && (
                                    <div className="animate-in fade-in slide-in-from-top-2 duration-300">
                                        <EpitopeMolstarViewer
                                            structureUrl={pdbBlobUrl}
                                            height={350}
                                            selectedResidues={selectedEpitopeResidues}
                                        />
                                    </div>
                                )}

                                {/* 2D Sequence Grid Selector */}
                                <div className="bg-slate-900/50 rounded-lg p-4 border border-slate-700">
                                    <div className="text-xs text-slate-500 mb-2">
                                        Click residue to select • Shift+click for range • Ctrl+click to add/remove
                                    </div>
                                    <EpitopeSelector
                                        chains={parsedChains}
                                        selectedResidues={selectedEpitopeResidues}
                                        onSelectionChange={(residues) => {
                                            setSelectedEpitopeResidues(residues);
                                            // Sync to text field
                                            setBindingSiteResidues(formatSelectedResidues(
                                                parsedChains.flatMap(c => c.residues).filter(r => residues.has(`${r.chainId}${r.resNum}`))
                                            ));
                                        }}
                                        activeChain={selectedChain || undefined}
                                    />
                                </div>

                                {/* Manual Text Input (for advanced users) */}
                                <div className="flex items-center gap-2">
                                    <span className="text-xs text-slate-500">Or enter manually:</span>
                                    <input
                                        type="text"
                                        value={bindingSiteResidues}
                                        onChange={e => setBindingSiteResidues(e.target.value)}
                                        placeholder="e.g., A45,A46,A52"
                                        className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-1.5 text-white font-mono text-xs focus:ring-2 focus:ring-amber-500 outline-none"
                                    />
                                </div>
                            </div>
                        )}

                        {/* Optional: Target SMILES if no PDB */}
                        {!targetSource && (
                            <div>
                                <label className="block text-sm font-medium text-slate-300 mb-2">
                                    Or: Target Molecule SMILES
                                </label>
                                <input
                                    type="text"
                                    value={ligandSmiles}
                                    onChange={e => setLigandSmiles(e.target.value)}
                                    placeholder="Enter SMILES for small molecule target (optional)"
                                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                />
                            </div>
                        )}
                    </div>
                ) : mode === 'ntp_binder' ? (
                    <div className="space-y-4">
                        <label className="block text-sm font-medium text-slate-300 mb-2">Select Nucleotide</label>
                        <div className="grid grid-cols-4 gap-2">
                            {NTP_TEMPLATES.map(ntp => (
                                <button
                                    key={ntp.id}
                                    onClick={() => setSelectedNtp(ntp.id)}
                                    className={`p-3 rounded-lg border text-center transition-all ${selectedNtp === ntp.id
                                        ? 'bg-amber-500/20 border-amber-500 text-amber-300'
                                        : 'bg-slate-900/50 border-slate-700 text-slate-300 hover:border-slate-600'
                                        }`}
                                >
                                    <div className="font-mono font-bold">{ntp.id}</div>
                                    <div className="text-xs text-slate-500">{ntp.name.split(' ')[0]}</div>
                                </button>
                            ))}
                        </div>
                        {selectedNtp && (
                            <div className="mt-3 p-3 bg-slate-900/50 rounded-lg">
                                <span className="text-xs text-slate-400">Selected SMILES:</span>
                                <code className="block text-xs text-amber-400 font-mono mt-1 break-all">
                                    {NTP_TEMPLATES.find(n => n.id === selectedNtp)?.smiles}
                                </code>
                            </div>
                        )}
                    </div>
                ) : mode === 'scaffold_around_ligand' ? (
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">Ligand PDB Structure</label>
                        <StructureInput
                            value={ligandPdb}
                            onChange={setLigandPdb}
                            onBrowse={() => { }}
                            enableMultiSelect={false}
                            enableDirectory={false}
                        />
                        <p className="text-xs text-slate-500 mt-2">Upload a PDB file containing your ligand's 3D coordinates</p>
                    </div>
                ) : mode === 'backbone_docking' ? (
                    <div className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Protein Backbone PDB</label>
                            <StructureInput
                                value={inputPdb}
                                onChange={setInputPdb}
                                onBrowse={() => { }}
                                enableMultiSelect={false}
                                enableDirectory={false}
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Ligand SMILES (optional)</label>
                            <input
                                type="text"
                                value={ligandSmiles}
                                onChange={e => setLigandSmiles(e.target.value)}
                                placeholder="e.g., CCO for ethanol"
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                            />
                        </div>
                    </div>
                ) : (
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">Ligand SMILES</label>
                        <input
                            type="text"
                            value={ligandSmiles}
                            onChange={e => setLigandSmiles(e.target.value)}
                            placeholder="e.g., CCO for ethanol, or paste any valid SMILES"
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                        />
                        <p className="text-xs text-slate-500 mt-2">
                            Enter the SMILES representation of your target molecule.
                            You can generate these from ChemDraw, PubChem, or similar tools.
                        </p>
                    </div>
                )}
            </div>

            {/* Design Parameters */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-6">
                <div className="flex items-center justify-between mb-4">
                    <h3 className="text-lg font-semibold text-white">Design Parameters</h3>
                    <span className="text-xs px-2 py-1 rounded bg-slate-700 text-slate-300">
                        Protocol: <span className="text-amber-400">{protocol.replace('-anything', '').replace('-small_molecule', ' + ligand')}</span>
                    </span>
                </div>
                <div className="grid grid-cols-3 gap-4">
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">
                            {mode === 'nanobody_binder' ? 'Nanobody Length' : 'Scaffold Length'}
                        </label>
                        <select
                            value={scaffoldLength}
                            onChange={e => setScaffoldLength(e.target.value)}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 outline-none"
                        >
                            {mode === 'nanobody_binder' ? (
                                <>
                                    <option value="110-130">VHH Standard (110-130)</option>
                                    <option value="100-115">VHH Compact (100-115)</option>
                                    <option value="125-145">VHH Extended (125-145)</option>
                                </>
                            ) : mode === 'peptide_binder' ? (
                                <>
                                    <option value="10-20">Short Peptide (10-20)</option>
                                    <option value="15-30">Medium Peptide (15-30)</option>
                                    <option value="25-50">Long Peptide (25-50)</option>
                                </>
                            ) : (
                                <>
                                    <option value="60-80">Small (60-80)</option>
                                    <option value="80-120">Medium (80-120)</option>
                                    <option value="100-150">Large (100-150)</option>
                                    <option value="140-200">Extra Large (140-200)</option>
                                </>
                            )}
                        </select>
                        <p className="text-xs text-slate-500 mt-1">
                            {mode === 'nanobody_binder' ? 'Typical VHH ~120 residues' : 'Binder size range'}
                        </p>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">Number of Designs</label>
                        <input
                            type="number"
                            value={numDesigns}
                            onChange={e => setNumDesigns(parseInt(e.target.value) || 10)}
                            min={1}
                            max={productionMode ? 60000 : 100}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 outline-none"
                        />
                        <p className="text-xs text-slate-500 mt-1">
                            {productionMode ? '1-60,000 designs (production)' : '1-100 designs'}
                        </p>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-300 mb-2">Batch Size</label>
                        <select
                            value={batchSize}
                            onChange={e => setBatchSize(parseInt(e.target.value))}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 outline-none"
                        >
                            <option value={1}>1 (safest)</option>
                            <option value={2}>2</option>
                            <option value={4}>4</option>
                            <option value={8}>8 (faster)</option>
                            <option value={16}>16</option>
                            <option value={32}>32 (max VRAM)</option>
                        </select>
                        <p className="text-xs text-slate-500 mt-1">Designs per GPU pass</p>
                    </div>
                </div>

                {/* Production Mode Toggle */}
                <div className="mt-4 p-4 border border-amber-500/30 rounded-lg bg-amber-500/5">
                    <label className="flex items-center gap-3 cursor-pointer">
                        <div className={`w-10 h-6 rounded-full p-1 transition-colors ${productionMode ? 'bg-amber-500' : 'bg-slate-700'}`}>
                            <div className={`w-4 h-4 bg-white rounded-full shadow transition-transform ${productionMode ? 'translate-x-4' : ''}`} />
                        </div>
                        <input type="checkbox" className="hidden" checked={productionMode} onChange={e => setProductionMode(e.target.checked)} />
                        <span className="text-sm font-medium text-amber-400">Production Mode</span>
                    </label>
                    {productionMode && (
                        <div className="mt-2 text-xs text-amber-400/80">
                            Unlocks up to 60,000 designs. Recommended: 10k-60k for production runs.
                            <br />Estimated time: ~30-60 sec/design on RTX 5090/3090.
                        </div>
                    )}
                </div>
            </div>

            {/* Advanced Options */}
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl overflow-hidden">
                <button
                    onClick={() => setShowAdvanced(!showAdvanced)}
                    className="w-full px-6 py-4 flex items-center justify-between hover:bg-slate-700/30 transition-colors"
                >
                    <span className="font-medium text-slate-300">Advanced Options</span>
                    <span className="text-slate-500">{showAdvanced ? '▲' : '▼'}</span>
                </button>
                {showAdvanced && (
                    <div className="p-6 border-t border-slate-700 space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Binding Site Residues</label>
                            <input
                                type="text"
                                value={bindingSiteResidues}
                                onChange={e => setBindingSiteResidues(e.target.value)}
                                placeholder="e.g., A:45-52,A:78-85"
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                            />
                            <p className="text-xs text-slate-500 mt-1">Optional: Specify binding pocket residue ranges</p>
                        </div>

                        {/* Catalytic Site - only for NTP binder mode */}
                        {mode === 'ntp_binder' && (
                            <div className="flex items-center gap-6">
                                <label className="flex items-center gap-3 cursor-pointer">
                                    <div className={`w-10 h-6 rounded-full p-1 transition-colors ${catalyticSite ? 'bg-amber-500' : 'bg-slate-700'}`}>
                                        <div className={`w-4 h-4 bg-white rounded-full shadow transition-transform ${catalyticSite ? 'translate-x-4' : ''}`} />
                                    </div>
                                    <input type="checkbox" className="hidden" checked={catalyticSite} onChange={e => setCatalyticSite(e.target.checked)} />
                                    <span className="text-sm text-slate-300">Catalytic Site (Mg2+)</span>
                                </label>
                            </div>
                        )}

                        {/* Secondary Structure Constraints */}
                        <div className="border-t border-slate-700 pt-4">
                            <div className="flex items-center justify-between mb-2">
                                <label className="text-sm font-medium text-slate-300">Secondary Structure</label>
                                <label className="flex items-center gap-1 cursor-pointer">
                                    <input type="checkbox" checked={customSS} onChange={e => setCustomSS(e.target.checked)} className="w-3 h-3 accent-amber-500" />
                                    <span className="text-xs text-slate-500">Custom</span>
                                </label>
                            </div>

                            {customSS ? (
                                <input
                                    type="text"
                                    value={secondaryStructure}
                                    onChange={e => setSecondaryStructure(e.target.value)}
                                    placeholder="e.g., helix:1-20,sheet:25-35,loop:40-45"
                                    className="w-full bg-slate-900 border border-amber-500/50 rounded-lg px-4 py-3 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                />
                            ) : (
                                <div className="space-y-3">
                                    {/* Element pills */}
                                    {ssElements.length > 0 && (
                                        <div className="flex flex-wrap gap-2">
                                            {ssElements.map(el => (
                                                <div key={el.id} className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-sm ${el.type === 'helix' ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30' :
                                                        el.type === 'sheet' ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30' :
                                                            'bg-green-500/20 text-green-300 border border-green-500/30'
                                                    }`}>
                                                    <span className="capitalize">{el.type}</span>
                                                    <span className="font-mono">{el.start}-{el.end}</span>
                                                    <button
                                                        type="button"
                                                        onClick={() => setSSElements(ssElements.filter(e => e.id !== el.id))}
                                                        className="text-slate-400 hover:text-white ml-1"
                                                    >×</button>
                                                </div>
                                            ))}
                                        </div>
                                    )}

                                    {/* Add element row */}
                                    <div className="flex items-center gap-2">
                                        <select
                                            id="ss-type-select"
                                            defaultValue="loop"
                                            className="bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                        >
                                            <option value="helix">Helix</option>
                                            <option value="sheet">Sheet</option>
                                            <option value="loop">Loop</option>
                                        </select>
                                        <input
                                            type="number"
                                            id="ss-start-input"
                                            placeholder="Start"
                                            min={1}
                                            className="w-20 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                        />
                                        <span className="text-slate-500">-</span>
                                        <input
                                            type="number"
                                            id="ss-end-input"
                                            placeholder="End"
                                            min={1}
                                            className="w-20 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                        />
                                        <button
                                            type="button"
                                            onClick={() => {
                                                const typeEl = document.getElementById('ss-type-select') as HTMLSelectElement;
                                                const startEl = document.getElementById('ss-start-input') as HTMLInputElement;
                                                const endEl = document.getElementById('ss-end-input') as HTMLInputElement;
                                                if (typeEl && startEl && endEl && startEl.value && endEl.value) {
                                                    setSSElements([...ssElements, {
                                                        id: `ss_${Date.now()}`,
                                                        type: typeEl.value as 'helix' | 'sheet' | 'loop',
                                                        start: parseInt(startEl.value),
                                                        end: parseInt(endEl.value)
                                                    }]);
                                                    startEl.value = '';
                                                    endEl.value = '';
                                                }
                                            }}
                                            className="bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                                        >
                                            + Add
                                        </button>
                                    </div>

                                    <p className="text-xs text-slate-600">Add helix, sheet, or loop constraints by residue range</p>
                                </div>
                            )}
                        </div>

                        {/* Covalent Constraints */}
                        <div className="border-t border-slate-700 pt-4">
                            <div className="flex items-center justify-between mb-3">
                                <label className="block text-sm font-medium text-slate-300">Covalent Constraints</label>
                                <button
                                    type="button"
                                    onClick={() => setCovalentBonds([...covalentBonds, {
                                        id: `bond_${Date.now()}`,
                                        type: 'disulfide',
                                        atom1_chain: 'A',
                                        atom1_residue: 1,
                                        atom1_atom: 'SG',
                                        atom2_chain: 'A',
                                        atom2_residue: 10,
                                        atom2_atom: 'SG'
                                    }])}
                                    className="px-3 py-1 text-xs bg-amber-500/20 text-amber-400 rounded-lg hover:bg-amber-500/30 transition-colors"
                                >
                                    + Add Bond
                                </button>
                            </div>
                            {covalentBonds.length === 0 ? (
                                <p className="text-xs text-slate-500">No covalent constraints. Add disulfide bonds, WHL staples, or custom atom connections.</p>
                            ) : (
                                <div className="space-y-3">
                                    {covalentBonds.map((bond, idx) => (
                                        <div key={bond.id} className="p-3 bg-slate-900/50 rounded-lg border border-slate-700">
                                            <div className="flex items-center justify-between mb-2">
                                                <select
                                                    value={bond.type}
                                                    onChange={e => {
                                                        const newType = e.target.value as 'disulfide' | 'whl_staple' | 'custom';
                                                        const updated = [...covalentBonds];
                                                        updated[idx] = {
                                                            ...bond,
                                                            type: newType,
                                                            atom1_atom: newType === 'disulfide' ? 'SG' : newType === 'whl_staple' ? 'SG' : bond.atom1_atom,
                                                            atom2_atom: newType === 'disulfide' ? 'SG' : newType === 'whl_staple' ? 'CK' : bond.atom2_atom
                                                        };
                                                        setCovalentBonds(updated);
                                                    }}
                                                    className="bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-white"
                                                >
                                                    <option value="disulfide">Disulfide (Cys-Cys)</option>
                                                    <option value="whl_staple">WHL Staple</option>
                                                    <option value="custom">Custom Bond</option>
                                                </select>
                                                <button
                                                    type="button"
                                                    onClick={() => setCovalentBonds(covalentBonds.filter(b => b.id !== bond.id))}
                                                    className="text-red-400 hover:text-red-300 text-xs"
                                                >
                                                    Remove
                                                </button>
                                            </div>
                                            <div className="grid grid-cols-6 gap-2 text-xs">
                                                <div>
                                                    <label className="text-slate-500">Chain 1</label>
                                                    <input
                                                        type="text"
                                                        value={bond.atom1_chain}
                                                        onChange={e => {
                                                            const updated = [...covalentBonds];
                                                            updated[idx] = { ...bond, atom1_chain: e.target.value };
                                                            setCovalentBonds(updated);
                                                        }}
                                                        className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-white"
                                                    />
                                                </div>
                                                <div>
                                                    <label className="text-slate-500">Res 1</label>
                                                    <input
                                                        type="number"
                                                        value={bond.atom1_residue}
                                                        onChange={e => {
                                                            const updated = [...covalentBonds];
                                                            updated[idx] = { ...bond, atom1_residue: parseInt(e.target.value) || 1 };
                                                            setCovalentBonds(updated);
                                                        }}
                                                        className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-white"
                                                    />
                                                </div>
                                                <div>
                                                    <label className="text-slate-500">Atom 1</label>
                                                    <input
                                                        type="text"
                                                        value={bond.atom1_atom}
                                                        onChange={e => {
                                                            const updated = [...covalentBonds];
                                                            updated[idx] = { ...bond, atom1_atom: e.target.value };
                                                            setCovalentBonds(updated);
                                                        }}
                                                        disabled={bond.type !== 'custom'}
                                                        className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-white disabled:opacity-50"
                                                    />
                                                </div>
                                                <div>
                                                    <label className="text-slate-500">Chain 2</label>
                                                    <input
                                                        type="text"
                                                        value={bond.atom2_chain}
                                                        onChange={e => {
                                                            const updated = [...covalentBonds];
                                                            updated[idx] = { ...bond, atom2_chain: e.target.value };
                                                            setCovalentBonds(updated);
                                                        }}
                                                        className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-white"
                                                    />
                                                </div>
                                                <div>
                                                    <label className="text-slate-500">Res 2</label>
                                                    <input
                                                        type="number"
                                                        value={bond.atom2_residue}
                                                        onChange={e => {
                                                            const updated = [...covalentBonds];
                                                            updated[idx] = { ...bond, atom2_residue: parseInt(e.target.value) || 1 };
                                                            setCovalentBonds(updated);
                                                        }}
                                                        className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-white"
                                                    />
                                                </div>
                                                <div>
                                                    <label className="text-slate-500">Atom 2</label>
                                                    <input
                                                        type="text"
                                                        value={bond.atom2_atom}
                                                        onChange={e => {
                                                            const updated = [...covalentBonds];
                                                            updated[idx] = { ...bond, atom2_atom: e.target.value };
                                                            setCovalentBonds(updated);
                                                        }}
                                                        disabled={bond.type !== 'custom'}
                                                        className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-white disabled:opacity-50"
                                                    />
                                                </div>
                                            </div>
                                            <p className="text-xs text-slate-500 mt-2">
                                                {bond.type === 'disulfide' ? 'Cysteine SG-SG bond' :
                                                    bond.type === 'whl_staple' ? 'WHL staple: SG connects to WHL CK/CH' :
                                                        'Custom atom-atom covalent bond'}
                                            </p>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Protocol Selection */}
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Protocol</label>
                            <select
                                value={protocol}
                                onChange={e => setProtocol(e.target.value as Protocol)}
                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-white focus:ring-2 focus:ring-amber-500 outline-none"
                            >
                                <option value="protein-anything">Protein (general)</option>
                                <option value="peptide-anything">Peptide (short binders)</option>
                                <option value="protein-small_molecule">Protein + Small Molecule</option>
                                <option value="nanobody-anything">Nanobody</option>
                                <option value="antibody-anything">Antibody (Fab/scFv)</option>
                            </select>
                            <p className="text-xs text-slate-500 mt-1">BoltzGen protocol - affects filtering and defaults</p>
                        </div>

                        {/* Checkpoint Mode Selection */}
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-2">Checkpoint Mode</label>
                            <div className="grid grid-cols-3 gap-2">
                                {[
                                    { id: 'both', name: 'Both', desc: 'Diverse + Adherence (default)' },
                                    { id: 'diverse', name: 'Diverse', desc: 'Higher structural variety' },
                                    { id: 'adherence', name: 'Adherence', desc: 'Closer to target' },
                                ].map(m => (
                                    <button
                                        key={m.id}
                                        type="button"
                                        onClick={() => setCheckpointMode(m.id as CheckpointMode)}
                                        className={`p-3 rounded-lg border text-center transition-all ${checkpointMode === m.id
                                            ? 'bg-amber-500/20 border-amber-500 text-amber-300'
                                            : 'bg-slate-900/50 border-slate-700 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        <div className="font-medium text-sm">{m.name}</div>
                                        <div className="text-xs text-slate-500">{m.desc}</div>
                                    </button>
                                ))}
                            </div>
                            <p className="text-xs text-slate-500 mt-1">
                                {checkpointMode === 'both'
                                    ? 'Uses both checkpoints for balanced design diversity'
                                    : checkpointMode === 'diverse'
                                        ? 'boltzgen1_diverse checkpoint - more structural variation'
                                        : 'boltzgen1_adherence checkpoint - better target adherence'}
                            </p>
                        </div>

                        {/* Pipeline Options */}
                        <div className="flex gap-6 mt-4">
                            <label className="flex items-center gap-3 cursor-pointer">
                                <div className={`w-10 h-6 rounded-full p-1 transition-colors ${skipInverseFolding ? 'bg-amber-500' : 'bg-slate-700'}`}>
                                    <div className={`w-4 h-4 bg-white rounded-full shadow transition-transform ${skipInverseFolding ? 'translate-x-4' : ''}`} />
                                </div>
                                <input type="checkbox" className="hidden" checked={skipInverseFolding} onChange={e => setSkipInverseFolding(e.target.checked)} />
                                <div>
                                    <span className="text-sm text-slate-300">Skip Inverse Folding</span>
                                    <p className="text-xs text-slate-500">Use designed backbones only</p>
                                </div>
                            </label>

                            <label className="flex items-center gap-3 cursor-pointer">
                                <div className={`w-10 h-6 rounded-full p-1 transition-colors ${reuseExisting ? 'bg-amber-500' : 'bg-slate-700'}`}>
                                    <div className={`w-4 h-4 bg-white rounded-full shadow transition-transform ${reuseExisting ? 'translate-x-4' : ''}`} />
                                </div>
                                <input type="checkbox" className="hidden" checked={reuseExisting} onChange={e => setReuseExisting(e.target.checked)} />
                                <div>
                                    <span className="text-sm text-slate-300">Reuse Existing</span>
                                    <p className="text-xs text-slate-500">Resume interrupted run</p>
                                </div>
                            </label>
                        </div>

                        {/* Docking Method Selection */}
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-3">Docking Validation</label>
                            <div className="grid grid-cols-4 gap-2">
                                {[
                                    { id: 'none', name: 'None', desc: 'Skip docking' },
                                    { id: 'diffdock', name: 'DiffDock', desc: 'ML-based' },
                                    { id: 'unidock', name: 'Uni-Dock', desc: 'GPU AutoDock' },
                                    { id: 'both', name: 'Both', desc: 'Compare engines' },
                                ].map(m => (
                                    <button
                                        key={m.id}
                                        type="button"
                                        onClick={() => setDockingMethod(m.id as DockingMethod)}
                                        className={`p-3 rounded-lg border text-center transition-all ${dockingMethod === m.id
                                            ? 'bg-amber-500/20 border-amber-500 text-amber-300'
                                            : 'bg-slate-900/50 border-slate-700 text-slate-300 hover:border-slate-600'
                                            }`}
                                    >
                                        <div className="font-medium text-sm">{m.name}</div>
                                        <div className="text-xs text-slate-500">{m.desc}</div>
                                    </button>
                                ))}
                            </div>
                            <p className="text-xs text-slate-500 mt-2">
                                {dockingMethod === 'both'
                                    ? 'Both engines will run - compare poses and scores for validation'
                                    : dockingMethod === 'unidock'
                                        ? 'Uni-Dock: GPU-accelerated AutoDock Vina for fast screening'
                                        : dockingMethod === 'diffdock'
                                            ? 'DiffDock: Diffusion-based ML docking with confidence scores'
                                            : 'No docking validation - designs will be filtered by BoltzGen metrics only'}
                            </p>
                        </div>

                        {/* Filtering Parameters */}
                        <div className="border-t border-slate-700 pt-4 mt-4">
                            <label className="block text-sm font-medium text-slate-300 mb-3">Filtering Parameters</label>
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <div className="flex items-center justify-between mb-1">
                                        <label className="text-xs text-slate-400">Budget: {budget || 50}</label>
                                        <label className="flex items-center gap-1 cursor-pointer">
                                            <input type="checkbox" checked={customBudget} onChange={e => setCustomBudget(e.target.checked)} className="w-3 h-3 accent-amber-500" />
                                            <span className="text-xs text-slate-500">Custom</span>
                                        </label>
                                    </div>
                                    {customBudget ? (
                                        <input
                                            type="number"
                                            value={budget}
                                            onChange={e => setBudget(e.target.value ? parseInt(e.target.value) : '')}
                                            placeholder="Custom value"
                                            min={1}
                                            className="w-full bg-slate-900 border border-amber-500/50 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                        />
                                    ) : (
                                        <input
                                            type="range"
                                            value={budget || 50}
                                            onChange={e => setBudget(parseInt(e.target.value))}
                                            min={10}
                                            max={200}
                                            step={10}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                                        />
                                    )}
                                    <div className="flex justify-between text-xs text-slate-500 mt-1">
                                        <span>10</span>
                                        <span>200</span>
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1">Min pLDDT: {minPlddt || 70}</label>
                                    <input
                                        type="range"
                                        value={minPlddt || 70}
                                        onChange={e => setMinPlddt(parseInt(e.target.value))}
                                        min={50}
                                        max={95}
                                        step={5}
                                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                                    />
                                    <div className="flex justify-between text-xs text-slate-500 mt-1">
                                        <span>50 (loose)</span>
                                        <span>95 (strict)</span>
                                    </div>
                                </div>
                                <div>
                                    <div className="flex items-center justify-between mb-1">
                                        <label className="text-xs text-slate-400">Max RMSD: {maxRmsd || 2.0}Å</label>
                                        <label className="flex items-center gap-1 cursor-pointer">
                                            <input type="checkbox" checked={customRmsd} onChange={e => setCustomRmsd(e.target.checked)} className="w-3 h-3 accent-amber-500" />
                                            <span className="text-xs text-slate-500">Custom</span>
                                        </label>
                                    </div>
                                    {customRmsd ? (
                                        <input
                                            type="number"
                                            value={maxRmsd}
                                            onChange={e => setMaxRmsd(e.target.value ? parseFloat(e.target.value) : '')}
                                            placeholder="Custom value"
                                            min={0}
                                            step={0.1}
                                            className="w-full bg-slate-900 border border-amber-500/50 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                        />
                                    ) : (
                                        <input
                                            type="range"
                                            value={maxRmsd || 2.0}
                                            onChange={e => setMaxRmsd(parseFloat(e.target.value))}
                                            min={0.5}
                                            max={5.0}
                                            step={0.25}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                                        />
                                    )}
                                    <div className="flex justify-between text-xs text-slate-500 mt-1">
                                        <span>0.5Å (tight)</span>
                                        <span>5.0Å (loose)</span>
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1">Diversity Alpha: {alpha.toFixed(2)}</label>
                                    <input
                                        type="range"
                                        value={alpha}
                                        onChange={e => setAlpha(parseFloat(e.target.value))}
                                        min={0}
                                        max={1}
                                        step={0.01}
                                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                                    />
                                    <div className="flex justify-between text-xs text-slate-500 mt-1">
                                        <span>Quality</span>
                                        <span>Diversity</span>
                                    </div>
                                </div>
                            </div>

                            {/* Additional Filtering Options */}
                            <div className="grid grid-cols-2 gap-4 mt-4">
                                <div>
                                    <label className="block text-xs text-slate-400 mb-1">Min Affinity Score</label>
                                    <input
                                        type="number"
                                        value={minConfScore}
                                        onChange={e => setMinConfScore(e.target.value ? parseFloat(e.target.value) : '')}
                                        placeholder="e.g., 0.5"
                                        min={0}
                                        max={1}
                                        step={0.05}
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                    />
                                    <p className="text-xs text-slate-500 mt-1">Binding probability (0-1)</p>
                                </div>
                                <div className="flex items-center">
                                    <label className="flex items-center gap-3 cursor-pointer">
                                        <div className={`w-10 h-6 rounded-full p-1 transition-colors ${filterBiased ? 'bg-amber-500' : 'bg-slate-700'}`}>
                                            <div className={`w-4 h-4 bg-white rounded-full shadow transition-transform ${filterBiased ? 'translate-x-4' : ''}`} />
                                        </div>
                                        <input type="checkbox" className="hidden" checked={filterBiased} onChange={e => setFilterBiased(e.target.checked)} />
                                        <div>
                                            <span className="text-sm text-slate-300">Filter Biased</span>
                                            <p className="text-xs text-slate-500">Remove AA outliers</p>
                                        </div>
                                    </label>
                                </div>
                            </div>

                            {/* Inverse Folding Options */}
                            <div className="border-t border-slate-700 pt-4 mt-4">
                                <label className="block text-sm font-medium text-slate-300 mb-3">Inverse Folding</label>
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-xs text-slate-400 mb-1">Avoid Residues</label>
                                        <input
                                            type="text"
                                            value={inverseFoldAvoid}
                                            onChange={e => setInverseFoldAvoid(e.target.value.toUpperCase())}
                                            placeholder="e.g., C or KEC"
                                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                        />
                                        <p className="text-xs text-slate-500 mt-1">Disallowed amino acids (1-letter codes)</p>
                                    </div>
                                    <div>
                                        <label className="block text-xs text-slate-400 mb-1">Seqs per Backbone: {inverseFoldNumSeqs}</label>
                                        <input
                                            type="range"
                                            value={inverseFoldNumSeqs}
                                            onChange={e => setInverseFoldNumSeqs(parseInt(e.target.value))}
                                            min={1}
                                            max={8}
                                            step={1}
                                            className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-amber-500"
                                        />
                                        <div className="flex justify-between text-xs text-slate-500 mt-1">
                                            <span>1</span>
                                            <span>8</span>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Diffusion Parameters */}
                            <div className="border-t border-slate-700 pt-4 mt-4">
                                <label className="block text-sm font-medium text-slate-300 mb-3">Diffusion Tuning</label>
                                <div className="grid grid-cols-2 gap-4">
                                    <div>
                                        <div className="flex items-center justify-between mb-1">
                                            <label className="text-xs text-slate-400">Step Scale: {stepScale || 1.8}</label>
                                            <label className="flex items-center gap-1 cursor-pointer">
                                                <input type="checkbox" checked={customStepScale} onChange={e => setCustomStepScale(e.target.checked)} className="w-3 h-3 accent-amber-500" />
                                                <span className="text-xs text-slate-500">Custom</span>
                                            </label>
                                        </div>
                                        {customStepScale ? (
                                            <input
                                                type="number"
                                                value={stepScale}
                                                onChange={e => setStepScale(e.target.value ? parseFloat(e.target.value) : '')}
                                                placeholder="Custom value"
                                                min={0.1}
                                                max={10}
                                                step={0.1}
                                                className="w-full bg-slate-900 border border-amber-500/50 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                            />
                                        ) : (
                                            <input
                                                type="range"
                                                value={stepScale || 1.8}
                                                onChange={e => setStepScale(parseFloat(e.target.value))}
                                                min={0.5}
                                                max={3.0}
                                                step={0.1}
                                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-cyan-500"
                                            />
                                        )}
                                        <div className="flex justify-between text-xs text-slate-500 mt-1">
                                            <span>0.5 (diverse)</span>
                                            <span>3.0 (quality)</span>
                                        </div>
                                    </div>
                                    <div>
                                        <div className="flex items-center justify-between mb-1">
                                            <label className="text-xs text-slate-400">Noise Scale: {noiseScale || 0.98}</label>
                                            <label className="flex items-center gap-1 cursor-pointer">
                                                <input type="checkbox" checked={customNoiseScale} onChange={e => setCustomNoiseScale(e.target.checked)} className="w-3 h-3 accent-amber-500" />
                                                <span className="text-xs text-slate-500">Custom</span>
                                            </label>
                                        </div>
                                        {customNoiseScale ? (
                                            <input
                                                type="number"
                                                value={noiseScale}
                                                onChange={e => setNoiseScale(e.target.value ? parseFloat(e.target.value) : '')}
                                                placeholder="Custom value"
                                                min={0}
                                                max={2}
                                                step={0.01}
                                                className="w-full bg-slate-900 border border-amber-500/50 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                            />
                                        ) : (
                                            <input
                                                type="range"
                                                value={noiseScale || 0.98}
                                                onChange={e => setNoiseScale(parseFloat(e.target.value))}
                                                min={0.7}
                                                max={1.3}
                                                step={0.02}
                                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-purple-500"
                                            />
                                        )}
                                        <div className="flex justify-between text-xs text-slate-500 mt-1">
                                            <span>0.7 (designable)</span>
                                            <span>1.3 (diverse)</span>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Advanced Filtering */}
                            <div className="border-t border-slate-700 pt-4 mt-4">
                                <label className="block text-sm font-medium text-slate-300 mb-3">Advanced Filtering</label>

                                {/* Metric Weights */}
                                <div className="mb-4">
                                    <div className="flex items-center justify-between mb-2">
                                        <label className="text-xs font-medium text-slate-400">Metric Weights</label>
                                        <label className="flex items-center gap-1 cursor-pointer">
                                            <input type="checkbox" checked={customMetrics} onChange={e => setCustomMetrics(e.target.checked)} className="w-3 h-3 accent-amber-500" />
                                            <span className="text-xs text-slate-500">Custom</span>
                                        </label>
                                    </div>
                                    {customMetrics ? (
                                        <input
                                            type="text"
                                            value={metricsOverride}
                                            onChange={e => setMetricsOverride(e.target.value)}
                                            placeholder="e.g., plip_hbonds_refolded=0.5 delta_sasa_refolded=0.5"
                                            className="w-full bg-slate-900 border border-amber-500/50 rounded-lg px-3 py-2 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                        />
                                    ) : (
                                        <div className="grid grid-cols-3 gap-3">
                                            <div>
                                                <label className="text-xs text-slate-500">H-bonds: {hbondWeight.toFixed(1)}</label>
                                                <input
                                                    type="range"
                                                    value={hbondWeight}
                                                    onChange={e => setHbondWeight(parseFloat(e.target.value))}
                                                    min={0.1}
                                                    max={2.0}
                                                    step={0.1}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-rose-500"
                                                />
                                                <div className="flex justify-between text-xs text-slate-600"><span>↑pri</span><span>↓pri</span></div>
                                            </div>
                                            <div>
                                                <label className="text-xs text-slate-500">SASA: {sasaWeight.toFixed(1)}</label>
                                                <input
                                                    type="range"
                                                    value={sasaWeight}
                                                    onChange={e => setSasaWeight(parseFloat(e.target.value))}
                                                    min={0.1}
                                                    max={2.0}
                                                    step={0.1}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                                                />
                                                <div className="flex justify-between text-xs text-slate-600"><span>↑pri</span><span>↓pri</span></div>
                                            </div>
                                            <div>
                                                <label className="text-xs text-slate-500">Contacts: {contactWeight.toFixed(1)}</label>
                                                <input
                                                    type="range"
                                                    value={contactWeight}
                                                    onChange={e => setContactWeight(parseFloat(e.target.value))}
                                                    min={0.1}
                                                    max={2.0}
                                                    step={0.1}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-green-500"
                                                />
                                                <div className="flex justify-between text-xs text-slate-600"><span>↑pri</span><span>↓pri</span></div>
                                            </div>
                                        </div>
                                    )}
                                    <p className="text-xs text-slate-600 mt-1">Lower = higher priority (tighter binding)</p>
                                </div>

                                {/* AA Composition Limits */}
                                <div className="mb-4">
                                    <div className="flex items-center justify-between mb-2">
                                        <label className="text-xs font-medium text-slate-400">AA Composition Limits</label>
                                        <label className="flex items-center gap-1 cursor-pointer">
                                            <input type="checkbox" checked={customFilters} onChange={e => setCustomFilters(e.target.checked)} className="w-3 h-3 accent-amber-500" />
                                            <span className="text-xs text-slate-500">Custom</span>
                                        </label>
                                    </div>
                                    {customFilters ? (
                                        <input
                                            type="text"
                                            value={additionalFilters}
                                            onChange={e => setAdditionalFilters(e.target.value)}
                                            placeholder="e.g., design_CYS<0.02 design_MET<0.05"
                                            className="w-full bg-slate-900 border border-amber-500/50 rounded-lg px-3 py-2 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                        />
                                    ) : (
                                        <div className="grid grid-cols-3 gap-3">
                                            <div>
                                                <label className="text-xs text-slate-500">Max Cys: {(maxCys * 100).toFixed(0)}%</label>
                                                <input
                                                    type="range"
                                                    value={maxCys}
                                                    onChange={e => setMaxCys(parseFloat(e.target.value))}
                                                    min={0}
                                                    max={0.15}
                                                    step={0.01}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-yellow-500"
                                                />
                                                <div className="flex justify-between text-xs text-slate-600"><span>0%</span><span>15%</span></div>
                                            </div>
                                            <div>
                                                <label className="text-xs text-slate-500">Max Met: {(maxMet * 100).toFixed(0)}%</label>
                                                <input
                                                    type="range"
                                                    value={maxMet}
                                                    onChange={e => setMaxMet(parseFloat(e.target.value))}
                                                    min={0}
                                                    max={0.20}
                                                    step={0.01}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-orange-500"
                                                />
                                                <div className="flex justify-between text-xs text-slate-600"><span>0%</span><span>20%</span></div>
                                            </div>
                                            <div>
                                                <label className="text-xs text-slate-500">Max Gly: {(maxGly * 100).toFixed(0)}%</label>
                                                <input
                                                    type="range"
                                                    value={maxGly}
                                                    onChange={e => setMaxGly(parseFloat(e.target.value))}
                                                    min={0}
                                                    max={0.30}
                                                    step={0.01}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-teal-500"
                                                />
                                                <div className="flex justify-between text-xs text-slate-600"><span>0%</span><span>30%</span></div>
                                            </div>
                                        </div>
                                    )}
                                    <p className="text-xs text-slate-600 mt-1">Limit problematic residues (oxidation, flexibility)</p>
                                </div>

                                {/* Size Range */}
                                <div>
                                    <div className="flex items-center justify-between mb-2">
                                        <label className="text-xs font-medium text-slate-400">Size Range: {sizeMin}-{sizeMax} residues</label>
                                        <label className="flex items-center gap-1 cursor-pointer">
                                            <input type="checkbox" checked={customSizeBuckets} onChange={e => setCustomSizeBuckets(e.target.checked)} className="w-3 h-3 accent-amber-500" />
                                            <span className="text-xs text-slate-500">Custom</span>
                                        </label>
                                    </div>
                                    {customSizeBuckets ? (
                                        <input
                                            type="text"
                                            value={sizeBuckets}
                                            onChange={e => setSizeBuckets(e.target.value)}
                                            placeholder="e.g., 10-15:5 15-20:10 20-25:5"
                                            className="w-full bg-slate-900 border border-amber-500/50 rounded-lg px-3 py-2 text-white font-mono text-sm focus:ring-2 focus:ring-amber-500 outline-none"
                                        />
                                    ) : (
                                        <div className="grid grid-cols-2 gap-3">
                                            <div>
                                                <label className="text-xs text-slate-500">Min Size: {sizeMin}</label>
                                                <input
                                                    type="range"
                                                    value={sizeMin}
                                                    onChange={e => setSizeMin(Math.min(parseInt(e.target.value), sizeMax - 5))}
                                                    min={5}
                                                    max={40}
                                                    step={5}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-indigo-500"
                                                />
                                            </div>
                                            <div>
                                                <label className="text-xs text-slate-500">Max Size: {sizeMax}</label>
                                                <input
                                                    type="range"
                                                    value={sizeMax}
                                                    onChange={e => setSizeMax(Math.max(parseInt(e.target.value), sizeMin + 5))}
                                                    min={10}
                                                    max={50}
                                                    step={5}
                                                    className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer accent-indigo-500"
                                                />
                                            </div>
                                        </div>
                                    )}
                                    <p className="text-xs text-slate-600 mt-1">CDR/loop length range (VHH CDR-H3: typically 10-25)</p>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>

            {/* Submit */}
            <div className="flex justify-end gap-3">
                <button
                    onClick={onBack}
                    className="px-6 py-3 rounded-lg bg-slate-700 text-slate-300 hover:bg-slate-600 transition-colors"
                >
                    Cancel
                </button>
                <button
                    onClick={handleSubmit}
                    disabled={!isValid || submitMutation.isPending}
                    className="px-8 py-3 rounded-lg bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-400 hover:to-orange-400 text-white font-semibold disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-lg shadow-amber-500/20"
                >
                    {submitMutation.isPending ? 'Submitting...' : 'Launch BoltzGen'}
                </button>
            </div>
        </div>
    );
}

export default BoltzGenTemplate;
