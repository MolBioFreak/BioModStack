import { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { SequenceManagerModal } from './SequenceManagerModal';
import { parseRegions, generateLibrary, normalizeAminoAcids, formatMutationLabel } from '../utils/mutationUtils';
import type { VariantSequence, SubstitutionStrategy, Mutation } from '../utils/mutationUtils';
import { InteractiveSequence } from './InteractiveSequence';
import { LigandSelector, type LigandEntry } from './LigandSelector';
import { TargetAntigenSelector } from './TargetAntigenSelector';
import { parsePDBFile, type Chain } from '../utils/pdbUtils';
import EpitopeMolstarViewer from './EpitopeMolstarViewer';
import { PhysicsRefinementPanel, type PhysicsRefinementSettings } from './PhysicsRefinementPanel';
import { DEFAULT_SETTINGS as PHYSICS_DEFAULTS } from './physicsRefinementSettings';
import { createLatestAsyncResourceController } from '../lib/latestAsyncResource';


interface MutagenesisTemplateProps {
    onBack: () => void;
    onSubmit: (jobName: string, variants: VariantSequence[], predictorConfig: UntypedApiValue) => void;
}

export function MutagenesisTemplate({ onBack, onSubmit }: MutagenesisTemplateProps) {
    // Top-level state
    const [jobNamePrefix, setJobNamePrefix] = useState('mutagenesis_lib');
    const [baseSequence, setBaseSequence] = useState('');
    const [mode, setMode] = useState<'library' | 'manual'>('library');

    // Library Generator State
    const [regionInput, setRegionInput] = useState('');
    const [strategy, setStrategy] = useState<SubstitutionStrategy>('random');
    const [numVariants, setNumVariants] = useState(20);
    const [mutationsPerVariant, setMutationsPerVariant] = useState<[number, number]>([1, 2]); // Min, Max
    const [mutationCountMode, setMutationCountMode] = useState<'range' | 'exact' | 'set'>('range');
    const [mutationCountExact, setMutationCountExact] = useState(1);
    const [mutationCountSetInput, setMutationCountSetInput] = useState('1,2,3');
    // Target Positions: positive selection (positions TO mutate)
    const [selectedPositions, setSelectedPositions] = useState<Set<number>>(new Set());
    const [lastClickedPos, setLastClickedPos] = useState<number | null>(null);
    const [isDragging, setIsDragging] = useState(false);
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
    // Selected positions for Library Generator (positive selection)
    const selectedPositionsList = useMemo(() => {
        return Array.from(selectedPositions)
            .filter(pos => pos > 0 && pos <= baseSequence.length)
            .sort((a, b) => a - b);
    }, [selectedPositions, baseSequence.length]);
    // Note: excludedPositions state kept for future Affinity Maturation enhancements
    const [generatedVariants, setGeneratedVariants] = useState<VariantSequence[]>([]);

    // Predictor Config
    const [predictor, setPredictor] = useState<'boltz' | 'rf3' | 'esmfold2' | 'both'>('boltz');
    const [predictorParams, setPredictorParams] = useState({
        recycling_steps: 3,
        diffusion_samples: 1,
        sampling_steps: 50,
        num_parallel_jobs: 1,
        use_msa: true,
        use_potentials: false,
        step_scale: 1.638
    });

    // Complex Mode: Ligands & Ions
    const [ligands, setLigands] = useState<LigandEntry[]>([]);

    // Physics refinement (OpenMM) - for ΔΔG validation
    const [physicsSettings, setPhysicsSettings] = useState<PhysicsRefinementSettings>(PHYSICS_DEFAULTS);

    // PDB Import State
    const [showPdbImport, setShowPdbImport] = useState(false);
    const [pdbImportTab, setPdbImportTab] = useState<'upload' | 'runs' | 'presets' | 'rcsb'>('upload');
    const [parsedChains, setParsedChains] = useState<Chain[]>([]);
    const [pdbBlobUrl, setPdbBlobUrl] = useState<string | null>(null);
    const pdbBlobUrlRef = useRef<string | null>(null);
    const targetSelectionControllerRef = useRef(createLatestAsyncResourceController());
    const replacePdbBlobUrl = useCallback((nextUrl: string | null) => {
        if (pdbBlobUrlRef.current && pdbBlobUrlRef.current !== nextUrl) URL.revokeObjectURL(pdbBlobUrlRef.current);
        pdbBlobUrlRef.current = nextUrl;
        setPdbBlobUrl(nextUrl);
    }, []);
    useEffect(() => () => {
        if (pdbBlobUrlRef.current) URL.revokeObjectURL(pdbBlobUrlRef.current);
        targetSelectionControllerRef.current.dispose();
    }, []);
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

    // Handlers for Target Positions selector (positive selection with shift+click/drag)
    const handlePositionClick = (pos: number, event: React.MouseEvent) => {
        if (event.shiftKey && lastClickedPos !== null) {
            // Shift+Click: select range from lastClickedPos to pos
            const start = Math.min(lastClickedPos, pos);
            const end = Math.max(lastClickedPos, pos);
            setSelectedPositions(prev => {
                const next = new Set(prev);
                for (let i = start; i <= end; i++) {
                    next.add(i);
                }
                return next;
            });
        } else {
            // Normal click: toggle single position
            setSelectedPositions(prev => {
                const next = new Set(prev);
                if (next.has(pos)) {
                    next.delete(pos);
                } else {
                    next.add(pos);
                }
                return next;
            });
        }
        setLastClickedPos(pos);
    };

    const handleDragStart = (pos: number) => {
        setIsDragging(true);
        setSelectedPositions(prev => new Set(prev).add(pos));
        setLastClickedPos(pos);
    };

    const handleDragMove = (pos: number) => {
        if (isDragging) {
            setSelectedPositions(prev => new Set(prev).add(pos));
        }
    };

    const handleDragEnd = () => {
        setIsDragging(false);
    };

    const handleGeneratePreview = useCallback(() => {
        if (!baseSequence) return;

        if (mode === 'library') {
            const allowedAAs = normalizeAminoAcids(allowedAAsInput);
            const blockedAAs = normalizeAminoAcids(blockedAAsInput);
            const excludeResidues = normalizeAminoAcids(excludeResiduesInput);
            const mutationCountSet = mutationCountSetInput
                .split(',')
                .map(v => parseInt(v.trim()))
                .filter(v => Number.isFinite(v) && v > 0);

            // Merge text-based regions with clicked selectedPositions
            const mergedPositions = new Set<number>(selectedPositions);
            for (const region of regions) {
                for (let i = region.start; i <= region.end; i++) {
                    if (i > 0 && i <= baseSequence.length) {
                        mergedPositions.add(i);
                    }
                }
            }

            // Convert to regions format for generateLibrary (contiguous ranges)
            const sortedPositions = Array.from(mergedPositions).sort((a, b) => a - b);
            const mergedRegions: { id: string; start: number; end: number; enabled: boolean }[] = [];
            if (sortedPositions.length > 0) {
                let start = sortedPositions[0];
                let end = sortedPositions[0];
                for (let i = 1; i <= sortedPositions.length; i++) {
                    const pos = sortedPositions[i];
                    if (pos === end + 1) {
                        end = pos;
                    } else {
                        mergedRegions.push({ id: `merged_${mergedRegions.length}`, start, end, enabled: true });
                        start = pos;
                        end = pos;
                    }
                }
            }

            const variants = generateLibrary(
                baseSequence,
                mergedRegions, // Use merged regions from clicks + text input
                strategy,
                numVariants,
                mutationsPerVariant,
                {
                    customAA: allowedAAs,
                    allowedAAs,
                    blockedAAs,
                    excludeFromResidues: excludeResidues,
                    excludedPositions: [], // No longer using exclusion in standard mode
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
    }, [allowedAAsInput, allowDeletions, allowInsertions, baseSequence, blockedAAsInput, excludeResiduesInput, indelProbability, indelSizes, manualMutations, mode, mutationCountExact, mutationCountMode, mutationCountSetInput, mutationsPerVariant, numVariants, regions, selectedPositions, strategy]);

    // Manual Mutation Handlers
    const handleAddMutation = (pos: number, toAA: string) => {
        const fromAA = baseSequence[pos - 1];
        setManualMutations(prev => {
            // Remove existing mutation at this pos if unknown
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
    }, [handleGeneratePreview, mode]);

    const handleSubmit = () => {
        if (generatedVariants.length === 0) return;
        onSubmit(jobNamePrefix, generatedVariants, {
            predictor,
            ...predictorParams,
            // Reference sequence for logging (mutants regenerate MSAs)
            msa_reference_sequence: baseSequence,

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
            <header className="flex items-center justify-between mb-6 border-b border-slate-800 pb-4">
                <div className="flex items-center gap-3">
                    <button
                        onClick={onBack}
                        className="p-2 hover:bg-slate-700 rounded-lg transition-colors text-slate-400 hover:text-white"
                    >
                        ← Back
                    </button>
                    <div>
                        <h2 className="text-xl font-bold bg-gradient-to-r from-accent to-accent-secondary bg-clip-text text-transparent">
                            Mutagenesis Library
                        </h2>
                        <p className="text-slate-400 text-sm">Generate variant libraries for structure prediction</p>
                    </div>
                </div>

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
                        className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-accent outline-none"
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
                                        ? 'bg-accent/20 text-accent border border-accent/50'
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
                                        onResidueClick={(residueKey) => {
                                            // Parse residue key (e.g., "A45") to extract position
                                            const match = residueKey.match(/^([A-Z])(\d+)$/);
                                            if (match) {
                                                const pos = parseInt(match[2], 10);
                                                if (pos > 0 && pos <= baseSequence.length) {
                                                    setSelectedPositions(prev => {
                                                        const next = new Set(prev);
                                                        if (next.has(pos)) {
                                                            next.delete(pos);
                                                        } else {
                                                            next.add(pos);
                                                        }
                                                        return next;
                                                    });
                                                }
                                            }
                                        }}
                                    />
                                </div>
                            )}
                        </div>
                    )}
                </section>

                {/* 2. Mode Toggle */}
                <div className="flex bg-slate-800/50 p-1 rounded-lg w-fit">
                    <button
                        onClick={() => setMode('library')}
                        className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${mode === 'library' ? 'bg-accent text-white shadow' : 'text-slate-400 hover:text-slate-200'}`}
                    >
                        Library Generator
                    </button>
                    <button
                        onClick={() => setMode('manual')}
                        className={`px-4 py-2 rounded-md text-sm font-medium transition-all ${mode === 'manual' ? 'bg-accent text-white shadow' : 'text-slate-400 hover:text-slate-200'}`}
                    >
                        Manual Editor
                    </button>
                </div>

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
                                className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-white font-mono text-sm focus:ring-2 focus:ring-accent outline-none"
                            />
                            {regions.length > 0 && (
                                <div className="flex gap-2 mt-2 flex-wrap">
                                    {regions.map(r => (
                                        <span key={r.id} className="text-xs bg-accent/20 text-accent px-2 py-1 rounded border border-accent/30">
                                            Pos {r.start}-{r.end} ({r.end - r.start + 1} aa)
                                        </span>
                                    ))}
                                </div>
                            )}
                        </div>

                        {/* Mutation Rules */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <label className="block text-sm font-medium text-slate-300 mb-1">Target Positions</label>
                                <p className="text-xs text-slate-500 mb-2">
                                    Click to select positions to mutate. <span className="text-emerald-400">Shift+Click</span> for range. <span className="text-cyan-400">Drag</span> to paint.
                                </p>
                                {baseSequence ? (
                                    <div className="bg-slate-950/60 border border-slate-800 rounded-lg p-3">
                                        <div className="flex items-center justify-between mb-2">
                                            <span className="text-[11px] text-emerald-400">
                                                Selected: {selectedPositionsList.length} positions
                                                {selectedPositionsList.length > 0 && selectedPositionsList.length <= 10
                                                    ? ` (${selectedPositionsList.join(', ')})`
                                                    : selectedPositionsList.length > 10
                                                        ? ` (${selectedPositionsList.slice(0, 5).join(', ')}...${selectedPositionsList.slice(-3).join(', ')})`
                                                        : ''}
                                            </span>
                                            <button
                                                onClick={() => setSelectedPositions(new Set())}
                                                className="text-[11px] text-slate-400 hover:text-white"
                                                disabled={selectedPositionsList.length === 0}
                                            >
                                                Clear
                                            </button>
                                        </div>
                                        <div
                                            className="flex flex-wrap gap-1 font-mono text-xs leading-none max-h-[160px] overflow-y-auto select-none"
                                            onMouseUp={handleDragEnd}
                                            onMouseLeave={handleDragEnd}
                                        >
                                            {baseSequence.split('').map((aa, idx) => {
                                                const pos = idx + 1;
                                                const isSelected = selectedPositions.has(pos);
                                                const isInRegion = regions.some(r => pos >= r.start && pos <= r.end);
                                                return (
                                                    <button
                                                        key={pos}
                                                        onClick={(e) => handlePositionClick(pos, e)}
                                                        onMouseDown={() => handleDragStart(pos)}
                                                        onMouseEnter={() => handleDragMove(pos)}
                                                        className={`w-7 h-7 rounded border transition-colors ${isSelected
                                                            ? 'bg-emerald-600/40 border-emerald-400 text-emerald-200 font-bold'
                                                            : isInRegion
                                                                ? 'bg-accent/20 border-accent text-accent'
                                                                : 'bg-slate-900 border-slate-700 text-slate-500 hover:bg-slate-800 hover:text-slate-200'
                                                            }`}
                                                        title={`Pos ${pos}: ${aa}${isSelected ? ' (target)' : isInRegion ? ' (in region)' : ''}`}
                                                    >
                                                        {aa}
                                                    </button>
                                                );
                                            })}
                                        </div>
                                        <div className="mt-2 text-[10px] text-slate-600">
                                            Tip: Regions input above is merged with clicked selections
                                        </div>
                                    </div>
                                ) : (
                                    <div className="text-xs text-slate-600 bg-slate-950/60 border border-slate-800 rounded-lg p-3">
                                        Load a sequence to select target positions.
                                    </div>
                                )}
                                <label className="block text-sm font-medium text-slate-300 mb-1 mt-4">Protect WT Residue Types</label>
                                <p className="text-xs text-slate-500 mb-2">Don't mutate positions where the wildtype is one of these (e.g., "CP" preserves all cysteines/prolines)</p>
                                <input
                                    type="text"
                                    value={excludeResiduesInput}
                                    onChange={(e) => setExcludeResiduesInput(e.target.value.toUpperCase())}
                                    placeholder="e.g., C, P (keep existing cysteines/prolines)"
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
                                <label className="block text-sm font-medium text-slate-300 mb-1 mt-4">Never Substitute TO These AAs</label>
                                <p className="text-xs text-slate-500 mb-2">When mutating, don't introduce these amino acids as replacements</p>
                                <input
                                    type="text"
                                    value={blockedAAsInput}
                                    onChange={(e) => setBlockedAAsInput(e.target.value.toUpperCase())}
                                    placeholder="e.g., C, M (avoid creating new cysteines/methionines)"
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
                                            className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-accent"
                                        />
                                        Allow insertions
                                    </label>
                                    <label className="flex items-center gap-2 text-sm text-slate-300">
                                        <input
                                            type="checkbox"
                                            checked={allowDeletions}
                                            onChange={(e) => setAllowDeletions(e.target.checked)}
                                            className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-accent"
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
                                                    className="w-3 h-3 rounded bg-slate-900 border-slate-700 text-accent"
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
                                    onChange={(e) => setStrategy(e.target.value as UntypedApiValue)}
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
                                                onChange={(e) => setMutationCountMode(e.target.value as UntypedApiValue)}
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
                                    disabled={!baseSequence || (regions.length === 0 && selectedPositions.size === 0)}
                                    className="px-3 py-1.5 bg-accent hover:bg-accent disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm rounded transition-colors"
                                >
                                    Generate Preview
                                </button>
                            </div>

                            {generatedVariants.length > 0 ? (
                                <div className="max-h-60 overflow-y-auto space-y-2 pr-2 custom-scrollbar">
                                    {generatedVariants.map((v, i) => (
                                        <div key={i} className="text-xs bg-slate-900 p-2 rounded flex justify-between items-center border border-slate-800">
                                            <span className="text-accent font-mono font-medium">{v.mutations.map(formatMutationLabel).join(', ')}</span>
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
                                    <span className="text-accent font-mono font-medium">
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


                {/* 6. Predictor Settings */}
                <section className="pt-6 border-t border-slate-800">
                    <h3 className="text-sm font-semibold text-slate-200 mb-4">Prediction Settings</h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
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
                        <div className={`cursor-pointer p-3 rounded-lg border text-center transition-all ${predictor === 'esmfold2' ? 'bg-violet-600/20 border-violet-500 text-violet-300' : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-600'}`}
                            onClick={() => setPredictor('esmfold2')}
                        >
                            <div className="font-bold mb-1">ESMFold2</div>
                            <div className="text-xs opacity-70">Single Model</div>
                        </div>
                        <div className={`cursor-pointer p-3 rounded-lg border text-center transition-all ${predictor === 'both' ? 'bg-accent/20 border-accent text-accent' : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-600'}`}
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
                                className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-accent"
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
                            <label className="text-slate-300" title="Enable physics/FK steering potentials (Boltz-2x). Can improve geometry, but high sample counts multiply internal particles and need memory-safe batching.">Use Potentials (Boltz-2x)</label>
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
                        className="px-6 py-3 bg-gradient-to-r from-blue-600 to-accent-secondary hover:from-blue-500 hover:to-accent disabled:opacity-50 disabled:grayscale text-white font-bold rounded-lg shadow-lg shadow-accent/20 transition-all transform active:scale-95 flex items-center gap-2"
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
                                    const selectionToken = targetSelectionControllerRef.current.begin();
                                    if (target?.url) {
                                        let blobUrl: string | null = null;
                                        try {
                                            const response = await fetch(target.url);
                                            const blob = await response.blob();
                                            blobUrl = URL.createObjectURL(blob);
                                            const file = new File([blob], target.name + '.pdb', { type: 'chemical/x-pdb' });
                                            const parsed = await parsePDBFile(file);
                                            if (!targetSelectionControllerRef.current.isCurrent(selectionToken)) {
                                                if (blobUrl) URL.revokeObjectURL(blobUrl);
                                                return;
                                            }
                                            if (parsed.chains.length === 1) {
                                                // Single chain - use directly
                                                setBaseSequence(parsed.chains[0].sequence);
                                                replacePdbBlobUrl(blobUrl);
                                                setSelectedChainId(parsed.chains[0].id);
                                                setShowPdbImport(false);
                                                setParsedChains([]);
                                            } else if (parsed.chains.length > 1) {
                                                // Multiple chains - let user select
                                                setParsedChains(parsed.chains);
                                                replacePdbBlobUrl(blobUrl);  // Store for later use
                                            } else {
                                                if (blobUrl) URL.revokeObjectURL(blobUrl);
                                                alert('No protein chains found in PDB');
                                            }
                                        } catch (err) {
                                            if (blobUrl) URL.revokeObjectURL(blobUrl);
                                            if (!targetSelectionControllerRef.current.isCurrent(selectionToken)) return;
                                            console.error('Failed to parse PDB:', err);
                                            alert('Failed to parse PDB file');
                                        }
                                    } else if (target?.file) {
                                        let blobUrl: string | null = null;
                                        try {
                                            blobUrl = URL.createObjectURL(target.file);
                                            const parsed = await parsePDBFile(target.file);
                                            if (!targetSelectionControllerRef.current.isCurrent(selectionToken)) {
                                                if (blobUrl) URL.revokeObjectURL(blobUrl);
                                                return;
                                            }
                                            if (parsed.chains.length === 1) {
                                                setBaseSequence(parsed.chains[0].sequence);
                                                replacePdbBlobUrl(blobUrl);
                                                setSelectedChainId(parsed.chains[0].id);
                                                setShowPdbImport(false);
                                                setParsedChains([]);
                                            } else if (parsed.chains.length > 1) {
                                                setParsedChains(parsed.chains);
                                                replacePdbBlobUrl(blobUrl);  // Store for later use
                                            } else {
                                                if (blobUrl) URL.revokeObjectURL(blobUrl);
                                                alert('No protein chains found in PDB');
                                            }
                                        } catch (err) {
                                            if (blobUrl) URL.revokeObjectURL(blobUrl);
                                            if (!targetSelectionControllerRef.current.isCurrent(selectionToken)) return;
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
