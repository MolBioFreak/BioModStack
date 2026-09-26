import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { DE_NOVO_PRELOAD_SELECTION } from './dashboard/IndependentProvisionPanel';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { completeCurrentLaunchContext, submitJob, type Job } from '../lib/api';
import { ModelDocumentationLinks } from './ModelDocumentationLinks';
import { ProteinLocalRedesignTemplate } from './ProteinLocalRedesignTemplate';
import ShapeBlueprintTemplate from './ShapeBlueprintTemplate';
import { ProteinDesignSections, ProteinDesignPanel, ProteinDesignRun, themedInsetStyle } from './ProteinDesignWorkflow';
import { LaProteinaMotifInput, type LaProteinaMotifInspection } from './LaProteinaMotifInput';
import { DeNovoContextInput } from './DeNovoContextInput';
import { DE_NOVO_MODIFICATION_MODE_CARDS, type ModificationMode } from './proteinModificationModes';

export interface DeNovoNavigationState {
    modification_mode: string;
    generator?: string;
    design_task?: string;
}

interface ProteinModificationTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
    requiredPinnedGpu?: number | null;
    onDraftChange?: (draft: Record<string, unknown>) => void;
    onNavigationChange?: (state: DeNovoNavigationState) => void;
    navigationState?: DeNovoNavigationState;
    runDetails?: ReactNode;
    onOpenTemplateManager?: (context: {
        currentParams?: Record<string, unknown>;
        currentModelId?: string;
        currentMode?: string;
        baseTemplateId?: string;
    }) => void;
}

type DeNovoBackend = 'disco' | 'laproteina';

const DE_NOVO_TASK_OPTIONS: Record<DeNovoBackend, Array<{ value: string; label: string }>> = {
    disco: [
        { value: 'unconditional', label: 'Unconditional' },
        { value: 'ligand_conditioned', label: 'Ligand conditioned' },
        { value: 'dna_conditioned', label: 'DNA conditioned' },
        { value: 'rna_conditioned', label: 'RNA conditioned' },
        { value: 'custom_json', label: 'Custom native JSON' },
    ],
    laproteina: [
        { value: 'unconditional', label: 'Unconditional' },
        { value: 'motif_scaffolding', label: 'Motif scaffolding' },
    ],
};

const fieldClass = 'w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]';
const labelClass = 'space-y-1 text-sm font-medium text-[var(--text-primary)]';

const initialString = (values: Record<string, unknown> | undefined, key: string, fallback: string): string => {
    const value = values?.[key];
    return typeof value === 'string' ? value : fallback;
};

const initialNumber = (values: Record<string, unknown> | undefined, key: string, fallback: number): number => {
    const value = Number(values?.[key]);
    return Number.isFinite(value) ? value : fallback;
};

const resolveNavigation = (values?: Record<string, unknown>): DeNovoNavigationState => {
    const rawMode = values?.modification_mode;
    const modification_mode = rawMode === 'rfd3_local_redesign'
        ? 'rfd3_iteration'
        : rawMode === 'rfd3_iteration' || rawMode === 'region_redesign' || rawMode === 'shape_blueprint' ? rawMode : 'de_novo_design';
    if (modification_mode !== 'de_novo_design') return { modification_mode };
    const engine = values?.generator ?? values?.backend;
    return {
        modification_mode,
        generator: engine === 'disco' || engine === 'laproteina' ? engine : 'rfd3',
        design_task: typeof values?.design_task === 'string' ? values.design_task : 'unconditional',
    };
};
const draftKey = (state: DeNovoNavigationState) => state.modification_mode === 'de_novo_design'
    ? `${state.modification_mode}:${state.generator}:${state.design_task}` : state.modification_mode;

export function ProteinModificationTemplate({
    onBack, initialValues, requiredPinnedGpu = null, onDraftChange, onNavigationChange, navigationState, runDetails, onOpenTemplateManager,
}: ProteinModificationTemplateProps) {
    const [navigation, setNavigation] = useState(() => resolveNavigation({ ...navigationState, ...initialValues }));
    const drafts = useRef<Record<string, Record<string, unknown>>>(
        initialValues?.de_novo_drafts && typeof initialValues.de_novo_drafts === 'object'
            ? { ...initialValues.de_novo_drafts as Record<string, Record<string, unknown>> } : {},
    );
    const activeKey = draftKey(navigation);
    const initialized = useRef(false);
    if (!initialized.current) {
        const { de_novo_drafts: _drafts, ...activeValues } = initialValues ?? {};
        drafts.current[activeKey] = { ...drafts.current[activeKey], ...activeValues };
        initialized.current = true;
    }
    const callbacks = useRef({ onDraftChange, onNavigationChange });
    callbacks.current = { onDraftChange, onNavigationChange };
    const validatedRedesign = navigation.modification_mode === 'region_redesign';
    const savedMode = navigation.modification_mode;
    const navigationSignature = JSON.stringify(navigationState);
    const previousNavigation = useRef(navigationSignature);
    useEffect(() => {
        if (previousNavigation.current === navigationSignature) return;
        previousNavigation.current = navigationSignature;
        if (navigationState) setNavigation(resolveNavigation({ ...navigationState }));
    }, [navigationSignature, navigationState]);
    const reportDraft = useCallback((draft: Record<string, unknown>) => {
        // Child snapshots replace values, including explicit clears; inactive drafts never enter requests.
        drafts.current[activeKey] = { ...draft, modification_mode: savedMode };
        callbacks.current.onDraftChange?.({ ...draft, ...navigation, modification_mode: savedMode, de_novo_drafts: { ...drafts.current } });
    }, [activeKey, navigation.modification_mode, navigation.generator, navigation.design_task, savedMode]);
    const changeNavigation = (state: DeNovoNavigationState) => {
        const next = resolveNavigation({ ...state });
        // Only shared intent seeds an untouched engine draft. Never translate
        // length ranges, structural roles or model-native conditions by guessing.
        const nextKey = draftKey(next);
        if (!Object.hasOwn(drafts.current, nextKey)) {
            const current = drafts.current[activeKey] ?? {};
            drafts.current[nextKey] = Object.fromEntries(
                ['job_name', 'num_designs'].filter(key => Object.hasOwn(current, key)).map(key => [key, current[key]]),
            );
        }
        setNavigation(next);
        callbacks.current.onNavigationChange?.(next);
    };
    const mode: ModificationMode = validatedRedesign ? 'rfd3_iteration' : navigation.modification_mode as ModificationMode;
    const generator = navigation.generator ?? 'rfd3';
    const task = navigation.design_task ?? 'unconditional';
    const lastGeneration = useRef({ generator, design_task: task });
    const lastRedesign = useRef(validatedRedesign || drafts.current.region_redesign ? 'region_redesign' : 'rfd3_iteration');
    const chooseMode = (next: ModificationMode) => changeNavigation(next === 'de_novo_design'
        ? { modification_mode: next, ...lastGeneration.current }
        : { modification_mode: next === 'rfd3_iteration' ? lastRedesign.current : next });
    if (mode === 'de_novo_design') lastGeneration.current = { generator, design_task: task };
    if (mode === 'rfd3_iteration') lastRedesign.current = navigation.modification_mode;
    // Keep the hydration object stable while a child reports changes.
    const childValues = useRef<{ key: string; values: Record<string, unknown> } | null>(null);
    if (childValues.current?.key !== activeKey) childValues.current = { key: activeKey, values: drafts.current[activeKey] ?? {} };
    return (
        <div className="space-y-6 text-[var(--text-primary)]" data-bms-de-novo-form="complete">
            <div className="flex items-center gap-3">
                <button type="button" onClick={onBack} className="rounded-lg border border-[var(--border-primary)] px-3 py-1.5 text-sm">Back</button>
                <h2 className="text-2xl font-semibold">De Novo Protein Design</h2>
            </div>
            <nav aria-label="Design task" className="flex flex-wrap gap-2">
                {DE_NOVO_MODIFICATION_MODE_CARDS.map(card => <button type="button" key={card.id}
                    aria-pressed={mode === card.id} onClick={() => chooseMode(card.id)}
                    className={`rounded-lg border px-4 py-2 text-sm ${mode === card.id ? 'border-[var(--accent-primary)] bg-[var(--bg-tertiary)]' : 'border-[var(--border-primary)]'}`}>{card.label}</button>)}
            </nav>
            {mode === 'de_novo_design' ? <>
                <div className="grid gap-4 sm:grid-cols-2">
                    <label className={labelClass}>Goal
                        <select className={fieldClass} value={task} onChange={event => {
                            const design_task = event.target.value;
                            const engine = design_task === 'motif_scaffolding' ? 'laproteina' : design_task === 'unconditional' ? generator : 'disco';
                            changeNavigation({ modification_mode: mode, generator: engine, design_task });
                        }}>
                            <option value="unconditional">Explore new folds</option>
                            <option value="dna_conditioned">Design around DNA · DISCO</option>
                            <option value="rna_conditioned">Design around RNA · DISCO</option>
                            <option value="ligand_conditioned">Design around a small molecule · DISCO</option>
                            <option value="motif_scaffolding">Scaffold a motif · La-Proteina</option>
                            <option value="custom_json">Custom native JSON · DISCO</option>
                        </select>
                    </label>
                    <label className={labelClass}>Engine
                        <select className={fieldClass} value={generator} onChange={event => changeNavigation({ modification_mode: mode, generator: event.target.value, design_task: 'unconditional' })}>
                            {(task === 'unconditional' ? ['rfd3', 'disco', 'laproteina'] : [generator]).map(engine => <option key={engine} value={engine}>{engine === 'rfd3' ? 'RFD3' : engine === 'disco' ? 'DISCO' : 'La-Proteina'}</option>)}
                        </select>
                    </label>
                </div>
                <GenerationEditor key={activeKey} initialValues={childValues.current.values} generator={generator}
                    designTask={task} runDetails={runDetails} onDraftChange={reportDraft} />
            </> : mode === 'rfd3_iteration' ? <ProteinLocalRedesignTemplate key={activeKey} embedded
                onBack={onBack} initialValues={childValues.current.values}
                submissionModelId={validatedRedesign ? 'protein_modification_experimental' : 'protein_local_redesign'}
                requiredPinnedGpu={requiredPinnedGpu} runDetails={runDetails} onDraftChange={reportDraft} />
                : <ShapeBlueprintTemplate key={activeKey} embedded initialValues={childValues.current.values} runDetails={runDetails} onDraftChange={reportDraft} />}
            {onOpenTemplateManager && <button type="button" className="rounded-lg border border-[var(--border-primary)] px-4 py-2 text-sm"
                onClick={() => onOpenTemplateManager({
                    currentParams: { ...drafts.current[activeKey], ...navigation, modification_mode: savedMode, de_novo_drafts: { ...drafts.current } },
                    currentModelId: mode === 'rfd3_iteration' && !validatedRedesign ? 'protein_local_redesign' : 'protein_modification_experimental',
                    baseTemplateId: 'protein_modification_experimental',
                    currentMode: mode === 'rfd3_iteration' ? validatedRedesign ? 'region_redesign' : 'rfd3_local_redesign' : mode,
                })}>Template Manager</button>}
        </div>
    );
}

function GenerationEditor({ initialValues, generator, designTask, onDraftChange, runDetails }: {
    initialValues: Record<string, unknown>;
    generator: string;
    designTask: string;
    runDetails?: ReactNode;
    onDraftChange: (draft: Record<string, unknown>) => void;
}) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const backend: DeNovoBackend = generator === 'laproteina' ? 'laproteina' : 'disco';
    const inputSection = designTask === 'unconditional' ? 'Design' : 'Source and regions';
    const [section, setSection] = useState(inputSection);
    const [jobName, setJobName] = useState(initialString(initialValues, 'job_name', 'protein_modification'));
    const [numDesigns, setNumDesigns] = useState(initialNumber(initialValues, 'num_designs', 8));
    const [minLength, setMinLength] = useState(initialNumber(initialValues, 'min_length', 100));
    const [maxLength, setMaxLength] = useState(initialNumber(initialValues, 'max_length', 200));
    const [seed, setSeed] = useState(initialNumber(initialValues, 'seed', 0));
    const [dumpTrajectories, setDumpTrajectories] = useState(initialValues?.dump_trajectories === true);
    const [targetLengths, setTargetLengths] = useState(initialString(initialValues, 'target_lengths', '100,200'));
    const [laproteinaPreset, setLaproteinaPreset] = useState(initialString(initialValues, 'laproteina_preset', 'ucond_tri'));
    const [laproteinaSamples, setLaproteinaSamples] = useState(initialNumber(initialValues, 'laproteina_samples_per_length', 8));
    const [laproteinaSteps, setLaproteinaSteps] = useState(initialNumber(initialValues, 'laproteina_num_steps', 400));
    const [motifTaskName, setMotifTaskName] = useState(initialString(initialValues, 'laproteina_motif_task_name', ''));
    const [motifPdb, setMotifPdb] = useState(initialString(initialValues, 'laproteina_motif_pdb', ''));
    const [motifInspection, setMotifInspection] = useState(initialValues.laproteina_motif_inspection as LaProteinaMotifInspection | undefined);
    const [motifContig, setMotifContig] = useState(initialString(initialValues, 'laproteina_contig_string', ''));
    const [motifSegmentOrder, setMotifSegmentOrder] = useState(initialString(initialValues, 'laproteina_segment_order', ''));
    const [motifAtomSelectionMode, setMotifAtomSelectionMode] = useState(initialString(initialValues, 'laproteina_atom_selection_mode', 'all_atom'));
    const [motifMinLength, setMotifMinLength] = useState(initialString(initialValues, 'laproteina_motif_min_length', ''));
    const [motifMaxLength, setMotifMaxLength] = useState(initialString(initialValues, 'laproteina_motif_max_length', ''));
    const [discoExperiment, setDiscoExperiment] = useState(initialString(initialValues, 'disco_experiment', 'designable'));
    const [discoEffort, setDiscoEffort] = useState(initialString(initialValues, 'disco_effort', 'fast'));
    const [discoInferenceSeeds, setDiscoInferenceSeeds] = useState(initialNumber(initialValues, 'disco_num_inference_seeds', 8));
    const [discoSeeds, setDiscoSeeds] = useState(initialString(initialValues, 'disco_seeds', ''));
    const [discoAttention, setDiscoAttention] = useState(initialValues.disco_use_deepspeed_evo_attention === true);
    const [discoInputJson, setDiscoInputJson] = useState(initialString(initialValues, 'disco_input_json_path', ''));
    const [ligandSdf, setLigandSdf] = useState(initialString(initialValues, 'disco_ligand_sdf', ''));
    const [ligandName, setLigandName] = useState(initialString(initialValues, 'disco_ligand_name', ''));
    const [nucleicSequence, setNucleicSequence] = useState(initialString(initialValues, 'disco_na_sequence', ''));
    const [error, setError] = useState<string | null>(null);

    const draftJson = JSON.stringify({
        ...Object.fromEntries(['laproteina_checkpoint_dir', 'laproteina_data_path', 'disco_checkpoint_path', 'disco_cutlass_path'].filter(key => Object.hasOwn(initialValues, key)).map(key => [key, initialValues[key]])),
        job_name: jobName,
        num_designs: numDesigns,
        min_length: minLength,
        max_length: maxLength,
        seed: seed,
        dump_trajectories: dumpTrajectories,
        target_lengths: targetLengths,
        laproteina_preset: laproteinaPreset,
        laproteina_samples_per_length: laproteinaSamples,
        laproteina_num_steps: laproteinaSteps,
        laproteina_motif_task_name: motifTaskName,
        laproteina_motif_pdb: motifPdb,
        laproteina_motif_inspection: motifInspection,
        laproteina_contig_string: motifContig,
        laproteina_segment_order: motifSegmentOrder,
        laproteina_atom_selection_mode: motifAtomSelectionMode,
        laproteina_motif_min_length: motifMinLength,
        laproteina_motif_max_length: motifMaxLength,
        disco_experiment: discoExperiment,
        disco_effort: discoEffort,
        disco_num_inference_seeds: discoInferenceSeeds,
        disco_seeds: discoSeeds,
        disco_use_deepspeed_evo_attention: discoAttention,
        disco_input_json_path: discoInputJson,
        disco_ligand_sdf: ligandSdf,
        disco_ligand_name: ligandName,
        disco_na_sequence: nucleicSequence,
    });
    useEffect(() => { onDraftChange(JSON.parse(draftJson)); }, [draftJson, onDraftChange]);

    const submitMutation = useMutation({
        mutationFn: async (payload: Partial<Job>) => submitJob(payload),
        onSuccess: async (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate(await completeCurrentLaunchContext(response.data) ?? '/');
        },
        onError: (err: Error) => setError(err.message || 'Failed to submit protein modification job'),
    });

    const buildAlternativeWorkflowRequest = () => ({
            name: jobName.trim(),
            model_id: 'protein_modification_experimental',
            mode: 'de_novo_design',
            params: {
                modification_mode: 'de_novo_design',
                generator: backend,
                backend,
                design_task: designTask,
                num_designs: numDesigns,
                target_lengths: targetLengths,
                ...(backend === 'laproteina' ? {
                    ...Object.fromEntries(['laproteina_checkpoint_dir', 'laproteina_data_path'].filter(key => Object.hasOwn(initialValues, key)).map(key => [key, initialValues[key]])),
                    laproteina_preset: laproteinaPreset,
                    laproteina_samples_per_length: laproteinaSamples,
                    laproteina_num_steps: laproteinaSteps,
                    laproteina_motif_task_name: motifTaskName || undefined,
                    laproteina_motif_pdb: motifPdb || undefined,
                    laproteina_contig_string: motifContig || undefined,
                    laproteina_segment_order: motifSegmentOrder || undefined,
                    laproteina_atom_selection_mode: motifAtomSelectionMode,
                    laproteina_motif_min_length: motifMinLength || undefined,
                    laproteina_motif_max_length: motifMaxLength || undefined,
                } : {
                    ...Object.fromEntries(['disco_checkpoint_path', 'disco_cutlass_path'].filter(key => Object.hasOwn(initialValues, key)).map(key => [key, initialValues[key]])),
                    disco_experiment: discoExperiment,
                    disco_effort: discoEffort,
                    disco_num_inference_seeds: discoInferenceSeeds,
                    disco_seeds: discoSeeds || undefined,
                    disco_use_deepspeed_evo_attention: discoAttention,
                    disco_input_json_path: discoInputJson || undefined,
                    disco_ligand_sdf: ligandSdf || undefined,
                    disco_ligand_name: ligandName || undefined,
                    disco_na_sequence: nucleicSequence || undefined,
                }),
            },
        });

    const submitAlternative = async () => {
        setError(null);
        if (!jobName.trim()) {
            setError('Job name is required.');
            return;
        }
        if (numDesigns < 1 || !targetLengths.trim()) {
            setError('Provide at least one design and one target length.');
            return;
        }
        if (!DE_NOVO_TASK_OPTIONS[backend].some((option) => option.value === designTask)) {
            setError(`The ${backend === 'disco' ? 'DISCO' : 'La-Proteina'} backend does not support the selected design task.`);
            return;
        }
        if (backend === 'laproteina' && designTask === 'motif_scaffolding' && !(motifTaskName.trim() || (motifPdb.trim() && motifContig.trim()))) {
            setError('La-Proteina motif scaffolding requires an upstream motif task or a motif PDB with a contig string.');
            return;
        }
        if (backend === 'disco' && designTask === 'custom_json' && !discoInputJson.trim()) {
            setError('DISCO custom native JSON design requires an input JSON file.');
            return;
        }
        if (backend === 'disco' && designTask === 'ligand_conditioned' && !ligandSdf.trim()) {
            setError('DISCO ligand-conditioned design requires a ligand SDF.');
            return;
        }
        if (backend === 'disco' && (designTask === 'dna_conditioned' || designTask === 'rna_conditioned') && !nucleicSequence.trim()) {
            setError('DISCO DNA/RNA-conditioned design requires a nucleic-acid sequence.');
            return;
        }

        await submitMutation.mutateAsync(buildAlternativeWorkflowRequest());
    };

    const buildDeNovoWorkflowRequest = () => ({
            name: jobName.trim(),
            model_id: 'protein_modification_experimental',
            mode: 'de_novo_design',
            params: {
                generator: 'rfd3',
                generation_mode: 'unconditional_monomer',
                min_length: minLength,
                max_length: maxLength,
                num_designs: numDesigns,
                seed,
                dump_trajectories: dumpTrajectories,
            },
        });

    const submitDeNovo = async () => {
        setError(null);
        if (!jobName.trim()) {
            setError('Job name is required.');
            return;
        }
        if (minLength < 40 || minLength > 600 || maxLength < 40 || maxLength > 600 || minLength > maxLength) {
            setError('Lengths must be between 40 and 600, with minimum no greater than maximum.');
            return;
        }
        if (!Number.isInteger(numDesigns) || numDesigns < 1) {
            setError('Number of designs must be at least 1.');
            return;
        }
        if (!Number.isInteger(seed) || seed < 0) {
            setError('Seed must be a non-negative integer.');
            return;
        }

        await submitMutation.mutateAsync(buildDeNovoWorkflowRequest());
    };

    const workflowRequest = generator === 'rfd3'
        ? (jobName.trim() && minLength >= 40 && maxLength <= 600 && minLength <= maxLength && Number.isInteger(numDesigns) && numDesigns >= 1 && Number.isInteger(seed) && seed >= 0 ? buildDeNovoWorkflowRequest() : null)
        : (jobName.trim() && numDesigns >= 1 && targetLengths.trim() && DE_NOVO_TASK_OPTIONS[backend].some(option => option.value === designTask) && (backend !== 'laproteina' || designTask !== 'motif_scaffolding' || motifTaskName.trim() || (motifPdb.trim() && motifContig.trim())) && (backend !== 'disco' || (designTask !== 'custom_json' || discoInputJson.trim()) && (designTask !== 'ligand_conditioned' || ligandSdf.trim()) && (!['dna_conditioned', 'rna_conditioned'].includes(designTask) || nucleicSequence.trim())) ? buildAlternativeWorkflowRequest() : null);
    const workflowRequestJson = JSON.stringify(workflowRequest);
    const stableWorkflowRequest = useMemo(() => JSON.parse(workflowRequestJson) as typeof workflowRequest, [workflowRequestJson]);
    const isMotif = backend === 'laproteina' && designTask === 'motif_scaffolding';
    const contextTask = ['ligand_conditioned', 'dna_conditioned', 'rna_conditioned', 'custom_json'].includes(designTask)
        ? designTask as 'ligand_conditioned' | 'dna_conditioned' | 'rna_conditioned' | 'custom_json' : null;
    const engineName = generator === 'rfd3' ? 'RFD3' : backend === 'laproteina' ? 'La-Proteina' : 'DISCO';
    const updateContext = (patch: { ligandPath?: string; ligandName?: string; nativeJsonPath?: string; sequence?: string }) => {
        if (patch.ligandPath !== undefined) setLigandSdf(patch.ligandPath);
        if (patch.ligandName !== undefined) setLigandName(patch.ligandName);
        if (patch.nativeJsonPath !== undefined) setDiscoInputJson(patch.nativeJsonPath);
        if (patch.sequence !== undefined) setNucleicSequence(patch.sequence);
    };
    const countHelp = generator === 'rfd3' ? 'Exact number of native RFD3 candidates.'
        : backend === 'laproteina' ? isMotif ? 'Motif sampling uses the requested design count.'
            : 'Samples per length controls generation. Requested design count is retained, not a combined sampling total.'
        : 'Exact seeds override inference seed count. Requested design count is retained, not the native output total.';
    const nativePresets = isMotif
        ? [['motif_idx_aa', 'Motif indexed all-atom'], ['motif_idx_tip', 'Motif indexed tip-atoms'], ['motif_uidx_aa', 'Motif unindexed all-atom'], ['motif_uidx_tip', 'Motif unindexed tip-atoms']]
        : [['ucond_tri', 'Unconditional (triangular)'], ['ucond_notri', 'Unconditional'], ['ucond_notri_long', 'Unconditional long']];
    const generationControls = <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {generator === 'rfd3' ? <>
            <label className={labelClass}>Minimum length
                <input className={fieldClass} type="number" min={40} max={600} value={minLength} onChange={event => setMinLength(Number(event.target.value))} />
            </label>
            <label className={labelClass}>Maximum length
                <input className={fieldClass} type="number" min={40} max={600} value={maxLength} onChange={event => setMaxLength(Number(event.target.value))} />
            </label>
            <label className={labelClass}>Number of designs
                <input className={fieldClass} type="number" min={1} value={numDesigns} onChange={event => setNumDesigns(Number(event.target.value))} />
            </label>
        </> : <>
            <label className={labelClass}>Target lengths
                <input className={fieldClass} value={targetLengths} onChange={event => setTargetLengths(event.target.value)} placeholder="100,150,200" />
            </label>
            <label className={labelClass}>Requested design count
                <input className={fieldClass} type="number" min={1} max={512} value={numDesigns} onChange={event => setNumDesigns(Number(event.target.value))} />
            </label>
            {backend === 'laproteina' && <label className={labelClass}>Samples per length
                <input className={fieldClass} type="number" min={1} max={512} value={laproteinaSamples} onChange={event => setLaproteinaSamples(Number(event.target.value))} />
                {isMotif && <span className="block text-xs text-[var(--text-secondary)]">Retained for unconditional sampling; not used for motifs.</span>}
            </label>}
        </>}
    </div>;
    return (
        <div className="w-full space-y-5 text-[var(--text-primary)]" data-bms-protein-generation-workflow={generator}>
            <ProteinDesignSections label="Generation sections" sections={[inputSection, 'Sampling']} active={section} onChange={setSection} />
            {/* Section navigation is presentation only. Source tools and viewers stay mounted. */}
            <div hidden={section !== inputSection} className="space-y-5">
                {designTask === 'unconditional' ? <ProteinDesignPanel title="Generation" description="Generate new protein candidates without a source structure.">
                    {generationControls}
                    <p className="text-xs text-[var(--text-secondary)]">{countHelp}</p>
                </ProteinDesignPanel> : isMotif ? <>
                    <LaProteinaMotifInput path={motifPdb} contig={motifContig} segmentOrder={motifSegmentOrder}
                        initialInspection={motifInspection} onInspectionChange={setMotifInspection} onChange={patch => {
                        if (patch.path !== undefined) setMotifPdb(patch.path);
                        if (patch.contig !== undefined) setMotifContig(patch.contig);
                        if (patch.segmentOrder !== undefined) setMotifSegmentOrder(patch.segmentOrder);
                    }} />
                    <ProteinDesignPanel title="Motif conditioning">
                        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                            <label className={labelClass}>Atom selection
                                <select className={fieldClass} value={motifAtomSelectionMode} onChange={event => setMotifAtomSelectionMode(event.target.value)}>
                                    <option value="all_atom">All atom</option><option value="tip_atoms">Tip atoms</option>
                                    <option value="backbone">Backbone</option><option value="sidechain">Sidechain</option>
                                    <option value="ca_only">Cα only</option><option value="random">Random</option>
                                </select>
                            </label>
                            <label className={labelClass}>Motif minimum length
                                <input className={fieldClass} type="number" min={1} max={2000} value={motifMinLength} onChange={event => setMotifMinLength(event.target.value)} placeholder="From target lengths" />
                            </label>
                            <label className={labelClass}>Motif maximum length
                                <input className={fieldClass} type="number" min={1} max={2000} value={motifMaxLength} onChange={event => setMotifMaxLength(event.target.value)} placeholder="From target lengths" />
                            </label>
                        </div>
                        <details className="rounded-lg border p-3" style={themedInsetStyle}>
                            <summary className="cursor-pointer text-sm font-medium">Named upstream motif task</summary>
                            <label className={`${labelClass} mt-3 block`}>Upstream motif task
                                <input className={fieldClass} value={motifTaskName} onChange={event => setMotifTaskName(event.target.value)} placeholder="La-Proteina motif_dict key" />
                            </label>
                            <p className="mt-2 text-xs text-[var(--text-secondary)]">Alternative to a custom motif. A supplied motif structure and contig take precedence.</p>
                        </details>
                    </ProteinDesignPanel>
                </> : contextTask && <ProteinDesignPanel title={contextTask === 'custom_json' ? 'Native design input' : 'Molecular context'}
                    description={contextTask === 'dna_conditioned' ? 'DNA context; preparation includes the reverse-complement strand.'
                        : contextTask === 'rna_conditioned' ? 'RNA context; preparation uses the supplied single strand.'
                            : contextTask === 'ligand_conditioned' ? 'Use the ligand’s existing SDF structure.' : 'Use an existing native DISCO input document.'}>
                    <DeNovoContextInput task={contextTask} ligandPath={ligandSdf} ligandName={ligandName} nativeJsonPath={discoInputJson} sequence={nucleicSequence} onChange={updateContext} />
                </ProteinDesignPanel>}
            </div>
            <div hidden={section !== 'Sampling'} className="space-y-5">
                {designTask !== 'unconditional' && <ProteinDesignPanel title="Generation" description={countHelp}>{generationControls}</ProteinDesignPanel>}
                <ProteinDesignPanel title={`${engineName} sampling`}>
                    {generator === 'rfd3' ? <div className="grid gap-4 sm:grid-cols-2">
                        <label className={labelClass}>Seed
                            <input className={fieldClass} type="number" min={0} value={seed} onChange={event => setSeed(Number(event.target.value))} />
                        </label>
                        <label className="flex items-center gap-3 text-sm font-medium">
                            <input type="checkbox" checked={dumpTrajectories} onChange={event => setDumpTrajectories(event.target.checked)} />Dump trajectories
                        </label>
                    </div> : backend === 'laproteina' ? <div className="grid gap-4 sm:grid-cols-2">
                        <label className={labelClass}>Preset
                            <select className={fieldClass} value={laproteinaPreset} onChange={event => setLaproteinaPreset(event.target.value)}>
                                {!nativePresets.some(([value]) => value === laproteinaPreset) && <option value={laproteinaPreset}>
                                    {laproteinaPreset} · native fallback: {isMotif ? 'motif indexed all-atom' : 'unconditional triangular'}
                                </option>}
                                {nativePresets.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                            </select>
                        </label>
                        <label className={labelClass}>Sampling steps
                            <input className={fieldClass} type="number" min={50} max={2000} value={laproteinaSteps} onChange={event => setLaproteinaSteps(Number(event.target.value))} />
                        </label>
                    </div> : <>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <label className={labelClass}>DISCO experiment
                                <select className={fieldClass} value={discoExperiment} onChange={event => setDiscoExperiment(event.target.value)}>
                                    <option value="designable">Designable</option><option value="diverse">Diverse</option>
                                </select>
                            </label>
                            <label className={labelClass}>Inference effort
                                <select className={fieldClass} value={discoEffort} onChange={event => setDiscoEffort(event.target.value)}>
                                    <option value="fast">Fast</option><option value="max">Max</option>
                                    {!['fast', 'max'].includes(discoEffort) && <option value={discoEffort}>{discoEffort} (saved)</option>}
                                </select>
                            </label>
                            <label className={labelClass}>Inference seed count
                                <input className={fieldClass} type="number" min={1} max={1024} value={discoInferenceSeeds} onChange={event => setDiscoInferenceSeeds(Number(event.target.value))} />
                            </label>
                            <label className={labelClass}>Exact seeds
                                <input className={fieldClass} value={discoSeeds} onChange={event => setDiscoSeeds(event.target.value)} placeholder="Optional comma-separated integers" />
                            </label>
                        </div>
                        {['dna_conditioned', 'rna_conditioned', 'ligand_conditioned'].includes(designTask) && <p className="text-sm text-[var(--text-secondary)]">For conditional generation, preparation uses Max inference effort even when Fast is requested.</p>}
                        <details className="rounded-lg border p-3" style={themedInsetStyle}>
                            <summary className="cursor-pointer text-sm font-medium">Advanced inference</summary>
                            <label className="mt-3 flex items-center gap-3 text-sm">
                                <input type="checkbox" checked={discoAttention} onChange={event => setDiscoAttention(event.target.checked)} />DeepSpeed Evoformer attention
                            </label>
                            <p className="mt-2 text-xs text-[var(--text-secondary)]">Uses the managed CUTLASS runtime when enabled.</p>
                        </details>
                        {designTask !== 'custom_json' && <details open={Boolean(discoInputJson)} className="rounded-lg border p-3" style={themedInsetStyle}>
                            <summary className="cursor-pointer text-sm font-medium">Native input override{discoInputJson ? ' · active' : ''}</summary>
                            <p className="my-3 text-sm text-[var(--text-secondary)]">A selected native JSON overrides the compiled task inputs. Clear it to use this workflow’s molecular context.</p>
                            <DeNovoContextInput task="custom_json" ligandPath={ligandSdf} ligandName={ligandName} nativeJsonPath={discoInputJson} sequence={nucleicSequence} onChange={updateContext} />
                        </details>}
                    </>}
                </ProteinDesignPanel>
                <ModelDocumentationLinks topics={generator === 'rfd3' ? ['rfdiffusion'] : backend === 'laproteina' ? ['laproteina'] : ['disco']} compact />
            </div>
            <details className="space-y-3 rounded-xl border p-4" style={themedInsetStyle}>
                <summary className="cursor-pointer text-sm font-medium">Run details</summary>
                <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
                    <div><dt className="text-[var(--text-secondary)]">Engine</dt><dd>{engineName}</dd></div>
                    <div><dt className="text-[var(--text-secondary)]">Goal</dt><dd>{designTask === 'unconditional' ? 'Explore new folds' : DE_NOVO_TASK_OPTIONS[backend].find(task => task.value === designTask)?.label}</dd></div>
                    <div><dt className="text-[var(--text-secondary)]">Lengths</dt><dd>{generator === 'rfd3' ? `${minLength}–${maxLength}` : targetLengths || 'Not specified'}</dd></div>
                    {discoInputJson && generator === 'disco' && <div><dt className="text-[var(--text-secondary)]">Native input override</dt><dd className="break-all">{discoInputJson}</dd></div>}
                </dl>
            </details>
            <ProteinDesignRun jobName={jobName} onJobNameChange={setJobName} pending={submitMutation.isPending}
                onSubmit={() => { void (generator === 'rfd3' ? submitDeNovo() : submitAlternative()); }} submitLabel="Generate candidates">
                {runDetails}
                <ExecutionTargetPicker workflowRequest={stableWorkflowRequest} preloadSelection={DE_NOVO_PRELOAD_SELECTION} />
                {error && <div role="alert" className="text-sm text-[var(--text-primary)]">{error}</div>}
            </ProteinDesignRun>
        </div>
    );
}
