import { useState, useRef, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob, fetchMsaCacheInfo, type MsaCacheInfo } from '../lib/api';
import { useNavigate } from 'react-router-dom';
import { SequenceManager } from './SequenceManager';
import { LigandSelector, type LigandEntry } from './LigandSelector';
import { TargetAntigenSelector } from './TargetAntigenSelector';
import { parsePDBFile, type Chain } from '../utils/pdbUtils';

interface StructurePredictionTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, any>;
}

export function StructurePredictionTemplate({ onBack, initialValues }: StructurePredictionTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const normalizeProtenixModel = (model?: string) => {
        if (!model) return 'protenix_base_20250630_v1.0.0';
        if (model === 'protenix_base_20241211_v0.2.1') return 'protenix_base_default_v1.0.0';
        if (model === 'protenix_esm_20241211_v0.2.1') return 'protenix_mini_esm_v0.5.0';
        return model;
    };

    // Core state
    const [jobName, setJobName] = useState(initialValues?.name || 'structure_prediction');
    const [pinnedGpus, setPinnedGpus] = useState<number[]>(initialValues?.pinned_gpus ?? []);
    const [lockGpus, setLockGpus] = useState(false);
    const [sequence, setSequence] = useState(initialValues?.sequence || '');
    const [sequenceName, setSequenceName] = useState(initialValues?.sequence_name || 'predicted');

    // Predictor selection
    const [predictor, setPredictor] = useState<'boltz' | 'rf3' | 'protenix' | 'both' | 'all'>(initialValues?.pred_method || 'boltz');

    // Boltz-2 parameters
    const [boltzUseMsa, setBoltzUseMsa] = useState(initialValues?.boltz_use_msa ?? true);
    const [boltzRecyclingSteps, setBoltzRecyclingSteps] = useState(initialValues?.boltz_recycling_steps ?? 3);
    const [boltzSamplingSteps, setBoltzSamplingSteps] = useState(initialValues?.boltz_sampling_steps ?? 50);
    const [boltzNumSamples, setBoltzNumSamples] = useState(initialValues?.boltz_num_samples ?? 1);
    const [boltzUsePotentials, setBoltzUsePotentials] = useState(initialValues?.boltz_use_potentials ?? false);
    const [boltzMethod, setBoltzMethod] = useState(initialValues?.boltz_method || '');
    const [boltzMaxParallelSamples, setBoltzMaxParallelSamples] = useState(initialValues?.boltz_max_parallel_samples ?? 1);

    // RF3 parameters
    const [rf3UseMsa, setRf3UseMsa] = useState(initialValues?.rf3_use_msa ?? true);
    const [rf3NumRecycles, setRf3NumRecycles] = useState(initialValues?.rf3_num_recycles ?? 10);
    const [rf3NumSamples, setRf3NumSamples] = useState(initialValues?.rf3_num_samples ?? 1);

    // Protenix parameters
    const [protenixModelWeights, setProtenixModelWeights] = useState(normalizeProtenixModel(initialValues?.protenix_model_weights));
    const [protenixSeeds, setProtenixSeeds] = useState(initialValues?.protenix_seeds || '42');
    const [protenixNSample, setProtenixNSample] = useState(initialValues?.protenix_n_sample ?? 5);
    const [protenixNStep, setProtenixNStep] = useState(initialValues?.protenix_n_step ?? 200);
    const [protenixNCycle, setProtenixNCycle] = useState(initialValues?.protenix_n_cycle ?? 10);
    const [protenixUseMsa, setProtenixUseMsa] = useState(initialValues?.protenix_use_msa ?? true);
    const [protenixUseTemplate, setProtenixUseTemplate] = useState(initialValues?.protenix_use_template ?? false);

    // Parallel jobs
    const [numParallelJobs, setNumParallelJobs] = useState(initialValues?.num_parallel_jobs ?? 1);

    // Error handling
    const [allowRetries, setAllowRetries] = useState(initialValues?.allow_retries ?? false);

    // MSA Quality Options (advanced)
    const [showMsaOptions, setShowMsaOptions] = useState(false);
    const [msaPreset, setMsaPreset] = useState<'maximum' | 'balanced' | 'fast'>(initialValues?.msa_preset || 'fast');
    const [msaTaxonomy, setMsaTaxonomy] = useState<string>(initialValues?.msa_taxon_list || '');
    // Empty means "use preset default" from run_local_msa.py
    const [msaEvalue, setMsaEvalue] = useState<string>(initialValues?.msa_evalue?.toString() || '');
    const [msaMinSeqId, setMsaMinSeqId] = useState<string>(initialValues?.msa_min_seq_id?.toString() || '');
    const [msaMinCoverage, setMsaMinCoverage] = useState<string>(initialValues?.msa_min_coverage?.toString() || '');
    const [msaMinDepthWarning, setMsaMinDepthWarning] = useState(initialValues?.msa_min_depth_warning ?? 100);
    const [msaMinDepthFail, setMsaMinDepthFail] = useState(initialValues?.msa_min_depth_fail ?? 0);  // 0 = no fail, just warn
    const [msaForceRefresh, setMsaForceRefresh] = useState(false);  // Purge cache for this sequence
    const [msaCacheOnly, setMsaCacheOnly] = useState(initialValues?.msa_cache_only ?? false);  // Skip generation, require cache hit
    const [msaAllowEmptyFallback, setMsaAllowEmptyFallback] = useState(initialValues?.msa_allow_empty_fallback ?? false);
    const [msaCacheInfo, setMsaCacheInfo] = useState<MsaCacheInfo | null>(null);
    const [msaCacheLoading, setMsaCacheLoading] = useState(false);
    const [msaCacheError, setMsaCacheError] = useState<string | null>(null);
    // NEW: Expansion, EnvDB, and Iterations controls
    const [msaUseExpand, setMsaUseExpand] = useState<boolean | undefined>(initialValues?.msa_use_expand);
    const [msaUseEnv, setMsaUseEnv] = useState<boolean | undefined>(initialValues?.msa_use_env);
    const [msaNumIterations, setMsaNumIterations] = useState<number | undefined>(initialValues?.msa_num_iterations);
    const [msaProvider, setMsaProvider] = useState<'local' | 'colabfold_api'>(
        initialValues?.msa_provider === 'colabfold_api' ? 'colabfold_api' : 'local'
    );
    const [colabfoldApiHost, setColabfoldApiHost] = useState<string>(
        initialValues?.colabfold_api_host || 'https://api.colabfold.com'
    );
    const [colabfoldApiMinInterval, setColabfoldApiMinInterval] = useState<number>(
        initialValues?.colabfold_api_min_interval ?? 6
    );
    const [colabfoldApiPollInterval, setColabfoldApiPollInterval] = useState<number>(
        initialValues?.colabfold_api_poll_interval ?? 6
    );

    // Complex components (ligands, DNA, RNA)
    // Initialize from cloned job data if present (complex_components array)
    const [ligands, setLigands] = useState<LigandEntry[]>(() => {
        const components = initialValues?.complex_components;
        if (!components || !Array.isArray(components) || components.length <= 1) {
            return [];
        }
        // First component is the primary protein (goes into sequence)
        // Rest are ligands/DNA/RNA
        return components.slice(1).map((c: any) => ({
            id: c.id || '',
            type: c.type || 'protein',
            sequence: c.sequence,
            ccd: c.ccd,
            smiles: c.smiles,
            name: c.name || `Chain ${c.id}`
        }));
    });

    const [showInputModal, setShowInputModal] = useState(false);
    const [inputModalTab, setInputModalTab] = useState<'library' | 'pdb'>('library');
    const importTargetRef = useRef<'primary' | 'additional'>('primary');  // Use ref to avoid stale closure
    const [parsedChains, setParsedChains] = useState<Chain[]>([]);
    const [selectedChainIndices, setSelectedChainIndices] = useState<Set<number>>(new Set());
    const [sequenceToSave, setSequenceToSave] = useState<{ sequence: string; name: string } | null>(null);

    const submitMutation = useMutation({
        mutationFn: async (data: any) => submitJob(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        }
    });

    const applyMsaPreset = (preset: 'maximum' | 'balanced' | 'fast') => {
        setMsaPreset(preset);
        // Preset selection should clear advanced overrides so behavior matches the preset label.
        setMsaUseExpand(undefined);
        setMsaUseEnv(undefined);
        setMsaNumIterations(undefined);
    };

    const msaNeeded =
        ((predictor === 'boltz' || predictor === 'both' || predictor === 'all') && boltzUseMsa) ||
        ((predictor === 'rf3' || predictor === 'both' || predictor === 'all') && rf3UseMsa) ||
        ((predictor === 'protenix' || predictor === 'all') && protenixUseMsa);

    useEffect(() => {
        if (numParallelJobs > 1 && msaProvider === 'colabfold_api') {
            setMsaProvider('local');
        }
    }, [numParallelJobs, msaProvider]);

    useEffect(() => {
        const normalizedSequence = sequence.replace(/\s+/g, '').trim();

        if (!msaNeeded || !normalizedSequence) {
            setMsaCacheInfo(null);
            setMsaCacheError(null);
            setMsaCacheLoading(false);
            if (msaCacheOnly) {
                setMsaCacheOnly(false);
            }
            return;
        }

        let active = true;
        setMsaCacheLoading(true);
        setMsaCacheError(null);

        const timer = setTimeout(() => {
            fetchMsaCacheInfo(normalizedSequence)
                .then((resp) => {
                    if (!active) return;
                    setMsaCacheInfo(resp.data);
                    if (msaCacheOnly && resp.data.cache_entries < 1) {
                        setMsaCacheOnly(false);
                    }
                })
                .catch((err: any) => {
                    if (!active) return;
                    setMsaCacheInfo(null);
                    setMsaCacheError(err?.response?.data?.detail || err?.message || 'Failed to read MSA cache');
                    if (msaCacheOnly) {
                        setMsaCacheOnly(false);
                    }
                })
                .finally(() => {
                    if (active) {
                        setMsaCacheLoading(false);
                    }
                });
        }, 300);

        return () => {
            active = false;
            clearTimeout(timer);
        };
    }, [sequence, msaNeeded, msaCacheOnly]);

    const msaCacheSummary = msaCacheLoading
        ? 'Cache: checking...'
        : msaCacheError
            ? 'Cache: unavailable'
            : (msaCacheInfo && msaCacheInfo.cache_entries > 0)
                ? `Cache: ${msaCacheInfo.cache_entries} entr${msaCacheInfo.cache_entries === 1 ? 'y' : 'ies'}`
                : 'Cache: none';

    const handleSubmit = () => {
        if (!sequence.trim()) {
            alert('Please enter an amino acid sequence');
            return;
        }

        const params: Record<string, any> = {
            sequence: sequence.trim(),
            sequence_name: sequenceName,
            pred_method: predictor,
            num_parallel_jobs: numParallelJobs,
        };

        // Boltz-2 parameters
        if (predictor === 'boltz' || predictor === 'both') {
            params.boltz_use_msa = boltzUseMsa;
            params.boltz_recycling_steps = boltzRecyclingSteps;
            params.boltz_sampling_steps = boltzSamplingSteps;
            params.boltz_num_samples = boltzNumSamples;
            params.boltz_use_potentials = boltzUsePotentials;
            params.boltz_max_parallel_samples = boltzMaxParallelSamples;
            if (boltzMethod) params.boltz_method = boltzMethod;
        }

        // RF3 parameters
        if (predictor === 'rf3' || predictor === 'both' || predictor === 'all') {
            params.rf3_use_msa = rf3UseMsa;
            params.rf3_num_recycles = rf3NumRecycles;
            params.rf3_num_samples = rf3NumSamples;
        }

        // Protenix parameters
        if (predictor === 'protenix' || predictor === 'all') {
            params.protenix_model_weights = protenixModelWeights;
            params.protenix_seeds = protenixSeeds;
            params.protenix_n_sample = protenixNSample;
            params.protenix_n_step = protenixNStep;
            params.protenix_n_cycle = protenixNCycle;
            params.protenix_use_msa = protenixUseMsa;
            params.protenix_use_template = protenixUseTemplate;
        }

        if (msaNeeded && msaProvider === 'colabfold_api' && numParallelJobs > 1) {
            alert('ColabFold API MSA provider currently supports only single-job submissions (num_parallel_jobs=1).');
            return;
        }

        if (msaNeeded && msaCacheOnly && (!msaCacheInfo || msaCacheInfo.cache_entries < 1)) {
            alert('Use Cache Only is enabled, but no cached MSA exists for this sequence.');
            return;
        }

        // MSA Quality parameters (when MSA is enabled for any predictor)
        if (msaNeeded) {
            params.msa_preset = msaPreset;  // Fast (default), Balanced, or Maximum
            if (msaTaxonomy) params.msa_taxon_list = msaTaxonomy;
            if (msaEvalue) params.msa_evalue = parseFloat(msaEvalue);
            if (msaMinSeqId) params.msa_min_seq_id = parseFloat(msaMinSeqId);
            if (msaMinCoverage) params.msa_min_coverage = parseFloat(msaMinCoverage);
            params.msa_min_depth_warning = msaMinDepthWarning;
            params.msa_min_depth_fail = msaMinDepthFail;
            if (msaForceRefresh && !msaCacheOnly) params.msa_force_refresh = true;
            if (msaCacheOnly) params.msa_cache_only = true;
            if (msaAllowEmptyFallback) params.msa_allow_empty_fallback = true;
            params.msa_provider = msaProvider;
            if (msaProvider === 'colabfold_api') {
                params.colabfold_api_host = colabfoldApiHost.trim() || 'https://api.colabfold.com';
                params.colabfold_api_min_interval = Math.max(0, Number(colabfoldApiMinInterval) || 0);
                params.colabfold_api_poll_interval = Math.max(1, Number(colabfoldApiPollInterval) || 6);
            }
            // NEW: Expansion, EnvDB, and Iterations overrides
            if (msaUseExpand !== undefined) params.msa_use_expand = msaUseExpand;
            if (msaUseEnv !== undefined) params.msa_use_env = msaUseEnv;
            if (msaNumIterations !== undefined) params.msa_num_iterations = msaNumIterations;
        }

        // Complex components
        if (ligands.length > 0) {
            params.complex_components = [
                { type: 'protein', id: 'A', sequence: sequence.trim() },
                ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles, sequence: l.sequence }))
            ];
        }

        const modelId = predictor === 'rf3' ? 'rf3' : predictor === 'protenix' ? 'protenix' : 'boltz2';
        const mode = (ligands.length > 0) ? 'complex' : 'predict';

        submitMutation.mutate({
            name: jobName,
            model_id: modelId,
            mode: mode,
            params: {
                ...params,
                pinned_gpus: pinnedGpus.length > 0 ? pinnedGpus : undefined,
                lock_gpus: lockGpus && pinnedGpus.length > 0,
                allow_retries: allowRetries
            },
            pinned_gpu: pinnedGpus.length === 1 ? pinnedGpus[0] : null
        });

        // Treat force-refresh as a one-shot action to avoid accidental cache-bypass on reruns.
        if (msaForceRefresh) {
            setMsaForceRefresh(false);
        }
    };



    const handlePdbSelect = async (target: any) => {
        if (!target) return;

        try {
            let file: File;
            if (target.type === 'upload' && target.file) {
                file = target.file;
            } else if (target.url) {
                const response = await fetch(target.url);
                const blob = await response.blob();
                file = new File([blob], target.name + '.pdb', { type: 'chemical/x-pdb' });
            } else {
                return;
            }

            const parsed = await parsePDBFile(file);
            if (parsed.chains.length === 1) {
                setSequence(parsed.chains[0].sequence);
                setSequenceName(target.name.replace('.pdb', ''));
                setShowInputModal(false);
                setParsedChains([]);
                setSelectedChainIndices(new Set());
            } else if (parsed.chains.length > 1) {
                setParsedChains(parsed.chains);
                setSequenceName(target.name.replace('.pdb', ''));
                setSelectedChainIndices(new Set());
            } else {
                alert('No protein chains found in PDB');
            }
        } catch (err) {
            console.error('Failed to parse PDB:', err);
            alert('Failed to parse PDB file');
        }
    };



    const handleMultiChainImport = () => {
        const selectedChains = parsedChains.filter((_, i) => selectedChainIndices.has(i));
        if (selectedChains.length === 0) return;

        // Sort by ID to keep order deterministic (A, B, C...)
        selectedChains.sort((a, b) => a.id.localeCompare(b.id));

        // First chain is primary
        const primary = selectedChains[0];
        setSequence(primary.sequence);

        // Others are ligands/complex components
        const others = selectedChains.slice(1);
        if (others.length > 0) {
            const newLigands: LigandEntry[] = others.map(c => ({
                id: c.id,
                type: 'protein',
                sequence: c.sequence,
                name: `Chain ${c.id}`
            }));

            // Append to existing ligands or replace?
            // "1:1 recreate" implies we might want to just set them.
            // But we should be careful not to wipe out manual adds if user intends to mix.
            // For now, let's append.
            setLigands(prev => [...prev, ...newLigands]);
        }

        setShowInputModal(false);
        setParsedChains([]);
        setSelectedChainIndices(new Set());
    };

    const toggleChainSelection = (index: number) => {
        const next = new Set(selectedChainIndices);
        if (next.has(index)) {
            next.delete(index);
        } else {
            next.add(index);
        }
        setSelectedChainIndices(next);
    };

    const showBoltzParams = predictor === 'boltz' || predictor === 'both' || predictor === 'all';
    const showRf3Params = predictor === 'rf3' || predictor === 'both' || predictor === 'all';
    const showProtenixParams = predictor === 'protenix' || predictor === 'all';

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
                        <h2 className="text-lg font-semibold text-slate-200">Structure Prediction</h2>
                        <p className="text-sm text-slate-500">Predict 3D structure from amino acid sequence</p>
                    </div>
                </div>
            </div>

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
                            placeholder="structure_prediction"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-400 mb-2">
                            GPU Pinning {pinnedGpus.length > 0 && <span className="text-blue-400">({pinnedGpus.length} selected)</span>}
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
                                        ? 'bg-blue-600 text-white ring-2 ring-blue-400'
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
                                    className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500"
                                />
                                <span className="text-sm text-slate-400">Lock selected GPU(s) exclusively during workflow</span>
                            </label>
                        )}
                    </div>
                </div>

                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-3">Structure Predictor</label>
                    <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                        {[
                            { id: 'boltz', name: 'Boltz-2', desc: 'Fast, SOTA accuracy', color: 'blue' },
                            { id: 'rf3', name: 'RoseTTAFold3', desc: 'Open-source AF3 alt.', color: 'green' },
                            { id: 'protenix', name: 'Protenix', desc: 'AF3-level, multi-modal', color: 'violet' },
                            { id: 'both', name: 'Boltz + RF3', desc: 'Ensemble (2)', color: 'purple' },
                            { id: 'all', name: 'All Three', desc: 'Full ensemble', color: 'amber' },
                        ].map((pred) => (
                            <button
                                key={pred.id}
                                onClick={() => setPredictor(pred.id as 'boltz' | 'rf3' | 'protenix' | 'both' | 'all')}
                                className={`p-3 rounded-lg border text-left transition-all ${predictor === pred.id
                                    ? pred.color === 'blue' ? 'bg-blue-600/20 border-blue-500 text-blue-300'
                                        : pred.color === 'green' ? 'bg-green-600/20 border-green-500 text-green-300'
                                            : pred.color === 'violet' ? 'bg-violet-600/20 border-violet-500 text-violet-300'
                                                : pred.color === 'amber' ? 'bg-amber-600/20 border-amber-500 text-amber-300'
                                                    : 'bg-accent/20 border-accent text-accent'
                                    : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-600'
                                    }`}
                            >
                                <div className="font-medium mb-1">{pred.name}</div>
                                <div className="text-xs opacity-70">{pred.desc}</div>
                            </button>
                        ))}
                    </div>
                </div>

                {/* Sequence Input */}
                <div>
                    <div className="flex justify-between items-center mb-2">
                        <label className="block text-sm font-medium text-slate-400">
                            Amino Acid Sequence
                            <span className="text-red-400 ml-1">*</span>
                        </label>
                        <div className="flex gap-2 items-center">
                            <button
                                onClick={() => {
                                    importTargetRef.current = 'primary';
                                    setInputModalTab('library');
                                    setShowInputModal(true);
                                }}
                                className="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-300 text-xs rounded-lg transition-colors flex items-center gap-1.5"
                            >
                                <span>📂</span> Select Input / Import
                            </button>
                            {sequence.length > 0 && (
                                <button
                                    onClick={() => {
                                        setSequenceToSave({ sequence, name: sequenceName });
                                        setInputModalTab('library');
                                        setShowInputModal(true);
                                    }}
                                    className="px-3 py-1.5 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 text-xs rounded-lg transition-colors border border-emerald-600/30"
                                >
                                    Save to Library
                                </button>
                            )}
                        </div>
                    </div>
                    <textarea
                        value={sequence}
                        onChange={(e) => setSequence(e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
                        placeholder="Enter amino acid sequence (A-Z)..."
                        rows={5}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-blue-500 outline-none"
                    />
                    {sequence && (
                        <div className="mt-2 flex justify-between items-center text-xs text-slate-500">
                            <span>{sequence.length} aa</span>
                            <button onClick={() => setSequence('')} className="text-red-400 hover:text-red-300">Clear</button>
                        </div>
                    )}
                </div>

                {/* Sequence Name */}
                <div className="grid grid-cols-2 gap-4">
                    <div>
                        <label className="block text-sm font-medium text-slate-400 mb-2">Sequence Name</label>
                        <input
                            type="text"
                            value={sequenceName}
                            onChange={(e) => setSequenceName(e.target.value)}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                            placeholder="predicted"
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-slate-400 mb-2">Parallel Jobs</label>
                        <input
                            type="number"
                            value={numParallelJobs}
                            onChange={(e) => setNumParallelJobs(Math.max(1, Math.min(500, parseInt(e.target.value) || 1)))}
                            min={1}
                            max={500}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                        />
                    </div>
                </div>

                {/* Boltz-2 Parameters */}
                {showBoltzParams && (
                    <div className="border border-slate-700/50 rounded-lg p-4 space-y-4">
                        <h3 className="text-sm font-semibold text-blue-400">Boltz-2 Settings</h3>

                        {/* Physics Potentials Toggle */}
                        <div className="flex items-center gap-3 p-3 bg-slate-900/50 rounded-lg border border-slate-700/50">
                            <input
                                type="checkbox"
                                id="boltz-potentials"
                                checked={boltzUsePotentials}
                                onChange={(e) => setBoltzUsePotentials(e.target.checked)}
                                className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-blue-600"
                            />
                            <label htmlFor="boltz-potentials" className="cursor-pointer">
                                <span className="text-slate-200 font-medium">Use Potentials (Boltz-2x)</span>
                                <p className="text-xs text-slate-500">Enable physics-based potentials. More accurate but slower.</p>
                            </label>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Use MSA</label>
                                <select
                                    value={boltzUseMsa ? 'true' : 'false'}
                                    onChange={(e) => setBoltzUseMsa(e.target.value === 'true')}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                >
                                    <option value="true">Yes</option>
                                    <option value="false">No</option>
                                </select>
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Recycling Steps</label>
                                <input
                                    type="number"
                                    value={boltzRecyclingSteps}
                                    onChange={(e) => setBoltzRecyclingSteps(Math.max(1, Math.min(10, parseInt(e.target.value) || 3)))}
                                    min={1}
                                    max={10}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Sampling Steps</label>
                                <input
                                    type="number"
                                    value={boltzSamplingSteps}
                                    onChange={(e) => setBoltzSamplingSteps(Math.max(10, Math.min(1000, parseInt(e.target.value) || 50)))}
                                    min={10}
                                    max={1000}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Num Samples</label>
                                <input
                                    type="number"
                                    value={boltzNumSamples}
                                    onChange={(e) => setBoltzNumSamples(Math.max(1, Math.min(32, parseInt(e.target.value) || 1)))}
                                    min={1}
                                    max={32}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Max Parallel</label>
                                <input
                                    type="number"
                                    value={boltzMaxParallelSamples}
                                    onChange={(e) => setBoltzMaxParallelSamples(Math.max(1, Math.min(boltzNumSamples, parseInt(e.target.value) || 1)))}
                                    min={1}
                                    max={boltzNumSamples}
                                    title="Max samples to run in parallel (1 = serial, lower VRAM usage)"
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                        </div>

                        <div>
                            <label className="text-xs text-slate-400 block mb-1">Conditioning Method</label>
                            <select
                                value={boltzMethod}
                                onChange={(e) => setBoltzMethod(e.target.value)}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                            >
                                <option value="">None (Standard Folding)</option>
                                <option value="md">Molecular Dynamics</option>
                                <option value="x-ray diffraction">X-ray Diffraction</option>
                                <option value="electron microscopy">Electron Microscopy</option>
                                <option value="solution nmr">Solution NMR</option>
                                <option value="solid-state nmr">Solid-State NMR</option>
                                <option value="afdb">AlphaFold DB</option>
                                <option value="boltz-1">Boltz-1</option>
                            </select>
                        </div>
                    </div>
                )}

                {/* RF3 Parameters */}
                {showRf3Params && (
                    <div className="border border-slate-700/50 rounded-lg p-4 space-y-4">
                        <h3 className="text-sm font-semibold text-green-400">RoseTTAFold3 Settings</h3>
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Use MSA</label>
                                <select
                                    value={rf3UseMsa ? 'true' : 'false'}
                                    onChange={(e) => setRf3UseMsa(e.target.value === 'true')}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                >
                                    <option value="true">Yes</option>
                                    <option value="false">No</option>
                                </select>
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Recycle Iterations</label>
                                <input
                                    type="number"
                                    value={rf3NumRecycles}
                                    onChange={(e) => setRf3NumRecycles(Math.max(1, Math.min(20, parseInt(e.target.value) || 10)))}
                                    min={1}
                                    max={20}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Num Samples</label>
                                <input
                                    type="number"
                                    value={rf3NumSamples}
                                    onChange={(e) => setRf3NumSamples(Math.max(1, Math.min(32, parseInt(e.target.value) || 1)))}
                                    min={1}
                                    max={32}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                        </div>
                    </div>
                )}

                {/* Protenix Parameters */}
                {showProtenixParams && (
                    <div className="border border-slate-700/50 rounded-lg p-4 space-y-4">
                        <h3 className="text-sm font-semibold text-violet-400">Protenix Settings</h3>

                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <div className="col-span-2">
                                <label className="text-xs text-slate-400 block mb-1">Model Variant</label>
                                <select
                                    value={protenixModelWeights}
                                    onChange={(e) => setProtenixModelWeights(e.target.value)}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                >
                                    <option value="protenix_base_20250630_v1.0.0">Base 2025-06-30 v1.0.0 (Latest)</option>
                                    <option value="protenix_base_default_v1.0.0">Base Default v1.0.0</option>
                                    <option value="protenix_mini_esm_v0.5.0">Mini ESM v0.5.0 (Light)</option>
                                    <option value="protenix_mini_default_v0.5.0">Mini Default v0.5.0</option>
                                </select>
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Use MSA</label>
                                <select
                                    value={protenixUseMsa ? 'true' : 'false'}
                                    onChange={(e) => {
                                        const useMsa = e.target.value === 'true';
                                        setProtenixUseMsa(useMsa);
                                        // Auto-switch to ESM model when MSA disabled
                                        if (!useMsa && !protenixModelWeights.includes('esm')) {
                                            setProtenixModelWeights('protenix_mini_esm_v0.5.0');
                                        }
                                    }}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                >
                                    <option value="true">Yes</option>
                                    <option value="false">No (ESM)</option>
                                </select>
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Use Templates</label>
                                <select
                                    value={protenixUseTemplate ? 'true' : 'false'}
                                    onChange={(e) => setProtenixUseTemplate(e.target.value === 'true')}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                >
                                    <option value="false">No</option>
                                    <option value="true">Yes (HMMER)</option>
                                </select>
                            </div>
                        </div>
                        {protenixUseTemplate && (
                            <p className="text-xs text-amber-300/90">
                                Template mode needs local mmCIF data at <code className="text-amber-200">.protenix_cache/mmcif</code>. If this
                                directory is empty, submission will be rejected.
                            </p>
                        )}

                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Seeds</label>
                                <input
                                    type="text"
                                    value={protenixSeeds}
                                    onChange={(e) => setProtenixSeeds(e.target.value.replace(/[^0-9,]/g, ''))}
                                    placeholder="42,123,456"
                                    title="Comma-separated random seeds"
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Samples/Seed</label>
                                <input
                                    type="number"
                                    value={protenixNSample}
                                    onChange={(e) => setProtenixNSample(Math.max(1, Math.min(32, parseInt(e.target.value) || 5)))}
                                    min={1}
                                    max={32}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Diffusion Steps</label>
                                <input
                                    type="number"
                                    value={protenixNStep}
                                    onChange={(e) => setProtenixNStep(Math.max(10, Math.min(1000, parseInt(e.target.value) || 200)))}
                                    min={10}
                                    max={1000}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Recycle Iter.</label>
                                <input
                                    type="number"
                                    value={protenixNCycle}
                                    onChange={(e) => setProtenixNCycle(Math.max(1, Math.min(20, parseInt(e.target.value) || 10)))}
                                    min={1}
                                    max={20}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                        </div>
                    </div>
                )}

                {/* MSA Quality Options (Advanced) */}
                {((showBoltzParams && boltzUseMsa) || (showRf3Params && rf3UseMsa) || (showProtenixParams && protenixUseMsa)) && (
                    <div className="border border-[var(--border-primary)] rounded-lg overflow-hidden">
                        <button
                            onClick={() => setShowMsaOptions(!showMsaOptions)}
                            className="w-full flex items-center justify-between p-3 bg-[var(--bg-tertiary)] hover:bg-[var(--bg-secondary)] transition-colors"
                        >
                            <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-[var(--text-primary)]">MSA Quality Options</span>
                                <span className="text-xs text-[var(--text-muted)]">(Advanced)</span>
                                <span className="text-xs text-[var(--text-muted)]">{msaCacheSummary}</span>
                            </div>
                            <span className="text-[var(--text-secondary)] text-sm">{showMsaOptions ? '▼' : '▶'}</span>
                        </button>
                        {showMsaOptions && (
                            <div className="p-4 space-y-4 bg-[var(--bg-secondary)]">
                                <div className="grid grid-cols-1 md:grid-cols-4 gap-3 pb-2 border-b border-[var(--border-primary)]">
                                    <div className="md:col-span-2">
                                        <label className="text-xs text-[var(--text-secondary)] block mb-1">MSA Provider</label>
                                        <select
                                            value={msaProvider}
                                            onChange={(e) => setMsaProvider(e.target.value as 'local' | 'colabfold_api')}
                                            className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        >
                                            <option value="local">Local MMseqs2 (recommended)</option>
                                            <option value="colabfold_api" disabled={numParallelJobs > 1}>
                                                ColabFold API (single-job only)
                                            </option>
                                        </select>
                                    </div>
                                    <div className="md:col-span-2 text-xs text-[var(--text-muted)] flex items-end">
                                        {numParallelJobs > 1
                                            ? 'Remote ColabFold API is disabled when parallel jobs > 1.'
                                            : 'Remote mode uses paced ticket submission to avoid hammering shared API infrastructure.'}
                                    </div>
                                </div>

                                {msaProvider === 'colabfold_api' && (
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 p-3 rounded-lg border border-cyan-500/30 bg-cyan-500/5">
                                        <div>
                                            <label className="text-xs text-[var(--text-secondary)] block mb-1">ColabFold API Host</label>
                                            <input
                                                type="text"
                                                value={colabfoldApiHost}
                                                onChange={(e) => setColabfoldApiHost(e.target.value)}
                                                className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                            />
                                        </div>
                                        <div>
                                            <label className="text-xs text-[var(--text-secondary)] block mb-1">Min Submit Interval (s)</label>
                                            <input
                                                type="number"
                                                min={0}
                                                step={1}
                                                value={colabfoldApiMinInterval}
                                                onChange={(e) => setColabfoldApiMinInterval(Math.max(0, parseInt(e.target.value) || 0))}
                                                className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                            />
                                        </div>
                                        <div>
                                            <label className="text-xs text-[var(--text-secondary)] block mb-1">Poll Interval (s)</label>
                                            <input
                                                type="number"
                                                min={1}
                                                step={1}
                                                value={colabfoldApiPollInterval}
                                                onChange={(e) => setColabfoldApiPollInterval(Math.max(1, parseInt(e.target.value) || 6))}
                                                className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                            />
                                        </div>
                                        <p className="md:col-span-3 text-xs text-cyan-200/80">
                                            Remote provider is scoped to single structure-prediction jobs in this release.
                                        </p>
                                    </div>
                                )}

                                {/* MSA Quality Preset - Primary Setting */}
                                <div>
                                    <label className="text-sm font-medium text-[var(--text-primary)] block mb-2">MSA Quality Preset</label>
                                    <div className="grid grid-cols-3 gap-2">
                                        <button
                                            type="button"
                                            onClick={() => applyMsaPreset('maximum')}
                                            className={`p-3 rounded-lg border text-left transition-colors ${msaPreset === 'maximum'
                                                ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10'
                                                : 'border-[var(--border-primary)] hover:border-[var(--border-secondary)]'
                                                }`}
                                        >
                                            <div className="text-sm font-medium text-[var(--text-primary)]">Maximum</div>
                                            <div className="text-xs text-[var(--text-muted)] mt-1">Full ColabFold workflow with environmental DB. Best quality. ~15-30s</div>
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => applyMsaPreset('balanced')}
                                            className={`p-3 rounded-lg border text-left transition-colors ${msaPreset === 'balanced'
                                                ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10'
                                                : 'border-[var(--border-primary)] hover:border-[var(--border-secondary)]'
                                                }`}
                                        >
                                            <div className="text-sm font-medium text-[var(--text-primary)]">Balanced</div>
                                            <div className="text-xs text-[var(--text-muted)] mt-1">Environmental search, no expansion. Good quality. ~8-15s</div>
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => applyMsaPreset('fast')}
                                            className={`p-3 rounded-lg border text-left transition-colors ${msaPreset === 'fast'
                                                ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10'
                                                : 'border-[var(--border-primary)] hover:border-[var(--border-secondary)]'
                                                }`}
                                        >
                                            <div className="text-sm font-medium text-[var(--text-primary)]">Fast</div>
                                            <div className="text-xs text-[var(--text-muted)] mt-1">UniRef30 only. Quick screening. ~3-5s</div>
                                        </button>
                                    </div>
                                </div>

                                {/* NEW: Expansion, EnvDB, and Iterations Controls */}
                                <div className="grid grid-cols-3 gap-4 pt-2 border-t border-[var(--border-primary)]">
                                    <label className="flex items-center gap-2 p-2 rounded-lg bg-[var(--bg-tertiary)] cursor-pointer hover:bg-[var(--bg-primary)] transition-colors">
                                        <input
                                            type="checkbox"
                                            checked={msaUseExpand ?? msaPreset === 'maximum'}
                                            onChange={(e) => setMsaUseExpand(e.target.checked)}
                                            className="w-4 h-4 rounded bg-[var(--bg-primary)] border-[var(--border-primary)]"
                                        />
                                        <div>
                                            <span className="text-sm text-[var(--text-primary)] font-medium">Expansion</span>
                                            <p className="text-xs text-[var(--text-muted)]">Deeper homolog coverage</p>
                                        </div>
                                    </label>
                                    <label className="flex items-center gap-2 p-2 rounded-lg bg-[var(--bg-tertiary)] cursor-pointer hover:bg-[var(--bg-primary)] transition-colors">
                                        <input
                                            type="checkbox"
                                            checked={msaUseEnv ?? msaPreset !== 'fast'}
                                            onChange={(e) => setMsaUseEnv(e.target.checked)}
                                            className="w-4 h-4 rounded bg-[var(--bg-primary)] border-[var(--border-primary)]"
                                        />
                                        <div>
                                            <span className="text-sm text-[var(--text-primary)] font-medium">EnvDB</span>
                                            <p className="text-xs text-[var(--text-muted)]">Environmental sequences</p>
                                        </div>
                                    </label>
                                    <div className="flex items-center gap-2 p-2 rounded-lg bg-[var(--bg-tertiary)]">
                                        <div>
                                            <span className="text-sm text-[var(--text-primary)] font-medium">Iterations</span>
                                            <p className="text-xs text-[var(--text-muted)]">Search passes</p>
                                        </div>
                                        <input
                                            type="number"
                                            min={1}
                                            max={5}
                                            value={msaNumIterations ?? (msaPreset === 'maximum' ? 3 : msaPreset === 'balanced' ? 2 : 1)}
                                            onChange={(e) => setMsaNumIterations(parseInt(e.target.value) || undefined)}
                                            className="w-14 bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1 text-[var(--text-primary)] text-sm"
                                        />
                                    </div>
                                </div>

                                <p className="text-xs text-[var(--text-muted)]">
                                    Advanced options below can override preset defaults.
                                    Use taxonomy filtering to restrict to relevant organisms.
                                </p>
                                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                                    {/* Taxonomy Filter */}
                                    <div>
                                        <label className="text-xs text-[var(--text-secondary)] block mb-1">Taxonomy Filter</label>
                                        <select
                                            value={msaTaxonomy}
                                            onChange={(e) => setMsaTaxonomy(e.target.value)}
                                            className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        >
                                            <option value="">All organisms</option>
                                            <option value="2">Bacteria only</option>
                                            <option value="2157">Archaea only</option>
                                            <option value="2759">Eukaryota only</option>
                                            <option value="10239">Viruses only</option>
                                            <option value="2,2157">Prokaryotes (Bacteria + Archaea)</option>
                                        </select>
                                    </div>
                                    {/* E-value */}
                                    <div>
                                        <label className="text-xs text-[var(--text-secondary)] block mb-1">E-value Threshold</label>
                                        <select
                                            value={msaEvalue}
                                            onChange={(e) => setMsaEvalue(e.target.value)}
                                            className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        >
                                            <option value="">Preset default</option>
                                            <option value="1">1 (Very relaxed)</option>
                                            <option value="0.1">0.1</option>
                                            <option value="0.01">0.01</option>
                                            <option value="0.001">0.001</option>
                                            <option value="0.0001">0.0001 (Strict)</option>
                                            <option value="0.00001">0.00001 (Very strict)</option>
                                        </select>
                                    </div>
                                    {/* Min Seq Identity */}
                                    <div>
                                        <label className="text-xs text-[var(--text-secondary)] block mb-1">Min Sequence ID</label>
                                        <select
                                            value={msaMinSeqId}
                                            onChange={(e) => setMsaMinSeqId(e.target.value)}
                                            className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        >
                                            <option value="">No minimum</option>
                                            <option value="0.1">10%</option>
                                            <option value="0.2">20%</option>
                                            <option value="0.3">30%</option>
                                            <option value="0.4">40%</option>
                                            <option value="0.5">50%</option>
                                        </select>
                                    </div>
                                    {/* Min Coverage */}
                                    <div>
                                        <label className="text-xs text-[var(--text-secondary)] block mb-1">Min Coverage</label>
                                        <select
                                            value={msaMinCoverage}
                                            onChange={(e) => setMsaMinCoverage(e.target.value)}
                                            className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                        >
                                            <option value="">No minimum</option>
                                            <option value="0.3">30%</option>
                                            <option value="0.5">50%</option>
                                            <option value="0.7">70%</option>
                                            <option value="0.8">80%</option>
                                            <option value="0.9">90%</option>
                                        </select>
                                    </div>
                                    {/* Depth Warning */}
                                    <div>
                                        <label className="text-xs text-[var(--text-secondary)] block mb-1">Depth Warning</label>
                                        <input
                                            type="number"
                                            value={msaMinDepthWarning}
                                            onChange={(e) => setMsaMinDepthWarning(Math.max(0, parseInt(e.target.value) || 100))}
                                            min={0}
                                            className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                            title="Warn if MSA has fewer sequences"
                                        />
                                    </div>
                                    {/* Depth Fail */}
                                    <div>
                                        <label className="text-xs text-[var(--text-secondary)] block mb-1">Depth Fail</label>
                                        <input
                                            type="number"
                                            value={msaMinDepthFail}
                                            onChange={(e) => setMsaMinDepthFail(Math.max(0, parseInt(e.target.value) || 10))}
                                            min={0}
                                            className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                            title="Fail job if MSA has fewer sequences (0 = no fail)"
                                        />
                                    </div>
                                </div>
                                <div className="p-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)]">
                                    {msaCacheLoading ? (
                                        <p className="text-xs text-[var(--text-muted)]">Checking local MSA cache...</p>
                                    ) : msaCacheError ? (
                                        <p className="text-xs text-[var(--error)]">{msaCacheError}</p>
                                    ) : msaCacheInfo && msaCacheInfo.cache_entries > 0 ? (
                                        <div className="space-y-1">
                                            <p className="text-sm text-[var(--text-primary)] font-medium">
                                                Cached MSA found: {msaCacheInfo.cache_entries} entr{msaCacheInfo.cache_entries === 1 ? 'y' : 'ies'}
                                            </p>
                                            <p className="text-xs text-[var(--text-muted)]">
                                                Canonical cache: {msaCacheInfo.canonical_exists ? 'yes' : 'no'} | Best depth: {msaCacheInfo.best_depth ?? 'unknown'}
                                            </p>
                                        </div>
                                    ) : (
                                        <p className="text-xs text-[var(--text-muted)]">No cached MSA found for this sequence.</p>
                                    )}
                                </div>
                                <label className="flex items-center gap-3 p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-lg cursor-pointer hover:bg-emerald-500/20 transition-colors">
                                    <input
                                        type="checkbox"
                                        checked={msaCacheOnly}
                                        onChange={(e) => {
                                            const enabled = e.target.checked;
                                            setMsaCacheOnly(enabled);
                                            if (enabled) {
                                                setMsaForceRefresh(false);
                                            }
                                        }}
                                        disabled={!msaCacheLoading && (!msaCacheInfo || msaCacheInfo.cache_entries < 1)}
                                        className="w-4 h-4 rounded bg-[var(--bg-primary)] border-emerald-500 text-emerald-400 focus:ring-emerald-500 disabled:opacity-50"
                                    />
                                    <div>
                                        <span className="text-emerald-300 font-medium">Use Existing Cache Only</span>
                                        <p className="text-xs text-emerald-200/70">Skip MSA generation. Job fails if cache is missing.</p>
                                    </div>
                                </label>
                                {/* Force Refresh Toggle */}
                                <label className="flex items-center gap-3 p-3 bg-[var(--error)]/10 border border-[var(--error)]/30 rounded-lg cursor-pointer hover:bg-[var(--error)]/20 transition-colors">
                                    <input
                                        type="checkbox"
                                        checked={msaForceRefresh}
                                        onChange={(e) => {
                                            const enabled = e.target.checked;
                                            setMsaForceRefresh(enabled);
                                            if (enabled) {
                                                setMsaCacheOnly(false);
                                            }
                                        }}
                                        className="w-4 h-4 rounded bg-[var(--bg-primary)] border-[var(--error)] text-[var(--error)] focus:ring-[var(--error)]"
                                    />
                                    <div>
                                        <span className="text-[var(--error)] font-medium">Regenerate MSA (Purge Cache)</span>
                                        <p className="text-xs text-[var(--error)]/70">Force fresh MSA search, ignoring cached results for this sequence</p>
                                    </div>
                                </label>
                                <label className="flex items-center gap-3 p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg cursor-pointer hover:bg-amber-500/20 transition-colors">
                                    <input
                                        type="checkbox"
                                        checked={msaAllowEmptyFallback}
                                        onChange={(e) => setMsaAllowEmptyFallback(e.target.checked)}
                                        className="w-4 h-4 rounded bg-[var(--bg-primary)] border-amber-500 text-amber-400 focus:ring-amber-500"
                                    />
                                    <div>
                                        <span className="text-amber-300 font-medium">Allow Empty MSA Fallback</span>
                                        <p className="text-xs text-amber-200/70">If chain MSA generation fails, continue with `msa: empty` instead of failing complex prep</p>
                                    </div>
                                </label>
                                <div className="text-xs text-[var(--text-muted)] bg-[var(--bg-tertiary)] p-2 rounded">
                                    <strong>Tip:</strong> For prokaryotic proteins (e.g., RepA), set Taxonomy Filter to "Bacteria only" or "Prokaryotes".
                                    For eukaryotic proteins, use "Eukaryota only".
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* Complex Components (Ligands, DNA, RNA) */}
                <LigandSelector
                    ligands={ligands}
                    setLigands={setLigands}
                    showCustomSmiles={true}
                    onImportProtein={() => {
                        importTargetRef.current = 'additional';
                        setInputModalTab('library');
                        setShowInputModal(true);
                    }}
                />

                {/* Submit */}
                <div className="flex justify-between items-center pt-6 border-t border-slate-800">
                    {/* Left side: Allow Retries checkbox */}
                    <label className="flex items-center gap-2 cursor-pointer text-slate-400 hover:text-slate-300">
                        <input
                            type="checkbox"
                            checked={allowRetries}
                            onChange={e => setAllowRetries(e.target.checked)}
                            className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-amber-500 focus:ring-amber-500"
                        />
                        <span className="text-sm">Allow Retries</span>
                        <span className="text-xs text-slate-500">(retry OOM errors)</span>
                    </label>

                    {/* Right side: Submit button */}
                    <button
                        onClick={handleSubmit}
                        disabled={!sequence.trim() || submitMutation.isPending}
                        className="px-6 py-3 bg-gradient-to-r from-blue-600 to-accent-secondary hover:from-blue-500 hover:to-accent disabled:opacity-50 disabled:grayscale text-white font-bold rounded-lg shadow-lg shadow-accent/20 transition-all transform active:scale-95"
                    >
                        {submitMutation.isPending ? 'Submitting...' : 'Launch Prediction'}
                    </button>
                </div>
            </div>

            {/* Unified Input Modal */}
            {showInputModal && (
                <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
                    <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-4xl max-h-[85vh] h-[80vh] flex flex-col shadow-2xl">
                        {/* Header with Tabs */}
                        <div className="flex border-b border-slate-700 bg-slate-800/50 rounded-t-xl">
                            <button
                                onClick={() => setInputModalTab('library')}
                                className={`flex-1 py-4 text-sm font-medium transition-colors border-b-2 ${inputModalTab === 'library'
                                    ? 'border-emerald-500 text-emerald-400 bg-slate-800'
                                    : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                                    }`}
                            >
                                Sequence Library
                            </button>
                            <button
                                onClick={() => setInputModalTab('pdb')}
                                className={`flex-1 py-4 text-sm font-medium transition-colors border-b-2 ${inputModalTab === 'pdb'
                                    ? 'border-blue-500 text-blue-400 bg-slate-800'
                                    : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                                    }`}
                            >
                                Import from PDB
                            </button>
                            <button
                                onClick={() => setShowInputModal(false)}
                                className="px-5 text-slate-400 hover:text-white hover:bg-slate-800/50 rounded-tr-xl transition-colors"
                            >
                                ✕
                            </button>
                        </div>

                        {/* Content */}
                        <div className="flex-1 overflow-hidden relative">
                            {inputModalTab === 'library' && (
                                <div className="absolute inset-0 p-5 overflow-auto">
                                    <SequenceManager
                                        onSelect={(seq) => {
                                            if (importTargetRef.current === 'additional') {
                                                // Add as additional protein chain
                                                setLigands(prev => [...prev, {
                                                    id: String.fromCharCode(66 + prev.length), // B, C, D...
                                                    type: 'protein',
                                                    sequence: seq.sequence,
                                                    name: seq.name || `Protein Chain (${seq.sequence.length}aa)`
                                                }]);
                                            } else {
                                                // Set as primary sequence
                                                setSequence(seq.sequence);
                                                setSequenceName(seq.name);
                                            }
                                            setShowInputModal(false);
                                        }}
                                        initialSequence={sequenceToSave?.sequence}
                                        initialName={sequenceToSave?.name}
                                        onClose={() => setShowInputModal(false)}
                                    />
                                </div>
                            )}

                            {inputModalTab === 'pdb' && (
                                <div className="absolute inset-0 p-5 overflow-auto">
                                    {parsedChains.length > 0 ? (
                                        <div className="space-y-4">
                                            <div className="flex items-center justify-between">
                                                <h3 className="text-lg font-medium text-slate-200">Select Chain</h3>
                                                <button
                                                    onClick={() => setParsedChains([])}
                                                    className="text-sm text-slate-400 hover:text-white"
                                                >
                                                    ← Back to search
                                                </button>
                                            </div>
                                            <p className="text-sm text-slate-500">
                                                Multiple chains found in PDB file. Please select one to use as input.
                                            </p>
                                            <div className="grid gap-2">
                                                {parsedChains.map((chain, i) => {
                                                    const isSelected = selectedChainIndices.has(i);
                                                    return (
                                                        <div
                                                            key={i}
                                                            onClick={() => toggleChainSelection(i)}
                                                            className={`flex items-center justify-between p-3 border rounded-lg cursor-pointer transition-colors ${isSelected
                                                                ? 'bg-blue-600/20 border-blue-500'
                                                                : 'bg-slate-800 border-slate-700 hover:border-slate-500'
                                                                }`}
                                                        >
                                                            <div className="flex items-center gap-3">
                                                                <div className={`w-5 h-5 rounded border flex items-center justify-center ${isSelected
                                                                    ? 'bg-blue-500 border-blue-500 text-white'
                                                                    : 'border-slate-500 bg-transparent'
                                                                    }`}>
                                                                    {isSelected && <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" /></svg>}
                                                                </div>
                                                                <div>
                                                                    <div className={`font-medium ${isSelected ? 'text-blue-300' : 'text-slate-300'}`}>
                                                                        Chain {chain.id}
                                                                    </div>
                                                                </div>
                                                            </div>
                                                            <div className="text-xs font-mono text-slate-400 bg-slate-900/50 px-2 py-1 rounded">
                                                                {chain.sequence.length} aa
                                                            </div>
                                                        </div>
                                                    );
                                                })}
                                            </div>

                                            {selectedChainIndices.size > 0 && (
                                                <div className="pt-4 flex justify-end">
                                                    <button
                                                        onClick={handleMultiChainImport}
                                                        className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition-colors shadow-lg shadow-blue-900/20"
                                                    >
                                                        Import {selectedChainIndices.size} Chain{selectedChainIndices.size > 1 ? 's' : ''}
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    ) : (
                                        <TargetAntigenSelector
                                            onSelect={handlePdbSelect}
                                        />
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

export default StructurePredictionTemplate;
