import { useEffect, useMemo, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
    cmApiError,
    cmSourceContentUrl,
    CANONICAL_CM_ANALYSIS_POLICY,
    compileCmRuntimePolicy,
    listCmSources,
    listCmReusableRuns,
    registerCmRcsbSelection,
    registerCmRunArtifact,
    searchCmRcsb,
    registerCmSource,
    submitCmRequest,
    type CmBackendCoordinates,
    type CmBackend,
    type CmFeaturePolicy,
    type CmRcsbEntry,
    type CmRcsbSearchResponse,
    type CmRcsbSelection,
    type CmReusableRun,
    type CmSource,
    type CmSourceKind,
    type CmSubmitRequest,
    type CmTask,
} from './conformationalMappingApi';
import { CM_SCIENTIFIC_LIMIT } from './conformationalMappingSemantics';
import { ModelDocumentationLinks } from '../ModelDocumentationLinks';
import MolstarViewer from '../MolstarViewer';

interface Props {
    onBack?: () => void;
    initialValues?: Record<string, unknown>;
    services?: {
        listSources?: typeof listCmSources;
        listReusableRuns?: typeof listCmReusableRuns;
        registerRunArtifact?: typeof registerCmRunArtifact;
        searchRcsb?: typeof searchCmRcsb;
        registerRcsb?: typeof registerCmRcsbSelection;
        registerSource?: typeof registerCmSource;
        submitRequest?: typeof submitCmRequest;
    };
}

interface LauncherState {
    name: string;
    notes: string;
    backend: CmBackend;
    snapshotId: string;
    sequenceId: string;
    checkpointId: string;
    configId: string;
    transferId: string;
    referenceIds: string[];
    importIds: string[];
    seeds: string;
    samples: number;
    featureMode: CmFeaturePolicy['mode'];
    proteinMsa: boolean;
    templates: boolean;
    rnaMsa: boolean;
    defaultRuntime: boolean;
    nCycle: number;
    nStep: number;
    task: CmTask;
    runs: number;
    networks: number;
    savedSteps: string;
    maxSteps: number;
    numRecycles: number;
    numDiffusionSteps: number;
    learningRate: number;
    gradientClip: number;
    skipMsa: boolean;
    computeConfidence: boolean;
    saveFullConfidence: boolean;
    computeEvaluation: boolean;
    stateComparisonMode: 'off' | 'pairwise' | 'reference';
    stateComparisonTargetId: string;
    referenceBackend: CmBackend;
    referenceOrderedSeed: number;
    referenceSampleIndex: number;
    referenceTask: CmTask;
    referenceId: string;
    referenceRunIndex: number;
    referenceSavedStep: number;
    referenceConfornetIndex: number;
    referenceStagedIndex: number;
    referenceSourceContentSha256: string;
    referenceStagedReceiptSha256: string;
}

const DEFAULT_STATE: LauncherState = {
    name: 'Conformational mapping', notes: '', backend: 'protenix_v2_ensemble', snapshotId: '',
    sequenceId: '', checkpointId: '', configId: '', transferId: '', referenceIds: [], importIds: [],
    seeds: '101', samples: 5,
    featureMode: 'regenerate_mutated_protein_v1', proteinMsa: true, templates: false, rnaMsa: false,
    defaultRuntime: true, nCycle: 10, nStep: 200,
    task: 'diversity',
    runs: 2, networks: 2, savedSteps: '5,10,15,20', maxSteps: 20,
    numRecycles: 0, numDiffusionSteps: 200, learningRate: 0.001, gradientClip: 10,
    skipMsa: false, computeConfidence: true, saveFullConfidence: false, computeEvaluation: true,
    stateComparisonMode: 'off', stateComparisonTargetId: '', referenceBackend: 'protenix_v2_ensemble',
    referenceOrderedSeed: 101, referenceSampleIndex: 0, referenceTask: 'diversity', referenceId: '',
    referenceRunIndex: 0, referenceSavedStep: 0, referenceConfornetIndex: 0, referenceStagedIndex: 0,
    referenceSourceContentSha256: '', referenceStagedReceiptSha256: '',
};

const STATE_KEY = 'bms.conformational-mapping.launcher.v1';

const SOURCE_KINDS: Array<{ value: CmSourceKind; label: string; accept: string }> = [
    { value: 'complex_snapshot', label: 'Complete-complex snapshot JSON', accept: '.json,application/json' },
    { value: 'structure_upload', label: 'Protein mmCIF upload', accept: '.cif,.mmcif' },
    { value: 'structure_artifact', label: 'Protein mmCIF artifact', accept: '.cif,.mmcif' },
    { value: 'protein_sequence', label: 'Protein sequence', accept: '.txt,.fa,.fasta,text/plain' },
    { value: 'confornets_config', label: 'ConforNets config', accept: '.json,.yaml,.yml' },
    { value: 'confornets_state', label: 'ConforNets transfer state', accept: '.pt,.pth,.ckpt' },
];
type SourceTab = 'upload' | 'runs' | 'rcsb' | 'cached';
const SOURCE_TABS: Array<{ value: SourceTab; label: string }> = [
    { value: 'upload', label: 'Upload' },
    { value: 'runs', label: 'Your Runs' },

    { value: 'rcsb', label: 'RCSB' },
    { value: 'cached', label: 'Cached' },
];

const asObject = (value: unknown): Record<string, unknown> | null =>
    value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const asStringArray = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const finite = (value: unknown, fallback: number): number => typeof value === 'number' && Number.isFinite(value) ? value : fallback;
const hydrateState = (values?: Record<string, unknown>): LauncherState => {
    let stored: Record<string, unknown> = {};
    try { stored = asObject(JSON.parse(sessionStorage.getItem(STATE_KEY) || '{}')) || {}; } catch { stored = {}; }
    // Session state wins over card defaults so every control survives launcher
    // navigation. Explicit clone/template loads clear this key before mounting.
    const merged = { ...(values || {}), ...stored };
    const editableState = Object.fromEntries(
        Object.entries(merged).filter(([key]) => key !== 'analysis' && key !== 'analysis_policy'),
    );
    const confornets = asObject(merged.confornets) || {};
    const feature = asObject(merged.feature_policy) || {};
    const runtime = asObject(merged.runtime_policy) || {};
    const comparison = asObject(merged.state_landscape_comparison) || {};
    const reference = asObject(comparison.reference_backend_coordinates) || {};
    const orderedSeeds = Array.isArray(merged.ordered_seeds) ? merged.ordered_seeds.join(',') : merged.seeds;
    const savedSteps = Array.isArray(confornets.saved_steps) ? confornets.saved_steps.join(',') : merged.savedSteps;
    const backend = ['protenix_v2_ensemble', 'confornets', 'external_import'].includes(String(merged.backend))
        ? merged.backend as CmBackend
        : DEFAULT_STATE.backend;
    const hydratedSeeds = typeof orderedSeeds === 'string' ? orderedSeeds : DEFAULT_STATE.seeds;
    const firstSeed = hydratedSeeds.split(',')[0].trim();
    const comparisonMode = comparison.mode === 'pairwise' || comparison.mode === 'reference'
        ? comparison.mode : (merged.stateComparisonMode === 'pairwise' || merged.stateComparisonMode === 'reference'
            ? merged.stateComparisonMode : DEFAULT_STATE.stateComparisonMode);
    const referenceBackend = ['protenix_v2_ensemble', 'confornets', 'external_import'].includes(String(reference.backend))
        ? reference.backend as CmBackend : DEFAULT_STATE.referenceBackend;
    return {
        ...DEFAULT_STATE,
        ...editableState,
        name: typeof merged.name === 'string' ? merged.name : DEFAULT_STATE.name,
        notes: typeof merged.notes === 'string' ? merged.notes : DEFAULT_STATE.notes,
        backend,
        snapshotId: String(merged.registered_snapshot_id || merged.snapshotId || ''),
        sequenceId: String(merged.registered_sequence_id || merged.sequenceId || ''),
        checkpointId: String(merged.registered_checkpoint_id || merged.checkpointId || ''),
        configId: String(merged.registered_config_id || merged.configId || ''),
        transferId: String(merged.registered_transfer_id || merged.transferId || ''),
        referenceIds: asStringArray(merged.registered_reference_ids || merged.referenceIds),
        importIds: asStringArray(
            merged.registered_artifact_ids
            || (typeof merged.registered_artifact_id === 'string' ? [merged.registered_artifact_id] : undefined)
            || merged.importIds,
        ).slice(0, 1),

        seeds: backend === 'confornets' && /^-?\d+$/.test(firstSeed) ? firstSeed : hydratedSeeds,
        samples: finite(merged.samples_per_seed ?? merged.samples, DEFAULT_STATE.samples),
        featureMode: (feature.mode || merged.featureMode || DEFAULT_STATE.featureMode) as CmFeaturePolicy['mode'],
        proteinMsa: typeof feature.protein_msa_enabled === 'boolean' ? feature.protein_msa_enabled : Boolean(merged.proteinMsa ?? DEFAULT_STATE.proteinMsa),
        templates: typeof feature.templates_enabled === 'boolean' ? feature.templates_enabled : Boolean(merged.templates ?? DEFAULT_STATE.templates),
        rnaMsa: typeof feature.rna_msa_enabled === 'boolean' ? feature.rna_msa_enabled : Boolean(merged.rnaMsa ?? DEFAULT_STATE.rnaMsa),
        defaultRuntime: typeof runtime.use_default_params === 'boolean' ? runtime.use_default_params : Boolean(merged.defaultRuntime ?? true),
        nCycle: finite(runtime.n_cycle ?? merged.nCycle, DEFAULT_STATE.nCycle),
        nStep: finite(runtime.n_step ?? merged.nStep, DEFAULT_STATE.nStep),
        task: (confornets.task || merged.task || DEFAULT_STATE.task) as CmTask,

        runs: finite(confornets.runs ?? merged.runs, DEFAULT_STATE.runs),
        networks: finite(confornets.confornet_count ?? merged.networks, DEFAULT_STATE.networks),
        savedSteps: typeof savedSteps === 'string' ? savedSteps : DEFAULT_STATE.savedSteps,
        maxSteps: finite(confornets.max_steps ?? merged.maxSteps, DEFAULT_STATE.maxSteps),
        numRecycles: finite(confornets.num_recycles ?? merged.numRecycles, DEFAULT_STATE.numRecycles),
        numDiffusionSteps: finite(confornets.num_diffusion_steps ?? merged.numDiffusionSteps, DEFAULT_STATE.numDiffusionSteps),
        learningRate: finite(confornets.learning_rate ?? merged.learningRate, DEFAULT_STATE.learningRate),
        gradientClip: finite(confornets.gradient_clip ?? merged.gradientClip, DEFAULT_STATE.gradientClip),
        skipMsa: Boolean(confornets.skip_msa ?? merged.skipMsa ?? DEFAULT_STATE.skipMsa),
        computeConfidence: Boolean(confornets.compute_confidence ?? merged.computeConfidence ?? DEFAULT_STATE.computeConfidence),
        saveFullConfidence: Boolean(confornets.save_full_confidence ?? merged.saveFullConfidence ?? DEFAULT_STATE.saveFullConfidence),
        computeEvaluation: Boolean(confornets.compute_evaluation ?? merged.computeEvaluation ?? DEFAULT_STATE.computeEvaluation),
        stateComparisonMode: comparisonMode,
        stateComparisonTargetId: String(comparison.target_id || merged.stateComparisonTargetId || ''),
        referenceBackend,
        referenceOrderedSeed: finite(reference.ordered_seed ?? merged.referenceOrderedSeed, DEFAULT_STATE.referenceOrderedSeed),
        referenceSampleIndex: finite(reference.sample_index ?? merged.referenceSampleIndex, DEFAULT_STATE.referenceSampleIndex),
        referenceTask: (reference.task || merged.referenceTask || DEFAULT_STATE.referenceTask) as CmTask,
        referenceId: String(reference.reference_id || merged.referenceId || ''),
        referenceRunIndex: finite(reference.run_index ?? merged.referenceRunIndex, DEFAULT_STATE.referenceRunIndex),
        referenceSavedStep: finite(reference.saved_step ?? merged.referenceSavedStep, DEFAULT_STATE.referenceSavedStep),
        referenceConfornetIndex: finite(reference.confornet_index ?? merged.referenceConfornetIndex, DEFAULT_STATE.referenceConfornetIndex),
        referenceStagedIndex: finite(reference.staged_index ?? merged.referenceStagedIndex, DEFAULT_STATE.referenceStagedIndex),
        referenceSourceContentSha256: String(reference.source_content_sha256 || merged.referenceSourceContentSha256 || ''),
        referenceStagedReceiptSha256: String(reference.staged_receipt_sha256 || merged.referenceStagedReceiptSha256 || ''),
    };
};

const sourceLabel = (source: CmSource): string => {
    const metadataLabel = source.metadata.name || source.metadata.target_id
        || (Array.isArray(source.metadata.target_ids) ? source.metadata.target_ids.join(', ') : null);
    return `${String(metadataLabel || source.source_id)} · ${(source.bytes / 1024).toFixed(1)} KiB · ${source.sha256.slice(0, 12)}`;
};

const metadataText = (value: unknown): string => {
    if (Array.isArray(value)) return value.map((item) => metadataText(item)).join(', ');
    if (value == null || value === '') return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
};

type CmSourceIdentityContext = {
    provider: unknown;
    accession: unknown;
    model: unknown;
    sample: unknown;
    chains: unknown;
    entities: unknown;
    coordinate: unknown;
};

const sourceIdentityContext = (source: CmSource): CmSourceIdentityContext | null => {
    const receipt = source.authority_receipt;
    if (!receipt
        || receipt.schema_name !== 'cm_source_authority_receipt'
        || receipt.schema_version !== 1
        || receipt.source_id !== source.source_id
        || receipt.source_kind !== source.source_kind
        || receipt.content_sha256 !== source.sha256
        || !/^[0-9a-f]{64}$/.test(receipt.content_sha256)
        || !/^[0-9a-f]{64}$/.test(receipt.receipt_sha256)) return null;
    const payload = asObject(receipt.payload);
    if (!payload) return null;
    const selection = asObject(payload.selection);
    if (receipt.authority_kind === 'rcsb_download') {
        if (source.source_kind !== 'structure_upload'
            || payload.provider !== 'RCSB'
            || typeof payload.accession !== 'string'
            || !/^[A-Z0-9]{4}$/.test(payload.accession)
            || !selection
            || selection.accession !== payload.accession) return null;
    }
    const scopes = selection ? [selection, payload] : [payload];
    const pick = (...keys: string[]): unknown => {
        for (const scope of scopes) {
            for (const key of keys) {
                if (scope[key] != null && scope[key] !== '') return scope[key];
            }
        }
        return undefined;
    };
    return {
        provider: pick('provider'),
        accession: pick('accession'),
        model: pick('model_id', 'model_ids'),
        sample: pick('sample_id', 'sample_ids'),
        chains: pick('chain_ids', 'chain_id'),
        entities: pick('entity_ids', 'entity_id'),
        coordinate: pick('backend_coordinates'),
    };
};

const identityStringArray = (value: unknown): string[] => {
    if (typeof value === 'string' && value.length > 0) return [value];
    return asStringArray(value).filter((item) => item.length > 0);
};

const integerList = (value: string): number[] | null => {
    const parts = value.split(',').map((item) => item.trim());
    if (!parts.length || parts.some((item) => !/^-?\d+$/.test(item))) return null;
    return parts.map(Number);
};

const cmCoordinateCardinality = (input: {
    backend: CmBackend;
    task: CmTask;
    targetCount: number;
    seedCount: number;
    samples: number;
    referenceCount: number;
    runs: number;
    savedStepCount: number;
    networkCount: number;
    importCount: number;
}): number => {
    if (input.backend === 'protenix_v2_ensemble') {
        return input.targetCount * input.seedCount * input.samples;
    }
    if (input.backend === 'external_import') {
        return input.importCount;
    }

    // Keep this expansion aligned with the backend request builder. MSE uses
    // one coordinate per selected reference; diversity expands saved steps
    // and networks; transfer is one run, one step, and one network.
    const references = input.task === 'mse' ? Math.max(input.referenceCount, 0) : 1;
    const runs = input.task === 'transfer' ? 1 : input.runs;
    const savedSteps = input.task === 'diversity' ? input.savedStepCount : 1;
    const networks = input.task === 'diversity' ? input.networkCount : 1;
    return references * runs * savedSteps * networks * input.samples;
};

const inputClass = 'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-orange-400 disabled:cursor-not-allowed disabled:opacity-50';
const checkClass = 'h-4 w-4 rounded border-slate-600 bg-slate-950 text-orange-500 focus:ring-orange-500';

export function ConformationalMappingLauncher({ onBack, initialValues, services }: Props) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [form, setForm] = useState<LauncherState>(() => hydrateState(initialValues));
    const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
    const [activeSourceTab, setActiveSourceTab] = useState<SourceTab>('upload');
    const [sourceKind, setSourceKind] = useState<CmSourceKind>('complex_snapshot');
    const [sourceFile, setSourceFile] = useState<File | null>(null);
    const [localPreviewUrl, setLocalPreviewUrl] = useState<string | null>(null);
    const [sourceTargetId, setSourceTargetId] = useState('');
    const [sourceState, setSourceState] = useState('reference');
    const [transferKind, setTransferKind] = useState<'confornet_state' | 'mse_state'>('confornet_state');
    const [sourceTestCases, setSourceTestCases] = useState('');
    const [snapshotEditor, setSnapshotEditor] = useState('');
    const [pastedSequence, setPastedSequence] = useState('');
    const [rcsbQuery, setRcsbQuery] = useState('');
    const [rcsbSearchResults, setRcsbSearchResults] = useState<CmRcsbSearchResponse | null>(null);
    const [selectedRcsbEntry, setSelectedRcsbEntry] = useState<CmRcsbEntry | null>(null);
    const [rcsbModelId, setRcsbModelId] = useState('');
    const [rcsbSampleId, setRcsbSampleId] = useState('');
    const [rcsbChainId, setRcsbChainId] = useState('');
    const [rcsbEntityId, setRcsbEntityId] = useState('');
    const [registeredSources, setRegisteredSources] = useState<CmSource[]>([]);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (form.backend !== 'external_import' || !sourceFile || sourceKind !== 'structure_upload') {
            setLocalPreviewUrl(null);
            return;
        }
        const url = URL.createObjectURL(sourceFile);
        setLocalPreviewUrl(url);
        return () => URL.revokeObjectURL(url);
    }, [form.backend, sourceFile, sourceKind]);

    const update = <K extends keyof LauncherState>(key: K, value: LauncherState[K]) =>
        setForm((current) => ({ ...current, [key]: value }));
    useEffect(() => { sessionStorage.setItem(STATE_KEY, JSON.stringify(form)); }, [form]);
    useEffect(() => {
        if (form.featureMode === 'features_disabled_control_v1'
            && (form.proteinMsa || form.templates || form.rnaMsa)) {
            setForm((current) => ({ ...current, proteinMsa: false, templates: false, rnaMsa: false }));
        }
    }, [form.featureMode, form.proteinMsa, form.rnaMsa, form.templates]);

    const sources = useQuery({ queryKey: ['cm-sources'], queryFn: services?.listSources || listCmSources });
    const sourceRegistry = useMemo(() => {
        const mergedSources = [...(sources.data || []), ...registeredSources];
        return Array.from(new Map(mergedSources.map((source) => [source.source_id, source])).values());
    }, [registeredSources, sources.data]);
    const byKind = (kind: CmSourceKind) => sourceRegistry.filter((source) => source.source_kind === kind);
    const reusableRuns = useQuery({
        queryKey: ['cm-reusable-runs'],
        queryFn: services?.listReusableRuns || listCmReusableRuns,
        enabled: activeSourceTab === 'runs',
        retry: false,
    });
    const structureSources = useMemo(
        () => sourceRegistry.filter((source) => (
            ['structure_upload', 'structure_artifact'].includes(source.source_kind)
            && source.format === 'mmcif'
        )),
        [sourceRegistry],
    );
    useEffect(() => {
        if (!sources.data && !registeredSources.length) return;
        const admissible = new Set(structureSources.map((source) => source.source_id));
        setForm((current) => {
            if (current.backend !== 'external_import') return current;
            const next = current.importIds.filter((id) => admissible.has(id)).slice(0, 1);
            if (next.length === current.importIds.length
                && next.every((id, index) => id === current.importIds[index])) return current;
            return { ...current, importIds: next };
        });
    }, [registeredSources.length, sources.data, structureSources]);
    useEffect(() => {
        if (!sources.data && !registeredSources.length) return;
        const checkpoints = sourceRegistry.filter((source) =>
            source.source_kind === 'confornets_checkpoint' && source.managed_checkpoint === true);
        const admissible = new Set(checkpoints.map((source) => source.source_id));
        setForm((current) => {
            if (current.checkpointId && admissible.has(current.checkpointId)) return current;
            const checkpointId = checkpoints.length === 1 ? checkpoints[0].source_id : '';
            return checkpointId === current.checkpointId ? current : { ...current, checkpointId };
        });
    }, [registeredSources.length, sourceRegistry, sources.data]);
    useEffect(() => {
        if (!sources.data && !registeredSources.length) return;
        const kindById = new Map(sourceRegistry.map((source) => [source.source_id, source.source_kind]));
        setForm((current) => {
            const snapshotId = kindById.get(current.snapshotId) === 'complex_snapshot' ? current.snapshotId : '';
            const sequenceId = kindById.get(current.sequenceId) === 'protein_sequence' ? current.sequenceId : '';
            const configId = kindById.get(current.configId) === 'confornets_config' ? current.configId : '';
            const transferId = kindById.get(current.transferId) === 'confornets_state' ? current.transferId : '';
            const referenceIds = current.referenceIds.filter((id) =>
                ['structure_upload', 'structure_artifact'].includes(kindById.get(id) || ''));
            if (snapshotId === current.snapshotId && sequenceId === current.sequenceId
                && configId === current.configId && transferId === current.transferId
                && referenceIds.length === current.referenceIds.length) return current;
            return { ...current, snapshotId, sequenceId, configId, transferId, referenceIds };
        });
    }, [registeredSources.length, sourceRegistry, sources.data]);
    const selectedSnapshot = sourceRegistry.find((source) =>
        source.source_id === form.snapshotId && source.source_kind === 'complex_snapshot');
    const selectedSource = sourceRegistry.find((source) => source.source_id === (
        form.backend === 'protenix_v2_ensemble'
            ? form.snapshotId
            : form.backend === 'confornets'
                ? form.sequenceId
                : form.importIds[0]
    ) && (
        (form.backend === 'protenix_v2_ensemble' && source.source_kind === 'complex_snapshot')
        || (form.backend === 'confornets' && source.source_kind === 'protein_sequence')
        || (form.backend === 'external_import' && ['structure_upload', 'structure_artifact'].includes(source.source_kind))
    ));
    const selectedInputIdentity = useMemo(
        () => selectedSource ? sourceIdentityContext(selectedSource) : null,
        [selectedSource],
    );
    const selectedCheckpoint = sourceRegistry.find((source) =>
        source.source_id === form.checkpointId
        && source.source_kind === 'confornets_checkpoint'
        && source.managed_checkpoint === true);
    const selectedConfig = sourceRegistry.find((source) =>
        source.source_id === form.configId && source.source_kind === 'confornets_config');
    const selectedTransfer = sourceRegistry.find((source) =>
        source.source_id === form.transferId && source.source_kind === 'confornets_state');
    const referenceSources = form.referenceIds.flatMap((id) => {
        const source = sourceRegistry.find((candidate) => candidate.source_id === id);
        return source && ['structure_upload', 'structure_artifact'].includes(source.source_kind) ? [source] : [];
    });
    const availableChainIds = useMemo(() => {
        if (form.backend === 'confornets') {
            return selectedSource?.submission_policy?.chain_id
                ? [selectedSource.submission_policy.chain_id]
                : [];
        }
        return identityStringArray(selectedInputIdentity?.chains);
    }, [form.backend, selectedInputIdentity?.chains, selectedSource?.submission_policy?.chain_id]);

    const compatibleSources = useMemo(() => sourceRegistry.filter((source) => {
        if (form.backend === 'protenix_v2_ensemble') return source.source_kind === 'complex_snapshot';
        if (form.backend === 'confornets') return source.source_kind === 'protein_sequence';
        return ['structure_upload', 'structure_artifact'].includes(source.source_kind) && source.format === 'mmcif';
    }), [form.backend, sourceRegistry]);
    const tabSources = useMemo(() => compatibleSources.filter((source) => {
        if (activeSourceTab === 'runs') return source.source_kind === 'structure_artifact';
        if (activeSourceTab === 'rcsb') {
            const receipt = source.authority_receipt;
            return receipt?.authority_kind === 'rcsb_download'
                && receipt.content_sha256 === source.sha256
                && receipt.payload.provider === 'RCSB';
        }
        if (activeSourceTab === 'cached') return true;
        return false;
    }), [activeSourceTab, compatibleSources]);
    const selectSource = (source: CmSource) => {
        setSourceFile(null);
        setForm((current) => ({
            ...current,
            snapshotId: source.source_kind === 'complex_snapshot' ? source.source_id : current.snapshotId,
            sequenceId: source.source_kind === 'protein_sequence' ? source.source_id : current.sequenceId,
            importIds: ['structure_upload', 'structure_artifact'].includes(source.source_kind)
                ? [source.source_id] : current.importIds,
        }));
    };
    const seedValues = useMemo(() => integerList(form.seeds), [form.seeds]);
    const stepValues = useMemo(() => integerList(form.savedSteps), [form.savedSteps]);
    const snapshotTargetIds = Array.isArray(selectedSnapshot?.metadata.target_ids)
        ? selectedSnapshot.metadata.target_ids.filter((item): item is string => typeof item === 'string') : [];
    const expectedCount = cmCoordinateCardinality({
        backend: form.backend,
        task: form.task,
        targetCount: Math.max(snapshotTargetIds.length, selectedSnapshot ? 1 : 0),
        seedCount: seedValues?.length || 0,
        samples: form.samples,
        referenceCount: form.referenceIds.length,
        runs: form.runs,
        savedStepCount: stepValues?.length || 0,
        networkCount: form.networks,
        importCount: form.importIds.length,
    });
    const selectedInputBytes = form.backend === 'external_import'
        ? form.importIds.reduce((total, id) => total + (sourceRegistry.find((source) => source.source_id === id)?.bytes || 0), 0)
        : selectedSnapshot?.bytes || 0;
    const planningMiBPerCandidate = form.backend === 'protenix_v2_ensemble' ? 80 : form.backend === 'confornets' ? 45 : 24;
    const estimatedStorageGiB = (selectedInputBytes + expectedCount * planningMiBPerCandidate * 1024 * 1024) / (1024 ** 3);

    const validationErrors = useMemo(() => {
        const errors: string[] = [];
        if (!form.name.trim()) errors.push('Request name is required.');
        if (form.notes.length > 4000) errors.push('Notes cannot exceed 4,000 characters.');

        if (!['regenerate_mutated_protein_v1', 'paired_regenerate_changed_protein_v1', 'features_disabled_control_v1'].includes(form.featureMode)) {
            errors.push('Feature policy mode is not an approved contract value.');
        }
        if (!Number.isInteger(form.samples) || form.samples < 1 || form.samples > 100) errors.push('Samples must be an integer from 1 to 100.');
        if (form.backend !== 'external_import') {
            if (!seedValues?.length || new Set(seedValues).size !== seedValues.length) errors.push('Ordered seeds must be unique integers.');
            if (seedValues?.some((seed) => seed < -2147483648 || seed > 2147483647)) errors.push('Seeds must be signed 32-bit integers.');
        }
        if (form.backend === 'protenix_v2_ensemble') {
            if (!selectedSnapshot) errors.push('Select a registered complete-complex snapshot.');
            if (form.featureMode === 'features_disabled_control_v1' && (form.proteinMsa || form.templates || form.rnaMsa)) {
                errors.push('The feature-disabled control cannot enable MSA or templates.');
            }
            if (!form.defaultRuntime && (![form.nCycle, form.nStep].every((value) => Number.isInteger(value) && value > 0))) {
                errors.push('Exact Protenix cycle and step controls must be positive integers.');
            }
        }
        if (form.backend === 'external_import') {
            if (form.importIds.length !== 1) errors.push('Select exactly one registered mmCIF handle.');
            if (new Set(form.importIds).size !== form.importIds.length) errors.push('Ordered import handles must be unique.');
            const admissibleIds = new Set(structureSources.map((source) => source.source_id));
            if (form.importIds.some((id) => !admissibleIds.has(id))) {
                errors.push('External import accepts registered mmCIF handles only.');
            }
        }
        if (form.backend === 'confornets') {
            if (!['diversity', 'mse', 'transfer'].includes(form.task)) errors.push('ConforNets task is not supported.');
            if (!form.sequenceId) errors.push('Select a registered single-chain protein sequence.');
            if (!selectedSource?.submission_policy) errors.push('Server-derived ConforNets submission policy is unavailable.');
            if (!selectedCheckpoint) errors.push('Select the installed managed checkpoint.');
            if (form.configId && !selectedConfig) errors.push('Select a registered ConforNets config.');

            if (!Number.isInteger(form.runs) || form.runs < 1) errors.push('Runs must be a positive integer.');
            if (!Number.isInteger(form.samples) || form.samples < 1) errors.push('Samples must be a positive integer.');
            if (!Number.isInteger(form.maxSteps) || form.maxSteps < 1) errors.push('Maximum steps must be a positive integer.');
            if (form.task === 'diversity') {
                if (!Number.isInteger(form.networks) || form.networks < 2) errors.push('Diversity requires at least two networks.');
                if (!stepValues?.length || new Set(stepValues).size !== stepValues.length || stepValues.some((step) => step < 0 || step > form.maxSteps)) {
                    errors.push('Saved steps must be unique non-negative integers no greater than maximum steps.');
                }
            }
            if (form.task === 'mse' && (form.referenceIds.length < 1 || form.referenceIds.length > 2
                || referenceSources.length !== form.referenceIds.length)) errors.push('MSE requires one or two registered references.');
            if (form.task === 'transfer' && !selectedTransfer) errors.push('Transfer requires a registered transfer-state handle.');
            if (![form.numRecycles, form.numDiffusionSteps].every(Number.isInteger) || form.numRecycles < 0 || form.numDiffusionSteps < 1) {
                errors.push('Recycle and diffusion-step controls are invalid.');
            }
            if (form.learningRate <= 0 || form.gradientClip <= 0) errors.push('Learning rate and gradient clip must be positive.');
            if (!seedValues || seedValues.length !== 1) errors.push('ConforNets requires exactly one explicit seed.');
        }
        if (form.stateComparisonMode !== 'off') {
            if (form.backend === 'external_import') {
                errors.push('State-landscape comparison requires at least two planned coordinates.');
            } else if (!form.stateComparisonTargetId.trim()) {
                errors.push('State-landscape comparison target is required.');
            } else if (form.stateComparisonMode === 'reference') {
                if (form.referenceBackend !== form.backend) errors.push('Reference coordinates must use the selected backend.');
                if (form.backend === 'protenix_v2_ensemble'
                    && (!Number.isInteger(form.referenceOrderedSeed) || !Number.isInteger(form.referenceSampleIndex))) {
                    errors.push('Reference seed and sample must be integers.');
                }
                if (form.backend === 'confornets'
                    && (![form.referenceRunIndex, form.referenceSavedStep, form.referenceConfornetIndex, form.referenceSampleIndex].every(Number.isInteger)
                        || (form.referenceTask === 'mse' && !form.referenceId))) {
                    errors.push('Reference ConforNets coordinates are incomplete.');
                }
            }
        }
        if (expectedCount < 1) errors.push('The current controls produce no candidate coordinates.');
        return errors;
    }, [expectedCount, form, referenceSources.length, seedValues, selectedCheckpoint, selectedConfig,
        selectedSnapshot, selectedSource?.submission_policy, selectedTransfer, stepValues, structureSources]);

    const register = useMutation({
        mutationFn: async ({ file, kind }: { file: File; kind: CmSourceKind }) => (services?.registerSource || registerCmSource)(kind, file, {
            name: file.name,
            ...(sourceTargetId.trim() ? { target_id: sourceTargetId.trim() } : {}),
            ...(kind === 'structure_upload' || kind === 'structure_artifact' ? { state: sourceState.trim() || 'reference' } : {}),
            ...(kind === 'confornets_state' ? { kind: transferKind, source_test_cases: sourceTestCases.trim() } : {}),
        }),
        onSuccess: async (source) => {
            setError(null); setSourceFile(null);
            setRegisteredSources((current) => [...current.filter((item) => item.source_id !== source.source_id), source]);
            if (source.source_kind === 'complex_snapshot') update('snapshotId', source.source_id);
            if (source.source_kind === 'protein_sequence') update('sequenceId', source.source_id);
            await queryClient.invalidateQueries({ queryKey: ['cm-sources'] });
        },
        onError: (value) => setError(cmApiError(value, 'Source registration failed.')),
    });

    const registerRunArtifactMutation = useMutation({
        mutationFn: async ({ run, artifact }: { run: CmReusableRun; artifact: CmReusableRun['artifacts'][number] }) => {
            const runId = run.request_id || run.run_id;
            if (!runId) throw new Error('The reusable run has no request identity.');
            return (services?.registerRunArtifact || registerCmRunArtifact)(runId, artifact.artifact_id);
        },
        onSuccess: async (source) => {
            setError(null);
            setRegisteredSources((current) => [...current.filter((item) => item.source_id !== source.source_id), source]);
            if (form.backend === 'external_import') update('importIds', [source.source_id]);
            if (form.backend === 'confornets' && form.task === 'mse') {
                update('referenceIds', [source.source_id]);
            }
            await queryClient.invalidateQueries({ queryKey: ['cm-sources'] });
        },
        onError: (value) => setError(cmApiError(value, 'Run artifact registration failed.')),
    });

    const searchRcsbMutation = useMutation({
        mutationFn: () => {
            const query = rcsbQuery.trim();
            if (query.length < 2) throw new Error('Enter at least two characters for an RCSB search.');
            return (services?.searchRcsb || searchCmRcsb)(query);
        },
        onSuccess: (response) => {
            setRcsbSearchResults(response);
            setSelectedRcsbEntry(null);
            setError(null);
        },
        onError: (value) => setError(cmApiError(value, 'RCSB search failed.')),
    });

    const registerRcsbSelectionMutation = useMutation({
        mutationFn: () => {
            if (!selectedRcsbEntry) throw new Error('Select an RCSB entry before registration.');
            const selection: CmRcsbSelection = {
                accession: selectedRcsbEntry.accession,
                ...(rcsbModelId ? { model_id: rcsbModelId } : {}),
                ...(rcsbSampleId ? { sample_id: rcsbSampleId } : {}),
                ...(rcsbChainId ? { chain_ids: [rcsbChainId] } : {}),
                ...(rcsbEntityId ? { entity_ids: [rcsbEntityId] } : {}),
            };
            return (services?.registerRcsb || registerCmRcsbSelection)(selection);
        },
        onSuccess: async (source) => {
            setError(null);
            setRegisteredSources((current) => [...current.filter((item) => item.source_id !== source.source_id), source]);
            if (form.backend === 'external_import') update('importIds', [source.source_id]);
            if (form.backend === 'confornets' && form.task === 'mse') {
                update('referenceIds', [source.source_id]);
            }
            await queryClient.invalidateQueries({ queryKey: ['cm-sources'] });
        },
        onError: (value) => setError(cmApiError(value, 'RCSB mmCIF registration failed.')),
    });

    const selectRcsbEntry = (entry: CmRcsbEntry) => {
        setSelectedRcsbEntry(entry);
        setRcsbModelId('');
        setRcsbSampleId('');
        setRcsbChainId('');
        setRcsbEntityId('');
        setError(null);
    };

    const registerSnapshotEditor = () => {
        try {
            const parsed = JSON.parse(snapshotEditor);
            if (!parsed || (typeof parsed !== 'object')) throw new Error('Snapshot JSON must contain an object or ordered array.');
            register.mutate({ file: new File([snapshotEditor], 'complete-complex-snapshot.json', { type: 'application/json' }), kind: 'complex_snapshot' });
        } catch (value) { setError(value instanceof Error ? value.message : 'Snapshot JSON is invalid.'); }
    };
    const registerPastedSequence = () => {
        const canonicalSequence = pastedSequence.replace(/\s+/g, '').toUpperCase();
        if (!canonicalSequence || /[^ACDEFGHIKLMNPQRSTVWY]/.test(canonicalSequence)) {
            setError('Paste one-letter protein residues only (ACDEFGHIKLMNPQRSTVWY); FASTA headers are not part of the registered sequence bytes.');
            return;
        }
        register.mutate({
            file: new File([canonicalSequence], 'protein-sequence.fasta', { type: 'text/plain' }),
            kind: 'protein_sequence',
        });
    };

    const buildPayload = (): CmSubmitRequest => {
        if (validationErrors.length) throw new Error(validationErrors.join(' '));
        const featurePolicy: CmFeaturePolicy = form.backend === 'protenix_v2_ensemble'
            ? { mode: form.featureMode, protein_msa_enabled: form.proteinMsa, templates_enabled: form.templates, rna_msa_enabled: form.rnaMsa }
            : { mode: form.featureMode };
        const payload: CmSubmitRequest = {
            name: form.name.trim(), notes: form.notes.trim(), idempotency_key: idempotencyKey, backend: form.backend,

            ordered_seeds: form.backend === 'external_import' ? [0] : seedValues!,
            samples_per_seed: form.backend === 'external_import' ? 1 : form.samples,
            feature_policy: featurePolicy,
            runtime_policy: compileCmRuntimePolicy(form.backend, form.defaultRuntime, form.nCycle, form.nStep),
            // Analysis policy is server-owned. Keep the typed field for the backend
            // contract, but never accept it from editable/session/clone state.
            analysis_policy: CANONICAL_CM_ANALYSIS_POLICY,
        };
        if (form.backend === 'protenix_v2_ensemble') {
            payload.registered_snapshot_id = form.snapshotId;
            if (form.stateComparisonMode !== 'off' && form.stateComparisonTargetId.trim()) {
                if (form.stateComparisonMode === 'reference') {
                    const referenceCoordinates: CmBackendCoordinates = {
                        backend: 'protenix_v2_ensemble',
                        target_id: form.stateComparisonTargetId.trim(),
                        ordered_seed: form.referenceOrderedSeed,
                        sample_index: form.referenceSampleIndex,
                    };
                    payload.state_landscape_comparison = {
                        mode: 'reference', target_id: form.stateComparisonTargetId.trim(),
                        scope: 'all_other_within_target', reference_backend_coordinates: referenceCoordinates,
                    };
                } else if (form.stateComparisonMode === 'pairwise') {
                    payload.state_landscape_comparison = {
                        mode: 'pairwise', target_id: form.stateComparisonTargetId.trim(), scope: 'all_within_target',
                    };
                }
            }
        }
        if (form.backend === 'external_import') {
            payload.registered_artifact_ids = [form.importIds[0]];
        }
        if (form.backend === 'confornets') {
            payload.registered_sequence_id = form.sequenceId;
            payload.registered_checkpoint_id = form.checkpointId;
            if (form.configId) payload.registered_config_id = form.configId;
            if (form.task === 'mse') payload.registered_reference_ids = form.referenceIds;
            if (form.task === 'transfer') payload.registered_transfer_id = form.transferId;
            payload.confornets = {
                task: form.task,
                runs: form.task === 'transfer' ? 1 : form.runs,
                saved_steps: form.task === 'mse' ? [form.maxSteps] : form.task === 'transfer' ? [0] : stepValues!,
                confornet_count: form.task === 'diversity' ? form.networks : 1,
                samples: form.samples, max_steps: form.maxSteps, num_recycles: form.numRecycles,
                num_diffusion_steps: form.numDiffusionSteps, learning_rate: form.learningRate, gradient_clip: form.gradientClip,
                skip_msa: form.skipMsa, compute_confidence: form.computeConfidence,
                save_full_confidence: form.saveFullConfidence, compute_evaluation: form.computeEvaluation,
            };
            if (form.stateComparisonMode !== 'off' && form.stateComparisonTargetId.trim()) {
                const targetId = form.stateComparisonTargetId.trim();
                if (form.stateComparisonMode === 'pairwise') {
                    payload.state_landscape_comparison = {
                        mode: 'pairwise', target_id: targetId, scope: 'all_within_target',
                    };
                } else {
                    const referenceCoordinates: CmBackendCoordinates = {
                        backend: 'confornets', target_id: targetId, task: form.referenceTask,
                        test_case_id: 'bms-canonical-monomer', reference_id: form.referenceTask === 'mse' ? (form.referenceId || null) : null,
                        run_index: form.referenceRunIndex, saved_step: form.referenceSavedStep,
                        confornet_index: form.referenceConfornetIndex, sample_index: form.referenceSampleIndex,
                    };
                    payload.state_landscape_comparison = {
                        mode: 'reference', target_id: targetId, scope: 'all_other_within_target',
                        reference_backend_coordinates: referenceCoordinates,
                    };
                }
            }
        }
        return payload;
    };

    const effectivePayload = validationErrors.length === 0 ? buildPayload() : null;
    const analysisPolicySummary = effectivePayload
        ? `analysis ${effectivePayload.analysis_policy.clash_detector_id}/${effectivePayload.analysis_policy.clash_detector_version} · zero ε ${effectivePayload.analysis_policy.sign_zero_epsilon} · support ${effectivePayload.analysis_policy.outer_support_minimum}/${effectivePayload.analysis_policy.inner_support_minimum} · sign ${effectivePayload.analysis_policy.sign_consistency_minimum} · clash-free ${effectivePayload.analysis_policy.clash_free_minimum} · rank ${effectivePayload.analysis_policy.rank_stability_minimum} · common universe ${effectivePayload.analysis_policy.minimum_common_ranked_universe_size}`
        : 'analysis unresolved';
    const scientificSummary = effectivePayload
        ? (() => {
            if (effectivePayload.backend === 'protenix_v2_ensemble') {
                const feature = effectivePayload.feature_policy;
                const featureSummary = feature.mode === 'features_disabled_control_v1'
                    ? feature.mode
                    : `${feature.mode} · protein MSA ${feature.protein_msa_enabled ? 'on' : 'off'} · templates ${feature.templates_enabled ? 'on' : 'off'} · RNA MSA ${feature.rna_msa_enabled ? 'on' : 'off'}`;
                const runtime = effectivePayload.runtime_policy.use_default_params
                    ? 'installed runtime defaults'
                    : `${effectivePayload.runtime_policy.n_cycle} cycles / ${effectivePayload.runtime_policy.n_step} steps`;
                const comparison = effectivePayload.state_landscape_comparison
                    ? `state comparison ${effectivePayload.state_landscape_comparison.target_id}`
                    : 'state comparison off';
                return `${effectivePayload.ordered_seeds.join(', ')} ordered seeds · ${effectivePayload.samples_per_seed} samples/seed · ${featureSummary} · ${runtime} · ${comparison} · ${analysisPolicySummary}`;
            }
            if (effectivePayload.backend === 'confornets') {
                const settings = effectivePayload.confornets;
                const policy = selectedSource?.submission_policy;
                const references = (effectivePayload.registered_reference_ids || []).map((sourceId) => {
                    const source = structureSources.find((item) => item.source_id === sourceId);
                    return source ? `${source.source_id}@${source.sha256}` : `${sourceId}@unresolved`;
                }).join(', ') || 'none';
                const transfer = effectivePayload.registered_transfer_id
                    ? `${effectivePayload.registered_transfer_id}@${sourceRegistry.find((source) => source.source_id === effectivePayload.registered_transfer_id)?.sha256 || 'unresolved'}`
                    : 'none';
                return `${String(settings?.task || 'task unresolved')} · seed ${effectivePayload.ordered_seeds[0]} · ${effectivePayload.samples_per_seed} samples/coordinate · ${String(settings?.runs ?? 'unresolved')} runs · ${String(settings?.confornet_count ?? 'unresolved')} networks · steps ${Array.isArray(settings?.saved_steps) ? settings.saved_steps.join(', ') : 'unresolved'} · checkpoint ${selectedCheckpoint ? `${selectedCheckpoint.source_id}@${selectedCheckpoint.sha256}` : 'unresolved'} · config ${selectedConfig ? `${selectedConfig.source_id}@${selectedConfig.sha256}` : 'installed defaults'} · chain ${policy?.chain_id || 'unresolved'} · test ${policy?.test_case_id || 'unresolved'} · benchmark ${policy?.benchmark_name || 'unresolved'} · references ${references} · transfer ${transfer} · ${String(settings?.num_recycles ?? 'unresolved')} recycles · ${String(settings?.num_diffusion_steps ?? 'unresolved')} diffusion steps · LR ${String(settings?.learning_rate ?? 'unresolved')} · clip ${String(settings?.gradient_clip ?? 'unresolved')} · MSA ${settings?.skip_msa ? 'skipped' : 'enabled'} · confidence ${settings?.compute_confidence ? 'on' : 'off'} · full confidence ${settings?.save_full_confidence ? 'on' : 'off'} · evaluation ${settings?.compute_evaluation ? 'on' : 'off'} · ${analysisPolicySummary}`;
            }
            return `${selectedSource ? `${selectedSource.source_id}@${selectedSource.sha256}` : 'source unresolved'} · immutable mmCIF normalized at admission · ${effectivePayload.feature_policy.mode} · installed runtime defaults · ${analysisPolicySummary}`;
        })()
        : 'Resolve blocking issues to compile the normalized request.';
    const expectedOutputsSummary = effectivePayload?.backend === 'confornets'
        ? `Native structures · saved-step artifacts · residue maps${effectivePayload.confornets?.compute_confidence ? ' · confidence' : ''}${effectivePayload.confornets?.compute_evaluation ? ' · evaluation' : ''} · FrustraMPNN landscapes · support and ranking receipts`
        : effectivePayload?.backend === 'external_import'
            ? 'Normalized structure snapshot · residue map · FrustraMPNN landscape · support and ranking receipts'
            : 'Native structures · residue maps · state-conditioned FrustraMPNN landscapes · support and ranking receipts';

    const submit = useMutation({
        mutationFn: async () => (services?.submitRequest || submitCmRequest)(buildPayload()),
        onSuccess: (receipt) => {
            setIdempotencyKey(crypto.randomUUID());
            navigate(`/designs/${receipt.request_id}`, { state: { cmSubmissionReceipt: receipt } });
        },
        onError: (value) => setError(cmApiError(value, 'Conformational-mapping submission failed.')),
    });

    const taskChanged = (task: CmTask) => setForm((current) => ({
        ...current, task,
        referenceIds: task === 'mse' ? current.referenceIds : [],
        transferId: task === 'transfer' ? current.transferId : '',
        networks: task === 'diversity' ? Math.max(2, current.networks) : 1,
        runs: task === 'transfer' ? 1 : current.runs,
        savedSteps: task === 'transfer' ? '0' : task === 'mse' ? String(current.maxSteps) : current.savedSteps,
    }));

    const uploadSourceKinds = useMemo(() => SOURCE_KINDS.filter((item) => {
        if (form.backend === 'protenix_v2_ensemble') return item.value === 'complex_snapshot';
        if (form.backend === 'external_import') {
            // Prior-run authority is issued only by the typed Your Runs
            // registration flow. A caller cannot upload and self-declare it.
            return item.value === 'structure_upload';
        }
        return item.value === 'protein_sequence'
            || (form.task === 'mse' && item.value === 'structure_upload')
            || (form.task === 'transfer' && item.value === 'confornets_state');
    }), [form.backend, form.task]);
    const checkpointSources = byKind('confornets_checkpoint').filter((source) =>
        source.managed_checkpoint === true);
    useEffect(() => {
        if (!uploadSourceKinds.some((item) => item.value === sourceKind)) {
            setSourceKind(uploadSourceKinds[0].value);
        }
    }, [sourceKind, uploadSourceKinds]);
    const sourceAccept = uploadSourceKinds.find((item) => item.value === sourceKind)?.accept;
    const planningWarning = expectedCount > 100 || estimatedStorageGiB > 10;
    const sourcePreviewUrl = localPreviewUrl || (
        selectedSource && ['structure_upload', 'structure_artifact'].includes(selectedSource.source_kind)
            ? cmSourceContentUrl(selectedSource.source_id)
            : null
    );
    const selectedSourceId = selectedSource?.source_id || '';
    const backendLabel = form.backend === 'protenix_v2_ensemble'
        ? 'Protenix v2 ensemble'
        : form.backend === 'confornets' ? 'ConforNets' : 'Immutable structure import';
    const cardClass = 'rounded-2xl border border-slate-800 bg-slate-900/70 p-4 sm:p-5';
    const backendChanged = (backend: CmBackend) => {
        setForm((current) => ({
            ...current,
            backend,
            referenceBackend: backend,
            seeds: backend === 'confornets' ? String(integerList(current.seeds)?.[0] ?? 101) : current.seeds,
        }));
        setSourceKind(backend === 'protenix_v2_ensemble'
            ? 'complex_snapshot' : backend === 'confornets' ? 'protein_sequence' : 'structure_upload');
    };
    const selectSourceTab = (tab: SourceTab) => {
        setActiveSourceTab(tab);
        if (tab !== 'upload') setSourceFile(null);
    };
    const handleSourceTabKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>, index: number) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const nextIndex = event.key === 'Home' ? 0
            : event.key === 'End' ? SOURCE_TABS.length - 1
                : (index + (event.key === 'ArrowRight' ? 1 : -1) + SOURCE_TABS.length) % SOURCE_TABS.length;
        const nextTab = SOURCE_TABS[nextIndex].value;
        selectSourceTab(nextTab);
        requestAnimationFrame(() => document.getElementById(`cm-source-tab-${nextTab}`)?.focus());
    };
    const selectedRcsbChain = selectedRcsbEntry?.chains.find((chain) => chain.chain_id === rcsbChainId);
    const rcsbSelectionReady = Boolean(
        selectedRcsbEntry
        && selectedRcsbEntry.models.some((model) => model.model_id === rcsbModelId)
        && selectedRcsbEntry.samples.some((sample) => sample.sample_id === rcsbSampleId)
        && selectedRcsbChain
        && selectedRcsbEntry.entities.some((entity) => entity.entity_id === rcsbEntityId)
        && selectedRcsbChain.entity_id === rcsbEntityId,
    );
    const rcsbSelectionSummary = selectedRcsbEntry
        ? `${selectedRcsbEntry.accession} · model ${rcsbModelId || 'unresolved'} · sample ${rcsbSampleId || 'unresolved'} · chain ${rcsbChainId || 'unresolved'} · entity ${rcsbEntityId || 'unresolved'}`
        : null;
    const runIdentity = (run: CmReusableRun): string => run.request_id || run.run_id || run.job_id;
    const runLabel = (run: CmReusableRun): string => run.name || run.run_name || runIdentity(run);
    const artifactLabel = (artifact: CmReusableRun['artifacts'][number]): string => artifact.name || artifact.candidate_id || artifact.artifact_id;
    const selectedInputContext = selectedInputIdentity ? [
        `provider ${metadataText(selectedInputIdentity.provider)}`,
        `accession ${metadataText(selectedInputIdentity.accession)}`,
        `model ${metadataText(selectedInputIdentity.model)}`,
        `sample ${metadataText(selectedInputIdentity.sample)}`,
        `chains ${metadataText(selectedInputIdentity.chains)}`,
        `entities ${metadataText(selectedInputIdentity.entities)}`,
        `coordinate ${metadataText(selectedInputIdentity.coordinate)}`,
    ].filter((item) => !item.endsWith('—')).join(' · ') : 'Server-owned source identity unavailable.';
    const hasSelectedInputContext = Boolean(selectedInputIdentity && [
        selectedInputIdentity.provider,
        selectedInputIdentity.accession,
        selectedInputIdentity.model,
        selectedInputIdentity.sample,
        selectedInputIdentity.chains,
        selectedInputIdentity.entities,
    ].some((value) => value != null && value !== '' && (!Array.isArray(value) || value.length > 0)));
    const comparisonControls = form.backend === 'external_import' ? (
        <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/30 p-3 text-xs text-slate-500">State-landscape comparison is unavailable for a single imported coordinate. Register one immutable structure and compare it in the canonical results viewer.</div>
    ) : (
        <div className="mt-4 space-y-3 rounded-xl border border-slate-800 bg-slate-950/30 p-3">
            <label className="block space-y-1 text-sm">State-landscape comparison<select aria-label="State-landscape comparison mode" value={form.stateComparisonMode} onChange={(event) => update('stateComparisonMode', event.target.value as LauncherState['stateComparisonMode'])} className={inputClass}><option value="off">Off</option><option value="pairwise">Pairwise within target</option><option value="reference">Compare against explicit reference coordinate</option></select></label>
            {form.stateComparisonMode !== 'off' && <>
                <label className="block space-y-1 text-sm">Comparison target ID<input value={form.stateComparisonTargetId} onChange={(event) => update('stateComparisonTargetId', event.target.value)} placeholder="Target identifier" className={inputClass} /></label>
                {form.stateComparisonMode === 'reference' && form.backend === 'protenix_v2_ensemble' && <div className="grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-sm">Reference ordered seed<input aria-label="Reference ordered seed" type="number" value={form.referenceOrderedSeed} onChange={(event) => update('referenceOrderedSeed', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-sm">Reference sample index<input aria-label="Reference sample index" type="number" min={0} value={form.referenceSampleIndex} onChange={(event) => update('referenceSampleIndex', Number(event.target.value))} className={inputClass} /></label><p className="sm:col-span-2 text-xs text-slate-500">Reference backend: Protenix v2 ensemble. The server resolves the registered snapshot and candidate authority.</p></div>}
                {form.stateComparisonMode === 'reference' && form.backend === 'confornets' && <div className="grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-sm">Reference task<select aria-label="Reference ConforNets task" value={form.referenceTask} onChange={(event) => update('referenceTask', event.target.value as CmTask)} className={inputClass}><option value="diversity">Diversity</option><option value="mse">Reference-guided MSE</option><option value="transfer">Transfer state</option></select></label>{form.referenceTask === 'mse' && <label className="space-y-1 text-sm">Reference structure ID<input aria-label="Reference structure ID" value={form.referenceId} onChange={(event) => update('referenceId', event.target.value)} className={inputClass} /></label>}<label className="space-y-1 text-sm">Reference run index<input aria-label="Reference run index" type="number" min={0} value={form.referenceRunIndex} onChange={(event) => update('referenceRunIndex', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-sm">Reference saved step<input aria-label="Reference saved step" type="number" min={0} value={form.referenceSavedStep} onChange={(event) => update('referenceSavedStep', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-sm">Reference ConforNet index<input aria-label="Reference ConforNet index" type="number" min={0} value={form.referenceConfornetIndex} onChange={(event) => update('referenceConfornetIndex', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-sm">Reference sample index<input aria-label="Reference sample index" type="number" min={0} value={form.referenceSampleIndex} onChange={(event) => update('referenceSampleIndex', Number(event.target.value))} className={inputClass} /></label><p className="sm:col-span-2 text-xs text-slate-500">Reference backend: canonical ConforNets. Chain, test case, and benchmark identity remain server-owned.</p></div>}
            </>}
        </div>
    );

    return (
        <div className="w-full space-y-5 text-slate-200" data-bms-cm-launcher="canonical">
            <header className="rounded-2xl border border-slate-700 bg-slate-900/80 p-4 sm:p-5">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-orange-300">Canonical operator launcher</p>
                        <h2 className="mt-1 text-2xl font-semibold text-white">Conformational Mapping</h2>
                        <p className="mt-2 max-w-5xl text-sm leading-6 text-slate-300">Create structural hypotheses from immutable inputs. Review source identity, scientific settings, and expected output before admission.</p>
                    </div>
                    {onBack && <button type="button" onClick={onBack} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300">Back</button>}
                </div>
                <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4" aria-label="Conformational Mapping workflow stages">
                    {[
                        ['1', 'Complete-complex Protenix v2 ensembles'],
                        ['2', 'Residue mapping'],
                        ['3', 'FrustraMPNN landscapes'],
                        ['4', 'Support + ranking'],
                    ].map(([number, title]) => <div key={number} className="rounded-xl border border-slate-800 bg-slate-950/40 px-3 py-2 text-xs"><span className="mr-2 text-orange-300">{number}</span>{title}</div>)}
                </div>
                <ModelDocumentationLinks topics={['protenix', 'confornets', 'fampnn']} summary="Primary Protenix v2 ensemble generation, canonical ConforNets, and FrustraMPNN analysis references." compact className="mt-4" />
            </header>

            <div className="grid gap-5 xl:grid-cols-2">
                <section className={`${cardClass} order-1 xl:order-1`} aria-labelledby="cm-run-record-heading">
                    <div className="flex items-start justify-between gap-3">
                        <div><p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-orange-300">1</p><h3 id="cm-run-record-heading" className="mt-1 font-semibold text-white">Run record</h3><p className="mt-1 text-xs text-slate-500">Name, notes, and derived details</p></div>
                        <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${validationErrors.length ? 'bg-amber-500/10 text-amber-200' : 'bg-emerald-500/10 text-emerald-200'}`}>{validationErrors.length ? 'Draft' : 'Ready'}</span>
                    </div>
                    <div className="mt-4 grid gap-4 lg:grid-cols-2">
                        <label className="space-y-1 text-sm">Run name<input value={form.name} maxLength={255} onChange={(event) => update('name', event.target.value)} className={inputClass} /></label>
                        <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-3 text-xs"><span className="text-slate-500">Effective workflow</span><div className="mt-1 font-medium text-white">{backendLabel}</div><span className="mt-2 block text-slate-500">Planned output</span><div className="mt-1 text-white">{expectedCount.toLocaleString()} candidate{expectedCount === 1 ? '' : 's'}</div></div>
                        <label className="space-y-1 text-sm lg:col-span-2">Notes<textarea value={form.notes} maxLength={4000} rows={5} onChange={(event) => update('notes', event.target.value)} className={inputClass} placeholder="Purpose, hypothesis, or handling context" /><span className="block text-right text-[11px] text-slate-600">{form.notes.length.toLocaleString()} / 4,000</span></label>
                    </div>
                    <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
                        <div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Owner</span><div className="mt-1 text-slate-200">Personal workflow</div></div>
                        <div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Input</span><div className="mt-1 truncate text-slate-200">{selectedSource ? sourceLabel(selectedSource) : 'Not selected'}</div></div>
                        <div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Chains</span><div className="mt-1 text-slate-200">{availableChainIds.join(', ') || 'Derived at server normalization'}</div></div>
                        <div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Validation</span><div className="mt-1 text-slate-200">{validationErrors.length ? `${validationErrors.length} blocking` : 'Typed request ready'}</div></div>
                    </div>
                </section>

                <section className={`${cardClass} order-4 xl:order-2`} aria-labelledby="cm-science-heading">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-orange-300">2</p><h3 id="cm-science-heading" className="mt-1 font-semibold text-white">Scientific controls</h3><p className="mt-1 text-xs text-slate-500">One workflow and backend authority</p>
                    <div className="mt-4 grid gap-2 md:grid-cols-3">
                        {([
                            ['protenix_v2_ensemble', 'Complete-complex ensemble', 'Target × seed × sample'],
                            ['confornets', 'Canonical ConforNets', 'One explicit seed and chain'],
                            ['external_import', 'Immutable import', 'One registered protein mmCIF'],
                        ] as const).map(([value, title, detail]) => <button key={value} type="button" onClick={() => backendChanged(value)} aria-pressed={form.backend === value} className={`rounded-xl border p-3 text-left ${form.backend === value ? 'border-orange-400/60 bg-orange-500/10' : 'border-slate-800 bg-slate-950/30 hover:border-slate-700'}`}><span className="text-sm font-medium text-white">{title}</span><span className="mt-1 block text-xs text-slate-500">{detail}</span></button>)}
                    </div>
                    {form.backend === 'protenix_v2_ensemble' && <div className="mt-4 space-y-4">
                        <div className="grid gap-4 md:grid-cols-2"><label className="space-y-1 text-sm">Ordered seeds<input value={form.seeds} onChange={(event) => update('seeds', event.target.value)} className={inputClass} inputMode="numeric" /></label><label className="space-y-1 text-sm">Samples per seed<span className="flex items-center gap-3"><input type="range" min={1} max={100} value={form.samples} onChange={(event) => update('samples', Number(event.target.value))} className="w-full accent-orange-500" /><output className="w-10 text-right text-white">{form.samples}</output></span></label><label className="space-y-1 text-sm md:col-span-2">Feature policy<select value={form.featureMode} onChange={(event) => update('featureMode', event.target.value as CmFeaturePolicy['mode'])} className={inputClass}><option value="regenerate_mutated_protein_v1">Regenerate changed protein</option><option value="paired_regenerate_changed_protein_v1">Regenerate matched WT and mutant</option><option value="features_disabled_control_v1">Feature-disabled control</option></select></label></div>
                        <div className="grid gap-2 sm:grid-cols-3">{([['proteinMsa', 'Protein MSA'], ['templates', 'Templates'], ['rnaMsa', 'RNA MSA']] as const).map(([key, label]) => <label key={key} className="flex items-center gap-2 rounded-lg border border-slate-800 p-3 text-sm"><input type="checkbox" checked={form[key]} disabled={form.featureMode === 'features_disabled_control_v1'} onChange={(event) => update(key, event.target.checked)} className={checkClass} />{label}</label>)}</div>
                        {comparisonControls}
                    </div>}
                    {form.backend === 'confornets' && <div className="mt-4 space-y-4">
                        <div className="grid gap-4 md:grid-cols-2"><label className="space-y-1 text-sm">Task<select value={form.task} onChange={(event) => taskChanged(event.target.value as CmTask)} className={inputClass}><option value="diversity">Diversity</option><option value="mse">Reference-guided MSE</option><option value="transfer">Transfer state</option></select></label><label className="space-y-1 text-sm">Explicit seed<input type="number" value={form.seeds} onChange={(event) => update('seeds', event.target.value)} className={inputClass} /></label><label className="space-y-1 text-sm md:col-span-2">Samples per coordinate<span className="flex items-center gap-3"><input type="range" min={1} max={100} value={form.samples} onChange={(event) => update('samples', Number(event.target.value))} className="w-full accent-orange-500" /><output className="w-10 text-right text-white">{form.samples}</output></span></label></div>
                        {(form.task === 'diversity' || form.task === 'mse') && <div className="grid gap-4 md:grid-cols-2"><label className="space-y-1 text-sm">Runs<input type="number" min={1} value={form.runs} onChange={(event) => update('runs', Number(event.target.value))} className={inputClass} /></label>{form.task === 'diversity' && <label className="space-y-1 text-sm">Networks<input type="number" min={2} value={form.networks} onChange={(event) => update('networks', Number(event.target.value))} className={inputClass} /></label>}{form.task === 'diversity' && <label className="space-y-1 text-sm">Saved steps<input value={form.savedSteps} onChange={(event) => update('savedSteps', event.target.value)} className={inputClass} /></label>}<label className="space-y-1 text-sm">Maximum steps<input type="number" min={1} value={form.maxSteps} onChange={(event) => update('maxSteps', Number(event.target.value))} className={inputClass} /></label></div>}
                        {form.task === 'mse' && <label className="block space-y-1 text-sm">One or two registered references<select multiple value={form.referenceIds} onChange={(event) => update('referenceIds', Array.from(event.target.selectedOptions, (option) => option.value).slice(0, 2))} className={`${inputClass} min-h-24`}>{structureSources.map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></label>}
                        {form.task === 'transfer' && <label className="block space-y-1 text-sm">Registered transfer state<select value={form.transferId} onChange={(event) => update('transferId', event.target.value)} className={inputClass}><option value="">Select…</option>{byKind('confornets_state').map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></label>}
                        {comparisonControls}
                    </div>}
                    {form.backend === 'external_import' && <div className="mt-4 rounded-xl border border-sky-500/20 bg-sky-500/5 p-3 text-sm text-sky-100"><strong>External import accepts registered mmCIF handles only.</strong><p className="mt-1 text-xs leading-5 text-slate-400">Snapshot and residue identity are derived server-side from immutable staged bytes. Ambiguous structures fail closed.</p></div>}
                    {form.backend !== 'external_import' && <details className="mt-4 rounded-xl border border-slate-800 p-3">
                        <summary className="cursor-pointer text-sm font-medium text-slate-300">Advanced settings</summary>
                        {form.backend === 'protenix_v2_ensemble' && <label className="mt-3 flex items-center gap-2 text-xs"><input type="checkbox" checked={form.defaultRuntime} onChange={(event) => update('defaultRuntime', event.target.checked)} className={checkClass} />Use installed runtime defaults</label>}
                        {!form.defaultRuntime && form.backend === 'protenix_v2_ensemble' && <div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs text-slate-400">Recycling cycles<input type="number" min={1} value={form.nCycle} onChange={(event) => update('nCycle', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-xs text-slate-400">Sampling steps<input type="number" min={1} value={form.nStep} onChange={(event) => update('nStep', Number(event.target.value))} className={inputClass} /></label></div>}
                        {form.backend === 'confornets' && <>
                            {checkpointSources.length > 1 ? <label className="mt-3 block space-y-1 text-xs text-slate-400">Checkpoint authority<select value={form.checkpointId} onChange={(event) => update('checkpointId', event.target.value)} className={inputClass}><option value="">Select…</option>{checkpointSources.map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></label> : <div className="mt-3 rounded-lg border border-slate-800 p-3 text-xs"><span className="text-slate-500">Canonical checkpoint</span><div className="mt-1 text-slate-200">{checkpointSources[0] ? sourceLabel(checkpointSources[0]) : 'No registered checkpoint authority'}</div></div>}
                            <div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs text-slate-400">Recycles<input type="number" min={0} value={form.numRecycles} onChange={(event) => update('numRecycles', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-xs text-slate-400">Diffusion steps<input type="number" min={1} value={form.numDiffusionSteps} onChange={(event) => update('numDiffusionSteps', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-xs text-slate-400">Learning rate<input type="number" min="0.0000001" step="0.0001" value={form.learningRate} onChange={(event) => update('learningRate', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-xs text-slate-400">Gradient clip<input type="number" min="0.0000001" value={form.gradientClip} onChange={(event) => update('gradientClip', Number(event.target.value))} className={inputClass} /></label></div>
                            <div className="mt-3 grid gap-2 sm:grid-cols-2">{([['skipMsa', 'Skip MSA'], ['computeConfidence', 'Compute confidence'], ['saveFullConfidence', 'Save full confidence'], ['computeEvaluation', 'Compute evaluation']] as const).map(([key, label]) => <label key={key} className="flex items-center gap-2 text-xs"><input type="checkbox" checked={form[key]} onChange={(event) => update(key, event.target.checked)} className={checkClass} />{label}</label>)}</div>
                        </>}
                        <p className="mt-3 text-xs text-slate-500">Backend identity, detector identity, benchmark IDs, and admission policy remain server-derived.</p>
                    </details>}
                </section>

                <section className={`${cardClass} order-2 xl:order-3`} aria-labelledby="cm-source-browser-heading">
                    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-orange-300">3</p><h3 id="cm-source-browser-heading" className="mt-1 font-semibold text-white">Source browser</h3><p className="mt-1 text-xs text-slate-500">Choose an immutable input</p></div><span className="text-xs text-slate-500">{sourceRegistry.length} registered</span></div>
                    <div className="mt-4 flex flex-wrap gap-2" role="tablist" aria-label="CM input sources">{SOURCE_TABS.map((tab, index) => <button key={tab.value} id={`cm-source-tab-${tab.value}`} aria-controls={`cm-source-panel-${tab.value}`} type="button" role="tab" aria-selected={activeSourceTab === tab.value} tabIndex={activeSourceTab === tab.value ? 0 : -1} onClick={() => selectSourceTab(tab.value)} onKeyDown={(event) => handleSourceTabKeyDown(event, index)} className={`rounded-lg border px-3 py-2 text-xs font-medium ${activeSourceTab === tab.value ? 'border-orange-400/60 bg-orange-500/10 text-orange-100' : 'border-slate-800 text-slate-400'}`}>{tab.label}</button>)}</div>
                    {activeSourceTab === 'upload' && <div id="cm-source-panel-upload" role="tabpanel" aria-labelledby="cm-source-tab-upload" className="mt-4 space-y-4">
                        <div className="grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs text-slate-400">Source type<select value={sourceKind} onChange={(event) => setSourceKind(event.target.value as CmSourceKind)} className={inputClass}>{uploadSourceKinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label className="space-y-1 text-xs text-slate-400">Local file<input type="file" accept={sourceAccept} onChange={(event) => { const file = event.target.files?.[0] || null; setSourceFile(file); if (file && form.backend === 'external_import' && sourceKind === 'structure_upload') update('importIds', []); }} className={inputClass} /></label><label className="space-y-1 text-xs text-slate-400">{sourceKind === 'protein_sequence' ? 'Target ID' : 'Optional source label'}<input value={sourceTargetId} onChange={(event) => setSourceTargetId(event.target.value)} className={inputClass} /></label><button type="button" disabled={!sourceFile || register.isPending} onClick={() => sourceFile && register.mutate({ file: sourceFile, kind: sourceKind })} className="self-end rounded-lg bg-orange-500 px-4 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40">Register</button></div>
                        {sourceKind === 'structure_upload' && <label className="block max-w-sm space-y-1 text-xs text-slate-400">Reference state label<input value={sourceState} onChange={(event) => setSourceState(event.target.value)} className={inputClass} /></label>}
                        {sourceKind === 'confornets_state' && <div className="grid max-w-2xl gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs text-slate-400">Transfer-state kind<select value={transferKind} onChange={(event) => setTransferKind(event.target.value as typeof transferKind)} className={inputClass}><option value="confornet_state">ConforNet state</option><option value="mse_state">MSE state</option></select></label><label className="space-y-1 text-xs text-slate-400">Source test cases<input value={sourceTestCases} onChange={(event) => setSourceTestCases(event.target.value)} className={inputClass} /></label></div>}
                        {sourceKind === 'protein_sequence' && <section className="rounded-xl border border-sky-500/25 bg-sky-500/5 p-3" aria-label="Paste protein sequence"><div className="flex items-center justify-between gap-2"><h4 className="text-sm font-medium text-sky-100">Paste protein sequence</h4><span className="text-xs text-slate-500">{pastedSequence.replace(/\s+/g, '').length.toLocaleString()} residues</span></div><textarea value={pastedSequence} onChange={(event) => setPastedSequence(event.target.value)} rows={5} spellCheck={false} className={`${inputClass} mt-3 font-mono text-xs`} placeholder="MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG" /><button type="button" disabled={!pastedSequence.trim() || register.isPending} onClick={registerPastedSequence} className="mt-3 rounded-lg border border-sky-400/40 px-3 py-2 text-sm text-sky-100 disabled:opacity-40">Register and select sequence</button></section>}
                        {sourceKind === 'complex_snapshot' && <details className="rounded-xl border border-slate-800 bg-slate-950/40 p-3"><summary className="cursor-pointer text-sm font-medium text-slate-300">Complete-complex snapshot editor</summary><textarea value={snapshotEditor} onChange={(event) => setSnapshotEditor(event.target.value)} rows={8} spellCheck={false} className={`${inputClass} mt-3 font-mono text-xs`} placeholder='{"schema_name":"cm_complex_snapshot",...}' /><button type="button" disabled={!snapshotEditor.trim() || register.isPending} onClick={registerSnapshotEditor} className="mt-3 rounded-lg border border-orange-400/40 px-3 py-2 text-sm text-orange-200 disabled:opacity-40">Validate and register snapshot</button></details>}
                    </div>}
                    {activeSourceTab === 'runs' && <div id="cm-source-panel-runs" role="tabpanel" aria-labelledby="cm-source-tab-runs" className="mt-4 space-y-3">
                        <div className="rounded-xl border border-sky-500/20 bg-sky-500/5 p-3 text-xs leading-5 text-sky-100">Completed artifacts remain discoverable here, but they become CM input authority only after you explicitly register one artifact.</div>
                        <label className="block max-w-sm space-y-1 text-xs text-slate-400">Upload source type<select aria-label="CM upload source type" value={sourceKind} onChange={(event) => setSourceKind(event.target.value as CmSourceKind)} className={inputClass}>{uploadSourceKinds.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
                        {reusableRuns.isLoading && <p className="text-sm text-slate-500">Loading your completed runs…</p>}
                        {reusableRuns.isError && <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{cmApiError(reusableRuns.error, 'Unable to load your completed runs.')}</div>}
                        {!reusableRuns.isLoading && !reusableRuns.isError && !reusableRuns.data?.length && <div className="rounded-xl border border-dashed border-slate-800 p-6 text-center text-sm text-slate-500">No completed reusable runs are available.</div>}
                        {reusableRuns.data?.map((run) => <article key={runIdentity(run)} className="rounded-xl border border-slate-800 bg-slate-950/30 p-3">
                            <div className="flex flex-wrap items-start justify-between gap-2"><div><h4 className="text-sm font-medium text-white">{runLabel(run)}</h4><p className="mt-1 text-xs text-slate-500">{run.workflow} · {run.status}{run.completed_at ? ` · completed ${run.completed_at}` : ''}</p></div><span className="font-mono text-[11px] text-slate-600">{runIdentity(run)}</span></div>
                            <div className="mt-3 space-y-2">{run.artifacts.map((artifact) => <div key={artifact.artifact_id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 p-3"><div className="min-w-0"><div className="truncate text-sm text-slate-200">{artifactLabel(artifact)}</div><div className="mt-1 text-[11px] leading-5 text-slate-500">{artifact.format} · {artifact.sha256} · model {artifact.model_id || '—'} · sample {artifact.sample_id || '—'} · chain {(artifact.chain_ids || []).join(', ') || '—'} · entity {(artifact.entity_ids || []).join(', ') || '—'}</div></div><button type="button" disabled={registerRunArtifactMutation.isPending || artifact.available === false} onClick={() => registerRunArtifactMutation.mutate({ run, artifact })} className="shrink-0 rounded-lg border border-sky-400/40 px-3 py-2 text-xs text-sky-100 disabled:opacity-40">Use {artifactLabel(artifact)}</button></div>)}</div>
                        </article>)}
                    </div>}
                    {activeSourceTab === 'rcsb' && <div id="cm-source-panel-rcsb" role="tabpanel" aria-labelledby="cm-source-tab-rcsb" className="mt-4 rounded-xl border border-violet-500/25 bg-violet-500/5 p-3">
                        <h4 className="text-sm font-medium text-violet-100">RCSB PDB tie-in</h4>
                        <p className="mt-1 text-xs text-slate-400">Search the RCSB catalogue, inspect entry metadata, select the exact model/sample/chain/entity context, then register the immutable mmCIF.</p>
                        <div className="mt-3 flex max-w-xl gap-2"><input aria-label="RCSB accession or keyword" value={rcsbQuery} onChange={(event) => setRcsbQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') searchRcsbMutation.mutate(); }} placeholder="4HHB or deoxyhaemoglobin" className={inputClass} /><button type="button" disabled={searchRcsbMutation.isPending || rcsbQuery.trim().length < 2} onClick={() => searchRcsbMutation.mutate()} className="shrink-0 rounded-lg border border-violet-400/40 px-3 py-2 text-sm text-violet-100 disabled:opacity-40">{searchRcsbMutation.isPending ? 'Searching…' : 'Search RCSB'}</button></div>
                        {rcsbSearchResults && <div className="mt-4 space-y-2" aria-label="RCSB search results">{rcsbSearchResults.results.length ? rcsbSearchResults.results.map((entry) => <article key={entry.accession} className="rounded-xl border border-slate-800 bg-slate-950/30 p-3"><div className="flex flex-wrap items-start justify-between gap-3"><div><h5 className="text-sm font-medium text-white">{entry.title}</h5><p className="mt-1 text-xs text-slate-400">{entry.accession} · {entry.method || 'method unavailable'} · resolution {entry.resolution ?? '—'} Å · {entry.organism || 'organism unavailable'} · released {entry.release_date || '—'}</p></div><button type="button" onClick={() => selectRcsbEntry(entry)} className="shrink-0 rounded-lg border border-violet-400/40 px-3 py-2 text-xs text-violet-100">Select {entry.accession}</button></div></article>) : <p className="text-sm text-slate-500">No RCSB entries matched this search.</p>}</div>}
                        {selectedRcsbEntry && <div className="mt-4 rounded-xl border border-violet-400/30 bg-violet-500/5 p-3"><div className="text-xs font-semibold text-violet-100">Selected entry: {selectedRcsbEntry.accession}</div><div className="mt-1 text-xs text-slate-400">{selectedRcsbEntry.title} · {selectedRcsbEntry.method || 'method unavailable'} · {selectedRcsbEntry.organism || 'organism unavailable'}</div><div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs text-slate-400">Model<select aria-label="RCSB model" value={rcsbModelId} onChange={(event) => setRcsbModelId(event.target.value)} className={inputClass}><option value="">Select model…</option>{selectedRcsbEntry.models.map((model) => <option key={model.model_id} value={model.model_id}>{model.label || `Model ${model.model_id}`}</option>)}</select></label><label className="space-y-1 text-xs text-slate-400">Sample<select aria-label="RCSB sample" value={rcsbSampleId} onChange={(event) => setRcsbSampleId(event.target.value)} className={inputClass}><option value="">Select sample…</option>{selectedRcsbEntry.samples.map((sample) => <option key={sample.sample_id} value={sample.sample_id}>{sample.label || sample.sample_id}</option>)}</select></label><label className="space-y-1 text-xs text-slate-400">Chain<select aria-label="RCSB chain" value={rcsbChainId} onChange={(event) => setRcsbChainId(event.target.value)} className={inputClass}><option value="">Select chain…</option>{selectedRcsbEntry.chains.map((chain) => <option key={chain.chain_id} value={chain.chain_id}>{chain.label || chain.chain_id} · entity {chain.entity_id}</option>)}</select></label><label className="space-y-1 text-xs text-slate-400">Entity<select aria-label="RCSB entity" value={rcsbEntityId} onChange={(event) => setRcsbEntityId(event.target.value)} className={inputClass}><option value="">Select entity…</option>{selectedRcsbEntry.entities.map((entity) => <option key={entity.entity_id} value={entity.entity_id}>{entity.label || entity.entity_id} · {entity.entity_type}</option>)}</select></label></div><div className="mt-3 flex flex-wrap items-center justify-between gap-3"><span className="text-xs text-slate-400">{rcsbSelectionSummary}</span><button type="button" disabled={!rcsbSelectionReady || registerRcsbSelectionMutation.isPending} onClick={() => registerRcsbSelectionMutation.mutate()} className="rounded-lg border border-violet-400/40 px-3 py-2 text-sm text-violet-100 disabled:opacity-40">{registerRcsbSelectionMutation.isPending ? 'Registering…' : 'Register selected RCSB mmCIF'}</button></div></div>}
                        <div className="mt-4 space-y-2">{tabSources.length ? tabSources.map((source) => <button key={source.source_id} type="button" onClick={() => selectSource(source)} aria-pressed={selectedSourceId === source.source_id} className={`w-full rounded-xl border p-3 text-left ${selectedSourceId === source.source_id ? 'border-orange-400/60 bg-orange-500/10' : 'border-slate-800 bg-slate-950/30'}`}><span className="block text-sm font-medium text-white">{String(source.metadata.name || source.source_id)}</span><span className="mt-1 block truncate font-mono text-[11px] text-slate-500">{source.sha256}</span></button>) : <div className="rounded-lg border border-dashed border-violet-500/20 p-3 text-xs text-slate-500">No registered RCSB sources are available.</div>}</div>
                    </div>}
                    {activeSourceTab === 'cached' && <div id="cm-source-panel-cached" role="tabpanel" aria-labelledby="cm-source-tab-cached" className="mt-4 space-y-2">{tabSources.length ? tabSources.map((source) => <button key={source.source_id} type="button" onClick={() => selectSource(source)} aria-pressed={selectedSourceId === source.source_id} className={`w-full rounded-xl border p-3 text-left ${selectedSourceId === source.source_id ? 'border-orange-400/60 bg-orange-500/10' : 'border-slate-800 bg-slate-950/30'}`}><span className="block text-sm font-medium text-white">{String(source.metadata.name || source.metadata.target_id || source.source_id)}</span><span className="mt-1 block truncate font-mono text-[11px] text-slate-500">{source.source_kind} · {source.sha256} · {source.bytes.toLocaleString()} bytes</span></button>) : <div className="rounded-xl border border-dashed border-slate-800 p-6 text-center text-sm text-slate-500">No compatible sources are available in this view.</div>}</div>}
                    {sources.isError && <div role="alert" className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">{cmApiError(sources.error, 'Unable to load the authenticated source registry.')}</div>}
                </section>

                <section className={`${cardClass} order-3 xl:order-4`} aria-labelledby="cm-preview-heading">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-orange-300">4</p><h3 id="cm-preview-heading" className="mt-1 font-semibold text-white">Input preview</h3><p className="mt-1 text-xs text-slate-500">Resolved structure and selection context</p>
                    <div className="mt-4 overflow-hidden rounded-xl border border-slate-800 bg-slate-950/50">{sourcePreviewUrl ? <MolstarViewer structureUrl={sourcePreviewUrl} format="cif" height={340} hideControls label="Selected CM input preview" /> : <div className="flex h-[340px] items-center justify-center px-6 text-center text-sm text-slate-500">{selectedSource ? 'This immutable source has no browser-safe preview URL. Its identity remains available below.' : 'Select a structure source or choose a local mmCIF file to preview it.'}</div>}</div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2"><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Selected input</span><div className="mt-1 break-words text-white">{selectedSource ? String(selectedSource.metadata.name || selectedSource.metadata.target_id || selectedSource.source_id) : sourceFile?.name || 'None'}</div></div><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Immutable provenance</span><div className="mt-1 break-all font-mono text-white">{selectedSource?.sha256 || 'Created after registration'}</div></div></div>
                    {selectedSource && <div className="mt-3 rounded-xl border border-slate-800 p-3 text-xs" aria-label="Preview source identity"><span className="text-slate-500">Server-owned source identity</span><div className="mt-1 break-words leading-5 text-slate-200">{selectedInputContext}</div></div>}
                    <div className="mt-4"><div className="text-xs text-slate-500">{form.backend === 'confornets' ? 'Server-canonical chain' : 'Retained source chains'}</div>{availableChainIds.length ? <div className="mt-2 flex flex-wrap gap-2">{availableChainIds.map((chainId) => <span key={chainId} className="rounded-lg border border-slate-800 px-3 py-2 text-xs text-slate-300">Chain {chainId}</span>)}</div> : <p className="mt-2 text-xs text-slate-500">Chain identities are derived after server-side source normalization.</p>}</div>
                    <p className="mt-4 rounded-lg border border-sky-500/20 bg-sky-500/5 p-3 text-xs leading-5 text-sky-100">{CM_SCIENTIFIC_LIMIT}</p>
                </section>
            </div>

            <section className={`${cardClass} ${planningWarning ? 'border-amber-500/40' : ''}`} aria-labelledby="cm-summary-heading">
                <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-orange-300">5</p><h3 id="cm-summary-heading" className="mt-1 font-semibold text-white">Pre-submit summary</h3><p className="mt-1 text-xs text-slate-500">Effective request</p></div><span className={`rounded-full px-3 py-1 text-xs font-medium ${validationErrors.length ? 'bg-red-500/10 text-red-200' : 'bg-emerald-500/10 text-emerald-200'}`}>{validationErrors.length ? `${validationErrors.length} blocking issue${validationErrors.length === 1 ? '' : 's'}` : 'Ready for typed admission'}</span></div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-7"><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Run</span><div className="mt-1 text-white">{effectivePayload?.name || form.name || 'Unnamed'}</div></div><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Workflow</span><div className="mt-1 text-white">{backendLabel}</div></div><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Input authority</span><div className="mt-1 truncate text-white">{selectedSource ? String(selectedSource.metadata.name || selectedSource.metadata.target_id || selectedSource.source_id) : 'Not selected'}</div><div className="mt-1 truncate font-mono text-[11px] text-slate-400">{selectedSourceId || 'Source unresolved'}</div><div className="mt-1 truncate font-mono text-[11px] text-slate-500">{selectedSource ? `${selectedSource.source_kind} · ${selectedSource.sha256}` : 'Source unresolved'}</div><div className="mt-1 text-[11px] text-slate-500">{[form.backend === 'confornets' && selectedSource?.submission_policy && `server-canonical chain ${selectedSource.submission_policy.chain_id}`, form.backend !== 'confornets' && availableChainIds.length > 0 && `retained chains ${availableChainIds.join(', ')}`].filter(Boolean).join(' · ') || (hasSelectedInputContext ? 'Server-owned source context shown here' : 'Source identity unavailable')}</div></div><div className="rounded-xl border border-slate-800 p-3 text-xs" aria-label="Selected input context"><span className="text-slate-500">Selected input context</span><div className="mt-1 break-words leading-5 text-slate-200">{selectedInputContext}</div></div><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Candidate coordinates</span><div className="mt-1 text-white">{expectedCount.toLocaleString()}</div></div><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Planning storage</span><div className="mt-1 text-white">~{estimatedStorageGiB.toFixed(2)} GiB</div></div><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Notes</span><div className="mt-1 text-white">{effectivePayload?.notes ? 'Included in immutable run record' : 'None'}</div></div></div>
                <div className="mt-3 grid gap-3 lg:grid-cols-2"><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Effective scientific settings</span><div className="mt-1 leading-5 text-slate-200">{scientificSummary}</div></div><div className="rounded-xl border border-slate-800 p-3 text-xs"><span className="text-slate-500">Expected outputs</span><div className="mt-1 leading-5 text-slate-200">{expectedOutputsSummary}</div></div></div>
                {validationErrors.length > 0 && <ul className="mt-4 grid gap-2 text-sm text-red-200 sm:grid-cols-2">{validationErrors.map((message) => <li key={message} className="rounded-lg border border-red-500/20 bg-red-500/5 px-3 py-2">{message}</li>)}</ul>}
                {planningWarning && <p className="mt-4 text-xs font-medium text-amber-200">Large request: confirm GPU queue capacity and durable result storage before submission.</p>}
                {error && <div role="alert" className="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">{error}</div>}
                <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-slate-800 pt-4"><p className="max-w-3xl text-xs leading-5 text-slate-500">Submission uses <code>/api/conformational-mapping/requests</code>. The request persists notes, selected-input identity, source hash, model/sample context, chains, backend settings, and expected cardinality.</p><button type="button" disabled={validationErrors.length > 0 || submit.isPending || sources.isError} onClick={() => { setError(null); submit.mutate(); }} className="rounded-lg bg-orange-500 px-5 py-2.5 font-semibold text-slate-950 disabled:opacity-40">{submit.isPending ? 'Submitting authenticated request…' : 'Launch conformational mapping'}</button></div>
            </section>
        </div>
    );
}

export default ConformationalMappingLauncher;
