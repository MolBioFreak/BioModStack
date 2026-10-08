import { useState, useMemo } from 'react';
import { SequenceManagerModal } from './SequenceManagerModal';
import { parseRegions, generateLibrary, normalizeAminoAcids, formatMutationLabel } from '../utils/mutationUtils';
import type { VariantSequence, SubstitutionStrategy, Mutation } from '../utils/mutationUtils';
import { InteractiveSequence } from './InteractiveSequence';
import { LigandSelector, type LigandEntry } from './LigandSelector';
import { TargetAntigenSelector } from './TargetAntigenSelector';
import { parsePDBFile, type Chain } from '../utils/pdbUtils';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { PhysicsRefinementPanel, type PhysicsRefinementSettings, DEFAULT_SETTINGS as PHYSICS_DEFAULTS } from './PhysicsRefinementPanel';

interface MutagenesisTemplateProps {
    onBack: () => void;
    onSubmit: (jobName: string, variants: VariantSequence[], predictorConfig: any) => void;
}

export function MutagenesisTemplate({ onBack, onSubmit }: MutagenesisTemplateProps) {
    // Top-level state
    const [jobNamePrefix, setJobNamePrefix] = useState('mutagenesis_lib');
    const [baseSequence, setBaseSequence] = useState('');
    const [mode, setMode] = useState<'library' | 'manual' | 'affinityMaturation'>('library');

    // Affinity Maturation State
    const [frustrampnnResults, setFrustrampnnResults] = useState<Array<{
        position: number;
        aa: string;
        frustration: number;
        suggestedAAs: string[];
        selected: boolean;
    }>>([]);
    const [frustrampnnLoading, setFrustrampnnLoading] = useState(false);
    const [maturationAllowedAAs, setMaturationAllowedAAs] = useState('');
    const [maturationGenMode, setMaturationGenMode] = useState<'singles' | 'combos' | 'sample'>('singles');
    const [maturationSampleN, setMaturationSampleN] = useState(20);
    const [ppiflowRotamer, setPpiflowRotamer] = useState(false);
    const [ppiflowFlow, setPpiflowFlow] = useState(false);
    const [ppiflowFinalBoltz, setPpiflowFinalBoltz] = useState(false);

    // Library Generator State
    const [regionInput, setRegionInput] = useState('');
    const [strategy, setStrategy] = useState<SubstitutionStrategy>('random');
    const [numVariants, setNumVariants] = useState(20);
    const [mutationsPerVariant, setMutationsPerVariant] = useState<[number, number]>([1, 2]); // Min, Max
    const [mutationCountMode, setMutationCountMode] = useState<'range' | 'exact' | 'set'>('range');
    const [mutationCountExact, setMutationCountExact] = useState(1);
    const [mutationCountSetInput, setMutationCountSetInput] = useState('1,2,3');
    const [excludedPositions, setExcludedPositions] = useState<Set<number>>(new Set());
    const [excludeResiduesInput, setExcludeResiduesInput] = useState('');
    const [allowedAAsInput, setAllowedAAsInput] = useState('');
    const [blockedAAsInput, setBlockedAAsInput] = useState('');
    const [allowInsertions, setAllowInsertions] = useState(false);
    const [allowDeletions, setAllowDeletions] = useState(false);
    const [indelSizes, setIndelSizes] = useState<number[]>([1]);
    const [indelProbability, setIndelProbability] = useState(0);

    // Sequence Manager State
    const [showSequenceManager, setShowSequenceManager] = useState(false);
    const [sequenceToSave, setSequenceToSave] = useState<{ sequence: string; name: string } | null>(null);

    // Manual Editor State
    const [manualMutations, setManualMutations] = useState<Mutation[]>([]);

    // Preview
    const regions = useMemo(() => parseRegions(regionInput), [regionInput]);
    const excludedPositionsList = useMemo(() => {
        return Array.from(excludedPositions)
            .filter(pos => pos > 0 && pos <= baseSequence.length)
            .sort((a, b) => a - b);
    }, [excludedPositions, baseSequence.length]);
    const [generatedVariants, setGeneratedVariants] = useState<VariantSequence[]>([]);

    // Predictor Config
    const [predictor, setPredictor] = useState<'boltz' | 'rf3' | 'both'>('boltz');
    const [predictorParams, setPredictorParams] = useState({
        recycling_steps: 3,
        diffusion_samples: 1,
        sampling_steps: 50,
        num_parallel_jobs: 1,
        use_msa: true,
        use_potentials: false,
        step_scale: 1.638
    });
    const [runFrustrampnnPost, setRunFrustrampnnPost] = useState(false);

    // Complex Mode: Ligands & Ions
    const [ligands, setLigands] = useState<LigandEntry[]>([]);

    // Physics refinement (OpenMM) - for ΔΔG validation
    const [physicsSettings, setPhysicsSettings] = useState<PhysicsRefinementSettings>(PHYSICS_DEFAULTS);

    // PDB Import State
    const [showPdbImport, setShowPdbImport] = useState(false);
    const [pdbImportTab, setPdbImportTab] = useState<'upload' | 'runs' | 'presets' | 'rcsb'>('upload');
    const [parsedChains, setParsedChains] = useState<Chain[]>([]);
    const [pdbBlobUrl, setPdbBlobUrl] = useState<string | null>(null);
    const [show3DViewer, setShow3DViewer] = useState(false);
    const [selectedChainId, setSelectedChainId] = useState<string | null>(null);

    // Convert mutation positions to Set for 3D viewer highlighting
    const mutationPositionsSet = useMemo(() => {
        const positions = new Set<string>();
        const chainId = selectedChainId || 'A';
        // For library mode, use regions
        if (mode === 'library' && regions.length > 0) {
            for (const region of regions) {
                for (let i = region.start; i <= region.end; i++) {
                    positions.add(`${chainId}${i}`);
                }
            }
        }
        // For manual mode, use mutation positions
        if (mode === 'manual') {
            for (const mut of manualMutations) {
                positions.add(`${chainId}${mut.position}`);
            }
        }
        return positions;
    }, [mode, regions, manualMutations, selectedChainId]);

    // Handlers
    const toggleExcludedPosition = (pos: number) => {
        setExcludedPositions(prev => {
            const next = new Set(prev);
            if (next.has(pos)) {
                next.delete(pos);
            } else {
                next.add(pos);
            }
            return next;
        });
    };

    const handleGeneratePreview = () => {
        if (!baseSequence) return;

        if (mode === 'library') {
            const allowedAAs = normalizeAminoAcids(allowedAAsInput);
            const blockedAAs = normalizeAminoAcids(blockedAAsInput);
            const excludeResidues = normalizeAminoAcids(excludeResiduesInput);
            const mutationCountSet = mutationCountSetInput
                .split(',')
                .map(v => parseInt(v.trim()))
                .filter(v => Number.isFinite(v) && v > 0);
            const variants = generateLibrary(
                baseSequence,
                regions,
                strategy,
                numVariants,
                mutationsPerVariant,
                {
                    customAA: allowedAAs,
                    allowedAAs,
                    blockedAAs,
                    excludeFromResidues: excludeResidues,
                    excludedPositions: excludedPositionsList,
                    mutationCountMode,
                    mutationCount: mutationCountExact,
                    mutationCountSet,
                    allowInsertions,
                    allowDeletions,
                    indelSizes,
                    indelProbability
                }
            );
            setGeneratedVariants(variants);
        } else {
            // Manual Mode: Generate variants from manual mutations
            // For now, let's treat the set of manual mutations as a single variant
            // TODO: Add combinatorics? E.g. "Select all single mutants"
            if (manualMutations.length > 0) {
                // Construct the mutated sequence
                const seqArray = baseSequence.split('');
                manualMutations.forEach(m => {
                    if (m.position > 0 && m.position <= seqArray.length) {
                        seqArray[m.position - 1] = m.to;
                    }
                });

                setGeneratedVariants([{
                    name: `manual_${manualMutations.map(formatMutationLabel).join('_')}`,
                    sequence: seqArray.join(''),
                    mutations: manualMutations
                }]);
            } else {
                setGeneratedVariants([]);
            }
        }
    };

    // Manual Mutation Handlers
    const handleAddMutation = (pos: number, toAA: string) => {
        const fromAA = baseSequence[pos - 1];
        setManualMutations(prev => {
            // Remove existing mutation at this pos if any
            const filtered = prev.filter(m => m.position !== pos);
            // Add new if different from wild type
            if (fromAA !== toAA) {
                return [...filtered, { position: pos, from: fromAA, to: toAA }].sort((a, b) => a.position - b.position);
            }
            return filtered;
        });
    };

    const handleRemoveMutation = (pos: number) => {
        setManualMutations(prev => prev.filter(m => m.position !== pos));
    };

    // Auto-update preview in manual mode
    useMemo(() => {
        if (mode === 'manual') handleGeneratePreview();
    }, [manualMutations, mode]);

    const handleSubmit = () => {
        if (generatedVariants.length === 0) return;
        onSubmit(jobNamePrefix, generatedVariants, {
            predictor,
            ...predictorParams,
            // Reference sequence for logging (mutants regenerate MSAs)
            msa_reference_sequence: baseSequence,
            // Optional post-run FrustraMPNN annotation
            run_frustrampnn: runFrustrampnnPost,
            // Include ALL fields from ligand entries - sequence is required for DNA/RNA!
            ligands: ligands.map(l => ({
                type: l.type,
                id: l.id,
                ccd: l.ccd,
                smiles: l.smiles,
                sequence: l.sequence  // CRITICAL: This was missing - DNA/RNA entries need their sequence
            })),
            // Physics refinement (OpenMM) for ΔΔG
            openmm_enabled: physicsSettings.enabled,
            openmm_compute_tier: physicsSettings.computeTier,
            openmm_restraint_mode: physicsSettings.restraintMode,
            openmm_mmgbsa_mode: physicsSettings.mmgbsaMode,
            openmm_force_field: physicsSettings.forceField,
            openmm_top_n_percentage: physicsSettings.topNPercentage,
            openmm_max_iterations: physicsSettings.maxIterations,
            openmm_tolerance: physicsSettings.tolerance,
            openmm_restraint_strength: physicsSettings.restraintStrength,
            openmm_implicit_solvent: physicsSettings.implicitSolvent,
            openmm_platform: physicsSettings.platform
        });
    };

    return (
        <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 shadow-xl animate-in fade-in slide-in-from-bottom-4">
            <header className="flex justify-between items-center mb-6 border-b border-slate-800 pb-4">
                <div className="flex items-center gap-6">
                    <div>
                        <h2 className="text-xl font-bold bg-gradient-to-r from-purple-400 to-pink-500 bg-clip-text text-transparent">
                            Mutagenesis Library
                        </h2>
                        <p className="text-slate-400 text-sm">Generate variant libraries for structure prediction</p>
                    </div>
                    {/* Top-level workflow tabs */}
                    <div className="flex bg-slate-800/50 p-1 rounded-lg">
                        <button
                            onClick={() => setMode('library')}
                            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-all ${mode !== 'affinityMaturation' ? 'bg-purple-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'}`}
                        >
                            Standard Workflow
                        </button>
                        <button
                            onClick={() => setMode('affinityMaturation')}
                            className={`px-3 py-1.5 rounded-md text-sm font-medium transition-all ${mode === 'affinityMaturation' ? 'bg-emerald-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'}`}
                        >
                            Affinity Maturation
                        </button>
                    </div>
                </div>
                <button onClick={onBack} className="text-slate-400 hover:text-white px-3 py-1 rounded hover:bg-slate-800 transition-colors">
                    Cancel
                </button>
            </header>

            <div className="space-y-8">
                {/* 1. Base Sequence */}
                <section>
                    <div className="flex justify-between items-center mb-2">
                        <label className="block text-sm font-medium text-slate-300">Base Sequence</label>
                        <div className="flex gap-2">
                            <button
                                onClick={() => setShowSequenceManager(true)}
                                className="px-3 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-300 text-xs rounded-lg transition-colors flex items-center gap-2"
                            >
                                Sequence Library
                            </button>
                            <button
                                onClick={() => {
                                    setPdbImportTab('upload');
                                    setShowPdbImport(true);
                                }}
                                className="px-3 py-1 bg-cyan-600/20 hover:bg-cyan-600/30 border border-cyan-600/30 text-cyan-400 text-xs rounded-lg transition-colors flex items-center gap-2"
                            >
                                Import PDB
                            </button>
                            <button
                                onClick={() => {
                                    setPdbImportTab('runs');
                                    setShowPdbImport(true);
                                }}
                                className="px-3 py-1 bg-indigo-600/20 hover:bg-indigo-600/30 border border-indigo-600/30 text-indigo-300 text-xs rounded-lg transition-colors flex items-center gap-2"
                            >
                                🧪 From Runs
                            </button>
                            {baseSequence && baseSequence.length > 0 && (
                                <button
                                    onClick={() => {
                                        setSequenceToSave({ sequence: baseSequence, name: jobNamePrefix.replace('mutagenesis_lib', '').replace(/^_/, '') || 'MySequence' });
                                        setShowSequenceManager(true);
                                    }}
                                    className="px-3 py-1 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 text-xs rounded-lg transition-colors flex items-center gap-1.5 border border-emerald-600/30"
                                >
                                    💾 Save
                                </button>
                            )}
                        </div>
                    </div>

                    <textarea
                        value={baseSequence}
                        onChange={(e) => setBaseSequence(e.target.value.toUpperCase().replace(/[^A-Z]/g, ''))}
                        placeholder="Select a sequence from library or paste raw amino acids..."
                        rows={4}
                        className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-purple-500 outline-none"
                    />

                    {baseSequence && (
                        <div className="mt-2 text-xs text-slate-500 font-mono bg-slate-950 p-2 rounded border border-slate-800/50 break-all flex justify-between items-center">
                            <span>Length: {baseSequence.length} aa</span>
                            <button onClick={() => setBaseSequence('')} className="text-red-400 hover:text-red-300">Clear</button>
                        </div>
                    )}

                    {/* 3D Structure Preview (if PDB loaded) */}
                    {pdbBlobUrl && (
                        <div className="mt-4">
                            <div className="flex items-center justify-between mb-2">
                                <span className="text-xs text-slate-400">3D Structure Preview</span>
                                <button
                                    onClick={() => setShow3DViewer(!show3DViewer)}
                                    className={`px-3 py-1 text-xs rounded-lg transition-all ${show3DViewer
                                        ? 'bg-purple-600/20 text-purple-400 border border-purple-500/50'
                                        : 'bg-slate-700 text-slate-400 hover:bg-slate-600'
                                        }`}
                                >
                                    {show3DViewer ? 'Hide 3D' : 'Show 3D'}
                                </button>
                            </div>
                            {show3DViewer && (
                                <div className="animate-in fade-in slide-in-from-top-2 duration-300">
                                    <div className="text-xs text-slate-500 mb-2">
                                        {mutationPositionsSet.size > 0
                                            ? `${mutationPositionsSet.size} mutation positions highlighted`
                                            : 'Define regions or mutations to highlight positions'}
                                    </div>
                                    <EpitopeMolstarViewer
                                        structureUrl={pdbBlobUrl}
                                        height={350}
                                        selectedResidues={mutationPositionsSet}
                                    />
                                </div>
                            )}
                        </div>
                    )}
                </section>

                {/* 2. Mode Toggle - Only show for Standard Workflow */}
                {mode !== 'affinityMaturation' && (
                    <div className="flex bg-slate-800/50 p-1 rounded-lg w-fit">
                        <button
                            onClick={() => setMode('library')}
                            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${mode === 'library' ? 'bg-purple-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'}`}
                        >
                            Library Generator
                        </button>
                        <button
                            onClick={() => setMode('manual')}
                            className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${mode === 'manual' ? 'bg-purple-600 text-white shadow' : 'text-slate-400 hover:text-slate-200'}`}
                        >
                            Manual Editor
                        </button>
                    </div>
                )}

                {/* 3. Library Generator UI */}
                {mode === 'library' && (
                    <div className="space-y-6 border-l-2 border-slate-800 pl-4">
                        {/* Region Input */}
                        <div>
                            <label className="block text-sm font-medium text-slate-300 mb-1">Mutation Regions</label>
                            <p className="text-xs text-slate-500 mb-2">Specify ranges to mutate (1-based indices), comma-separated. E.g., "23-42, 67-72"</p>
                            <input
                                type="text"
                                value={regionInput}
                                onChange={(e) => setRegionInput(e.target.value)}
                                placeholder="e.g., 10-20, 45-50"
                                className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-sm focus:ring-2 focus:ring-purple-500 outline-none"
                            />
                            {regions.length > 0 && (
                                <div className="flex gap-2 mt-2 flex-wrap">
                                    {regions.map(r => (
                                        <span key={r.id} className="text-xs bg-purple-500/20 text-purple-300 px-2 py-1 rounded border border-purple-500/30">
                                            Pos {r.start}-{r.end} ({r.end - r.start + 1} aa)
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Mutation Rules */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <label className="block text-sm font-medium text-slate-300 mb-1">Exclude Positions</label>
                                <p className="text-xs text-slate-500 mb-2">Click residues to blacklist within your mutation region.</p>
                                {baseSequence ? (
                                    <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-3">
                                        <div className="flex items-center justify-between mb-2">
                                            <span className="text-[11px] text-slate-500">
                                                Excluded: {excludedPositionsList.length}
                                                {excludedPositionsList.length > 0 ? ` (${excludedPositionsList.join(', ')})` : ''}
                                            </span>
                                            <button
                                                onClick={() => setExcludedPositions(new Set())}
                                                className="text-[11px] text-slate-400 hover:text-white"
                                                disabled={excludedPositionsList.length === 0}
                                            >
                                                Clear
                                            </button>
                                        </div>
                                        <div className="flex flex-wrap gap-1 font-mono text-xs leading-none max-h-[160px] overflow-y-auto">
                                            {baseSequence.split('').map((aa, idx) => {
                                                const pos = idx + 1;
                                                const isExcluded = excludedPositions.has(pos);
                                                return (
                                                    <button
                                                        key={pos}
                                                        onClick={() => toggleExcludedPosition(pos)}
                                                        className={`w-7 h-7 rounded border transition-colors ${isExcluded
                                                            ? 'bg-red-600/30 border-red-400 text-red-200 line-through'
                                                            : 'bg-slate-900 border-slate-700 text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                                                            }`}
                                                        title={`Pos ${pos}: ${aa}${isExcluded ? ' (excluded)' : ''}`}
                                                    >
                                                        {aa}
                                                    </button>
                                                );
                                            })}
                                        </div>
                                    </div>
                                ) : (
                                    <div className="text-xs text-slate-600 bg-slate-950/60 border border-slate-800 rounded-lg p-3">
                                        Load a sequence to enable position exclusion.
                                    </div>
                                )}
                                <label className="block text-sm font-medium text-slate-300 mb-1 mt-4">Exclude Residues</label>
                                <p className="text-xs text-slate-500 mb-2">Do not mutate positions with these WT residues (e.g., "CP")</p>
                                <input
                                    type="text"
                                    value={excludeResiduesInput}
                                    onChange={(e) => setExcludeResiduesInput(e.target.value.toUpperCase())}
                                    placeholder="e.g., C, P"
                                    className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-sm"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-slate-300 mb-1">Allowed AAs (Whitelist)</label>
                                <p className="text-xs text-slate-500 mb-2">Restrict substitutions/insertions to these AAs</p>
                                <input
                                    type="text"
                                    value={allowedAAsInput}
                                    onChange={(e) => setAllowedAAsInput(e.target.value.toUpperCase())}
                                    placeholder="e.g., AST"
                                    className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-sm"
                                />
                                <label className="block text-sm font-medium text-slate-300 mb-1 mt-4">Blocked AAs (Blacklist)</label>
                                <p className="text-xs text-slate-500 mb-2">Exclude these AAs from substitutions/insertions</p>
                                <input
                                    type="text"
                                    value={blockedAAsInput}
                                    onChange={(e) => setBlockedAAsInput(e.target.value.toUpperCase())}
                                    placeholder="e.g., C, P"
                                    className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-sm"
                                />
                            </div>
                        </div>

                        {/* Indel Rules */}
                        <div className="bg-slate-950/50 rounded-lg p-4 border border-slate-800">
                            <div className="flex items-center justify-between mb-3">
                                <h4 className="text-sm font-semibold text-slate-300">Loop Resize (Indels)</h4>
                                <span className="text-xs text-slate-500">CDR-only recommended</span>
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <label className="flex items-center gap-2 text-sm text-slate-300">
                                        <input
                                            type="checkbox"
                                            checked={allowInsertions}
                                            onChange={(e) => setAllowInsertions(e.target.checked)}
                                            className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-purple-600"
                                        />
                                        Allow insertions
                                    </label>
                                    <label className="flex items-center gap-2 text-sm text-slate-300">
                                        <input
                                            type="checkbox"
                                            checked={allowDeletions}
                                            onChange={(e) => setAllowDeletions(e.target.checked)}
                                            className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-purple-600"
                                        />
                                        Allow deletions
                                    </label>
                                    <div className="text-xs text-slate-500">Indel sizes</div>
                                    <div className="flex gap-2">
                                        {[1, 2, 3].map(size => (
                                            <label key={size} className="flex items-center gap-1 text-xs text-slate-400">
                                                <input
                                                    type="checkbox"
                                                    checked={indelSizes.includes(size)}
                                                    onChange={(e) => {
                                                        setIndelSizes(prev => {
                                                            const next = new Set(prev);
                                                            if (e.target.checked) {
                                                                next.add(size);
                                                            } else {
                                                                next.delete(size);
                                                            }
                                                            return Array.from(next).sort();
                                                        });
                                                    }}
                                                    className="w-3 h-3 rounded bg-slate-900 border-slate-700 text-purple-600"
                                                />
                                                {size}
                                            </label>
                                        ))}
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-xs text-slate-500 mb-2">Indel Probability</label>
                                    <input
                                        type="range"
                                        min={0}
                                        max={100}
                                        step={5}
                                        value={Math.round(indelProbability * 100)}
                                        onChange={(e) => setIndelProbability(parseInt(e.target.value) / 100)}
                                        className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer"
                                    />
                                    <div className="flex justify-between text-xs text-slate-500 mt-1">
                                        <span>0%</span>
                                        <span className="text-slate-300 font-medium">{Math.round(indelProbability * 100)}%</span>
                                        <span>100%</span>
                                    </div>
                                    <p className="text-[11px] text-slate-500 mt-2">Applied per variant; substitutions still follow mutation count settings.</p>
                                </div>
                            </div>
                        </div>

                        {/* Strategy Options */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <label className="block text-sm font-medium text-slate-300 mb-2">Strategy</label>
                                <select
                                    value={strategy}
                                    onChange={(e) => setStrategy(e.target.value as any)}
                                    className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                                >
                                    <option value="random">Random (Any AA)</option>
                                    <option value="conservative">Conservative (Similar properties)</option>
                                    <option value="nonconservative">Non-Conservative (Different properties)</option>
                                    <option value="custom">Custom Set (Use "Allowed AAs")</option>
                                </select>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-slate-300 mb-2">Library Size</label>
                                <div className="flex gap-4">
                                    <div className="flex-1">
                                        <label className="text-xs text-slate-500 block mb-1">Generate N Variants</label>
                                        <input
                                            type="number"
                                            value={numVariants}
                                            onChange={(e) => setNumVariants(Math.min(100, Math.max(1, parseInt(e.target.value) || 1)))}
                                            className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                                        />
                                    </div>
                                    <div className="flex-1">
                                        <label className="text-xs text-slate-500 block mb-1">Mutations / Variant</label>
                                        <div className="space-y-2">
                                            <select
                                                value={mutationCountMode}
                                                onChange={(e) => setMutationCountMode(e.target.value as any)}
                                                className="w-full bg-slate-950 border border-slate-700 rounded-lg px-2 py-2 text-white text-sm"
                                            >
                                                <option value="range">Range (min-max)</option>
                                                <option value="exact">Exact N</option>
                                                <option value="set">Random from set</option>
                                            </select>
                                            {mutationCountMode === 'range' && (
                                                <div className="flex gap-2 items-center">
                                                    <input
                                                        type="number"
                                                        value={mutationsPerVariant[0]}
                                                        onChange={(e) => setMutationsPerVariant([parseInt(e.target.value) || 1, mutationsPerVariant[1]])}
                                                        className="w-full bg-slate-950 border border-slate-700 rounded-lg px-2 py-2 text-white text-sm text-center"
                                                        min={1}
                                                    />
                                                    <span className="text-slate-500">-</span>
                                                    <input
                                                        type="number"
                                                        value={mutationsPerVariant[1]}
                                                        onChange={(e) => setMutationsPerVariant([mutationsPerVariant[0], parseInt(e.target.value) || 1])}
                                                        className="w-full bg-slate-950 border border-slate-700 rounded-lg px-2 py-2 text-white text-sm text-center"
                                                        min={1}
                                                    />
                                                </div>
                                            )}
                                            {mutationCountMode === 'exact' && (
                                                <input
                                                    type="number"
                                                    value={mutationCountExact}
                                                    onChange={(e) => setMutationCountExact(parseInt(e.target.value) || 1)}
                                                    className="w-full bg-slate-950 border border-slate-700 rounded-lg px-2 py-2 text-white text-sm text-center"
                                                    min={1}
                                                />
                                            )}
                                            {mutationCountMode === 'set' && (
                                                <input
                                                    type="text"
                                                    value={mutationCountSetInput}
                                                    onChange={(e) => setMutationCountSetInput(e.target.value)}
                                                    placeholder="e.g., 1,2,3"
                                                    className="w-full bg-slate-950 border border-slate-700 rounded-lg px-2 py-2 text-white text-sm"
                                                />
                                            )}
                                            <p className="text-[11px] text-slate-500">Counts apply to substitutions; indels are additional.</p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>


                        {/* Preview Action */}
                        <div className="bg-slate-950/50 rounded-lg p-4 border border-slate-800">
                            <div className="flex justify-between items-center mb-4">
                                <h3 className="text-sm font-semibold text-slate-300">Preview</h3>
                                <button
                                    onClick={handleGeneratePreview}
                                    disabled={!baseSequence || regions.length === 0}
                                    className="px-3 py-1.5 bg-purple-600 hover:bg-purple-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm rounded transition-colors"
                                >
                                    Generate Preview
                                </button>
                            </div>

                            {generatedVariants.length > 0 ? (
                                <div className="max-h-60 overflow-y-auto space-y-2 pr-2 custom-scrollbar">
                                    {generatedVariants.map((v, i) => (
                                        <div key={i} className="text-xs bg-slate-900 p-2 rounded flex justify-between items-center border border-slate-800">
                                            <span className="text-purple-400 font-mono font-medium">{v.mutations.map(formatMutationLabel).join(', ')}</span>
                                            <span className="text-slate-500 font-mono text-[10px] truncate max-w-[200px]" title={v.sequence}>{v.sequence}</span>
                                        </div>
                                    ))}
                                </div>
                            ) : (
                                <div className="text-center py-6 text-slate-600 text-sm italic">
                                    Enter sequence and regions, then click Generate to preview variants.
                                </div>
                            )}
                        </div>
                    </div>
                )}

                {/* 4. Manual Editor */}
                {mode === 'manual' && (
                    <div className="space-y-6 border-l-2 border-slate-800 pl-4">
                        <div className="flex justify-between items-start">
                            <div>
                                <h3 className="text-sm font-semibold text-slate-200 mb-1">Interactive Editor</h3>
                                <p className="text-xs text-slate-500">Click residues to mutate. Mutations are applied cumulatively to a single variant.</p>
                            </div>
                            {manualMutations.length > 0 && (
                                <button
                                    onClick={() => setManualMutations([])}
                                    className="text-xs text-red-400 hover:text-red-300 px-2 py-1 rounded hover:bg-red-500/10 transition-colors"
                                >
                                    Clear All ({manualMutations.length})
                                </button>
                            )}
                        </div>

                        {baseSequence ? (
                            <InteractiveSequence
                                sequence={baseSequence}
                                mutations={manualMutations}
                                onMutationAdd={handleAddMutation}
                                onMutationRemove={handleRemoveMutation}
                            />
                        ) : (
                            <div className="p-8 border border-dashed border-slate-700 rounded-lg text-center text-slate-500 text-sm">
                                Please select a base sequence above to start editing.
                            </div>
                        )}

                        {/* Preview (Manual) */}
                        {manualMutations.length > 0 && (
                            <div className="bg-slate-950/50 rounded-lg p-4 border border-slate-800">
                                <h3 className="text-sm font-semibold text-slate-300 mb-2">Variant Preview</h3>
                                <div className="text-xs bg-slate-900 p-2 rounded flex justify-between items-center border border-slate-800">
                                    <span className="text-purple-400 font-mono font-medium">
                                        {manualMutations.map(formatMutationLabel).join(', ')}
                                    </span>
                                    <span className="text-slate-500 font-mono text-[10px] truncate max-w-[200px]">
                                        {generatedVariants[0]?.sequence}
                                    </span>
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* 5. Affinity Maturation Mode */}
                {mode === 'affinityMaturation' && (
                    <div className="space-y-6 border-l-2 border-emerald-800 pl-4">
                        <div className="flex justify-between items-start">
                            <div>
                                <h3 className="text-sm font-semibold text-slate-200 mb-1">FrustraMPNN-Guided Maturation</h3>
                                <p className="text-xs text-slate-500">Identify frustrated CDR positions and generate optimized variants</p>
                            </div>
                        </div>

                        {/* FrustraMPNN Analysis Section */}
                        <div className="bg-slate-950/50 rounded-lg p-4 border border-slate-800">
                            <div className="flex justify-between items-center mb-4">
                                <h4 className="text-sm font-semibold text-slate-300">FrustraMPNN Analysis</h4>
                                <button
                                    onClick={() => {
                                        setFrustrampnnLoading(true);
                                        // Placeholder: In production, call API
                                        setTimeout(() => {
                                            setFrustrampnnResults([
                                                { position: 103, aa: 'S', frustration: 0.82, suggestedAAs: ['A', 'T', 'N'], selected: true },
                                                { position: 105, aa: 'Y', frustration: 0.71, suggestedAAs: ['F', 'W'], selected: true },
                                                { position: 108, aa: 'G', frustration: 0.45, suggestedAAs: [], selected: false },
                                            ]);
                                            setFrustrampnnLoading(false);
                                        }, 1000);
                                    }}
                                    disabled={!baseSequence || frustrampnnLoading}
                                    className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm rounded transition-colors flex items-center gap-2"
                                >
                                    {frustrampnnLoading ? (
                                        <><span className="animate-spin">...</span> Analyzing...</>
                                    ) : (
                                        <>Run Analysis</>
                                    )}
                                </button>
                            </div>

                            {frustrampnnResults.length > 0 ? (
                                <div className="overflow-x-auto">
                                    <table className="w-full text-sm">
                                        <thead>
                                            <tr className="text-left text-slate-400 border-b border-slate-700">
                                                <th className="py-2 px-2">Select</th>
                                                <th className="py-2 px-2">Position</th>
                                                <th className="py-2 px-2">WT</th>
                                                <th className="py-2 px-2">Frustration</th>
                                                <th className="py-2 px-2">Suggested AAs</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {frustrampnnResults.map((row, idx) => (
                                                <tr key={row.position} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                                                    <td className="py-2 px-2">
                                                        <input
                                                            type="checkbox"
                                                            checked={row.selected}
                                                            onChange={() => {
                                                                setFrustrampnnResults(prev => {
                                                                    const next = [...prev];
                                                                    next[idx] = { ...next[idx], selected: !next[idx].selected };
                                                                    return next;
                                                                });
                                                            }}
                                                            className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-emerald-600"
                                                        />
                                                    </td>
                                                    <td className="py-2 px-2 font-mono text-slate-300">{row.position}</td>
                                                    <td className="py-2 px-2 font-mono font-bold text-purple-400">{row.aa}</td>
                                                    <td className="py-2 px-2">
                                                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${row.frustration > 0.7 ? 'bg-red-500/20 text-red-400' :
                                                            row.frustration > 0.5 ? 'bg-amber-500/20 text-amber-400' :
                                                                'bg-green-500/20 text-green-400'
                                                            }`}>
                                                            {row.frustration.toFixed(2)}
                                                        </span>
                                                    </td>
                                                    <td className="py-2 px-2 font-mono text-slate-400">
                                                        {row.suggestedAAs.length > 0 ? row.suggestedAAs.join(', ') : '-'}
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            ) : (
                                <div className="text-center py-6 text-slate-600 text-sm italic">
                                    {baseSequence ? 'Click "Run Analysis" to identify frustrated positions' : 'Load a sequence first, then run analysis'}
                                </div>
                            )}
                        </div>

                        {/* Mutation Generation Options */}
                        {frustrampnnResults.filter(r => r.selected).length > 0 && (
                            <div className="bg-slate-950/50 rounded-lg p-4 border border-slate-800">
                                <h4 className="text-sm font-semibold text-slate-300 mb-3">Generation Options</h4>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-xs text-slate-400 mb-2">Allowed AAs (override FrustraMPNN)</label>
                                        <input
                                            type="text"
                                            value={maturationAllowedAAs}
                                            onChange={(e) => setMaturationAllowedAAs(e.target.value.toUpperCase())}
                                            placeholder="Leave empty to use FrustraMPNN suggestions"
                                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-sm"
                                        />
                                    </div>
                                    <div>
                                        <label className="block text-xs text-slate-400 mb-2">Generation Mode</label>
                                        <select
                                            value={maturationGenMode}
                                            onChange={(e) => setMaturationGenMode(e.target.value as any)}
                                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                                        >
                                            <option value="singles">Single Mutants Only</option>
                                            <option value="combos">Top Combinations</option>
                                            <option value="sample">Random Sample</option>
                                        </select>
                                    </div>
                                    {maturationGenMode === 'sample' && (
                                        <div>
                                            <label className="block text-xs text-slate-400 mb-2">Sample N Variants</label>
                                            <input
                                                type="number"
                                                value={maturationSampleN}
                                                onChange={(e) => setMaturationSampleN(parseInt(e.target.value) || 20)}
                                                min={1}
                                                max={100}
                                                className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                                            />
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}

                        {/* PPIFlow Refinement Options */}
                        <div className="bg-slate-950/50 rounded-lg p-4 border border-slate-800">
                            <h4 className="text-sm font-semibold text-slate-300 mb-3">Refinement Pipeline</h4>
                            <p className="text-xs text-slate-500 mb-4">Order: Boltz-2 → [PPIFlow Rotamer] → [PPIFlow Flow] → [Final Boltz-2]</p>
                            <div className="space-y-3">
                                <label className="flex items-center gap-3 text-sm text-slate-300 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={ppiflowRotamer}
                                        onChange={(e) => setPpiflowRotamer(e.target.checked)}
                                        className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-cyan-600"
                                    />
                                    <span>PPIFlow Rotamer Enrichment</span>
                                    <span className="text-xs text-slate-500">(sidechain optimization)</span>
                                </label>
                                <label className="flex items-center gap-3 text-sm text-slate-300 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={ppiflowFlow}
                                        onChange={(e) => setPpiflowFlow(e.target.checked)}
                                        className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-cyan-600"
                                    />
                                    <span>PPIFlow Flow Matching</span>
                                    <span className="text-xs text-slate-500">(interface polish)</span>
                                </label>
                                <label className="flex items-center gap-3 text-sm text-slate-300 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={ppiflowFinalBoltz}
                                        onChange={(e) => setPpiflowFinalBoltz(e.target.checked)}
                                        className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-blue-600"
                                    />
                                    <span>Final Boltz-2 Validation</span>
                                    <span className="text-xs text-slate-500">(after PPIFlow)</span>
                                </label>
                            </div>
                        </div>
                    </div>
                )}

                {/* 6. Predictor Settings */}
                <section className="pt-6 border-t border-slate-800">
                    <h3 className="text-sm font-semibold text-slate-200 mb-4">Prediction Settings</h3>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
                        <div className={`cursor-pointer p-3 rounded-lg border text-center transition-all ${predictor === 'boltz' ? 'bg-blue-600/20 border-blue-500 text-blue-300' : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-600'}`}
                            onClick={() => setPredictor('boltz')}
                        >
                            <div className="font-bold mb-1">Boltz-2</div>
                            <div className="text-xs opacity-70">Single Model</div>
                        </div>
                        <div className={`cursor-pointer p-3 rounded-lg border text-center transition-all ${predictor === 'rf3' ? 'bg-green-600/20 border-green-500 text-green-300' : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-600'}`}
                            onClick={() => setPredictor('rf3')}
                        >
                            <div className="font-bold mb-1">RoseTTAFold3</div>
                            <div className="text-xs opacity-70">Single Model</div>
                        </div>
                        <div className={`cursor-pointer p-3 rounded-lg border text-center transition-all ${predictor === 'both' ? 'bg-purple-600/20 border-purple-500 text-purple-300' : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-600'}`}
                            onClick={() => setPredictor('both')}
                        >
                            <div className="font-bold mb-1">Ensemble (Both)</div>
                            <div className="text-xs opacity-70">Run in Parallel</div>
                        </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 text-sm pt-4">
                        <div className="col-span-1 md:col-span-2 lg:col-span-1">
                            <label className="text-slate-400 block mb-1">Job Name Prefix</label>
                            <input
                                type="text"
                                value={jobNamePrefix}
                                onChange={(e) => setJobNamePrefix(e.target.value)}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-white"
                            />
                        </div>
                        <div>
                            <label className="text-slate-400 block mb-1" title="Higher values improve quality but take longer">Recycling Steps</label>
                            <input
                                type="number"
                                value={predictorParams.recycling_steps}
                                onChange={(e) => setPredictorParams(p => ({ ...p, recycling_steps: parseInt(e.target.value) || 3 }))}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-white"
                                min={1} max={10}
                            />
                        </div>
                        <div>
                            <label className="text-slate-400 block mb-1" title="Number of different structure predictions to generate">Num Structures</label>
                            <input
                                type="number"
                                value={predictorParams.diffusion_samples}
                                onChange={(e) => setPredictorParams(p => ({ ...p, diffusion_samples: parseInt(e.target.value) || 1 }))}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-white"
                                min={1} max={100}
                            />
                        </div>
                        <div>
                            <label className="text-slate-400 block mb-1" title="Diffusion sampling steps (higher = better quality, more VRAM)">Sampling Steps</label>
                            <input
                                type="range"
                                min={10}
                                max={1000}
                                step={10}
                                value={predictorParams.sampling_steps}
                                onChange={(e) => setPredictorParams(p => ({ ...p, sampling_steps: parseInt(e.target.value) }))}
                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer"
                            />
                            <div className="flex justify-between text-xs text-slate-500 mt-1">
                                <span>10</span>
                                <span className="text-slate-300 font-medium">{predictorParams.sampling_steps}</span>
                                <span>1000</span>
                            </div>
                        </div>
                        <div>
                            <label className="text-slate-400 block mb-1" title="Number of parallel jobs to split the work into (helps with VRAM)">Parallel Jobs</label>
                            <input
                                type="number"
                                value={predictorParams.num_parallel_jobs}
                                onChange={(e) => setPredictorParams(p => ({ ...p, num_parallel_jobs: parseInt(e.target.value) || 1 }))}
                                className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-white"
                                min={1} max={50}
                            />
                        </div>
                        <div className="flex items-center gap-2 pt-6">
                            <input
                                type="checkbox"
                                checked={predictorParams.use_msa}
                                onChange={(e) => setPredictorParams(p => ({ ...p, use_msa: e.target.checked }))}
                                className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-purple-600"
                            />
                            <label className="text-slate-300">Generate MSA</label>
                        </div>
                        <div className="flex items-center gap-2 pt-6">
                            <input
                                type="checkbox"
                                checked={predictorParams.use_potentials}
                                onChange={(e) => setPredictorParams(p => ({ ...p, use_potentials: e.target.checked }))}
                                className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-blue-600"
                            />
                            <label className="text-slate-300" title="Enable physics-based potentials (Boltz-2x). More accurate but slower.">Use Potentials (Boltz-2x)</label>
                        </div>
                        <div className="flex items-center gap-2 pt-6">
                            <input
                                type="checkbox"
                                checked={runFrustrampnnPost}
                                onChange={(e) => setRunFrustrampnnPost(e.target.checked)}
                                className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-amber-600"
                            />
                            <label className="text-slate-300" title="Run FrustraMPNN after Boltz-2 to annotate frustration per variant">
                                FrustraMPNN QC (post-run)
                            </label>
                        </div>
                        <div>
                            <label className="text-slate-400 block mb-1" title="Step scale for diffusion (lower = more diverse, higher = more conserved). Default: 1.638">Step Scale</label>
                            <input
                                type="range"
                                min={0.5}
                                max={3.0}
                                step={0.1}
                                value={predictorParams.step_scale}
                                onChange={(e) => setPredictorParams(p => ({ ...p, step_scale: parseFloat(e.target.value) }))}
                                className="w-full h-2 bg-slate-700 rounded-lg appearance-none cursor-pointer"
                            />
                            <div className="flex justify-between text-xs text-slate-500 mt-1">
                                <span>0.5 (diverse)</span>
                                <span className="text-slate-300 font-medium">{predictorParams.step_scale.toFixed(1)}</span>
                                <span>3.0 (conserved)</span>
                            </div>
                        </div>
                    </div>
                </section>

                {/* Physics Refinement (OpenMM) for ΔΔG Validation */}
                <section className="pt-4 border-t border-slate-800">
                    <PhysicsRefinementPanel
                        settings={physicsSettings}
                        onSettingsChange={setPhysicsSettings}
                        isAntibody={false}
                    />
                </section>

                {/* 6. Ligands & Cofactors (Complex Mode) */}
                <LigandSelector ligands={ligands} setLigands={setLigands} showCustomSmiles={true} />

                {/* Submit Panel */}
                <div className="flex justify-end pt-6 border-t border-slate-800">
                    <button
                        onClick={handleSubmit}
                        disabled={generatedVariants.length === 0}
                        className="px-6 py-3 bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 disabled:opacity-50 disabled:grayscale text-white font-bold rounded-lg shadow-lg shadow-purple-900/20 transition-all transform active:scale-95 flex items-center gap-2"
                    >
                        <span>🚀</span> Submit {generatedVariants.length} Jobs
                    </button>
                </div>
            </div>

            {/* Sequence Manager Modal */}
            <SequenceManagerModal
                isOpen={showSequenceManager}
                onClose={() => {
                    setShowSequenceManager(false);
                    setSequenceToSave(null);
                }}
                onSelect={(seq) => {
                    setBaseSequence(seq.sequence);
                    // Proactively update job name prefix if it's still default
                    if (jobNamePrefix === 'mutagenesis_lib') {
                        setJobNamePrefix(`${seq.name.toLowerCase().replace(/\s+/g, '_')}_mut`);
                    }
                }}
                initialSequence={sequenceToSave?.sequence || ''}
                initialName={sequenceToSave?.name || ''}
            />

            {/* PDB Import Modal */}
            {showPdbImport && (
                <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center">
                    <div className="bg-slate-900 border border-slate-700 rounded-xl p-6 max-w-2xl w-full max-h-[80vh] overflow-y-auto shadow-2xl">
                        <div className="flex justify-between items-center mb-4">
                            <h3 className="text-lg font-bold text-white">Import Sequence from PDB</h3>
                            <button
                                onClick={() => {
                                    setShowPdbImport(false);
                                    setParsedChains([]);
                                }}
                                className="text-slate-400 hover:text-white"
                            >
                                ✕
                            </button>
                        </div>

                        {parsedChains.length === 0 ? (
                            <TargetAntigenSelector
                                onSelect={async (target) => {
                                    if (target?.url) {
                                        try {
                                            const response = await fetch(target.url);
                                            const blob = await response.blob();
                                            const blobUrl = URL.createObjectURL(blob);
                                            const file = new File([blob], target.name + '.pdb', { type: 'chemical/x-pdb' });
                                            const parsed = await parsePDBFile(file);
                                            if (parsed.chains.length === 1) {
                                                // Single chain - use directly
                                                setBaseSequence(parsed.chains[0].sequence);
                                                setPdbBlobUrl(blobUrl);
                                                setSelectedChainId(parsed.chains[0].id);
                                                setShowPdbImport(false);
                                                setParsedChains([]);
                                            } else if (parsed.chains.length > 1) {
                                                // Multiple chains - let user select
                                                setParsedChains(parsed.chains);
                                                setPdbBlobUrl(blobUrl);  // Store for later use
                                            } else {
                                                alert('No protein chains found in PDB');
                                            }
                                        } catch (err) {
                                            console.error('Failed to parse PDB:', err);
                                            alert('Failed to parse PDB file');
                                        }
                                    } else if (target?.file) {
                                        try {
                                            const blobUrl = URL.createObjectURL(target.file);
                                            const parsed = await parsePDBFile(target.file);
                                            if (parsed.chains.length === 1) {
                                                setBaseSequence(parsed.chains[0].sequence);
                                                setPdbBlobUrl(blobUrl);
                                                setSelectedChainId(parsed.chains[0].id);
                                                setShowPdbImport(false);
                                                setParsedChains([]);
                                            } else if (parsed.chains.length > 1) {
                                                setParsedChains(parsed.chains);
                                                setPdbBlobUrl(blobUrl);  // Store for later use
                                            } else {
                                                alert('No protein chains found in PDB');
                                            }
                                        } catch (err) {
                                            console.error('Failed to parse PDB:', err);
                                            alert('Failed to parse PDB file');
                                        }
                                    }
                                }}
                                initialTab={pdbImportTab}
                            />
                        ) : (
                            <div className="space-y-4">
                                <p className="text-sm text-slate-400">Select a chain to import:</p>
                                <div className="grid grid-cols-2 gap-3">
                                    {parsedChains.map(chain => (
                                        <button
                                            key={chain.id}
                                            onClick={() => {
                                                setBaseSequence(chain.sequence);
                                                setSelectedChainId(chain.id);
                                                setShowPdbImport(false);
                                                setParsedChains([]);
                                            }}
                                            className="p-3 bg-slate-800 hover:bg-slate-700 border border-slate-600 rounded-lg text-left transition-colors"
                                        >
                                            <div className="text-sm font-medium text-cyan-400">Chain {chain.id}</div>
                                            <div className="text-xs text-slate-500">{chain.length} residues</div>
                                            <div className="text-xs text-slate-600 font-mono mt-1 truncate">
                                                {chain.sequence.slice(0, 30)}...
                                            </div>
                                        </button>
                                    ))}
                                </div>
                                <button
                                    onClick={() => setParsedChains([])}
                                    className="text-sm text-slate-400 hover:text-white"
                                >
                                    ← Back to PDB selection
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
