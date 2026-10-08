import { useState, useRef } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob } from '../lib/api';
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

    // Core state
    const [jobName, setJobName] = useState(initialValues?.name || 'structure_prediction');
    const [pinnedGpus, setPinnedGpus] = useState<number[]>(initialValues?.pinned_gpus ?? []);
    const [lockGpus, setLockGpus] = useState(false);
    const [sequence, setSequence] = useState(initialValues?.sequence || '');
    const [sequenceName, setSequenceName] = useState(initialValues?.sequence_name || 'predicted');

    // Predictor selection
    const [predictor, setPredictor] = useState<'boltz' | 'rf3' | 'both'>(initialValues?.pred_method || 'boltz');

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

    // Parallel jobs
    const [numParallelJobs, setNumParallelJobs] = useState(initialValues?.num_parallel_jobs ?? 1);

    // Error handling
    const [allowRetries, setAllowRetries] = useState(initialValues?.allow_retries ?? false);

    // MSA Quality Options (advanced)
    const [showMsaOptions, setShowMsaOptions] = useState(false);
    const [msaPreset, setMsaPreset] = useState<'maximum' | 'balanced' | 'fast'>(initialValues?.msa_preset || 'maximum');
    const [msaTaxonomy, setMsaTaxonomy] = useState<string>(initialValues?.msa_taxon_list || '');
    const [msaEvalue, setMsaEvalue] = useState<string>(initialValues?.msa_evalue?.toString() || '0.001');
    const [msaMinSeqId, setMsaMinSeqId] = useState<string>(initialValues?.msa_min_seq_id?.toString() || '');
    const [msaMinCoverage, setMsaMinCoverage] = useState<string>(initialValues?.msa_min_coverage?.toString() || '');
    const [msaMinDepthWarning, setMsaMinDepthWarning] = useState(initialValues?.msa_min_depth_warning ?? 100);
    const [msaMinDepthFail, setMsaMinDepthFail] = useState(initialValues?.msa_min_depth_fail ?? 0);  // 0 = no fail, just warn
    const [msaForceRefresh, setMsaForceRefresh] = useState(false);  // Purge cache for this sequence

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
        if (predictor === 'rf3' || predictor === 'both') {
            params.rf3_use_msa = rf3UseMsa;
            params.rf3_num_recycles = rf3NumRecycles;
            params.rf3_num_samples = rf3NumSamples;
        }

        // MSA Quality parameters (when MSA is enabled)
        if ((predictor === 'boltz' && boltzUseMsa) || (predictor === 'rf3' && rf3UseMsa) || predictor === 'both') {
            params.msa_preset = msaPreset;  // Maximum (default), Balanced, or Fast
            if (msaTaxonomy) params.msa_taxon_list = msaTaxonomy;
            if (msaEvalue) params.msa_evalue = parseFloat(msaEvalue);
            if (msaMinSeqId) params.msa_min_seq_id = parseFloat(msaMinSeqId);
            if (msaMinCoverage) params.msa_min_coverage = parseFloat(msaMinCoverage);
            params.msa_min_depth_warning = msaMinDepthWarning;
            params.msa_min_depth_fail = msaMinDepthFail;
            if (msaForceRefresh) params.msa_force_refresh = true;
        }

        // Complex components
        if (ligands.length > 0) {
            params.complex_components = [
                { type: 'protein', id: 'A', sequence: sequence.trim() },
                ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles, sequence: l.sequence }))
            ];
        }

        const modelId = predictor === 'rf3' ? 'rf3' : 'boltz2';

        submitMutation.mutate({
            name: jobName,
            model_id: modelId,
            mode: 'predict',
            params: {
                ...params,
                pinned_gpus: pinnedGpus.length > 0 ? pinnedGpus : undefined,
                lock_gpus: lockGpus && pinnedGpus.length > 0,
                allow_retries: allowRetries
            },
            pinned_gpu: pinnedGpus.length === 1 ? pinnedGpus[0] : null
        });
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

    const showBoltzParams = predictor === 'boltz' || predictor === 'both';
    const showRf3Params = predictor === 'rf3' || predictor === 'both';

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

                {/* Predictor Selection - Card Style */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-3">Structure Predictor</label>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        {[
                            { id: 'boltz', name: 'Boltz-2', desc: 'Fast, SOTA accuracy', color: 'blue' },
                            { id: 'rf3', name: 'RoseTTAFold3', desc: 'Open-source AF3 alternative', color: 'green' },
                            { id: 'both', name: 'Ensemble (Both)', desc: 'Run in parallel', color: 'purple' },
                        ].map((pred) => (
                            <button
                                key={pred.id}
                                onClick={() => setPredictor(pred.id as 'boltz' | 'rf3' | 'both')}
                                className={`p-3 rounded-lg border text-left transition-all ${predictor === pred.id
                                    ? pred.color === 'blue' ? 'bg-blue-600/20 border-blue-500 text-blue-300'
                                        : pred.color === 'green' ? 'bg-green-600/20 border-green-500 text-green-300'
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

                {/* MSA Quality Options (Advanced) */}
                {((showBoltzParams && boltzUseMsa) || (showRf3Params && rf3UseMsa)) && (
                    <div className="border border-[var(--border-primary)] rounded-lg overflow-hidden">
                        <button
                            onClick={() => setShowMsaOptions(!showMsaOptions)}
                            className="w-full flex items-center justify-between p-3 bg-[var(--bg-tertiary)] hover:bg-[var(--bg-secondary)] transition-colors"
                        >
                            <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-[var(--text-primary)]">MSA Quality Options</span>
                                <span className="text-xs text-[var(--text-muted)]">(Advanced)</span>
                            </div>
                            <span className="text-[var(--text-secondary)] text-sm">{showMsaOptions ? '▼' : '▶'}</span>
                        </button>
                        {showMsaOptions && (
                            <div className="p-4 space-y-4 bg-[var(--bg-secondary)]">
                                {/* MSA Quality Preset - Primary Setting */}
                                <div>
                                    <label className="text-sm font-medium text-[var(--text-primary)] block mb-2">MSA Quality Preset</label>
                                    <div className="grid grid-cols-3 gap-2">
                                        <button
                                            type="button"
                                            onClick={() => setMsaPreset('maximum')}
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
                                            onClick={() => setMsaPreset('balanced')}
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
                                            onClick={() => setMsaPreset('fast')}
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
                                            <option value="1">1 (Very relaxed)</option>
                                            <option value="0.1">0.1</option>
                                            <option value="0.01">0.01</option>
                                            <option value="0.001">0.001 (Default)</option>
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
                                {/* Force Refresh Toggle */}
                                <label className="flex items-center gap-3 p-3 bg-[var(--error)]/10 border border-[var(--error)]/30 rounded-lg cursor-pointer hover:bg-[var(--error)]/20 transition-colors">
                                    <input
                                        type="checkbox"
                                        checked={msaForceRefresh}
                                        onChange={(e) => setMsaForceRefresh(e.target.checked)}
                                        className="w-4 h-4 rounded bg-[var(--bg-primary)] border-[var(--error)] text-[var(--error)] focus:ring-[var(--error)]"
                                    />
                                    <div>
                                        <span className="text-[var(--error)] font-medium">Regenerate MSA (Purge Cache)</span>
                                        <p className="text-xs text-[var(--error)]/70">Force fresh MSA search, ignoring cached results for this sequence</p>
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
