import { DocumentCaptureContext, DocumentCaptureRegistry, DeNovoDraftPreparationContext, type CapturedAnnotatedDocument } from '../lib/deNovoDocumentCapture';
import { DeNovoDocumentEditors, DeNovoDocumentEditorPanels, DeNovoEditorLayout } from './DeNovoDocumentEditors';
import { materializeLaProteinaStudioCapture } from '../lib/laProteinaStudioCapture';
import { LaProteinaEvaluation } from './LaProteinaEvaluation';
import { DeNovoNativeGenerationSettings } from './DeNovoNativeGenerationSettings';
import { OtherDeNovoSettings, retainedRfd3Draft, rfd3ExecutionKeys, useRfd3GenerationBounds } from './OtherDeNovoSettings';
import { DiscoJobsInput } from './DiscoJobsInput';
import { Rfd3SelectionWorkbench } from './Rfd3SelectionWorkbench';
import { Rfd3RegionTask } from './Rfd3RegionTask';
import type { Rfd3SourceSelection } from '../lib/rfd3SourceSelection';
import type { Rfd3SelectionObservations, Rfd3InspectionSource } from '../lib/rfd3SelectionObservations';
import { projectRfd3SourceSelection } from '../lib/rfd3SourceSelection';
import { NativeSourceInput } from './DeNovoNativeSources';
import { ResequencePanel } from './ResequencePanel';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { DE_NOVO_PRELOAD_SELECTION } from './dashboard/IndependentProvisionPanel';
import { useContext, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { api, completeCurrentLaunchContext, submitJob, type NativeGenerationSourceBinding, type StructureMaterialization, type Job } from '../lib/api';
import { ModelDocumentationLinks } from './ModelDocumentationLinks';
import { ModelReferenceCard } from './ModelReferenceCard';
import ShapeBlueprintTemplate from './ShapeBlueprintTemplate';
import { ProteinDesignSections, ProteinDesignPanel, ProteinDesignRun, themedInsetStyle } from './ProteinDesignWorkflow';
import { LaProteinaMotifInput, type LaProteinaMotifInspection } from './LaProteinaMotifInput';
import { DeNovoContextInput } from './DeNovoContextInput';
import { PreparedGeometryContext, type PreparedGeometryDocument } from '../lib/preparedGeometry';
import { MolecularConstructionWorkbench } from './MolecularConstructionWorkbench';
import type { MolecularConstructionDraft } from '../lib/molecularConstruction';
import { GeneralSequenceDesignSettings } from './GeneralSequenceDesignSettings';
import { DeNovoRunScope } from './DeNovoRunScope';
import { generalSequenceRequest, hydrateGeneralSequenceDesign } from '../lib/generalSequenceDesign';
import { DE_NOVO_MODIFICATION_MODE_CARDS, type ModificationMode } from './proteinModificationModes';

export interface DeNovoNavigationState {
    modification_mode: string;
    generator?: string;
    design_task?: string;
    native_mode?: string;
}

interface ProteinModificationTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, unknown>;
    onDraftChange?: (draft: Record<string, unknown>) => void;
    onNavigationChange?: (state: DeNovoNavigationState) => void;
    navigationState?: DeNovoNavigationState;
    runDetails?: ReactNode;
    launchContextId?: string | null;
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
        { value: 'custom_json', label: 'Explicit jobs / native JSON' },
    ],
    laproteina: [
        { value: 'unconditional', label: 'Unconditional' },
        { value: 'motif_scaffolding', label: 'Motif scaffolding' },
    ],
};

const fieldClass = 'w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]';
const labelClass = 'space-y-1 text-sm font-medium text-[var(--text-primary)]';

const initialValue = (values: Record<string, unknown> | undefined, key: string): unknown =>
    values && Object.hasOwn(values, key) ? values[key] : values?.[`pcad_${key}`];
const settingsObject = (value: unknown): Record<string, unknown> => {
    if (typeof value === 'string') { try { value = JSON.parse(value); } catch { return {}; } }
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
};

const initialString = (values: Record<string, unknown> | undefined, key: string, fallback: string): string => {
    const value = initialValue(values, key);
    return typeof value === 'string' ? value : fallback;
};

const initialNumber = (values: Record<string, unknown> | undefined, key: string, fallback: number | ''): number | '' => {
    const saved = initialValue(values, key);
    if (saved === '') return '';
    if (saved === undefined) return fallback;
    const value = Number(saved);
    return Number.isFinite(value) ? value : fallback;
};
const editedNumber = (value: string): number | '' => value === '' ? '' : Number(value);

const resolveNavigation = (values?: Record<string, unknown>): DeNovoNavigationState => {
    const rawMode = values?.modification_mode;
    const modification_mode = rawMode === 'rfd3_local_redesign'
        ? 'rfd3_iteration'
        : rawMode === 'rfd3_iteration' || rawMode === 'region_redesign' || rawMode === 'resequence' || rawMode === 'shape_blueprint' ? rawMode : 'de_novo_design';
    const native = values?.generator === 'rfd4_proteina' || values?.generator === 'complexa' || values?.generator === 'laproteina';
    const nativeMode = native ? { native_mode: typeof values?.native_mode === 'string' ? values.native_mode : 'generate' } : {};
    if (modification_mode !== 'de_novo_design') return { modification_mode, ...(values?.generator === 'rfd4_proteina' ? { generator: 'rfd4_proteina', design_task: 'refine', ...nativeMode } : {}) };
    const engine = values?.generator ?? values?.backend;
    // Historical RFD3 drafts often predate an explicit generator field.
    const legacyRfd3 = values?.generation_mode === 'unconditional_monomer';
    return {
        modification_mode,
        ...nativeMode,
        generator: typeof engine === 'string' ? engine : legacyRfd3 ? 'rfd3' : 'rfd4_proteina',
        design_task: typeof values?.design_task === 'string' ? values.design_task : 'unconditional',
    };
};
const draftKey = (state: DeNovoNavigationState) => state.modification_mode === 'de_novo_design'
    ? `${state.modification_mode}:${state.generator}:${state.design_task}${state.generator === 'rfd4_proteina' || state.generator === 'complexa' || state.generator === 'laproteina' && state.native_mode === 'evaluate' ? `:${state.native_mode ?? 'generate'}:v1` : ''}` : state.generator === 'rfd4_proteina' ? `${state.modification_mode}:rfd4_proteina:${state.native_mode ?? 'generate'}:v1` : state.modification_mode;

export function ProteinModificationTemplate({
    onBack, initialValues, onDraftChange, onNavigationChange, navigationState, runDetails, onOpenTemplateManager, launchContextId,
}: ProteinModificationTemplateProps) {
    const [navigation, setNavigation] = useState(() => resolveNavigation({ ...navigationState, ...initialValues }));
    // Suite-owned authoring, not a generator override or engine-specific draft.
    // A deliberate Template/Project load remounts this parent; child reports must
    // never overwrite the latest shared recipe with their hydration snapshot.
    const [construction, setConstruction] = useState<MolecularConstructionDraft | undefined>(() =>
        initialValues?.molecular_construction && typeof initialValues.molecular_construction === 'object'
            ? structuredClone(initialValues.molecular_construction as MolecularConstructionDraft) : undefined);
    const constructionRef = useRef(construction);
    const constructionGeneration = useRef(0);
    const preparedRef = useRef<Record<string, PreparedGeometryDocument>>(structuredClone((initialValues?.prepared_geometry_documents ?? {}) as Record<string, PreparedGeometryDocument>));
    useEffect(() => () => { constructionGeneration.current++; }, []);
    const [constructionOpen, setConstructionOpen] = useState(false);

    const drafts = useRef<Record<string, Record<string, unknown>>>(
        initialValues?.de_novo_drafts && typeof initialValues.de_novo_drafts === 'object'
            ? { ...initialValues.de_novo_drafts as Record<string, Record<string, unknown>> } : {},
    );
    const activeKey = draftKey(navigation);
    const initialized = useRef(false);
    if (!initialized.current) {
        const { de_novo_drafts: _drafts, molecular_construction: _construction, prepared_geometry_documents: _prepared, ...activeValues } = initialValues ?? {};
        for (const engine of ['laproteina', 'disco']) {
            if (navigation.generator === engine) continue;
            const legacy = Object.fromEntries(Object.entries(activeValues).filter(([key]) => key.startsWith(`${engine}_`)));
            const key = draftKey({ modification_mode: 'de_novo_design', generator: engine, design_task: 'unconditional' });
            if (Object.keys(legacy).length && !drafts.current[key]) drafts.current[key] = legacy;
        }
        drafts.current[activeKey] = { ...drafts.current[activeKey], ...activeValues };
        initialized.current = true;
    }
    const callbacks = useRef({ onDraftChange, onNavigationChange });
    const savedGeneration = initialValues?.suite_last_generation;
    const lastGeneration = useRef<{ generator: string; design_task: string; native_mode?: string }>(
        savedGeneration && typeof savedGeneration === 'object' && !Array.isArray(savedGeneration) && typeof (savedGeneration as Record<string, unknown>).generator === 'string'
            ? savedGeneration as { generator: string; design_task: string; native_mode?: string }
            : { generator: navigation.generator ?? 'rfd4_proteina', design_task: navigation.design_task ?? 'unconditional', native_mode: navigation.native_mode },
    );
    const lastTaskByEngine = useRef<Record<string, string>>({
        ...(initialValues?.suite_last_tasks && typeof initialValues.suite_last_tasks === 'object' && !Array.isArray(initialValues.suite_last_tasks) ? initialValues.suite_last_tasks as Record<string, string> : {}),
        ...(navigation.modification_mode === 'de_novo_design' ? { [navigation.generator ?? 'rfd4_proteina']: navigation.design_task ?? 'unconditional' } : {}),
    });
    const lastModeByEngine = useRef<Record<string, string>>({
        ...(initialValues?.suite_last_modes && typeof initialValues.suite_last_modes === 'object' && !Array.isArray(initialValues.suite_last_modes) ? initialValues.suite_last_modes as Record<string, string> : {}),
        ...(navigation.generator && navigation.native_mode ? { [navigation.generator]: navigation.native_mode } : {}),
    });
    const [suiteView, setSuiteView] = useState<'simple' | 'advanced'>(initialValues?.suite_view === 'advanced' ? 'advanced' : 'simple');
    const activeRequest = useRef<Record<string, unknown>>({});
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
        const { native_job_request, molecular_construction: _childConstruction, prepared_geometry_documents: _childPrepared, ...editor } = draft;
        activeRequest.current = Object.hasOwn(draft, 'native_job_request') ? { native_job_request } : {};
        drafts.current[activeKey] = { ...editor, modification_mode: savedMode };
        // Complexa derives its operation inside the request; its navigation key
        // stays stable while that operation changes.
        if (navigation.generator && navigation.generator !== 'complexa' && typeof draft.native_mode === 'string') lastModeByEngine.current[navigation.generator] = draft.native_mode;
        callbacks.current.onDraftChange?.({ ...editor, ...activeRequest.current, ...(constructionRef.current ? { molecular_construction: constructionRef.current } : {}), ...(Object.keys(preparedRef.current).length ? { prepared_geometry_documents: preparedRef.current } : {}), ...navigation, suite_view: suiteView, suite_last_generation: { ...lastGeneration.current }, suite_last_tasks: { ...lastTaskByEngine.current }, suite_last_modes: { ...lastModeByEngine.current }, ...(navigation.generator === 'complexa' && typeof draft.native_mode === 'string' ? { native_mode: draft.native_mode } : {}), modification_mode: savedMode, de_novo_drafts: Object.fromEntries(Object.entries(drafts.current).filter(([key]) => key !== activeKey)) });
    }, [activeKey, navigation.modification_mode, navigation.generator, navigation.design_task, navigation.native_mode, savedMode, suiteView]);
    const changeConstruction = (next: MolecularConstructionDraft) => {
        constructionGeneration.current++;
        constructionRef.current = structuredClone(next);
        setConstruction(constructionRef.current);
        // Report synchronously through the existing Save/Template/Project owner,
        // retaining the child's exact request rather than recompiling science.
        reportDraft({ ...drafts.current[activeKey], ...activeRequest.current });
    };
    const changeNavigation = (state: DeNovoNavigationState) => {
        const next = resolveNavigation({ ...state });
        // Only shared intent seeds an untouched engine draft. Never translate
        // length ranges, structural roles or model-native conditions by guessing.
        const nextKey = draftKey(next);
        if (!Object.hasOwn(drafts.current, nextKey)) {
            const current = drafts.current[activeKey] ?? {};
            drafts.current[nextKey] = next.generator === 'rfd3' && navigation.generator === 'rfd3' && next.modification_mode === 'de_novo_design' && navigation.modification_mode === 'de_novo_design' ? { ...current } : Object.fromEntries(
                (['rfd3', 'disco', 'laproteina'].includes(next.generator ?? '') && ['rfd3', 'disco', 'laproteina'].includes(navigation.generator ?? '') ? ['job_name', 'num_designs'] : ['job_name']).filter(key => Object.hasOwn(current, key)).map(key => [key, current[key]]),
            );
        }
        if (next.modification_mode === 'de_novo_design' && next.generator) lastTaskByEngine.current[next.generator] = next.design_task ?? 'unconditional';
        if (next.generator && next.native_mode) lastModeByEngine.current[next.generator] = next.native_mode;
        setNavigation(next);
        callbacks.current.onNavigationChange?.(next);
    };
    const mode: ModificationMode = validatedRedesign ? 'rfd3_iteration' : navigation.modification_mode as ModificationMode;
    const generator = navigation.generator ?? (mode === 'de_novo_design' ? 'rfd4_proteina' : 'rfd3');
    const task = navigation.design_task ?? 'unconditional';
    const lastRedesign = useRef(validatedRedesign || drafts.current.region_redesign ? 'region_redesign' : 'rfd3_iteration');
    const chooseMode = (next: ModificationMode) => changeNavigation(next === 'de_novo_design'
        ? { modification_mode: next, ...lastGeneration.current }
        : { modification_mode: next === 'rfd3_iteration' ? lastRedesign.current : next, ...(next === 'rfd3_iteration' && generator === 'rfd4_proteina' ? { generator } : {}) });
    if (mode === 'de_novo_design') lastGeneration.current = { generator, design_task: task, native_mode: navigation.native_mode };
    if (mode === 'rfd3_iteration') lastRedesign.current = navigation.modification_mode;
    // Keep the hydration object stable while a child reports changes.
    const childValues = useRef<{ key: string; values: Record<string, unknown> } | null>(null);
    if (childValues.current?.key !== activeKey) childValues.current = { key: activeKey, values: drafts.current[activeKey] ?? {} };
    const nativeModeControl = (generator === 'rfd4_proteina' || generator === 'laproteina') && mode !== 'shape_blueprint' ? <label className={labelClass}>Operation
        <select aria-label="Native mode" className={fieldClass} value={navigation.native_mode ?? 'generate'} onChange={event => changeNavigation({ ...navigation, native_mode: event.target.value })}>
            <option value="generate">Generate</option><option value="evaluate">Evaluate</option>
        </select>
    </label> : null;
    const constructionPanel = (inContextRow: boolean) => <details className={`rounded-lg border border-[var(--border-primary)] p-3 ${inContextRow ? (constructionOpen ? 'col-span-full' : 'self-end') : ''}`}
                data-molecular-construction-panel open={constructionOpen} onToggle={event => setConstructionOpen(event.currentTarget.open)}>
                <summary className="cursor-pointer text-sm font-medium">DNA / RNA construction{construction ? ' · retained recipe' : ''}</summary>
                {constructionOpen && <div className="mt-3 min-w-0">
                    <p className="mb-3 text-sm">One shared molecular recipe across engines. Construction is optional and does not change a generator's inputs until you explicitly use its prepared document.</p>
                    <MolecularConstructionWorkbench initialDraft={construction} onDraftChange={changeConstruction} />
                </div>}
            </details>;
    return (
        <PreparedGeometryContext.Provider value={{ current: construction, generation: () => constructionGeneration.current, retain: document => {
            // Coordinate equality is not recipe/provenance equality: retain each adoption.
            const key = `${document.sha256}:${Object.keys(preparedRef.current).length}`;
            preparedRef.current = { ...preparedRef.current, [key]: structuredClone(document) };
            reportDraft({ ...drafts.current[activeKey], ...activeRequest.current });
        } }}>
        <div className="space-y-3 text-[var(--text-primary)]" data-bms-de-novo-form="complete">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2" data-de-novo-header-controls>
                <button type="button" onClick={onBack} className="rounded-lg bg-[var(--bg-tertiary)] px-3 py-1.5 text-sm">Back</button>
                <h2 className="text-lg font-semibold">Protein design</h2>
                <nav aria-label="Design task" className="flex flex-wrap gap-2">
                {DE_NOVO_MODIFICATION_MODE_CARDS.map(card => <button type="button" key={card.id}
                    aria-pressed={mode === card.id} onClick={() => chooseMode(card.id)}
                    className={`rounded-lg px-3 py-1.5 text-sm ${mode === card.id ? 'bg-[var(--bg-tertiary)] text-[var(--accent-primary)]' : ''}`}>{card.label}</button>)}
            </nav>
                <div role="group" aria-label="Editor detail" className="flex gap-2">
                {(['simple', 'advanced'] as const).map(view => <button type="button" key={view} aria-pressed={suiteView === view} onClick={() => setSuiteView(view)} className="rounded-lg bg-[var(--bg-tertiary)] px-3 py-1.5 text-sm">{view === 'simple' ? 'Simple' : 'Advanced'}</button>)}
            </div>
                <div className="ml-auto">{mode !== 'shape_blueprint' && <ModelReferenceCard model={generator} />}</div>
            </div>
            {mode !== 'shape_blueprint' && mode !== 'de_novo_design' && mode !== 'resequence' && constructionPanel(false)}
            {mode === 'de_novo_design' ? <>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-3" data-de-novo-context>
                    {!['rfd4_proteina', 'complexa'].includes(generator) && <label className={labelClass}>Goal
                        <select className={fieldClass} value={task} onChange={event => {
                            const design_task = event.target.value;
                            changeNavigation({ ...navigation, design_task });
                        }}>
                            <option value="unconditional">Explore new folds</option>
                            {generator !== 'laproteina' && <><option value="dna_conditioned">{generator === 'rfd3' ? 'Configure DNA context' : 'Design around DNA'}</option>
                            <option value="rna_conditioned">{generator === 'rfd3' ? 'Configure RNA context' : 'Design around RNA'}</option>
                            <option value="ligand_conditioned">{generator === 'rfd3' ? 'Configure small-molecule context' : 'Design around a small molecule'}</option></>}
                            {generator === 'laproteina' && <option value="motif_scaffolding">Scaffold a motif</option>}
                            {generator === 'disco' && <option value="custom_json">Use a native input document</option>}
                            {!['unconditional', ...(generator !== 'laproteina' ? ['dna_conditioned', 'rna_conditioned', 'ligand_conditioned'] : []), ...(generator === 'laproteina' ? ['motif_scaffolding'] : []), ...(generator === 'disco' ? ['custom_json'] : [])].includes(task) && <option value={task}>Saved goal ({task})</option>}
                        </select>
                    </label>}
                    <label className={labelClass}>Engine
                        <select className={fieldClass} value={generator} onChange={event => changeNavigation({ modification_mode: mode, generator: event.target.value, design_task: lastTaskByEngine.current[event.target.value] ?? 'unconditional', native_mode: lastModeByEngine.current[event.target.value] })}>
                            {['rfd4_proteina', 'rfd3', 'disco', 'laproteina', 'complexa'].map(engine => <option key={engine} value={engine}>{engine === 'rfd3' ? 'RFD3' : engine === 'disco' ? 'DISCO' : engine === 'laproteina' ? 'La-Proteina' : engine === 'rfd4_proteina' ? 'RFD4-Proteina' : engine === 'complexa' ? 'Complexa' : engine}</option>)}
                        </select>
                    </label>
                    {nativeModeControl}
                    {constructionPanel(true)}
                </div>
                {generator === 'laproteina' && navigation.native_mode === 'evaluate' ? <LaProteinaEvaluation key={activeKey} initialValues={childValues.current.values} runDetails={runDetails} onDraftChange={reportDraft} />
                    : generator === 'rfd4_proteina' || generator === 'complexa' ? <DeNovoNativeGenerationSettings key={activeKey} model={generator}
                    mode={navigation.native_mode ?? 'generate'}
                    initialValues={childValues.current.values} runDetails={runDetails} onDraftChange={reportDraft} suiteView={suiteView} onSuiteViewChange={setSuiteView} />
                    : ['rfd3', 'disco', 'laproteina'].includes(generator) ? <GenerationEditor key={activeKey} initialValues={childValues.current.values} generator={generator}
                    designTask={task} runDetails={runDetails} onDraftChange={reportDraft} suiteView={suiteView} onSuiteViewChange={setSuiteView} /> : <p role="alert">No native editor registered for {generator}. Retained settings have not been translated to another engine.</p>}
            </> : mode === 'resequence' ? <ResequencePanel />
            : mode === 'rfd3_iteration' ? <><div className="grid grid-cols-2 gap-3">{nativeModeControl}<label className={labelClass}>Engine<select className={fieldClass} value={generator === 'rfd4_proteina' ? generator : 'rfd3'} onChange={event => changeNavigation({ modification_mode: savedMode, generator: event.target.value })}><option value="rfd3">RFD3</option><option value="rfd4_proteina">RFD4-Proteina</option></select></label></div>{generator === 'rfd4_proteina' ? <DeNovoNativeGenerationSettings key={activeKey} model="rfd4_proteina" mode={navigation.native_mode ?? 'generate'} initialValues={childValues.current.values} runDetails={runDetails} onDraftChange={reportDraft} suiteView={suiteView} onSuiteViewChange={setSuiteView} /> : <GenerationEditor key={activeKey} initialValues={childValues.current.values.input_structure ? { generation_task: 'partial_diffusion', ...childValues.current.values } : childValues.current.values} generator="rfd3"
                designTask="unconditional" runDetails={runDetails} onDraftChange={reportDraft} suiteView={suiteView} onSuiteViewChange={setSuiteView} />}</>
                : <ShapeBlueprintTemplate launchContextId={launchContextId} key={activeKey} embedded initialValues={childValues.current.values} runDetails={runDetails} onDraftChange={reportDraft} />}
            {onOpenTemplateManager && <button type="button" className="rounded-lg border border-[var(--border-primary)] px-4 py-2 text-sm"
                onClick={() => onOpenTemplateManager({
                    currentParams: { ...drafts.current[activeKey], ...activeRequest.current, ...(constructionRef.current ? { molecular_construction: constructionRef.current } : {}), ...(Object.keys(preparedRef.current).length ? { prepared_geometry_documents: preparedRef.current } : {}), ...navigation, suite_view: suiteView, suite_last_generation: { ...lastGeneration.current }, suite_last_tasks: { ...lastTaskByEngine.current }, suite_last_modes: { ...lastModeByEngine.current }, modification_mode: savedMode, de_novo_drafts: Object.fromEntries(Object.entries(drafts.current).filter(([key]) => key !== activeKey)) },
                    currentModelId: generator === 'rfd4_proteina' || generator === 'complexa' || generator === 'laproteina' && navigation.native_mode === 'evaluate' ? generator : 'protein_modification_experimental',
                    baseTemplateId: 'protein_modification_experimental',
                    currentMode: generator === 'rfd4_proteina' || generator === 'complexa' || generator === 'laproteina' && navigation.native_mode === 'evaluate' ? navigation.native_mode ?? 'generate' : mode === 'rfd3_iteration' ? validatedRedesign ? 'region_redesign' : 'de_novo_design' : mode,
                })}>Template Manager</button>}
        </div>
        </PreparedGeometryContext.Provider>
    );
}

function GenerationEditor({ initialValues, generator, designTask, onDraftChange, runDetails, suiteView, onSuiteViewChange }: {
    initialValues: Record<string, unknown>;
    generator: string;
    designTask: string;
    runDetails?: ReactNode;
    suiteView: 'simple' | 'advanced';
    onSuiteViewChange: (view: 'simple' | 'advanced') => void;
    onDraftChange: (draft: Record<string, unknown>) => void;
}) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [documentCaptures] = useState(() => new DocumentCaptureRegistry());
    const preparation = useContext(DeNovoDraftPreparationContext);
    const [molecularDocuments, setMolecularDocuments] = useState<Record<string, CapturedAnnotatedDocument>>(() => settingsObject(initialValues.molecular_documents) as Record<string, CapturedAnnotatedDocument>);
    const backend: DeNovoBackend = generator === 'laproteina' ? 'laproteina' : 'disco';
    const inputSection = designTask === 'unconditional' ? 'Design' : 'Source and regions';
    const [section, setSection] = useState(inputSection);
    const manualSection = useRef(false);
    useEffect(() => { if (manualSection.current) { manualSection.current = false; return; } setSection(suiteView === 'advanced' ? 'Sampling' : inputSection); }, [suiteView, inputSection]);
    const [jobName, setJobName] = useState(initialString(initialValues, 'job_name', 'protein_modification'));
    const [numDesigns, setNumDesigns] = useState(initialNumber(initialValues, 'num_designs', 8));
    const [minLength, setMinLength] = useState(initialNumber(initialValues, 'min_length', 100));
    const [maxLength, setMaxLength] = useState(initialNumber(initialValues, 'max_length', 200));
    const [lengthMode, setLengthMode] = useState<'exact' | 'range'>(initialValues.length_mode === 'exact' ? 'exact' : 'range');
    const [exactLength, setExactLength] = useState(initialNumber(initialValues, 'exact_length', typeof initialValues.min_length === 'number' && initialValues.min_length === initialValues.max_length ? initialValues.min_length : ''));
    const requestMinLength = lengthMode === 'exact' ? exactLength : minLength;
    const requestMaxLength = lengthMode === 'exact' ? exactLength : maxLength;
    const [seed, setSeed] = useState(initialNumber(initialValues, 'seed', 0));
    const [dumpTrajectories, setDumpTrajectories] = useState(initialValues?.dump_trajectories === true);
    const [alternativeLengthMode, setAlternativeLengthMode] = useState<'exact' | 'list'>(initialValues.alternative_length_mode === 'exact' ? 'exact' : 'list');
    const [targetLengthList, setTargetLengthList] = useState(initialString(initialValues, 'target_length_list', initialString(initialValues, 'target_lengths', '')));
    const [targetLengthExact, setTargetLengthExact] = useState(initialString(initialValues, 'target_length_exact', ''));
    const targetLengths = alternativeLengthMode === 'exact' ? targetLengthExact : targetLengthList;
    const [laproteinaPreset, setLaproteinaPreset] = useState(initialString(initialValues, 'laproteina_preset', designTask === 'motif_scaffolding' ? 'motif_idx_aa' : 'ucond_tri'));
    const [laproteinaSamples, setLaproteinaSamples] = useState(initialNumber(initialValues, 'laproteina_samples_per_length', 8));
    const [laproteinaSteps, setLaproteinaSteps] = useState(initialNumber(initialValues, 'laproteina_num_steps', 400));
    const [motifTaskName, setMotifTaskName] = useState(initialString(initialValues, 'laproteina_motif_task_name', ''));
    const [motifPdb, setMotifPdb] = useState(initialString(initialValues, 'laproteina_motif_pdb', ''));
    const [motifInspection, setMotifInspection] = useState(initialValues.laproteina_motif_inspection as LaProteinaMotifInspection | undefined);
    const [motifAtomSpec, setMotifAtomSpec] = useState<string | null | undefined>(initialValue(initialValues, 'laproteina_motif_atom_spec') as string | null | undefined);
    const [motifContig, setMotifContig] = useState(initialString(initialValues, 'laproteina_contig_string', ''));
    const [motifSegmentOrder, setMotifSegmentOrder] = useState(initialString(initialValues, 'laproteina_segment_order', ''));
    const [motifAtomSelectionMode, setMotifAtomSelectionMode] = useState(initialString(initialValues, 'laproteina_atom_selection_mode', 'all_atom'));
    const [motifMinLength, setMotifMinLength] = useState<string | number | null>(initialValue(initialValues, 'laproteina_motif_min_length') === null ? null : initialValue(initialValues, 'laproteina_motif_min_length') as string | number ?? '');
    const [motifMaxLength, setMotifMaxLength] = useState<string | number | null>(initialValue(initialValues, 'laproteina_motif_max_length') === null ? null : initialValue(initialValues, 'laproteina_motif_max_length') as string | number ?? '');
    const [discoExperiment, setDiscoExperiment] = useState(initialString(initialValues, 'disco_experiment', 'designable'));
    const [discoEffort, setDiscoEffort] = useState(initialString(initialValues, 'disco_effort', 'fast'));
    const [discoInferenceSeeds, setDiscoInferenceSeeds] = useState<number | '' | null>(initialValue(initialValues, 'disco_num_inference_seeds') === null ? null : initialNumber(initialValues, 'disco_num_inference_seeds', 8));
    // Without an explicit seed count, DISCO produces the requested number of designs per input
    // (one seed is one sample); a saved or edited count, including native null, always wins.
    const [discoSeedCountSet, setDiscoSeedCountSet] = useState(initialValue(initialValues, 'disco_num_inference_seeds') !== undefined);
    const discoSeedCount: number | '' | null = discoSeedCountSet ? discoInferenceSeeds : numDesigns;
    const [discoSeeds, setDiscoSeeds] = useState<string | number[] | null>(() => {
        const saved = initialValue(initialValues, 'disco_seeds');
        return saved === null || Array.isArray(saved) ? saved as number[] | null : typeof saved === 'string' ? saved : '';
    });
    const [discoSeedText, setDiscoSeedText] = useState<string>();
    const [discoAttention, setDiscoAttention] = useState(initialValue(initialValues, 'disco_use_deepspeed_evo_attention') === true);
    const [discoInputJson, setDiscoInputJson] = useState(initialString(initialValues, 'disco_input_json_path', ''));
    const [ligandSdf, setLigandSdf] = useState(initialString(initialValues, 'disco_ligand_sdf', ''));
    const [ligandName, setLigandName] = useState(initialString(initialValues, 'disco_ligand_name', ''));
    const [nucleicSequence, setNucleicSequence] = useState(initialString(initialValues, 'disco_na_sequence', ''));
    const [nativeControls, setNativeControls] = useState<Record<string, unknown>>(() => Object.fromEntries(
        ['laproteina_settings', 'disco_settings', 'pcad_laproteina_settings_json', 'pcad_disco_settings_json', 'pcad_disco_jobs_json', 'pcad_disco_jobs_source_path'].filter(key => Object.hasOwn(initialValues, key)).map(key => { let value = initialValues[key]; if (key.endsWith('_json') && typeof value === 'string') { try { value = JSON.parse(value); } catch { /* Retain invalid saved input for explicit repair. */ } } return [key, value]; })));
    const nativeOverrideKey = `pcad_${backend}_settings_json`;
    const historicalOverrideKey = `${backend}_settings`;
    const newestOverrides = settingsObject(nativeControls[nativeOverrideKey]);
    const historicalOverrides = settingsObject(nativeControls[historicalOverrideKey]);
    // Match the native presence-based projection: flat < historical < pcad JSON.
    // Keep each layer separate so editing/reset never discards a shadowed draft.
    const nativeOverrides = { ...historicalOverrides, ...newestOverrides };
    const overrideOwner = (key: string) => Object.hasOwn(newestOverrides, key) ? nativeOverrideKey
        : Object.hasOwn(historicalOverrides, key) ? historicalOverrideKey : undefined;
    // Named motifs inherit their native dictionary, not the old flat motif
    // defaults (the projection deliberately omits those). Do not present a
    // shadowed flat value as effective when its last native override is reset.
    const namedMotif = backend === 'laproteina' && Boolean((nativeOverrides.motif_task_name ?? motifTaskName) && !(nativeOverrides.motif_pdb ?? motifPdb));
    const inheritsNamedMotif = (key: string) => namedMotif && ['contig_string', 'segment_order', 'motif_atom_spec', 'atom_selection_mode', 'motif_min_length', 'motif_max_length'].includes(key);
    const effectiveOverride = (key: string, fallback: unknown) => Object.hasOwn(nativeOverrides, key) ? nativeOverrides[key] : inheritsNamedMotif(key) ? undefined : fallback;
    const setEffectiveOverride = (key: string, value: unknown, setPrimary: () => void) => {
        const owner = overrideOwner(key);
        if (!owner) { setPrimary(); return; }
        setNativeControls(current => ({ ...current, [owner]: { ...settingsObject(current[owner]), [key]: value } }));
    };
    const resetOverride = (key: string) => {
        if (key === 'seeds') setDiscoSeedText(undefined);
        setNativeControls(current => {
            const owner = overrideOwner(key);
            if (!owner) return current;
            const next = { ...settingsObject(current[owner]) }; delete next[key];
            return { ...current, [owner]: next };
        });
    };
    const overrideNote = (key: string) => {
        const owner = overrideOwner(key);
        if (!owner) return null;
        const resetLabel = owner === nativeOverrideKey && Object.hasOwn(historicalOverrides, key) ? 'Use retained override' : inheritsNamedMotif(key) ? 'Use named motif profile' : 'Use primary value';
        return <span className="block text-xs text-[var(--text-secondary)]">Native override ({key}) from {owner}: {JSON.stringify(nativeOverrides[key])}. <button type="button" aria-label={`${resetLabel} for ${key}`} onClick={() => resetOverride(key)}>{resetLabel}</button></span>;
    };
    const [rfd3Settings, setRfd3Settings] = useState(() => retainedRfd3Draft(initialValues));
    const laCatalog = useQuery({ queryKey: ['other-denovo-settings', 'laproteina', designTask], enabled: generator === 'laproteina' && designTask === 'motif_scaffolding',
        queryFn: async () => (await api.get<{ native_task_catalog?: { tasks: Record<string, Record<string, unknown>> } }>('/api/models/protein_modification_experimental/generation-settings', { params: { mode: 'de_novo_design', generator: 'laproteina', task: designTask } })).data,
        staleTime: 60_000, retry: false });
    const rfd3ConditionUndo = useRef<{ present: boolean; value: unknown } | undefined>(undefined);
    const [rfd3Picks, setRfd3Picks] = useState<Rfd3SourceSelection>();
    const [rfd3Observations, setRfd3Observations] = useState<Rfd3SelectionObservations>(() => settingsObject(initialValues.rfd3_selection_observations) as Rfd3SelectionObservations);
    const [rfd3SourceInspection, setRfd3SourceInspection] = useState<{ receipt: StructureMaterialization; path: string; model?: number }>();
    const rfd3Source = typeof rfd3Settings.input_structure === 'string' && rfd3Settings.input_structure ? {
        path: rfd3Settings.input_structure, output_format: 'native' as const,
        ...(typeof rfd3Settings.source_model === 'number' ? { model_number: rfd3Settings.source_model } : {}),
    } : undefined;
    // The exact binding the picker just acquired, so the integrated viewer does not re-read a source it already holds.
    const [rfd3Acquired, setRfd3Acquired] = useState<Extract<NativeGenerationSourceBinding, { path?: string }> & { model_number?: number }>();
    const retainedRfd3Source = molecularDocuments.input?.source;
    const rfd3Binding = rfd3Source && rfd3Acquired && rfd3Acquired.path === rfd3Source.path && rfd3Acquired.model_number === rfd3Source.model_number ? rfd3Acquired
        : rfd3Source && retainedRfd3Source?.path === rfd3Source.path && retainedRfd3Source.model_number === rfd3Source.model_number ? retainedRfd3Source : rfd3Source;
    // Source reading starts only once the optional section is opened; closed/source-free forms issue no extra requests.
    const [rfd3SourceOpen, setRfd3SourceOpen] = useState(false);
    const rfd3Visual = rfd3Picks ? { ...rfd3Picks, inputPath: rfd3Source?.path ?? '', sourceModel: rfd3Source?.model_number, sourceMapping: rfd3Settings.source_mapping as string | undefined } : undefined;
    const rfd3Selected = rfd3Visual && !projectRfd3SourceSelection(rfd3Visual).issue ? [...rfd3Visual.identities] : [];
    const rfd3InspectionSource: Rfd3InspectionSource | undefined = rfd3Source && rfd3SourceInspection?.path === rfd3Source.path && rfd3SourceInspection.model === rfd3Source.model_number ? {
        inputPath: rfd3Source.path, sourceModel: rfd3Source.model_number, sourceMapping: rfd3Settings.source_mapping as string | undefined,
        source: { documentId: rfd3SourceInspection.receipt.path, source_positions: rfd3SourceInspection.receipt.source_positions, model_number: rfd3SourceInspection.receipt.model_number },
        retainedBinding: { receipt: rfd3SourceInspection.receipt, inputPath: rfd3Source.path, sourceModel: rfd3Source.model_number },
    } : undefined;
    useEffect(() => { setRfd3Picks(undefined); }, [rfd3Source?.path, rfd3Source?.model_number]);
    const clearRfd3Source = () => setRfd3Settings(previous => {
        const next = { ...previous }; delete next.input_structure; delete next.source_model; delete next.source_mapping; delete next.generation_task; delete next.redesign; return next;
    });
    const rfd3Bounds = useRfd3GenerationBounds(generator === 'rfd3');
    const rfd3LengthsInvalid = requestMinLength === '' || requestMaxLength === '' || !Number.isInteger(requestMinLength) || !Number.isInteger(requestMaxLength) || Number(requestMinLength) > Number(requestMaxLength) ||
        [requestMinLength, requestMaxLength].some((value, index) => {
            const bound = rfd3Bounds?.[index === 0 ? 'min_length' : 'max_length'];
            return (bound?.minimum != null && Number(value) < bound.minimum) || (bound?.maximum != null && Number(value) > bound.maximum);
        });
    const hasNativeJobs = Object.hasOwn(nativeControls, 'pcad_disco_jobs_json');
    const effectiveLaMotif = { motif_task_name: motifTaskName, motif_pdb: motifPdb, contig_string: motifContig, ...nativeOverrides };
    const hasEffectiveLaMotif = Boolean(effectiveLaMotif.motif_task_name || (effectiveLaMotif.motif_pdb && effectiveLaMotif.contig_string));
    const authorLaMotif = (key: string, value: unknown) => setNativeControls(current => ({ ...current, pcad_laproteina_settings_json: { ...(current.pcad_laproteina_settings_json && typeof current.pcad_laproteina_settings_json === 'object' && !Array.isArray(current.pcad_laproteina_settings_json) ? current.pcad_laproteina_settings_json as Record<string, unknown> : {}), [key]: value } }));
    const retainedAlternativeParams = Object.fromEntries(Object.entries(initialValues).filter(([key]) =>
        (key.startsWith(`${backend}_`) || key.startsWith(`pcad_${backend}_`)) && !['laproteina_settings', 'disco_settings', 'pcad_laproteina_settings_json', 'pcad_disco_settings_json', 'pcad_disco_jobs_json', 'pcad_disco_jobs_source_path'].includes(key) && key !== 'laproteina_motif_inspection'));
    const [error, setError] = useState<string | null>(null);
    const [sequenceDesign, setSequenceDesign] = useState(() => hydrateGeneralSequenceDesign(initialValues));
    const [sequenceOmitted, setSequenceOmitted] = useState(() => initialValues.sequence_design_omitted === true);
    const [sequenceNull, setSequenceNull] = useState(() => initialValues.sequence_design === null || initialValues.sequence_design_null === true);

    const draftValues: Record<string, unknown> = {
        ...retainedAlternativeParams,
        ...(Object.keys(molecularDocuments).length ? { molecular_documents: molecularDocuments } : {}),
        ...sequenceDesign,
        ...(sequenceOmitted ? { sequence_design_omitted: true } : {}),
        ...(sequenceNull ? { sequence_design_null: true } : {}),
        ...nativeControls,
        ...rfd3Settings,
        rfd3_selection_observations: rfd3Observations,
        ...Object.fromEntries(['laproteina_checkpoint_dir', 'laproteina_data_path', 'disco_checkpoint_path', 'disco_cutlass_path'].filter(key => Object.hasOwn(initialValues, key)).map(key => [key, initialValues[key]])),
        job_name: jobName,
        num_designs: numDesigns,
        min_length: minLength,
        max_length: maxLength,
        length_mode: lengthMode,
        exact_length: exactLength,
        seed: seed,
        dump_trajectories: dumpTrajectories,
        target_lengths: targetLengths,
        alternative_length_mode: alternativeLengthMode,
        target_length_list: targetLengthList,
        target_length_exact: targetLengthExact,
        laproteina_preset: laproteinaPreset,
        laproteina_samples_per_length: laproteinaSamples,
        laproteina_num_steps: laproteinaSteps,
        laproteina_motif_task_name: motifTaskName,
        laproteina_motif_pdb: motifPdb,
        laproteina_motif_inspection: motifInspection,
        laproteina_motif_atom_spec: motifAtomSpec,
        laproteina_contig_string: motifContig,
        laproteina_segment_order: motifSegmentOrder,
        laproteina_atom_selection_mode: motifAtomSelectionMode,
        laproteina_motif_min_length: motifMinLength,
        laproteina_motif_max_length: motifMaxLength,
        disco_experiment: discoExperiment,
        disco_effort: discoEffort,
        // Only an explicit count is saved; otherwise a reopened draft keeps following the requested count.
        disco_num_inference_seeds: discoSeedCountSet ? discoInferenceSeeds : undefined,
        disco_seeds: discoSeeds,
        disco_use_deepspeed_evo_attention: discoAttention,
        disco_input_json_path: discoInputJson,
        disco_ligand_sdf: ligandSdf,
        disco_ligand_name: ligandName,
        disco_na_sequence: nucleicSequence,
    };
    // Retain unrecognized editor imports, without reintroducing cleared fields
    // owned by this child or placing extras in the native request.
    const clearable = new Set<string>([...rfd3ExecutionKeys, 'conditioning', 'generation_task', 'redesign', 'input_structure', 'source_mapping', 'source_model', 'generation_mode', 'native_job_request', 'de_novo_drafts', 'generator', 'backend', 'design_task', 'modification_mode', 'native_mode', 'suite_view', 'suite_last_tasks', 'suite_last_modes', 'laproteina_settings', 'disco_settings', 'sequence_design_null', 'sequence_design_omitted', 'pcad_laproteina_settings_json', 'pcad_disco_settings_json', 'pcad_disco_jobs_json', 'pcad_disco_jobs_source_path']);
    const extras = Object.fromEntries(Object.entries(initialValues).filter(([key]) => !Object.hasOwn(draftValues, key) && !clearable.has(key)));
    const draftJson = JSON.stringify({ ...extras, ...draftValues });
    const submitMutation = useMutation({
        mutationFn: async () => submitJob(await prepareCurrent.current()),
        onSuccess: async (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate(await completeCurrentLaunchContext(response.data) ?? '/');
        },
        onError: (err: Error) => setError(err.message || 'Failed to submit protein modification job'),
    });

    const buildAlternativeWorkflowRequest = () => {
        const request = {
            ...(sequenceOmitted ? {} : generalSequenceRequest(sequenceDesign)),
            ...(sequenceNull ? { sequence_design: null } : {}),
            name: jobName.trim(),
            model_id: 'protein_modification_experimental',
            mode: 'de_novo_design',
            params: {
                ...retainedAlternativeParams,
                ...Object.fromEntries(Object.entries(nativeControls).filter(([key]) => (key.startsWith(`pcad_${backend}_`) || key === `${backend}_settings`))),
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
                    laproteina_motif_atom_spec: motifAtomSpec,
                    laproteina_contig_string: motifContig || undefined,
                    laproteina_segment_order: motifSegmentOrder || undefined,
                    laproteina_atom_selection_mode: motifAtomSelectionMode,
                    laproteina_motif_min_length: motifMinLength === '' ? undefined : motifMinLength,
                    laproteina_motif_max_length: motifMaxLength === '' ? undefined : motifMaxLength,
                } : {
                    ...Object.fromEntries(['disco_checkpoint_path', 'disco_cutlass_path'].filter(key => Object.hasOwn(initialValues, key)).map(key => [key, initialValues[key]])),
                    disco_experiment: discoExperiment,
                    disco_effort: discoEffort,
                    disco_num_inference_seeds: discoSeedCount,
                    disco_seeds: discoSeeds === '' ? undefined : discoSeeds,
                    disco_use_deepspeed_evo_attention: discoAttention,
                    disco_input_json_path: discoInputJson || undefined,
                    disco_ligand_sdf: ligandSdf || undefined,
                    disco_ligand_name: ligandName || undefined,
                    disco_na_sequence: nucleicSequence || undefined,
                }),
            },
        };
        // A cleared optional primary must not resurrect a retained pcad_ flat
        // alias after JSON drops its undefined canonical key. Keep the original
        // aliases in editor drafts; suppress only shadowed wire aliases here.
        const params = request.params as Record<string, unknown>;
        for (const [key, value] of Object.entries(params)) {
            const alias = `pcad_${key}`;
            if (value === undefined && Object.hasOwn(params, alias) && params[alias] !== '') delete params[alias];
        }
        return request;
    };

    const submitAlternative = async () => {
        setError(null);
        if (!jobName.trim()) {
            setError('Job name is required.');
            return;
        }
        if (Number(numDesigns) < 1 || !targetLengths.trim()) {
            setError('Provide at least one design and one target length.');
            return;
        }
        if (!DE_NOVO_TASK_OPTIONS[backend].some((option) => option.value === designTask)) {
            setError(`The ${backend === 'disco' ? 'DISCO' : 'La-Proteina'} backend does not support the selected design task.`);
            return;
        }
        if (backend === 'laproteina' && designTask === 'motif_scaffolding' && !hasEffectiveLaMotif) {
            setError('La-Proteina motif scaffolding requires an upstream motif task or a motif PDB with a contig string.');
            return;
        }
        if (backend === 'disco' && !hasNativeJobs && designTask === 'custom_json' && !discoInputJson.trim()) {
            setError('DISCO custom native JSON design requires an input JSON file.');
            return;
        }
        if (backend === 'disco' && !hasNativeJobs && designTask === 'ligand_conditioned' && !ligandSdf.trim()) {
            setError('DISCO ligand-conditioned design requires a ligand SDF.');
            return;
        }
        if (backend === 'disco' && !hasNativeJobs && (designTask === 'dna_conditioned' || designTask === 'rna_conditioned') && !nucleicSequence.trim()) {
            setError('DISCO DNA/RNA-conditioned design requires a nucleic-acid sequence.');
            return;
        }

        try { await submitMutation.mutateAsync(); }
        catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    };

    const buildDeNovoWorkflowRequest = () => ({
            ...(sequenceOmitted ? {} : generalSequenceRequest(sequenceDesign)),
            ...(sequenceNull ? { sequence_design: null } : {}),
            name: jobName.trim(),
            model_id: 'protein_modification_experimental',
            mode: 'de_novo_design',
            params: {
                generation_mode: 'unconditional_monomer',
                ...rfd3Settings,
                generator: 'rfd3',
                min_length: requestMinLength,
                max_length: requestMaxLength,
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
        if (rfd3LengthsInvalid) {
            setError(`Lengths must follow current public admission (${rfd3Bounds?.min_length.minimum ?? 'not reported'}–${rfd3Bounds?.max_length.maximum ?? 'not reported'}), with minimum no greater than maximum.`);
            return;
        }
        if (!Number.isInteger(numDesigns) || Number(numDesigns) < 1) {
            setError('Number of designs must be at least 1.');
            return;
        }
        if (!Number.isInteger(seed) || Number(seed) < 0) {
            setError('Seed must be a non-negative integer.');
            return;
        }

        try { await submitMutation.mutateAsync(); }
        catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    };

    const workflowRequest = generator === 'rfd3'
        ? (jobName.trim() && !rfd3LengthsInvalid && Number.isInteger(numDesigns) && Number(numDesigns) >= 1 && Number.isInteger(seed) && Number(seed) >= 0 ? buildDeNovoWorkflowRequest() : null)
        : (jobName.trim() && Number(numDesigns) >= 1 && targetLengths.trim() && DE_NOVO_TASK_OPTIONS[backend].some(option => option.value === designTask) && (backend !== 'laproteina' || designTask !== 'motif_scaffolding' || hasEffectiveLaMotif) && (backend !== 'disco' || hasNativeJobs || (designTask !== 'custom_json' || discoInputJson.trim()) && (designTask !== 'ligand_conditioned' || ligandSdf.trim()) && (!['dna_conditioned', 'rna_conditioned'].includes(designTask) || nucleicSequence.trim())) ? buildAlternativeWorkflowRequest() : null);
    const workflowRequestJson = JSON.stringify(workflowRequest);
    useEffect(() => {
        const values = JSON.parse(draftJson) as Record<string, unknown>;
        // Migrate historical flat drafts without multiplying inactive engine defaults.
        for (const key of Object.keys(values)) {
            if ((key.startsWith('laproteina_') && generator !== 'laproteina') || (key.startsWith('disco_') && generator !== 'disco')) delete values[key];
        }
        onDraftChange({ ...values, native_job_request: JSON.parse(workflowRequestJson) });
    }, [draftJson, workflowRequestJson, generator, onDraftChange]);
    const currentCaptureDraft = useRef({ draftJson, workflowRequestJson, onDraftChange, generation: 0 });
    if (currentCaptureDraft.current.draftJson !== draftJson || currentCaptureDraft.current.workflowRequestJson !== workflowRequestJson) {
        currentCaptureDraft.current = { draftJson, workflowRequestJson, onDraftChange, generation: currentCaptureDraft.current.generation + 1 };
    } else currentCaptureDraft.current.onDraftChange = onDraftChange;
    const captureLifetime = useRef(0);
    useEffect(() => () => { captureLifetime.current++; }, []);
    const prepareCurrent = useRef<() => Promise<Partial<Job>>>(async () => ({}));
    const receiveCaptured = async (captured: Record<string, CapturedAnnotatedDocument>) => {
        const snapshot = currentCaptureDraft.current;
        const lifetime = captureLifetime.current;
        const documentGeneration = documentCaptures.getSnapshot();
        const exact = JSON.parse(snapshot.draftJson);
        const request = JSON.parse(snapshot.workflowRequestJson);
        let motifPath: string | undefined;
        if (generator === 'laproteina' && captured.motif) {
            const overrides = { ...settingsObject(exact.laproteina_settings), ...settingsObject(exact.pcad_laproteina_settings_json) };
            const contig = Object.hasOwn(overrides, 'contig_string') ? overrides.contig_string : exact.laproteina_contig_string;
            motifPath = await materializeLaProteinaStudioCapture(captured.motif, String(contig ?? ''));
        }
        if (lifetime !== captureLifetime.current || snapshot.generation !== currentCaptureDraft.current.generation || documentGeneration !== documentCaptures.getSnapshot()) {
            throw new Error('The source or native settings changed during capture. Your editor is retained; capture the current draft again.');
        }
        if (generator === 'rfd3' && captured.input) {
            const source = captured.input.source;
            exact.input_structure = source.path;
            if (source.model_number === undefined) delete exact.source_model; else exact.source_model = source.model_number;
            if (request) {
                request.params.input_structure = source.path;
                if (source.model_number === undefined) delete request.params.source_model; else request.params.source_model = source.model_number;
            }
            setRfd3Acquired(source);
            setRfd3Settings(previous => {
                const next: Record<string, unknown> = { ...previous, input_structure: source.path };
                if (source.model_number === undefined) delete next.source_model; else next.source_model = source.model_number;
                return next;
            });
        }
        if (motifPath !== undefined) {
            // Explicit Studio adoption replaces the effective motif path only;
            // all other native/manual/task overrides retain their precedence.
            exact.pcad_laproteina_settings_json = { ...settingsObject(exact.pcad_laproteina_settings_json), motif_pdb: motifPath };
            if (request) request.params.pcad_laproteina_settings_json = exact.pcad_laproteina_settings_json;
            captured = { ...captured, motif: { ...captured.motif, provenance: { ...captured.motif.provenance, native_motif_path: motifPath } } };
            setNativeControls(previous => ({ ...previous, pcad_laproteina_settings_json: exact.pcad_laproteina_settings_json }));
        }
        if (Object.keys(captured).length) {
            exact.molecular_documents = { ...settingsObject(exact.molecular_documents), ...captured };
            setMolecularDocuments(exact.molecular_documents);
        }
        for (const key of Object.keys(exact)) {
            if ((key.startsWith('laproteina_') && generator !== 'laproteina') || (key.startsWith('disco_') && generator !== 'disco')) delete exact[key];
        }
        snapshot.onDraftChange({ ...exact, native_job_request: request });
        return request as Partial<Job>;
    };
    const receiveCurrent = useRef(receiveCaptured); receiveCurrent.current = receiveCaptured;
    prepareCurrent.current = async () => receiveCurrent.current(await documentCaptures.capture());
    useEffect(() => {
        if (!preparation) return;
        const prepare = async () => { await prepareCurrent.current(); };
        preparation.current = prepare;
        return () => { if (preparation.current === prepare) preparation.current = null; };
    }, [preparation]);
    const effectiveMotifPath = effectiveOverride('motif_pdb', motifPdb);
    const motifStudioSource = typeof effectiveMotifPath === 'string' && effectiveMotifPath
        ? molecularDocuments.motif?.provenance?.native_motif_path === effectiveMotifPath
            ? molecularDocuments.motif.source : { path: effectiveMotifPath, output_format: 'native' as const }
        : undefined;
    const studioSources: Record<string, NativeGenerationSourceBinding> | undefined = generator === 'rfd3' ? (rfd3Binding ? { input: rfd3Binding } : undefined)
        : generator === 'laproteina' && designTask === 'motif_scaffolding' && motifStudioSource ? { motif: motifStudioSource } : undefined;
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
        : 'Inference seed count follows the requested design count unless you set it; exact seeds override both. Each seed produces one design per input.';
    const nativePresets = isMotif
        ? [['motif_idx_aa', 'Motif indexed all-atom'], ['motif_idx_tip', 'Motif indexed tip-atoms'], ['motif_uidx_aa', 'Motif unindexed all-atom'], ['motif_uidx_tip', 'Motif unindexed tip-atoms']]
        : [['ucond_tri', 'Unconditional (triangular)'], ['ucond_notri', 'Unconditional'], ['ucond_notri_long', 'Unconditional long']];
    const presetControl = <label className={labelClass}>Preset
        <select className={fieldClass} aria-label="La-Proteina preset" value={laproteinaPreset} onChange={event => setLaproteinaPreset(event.target.value)}>
            {!nativePresets.some(([value]) => value === laproteinaPreset) && <option value={laproteinaPreset}>{laproteinaPreset} · saved, incompatible with this task</option>}
            {nativePresets.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
    </label>;
    const generationControls = <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {generator === 'rfd3' ? <>
            <label className={labelClass}>Length selection
                <select className={fieldClass} value={lengthMode} onChange={event => setLengthMode(event.target.value as 'exact' | 'range')}><option value="range">Length range</option><option value="exact">Exact length</option></select>
            </label>
            {lengthMode === 'exact' ? <label className={labelClass}>Exact length
                <input className={fieldClass} type="number" min={rfd3Bounds?.min_length.minimum} max={rfd3Bounds?.min_length.maximum} value={exactLength} onChange={event => setExactLength(editedNumber(event.target.value))} />
            </label> : <>
            <label className={labelClass}>Minimum length
                <input className={fieldClass} type="number" min={rfd3Bounds?.min_length.minimum} max={rfd3Bounds?.min_length.maximum} value={minLength} onChange={event => setMinLength(editedNumber(event.target.value))} />
            </label>
            <label className={labelClass}>Maximum length
                <input className={fieldClass} type="number" min={rfd3Bounds?.max_length.minimum} max={rfd3Bounds?.max_length.maximum} value={maxLength} onChange={event => setMaxLength(editedNumber(event.target.value))} />
            </label></>}
            <label className={labelClass}>Number of designs
                <input className={fieldClass} type="number" min={1} value={numDesigns} onChange={event => setNumDesigns(editedNumber(event.target.value))} />
            </label>
        </> : <>
            <label className={labelClass}>Length selection
                <select className={fieldClass} value={alternativeLengthMode} onChange={event => setAlternativeLengthMode(event.target.value as 'exact' | 'list')}><option value="list">Discrete lengths</option><option value="exact">Exact length</option></select>
            </label>
            {alternativeLengthMode === 'exact' ? <label className={labelClass}>Exact target length
                <input className={fieldClass} type="number" value={targetLengthExact} onChange={event => setTargetLengthExact(event.target.value)} />
                <span>Required; enter one length.</span>
            </label> : <label className={labelClass}>Target lengths
                <input className={fieldClass} value={targetLengthList} onChange={event => setTargetLengthList(event.target.value)} placeholder="Comma-separated lengths" />
                <span>Required; enter a discrete comma-separated list, not a range.</span>
            </label>}
            <label className={labelClass}>Requested design count
                <input className={fieldClass} type="number" min={1} max={512} value={numDesigns} onChange={event => setNumDesigns(editedNumber(event.target.value))} />
            </label>
            {backend === 'laproteina' && !isMotif && <label className={labelClass}>Samples per length
                <input className={fieldClass} type="number" min={1} max={512} value={laproteinaSamples} onChange={event => setLaproteinaSamples(editedNumber(event.target.value))} />
                {isMotif && <span className="block text-xs text-[var(--text-secondary)]">Retained for unconditional sampling; not used for motifs.</span>}
            </label>}
        </>}
    </div>;
    return (
        <DocumentCaptureContext.Provider value={documentCaptures}><DeNovoDocumentEditors registry={documentCaptures} sources={studioSources} molecularDocuments={molecularDocuments}
            onAdopt={(slot, document) => { void receiveCurrent.current({ [slot]: document }).catch(cause => setError(cause instanceof Error ? cause.message : String(cause))); }}>
        <div className="w-full space-y-5 text-[var(--text-primary)]" data-bms-protein-generation-workflow={generator}>
            <DeNovoEditorLayout><div className="min-w-0 space-y-5">
            <ProteinDesignSections label="Generation sections" sections={[inputSection, 'Sampling', 'Sequence Design']} active={section} onChange={next => { setSection(next); const view = next === 'Sampling' ? 'advanced' : 'simple'; manualSection.current = suiteView !== view; onSuiteViewChange(view); }} />
            {/* Section navigation is presentation only. Source tools and viewers stay mounted. */}
            <div hidden={section !== inputSection} className="space-y-5">
            {generator === 'rfd3' && <p className="text-xs">Choose a context document, then edit coordinates in AtomWorks or assign RFD3 conditions below. Studio’s RFD4 annotations are not RFD3 masks.</p>}
            {generator === 'disco' && <p className="text-xs">Use Native jobs for DISCO entities, sequences, modifications and bonds. Studio scaffold coordinates and RFD4 conditions are not DISCO inputs.</p>}
                {designTask === 'unconditional' || generator === 'rfd3' ? <ProteinDesignPanel title="Generation" description={generator === 'rfd3' && designTask !== 'unconditional' ? 'This goal does not automatically add context or conditions. Supply an input structure and configure the desired conditions under Sampling; without them the request remains source-free.' : 'Generate new protein candidates.'}>
                    {generationControls}
                    <p className="text-xs text-[var(--text-secondary)]">{countHelp}</p>
                    {generator === 'rfd3' && <button type="button" aria-expanded={rfd3SourceOpen} onClick={() => setRfd3SourceOpen(!rfd3SourceOpen)}>Choose / edit RFD3 context</button>}
                    {generator === 'rfd3' && <details open={rfd3SourceOpen} onToggle={event => setRfd3SourceOpen(event.currentTarget.open)}><summary>Input structure (optional)</summary>
                        {rfd3SourceOpen && <>
                        <NativeSourceInput integrated label="RFD3 input structure"
                            selectedIdentities={rfd3Selected}
                            onSelection={(_slot, receipt, selection) => setRfd3Picks({ identities: selection.identities.flatMap(identity => 'first' in identity && 'second' in identity ? [identity.first, identity.second] : [identity]) as Rfd3SourceSelection['identities'],
                                source: { documentId: receipt.path, source_positions: receipt.source_positions, model_number: receipt.model_number, model_numbers: receipt.model_numbers },
                                inputPath: rfd3Source?.path ?? '', sourceModel: rfd3Source?.model_number,
                                retainedBinding: { receipt, inputPath: rfd3Source?.path ?? '', sourceModel: rfd3Source?.model_number } })} value={rfd3Source ? 'input' : ''} bindings={rfd3Binding ? { input: rfd3Binding } : undefined}
                            onChange={slot => { if (slot === '') clearRfd3Source(); }}
                            onAcquire={source => {
                                if (!('kind' in source)) setRfd3Acquired(source);
                                if (!('kind' in source)) setRfd3Settings(previous => {
                                    const next: Record<string, unknown> = { ...previous, input_structure: source.path };
                                    if (source.model_number == null) delete next.source_model; else next.source_model = source.model_number;
                                    return next;
                                });
                                return 'input';
                            }}
                            inspection={rfd3SourceInspection?.path === rfd3Source?.path && rfd3SourceInspection?.model === rfd3Source?.model_number ? rfd3SourceInspection?.receipt : undefined}
                            onInspection={(_slot, receipt, source) => { if (!('kind' in source)) setRfd3SourceInspection({ receipt, path: source.path ?? '', model: source.model_number }); }} />
                        <label>Source identity mapping<select aria-label="RFD3 source identity mapping" value={String(rfd3Settings.source_mapping ?? '')} onChange={event => {
                            const selected = event.target.value;
                            setRfd3Settings(previous => { const next = { ...previous }; if (selected) next.source_mapping = selected; else delete next.source_mapping; return next; });
                        }}><option value="">Manual / historical source mapping</option><option value="bms.rfd3.source-map.v1">Exact author-to-native source map</option>
                            {rfd3Settings.source_mapping && rfd3Settings.source_mapping !== 'bms.rfd3.source-map.v1' ? <option value={String(rfd3Settings.source_mapping)}>{String(rfd3Settings.source_mapping)} · retained historical mapping</option> : null}
                        </select></label>
                        {rfd3Source && <Rfd3RegionTask settings={rfd3Settings} onChange={setRfd3Settings} />}
                        <Rfd3SelectionWorkbench key={JSON.stringify([rfd3Source?.path, rfd3Source?.model_number])}
                            value={settingsObject(rfd3Settings.conditioning)} visual={rfd3Visual}
                            retainedObservations={rfd3Observations} onObservationsChange={setRfd3Observations} inspectionSource={rfd3InspectionSource}
                            onChange={(conditioning, action) => {
                                const before = action === 'undo' ? rfd3ConditionUndo.current : undefined;
                                rfd3ConditionUndo.current = before ? undefined : { present: Object.hasOwn(rfd3Settings, 'conditioning'), value: rfd3Settings.conditioning };
                                setRfd3Settings(previous => {
                                    const next: Record<string, unknown> = { ...previous, conditioning };
                                    if (before) { if (before.present) next.conditioning = before.value; else delete next.conditioning; }
                                    return next;
                                });
                            }}
                            onHighlight={setRfd3Picks} />
                        {rfd3Source && <button type="button" onClick={clearRfd3Source}>Clear input structure</button>}
                        </>}
                    </details>}
                </ProteinDesignPanel> : isMotif ? <>
                    <LaProteinaMotifInput
                        taskName={String(effectiveOverride('motif_task_name', motifTaskName) ?? '')}
                        taskCatalog={laCatalog.data?.native_task_catalog?.tasks}
                        onTaskChange={name => setEffectiveOverride('motif_task_name', name, () => setMotifTaskName(name))}
                        studioSource={motifStudioSource}
                        path={String(effectiveMotifPath ?? '')} contig={String(effectiveOverride('contig_string', motifContig) ?? '')} segmentOrder={String(effectiveOverride('segment_order', motifSegmentOrder) ?? '')}
                        atomSpec={effectiveOverride('motif_atom_spec', motifAtomSpec) as string | null | undefined} initialInspection={motifInspection} onInspectionChange={setMotifInspection} onChange={patch => {
                        if (patch.path !== undefined) { const path = patch.path; setEffectiveOverride('motif_pdb', path, () => setMotifPdb(path)); }
                        if (patch.atomSpec !== undefined) { if (!Object.hasOwn(nativeOverrides, 'motif_atom_spec')) setMotifAtomSpec(patch.atomSpec); authorLaMotif('motif_atom_spec', patch.atomSpec); }
                        if (patch.contig !== undefined) { if (!Object.hasOwn(nativeOverrides, 'contig_string')) setMotifContig(patch.contig); authorLaMotif('contig_string', patch.contig); }
                        if (patch.segmentOrder !== undefined) { if (!Object.hasOwn(nativeOverrides, 'segment_order')) setMotifSegmentOrder(patch.segmentOrder); authorLaMotif('segment_order', patch.segmentOrder); }
                    }} />
                    {['contig_string', 'segment_order', 'motif_atom_spec'].map(key => <div key={key}>{overrideNote(key)}</div>)}
                    <ProteinDesignPanel title="Motif conditioning">
                        {namedMotif && <p className="text-xs">Unchanged motif settings inherit the named native motif task. Its effective values are not reported here; retained flat motif values are not requested overrides.</p>}
                        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                            {presetControl}
                            <label className={labelClass}>Atom selection
                                <select className={fieldClass} value={String(effectiveOverride('atom_selection_mode', motifAtomSelectionMode) ?? '')} onChange={event => { if (!Object.hasOwn(nativeOverrides, 'atom_selection_mode')) setMotifAtomSelectionMode(event.target.value); authorLaMotif('atom_selection_mode', event.target.value); }}>
                                    <option value="all_atom">All atom</option><option value="tip_atoms">Tip atoms</option>
                                    <option value="bb3o">Backbone N, Cα, C, O</option><option value="ca">Cα only</option>
                                    {!['all_atom', 'tip_atoms', 'bb3o', 'ca'].includes(String(effectiveOverride('atom_selection_mode', motifAtomSelectionMode) ?? '')) && <option value={String(effectiveOverride('atom_selection_mode', motifAtomSelectionMode) ?? '')}>{inheritsNamedMotif('atom_selection_mode') && effectiveOverride('atom_selection_mode', motifAtomSelectionMode) === undefined ? 'From named motif profile' : `${String(effectiveOverride('atom_selection_mode', motifAtomSelectionMode) ?? 'null')} · retained historical value`}</option>}
                                </select>
                                {overrideNote('atom_selection_mode')}
                            </label>
                            <label className={labelClass}>Motif minimum length
                                <input className={fieldClass} type="number" min={1} max={2000} value={String(effectiveOverride('motif_min_length', motifMinLength) ?? '')} onChange={event => { if (!Object.hasOwn(nativeOverrides, 'motif_min_length')) setMotifMinLength(event.target.value); authorLaMotif('motif_min_length', event.target.value === '' ? null : Number(event.target.value)); }} placeholder="From target lengths" />
                                {overrideNote('motif_min_length')}
                            </label>
                            <label className={labelClass}>Motif maximum length
                                <input className={fieldClass} type="number" min={1} max={2000} value={String(effectiveOverride('motif_max_length', motifMaxLength) ?? '')} onChange={event => { if (!Object.hasOwn(nativeOverrides, 'motif_max_length')) setMotifMaxLength(event.target.value); authorLaMotif('motif_max_length', event.target.value === '' ? null : Number(event.target.value)); }} placeholder="From target lengths" />
                                {overrideNote('motif_max_length')}
                            </label>
                        </div>
                        {overrideNote('motif_task_name')}
                        {laCatalog.error && <p role="status">Named task discovery unavailable. Manual task names and retained overrides remain usable.</p>}
                    </ProteinDesignPanel>
                </> : contextTask && <ProteinDesignPanel title={contextTask === 'custom_json' ? 'Native design input' : 'Molecular context'}
                    description={contextTask === 'dna_conditioned' ? 'DNA context; preparation includes the reverse-complement strand.'
                        : contextTask === 'rna_conditioned' ? 'RNA context; preparation uses the supplied single strand.'
                            : contextTask === 'ligand_conditioned' ? 'Use the ligand’s existing SDF structure.' : 'Use an existing native DISCO input document.'}>
                    <DeNovoContextInput task={contextTask} ligandPath={ligandSdf} ligandName={ligandName} nativeJsonPath={discoInputJson} sequence={nucleicSequence} onChange={updateContext} />
                </ProteinDesignPanel>}
            </div>
            {generator === 'disco' && <DiscoJobsInput value={nativeControls.pcad_disco_jobs_json} path={discoInputJson} sourcePath={nativeControls.pcad_disco_jobs_source_path as string | undefined} onChange={(jobs, sourcePath) => setNativeControls(current => { const next = { ...current }; if (jobs === undefined) { delete next.pcad_disco_jobs_json; delete next.pcad_disco_jobs_source_path; } else { next.pcad_disco_jobs_json = jobs; if (sourcePath !== undefined) next.pcad_disco_jobs_source_path = sourcePath; } return next; })} />}
            <div hidden={section !== 'Sampling'} className="space-y-5">
                <OtherDeNovoSettings generalConditions={generator === 'rfd3'} model={generator as 'rfd3' | DeNovoBackend} task={generator === 'rfd3' ? 'unconditional_monomer' : designTask} selectedPreset={generator === 'laproteina' ? laproteinaPreset : undefined} requestParams={generator === 'rfd3' ? undefined : JSON.parse(JSON.stringify(buildAlternativeWorkflowRequest().params))} value={generator === 'rfd3' ? rfd3Settings : nativeControls} onChange={generator === 'rfd3' ? setRfd3Settings : setNativeControls}
                    primaryOverrideFields={generator === 'laproteina' ? ['num_steps', ...(isMotif ? ['contig_string', 'segment_order', 'motif_atom_spec', 'atom_selection_mode', 'motif_min_length', 'motif_max_length'] : [])] : generator === 'disco' ? ['experiment', 'effort', 'num_inference_seeds', 'seeds', 'use_deepspeed_evo_attention'] : []} />
                {designTask !== 'unconditional' && <ProteinDesignPanel title="Generation" description={countHelp}>{generationControls}</ProteinDesignPanel>}
                <ProteinDesignPanel title={`${engineName} sampling`}>
                    {generator === 'rfd3' ? <div className="grid gap-4 sm:grid-cols-2">
                        <label className={labelClass}>Seed
                            <input className={fieldClass} type="number" min={0} value={seed} onChange={event => setSeed(editedNumber(event.target.value))} />
                        </label>
                        <label className="flex items-center gap-3 text-sm font-medium">
                            <input type="checkbox" checked={dumpTrajectories} onChange={event => setDumpTrajectories(event.target.checked)} />Dump trajectories
                        </label>
                    </div> : backend === 'laproteina' ? <div className="grid gap-4 sm:grid-cols-2">
                        {!isMotif && presetControl}
                        <label className={labelClass}>Sampling steps
                            <input className={fieldClass} type="number" min={50} max={2000} value={String(effectiveOverride('num_steps', laproteinaSteps) ?? '')} onChange={event => { const value = editedNumber(event.target.value); setEffectiveOverride('num_steps', value, () => setLaproteinaSteps(value)); }} />
                            <span className="block text-xs">BMS sampling value is sent explicitly; a native override takes precedence over it and the upstream profile reference.</span>
                            {overrideNote('num_steps')}
                        </label>
                    </div> : <>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <label className={labelClass}>DISCO experiment
                                <select className={fieldClass} value={String(effectiveOverride('experiment', discoExperiment) ?? '')} onChange={event => setEffectiveOverride('experiment', event.target.value, () => setDiscoExperiment(event.target.value))}>
                                    <option value="designable">Designable</option><option value="diverse">Diverse</option>
                                    {!['designable', 'diverse'].includes(String(effectiveOverride('experiment', discoExperiment) ?? '')) && <option value={String(effectiveOverride('experiment', discoExperiment) ?? '')}>{String(effectiveOverride('experiment', discoExperiment) ?? 'null')} (saved)</option>}
                                </select>
                                {overrideNote('experiment')}
                            </label>
                            <label className={labelClass}>Inference effort
                                <select className={fieldClass} value={String(effectiveOverride('effort', discoEffort) ?? '')} onChange={event => setEffectiveOverride('effort', event.target.value, () => setDiscoEffort(event.target.value))}>
                                    <option value="fast">Fast</option><option value="max">Max</option>
                                    {!['fast', 'max'].includes(String(effectiveOverride('effort', discoEffort) ?? '')) && <option value={String(effectiveOverride('effort', discoEffort) ?? '')}>{String(effectiveOverride('effort', discoEffort) ?? 'null')} (saved)</option>}
                                </select>
                                {overrideNote('effort')}
                            </label>
                            <label className={labelClass}>Inference seed count
                                <input className={fieldClass} type="number" min={1} max={1024} value={String(effectiveOverride('num_inference_seeds', discoSeedCount) ?? '')} onChange={event => { const value = editedNumber(event.target.value); setEffectiveOverride('num_inference_seeds', value, () => { setDiscoSeedCountSet(true); setDiscoInferenceSeeds(value); }); }} />
                                {effectiveOverride('num_inference_seeds', discoSeedCount) === null && <span>None (native seeds list; legacy launch may resolve a count)</span>}
                                {overrideNote('num_inference_seeds')}
                            </label>
                            <label className={labelClass}>Exact seeds
                                <input className={fieldClass} value={discoSeedText ?? (Array.isArray(effectiveOverride('seeds', discoSeeds)) ? (effectiveOverride('seeds', discoSeeds) as unknown[]).join(',') : String(effectiveOverride('seeds', discoSeeds) ?? ''))} onChange={event => { const text = event.target.value; setDiscoSeedText(text); const tokens = text.split(',').map(token => token.trim()).filter(Boolean); setEffectiveOverride('seeds', tokens.every(token => /^[+-]?\d+$/.test(token)) ? tokens.map(Number) : text, () => setDiscoSeeds(text)); }} placeholder="Optional comma-separated integers" />
                                {effectiveOverride('seeds', discoSeeds) === null && <span>Native null (no exact seeds)</span>}
                                {overrideNote('seeds')}
                                <span className="flex gap-2 text-xs">
                                    <button type="button" onClick={() => { setDiscoSeedText(undefined); setEffectiveOverride('seeds', null, () => setDiscoSeeds(null)); }}>Set explicit null for seeds</button>
                                    <button type="button" onClick={() => { setDiscoSeedText(undefined); setEffectiveOverride('seeds', [], () => setDiscoSeeds([])); }}>Set empty seeds list</button>
                                </span>
                            </label>
                        </div>
                        {['dna_conditioned', 'rna_conditioned', 'ligand_conditioned'].includes(designTask) && <p className="text-sm text-[var(--text-secondary)]">Max effort is recommended for conditional generation. Explicit Fast remains Fast; native overrides take precedence over these controls.</p>}
                        <details className="rounded-lg border p-3" style={themedInsetStyle}>
                            <summary className="cursor-pointer text-sm font-medium">Advanced inference</summary>
                            <label className="mt-3 flex items-center gap-3 text-sm">
                                <input type="checkbox" ref={node => { if (node) node.indeterminate = effectiveOverride('use_deepspeed_evo_attention', discoAttention) === null; }} checked={effectiveOverride('use_deepspeed_evo_attention', discoAttention) === true} onChange={event => setEffectiveOverride('use_deepspeed_evo_attention', event.target.checked, () => setDiscoAttention(event.target.checked))} />DeepSpeed Evoformer attention
                            </label>
                            {overrideNote('use_deepspeed_evo_attention')}
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
            <div hidden={section !== 'Sequence Design'}>
                <GeneralSequenceDesignSettings value={sequenceDesign} onChange={next => { if (next.invocation_draft?.enabled || next.sequence_design.enabled) { setSequenceOmitted(false); setSequenceNull(false); } setSequenceDesign(next); }} />
            </div>
            </div><DeNovoDocumentEditorPanels /></DeNovoEditorLayout>
            <ProteinDesignRun stickyAction jobName={jobName} onJobNameChange={setJobName} pending={submitMutation.isPending}
                onSubmit={() => { void (generator === 'rfd3' ? submitDeNovo() : submitAlternative()); }} submitLabel="Generate candidates">
                <DeNovoRunScope task={`${engineName} · ${designTask.replaceAll('_', ' ')}`} documents={generator === 'rfd3' ? [rfd3Source?.path].filter((path): path is string => !!path) : generator === 'laproteina' ? (isMotif ? [effectiveMotifPath].filter((path): path is string => typeof path === 'string' && !!path) : []) : ['Native entities / sequence inputs (see task inputs)']} operations={sequenceOmitted || sequenceNull && !sequenceDesign.invocation_draft && !sequenceDesign.sequence_design.enabled ? undefined : generalSequenceRequest(sequenceDesign).sequence_design} />
                {runDetails}
                <ExecutionTargetPicker workflowRequest={stableWorkflowRequest} preloadSelection={DE_NOVO_PRELOAD_SELECTION} />
                {error && <div role="alert" className="text-sm text-[var(--text-primary)]">{error}</div>}
            </ProteinDesignRun>
        </div></DeNovoDocumentEditors></DocumentCaptureContext.Provider>
    );
}
