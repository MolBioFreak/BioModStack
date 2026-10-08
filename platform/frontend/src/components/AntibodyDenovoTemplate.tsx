import React, { useState, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { submitJob, uploadFile } from '../lib/api';
import { useNavigate } from 'react-router-dom';
import { parsePDBFile, type Chain } from '../utils/pdbUtils';
import { EpitopeSelector } from './EpitopeSelector';

interface AntibodyDenovoTemplateProps {
    onBack: () => void;
}

export const AntibodyDenovoTemplate: React.FC<AntibodyDenovoTemplateProps> = ({ onBack }) => {
    const [jobName, setJobName] = useState('antibody_design');
    const [targetPdb, setTargetPdb] = useState<File | null>(null);
    const [numDesigns, setNumDesigns] = useState(10);
    const [seqDesigner, setSeqDesigner] = useState<'fampnn' | 'antifold' | 'proteinmpnn'>('fampnn');
    const [useAntiberty, setUseAntiberty] = useState(true);
    const [useThermoMPNN, setUseThermoMPNN] = useState(true);
    const [explorationMode, setExplorationMode] = useState(true); // Parallel GPU distribution

    // Framework selection - preset or custom
    const [frameworkType, setFrameworkType] = useState<'standard-fv' | 'nanobody' | 'custom'>('standard-fv');
    const [customFrameworkFile, setCustomFrameworkFile] = useState<File | null>(null);
    const [customFrameworkPath, setCustomFrameworkPath] = useState<string | null>(null);

    const [isUploading, setIsUploading] = useState(false);
    const [uploadedPath, setUploadedPath] = useState<string | null>(null);

    // PDB parsing state
    const [parsedChains, setParsedChains] = useState<Chain[]>([]);
    const [selectedChain, setSelectedChain] = useState<string | null>(null);
    const [selectedResidues, setSelectedResidues] = useState<Set<string>>(new Set());
    const [isParsing, setIsParsing] = useState(false);

    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const submitMutation = useMutation({
        mutationFn: async (data: any) => submitJob(data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate('/');
        }
    });

    // Auto-parse PDB when file is selected
    useEffect(() => {
        if (targetPdb) {
            setIsParsing(true);
            parsePDBFile(targetPdb)
                .then(result => {
                    setParsedChains(result.chains);
                    // Auto-select first chain with most residues
                    if (result.chains.length > 0) {
                        const longestChain = result.chains.reduce((a, b) =>
                            a.length > b.length ? a : b
                        );
                        setSelectedChain(longestChain.id);
                    }
                    // Clear previous selection
                    setSelectedResidues(new Set());
                    setUploadedPath(null);
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
        if (!targetPdb) {
            alert('Please upload a target PDB file');
            return;
        }
        if (selectedResidues.size === 0) {
            alert('Please select at least one epitope residue');
            return;
        }

        try {
            // Step 1: Upload PDB file if not already uploaded
            let pdbPath = uploadedPath;
            if (!pdbPath) {
                pdbPath = await handleFileUpload(targetPdb);
            }

            // Format selected residues for backend
            const epitopeString = Array.from(selectedResidues).sort().join(',');

            // Determine pipeline steps
            const pipelineSteps = ['rfantibody', seqDesigner];
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
                model_id: 'antibody_denovo',
                mode: 'antibody_denovo_pipeline', // Matches main.nf logic
                params: {
                    target_pdb: pdbPath,
                    pdb_source: 'upload',
                    epitope_residues: epitopeString,
                    antigen_chains: selectedChain || undefined, // Send selected chain
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
                    exploration_mode: explorationMode, // Parallel vs serial GPU processing
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
                        };

                        const steps: Array<{ name: string; colorKey: string }> = [
                            { name: 'RFantibody', colorKey: 'emerald' },
                            { name: seqDesigner.toUpperCase(), colorKey: 'blue' },
                            { name: 'Boltz2', colorKey: 'purple' },
                        ];
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
                {/* Job Name */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Job Name</label>
                    <input
                        type="text"
                        value={jobName}
                        onChange={(e) => setJobName(e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                        placeholder="antibody_design"
                    />
                </div>

                {/* Target PDB Upload */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Target Antigen PDB</label>
                    <div className="flex items-center gap-4">
                        <input
                            type="file"
                            accept=".pdb,.cif"
                            onChange={(e) => {
                                const file = e.target.files?.[0] || null;
                                setTargetPdb(file);
                            }}
                            className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none file:mr-4 file:py-1 file:px-4 file:rounded-lg file:border-0 file:bg-blue-600 file:text-white file:cursor-pointer"
                        />
                        {targetPdb && (
                            <span className="text-sm text-emerald-400">
                                {isParsing ? '⏳ Parsing...' : uploadedPath ? '✓ Uploaded' : '📎'} {targetPdb.name}
                            </span>
                        )}
                    </div>
                    <p className="mt-1 text-xs text-slate-500">Upload the antigen structure you want to design antibodies against</p>
                </div>

                {/* Framework Selection */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Antibody Framework</label>
                    <div className="grid grid-cols-3 gap-3 mb-3">
                        <button
                            onClick={() => setFrameworkType('standard-fv')}
                            className={`p-3 rounded-lg border transition-all ${frameworkType === 'standard-fv'
                                ? 'bg-blue-600/20 border-blue-500 text-blue-400'
                                : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
                                }`}
                        >
                            <div className="text-sm font-medium">Standard Fv</div>
                            <div className="text-xs opacity-75">hu-4D5-8 (Herceptin)</div>
                        </button>
                        <button
                            onClick={() => setFrameworkType('nanobody')}
                            className={`p-3 rounded-lg border transition-all ${frameworkType === 'nanobody'
                                ? 'bg-purple-600/20 border-purple-500 text-purple-400'
                                : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
                                }`}
                        >
                            <div className="text-sm font-medium">Nanobody</div>
                            <div className="text-xs opacity-75">VHH single-domain</div>
                        </button>
                        <button
                            onClick={() => setFrameworkType('custom')}
                            className={`p-3 rounded-lg border transition-all ${frameworkType === 'custom'
                                ? 'bg-amber-600/20 border-amber-500 text-amber-400'
                                : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-600'
                                }`}
                        >
                            <div className="text-sm font-medium">Custom</div>
                            <div className="text-xs opacity-75">Upload HLT format</div>
                        </button>
                    </div>

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

                {/* Interactive Epitope Selector */}
                {parsedChains.length > 0 && (
                    <div>
                        <label className="block text-sm font-medium text-slate-400 mb-2">
                            Epitope Selection
                            <span className="ml-2 text-xs text-slate-500 font-normal">
                                (Click residues to select epitope hotspots)
                            </span>
                        </label>
                        <EpitopeSelector
                            chains={parsedChains}
                            selectedResidues={selectedResidues}
                            onSelectionChange={setSelectedResidues}
                            activeChain={selectedChain || undefined}
                        />
                    </div>
                )}

                {/* Fallback text input if no PDB */}
                {parsedChains.length === 0 && targetPdb && !isParsing && (
                    <div className="p-4 bg-amber-500/10 border border-amber-500/30 rounded-lg text-amber-400 text-sm">
                        ⚠️ Could not parse PDB file. Please ensure it's a valid PDB format.
                    </div>
                )}

                {/* Number of Designs */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Number of Designs</label>
                    <input
                        type="number"
                        value={numDesigns}
                        onChange={(e) => setNumDesigns(parseInt(e.target.value) || 10)}
                        min={1}
                        max={100}
                        className="w-32 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                    />
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

                {/* Validation Options */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">Validation Steps</label>
                    <div className="flex gap-4">
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={useAntiberty}
                                onChange={(e) => setUseAntiberty(e.target.checked)}
                                className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-blue-600 focus:ring-blue-500"
                            />
                            <span className="text-sm text-slate-300">AntiBERTy (Immunogenicity)</span>
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={useThermoMPNN}
                                onChange={(e) => setUseThermoMPNN(e.target.checked)}
                                className="w-4 h-4 rounded border-slate-600 bg-slate-800 text-blue-600 focus:ring-blue-500"
                            />
                            <span className="text-sm text-slate-300">ThermoMPNN (Stability)</span>
                        </label>
                    </div>
                </div>

                {/* GPU Processing Mode */}
                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-2">GPU Processing Mode</label>
                    <div className="flex gap-3">
                        <button
                            onClick={() => setExplorationMode(true)}
                            className={`px-4 py-2 rounded-lg font-medium transition-all flex items-center gap-2 ${explorationMode
                                ? 'bg-emerald-600 text-white'
                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                }`}
                        >
                            <span className="text-lg">🚀</span>
                            Exploration
                        </button>
                        <button
                            onClick={() => setExplorationMode(false)}
                            className={`px-4 py-2 rounded-lg font-medium transition-all flex items-center gap-2 ${!explorationMode
                                ? 'bg-purple-600 text-white'
                                : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                }`}
                        >
                            <span className="text-lg">🔬</span>
                            Refinement
                        </button>
                    </div>
                    <p className="text-xs text-slate-500 mt-2">
                        {explorationMode
                            ? "Parallel: Jobs queue through scheduler for multi-GPU distribution"
                            : "Serial: Run each validation one-by-one on assigned GPU"}
                    </p>
                </div>
            </div>

            {/* Submit Button */}
            <div className="mt-8 flex justify-end">
                <button
                    onClick={handleSubmit}
                    disabled={submitMutation.isPending || isUploading || !targetPdb || selectedResidues.size === 0}
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
                    ) : (
                        <>
                            🧬 Generate Antibodies ({selectedResidues.size} hotspots)
                        </>
                    )}
                </button>
            </div>
        </div>
    );
};

export default AntibodyDenovoTemplate;
