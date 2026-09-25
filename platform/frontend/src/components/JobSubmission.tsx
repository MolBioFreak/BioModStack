import { launcherWorkflowTemplates, launcherExperimentalTemplates, visibleLauncherTemplates } from '../lib/launcherCatalog';


import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { ParamField, compactUiCopy } from './ModelParameterField';
import { NativeBinderGeneration } from './NativeBinderGeneration';
import { BindCraft2LifecycleDraft } from './BindCraft2Campaign';
import { fetchNativeGenerationInventory, nativeBinderDraft, submitNativeBinderRequest, type NativeBinderModel } from '../lib/nativeBinderAuthoring';
import { FampnnAnalysisControls, fampnnOverridePayload, hydrateFampnnOverrides, fampnnUserParams } from './FampnnAnalysisControls';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, EXECUTION_TARGET_STORAGE_KEY, completeCurrentLaunchContext, fetchModels, fetchModelById, fetchFiles, submitJob, uploadFile, fetchTemplates, fetchTemplateById, fetchInputPresets, type Job } from '../lib/api';
import { getLaunchContext, type JsonObject } from '../lib/projectManager';
import { SequenceManagerModal } from './SequenceManagerModal';
import { TemplateManagerModal } from './TemplateManagerModal';
import { MutagenesisTemplate, buildMutagenesisWorkflowRequest } from './MutagenesisTemplate';
import { AntibodyDenovoTemplate } from './AntibodyDenovoTemplate';

import { StructurePredictionTemplate } from './StructurePredictionTemplate';


import { OligoDesignerTemplate } from './OligoDesignerTemplate';
import { ProteinModificationTemplate } from './ProteinModificationTemplate';

import { MolecularDynamicsTemplate } from './MolecularDynamicsTemplate';
import {
    buildMolecularDynamicsHandoffInitialValues,
    loadMolecularDynamicsDraft,
    parseMolecularDynamicsHandoffRoute,
} from './gen2StartingStructureState.js';
import { ConformationalMappingLauncher } from './conformationalMapping/ConformationalMappingLauncher';
import { LigandSelector, type LigandEntry } from './LigandSelector';
import { ModelDocumentationLinks, getModelDocumentationLinks, type ModelDocumentationTopic } from './ModelDocumentationLinks';
import { getDedicatedTemplateInitialValues, isDedicatedLauncherTemplate } from './jobSubmissionTemplateState.js';
import { getWorkflowModelTopics } from './workflowModelInventory.js';
import { isAntibodyPipelineMode } from '../lib/antibodyModes';
import { ModelIntegrationControl, useModelIntegrationConfig } from './ModelIntegrationControl';
import { FrustraMpnnSettingsPanel } from './frustrampnn/FrustraMpnnSettingsPanel.js';
import { ExecutionTargetPicker } from './ExecutionTargetPicker';
import { ExecutionPolicyControl } from './ExecutionPolicyControl';
import { initialExecutionPolicy } from '../lib/executionPolicy';
import { ProjectTechnicalDetails, ProjectWorkflowSetupBanner, useProjectWorkflowSetup } from './project-manager/ProjectWorkflowSetup';
import {
    hydrateFrustraMpnnSettings,
    mergeFrustraMpnnLaunchParams,
    resolveFrustraMpnnWorkflowId,
    type FrustraMpnnRequestedSettings,
} from './frustrampnn/frustraMpnnSettingsState.js';


interface FileBrowserProps {
    onSelect: (path: string) => void;
    onCancel: () => void;
}

function FileBrowser({ onSelect, onCancel }: FileBrowserProps) {
    const [path, setPath] = useState('/');
    const fileInputRef = useRef<HTMLInputElement>(null);
    const queryClient = useQueryClient();

    const { data: files } = useQuery({
        queryKey: ['files', path],
        queryFn: () => fetchFiles(path),
    });

    const uploadMutation = useMutation({
        mutationFn: (file: File) => uploadFile(path === '/' ? 'inputs' : path, file),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['files', path] });
        },
    });

    const handleNavigate = (newPath: string) => {
        setPath(newPath);
    };

    const handleUp = () => {
        const parts = path.split('/').filter(p => p);
        parts.pop();
        setPath('/' + parts.join('/'));
    };

    const handleUploadClick = () => {
        if (fileInputRef.current) {
            fileInputRef.current.click();
        }
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files[0]) {
            uploadMutation.mutate(e.target.files[0]);
        }
        // Reset input
        e.target.value = '';
    };

    return (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
            <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl h-[80vh] flex flex-col shadow-2xl">
                <div className="p-4 border-b border-slate-700 flex justify-between items-center bg-slate-800/50 rounded-t-xl">
                    <h3 className="font-semibold text-slate-200">Select File</h3>
                    <div className="flex gap-3 items-center">
                        <input
                            type="file"
                            ref={fileInputRef}
                            onChange={handleFileChange}
                            className="hidden"
                        />
                        <button
                            onClick={handleUploadClick}
                            disabled={uploadMutation.isPending}
                            className="rounded-md border border-slate-600 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700"
                        >
                            {uploadMutation.isPending ? 'Uploading...' : 'Upload'}
                        </button>
                        <button onClick={onCancel} className="text-slate-400 hover:text-white">✕</button>
                    </div>
                </div>

                <div className="p-2 border-b border-slate-700 bg-slate-800/30 flex items-center gap-2">
                    <button
                        onClick={handleUp}
                        className="rounded border border-slate-600 bg-slate-800 px-2.5 py-1 text-sm font-medium text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                        disabled={path === '/'}
                    >
                        Up
                    </button>
                    <input
                        type="text"
                        value={path}
                        readOnly
                        className="flex-1 bg-transparent text-sm text-slate-400 outline-none"
                    />
                </div>

                <div className="flex-1 overflow-auto p-2">
                    {files?.data.entries.map((entry: UntypedApiValue) => (
                        <div
                            key={entry.path}
                            onClick={() => entry.is_directory ? handleNavigate(entry.path) : onSelect(entry.path)}
                            className={`flex items-center gap-3 p-2 rounded cursor-pointer ${entry.is_directory
                                ? 'text-blue-400 hover:bg-blue-500/10'
                                : 'text-slate-300 hover:bg-slate-700'
                                }`}
                        >
                            <span className={`inline-flex h-7 min-w-10 items-center justify-center rounded border text-[10px] font-semibold uppercase tracking-[0.14em] ${
                                entry.is_directory
                                    ? 'border-blue-500/30 bg-blue-500/10 text-blue-300'
                                    : 'border-slate-600 bg-slate-800 text-slate-300'
                            }`}>
                                {entry.is_directory ? 'Dir' : 'File'}
                            </span>
                            <span className="flex-1 truncate">{entry.name}</span>
                            {!entry.is_directory && (
                                <span className="text-xs text-slate-500">
                                    {(entry.size_bytes / 1024).toFixed(1)} KB
                                </span>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

const MODEL_DOCUMENTATION_TOPIC_KEYS = new Set<ModelDocumentationTopic>([
    'alphafold2', 'boltz2', 'boltzgen', 'caliby', 'chai1', 'confornets', 'diffdock', 'disco', 'esmfold2',
    'fampnn', 'fold_cp', 'laproteina', 'ligandmpnn', 'ppiflow', 'protein_hunter', 'proteinmpnn', 'protenix', 'rf3',
    'rfantibody', 'rfdiffusion', 'rfdpoly', 'unidock',
]);

const getTemplateDocumentationTopics = (
    template: UntypedApiValue | null | undefined,
    launchParams?: UntypedApiValue | null,
): ModelDocumentationTopic[] => {
    const selectedTopic = String(
        launchParams?.workflow_model_topic
        || launchParams?.model_documentation_topic
        || launchParams?.mapping_model
        || template?.preset_params?.workflow_model_topic
        || template?.preset_params?.model_documentation_topic
        || ''
    ).trim() as ModelDocumentationTopic;
    if (selectedTopic && MODEL_DOCUMENTATION_TOPIC_KEYS.has(selectedTopic)) return [selectedTopic];

    const workflowTopics = getWorkflowModelTopics(template?.id || template?.model_id);
    if (workflowTopics.length > 0) return workflowTopics;

    const identity = `${template?.id || ''} ${template?.model_id || ''} ${template?.name || ''}`.toLowerCase();
    if (identity.includes('boltz_cp_experimental') || identity.includes('fold-cp')) return ['fold_cp', 'boltz2'];
    if (identity.includes('confornets')) return ['confornets'];
    if (identity.includes('esmfold2')) return ['esmfold2'];
    if (identity.includes('protein_modification_experimental') || identity.includes('protein_local_redesign') || identity.includes('local redesign')) return ['laproteina', 'disco', 'rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2'];
    if (identity.includes('antibody_denovo') || identity.includes('nanobody') || identity.includes('rfantibody')) return ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2', 'esmfold2'];

    if (identity.includes('structure_prediction') || identity.includes('structure prediction')) return ['boltz2', 'fold_cp', 'protenix', 'esmfold2'];
    if (identity.includes('boltz')) return ['boltz2'];
    if (identity.includes('rfdiffusion') || identity.includes('diffusion')) return ['rfdiffusion'];
    return [];
};

const getModelDocumentationTopics = (model: UntypedApiValue | null | undefined): ModelDocumentationTopic[] => {
    const workflowTopics = getWorkflowModelTopics(model?.id);
    if (workflowTopics.length > 0) return workflowTopics;

    const identity = `${model?.id || ''} ${model?.name || ''} ${model?.category || ''}`.toLowerCase();
    if (identity.includes('boltz_cp_experimental') || identity.includes('fold-cp')) return ['fold_cp', 'boltz2'];
    if (identity.includes('confornets')) return ['confornets'];
    if (identity.includes('esmfold2')) return ['esmfold2'];
    if (identity.includes('protenix')) return ['protenix'];
    if (identity.includes('rf3') || identity.includes('rosettafold')) return ['rf3'];
    if (identity.includes('antibody') || identity.includes('rfantibody')) return ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2', 'esmfold2'];
    if (identity.includes('rfdiffusion')) return ['rfdiffusion'];
    if (identity.includes('boltzgen')) return ['boltzgen'];
    if (identity.includes('boltz2') || identity.includes('boltz-2')) return ['boltz2'];
    return [];
};

const getCompactTemplateDescription = (template: UntypedApiValue): string => {
    switch (template.id) {
        case 'structure_prediction':
            return 'Predict proteins, nucleic acids, and complexes.';
        case 'antibody_denovo':
            return 'Generate, refine, validate, and review nanobody candidates.';

        case 'protein_modification_experimental':
        case 'protein_local_redesign':
            return 'Generate new proteins or remodel selected regions of an existing structure.';
        case 'boltz_cp_experimental':
            return 'NVIDIA Fold-CP predictor inside Structure Prediction.';
        case 'confornets_experimental':
        case 'conformational_mapping':
            return 'Complete-complex Protenix v2 ensembles, canonical ConforNets/import alternatives, residue mapping, FrustraMPNN landscapes, and support-ranked comparison.';
        case 'esmfold2':
        case 'esmfold2_experimental':
            return 'ESMFold2 engine inside Structure Prediction.';
        case 'mutagenesis':
            return 'Build variant libraries and predict structures.';
        case 'oligo_design':
            return 'Design nucleoprotein assemblies with validation.';
        default:
            return template.short_description || template.summary || template.description || '';
    }
};

export function JobSubmission() {
    const [initialReturnPolicy] = useState(initialExecutionPolicy);
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const projectSetup = useProjectWorkflowSetup();
    const mdHandoff = useMemo(() => {
        try {
            return { route: parseMolecularDynamicsHandoffRoute(`?${searchParams.toString()}`), error: '' };
        } catch (error) {
            return { route: null, error: error instanceof Error ? error.message.slice(0, 512) : 'Invalid Molecular Dynamics handoff route.' };
        }
    }, [searchParams]);
    const launchContextId = searchParams.get('launch_context_id');
    const launchContextQuery = useQuery({
        queryKey: ['launch-context', launchContextId],
        queryFn: ({ signal }) => getLaunchContext(launchContextId as string, signal),
        enabled: Boolean(launchContextId),
        retry: false,
    });
    const recoveredLaunchRef = useRef<string | null>(null);
    useEffect(() => {
        const recoveryJobId = launchContextQuery.data?.recovery_job_id;
        if (!recoveryJobId || recoveredLaunchRef.current === recoveryJobId) return;
        recoveredLaunchRef.current = recoveryJobId;
        void completeCurrentLaunchContext({ id: recoveryJobId }).then((returnUri) => {
            if (returnUri) navigate(returnUri);
        });
    }, [launchContextQuery.data?.recovery_job_id, navigate]);
    const [wizardMode, setWizardMode] = useState<'templates' | 'experimental' | 'manual'>(() => searchParams.has('model') ? 'manual' : 'templates');

    // Read template from URL, allows page refresh and bookmarking
    const urlTemplate = searchParams.get('template');
    const routeTemplateId = urlTemplate === 'protein_local_redesign'
        ? 'protein_modification_experimental'
        : urlTemplate === 'confornets_experimental' ? 'conformational_mapping' : urlTemplate;
    const [selectedTemplateId, setSelectedTemplateIdInternal] = useState<string | null>(routeTemplateId);
    const [engineChooserOpen, setEngineChooserOpen] = useState(false);

    const pendingTemplateRoute = useRef<string | null | undefined>(undefined);
    // Wrapper to sync state with URL
    const setSelectedTemplateId = useCallback((id: string | null) => {
        const canonicalId = id === 'confornets_experimental' ? 'conformational_mapping' : id;
        pendingTemplateRoute.current = canonicalId;
        setSelectedTemplateIdInternal(canonicalId);
        setEngineChooserOpen(false);
        const next = new URLSearchParams(searchParams);
        if (canonicalId) {
            next.set('template', canonicalId);
        } else {
            next.delete('template');
        }
        next.delete('model'); next.delete('mode'); next.delete('engine');
        setSearchParams(next, { replace: true });
    }, [searchParams, setSearchParams]);
    const [selectedModelId, setSelectedModelId] = useState<string | null>(() => searchParams.get('model'));
    const [selectedModeId, setSelectedModeId] = useState<string | null>(() => searchParams.get('mode'));
    // Explicit model handoffs and mode changes must reopen the same editor on
    // refresh, without resurrecting the template or the retired model catalog.
    useEffect(() => {
        if (wizardMode !== 'manual' || !selectedModelId) return;
        const next = new URLSearchParams(searchParams);
        next.delete('template');
        next.delete('engine');
        next.set('model', selectedModelId);
        if (selectedModeId) next.set('mode', selectedModeId);
        else next.delete('mode');
        if (next.toString() !== searchParams.toString()) {
            pendingTemplateRoute.current = null;
            setSearchParams(next, { replace: true });
        }
    }, [wizardMode, selectedModelId, selectedModeId, searchParams, setSearchParams]);
    const [jobName, setJobName] = useState('');
    const [params, setParams] = useState<Record<string, UntypedApiValue>>({});
    // Keep user scopes in the canonical cloned/saved request state.
    const fampnnOverrides = params.fampnn_analysis_overrides;
    const setFampnnOverrides = (value: unknown) => setParams(previous => ({ ...previous, fampnn_analysis_overrides: value }));
    let fampnnError = '';
    try { hydrateFampnnOverrides(fampnnOverrides); } catch (error) { fampnnError = String(error); }
    const frustrampnnIntegrationQuery = useModelIntegrationConfig('frustrampnn');
    const [explicitRunFrustrampnn, setExplicitRunFrustrampnn] = useState<boolean | undefined>(undefined);
    const [frustrampnnSettings, setFrustrampnnSettings] = useState<FrustraMpnnRequestedSettings>(() => (
        hydrateFrustraMpnnSettings(undefined)
    ));
    const [showFileBrowser, setShowFileBrowser] = useState<string | null>(null);
    const [showSequenceManager, setShowSequenceManager] = useState(false);
    const [showTemplateManager, setShowTemplateManager] = useState(false);
    const [sequenceToSave, setSequenceToSave] = useState<{ sequence: string; name?: string } | null>(null);
    const [activeSequenceField, setActiveSequenceField] = useState<string>('sequence');
    const [ligands, setLigands] = useState<LigandEntry[]>([]);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [clonedValues, setClonedValues] = useState<Record<string, UntypedApiValue> | undefined>(undefined);
    const binderDraftRef = useRef<Record<string, UntypedApiValue> | undefined>(undefined);
    const [binderInitialDraft, setBinderInitialDraft] = useState<Record<string, UntypedApiValue> | undefined>(undefined);
    const binderNativeDrafts = useRef<Record<string, Record<string, UntypedApiValue>>>({});
    useEffect(() => {
        if (clonedValues?.binder_native_drafts && typeof clonedValues.binder_native_drafts === 'object') {
            binderNativeDrafts.current = { ...clonedValues.binder_native_drafts };
        }
    }, [clonedValues]);
    const [projectDraftValues, setProjectDraftValues] = useState<Record<string, UntypedApiValue>>({});
    const hydratedProjectSetup = useRef<string | null>(null);
    const [projectActionError, setProjectActionError] = useState<string | null>(null);
    const [projectActionBusy, setProjectActionBusy] = useState(false);
    useEffect(() => {
        if (!projectSetup.active || !projectSetup.setup) { hydratedProjectSetup.current = null; return; }
        const identity = `${projectSetup.setup.project_id}:${projectSetup.setup.setup_context_id}`;
        if (hydratedProjectSetup.current === identity) return;
        hydratedProjectSetup.current = identity;
        setBinderInitialDraft(undefined);
        binderDraftRef.current = undefined;
        setClonedValues(projectSetup.settings as Record<string, UntypedApiValue>);
        setProjectDraftValues(projectSetup.settings as Record<string, UntypedApiValue>);
        const draft = projectSetup.settings as Record<string, UntypedApiValue>;
        if (draft.binder_workflow_draft) binderDraftRef.current = draft.binder_workflow_draft;
        if (['ppiflow', 'boltzgen'].includes(String(draft.model_id)) && typeof draft.mode === 'string') {
            setWizardMode('manual'); setSelectedTemplateId(null);
            setSelectedModelId(draft.model_id); setSelectedModeId(draft.mode);
            setParams(draft); setJobName(typeof draft.job_name === 'string' ? draft.job_name : '');
        }
    }, [projectSetup.active, projectSetup.setup?.project_id, projectSetup.setup?.setup_context_id, projectSetup.setup?.generation]);
    const mdHandoffInitialValues = useMemo<Record<string, UntypedApiValue> | undefined>(() => {
        const route = mdHandoff.route;
        if (!route) return undefined;
        const savedDraft = route.draftId ? loadMolecularDynamicsDraft(sessionStorage, route.draftId) : null;
        return buildMolecularDynamicsHandoffInitialValues(route, savedDraft) as Record<string, UntypedApiValue>;
    }, [mdHandoff.route]);
    const molecularDynamicsInitialValues = useMemo<Record<string, UntypedApiValue>>(
        () => ({ ...(clonedValues || {}), ...(mdHandoffInitialValues || {}) }),
        [clonedValues, mdHandoffInitialValues],
    );
    const [dedicatedTemplateVersion, setDedicatedTemplateVersion] = useState(0);
    const hydratedLaunchContextRef = useRef<string | null>(null);
    useEffect(() => {
        const context = launchContextQuery.data;
        const scheduler = context?.pinned_scheduler as UntypedApiValue | null | undefined;
        const schedulerParams = scheduler?.params;
        if (
            !context?.launch_context_id
            || hydratedLaunchContextRef.current === context.launch_context_id
            || !scheduler
            || !['esmfold2', 'boltz2', 'protenix', 'molecular_dynamics'].includes(String(scheduler.model_id))
            || !schedulerParams
            || typeof schedulerParams !== 'object'
            || Array.isArray(schedulerParams)
        ) return;
        const paramsFromContext = schedulerParams as Record<string, UntypedApiValue>;
        const isMdContext = scheduler.model_id === 'molecular_dynamics';
        const loadedJobName = typeof scheduler.name === 'string' && scheduler.name.trim()
            ? scheduler.name
            : String(paramsFromContext.name || paramsFromContext.job_name || (isMdContext ? 'molecular_dynamics' : 'structure_prediction'));
        const nextValues: Record<string, UntypedApiValue> = isMdContext
            ? {
                name: loadedJobName,
                job_name: loadedJobName,
                model_id: scheduler.model_id,
                mode: scheduler.mode,
                intent: paramsFromContext,
            }
            : {
                ...paramsFromContext,
                name: loadedJobName,
                job_name: loadedJobName,
                model_id: scheduler.model_id,
                mode: scheduler.mode,
            };
        hydratedLaunchContextRef.current = context.launch_context_id;
        setClonedValues(nextValues);
        setParams(nextValues);
        setJobName(loadedJobName);
        if (isMdContext) {
            setWizardMode('templates');
            setSelectedTemplateId('molecular_dynamics');
            setSelectedModelId(null);
            setSelectedModeId(null);
        } else {
            setSelectedModelId(String(scheduler.model_id));
            setSelectedModeId(typeof scheduler.mode === 'string' ? scheduler.mode : null);
        }
        setDedicatedTemplateVersion((version) => version + 1);
    }, [launchContextQuery.data, setSelectedTemplateId]);
    const [templateManagerContext, setTemplateManagerContext] = useState<{
        currentParams?: Record<string, UntypedApiValue>;
        currentModelId?: string;
        currentMode?: string;
        baseTemplateId?: string;
    }>({});

    const openTemplateManager = (context: {
        currentParams?: Record<string, UntypedApiValue>;
        currentModelId?: string;
        currentMode?: string;
        baseTemplateId?: string;
    }) => {
        setTemplateManagerContext(context);
        setShowTemplateManager(true);
    };

    // Dedicated templates should not retain stale clone params once user navigates away.
    const handleDedicatedTemplateBack = () => {
        if (selectedTemplateId === 'antibody_denovo') setBinderInitialDraft(binderDraftRef.current);
        setSelectedTemplateId(null);
        setClonedValues(undefined);
    };

    const openMdStructurePrediction = (values: Record<string, unknown>) => {
        setWizardMode('templates');
        setClonedValues(values as Record<string, UntypedApiValue>);
        setSelectedTemplateId('structure_prediction');
        setDedicatedTemplateVersion((version) => version + 1);
    };

    const handleTemplateCardSelect = (templateId: string) => {
        setClonedValues(getDedicatedTemplateInitialValues(templateId));
        if (isDedicatedLauncherTemplate(templateId)) {
            setDedicatedTemplateVersion((prev) => prev + 1);
        }
        setSelectedTemplateId(templateId);
    };

    // Check for cloned job data on mount
    useEffect(() => {
        const stored = localStorage.getItem('clonedJobData');
        if (stored) {
            try {
                const data = JSON.parse(stored);
                // A saved Local choice must clear any unrelated launcher target.
                if (data.execution_target_id) {
                    sessionStorage.setItem(EXECUTION_TARGET_STORAGE_KEY, data.execution_target_id);
                } else {
                    sessionStorage.removeItem(EXECUTION_TARGET_STORAGE_KEY);
                }
                window.dispatchEvent(new Event('bms:execution-target-change'));
                data.params = fampnnUserParams(data.params || {});
                delete data.params.remote_result_policy;
                console.log('Loading cloned job data:', data);

                // Set common fields
                if (data.name) setJobName(data.name);

                // Determine routing
                if (data.model_id === 'bindcraft2') {
                    setWizardMode('templates');
                    setSelectedTemplateId('antibody_denovo');
                    setClonedValues({ ...data.params, name: data.name, job_name: data.name, model_id: data.model_id, denovo_generator: 'bindcraft2', mode: data.mode });
                }
                else if (data.model_id === 'antibody_denovo' || data.model_id === 'template_antibody_denovo' || data.mode === 'antibody_denovo' || isAntibodyPipelineMode(data.mode) || data.params?.antibody_pipeline_steps) {
                    setWizardMode('templates');
                    setSelectedTemplateId('antibody_denovo');
                    setClonedValues({ ...data.params, name: data.name, ...(data.mode !== undefined ? { mode: data.mode } : {}) });
                }
                else if (data.params?.mutagenesis_variants) {
                    setWizardMode('templates');
                    setSelectedTemplateId('mutagenesis');
                    // Mutagenesis logic might need updates for pre-filling too, but focusing on Antibody first
                }
                // 3. Fold-CP reopens as a predictor inside Structure Prediction.
                else if (data.model_id === 'boltz_cp_experimental') {
                    setWizardMode('templates');
                    setSelectedTemplateId('structure_prediction');
                    setClonedValues({
                        ...data.params,
                        name: data.name,
                        pred_method: 'fold_cp',
                        execution_target_id: data.execution_target_id ?? null,
                    });
                }
                // 4. ESMFold2 compatibility IDs reopen the parent Structure Prediction workflow.
                else if (data.model_id === 'esmfold2' || data.model_id === 'esmfold2_experimental') {
                    setWizardMode('templates');
                    setSelectedTemplateId('structure_prediction');
                    setClonedValues({
                        ...data.params,
                        name: data.name,
                        pred_method: 'esmfold2',
                    });
                }

                else if (data.model_id === 'molecular_dynamics') {
                    setWizardMode('templates');
                    setSelectedTemplateId('molecular_dynamics');
                    setClonedValues({
                        ...data.params,
                        name: data.name,
                        job_name: data.name,
                        source_job_id: data.source_job_id,
                    });
                }
                else if (data.model_id === 'boltzgen' && data.mode === 'nanobody_binder' && !data.params?.native_generation_authoring) {
                    setWizardMode('templates');
                    setSelectedTemplateId('antibody_denovo');
                    setClonedValues({ ...data.params, name: data.name, denovo_generator: 'boltzgen' });
                }

                // 6. Legacy and canonical conformational-mapping jobs reopen the one published canonical launcher.
                else if (data.model_id === 'confornets_experimental' || data.params?.template_model_id === 'confornets_experimental') {
                    sessionStorage.removeItem('bms.conformational-mapping.launcher.v1');
                    setWizardMode('templates');
                    setSelectedTemplateId('conformational_mapping');
                    const legacyDefaults = getDedicatedTemplateInitialValues('conformational_mapping') || {};
                    const legacyConfornets = {
                        ...(
                            legacyDefaults.confornets
                            && typeof legacyDefaults.confornets === 'object'
                            && !Array.isArray(legacyDefaults.confornets)
                                ? legacyDefaults.confornets as Record<string, unknown>
                                : {}
                        ),
                    };
                    delete legacyConfornets.output_count;
                    setClonedValues({
                        ...legacyDefaults,
                        name: data.name,
                        backend: 'confornets',
                        confornets: legacyConfornets,
                    });
                    setJobName(data.name || data.params?.job_name || data.params?.sequence_name || '');
                }
                else if (data.model_id === 'conformational_mapping') {
                    sessionStorage.removeItem('bms.conformational-mapping.launcher.v1');
                    setWizardMode('templates');
                    setSelectedTemplateId('conformational_mapping');
                    setClonedValues({
                        ...(getDedicatedTemplateInitialValues('conformational_mapping') || {}),
                        ...data.params,
                        name: data.name || data.params?.name || 'Conformational mapping',
                    });
                    setJobName(data.name || 'Conformational mapping');
                }
                else if (data.model_id === 'protein_local_redesign' || data.params?.template_model_id === 'protein_local_redesign') {
                    setWizardMode('experimental');
                    setSelectedTemplateId('protein_modification_experimental');
                    setClonedValues({
                        ...data.params,
                        pinned_gpu: data.pinned_gpu,
                        name: data.name,
                        modification_mode: 'rfd3_local_redesign',
                        template_model_id: 'protein_local_redesign',
                    });
                }
                else if (data.mode === 'shape_blueprint' || data.params?.modification_mode === 'shape_blueprint') {
                    setWizardMode('experimental');
                    setSelectedTemplateId('protein_modification_experimental');
                    setClonedValues({ ...data.params, name: data.name, modification_mode: 'shape_blueprint' });
                }
                // 7. Manual Mode
                else {
                    setWizardMode('manual');
                    setSelectedTemplateId(null);
                    setSelectedModelId(data.model_id);
                    setSelectedModeId(data.mode);
                    setClonedValues(data.params);
                    setParams(data.params);
                }

                // Clear storage
                localStorage.removeItem('clonedJobData');
            } catch (e) {
                console.error("Failed to parse cloned job data", e);
            }
        }
    }, [setSelectedTemplateId]);

    useEffect(() => {
        setExplicitRunFrustrampnn(
            typeof params.run_frustrampnn === 'boolean' ? params.run_frustrampnn : undefined,
        );
        setFrustrampnnSettings(hydrateFrustraMpnnSettings(params.frustrampnn_settings));
    }, [params.run_frustrampnn, params.frustrampnn_settings]);

    const { data: modelsData } = useQuery({
        queryKey: ['models'],
        queryFn: () => fetchModels(),
    });

    const { data: templatesData } = useQuery({
        queryKey: ['templates'],
        queryFn: () => fetchTemplates(),
    });

    // Dedicated launcher templates that use specialized components instead of API-driven config
    const dedicatedTemplateByModelId: Record<string, string> = {
        template_antibody_denovo: 'antibody_denovo',
        bindcraft2: 'antibody_denovo',

        protein_modification_experimental: 'protein_modification_experimental',
        protein_local_redesign: 'protein_modification_experimental',
        protein_cad_experimental: 'protein_modification_experimental',
        boltz_cp_experimental: 'structure_prediction',
        conformational_mapping: 'conformational_mapping',
        confornets_experimental: 'conformational_mapping',
        esmfold2: 'structure_prediction',
        esmfold2_experimental: 'structure_prediction',
    };
    const visibleApiTemplates = useMemo(() => visibleLauncherTemplates(
        templatesData?.data ?? [], Boolean((window as UntypedApiValue).__DEBUG_MODE__)
    ), [templatesData]);
    const workflowTemplateCards = useMemo(
        () => [...visibleApiTemplates.filter((t: UntypedApiValue) => !t.experimental), ...launcherWorkflowTemplates],
        [launcherWorkflowTemplates, visibleApiTemplates]
    );
    const experimentalTemplateCards = useMemo(
        () => [...visibleApiTemplates.filter((t: UntypedApiValue) => t.experimental), ...launcherExperimentalTemplates],
        [launcherExperimentalTemplates, visibleApiTemplates]
    );

    useEffect(() => {
        // Router transitions can lag local state by a render. Do not reopen the
        // previous template while an explicit native-model handoff clears its URL.
        if (pendingTemplateRoute.current !== undefined) {
            if (routeTemplateId !== pendingTemplateRoute.current) return;
            pendingTemplateRoute.current = undefined;
        }
        if (!routeTemplateId || routeTemplateId === selectedTemplateId) return;
        setEngineChooserOpen(false);
        if (routeTemplateId === 'boltz_cp_experimental') {
            setSelectedTemplateIdInternal('structure_prediction');
            setClonedValues((previous: UntypedApiValue) => ({ ...(previous || {}), pred_method: 'fold_cp' }));
            setWizardMode('templates');
            return;
        }
        const apiTemplate = visibleApiTemplates.find((template: UntypedApiValue) => template.id === routeTemplateId);
        if (!isDedicatedLauncherTemplate(routeTemplateId) && !apiTemplate) return;
        setSelectedTemplateIdInternal(routeTemplateId);
        setWizardMode(apiTemplate?.experimental ? 'experimental' : 'templates');
    }, [routeTemplateId, selectedTemplateId, visibleApiTemplates]);

    const routeUserTemplate = (template: UntypedApiValue) => {
        // Historical generic BoltzGen templates can carry the old antibody card
        // identity. The explicit native model/mode remains the scientific owner.
        if (template.model_id === 'boltzgen' && template.mode && template.mode !== 'nanobody_binder') {
            setWizardMode('manual');
            setSelectedTemplateId(null);
            setSelectedModelId(template.model_id);
            setSelectedModeId(template.mode);
            setClonedValues(template.params || {});
            setParams(template.params || {});
            setJobName(template.params?.job_name || template.params?.name || template.name || '');
            return;
        }
        const rawApiTemplateId = typeof template.base_template_id === 'string' ? template.base_template_id : null;
        const apiTemplateId = rawApiTemplateId === 'confornets_experimental'
            ? 'conformational_mapping'
            : rawApiTemplateId === 'boltz_cp_experimental'
                ? 'structure_prediction'
                : rawApiTemplateId;
        const matchedApiTemplate = apiTemplateId
            ? visibleApiTemplates.find((candidate: UntypedApiValue) => candidate.id === apiTemplateId)
            : null;
        if (matchedApiTemplate) {
            const loadedJobName = template.params?.job_name || template.params?.name || template.name || '';
            if (apiTemplateId === 'conformational_mapping') {
                sessionStorage.removeItem('bms.conformational-mapping.launcher.v1');
            }
            setWizardMode(matchedApiTemplate.experimental ? 'experimental' : 'templates');
            setSelectedTemplateId(apiTemplateId);
            setClonedValues({
                ...(matchedApiTemplate.preset_params || {}),
                ...(template.params || {}),
                job_name: loadedJobName,
                name: loadedJobName,
            });
            setParams({ ...(matchedApiTemplate.preset_params || {}), ...(template.params || {}) });
            setJobName(loadedJobName);
            setSelectedModelId(null);
            setSelectedModeId(null);
            return;
        }

        const dedicatedTemplateId =
            (isDedicatedLauncherTemplate(apiTemplateId) && apiTemplateId) ||
            (template.model_id === 'boltzgen' && template.mode === 'nanobody_binder' && !template.params?.native_generation_authoring ? 'antibody_denovo' : template.model_id ? dedicatedTemplateByModelId[template.model_id] : null);

        if (dedicatedTemplateId) {
            const loadedJobName = template.params?.job_name || template.params?.name || template.name || '';
            const templateModelId = template.model_id || template.params?.template_model_id;
            const isLegacyEsmfold2 = templateModelId === 'esmfold2' || templateModelId === 'esmfold2_experimental';
            const isLegacyFoldCp = templateModelId === 'boltz_cp_experimental' || rawApiTemplateId === 'boltz_cp_experimental';
            const isLegacyBoltzGen = templateModelId === 'boltzgen';
            if (dedicatedTemplateId === 'conformational_mapping') {
                sessionStorage.removeItem('bms.conformational-mapping.launcher.v1');
            }
            setWizardMode('templates');
            setSelectedTemplateId(dedicatedTemplateId);
            setDedicatedTemplateVersion((prev) => prev + 1);
            setClonedValues({
                ...template.params,
                name: loadedJobName,
                job_name: loadedJobName,
                template_model_id: isLegacyEsmfold2 || isLegacyFoldCp || isLegacyBoltzGen ? undefined : templateModelId,
                modification_mode: templateModelId === 'protein_local_redesign'
                    ? 'rfd3_local_redesign'
                    : template.params?.modification_mode,
                ...(isLegacyEsmfold2 ? { pred_method: 'esmfold2' } : {}),
                ...(isLegacyFoldCp ? { pred_method: 'fold_cp' } : {}),
                ...(isLegacyBoltzGen ? { denovo_generator: 'boltzgen' } : {}),
                ...(templateModelId === 'bindcraft2' ? { denovo_generator: 'bindcraft2', model_id: 'bindcraft2', mode: template.mode ?? template.params?.mode ?? 'campaign' } : {}),
                structure_launch_variant: template.params?.structure_launch_variant,
            });
            setJobName(loadedJobName);
            setSelectedModelId(null);
            setSelectedModeId(null);
            setParams({});
            return;
        }

        setWizardMode('manual');
        setSelectedTemplateId(null);
        setClonedValues(fampnnUserParams(template.params || {}));
        setParams(fampnnUserParams(template.params || {}));
        if (template.model_id) setSelectedModelId(template.model_id);
        if (template.mode) setSelectedModeId(template.mode);
        setJobName(template.params?.job_name || template.name || '');
    };

    const { data: selectedTemplateData } = useQuery({
        queryKey: ['template', selectedTemplateId],
        queryFn: () => selectedTemplateId ? fetchTemplateById(selectedTemplateId) : null,
        // Skip fetch for hardcoded templates - they don't exist in the API
        enabled: !!selectedTemplateId && !isDedicatedLauncherTemplate(selectedTemplateId),
    });
    const templateDetail = selectedTemplateData?.data?.data ?? selectedTemplateData?.data;

    // Fetch ligand presets for dynamic dropdown
    const { data: ligandPresetsData } = useQuery({
        queryKey: ['presets', 'ligand'],
        queryFn: () => fetchInputPresets('ligand'),
    });
    const ligandPresets = ligandPresetsData?.data ?? [];

    const submitBinderRequest = async (jobData: Partial<Job>, draft = projectDraftValues) => {
        if (!projectSetup.active) return submitNativeBinderRequest({ ...jobData, ...(launchContextId ? { launch_context_id: launchContextId } : {}) });
        setProjectActionError(null);
        setProjectActionBusy(true);
        try {
            // Save the real editor request separately from source/UI provenance.
            const prepared = await projectSetup.startRun(JSON.parse(JSON.stringify({ ...draft, binder_native_drafts: binderNativeDrafts.current, native_job_request: jobData })) as JsonObject, { stayInEditor: true });
            const contextId = prepared.launch_context_id;
            if (!contextId) throw new Error('The native Project workflow did not return a launch context.');
            const context = await getLaunchContext(contextId);
            const request = context.pinned_scheduler as Partial<Job> | undefined;
            // Submit the server-normalized request, not recomputed form defaults.
            const response = await submitNativeBinderRequest({ ...(request ?? jobData), launch_context_id: contextId });
            const binding = await api.post<{ return_uri: string }>(`/api/launch-contexts/${encodeURIComponent(contextId)}/bind`, { job_id: response.data.id });
            return { ...response, data: { ...response.data, return_uri: binding.data.return_uri } };
        } catch (error) {
            setProjectActionError(error instanceof Error ? error.message : String(error));
            throw error;
        } finally { setProjectActionBusy(false); }
    };
    const submitMutation = useMutation({
        mutationFn: (jobData: Partial<Job>) => ['ppiflow', 'boltzgen', 'bindcraft2'].includes(jobData.model_id ?? '')
            ? submitBinderRequest(jobData) : submitJob(jobData),
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            const returnUri = response.data?.return_uri;
            if (typeof returnUri === 'string' && returnUri.startsWith('/projects/') && !returnUri.startsWith('//')) {
                navigate(returnUri);
            } else {
                navigate('/');
            }
        },
        onError: (error: UntypedApiValue) => {
            console.error('Job submission failed:', error);
            const detail = error.response?.data?.detail;
            const message = typeof detail === 'object'
                ? JSON.stringify(detail, null, 2)
                : (detail || error.message || error);
            window.alert('Job Submission Failed:\n' + message);
        }
    });

    const models = (modelsData?.data ?? []).filter((model: UntypedApiValue) => !['protein_modification_experimental', 'protein_cad_experimental', 'protein_local_redesign', 'caliby_experimental', 'protein_hunter_experimental', 'boltz_cp_experimental', 'confornets_experimental', 'conformational_mapping', 'esmfold2', 'esmfold2_experimental'].includes(model.id));
    const listedSelectedModel = models.find((m: UntypedApiValue) => m.id === selectedModelId);
    const isNativeBinderGeneration = wizardMode === 'manual' && ['ppiflow', 'boltzgen'].includes(selectedModelId ?? '')
        && ['protein_binder', 'peptide_binder', 'antibody_binder', 'nanobody_binder'].includes(selectedModeId ?? '');
    // The default model list omits experimental entries. Explicit binder
    // handoffs resolve their own public definition, not a different model.
    const selectedNativeModelQuery = useQuery({
        queryKey: ['native-binder-model', selectedModelId],
        queryFn: () => fetchModelById(selectedModelId!),
        enabled: isNativeBinderGeneration && !listedSelectedModel,
    });
    const selectedModel = listedSelectedModel ?? (isNativeBinderGeneration ? selectedNativeModelQuery.data?.data : undefined);
    const selectedMode = selectedModel?.modes.find((m: UntypedApiValue) => m.id === selectedModeId);
    const nativeGenerationQuery = useQuery({
        queryKey: ['native-generation-settings', selectedModelId, selectedModeId],
        queryFn: () => fetchNativeGenerationInventory(selectedModelId as NativeBinderModel, selectedModeId!),
        enabled: isNativeBinderGeneration && !!selectedMode,
    });
    const nativeGenerationInventory = isNativeBinderGeneration ? nativeGenerationQuery.data : undefined;
    const resolvedFrustrampnnWorkflowId = useMemo(() => {
        if (wizardMode === 'manual') {
            return resolveFrustraMpnnWorkflowId(selectedModelId, selectedModeId);
        }
        if (!selectedTemplateId || isDedicatedLauncherTemplate(selectedTemplateId) || !templateDetail) {
            return null;
        }

        const launchParams = { ...templateDetail.preset_params, ...params };
        let modelId = launchParams.template_model_id;
        let modeId = launchParams.template_mode_id;
        if (!(modelId && modeId) && launchParams.rfd_mode) {
            modelId = 'rfdiffusion';
            modeId = launchParams.rfd_mode;
        } else if (
            !(modelId && modeId)
            && launchParams.diffusion_method === 'boltzgen'
            && ligands.some((ligand) => ligand.type === 'dna' || ligand.type === 'rna')
        ) {
            modelId = 'boltz2';
            modeId = 'complex';
        }
        return resolveFrustraMpnnWorkflowId(modelId, modeId);
    }, [wizardMode, selectedModelId, selectedModeId, selectedTemplateId, templateDetail, params, ligands]);
    const configuredFrustrampnnWorkflow = resolvedFrustrampnnWorkflowId
        ? frustrampnnIntegrationQuery.data?.workflows?.[resolvedFrustrampnnWorkflowId]
        : undefined;
    const frustrampnnConfigurationReady = !resolvedFrustrampnnWorkflowId || (
        !frustrampnnIntegrationQuery.isFetching
        && !frustrampnnIntegrationQuery.isError
        && configuredFrustrampnnWorkflow !== undefined
    );
    const runFrustrampnn = explicitRunFrustrampnn
        ?? configuredFrustrampnnWorkflow?.default_enabled
        ?? false;

    const initializedModelParams = useRef<{ id: string | null; clone: typeof clonedValues } | null>(null);
    const initializedTemplateParams = useRef<{ id: string | null; clone: typeof clonedValues } | null>(null);
    // Refreshes may fill missing defaults, never replace saved/operator values.
    useEffect(() => {
        if (wizardMode === 'manual' && selectedModel) {
            initializedTemplateParams.current = null;
            const defaults: Record<string, UntypedApiValue> = {};
            const definitions = isNativeBinderGeneration ? nativeGenerationInventory?.parameters || [] : selectedModel.params || [];
            const prior = initializedModelParams.current;
            const sameDraft = prior?.id === selectedModelId && prior?.clone === clonedValues;
            setParams(previous => {
                const saved = sameDraft ? previous : clonedValues || {};
                definitions.forEach((p: UntypedApiValue) => {
                    if (p.default !== undefined && !(p.aliases || []).some((alias: string) => Object.hasOwn(saved, alias))) defaults[p.name] = p.default;
                });
                return { ...defaults, ...saved };
            });
            initializedModelParams.current = { id: selectedModelId, clone: clonedValues };
        }
    }, [wizardMode, selectedModel, selectedModelId, clonedValues, nativeGenerationInventory, isNativeBinderGeneration]);

    // Initialize params when template changes (template mode)
    useEffect(() => {
        if (wizardMode !== 'manual' && templateDetail?.user_params) {
            initializedModelParams.current = null;
            const defaults: Record<string, UntypedApiValue> = {};
            templateDetail.user_params.forEach((p: UntypedApiValue) => {
                if (p.default !== undefined) defaults[p.name] = p.default;
            });
            const nextParams = { ...defaults, ...(clonedValues || {}) };
            const prior = initializedTemplateParams.current;
            const sameDraft = prior?.id === selectedTemplateId && prior?.clone === clonedValues;
            setParams(previous => sameDraft ? { ...defaults, ...previous } : nextParams);
            initializedTemplateParams.current = { id: selectedTemplateId, clone: clonedValues };
            const defaultTemplateName = nextParams.job_name || nextParams.name || nextParams.sequence_name || templateDetail.name || selectedTemplateId || '';
            if (defaultTemplateName && !sameDraft) {
                setJobName(prev => (clonedValues?.name || clonedValues?.job_name) ? String(defaultTemplateName) : (prev.trim() ? prev : String(defaultTemplateName)));
            }
        }
    }, [wizardMode, templateDetail, selectedTemplateId, clonedValues]);

    useEffect(() => {
        if (!selectedTemplateId || isDedicatedLauncherTemplate(selectedTemplateId)) {
            return;
        }
        const matchedTemplate = visibleApiTemplates.find((template: UntypedApiValue) => template.id === selectedTemplateId);
        if (matchedTemplate) {
            setWizardMode(matchedTemplate.experimental ? 'experimental' : 'templates');
        }
    }, [selectedTemplateId, visibleApiTemplates]);

    useEffect(() => {
        if (projectSetup.active && wizardMode === 'manual' && selectedModelId && ['boltzgen', 'ppiflow'].includes(selectedModelId)) {
            setProjectDraftValues({ ...nativeBinderDraft(params, jobName), model_id: selectedModelId, mode: selectedModeId, native_generation_authoring: true, binder_workflow_draft: binderDraftRef.current, binder_native_drafts: { ...binderNativeDrafts.current, [`${selectedModelId}:${selectedModeId}`]: nativeBinderDraft(params, jobName) } });
        }
    }, [projectSetup.active, wizardMode, selectedModelId, selectedModeId, params, jobName]);

    // Handle param change
    const updateParam = (key: string, value: UntypedApiValue) => {
        setParams(prev => {
            const next = { ...prev, [key]: value };
            if (selectedModelId && selectedModeId) binderNativeDrafts.current[`${selectedModelId}:${selectedModeId}`] = nativeBinderDraft(next, jobName);
            return next;
        });
    };

    const getTemplateIconLabel = (template: UntypedApiValue) => {
        if (template.id === 'protein_cad_experimental') return 'PC';


        if (template.id === 'boltz_cp_experimental') return 'CP';
        if (template.id === 'confornets_experimental') return 'CM';
        if (template.id === 'conformational_mapping') return 'CM';

        return template.icon === 'target' ? 'TG'
            : template.icon === 'flask' ? 'RF'
                : template.icon === 'dna' ? 'MU'
                    : template.icon === 'microscope' ? 'SP'
                        : template.icon === 'pill' ? 'BG'
                            : template.icon === 'binder' ? 'BC'
                                : template.icon === 'cube' ? 'PL'
                                    : 'OL';
    };

    const renderTemplateCard = (template: UntypedApiValue) => {
        const isSelected = selectedTemplateId === template.id;
        const docTopics = getTemplateDocumentationTopics(template);
        const docLinks = getModelDocumentationLinks(docTopics);
        return (
            <div
                key={template.id}
                onClick={() => handleTemplateCardSelect(template.id)}
                className={`cursor-pointer rounded-lg border-2 p-4 transition-all ${
                    template.experimental
                        ? isSelected
                            ? 'border-orange-400/60 bg-orange-500/10 shadow-xl'
                            : 'border-orange-500/25 bg-orange-500/5 hover:border-orange-400/50 hover:shadow-lg'
                        : isSelected
                            ? 'scale-[1.02] border-[var(--accent-primary)] shadow-xl'
                            : 'border-[var(--border-primary)] hover:scale-[1.01] hover:border-[var(--border-secondary)] hover:shadow-lg'
                } bg-[var(--card-bg)] text-[var(--text-primary)]`}
                style={{
                    boxShadow: isSelected
                        ? template.experimental
                            ? '0 10px 34px rgba(251, 146, 60, 0.18)'
                            : '0 8px 30px color-mix(in srgb, var(--accent-primary) 35%, transparent)'
                        : undefined
                }}
            >
                <div className="mb-2 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <div
                            className="flex h-10 w-10 items-center justify-center rounded text-sm font-bold"
                            style={{ backgroundColor: `${template.color}20`, color: template.color }}
                        >
                            {getTemplateIconLabel(template)}
                        </div>
                        <h3 className="font-bold text-base" style={{ color: template.color }}>{template.name}</h3>
                    </div>
                    {template.experimental && (
                        <span className="rounded-full border border-orange-400/25 bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-orange-300">
                            Experimental Alpha
                        </span>
                    )}
                </div>
                <p className="mb-2 text-xs opacity-70 line-clamp-2">{getCompactTemplateDescription(template)}</p>
                {docLinks.length > 0 && (
                    <div className="group/docs relative mb-2 inline-block" onClick={(event) => event.stopPropagation()}>
                        <span
                            className="inline-flex rounded-md border border-slate-600/80 bg-slate-900/70 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-300 transition-colors group-hover/docs:border-blue-400/60 group-hover/docs:text-blue-200"
                            aria-haspopup="true"
                        >
                            Docs ({docLinks.length})
                        </span>
                        <div
                            data-bms-workflow-doc-hover="true"
                            className="pointer-events-none absolute left-0 top-full z-30 mt-1 hidden min-w-56 max-w-[min(28rem,80vw)] flex-wrap gap-1 rounded-lg border border-slate-700/80 bg-slate-950/95 p-2 shadow-2xl group-hover/docs:flex group-hover/docs:pointer-events-auto"
                        >
                            {docLinks.map((link) => (
                                <a
                                    key={link.href}
                                    href={link.href}
                                    target="_blank"
                                    rel="noreferrer"
                                    onClick={(event) => event.stopPropagation()}
                                    className="inline-flex rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-[10px] font-medium text-slate-200 transition-colors hover:border-blue-400/60 hover:text-blue-200"
                                >
                                    {link.label}
                                </a>
                            ))}
                        </div>
                    </div>
                )}
                <div className="flex items-center gap-0.5 flex-wrap text-[10px]">
                    {template.stages.map((stage: UntypedApiValue, idx: number) => (
                        <div key={idx} className="flex items-center">
                            <span
                                className="rounded px-1.5 py-0.5 font-medium"
                                style={{ backgroundColor: `${template.color}15`, color: template.color }}
                            >
                                {stage.tool}
                            </span>
                            {idx < template.stages.length - 1 && (
                                <span className="mx-0.5 opacity-40" style={{ color: template.color }}>→</span>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        );
    };

    // Filter params for current mode
    const visibleParams = useMemo(() => nativeGenerationInventory?.parameters ?? (selectedModel?.params || []).filter((p: UntypedApiValue) => {
        if (!selectedMode) return false;
        if (selectedMode.params && selectedMode.params.length > 0) {
            return selectedMode.params.includes(p.name);
        }
        return !p.hidden;
    }) ?? [], [selectedMode, selectedModel?.params, nativeGenerationInventory]);

    // Group visible params by ui_group
    const groupedParams = useMemo(() => {
        const groups: Record<string, UntypedApiValue[]> = {};
        visibleParams.forEach((p: UntypedApiValue) => {
            const group = p.ui_group || 'General';
            if (!groups[group]) groups[group] = [];
            groups[group].push(p);
        });
        // Sort params within each group by ui_order
        Object.values(groups).forEach(grp => {
            grp.sort((a, b) => (a.ui_order ?? 99) - (b.ui_order ?? 99));
        });
        return groups;
    }, [visibleParams]);

    // Check if ready to submit - works for both template mode and manual mode
    const isTemplateMode = wizardMode === 'templates' || wizardMode === 'experimental';
    const templateLaunchName = String(jobName || params.job_name || params.sequence_name || templateDetail?.name || selectedTemplateId || '').trim();

    const visibleTemplateParams = useMemo(() => {
        if (!templateDetail?.user_params) return [];
        return templateDetail.user_params.filter((param: UntypedApiValue) => {
            if (!param.condition) return true;
            const controllingParam = templateDetail.user_params.find((p: UntypedApiValue) => p.name === param.condition.param);
            const controllingValue = params[param.condition.param] !== undefined
                ? params[param.condition.param]
                : controllingParam?.default;
            return param.condition.values.includes(controllingValue);
        });
    }, [templateDetail, params]);

    const groupedTemplateParams = useMemo(() => {
        const groups: Record<string, UntypedApiValue[]> = {};
        visibleTemplateParams.forEach((p: UntypedApiValue) => {
            const group = p.ui_group || 'General';
            if (!groups[group]) groups[group] = [];
            groups[group].push(p);
        });
        Object.values(groups).forEach(grp => {
            grp.sort((a, b) => (a.ui_order ?? 99) - (b.ui_order ?? 99));
        });
        return groups;
    }, [visibleTemplateParams]);

    const templateManagerParams = useMemo(() => {
        const baseParams = isTemplateMode && templateDetail
            ? {
                ...(templateDetail.preset_params || {}),
                ...params,
                job_name: templateLaunchName,
            }
            : params;
        return configuredFrustrampnnWorkflow
            ? mergeFrustraMpnnLaunchParams(fampnnUserParams(baseParams), runFrustrampnn, frustrampnnSettings)
            : fampnnUserParams(baseParams);
    }, [
        isTemplateMode,
        params,
        templateDetail,
        templateLaunchName,
        configuredFrustrampnnWorkflow,
        runFrustrampnn,
        frustrampnnSettings,
    ]);

    const missingRequiredTemplateParams = isTemplateMode && templateDetail?.user_params
        ? templateDetail.user_params
            .filter((param: UntypedApiValue) => param.required)
            .filter((param: UntypedApiValue) => {
                const value = params[param.name] ?? param.default;
                return value === undefined || value === null || String(value).trim() === '';
            })
            .map((param: UntypedApiValue) => param.label || param.name)
        : [];
    const fampnnLaunchParams = isTemplateMode ? { ...templateDetail?.preset_params, ...params } : params;
    const usesFampnn = wizardMode === 'manual' && selectedModelId === 'fampnn';
    // General RFD3 is backbone-only, BoltzGen skips sequence design, and the
    // legacy RFdiffusion FA-MPNN parent is not in the admitted caller inventory.
    const unsupportedFampnnParent = isTemplateMode && !isDedicatedLauncherTemplate(selectedTemplateId)
        && (fampnnLaunchParams.seq_method === 'fampnn' || fampnnLaunchParams.seq_design_fampnn === true);
    const activeFampnnOverrides = fampnnLaunchParams.fampnn_analysis_overrides;
    if (usesFampnn) {
        try { hydrateFampnnOverrides(activeFampnnOverrides); } catch (error) { fampnnError = String(error); }
    } else { fampnnError = ''; }
    if (unsupportedFampnnParent) fampnnError = 'This general/binder template is not an admitted FA-MPNN caller. Launch remains disabled; use the standalone or local-redesign launcher.';
    const genericFampnnControl = usesFampnn ? <FampnnAnalysisControls value={activeFampnnOverrides} onChange={setFampnnOverrides}
        summaryDefault="Declared input protein residues"
        mutationDefault="Declared input protein residues" /> : null;
    const allMissingRequiredTemplateParams = missingRequiredTemplateParams;
    const nativeBinderModeMissing = wizardMode === 'manual' && ['boltzgen', 'ppiflow'].includes(selectedModelId ?? '') && !selectedMode;
    const isReady = !nativeBinderModeMissing && !fampnnError && frustrampnnConfigurationReady && Boolean(
        (isTemplateMode && selectedTemplateId && templateLaunchName && templateDetail && allMissingRequiredTemplateParams.length === 0) ||
        (wizardMode === 'manual' && jobName && selectedModelId && selectedModeId)
    );
    const launchBlockedReason = nativeBinderModeMissing
        ? 'The selected native mode is not advertised by the current model registry.'
        : !frustrampnnConfigurationReady
        ? 'FrustraMPNN integration configuration is unavailable. Launch is blocked.'
        : !isReady
        ? (isTemplateMode && selectedTemplateId && allMissingRequiredTemplateParams.length > 0
            ? `Required: ${allMissingRequiredTemplateParams.join(', ')}`
            : 'Select a workflow and complete required fields')
        : '';

    const buildWorkflowRequest = (): Partial<Job> | null => {
        // Get template data - handle both axios response wrapper and direct data
        const templateData = templateDetail;

        if (isTemplateMode && templateData) {
            // Template mode: merge preset params with user params
            const mergedParams = fampnnUserParams({ ...templateData.preset_params, ...params });
            delete mergedParams.fampnn_analysis_overrides;
            const templateModelIdOverride = mergedParams.template_model_id;
            const templateModeIdOverride = mergedParams.template_mode_id;
            delete mergedParams.template_model_id;
            delete mergedParams.template_mode_id;

            // Determine the Nextflow profile based on template type
            // Priority: rfd_mode (binder/monomer) > diffusion_method (boltzgen) > pred_method (structure prediction/validation) > skip_rfd (fampnn_predict)
            let nextflowProfile = '';
            let effectiveModelId = 'template_' + (selectedTemplateId || 'unknown');

            if (templateModelIdOverride && templateModeIdOverride) {
                effectiveModelId = templateModelIdOverride;
                nextflowProfile = templateModeIdOverride;
            } else if (mergedParams.rfd_mode) {
                // Binder or monomer design templates
                nextflowProfile = mergedParams.rfd_mode;
                effectiveModelId = 'rfdiffusion';
            } else if (mergedParams.diffusion_method === 'boltzgen') {
                // Check if this is complex PREDICTION (DNA/RNA present) vs DESIGN
                const hasNucleicAcid = ligands.some(l => l.type === 'dna' || l.type === 'rna');
                if (hasNucleicAcid) {
                    // DNA/RNA complex prediction - use Boltz-2, NOT BoltzGen
                    nextflowProfile = 'complex';
                    effectiveModelId = 'boltz2';
                } else {
                    // BoltzGen ligand-aware binder design template
                    nextflowProfile = 'boltzgen';
                    effectiveModelId = 'boltzgen';
                }
            } else if (mergedParams.pred_method) {
                // Structure prediction templates - map pred_method to model_id and mode
                const structurePredictionMethodMap: Record<string, { model_id: string; mode: string }> = {
                    boltz: { model_id: 'boltz2', mode: 'predict' },
                    fold_cp: { model_id: 'boltz_cp_experimental', mode: 'design' },
                    boltz_api: { model_id: 'boltz_api', mode: 'predict' },
                    protenix: { model_id: 'protenix', mode: 'predict' },
                    esmfold2: { model_id: 'esmfold2', mode: 'predict' },
                    boltz_protenix: { model_id: 'boltz2', mode: 'complex' },
                };
                const compatibilityMethodMap: Record<string, { model_id: string; mode: string }> = {
                    boltz: { model_id: 'boltz2', mode: 'predict' },
                    rf3: { model_id: 'rf3', mode: 'predict' },
                    protenix: { model_id: 'protenix', mode: 'predict' },
                    both: { model_id: 'boltz2', mode: 'predict' },
                    all: { model_id: 'boltz2', mode: 'predict' },
                };
                const predMethodMap = selectedTemplateId === 'structure_prediction'
                    ? structurePredictionMethodMap
                    : compatibilityMethodMap;
                const mapping = predMethodMap[mergedParams.pred_method];
                if (mapping) {
                    effectiveModelId = mapping.model_id;
                    nextflowProfile = mapping.mode;
                } else if (selectedTemplateId === 'structure_prediction') {
                    return null;
                } else {
                    nextflowProfile = mergedParams.pred_method;
                }
            } else if (mergedParams.skip_rfd === true) {
                // DNA polymerase or similar - skip diffusion, just sequence design + prediction
                nextflowProfile = 'fampnn_predict';
                effectiveModelId = 'proteinmpnn';
            } else {
                // Fallback to template ID
                nextflowProfile = selectedTemplateId || 'binder_denovo';
            }

            const governedMergedParams = resolvedFrustrampnnWorkflowId
                ? mergeFrustraMpnnLaunchParams(mergedParams, runFrustrampnn, frustrampnnSettings)
                : mergedParams;


            // Add complex_components if ligands are selected
            const finalParams = ligands.length > 0 ? {
                ...governedMergedParams,
                complex_components: [
                    { type: 'protein', id: 'A', sequence: governedMergedParams.sequence || params.sequence },
                    ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles, sequence: l.sequence, name: l.name }))
                ]
            } : governedMergedParams;

            return {
                name: templateLaunchName,
                model_id: effectiveModelId,
                mode: nextflowProfile,
                params: finalParams,
            };
        } else if (selectedModelId && selectedModeId) {
            if (nativeBinderModeMissing) return null;
            // Manual mode

            // Filter params to only include those defined in the selected mode
            const filteredParams: Record<string, UntypedApiValue> = {};
            if (selectedMode && selectedMode.params) {
                (nativeGenerationInventory?.parameters.flatMap(parameter => [parameter.name, ...(parameter.aliases || [])]) || selectedMode.params).forEach((paramName: string) => {
                    // Empty native sequence settings (e.g. omit_AAs or a
                    // cleared optional target chain) are values, not defaults.
                    const preserveNativeEmpty = ['fampnn', 'proteinmpnn', 'boltzgen', 'ppiflow'].includes(selectedModelId);
                    if (params[paramName] !== undefined && (params[paramName] !== '' || preserveNativeEmpty)) {
                        filteredParams[paramName] = params[paramName];
                    }
                });
            } else {
                // Fallback if no params defined in mode (shouldn't happen for well-defined models)
                Object.assign(filteredParams, params);
            }

            for (const key of Object.keys(filteredParams)) {
                if (!(key in fampnnUserParams(filteredParams)) || key === 'fampnn_analysis_overrides') delete filteredParams[key];
            }

            // specific check for ntp_type to ensure it's not sent if empty even if in params list
            if (filteredParams['ntp_type'] === '') {
                delete filteredParams['ntp_type'];
            }

            const governedFilteredParams = resolvedFrustrampnnWorkflowId
                ? mergeFrustraMpnnLaunchParams(filteredParams, runFrustrampnn, frustrampnnSettings)
                : filteredParams;

            // Add complex_components if ligands are selected (e.g. for Complex Prediction)
            const proteinSeq = governedFilteredParams.sequence || governedFilteredParams.protein_sequence;

            const finalParams = ligands.length > 0 ? {
                ...governedFilteredParams,
                complex_components: [
                    { type: 'protein', id: 'A', sequence: proteinSeq },
                    ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles, sequence: l.sequence, name: l.name }))
                ]
            } : governedFilteredParams;

            return {
                name: jobName,
                model_id: selectedModelId,
                mode: selectedModeId,
                params: finalParams,
                ...(selectedModelId === 'fampnn' ? fampnnOverridePayload(fampnnOverrides) : {}),
            };
        }
        return null;
    };
    // Dedicated forms own separate live state; don't substitute a generic request.
    const workflowRequest = (isTemplateMode && isDedicatedLauncherTemplate(selectedTemplateId)) || !frustrampnnConfigurationReady || (usesFampnn && !!fampnnError) ? null : buildWorkflowRequest();
    const handleSubmit = () => {
        if (!isReady) return;
        const request = buildWorkflowRequest();
        if (request) submitMutation.mutate(request);
    };
    const genericFrustrampnnControl = resolvedFrustrampnnWorkflowId ? (
        <div
            className="rounded-xl border border-cyan-900/70 bg-cyan-950/15 p-4"
            data-job-submission-frustrampnn
        >
            {configuredFrustrampnnWorkflow ? (
                <ModelIntegrationControl
                    modelId="frustrampnn"
                    workflowId={resolvedFrustrampnnWorkflowId}
                    checked={runFrustrampnn}
                    onChange={(checked) => {
                        setExplicitRunFrustrampnn(checked);
                        setParams((previous) => ({ ...previous, run_frustrampnn: checked }));
                    }}
                    fallbackLabel="Frustration analysis"
                    integration={frustrampnnIntegrationQuery.data}
                    settingsControl={(
                        <FrustraMpnnSettingsPanel
                            value={frustrampnnSettings}
                            onChange={(settings) => {
                                setFrustrampnnSettings(settings);
                                setParams((previous) => ({ ...previous, frustrampnn_settings: settings }));
                            }}
                        />
                    )}
                />
            ) : (
                <p role="alert" className="text-sm text-amber-300">
                    FrustraMPNN integration configuration is unavailable. Launch is blocked.
                </p>
            )}
        </div>
    ) : null;

    // Dedicated templates that handle their own header/navigation
    const dedicatedTemplates = ['mutagenesis', 'antibody_denovo', 'structure_prediction', 'oligo_design', 'protein_modification_experimental', 'molecular_dynamics', 'conformational_mapping'];
    const showMainHeader = !isNativeBinderGeneration && (!selectedTemplateId || !dedicatedTemplates.includes(selectedTemplateId));

    const preparedStructureModelId = launchContextQuery.data?.pinned_scheduler?.model_id;
    const isPreparedStructureFamily = ['boltz2', 'protenix'].includes(String(preparedStructureModelId));
    const preparedStructureScheduler = (() => {
        const context = launchContextQuery.data;
        const scheduler = context?.pinned_scheduler;
        if (!scheduler || typeof scheduler !== 'object' || Array.isArray(scheduler)) return null;
        const modelId = scheduler.model_id;
        const mode = scheduler.mode;
        const name = scheduler.name;
        const pinnedParams = scheduler.params;
        if (
            !context?.launch_context_id
            || context.state !== 'reserved'
            || !['boltz2', 'protenix'].includes(String(modelId))
            || mode !== 'predict'
            || typeof name !== 'string'
            || !name.trim()
            || !pinnedParams
            || typeof pinnedParams !== 'object'
            || Array.isArray(pinnedParams)
        ) return null;
        return {
            context,
            name,
            modelId: String(modelId),
            mode: String(mode),
            params: pinnedParams as Record<string, UntypedApiValue>,
        };
    })();
    const preparedStructureRequest = preparedStructureScheduler ? {
                name: preparedStructureScheduler.name,
                model_id: preparedStructureScheduler.modelId,
                mode: preparedStructureScheduler.mode,
                params: preparedStructureScheduler.params,
                launch_context_id: preparedStructureScheduler.context.launch_context_id,
                ...(preparedStructureScheduler.context.pinned_gpu === null
                    ? {}
                    : { pinned_gpu: preparedStructureScheduler.context.pinned_gpu }),
            } : null;
    const submitPreparedStructure = useMutation({
        mutationFn: async () => {
            if (!preparedStructureRequest) {
                throw new Error('The prepared Project structure-prediction scheduler is unavailable.');
            }
            const response = await submitJob(preparedStructureRequest);
            return {
                response: response.data,
                returnUri: await completeCurrentLaunchContext(response.data),
            };
        },
        onSuccess: ({ returnUri }) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            navigate(returnUri ?? '/');
        },
    });

    if (preparedStructureScheduler && !projectSetup.active) {
        return (
            <div className="min-h-screen bg-slate-950 p-6 text-slate-100">
                <main className="mx-auto max-w-4xl space-y-5">
                    <section className="rounded-2xl border border-blue-500/40 bg-blue-950/30 p-5">
                        <h1 className="text-xl font-semibold">Confirm prepared Project structure prediction</h1>
                        <ExecutionTargetPicker workflowRequest={preparedStructureRequest} />
                        <p className="mt-2 text-sm text-blue-100/80">
                            This request is immutable. BioModStack will submit the exact server-pinned scheduler values; the native structure form cannot recompute or edit them.
                        </p>
                        <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2">
                            <div><dt className="text-slate-400">Project</dt><dd className="break-all font-mono">{preparedStructureScheduler.context.project_id}</dd></div>
                            <div><dt className="text-slate-400">Preparation</dt><dd className="break-all font-mono">{preparedStructureScheduler.context.preparation_id}</dd></div>
                            <div><dt className="text-slate-400">Model / mode</dt><dd className="font-mono">{preparedStructureScheduler.modelId} / {preparedStructureScheduler.mode}</dd></div>
                            <div><dt className="text-slate-400">Job name</dt><dd>{preparedStructureScheduler.name}</dd></div>
                        </dl>
                    </section>
                    <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
                        <h2 className="text-sm font-semibold">Exact pinned scientific request</h2>
                        <pre className="mt-3 max-h-[32rem] overflow-auto rounded-lg bg-slate-950 p-4 text-xs text-slate-300">{JSON.stringify(preparedStructureScheduler.params, null, 2)}</pre>
                    </section>
                    {submitPreparedStructure.error && <p role="alert" className="rounded-lg border border-red-500/40 bg-red-950/40 p-3 text-sm text-red-100">{String(submitPreparedStructure.error instanceof Error ? submitPreparedStructure.error.message : submitPreparedStructure.error)}</p>}
                    <button
                        type="button"
                        disabled={submitPreparedStructure.isPending}
                        onClick={() => submitPreparedStructure.mutate()}
                        className="rounded-lg bg-blue-500 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                    >
                        {submitPreparedStructure.isPending ? 'Submitting exact prepared request…' : 'Submit exact prepared request'}
                    </button>
                </main>
            </div>
        );
    }
    if (launchContextId && launchContextQuery.isLoading) {
        return <div className="min-h-screen bg-slate-950 p-6 text-slate-100"><main className="mx-auto max-w-3xl rounded-2xl border border-blue-500/40 bg-blue-950/30 p-5">Resolving immutable Project launch authority…</main></div>;
    }
    if (launchContextId && launchContextQuery.isError) {
        return <div className="min-h-screen bg-slate-950 p-6 text-slate-100"><main role="alert" className="mx-auto max-w-3xl rounded-2xl border border-red-500/40 bg-red-950/40 p-5">Project launch is blocked because the launch context is invalid, expired, claimed, or unavailable.</main></div>;
    }
    if (isPreparedStructureFamily) {
        return <div className="min-h-screen bg-slate-950 p-6 text-slate-100"><main role="alert" className="mx-auto max-w-3xl rounded-2xl border border-red-500/40 bg-red-950/40 p-5">Project launch is blocked because the prepared Boltz-2/Protenix scheduler authority is incomplete or is not reserved by a Project Run Group.</main></div>;
    }
    if (projectSetup.active && (projectSetup.isLoading || (projectSetup.setup && hydratedProjectSetup.current !== `${projectSetup.setup.project_id}:${projectSetup.setup.setup_context_id}`))) {
        return <div className="min-h-screen bg-slate-950 p-6 text-slate-100">Loading Project workflow setup…</div>;
    }
    if (projectSetup.active && (projectSetup.error || !projectSetup.setup)) {
        return <div role="alert" className="min-h-screen bg-slate-950 p-6 text-red-200">Project workflow setup is unavailable or invalid.</div>;
    }

    return (
        <div className="min-h-screen bg-slate-950 p-6">
            {projectSetup.setup && <><ProjectWorkflowSetupBanner setup={projectSetup.setup}/><section className="mx-auto mb-4 mt-4 flex max-w-[104rem] flex-wrap items-center gap-2 rounded-xl border border-blue-500/30 bg-blue-950/20 p-3"><button type="button" className="rounded-lg border border-blue-400 px-3 py-2 text-xs font-semibold text-blue-200" disabled={projectActionBusy} onClick={async () => {
                setProjectActionBusy(true); setProjectActionError(null);
                try { await projectSetup.saveDraft(projectDraftValues as JsonObject); }
                catch (error) { setProjectActionError(error instanceof Error ? error.message : String(error)); }
                finally { setProjectActionBusy(false); }
            }}>Save draft</button>{selectedTemplateId === 'antibody_denovo'
                ? <span className="text-xs text-blue-200">Launch with the native editor below; this Project remains the destination.</span>
                : <button type="button" className="rounded-lg bg-blue-500 px-3 py-2 text-xs font-semibold text-white" disabled={projectActionBusy || (isNativeBinderGeneration && !isReady)} onClick={async () => {
                    if (isNativeBinderGeneration) { handleSubmit(); return; }
                    setProjectActionBusy(true); setProjectActionError(null);
                    try { await projectSetup.startRun(projectDraftValues as JsonObject); }
                    catch (error) { setProjectActionError(error instanceof Error ? error.message : String(error)); }
                    finally { setProjectActionBusy(false); }
                }}>Start run</button>}{projectActionError && <p role="alert">{projectActionError}</p>}<ProjectTechnicalDetails setup={projectSetup.setup}/></section></>}
            {!(isTemplateMode && ['structure_prediction', 'mutagenesis', 'antibody_denovo', 'oligo_design', 'protein_modification_experimental', 'molecular_dynamics'].includes(selectedTemplateId ?? '')) && <ExecutionTargetPicker workflowRequest={workflowRequest} />}
            <ExecutionPolicyControl initialPolicy={initialReturnPolicy} />
            {launchContextId && (
                <aside className="mb-4 rounded-lg border border-blue-500/40 bg-blue-950/40 px-4 py-3 text-sm text-blue-100" aria-label="Project launch destination">
                    {launchContextQuery.isLoading && 'Resolving Project launch destination…'}
                    {launchContextQuery.isError && 'Project launch destination is invalid, expired, claimed, or unavailable.'}
                    {launchContextQuery.data && (
                        <>
                            <div className="font-semibold">Verified Project launch destination</div>
                            <div className="mt-1 font-mono text-xs">
                                Project {launchContextQuery.data.project_id} · Global Experiment {launchContextQuery.data.global_experiment_id} · Domain Experiment {launchContextQuery.data.domain_experiment_id}
                            </div>
                        </>
                    )}
                </aside>
            )}
            {mdHandoff.error && (
                <aside role="alert" className="mb-4 rounded-lg border border-red-500/40 bg-red-950/40 px-4 py-3 text-sm text-red-100">
                    {mdHandoff.error}
                </aside>
            )}
            {/* Main header - hidden when dedicated templates are active */}
            {showMainHeader && (
                <header className="mb-8 flex items-center gap-4">
                    <Link
                        to="/"
                        className="inline-flex items-center rounded-lg border border-slate-700 px-3 py-2 text-sm font-medium text-slate-300 transition-colors hover:bg-slate-800 hover:text-white"
                    >
                        Back
                    </Link>
                    <div>
                        <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-400 to-accent bg-clip-text text-transparent">
                            New Experiment
                        </h1>
                        <p className="text-slate-400 text-sm">Configure and launch a new job</p>
                    </div>
                </header>
            )}

            <main className={`${selectedTemplateId === 'protein_modification_experimental'
                ? 'max-w-none'
                : 'max-w-[104rem]'} mx-auto space-y-8`}>

                {/* 2. Mode Toggle: workflow cards only; the raw model-picker tab stays hidden for now. */}
                <section>
                    <div className="flex gap-2 mb-4">
                        <button
                            onClick={() => {
                                setWizardMode('templates');
                                setSelectedModelId(null);
                                setSelectedModeId(null);
                                setSelectedTemplateId(null);
                                setClonedValues(undefined);
                            }}
                            className={`min-w-[9.5rem] rounded-lg border px-4 py-2.5 text-sm font-medium transition-all ${wizardMode === 'templates'
                                ? 'border-blue-500/40 bg-blue-500/15 text-blue-300'
                                : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800'
                                }`}
                        >
                            Workflows
                        </button>
                        <button
                            onClick={() => {
                                setWizardMode('experimental');
                                setSelectedModelId(null);
                                setSelectedModeId(null);
                                setSelectedTemplateId(null);
                                setClonedValues(undefined);
                            }}
                            className={`min-w-[9.5rem] rounded-lg border px-4 py-2.5 text-sm font-medium transition-all ${wizardMode === 'experimental'
                                ? 'border-orange-400/40 bg-orange-500/12 text-orange-300'
                                : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800'
                                }`}
                        >
                            Experimental
                        </button>
                    </div>

                    {/* Templates Mode */}
                    {(wizardMode === 'templates' || wizardMode === 'experimental') && (
                        <div className="space-y-4">
                            {selectedTemplateId === 'mutagenesis' ? (
                                <MutagenesisTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    onSubmit={async (jobNamePrefix, variants, predictorConfig) => {
                                        // MUTAGENESIS BATCH: Single API call with all variants
                                        // Each variant regenerates its own MSA (no shared reference MSA)

                                        console.log('[MUTAGENESIS BATCH] Submitting', variants.length, 'variants as single batch');
                                        if (predictorConfig.msa_reference_sequence) {
                                            console.log('[MUTAGENESIS BATCH] Ignoring reference MSA (mutants regenerate MSAs)');
                                        }


                                        try {
                                            await submitMutation.mutateAsync(buildMutagenesisWorkflowRequest(jobNamePrefix, variants, predictorConfig));
                                            queryClient.invalidateQueries({ queryKey: ['jobs'] });
                                        } catch (error) {
                                            console.error("[MUTAGENESIS BATCH] Submission failed", error);
                                        }
                                    }}
                                />
                            ) : selectedTemplateId === 'antibody_denovo' && clonedValues?.model_id === 'bindcraft2' && clonedValues.mode && clonedValues.mode !== 'campaign' ? (
                                <BindCraft2LifecycleDraft key={`bc2-action:${dedicatedTemplateVersion}:${clonedValues.mode}`}
                                    initialValues={clonedValues} onBack={handleDedicatedTemplateBack}
                                    onDraftChange={draft => { binderDraftRef.current = draft; if (projectSetup.active) setProjectDraftValues(draft); }}
                                    onSubmitRequest={submitBinderRequest} />
                            ) : selectedTemplateId === 'antibody_denovo' ? (
                                <AntibodyDenovoTemplate
                                    key={`antibody_denovo:${dedicatedTemplateVersion}`}
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                    initialDraft={binderInitialDraft}
                                    initialEngineChooserOpen={engineChooserOpen}
                                    onSubmitRequest={submitBinderRequest}
                                    onDraftChange={draft => {
                                        binderDraftRef.current = draft;
                                        if (projectSetup.active) setProjectDraftValues({ ...draft, binder_native_drafts: binderNativeDrafts.current });
                                    }}
                                    onOpenNativeRoute={route => {
                                        setBinderInitialDraft(binderDraftRef.current);
                                        const destinationKey = 'templateId' in route ? route.templateId : `${route.modelId}:${route.mode}`;
                                        const inherited: Record<string, UntypedApiValue> = {};
                                        // Only native fields shared by these contracts inherit sources.
                                        // Never turn context into a seed complex or infer binder chain roles.
                                        if ('modelId' in route && ['boltzgen', 'ppiflow'].includes(route.modelId) && ['protein_binder', 'antibody_binder', 'nanobody_binder', 'peptide', 'peptide_binder'].includes(route.mode)) {
                                            const target = route.sources?.target;
                                            if (target) {
                                                // Keep source identity/role context in the UI draft. The
                                                // registry whitelist below remains the scientific request.
                                                if (target.reference) inherited.target_source = target.reference;
                                                if ('modelNumber' in target) inherited.target_model_number = target.modelNumber;
                                                if ('chain' in target) inherited.selected_chain = target.chain;
                                                if (target.chain && route.modelId === 'boltzgen') inherited.target_chains = target.chain;
                                                if (target.chain && route.modelId === 'ppiflow' && route.mode === 'protein_binder') inherited.target_chain = target.chain;
                                                if ('residues' in target) inherited.selected_residues = target.residues;
                                                // A nonprimary model needs a source-owned materialized
                                                // document; copying the whole file would change intent.
                                                if (target.modelNumber == null || target.modelNumber === 1) inherited.target_pdb = target.path;
                                            }
                                            if (route.modelId === 'ppiflow' && ['antibody_binder', 'nanobody_binder'].includes(route.mode)) {
                                                if (route.sources?.framework) inherited.framework_pdb = route.sources.framework.path;
                                                if (route.sources?.target?.chain) inherited.antigen_chain = route.sources.target.chain;
                                            }
                                        }
                                        const destination = { ...inherited, ...(binderNativeDrafts.current[destinationKey] ?? {}), ...route.initialDraft };
                                        setClonedValues(destination);
                                        setParams(destination);
                                        if (typeof destination.job_name === 'string') setJobName(destination.job_name);
                                        if ('templateId' in route) {
                                            setWizardMode('experimental');
                                            setSelectedTemplateId(route.templateId);
                                        } else {
                                            setWizardMode('manual');
                                            setSelectedTemplateId(null);
                                            setSelectedModelId(route.modelId);
                                            setSelectedModeId(route.mode);
                                        }
                                    }}
                                />
                            ) : selectedTemplateId === 'structure_prediction' ? (
                                <StructurePredictionTemplate
                                    key={`${selectedTemplateId}:${dedicatedTemplateVersion}`}
                                    onBack={handleDedicatedTemplateBack}
                                    onOpenTemplateManager={openTemplateManager}
                                    initialValues={clonedValues}
                                    onDraftChange={projectSetup.active ? setProjectDraftValues : undefined}
                                    sourceSequenceId={mdHandoff.route?.sourceSequenceId ?? null}
                                    mdDraftId={mdHandoff.route?.draftId ?? null}
                                    returnTemplate={mdHandoff.route?.returnTemplate ?? null}
                                />
                            ) : selectedTemplateId === 'oligo_design' ? (
                                <OligoDesignerTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                />
                            ) : selectedTemplateId === 'protein_modification_experimental' ? (
                                <ProteinModificationTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                    requiredPinnedGpu={launchContextQuery.data?.pinned_gpu ?? null}
                                    onDraftChange={projectSetup.active ? setProjectDraftValues : undefined}
                                />

                            ) : selectedTemplateId === 'molecular_dynamics' ? (
                                <MolecularDynamicsTemplate
                                    key={`molecular_dynamics:${dedicatedTemplateVersion}`}
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={molecularDynamicsInitialValues}
                                    launchContextId={launchContextId}
                                    onOpenStructurePrediction={openMdStructurePrediction}
                                />
                            ) : selectedTemplateId === 'conformational_mapping' ? (
                                <ConformationalMappingLauncher
                                    key={`conformational_mapping:${dedicatedTemplateVersion}`}
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={{
                                        ...(getDedicatedTemplateInitialValues('conformational_mapping') || {}),
                                        ...(templateDetail?.preset_params || {}),
                                        ...(clonedValues || {}),
                                    }}
                                    onDraftChange={projectSetup.active ? setProjectDraftValues : undefined}
                                />
                            ) : (
                                <>
                                    <p className="text-slate-300 text-base font-medium mb-4">
                                        {wizardMode === 'experimental'
                                            ? 'Choose active alpha:'
                                            : 'Choose workflow:'}
                                    </p>
                                    {wizardMode === 'experimental' && (
                                        <div className="rounded-xl border border-orange-400/20 bg-orange-500/8 px-4 py-3 text-sm text-orange-100">
                                            <span className="font-semibold text-orange-200">Alpha:</span> launch controls here; method docs linked.
                                        </div>
                                    )}
                                    <div className="grid grid-cols-2 gap-3">
                                        {(wizardMode === 'experimental' ? experimentalTemplateCards : workflowTemplateCards).map((template: UntypedApiValue) =>
                                            renderTemplateCard(template)
                                        )}
                                    </div>
                                    {wizardMode === 'experimental' && experimentalTemplateCards.length === 0 && (
                                        <div className="rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-5 text-sm text-slate-400">
                                            No experimental workflows are currently exposed in this branch.
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    )}

                </section>

                {/* 3. Template Configuration - Only show if template selected and NOT a dedicated template */}
                {selectedTemplateId && !isDedicatedLauncherTemplate(selectedTemplateId) && templateDetail && (
                    <section className="animate-in fade-in slide-in-from-bottom-4 duration-500" data-bms-template-config-shell="true">
                        <div className="rounded-2xl border border-slate-700 bg-slate-900/45 p-6 shadow-xl">
                            <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
                                <div>
                                    <button
                                        type="button"
                                        onClick={handleDedicatedTemplateBack}
                                        className="mb-3 inline-flex items-center rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800 hover:text-white"
                                    >
                                        ← Back to workflows
                                    </button>
                                    <h2 className="flex items-center gap-2 text-xl font-semibold text-slate-100">
                                        <span className={`h-6 w-1.5 rounded-full ${templateDetail.experimental ? 'bg-orange-400' : 'bg-emerald-500'}`} />
                                        {templateDetail.name}
                                        {templateDetail.experimental && (
                                            <span className="rounded-full border border-orange-400/25 bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-orange-300">
                                                Experimental Alpha
                                            </span>
                                        )}
                                    </h2>
                                    <p className="mt-1 max-w-3xl text-sm text-slate-400">
                                        {compactUiCopy(templateDetail.description || templateDetail.goal || '', 170)}
                                    </p>
                                </div>
                                <ModelDocumentationLinks
                                    topics={getTemplateDocumentationTopics(templateDetail, params)}
                                    summary="Model docs update from the selected workflow model; launch controls stay here."
                                    compact
                                    className="min-w-[18rem] flex-1 md:max-w-xl"
                                />
                            </div>

                            <div className="mb-6 flex flex-wrap items-center gap-2 border-y border-slate-800 py-3 text-[11px]">
                                {templateDetail.stages.map((stage: UntypedApiValue, idx: number) => (
                                    <div key={idx} className="flex items-center gap-2">
                                        <span className="rounded-md bg-slate-800 px-2 py-1 font-medium text-slate-300">
                                            {stage.tool}
                                        </span>
                                        {idx < templateDetail.stages.length - 1 && (
                                            <span className="text-slate-600">→</span>
                                        )}
                                    </div>
                                ))}
                            </div>

                            <div className="space-y-6">
                                {['Inputs', 'Mapping Mode', 'Model Orchestration', 'Outputs', 'Advanced'].filter(group => groupedTemplateParams[group]).map((groupName) => (
                                    <div key={groupName} className={groupName === 'Advanced' ? 'rounded-xl border border-slate-700/60' : ''}>
                                        {groupName === 'Advanced' ? (
                                            <button
                                                type="button"
                                                onClick={() => setShowAdvanced(!showAdvanced)}
                                                className="flex w-full items-center justify-between rounded-xl bg-slate-800/45 px-4 py-3 text-left transition-colors hover:bg-slate-800/70"
                                            >
                                                <span className="flex items-center gap-2 text-sm font-medium text-slate-300">
                                                    <span className="h-4 w-1 rounded-full bg-slate-500" />
                                                    Advanced / Runtime Paths
                                                </span>
                                                <span className="text-xs text-slate-500">{showAdvanced ? '▲' : '▼'}</span>
                                            </button>
                                        ) : (
                                            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-300">
                                                <span className={`h-4 w-1 rounded-full ${groupName === 'Inputs' ? 'bg-emerald-500' : groupName === 'Outputs' ? 'bg-purple-500' : 'bg-blue-500'}`} />
                                                {groupName}
                                            </h3>
                                        )}
                                        {(groupName !== 'Advanced' || showAdvanced) && (
                                            <div className={`${groupName === 'Advanced' ? 'p-4 ' : ''}grid grid-cols-1 gap-4 md:grid-cols-2`}>
                                                {groupedTemplateParams[groupName].map((param: UntypedApiValue) => (
                                                    <ParamField
                                                        key={param.name}
                                                        param={param}
                                                        params={params}
                                                        updateParam={updateParam}
                                                        setShowFileBrowser={setShowFileBrowser}
                                                        setActiveSequenceField={setActiveSequenceField}
                                                        setShowSequenceManager={setShowSequenceManager}
                                                        setSequenceToSave={setSequenceToSave}
                                                        ligandPresets={ligandPresets}
                                                    />
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>

                            {unsupportedFampnnParent && <p role="alert">{fampnnError}</p>}
                            {genericFrustrampnnControl}

                            {(templateDetail?.preset_params?.pred_method ||
                                selectedTemplateId?.includes('structure') ||
                                selectedTemplateId?.includes('predict')) && (
                                    <LigandSelector
                                        ligands={ligands}
                                        setLigands={setLigands}
                                        showCustomSmiles={true}
                                    />
                                )}
                        </div>
                    </section>
                )}

                {/* 4. Configure - Only show if model selected (Manual Mode) */}
                {wizardMode === 'manual' && selectedModel && (
                    <section className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                        <div className="bg-slate-800/30 border border-slate-700 rounded-xl p-6">
                            <h2 className="text-lg font-semibold text-slate-200 mb-6 flex items-center gap-2">
                                <span className="w-1.5 h-6 bg-blue-500 rounded-full" />
                                {isNativeBinderGeneration ? 'De Novo Binder Design' : 'Configuration'}
                            </h2>

                            <ModelDocumentationLinks
                                topics={getModelDocumentationTopics(selectedModel)}
                                summary="Docs linked; launch controls here."
                                compact
                                className="mb-6"
                            />

                            <div className="space-y-6">
                                {['boltzgen', 'ppiflow'].includes(selectedModelId ?? '') && <label className="block text-sm text-[var(--text-secondary)]">
                                    Job name
                                    <input aria-label="Native binder job name" className="mt-2 w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-3 text-[var(--text-primary)]"
                                        value={jobName} onChange={event => {
                                            const name = event.target.value;
                                            setJobName(name);
                                            binderNativeDrafts.current[`${selectedModelId}:${selectedModeId}`] = nativeBinderDraft(params, name);
                                        }} />
                                </label>}
                                {/* Mode Selection */}
                                <div>
                                    <label className="block text-sm font-medium text-slate-400 mb-2">
                                        Workflow Mode
                                    </label>
                                    <select
                                        value={selectedModeId || ''}
                                        onChange={(e) => {
                                            const mode = e.target.value;
                                            if (['boltzgen', 'ppiflow'].includes(selectedModelId ?? '')) {
                                                binderNativeDrafts.current[`${selectedModelId}:${selectedModeId}`] = nativeBinderDraft(params, jobName);
                                                const sourceContext = Object.fromEntries(['target_pdb', 'target_source', 'target_model_number', 'selected_chain', 'selected_residues']
                                                    .filter(key => Object.hasOwn(params, key)).map(key => [key, params[key]]));
                                                const draft = { ...sourceContext, ...binderNativeDrafts.current[`${selectedModelId}:${mode}`] };
                                                setClonedValues(draft);
                                                setParams(draft);
                                                setJobName(typeof draft.job_name === 'string' ? draft.job_name : '');
                                            }
                                            setSelectedModeId(mode);
                                        }}
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                                    >
                                        <option value="" disabled>Select a mode...</option>
                                        {(selectedModel.modes || [])
                                            .filter((mode: UntypedApiValue) => mode.id !== 'dna_complex' && !mode.selected_only) // Selected-only operations use the Results selection.
                                            .map((mode: UntypedApiValue) => (
                                                <option key={mode.id} value={mode.id}>
                                                    {mode.name}
                                                </option>
                                            ))}
                                    </select>
                                    {selectedMode && (
                                        <p className="mt-2 text-sm text-slate-500">{compactUiCopy(selectedMode.description, 120)}</p>
                                    )}
                                </div>

                                {isNativeBinderGeneration && selectedMode && <>
                                    <button type="button" onClick={() => {
                                        binderNativeDrafts.current[`${selectedModelId}:${selectedModeId}`] = nativeBinderDraft(params, jobName);
                                        const saved = binderDraftRef.current ?? params.binder_workflow_draft;
                                        const source = params.target_source;
                                        const inherited = { target_pdb: params.target_pdb ?? params.boltzgen_target_pdb_path,
                                            target_source: source, selected_chain: params.target_chain ?? params.antigen_chain ?? params.target_chains,
                                            selected_residues: params.selected_residues, target_model_number: params.target_model_number };
                                        setBinderInitialDraft({ ...inherited, ...saved });
                                        setClonedValues(undefined);
                                        setWizardMode('templates'); setSelectedTemplateId('antibody_denovo');
                                        setEngineChooserOpen(true);
                                    }}>Change generation engine</button>
                                    {nativeGenerationQuery.error && <p role="status" className="text-sm text-amber-500">{nativeGenerationQuery.error.message} Catalog controls remain available; the native inventory may contain additional settings.</p>}
                                    <NativeBinderGeneration key={`${selectedModelId}:${selectedModeId}`} model={selectedModelId as NativeBinderModel} mode={selectedModeId!}
                                        parameters={visibleParams} values={params} profile={nativeGenerationInventory ? { profile: nativeGenerationInventory.profile, assets: nativeGenerationInventory.assets } : undefined} nativeBehavior={nativeGenerationInventory?.native_behavior} onBrowse={setShowFileBrowser}
                                        onPatch={patch => setParams(previous => {
                                            const next = { ...previous, ...patch };
                                            binderNativeDrafts.current[`${selectedModelId}:${selectedModeId}`] = nativeBinderDraft(next, jobName);
                                            return next;
                                        })} />
                                </>}
                                {/* Other models retain their existing editor. */}
                                {!isNativeBinderGeneration && selectedMode && Object.keys(groupedParams).length > 0 && (
                                    <div className="space-y-6 pt-6 border-t border-slate-700/50">
                                        {/* Render groups in preferred order */}
                                        {['Inputs', 'Docking Settings', 'General'].filter(g => groupedParams[g]).map(groupName => (
                                            <div key={groupName}>
                                                {groupName !== 'General' && (
                                                    <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                                                        <span className={`w-1 h-4 rounded-full ${groupName === 'Inputs' ? 'bg-emerald-500' : 'bg-blue-500'}`} />
                                                        {groupName}
                                                    </h3>
                                                )}
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                    {groupedParams[groupName].map((param: UntypedApiValue) => (
                                                        <ParamField key={param.name} param={param} params={params} updateParam={updateParam} setShowFileBrowser={setShowFileBrowser} setActiveSequenceField={setActiveSequenceField} setShowSequenceManager={setShowSequenceManager} setSequenceToSave={setSequenceToSave} ligandPresets={ligandPresets} />
                                                    ))}
                                                </div>
                                            </div>
                                        ))}

                                        {/* Advanced section - collapsible */}
                                        {groupedParams['Advanced'] && (
                                            <div className="border border-slate-700/50 rounded-lg overflow-hidden">
                                                <button
                                                    type="button"
                                                    onClick={() => setShowAdvanced(!showAdvanced)}
                                                    className="w-full flex items-center justify-between px-4 py-3 bg-slate-800/50 hover:bg-slate-800/70 transition-colors"
                                                >
                                                    <span className="text-sm font-medium text-slate-400 flex items-center gap-2">
                                                        <span className="w-1 h-4 rounded-full bg-slate-500" />
                                                        Advanced Settings
                                                    </span>
                                                    <span className="text-slate-500 text-xs">{showAdvanced ? '▲' : '▼'}</span>
                                                </button>
                                                {showAdvanced && (
                                                    <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                                                        {groupedParams['Advanced'].map((param: UntypedApiValue) => (
                                                            <ParamField key={param.name} param={param} params={params} updateParam={updateParam} setShowFileBrowser={setShowFileBrowser} setActiveSequenceField={setActiveSequenceField} setShowSequenceManager={setShowSequenceManager} setSequenceToSave={setSequenceToSave} ligandPresets={ligandPresets} />
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                )
                                }

                                {genericFampnnControl}
                                {genericFrustrampnnControl}

                                {/* Ligand Selector for Complex Prediction mode in manual/advanced mode */}
                                {selectedModeId === 'complex' && (
                                    <div className="pt-6 border-t border-slate-700/50">
                                        <LigandSelector
                                            ligands={ligands}
                                            setLigands={setLigands}
                                            showCustomSmiles={true}
                                        />
                                    </div>
                                )}
                            </div>
                        </div>
                    </section>
                )}

                {/* Submit Button - Hide if Mutagenesis, Antibody De Novo, or Structure Prediction Template is active (they have their own) */}
                {!isDedicatedLauncherTemplate(selectedTemplateId) && (
                    <div className="flex justify-end gap-3 pt-4 pb-12">
                        {/* Save as Template Button */}
                        {(isTemplateMode || (wizardMode === 'manual' && selectedModelId)) && (
                            <button
                                onClick={() => openTemplateManager({
                                    currentParams: isNativeBinderGeneration ? { ...nativeBinderDraft(templateManagerParams, jobName), native_generation_authoring: true, binder_workflow_draft: binderDraftRef.current, binder_native_drafts: { ...binderNativeDrafts.current, [`${selectedModelId}:${selectedModeId}`]: nativeBinderDraft(params, jobName) } } : templateManagerParams,
                                    currentModelId: selectedModelId || templateDetail?.preset_params?.template_model_id || undefined,
                                    currentMode: selectedModeId || templateDetail?.preset_params?.template_mode_id || undefined,
                                    baseTemplateId: selectedTemplateId || undefined,
                                })}
                                className="inline-flex min-w-[12rem] items-center justify-center rounded-xl border border-slate-600 bg-slate-900/60 px-6 py-3.5 text-sm font-semibold text-slate-100 transition-all hover:bg-slate-800"
                            >
                                Template Manager
                            </button>
                        )}
                        <button
                            onClick={handleSubmit}
                            disabled={!isReady || submitMutation.isPending}
                            title={!isReady ? launchBlockedReason : undefined}
                            className={`inline-flex min-w-[12rem] items-center justify-center rounded-xl border px-6 py-3.5 text-sm font-semibold transition-all ${isReady
                                ? 'border-blue-500/40 bg-blue-500/15 text-blue-200 hover:bg-blue-500/20'
                                : 'border-slate-700 bg-slate-900/60 text-slate-500 cursor-not-allowed'
                                }`}
                        >
                            {submitMutation.isPending ? 'Launching Job...' : 'Launch Experiment'}
                        </button>
                        {!isReady && launchBlockedReason && selectedTemplateId && (
                            <div className="self-center text-xs text-slate-500">{launchBlockedReason}</div>
                        )}
                    </div>
                )}
            </main>

            {/* Loading Overlay for Batch Submission */}
            {submitMutation.isPending && selectedTemplateId === 'mutagenesis' && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-[100] flex items-center justify-center">
                    <div className="bg-slate-900 border border-slate-700 p-8 rounded-2xl shadow-2xl flex flex-col items-center">
                        <div className="w-16 h-16 border-4 border-accent/30 border-t-accent rounded-full animate-spin mb-4" />
                        <h3 className="text-xl font-bold text-white mb-2">Submitting Batch Jobs...</h3>
                        <p className="text-slate-400">Please wait while we launch your variant library.</p>
                    </div>
                </div>
            )}

            {/* File Browser Modal */}
            {showFileBrowser && (
                <FileBrowser
                    onSelect={(path) => {
                        updateParam(showFileBrowser, path);
                        setShowFileBrowser(null);
                    }}
                    onCancel={() => setShowFileBrowser(null)}
                />
            )}

            {/* Sequence Manager Modal */}
            <SequenceManagerModal
                isOpen={showSequenceManager}
                onClose={() => {
                    setShowSequenceManager(false);
                    setSequenceToSave(null);
                }}
                onSelect={(seq) => {
                    // Load selected sequence into the current sequence param
                    updateParam(activeSequenceField, seq.sequence);
                    if (seq.name && activeSequenceField === 'sequence') updateParam('sequence_name', seq.name);
                }}
                initialSequence={sequenceToSave?.sequence || ''}
                initialName={sequenceToSave?.name || ''}
            />

            {/* Template Manager Modal */}
            <TemplateManagerModal
                isOpen={showTemplateManager}
                onClose={() => {
                    setShowTemplateManager(false);
                    setTemplateManagerContext({});
                }}
                onSelect={routeUserTemplate}
                currentParams={templateManagerContext.currentParams}
                currentModelId={templateManagerContext.currentModelId}
                currentMode={templateManagerContext.currentMode}
                baseTemplateId={templateManagerContext.baseTemplateId}
            />
        </div>
    );
}
