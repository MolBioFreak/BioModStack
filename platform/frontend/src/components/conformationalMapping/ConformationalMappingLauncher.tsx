import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
    cmApiError,
    listCmSources,
    registerCmRcsbMmcif,
    registerCmSource,
    submitCmRequest,
    type CmAnalysisPolicy,
    type CmBackend,
    type CmFeaturePolicy,
    type CmSource,
    type CmSourceKind,
    type CmSubmitRequest,
    type CmTask,
} from './conformationalMappingApi';
import { CM_SCIENTIFIC_LIMIT } from './conformationalMappingSemantics';
import { ModelDocumentationLinks } from '../ModelDocumentationLinks';

interface Props {
    onBack?: () => void;
    initialValues?: Record<string, unknown>;
    services?: {
        listSources?: typeof listCmSources;
        submitRequest?: typeof submitCmRequest;
    };
}

interface LauncherState {
    name: string;
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
    chainId: string;
    testCaseId: string;
    benchmarkName: string;
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
    stateComparisonTargetId: string;
    analysis: CmAnalysisPolicy;
}

const DEFAULT_ANALYSIS: CmAnalysisPolicy = {
    sign_zero_epsilon: 0.000001,
    clash_detector_id: 'bms_clash',
    clash_detector_version: '1',
    outer_support_minimum: 0.8,
    inner_support_minimum: 0.6,
    sign_consistency_minimum: 0.8,
    clash_free_minimum: 0.9,
    rank_stability_minimum: 0.6,
    minimum_common_ranked_universe_size: 3,
};

const DEFAULT_STATE: LauncherState = {
    name: 'Conformational mapping', backend: 'protenix_v2_ensemble', snapshotId: '',
    sequenceId: '', checkpointId: '', configId: '', transferId: '', referenceIds: [], importIds: [],
    seeds: '101,202,303,404,505', samples: 5,
    featureMode: 'regenerate_mutated_protein_v1', proteinMsa: true, templates: false, rnaMsa: false,
    defaultRuntime: true, nCycle: 10, nStep: 200,
    task: 'diversity', chainId: 'A', testCaseId: 'canonical-case', benchmarkName: 'biomodstack',
    runs: 2, networks: 2, savedSteps: '5,10,15,20', maxSteps: 20,
    numRecycles: 0, numDiffusionSteps: 200, learningRate: 0.001, gradientClip: 10,
    skipMsa: false, computeConfidence: true, saveFullConfidence: false, computeEvaluation: true,
    stateComparisonTargetId: '',
    analysis: DEFAULT_ANALYSIS,
};

const STATE_KEY = 'bms.conformational-mapping.launcher.v1';
const SOURCE_KINDS: Array<{ value: CmSourceKind; label: string; accept: string }> = [
    { value: 'complex_snapshot', label: 'Complete-complex snapshot JSON', accept: '.json,application/json' },
    { value: 'structure_upload', label: 'Protein mmCIF upload', accept: '.cif,.mmcif' },
    { value: 'structure_artifact', label: 'Protein mmCIF artifact', accept: '.cif,.mmcif' },
    { value: 'protein_sequence', label: 'Protein sequence', accept: '.txt,.fa,.fasta,text/plain' },
    { value: 'confornets_checkpoint', label: 'ConforNets checkpoint', accept: '.pt,.pth,.ckpt' },
    { value: 'confornets_config', label: 'ConforNets config', accept: '.json,.yaml,.yml' },
    { value: 'confornets_state', label: 'ConforNets transfer state', accept: '.pt,.pth,.ckpt' },
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
    const confornets = asObject(merged.confornets) || {};
    const feature = asObject(merged.feature_policy) || {};
    const runtime = asObject(merged.runtime_policy) || {};
    const analysis = asObject(merged.analysis_policy) || asObject(merged.analysis) || {};
    const orderedSeeds = Array.isArray(merged.ordered_seeds) ? merged.ordered_seeds.join(',') : merged.seeds;
    const savedSteps = Array.isArray(confornets.saved_steps) ? confornets.saved_steps.join(',') : merged.savedSteps;
    return {
        ...DEFAULT_STATE,
        ...merged,
        name: typeof merged.name === 'string' ? merged.name : DEFAULT_STATE.name,
        backend: ['protenix_v2_ensemble', 'confornets', 'external_import'].includes(String(merged.backend)) ? merged.backend as CmBackend : DEFAULT_STATE.backend,
        snapshotId: String(merged.registered_snapshot_id || merged.snapshotId || ''),
        sequenceId: String(merged.registered_sequence_id || merged.sequenceId || ''),
        checkpointId: String(merged.registered_checkpoint_id || merged.checkpointId || ''),
        configId: String(merged.registered_config_id || merged.configId || ''),
        transferId: String(merged.registered_transfer_id || merged.transferId || ''),
        referenceIds: asStringArray(merged.registered_reference_ids || merged.referenceIds),
        importIds: asStringArray(merged.registered_artifact_ids || merged.importIds),
        seeds: typeof orderedSeeds === 'string' ? orderedSeeds : DEFAULT_STATE.seeds,
        samples: finite(merged.samples_per_seed ?? merged.samples, DEFAULT_STATE.samples),
        featureMode: (feature.mode || merged.featureMode || DEFAULT_STATE.featureMode) as CmFeaturePolicy['mode'],
        proteinMsa: typeof feature.protein_msa_enabled === 'boolean' ? feature.protein_msa_enabled : Boolean(merged.proteinMsa ?? DEFAULT_STATE.proteinMsa),
        templates: typeof feature.templates_enabled === 'boolean' ? feature.templates_enabled : Boolean(merged.templates ?? DEFAULT_STATE.templates),
        rnaMsa: typeof feature.rna_msa_enabled === 'boolean' ? feature.rna_msa_enabled : Boolean(merged.rnaMsa ?? DEFAULT_STATE.rnaMsa),
        defaultRuntime: typeof runtime.use_default_params === 'boolean' ? runtime.use_default_params : Boolean(merged.defaultRuntime ?? true),
        nCycle: finite(runtime.n_cycle ?? merged.nCycle, DEFAULT_STATE.nCycle),
        nStep: finite(runtime.n_step ?? merged.nStep, DEFAULT_STATE.nStep),
        task: (confornets.task || merged.task || DEFAULT_STATE.task) as CmTask,
        chainId: String(confornets.chain_id || merged.chainId || DEFAULT_STATE.chainId),
        testCaseId: String(confornets.test_case_id || merged.testCaseId || DEFAULT_STATE.testCaseId),
        benchmarkName: String(confornets.benchmark_name || merged.benchmarkName || DEFAULT_STATE.benchmarkName),
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
        stateComparisonTargetId: String(asObject(merged.state_landscape_comparison)?.target_id || merged.stateComparisonTargetId || ''),
        analysis: { ...DEFAULT_ANALYSIS, ...analysis } as CmAnalysisPolicy,
    };
};

const sourceLabel = (source: CmSource): string => {
    const metadataLabel = source.metadata.name || source.metadata.target_id
        || (Array.isArray(source.metadata.target_ids) ? source.metadata.target_ids.join(', ') : null);
    return `${String(metadataLabel || source.source_id)} · ${(source.bytes / 1024).toFixed(1)} KiB · ${source.sha256.slice(0, 12)}`;
};

const integerList = (value: string): number[] | null => {
    const parts = value.split(',').map((item) => item.trim());
    if (!parts.length || parts.some((item) => !/^-?\d+$/.test(item))) return null;
    return parts.map(Number);
};

const inputClass = 'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-orange-400 disabled:cursor-not-allowed disabled:opacity-50';
const checkClass = 'h-4 w-4 rounded border-slate-600 bg-slate-950 text-orange-500 focus:ring-orange-500';

export function ConformationalMappingLauncher({ onBack, initialValues, services }: Props) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [form, setForm] = useState<LauncherState>(() => hydrateState(initialValues));
    const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
    const [sourceKind, setSourceKind] = useState<CmSourceKind>('complex_snapshot');
    const [sourceFile, setSourceFile] = useState<File | null>(null);
    const [sourceTargetId, setSourceTargetId] = useState('');
    const [sourceState, setSourceState] = useState('reference');
    const [transferKind, setTransferKind] = useState<'confornet_state' | 'mse_state'>('confornet_state');
    const [sourceTestCases, setSourceTestCases] = useState('');
    const [snapshotEditor, setSnapshotEditor] = useState('');
    const [pastedSequence, setPastedSequence] = useState('');
    const [rcsbPdbId, setRcsbPdbId] = useState('');
    const [error, setError] = useState<string | null>(null);

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
    const byKind = (kind: CmSourceKind) => (sources.data || []).filter((source) => source.source_kind === kind);
    const structureSources = useMemo(
        () => (sources.data || []).filter((source) => (
            ['structure_upload', 'structure_artifact'].includes(source.source_kind)
            && source.format === 'mmcif'
        )),
        [sources.data],
    );
    useEffect(() => {
        if (!sources.data) return;
        const admissible = new Set(structureSources.map((source) => source.source_id));
        setForm((current) => {
            if (current.backend !== 'external_import') return current;
            const next = current.importIds.filter((id) => admissible.has(id)).slice(0, 1);
            if (next.length === current.importIds.length
                && next.every((id, index) => id === current.importIds[index])) return current;
            return { ...current, importIds: next };
        });
    }, [sources.data, structureSources]);
    const selectedSnapshot = (sources.data || []).find((source) => source.source_id === form.snapshotId);
    const seedValues = useMemo(() => integerList(form.seeds), [form.seeds]);
    const stepValues = useMemo(() => integerList(form.savedSteps), [form.savedSteps]);
    const snapshotTargetIds = Array.isArray(selectedSnapshot?.metadata.target_ids)
        ? selectedSnapshot.metadata.target_ids.filter((item): item is string => typeof item === 'string') : [];
    const referenceFactor = form.task === 'mse' ? form.referenceIds.length : 1;
    const expectedCount = form.backend === 'confornets'
        ? referenceFactor * form.runs * (form.task === 'transfer' || form.task === 'mse' ? 1 : (stepValues?.length || 0))
            * (form.task === 'diversity' ? form.networks : 1) * form.samples
        : form.backend === 'external_import'
            ? form.importIds.length
            : Math.max(snapshotTargetIds.length, selectedSnapshot ? 1 : 0) * (seedValues?.length || 0) * form.samples;
    const selectedInputBytes = form.backend === 'external_import'
        ? form.importIds.reduce((total, id) => total + (sources.data?.find((source) => source.source_id === id)?.bytes || 0), 0)
        : selectedSnapshot?.bytes || 0;
    const planningMiBPerCandidate = form.backend === 'protenix_v2_ensemble' ? 80 : form.backend === 'confornets' ? 45 : 24;
    const estimatedStorageGiB = (selectedInputBytes + expectedCount * planningMiBPerCandidate * 1024 * 1024) / (1024 ** 3);

    const validationErrors = useMemo(() => {
        const errors: string[] = [];
        if (!form.name.trim()) errors.push('Request name is required.');
        if (!['regenerate_mutated_protein_v1', 'paired_regenerate_changed_protein_v1', 'features_disabled_control_v1'].includes(form.featureMode)) {
            errors.push('Feature policy mode is not an approved contract value.');
        }
        if (!Number.isInteger(form.samples) || form.samples < 1 || form.samples > 100) errors.push('Samples must be an integer from 1 to 100.');
        if (form.backend !== 'external_import') {
            if (!seedValues?.length || new Set(seedValues).size !== seedValues.length) errors.push('Ordered seeds must be unique integers.');
            if (seedValues?.some((seed) => seed < -2147483648 || seed > 2147483647)) errors.push('Seeds must be signed 32-bit integers.');
        }
        if (form.backend === 'protenix_v2_ensemble') {
            if (!form.snapshotId) errors.push('Select a registered complete-complex snapshot.');
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
            if (!form.checkpointId) errors.push('Select a registered canonical checkpoint.');
            if (!form.chainId.trim() || form.chainId.length > 8) errors.push('Chain ID must contain 1–8 characters.');
            if (!form.testCaseId.trim() || !form.benchmarkName.trim()) errors.push('Test-case and benchmark identities are required.');
            if (!Number.isInteger(form.runs) || form.runs < 1) errors.push('Runs must be a positive integer.');
            if (!Number.isInteger(form.samples) || form.samples < 1) errors.push('Samples must be a positive integer.');
            if (!Number.isInteger(form.maxSteps) || form.maxSteps < 1) errors.push('Maximum steps must be a positive integer.');
            if (form.task === 'diversity') {
                if (!Number.isInteger(form.networks) || form.networks < 2) errors.push('Diversity requires at least two networks.');
                if (!stepValues?.length || new Set(stepValues).size !== stepValues.length || stepValues.some((step) => step < 0 || step > form.maxSteps)) {
                    errors.push('Saved steps must be unique non-negative integers no greater than maximum steps.');
                }
            }
            if (form.task === 'mse' && (form.referenceIds.length < 1 || form.referenceIds.length > 2)) errors.push('MSE requires one or two registered references.');
            if (form.task === 'transfer' && !form.transferId) errors.push('Transfer requires a registered transfer-state handle.');
            if (![form.numRecycles, form.numDiffusionSteps].every(Number.isInteger) || form.numRecycles < 0 || form.numDiffusionSteps < 1) {
                errors.push('Recycle and diffusion-step controls are invalid.');
            }
            if (form.learningRate <= 0 || form.gradientClip <= 0) errors.push('Learning rate and gradient clip must be positive.');
            if (!seedValues || seedValues.length !== 1) errors.push('ConforNets requires exactly one explicit seed.');
        }
        const fractionThresholds = [
            form.analysis.outer_support_minimum,
            form.analysis.inner_support_minimum,
            form.analysis.sign_consistency_minimum,
            form.analysis.clash_free_minimum,
        ];
        if (form.analysis.sign_zero_epsilon <= 0 || fractionThresholds.some((value) => value < 0 || value > 1)) {
            errors.push('Analysis epsilon and support thresholds are outside their admitted ranges.');
        }
        if (form.analysis.clash_detector_id !== 'bms_clash' || form.analysis.clash_detector_version !== '1') {
            errors.push('Requested clash detector is not installed.');
        }
        if (form.analysis.rank_stability_minimum < -1 || form.analysis.rank_stability_minimum > 1
            || !Number.isInteger(form.analysis.minimum_common_ranked_universe_size)
            || form.analysis.minimum_common_ranked_universe_size < 3) {
            errors.push('Rank stability or minimum common ranked universe is invalid.');
        }
        if (expectedCount < 1) errors.push('The current controls produce no candidate coordinates.');
        return errors;
    }, [expectedCount, form, seedValues, stepValues, structureSources]);

    const register = useMutation({
        mutationFn: async ({ file, kind }: { file: File; kind: CmSourceKind }) => registerCmSource(kind, file, {
            name: file.name,
            ...(sourceTargetId.trim() ? { target_id: sourceTargetId.trim() } : {}),
            ...(kind === 'structure_upload' || kind === 'structure_artifact' ? { state: sourceState.trim() || 'reference' } : {}),
            ...(kind === 'confornets_state' ? { kind: transferKind, source_test_cases: sourceTestCases.trim() } : {}),
        }),
        onSuccess: async (source) => {
            setError(null); setSourceFile(null);
            if (source.source_kind === 'complex_snapshot') update('snapshotId', source.source_id);
            if (source.source_kind === 'protein_sequence') update('sequenceId', source.source_id);
            await queryClient.invalidateQueries({ queryKey: ['cm-sources'] });
        },
        onError: (value) => setError(cmApiError(value, 'Source registration failed.')),
    });

    const registerRcsb = useMutation({
        mutationFn: () => registerCmRcsbMmcif(rcsbPdbId),
        onSuccess: async (source) => {
            setError(null);
            setRcsbPdbId('');
            update('importIds', [source.source_id]);
            await queryClient.invalidateQueries({ queryKey: ['cm-sources'] });
        },
        onError: (value) => setError(cmApiError(value, 'RCSB mmCIF registration failed.')),
    });

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
            name: form.name.trim(), idempotency_key: idempotencyKey, backend: form.backend,
            ordered_seeds: form.backend === 'external_import' ? [0] : seedValues!,
            samples_per_seed: form.backend === 'external_import' ? 1 : form.samples,
            feature_policy: featurePolicy,
            runtime_policy: form.defaultRuntime ? { use_default_params: true } : { use_default_params: false, n_cycle: form.nCycle, n_step: form.nStep },
            analysis_policy: form.analysis,
        };
        if (form.backend === 'protenix_v2_ensemble') {
            payload.registered_snapshot_id = form.snapshotId;
            if (form.stateComparisonTargetId.trim()) {
                payload.state_landscape_comparison = {
                    mode: 'pairwise', target_id: form.stateComparisonTargetId.trim(), scope: 'all_within_target',
                };
            }
        }
        if (form.backend === 'external_import') {
            payload.registered_artifact_ids = form.importIds;
        }
        if (form.backend === 'confornets') {
            payload.registered_sequence_id = form.sequenceId;
            payload.registered_checkpoint_id = form.checkpointId;
            if (form.configId) payload.registered_config_id = form.configId;
            if (form.task === 'mse') payload.registered_reference_ids = form.referenceIds;
            if (form.task === 'transfer') payload.registered_transfer_id = form.transferId;
            payload.confornets = {
                chain_id: form.chainId.trim(), task: form.task, test_case_id: form.testCaseId.trim(), benchmark_name: form.benchmarkName.trim(),
                runs: form.task === 'transfer' ? 1 : form.runs,
                saved_steps: form.task === 'mse' ? [form.maxSteps] : form.task === 'transfer' ? [0] : stepValues!,
                confornet_count: form.task === 'diversity' ? form.networks : 1,
                samples: form.samples, max_steps: form.maxSteps, num_recycles: form.numRecycles,
                num_diffusion_steps: form.numDiffusionSteps, learning_rate: form.learningRate, gradient_clip: form.gradientClip,
                skip_msa: form.skipMsa, compute_confidence: form.computeConfidence,
                save_full_confidence: form.saveFullConfidence, compute_evaluation: form.computeEvaluation,
            };
        }
        return payload;
    };

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

    const sourceAccept = SOURCE_KINDS.find((item) => item.value === sourceKind)?.accept;
    const planningWarning = expectedCount > 100 || estimatedStorageGiB > 10;

    return (
        <div className="mx-auto max-w-7xl space-y-5 text-slate-200" data-bms-cm-launcher="canonical">
            <header className="rounded-2xl border border-slate-700 bg-slate-900/80 p-5 sm:p-6">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-orange-300">Canonical operator launcher</p>
                        <h2 className="mt-1 text-2xl font-semibold text-white">Conformational Mapping</h2>
                        <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-300">
                            Generate complete-complex Protenix v2 ensembles across explicit target × seed × sample coordinates,
                            or use canonical single-chain ConforNets and authenticated structure imports. Every path is normalized
                            through residue mapping, FrustraMPNN landscapes, clash/support analysis, and stable candidate ranking.
                        </p>
                    </div>
                    {onBack && <button type="button" onClick={onBack} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-500 hover:text-white">Back</button>}
                </div>
                <p className="mt-4 rounded-lg border border-sky-500/20 bg-sky-500/5 p-3 text-xs leading-5 text-sky-100">{CM_SCIENTIFIC_LIMIT}</p>
                <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4" aria-label="Conformational Mapping workflow stages">
                    {[
                        ['1', 'Complete-complex Protenix v2 ensembles', 'Primary complete-complex generator'],
                        ['2', 'Residue mapping', 'Normalize identities across every hypothesis'],
                        ['3', 'FrustraMPNN landscapes', 'Compute mapped exact-20 energetic landscapes'],
                        ['4', 'Support + ranking', 'Compare signs, clashes, support, and rank stability'],
                    ].map(([number, title, detail]) => (
                        <div key={number} className="rounded-xl border border-slate-700 bg-slate-950/50 p-3">
                            <div className="text-[10px] font-semibold uppercase tracking-wider text-orange-300">Stage {number}</div>
                            <div className="mt-1 text-sm font-medium text-white">{title}</div>
                            <div className="mt-1 text-xs leading-5 text-slate-500">{detail}</div>
                        </div>
                    ))}
                </div>
                <ModelDocumentationLinks
                    topics={['protenix', 'confornets', 'fampnn']}
                    summary="Primary Protenix v2 ensemble generation, canonical ConforNets, and FrustraMPNN analysis references."
                    compact
                    className="mt-4"
                />
            </header>

            <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 sm:p-5" aria-labelledby="cm-source-heading">
                <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 id="cm-source-heading" className="font-semibold text-white">Registered input vault</h3><p className="text-xs text-slate-500">Uploads become immutable content-addressed handles. Requests submit handles only.</p></div><span className="text-xs text-slate-500">{sources.data?.length || 0} available</span></div>
                <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(220px,1fr)_minmax(240px,1.4fr)_minmax(160px,.8fr)_auto]">
                    <label className="space-y-1 text-xs text-slate-400">Source type<select value={sourceKind} onChange={(event) => setSourceKind(event.target.value as CmSourceKind)} className={inputClass}>{SOURCE_KINDS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
                    <label className="space-y-1 text-xs text-slate-400">Local file<input type="file" accept={sourceAccept} onChange={(event) => setSourceFile(event.target.files?.[0] || null)} className={inputClass} /></label>
                    <label className="space-y-1 text-xs text-slate-400">{sourceKind === 'protein_sequence' ? 'Target ID' : 'Optional target label'}<input value={sourceTargetId} onChange={(event) => setSourceTargetId(event.target.value)} className={inputClass} /></label>
                    <button type="button" disabled={!sourceFile || register.isPending} onClick={() => sourceFile && register.mutate({ file: sourceFile, kind: sourceKind })} className="self-end rounded-lg bg-slate-700 px-4 py-2 text-sm font-medium hover:bg-slate-600 disabled:opacity-40">Register</button>
                </div>
                {(sourceKind === 'structure_upload' || sourceKind === 'structure_artifact') && <label className="mt-3 block max-w-sm space-y-1 text-xs text-slate-400">Reference state label<input value={sourceState} onChange={(event) => setSourceState(event.target.value)} className={inputClass} /></label>}
                {sourceKind === 'confornets_state' && <div className="mt-3 grid max-w-2xl gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs text-slate-400">Transfer-state kind<select value={transferKind} onChange={(event) => setTransferKind(event.target.value as typeof transferKind)} className={inputClass}><option value="confornet_state">ConforNet state</option><option value="mse_state">MSE state</option></select></label><label className="space-y-1 text-xs text-slate-400">Source test cases<input value={sourceTestCases} onChange={(event) => setSourceTestCases(event.target.value)} className={inputClass} /></label></div>}
                {sourceKind === 'protein_sequence' && <section className="mt-4 rounded-xl border border-sky-500/25 bg-sky-500/5 p-3" aria-label="Paste protein sequence"><div className="flex flex-wrap items-start justify-between gap-2"><div><h4 className="text-sm font-medium text-sky-100">Paste protein sequence</h4><p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">Whitespace is normalized before immutable registration. Enter a target ID above to retain a human-readable source label; sequence bytes are registered without a FASTA header.</p></div><span className="text-xs text-slate-500">{pastedSequence.replace(/\s+/g, '').length.toLocaleString()} residues</span></div><textarea value={pastedSequence} onChange={(event) => setPastedSequence(event.target.value)} rows={5} spellCheck={false} className={`${inputClass} mt-3 font-mono text-xs`} placeholder="MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG" /><button type="button" disabled={!pastedSequence.trim() || register.isPending} onClick={registerPastedSequence} className="mt-3 rounded-lg border border-sky-400/40 px-3 py-2 text-sm text-sky-100 disabled:opacity-40">Register and select sequence</button></section>}
                <section className="mt-4 rounded-xl border border-violet-500/25 bg-violet-500/5 p-3" aria-label="RCSB PDB tie-in"><h4 className="text-sm font-medium text-violet-100">RCSB PDB tie-in</h4><p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">Enter a four-character PDB accession. BioModStack downloads raw RCSB mmCIF on the server, validates it as mmCIF, then registers an immutable CM structure handle. Cached PDB bytes and browser URLs are never submitted as CM authority.</p><div className="mt-3 flex max-w-xl gap-2"><input value={rcsbPdbId} onChange={(event) => setRcsbPdbId(event.target.value.toUpperCase())} maxLength={4} placeholder="1UBQ" className={inputClass} /><button type="button" disabled={!/^[A-Z0-9]{4}$/.test(rcsbPdbId) || registerRcsb.isPending} onClick={() => registerRcsb.mutate()} className="shrink-0 rounded-lg border border-violet-400/40 px-3 py-2 text-sm text-violet-100 disabled:opacity-40">Register raw mmCIF</button></div></section>
                <details className="mt-4 rounded-xl border border-slate-800 bg-slate-950/40 p-3"><summary className="cursor-pointer text-sm font-medium text-slate-300">Complete-complex snapshot editor</summary><p className="mt-2 text-xs text-slate-500">Paste one complete <code>cm_complex_snapshot</code> object or an ordered array. The server performs canonical schema validation before registering it.</p><textarea value={snapshotEditor} onChange={(event) => setSnapshotEditor(event.target.value)} rows={8} spellCheck={false} className={`${inputClass} mt-3 font-mono text-xs`} placeholder='{"schema_name":"cm_complex_snapshot",...}' /><button type="button" disabled={!snapshotEditor.trim() || register.isPending} onClick={registerSnapshotEditor} className="mt-3 rounded-lg border border-orange-400/40 px-3 py-2 text-sm text-orange-200 disabled:opacity-40">Validate and register snapshot</button></details>
            </section>

            <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 sm:p-5" aria-labelledby="cm-request-heading">
                <h3 id="cm-request-heading" className="font-semibold text-white">Request and backend</h3>
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                    <label className="space-y-1 text-sm">Request name<input value={form.name} onChange={(event) => update('name', event.target.value)} className={inputClass} /></label>
                    <label className="space-y-1 text-sm">Backend<select value={form.backend} onChange={(event) => update('backend', event.target.value as CmBackend)} className={inputClass}><option value="protenix_v2_ensemble">Protenix v2 · complete complex</option><option value="confornets">ConforNets · canonical single chain</option><option value="external_import">Secure registered structure import</option></select></label>
                </div>
                <div className="mt-4 grid gap-2 md:grid-cols-3">
                    {(['protenix_v2_ensemble', 'confornets', 'external_import'] as CmBackend[]).map((value) => <button key={value} type="button" onClick={() => update('backend', value)} aria-pressed={form.backend === value} className={`rounded-xl border p-3 text-left ${form.backend === value ? 'border-orange-400/60 bg-orange-500/10' : 'border-slate-800 bg-slate-950/30 hover:border-slate-700'}`}><span className="text-sm font-medium text-white">{value === 'protenix_v2_ensemble' ? 'Complete-complex ensemble' : value === 'confornets' ? 'Canonical ConforNets' : 'Immutable import'}</span><span className="mt-1 block text-xs text-slate-500">{value === 'protenix_v2_ensemble' ? 'Every target × ordered seed × sample.' : value === 'confornets' ? 'Supported task coordinate product only.' : 'Exactly one protein mmCIF per request.'}</span></button>)}
                </div>
            </section>

            {form.backend === 'protenix_v2_ensemble' && <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 sm:p-5"><h3 className="font-semibold text-white">Complete-complex authority</h3><label className="mt-3 block space-y-1 text-sm">Registered snapshot<select value={form.snapshotId} onChange={(event) => update('snapshotId', event.target.value)} className={inputClass}><option value="">Select a registered snapshot…</option>{byKind('complex_snapshot').map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></label>{selectedSnapshot && <div className="mt-3 grid gap-3 rounded-xl border border-slate-800 bg-slate-950/40 p-3 text-xs sm:grid-cols-3"><div><span className="text-slate-500">Targets</span><div className="mt-1 text-white">{snapshotTargetIds.length || 'Server validated'}</div></div><div><span className="text-slate-500">Content</span><div className="mt-1 font-mono text-white">{selectedSnapshot.sha256.slice(0, 16)}…</div></div><div><span className="text-slate-500">Bytes</span><div className="mt-1 text-white">{selectedSnapshot.bytes.toLocaleString()}</div></div>{snapshotTargetIds.length > 0 && <div className="sm:col-span-3"><span className="text-slate-500">Ordered complete-complex targets</span><div className="mt-1 break-words text-slate-300">{snapshotTargetIds.join(' → ')}</div></div>}</div>}</section>}

            {form.backend === 'protenix_v2_ensemble' && <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 sm:p-5"><h3 className="font-semibold text-white">Protenix coordinate and feature controls</h3><div className="mt-4 grid gap-4 md:grid-cols-2"><label className="space-y-1 text-sm">Ordered seeds<input value={form.seeds} onChange={(event) => update('seeds', event.target.value)} className={inputClass} inputMode="numeric" /></label><label className="space-y-1 text-sm">Samples per seed<input type="number" min={1} max={100} value={form.samples} onChange={(event) => update('samples', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-sm md:col-span-2">Feature policy<select value={form.featureMode} onChange={(event) => update('featureMode', event.target.value as CmFeaturePolicy['mode'])} className={inputClass}><option value="regenerate_mutated_protein_v1">Regenerate changed protein</option><option value="paired_regenerate_changed_protein_v1">Regenerate matched WT and mutant</option><option value="features_disabled_control_v1">Feature-disabled control</option></select></label></div><div className="mt-4 grid gap-3 sm:grid-cols-3">{([['proteinMsa', 'Protein MSA'], ['templates', 'Templates'], ['rnaMsa', 'RNA MSA']] as const).map(([key, label]) => <label key={key} className="flex items-center gap-2 rounded-lg border border-slate-800 p-3 text-sm"><input type="checkbox" checked={form[key]} disabled={form.featureMode === 'features_disabled_control_v1'} onChange={(event) => update(key, event.target.checked)} className={checkClass} />{label}</label>)}</div><div className="mt-4 rounded-xl border border-slate-800 p-3"><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.defaultRuntime} onChange={(event) => update('defaultRuntime', event.target.checked)} className={checkClass} />Use installed runtime defaults</label>{!form.defaultRuntime && <div className="mt-3 grid gap-3 sm:grid-cols-2"><label className="space-y-1 text-xs text-slate-400">Cycles<input type="number" min={1} value={form.nCycle} onChange={(event) => update('nCycle', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-xs text-slate-400">Steps<input type="number" min={1} value={form.nStep} onChange={(event) => update('nStep', Number(event.target.value))} className={inputClass} /></label></div>}</div><label className="mt-4 block space-y-1 text-sm">State-conditioned FrustraMPNN comparison target <input value={form.stateComparisonTargetId} onChange={(event) => update('stateComparisonTargetId', event.target.value)} placeholder="Optional target ID; enables pairwise candidate comparison" className={inputClass} /></label><p className="mt-1 text-xs text-slate-500">When set, BioModStack persists one immutable pairwise state-analysis artifact for this snapshot target. This is structural hypothesis evidence, not MD or population analysis.</p></section>}

            {form.backend === 'external_import' && <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 sm:p-5"><h3 className="font-semibold text-white">Secure protein mmCIF handle</h3><p className="mt-1 text-xs text-slate-500">Select one principal-owned immutable mmCIF identity. Snapshot and residue identity are derived server-side from immutable staged bytes; ambiguous or incomplete structures fail closed.</p><select value={form.importIds[0] || ''} onChange={(event) => update('importIds', event.target.value ? [event.target.value] : [])} className={`${inputClass} mt-3`}><option value="">Select one registered mmCIF…</option>{structureSources.map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></section>}

            {form.backend === 'confornets' && <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 sm:p-5"><h3 className="font-semibold text-white">Canonical ConforNets controls</h3><p className="mt-1 text-xs text-slate-500">Single-chain protein runtime only. Task-specific controls are constrained to the server-supported coordinate product.</p><div className="mt-4 grid gap-4 md:grid-cols-2"><label className="space-y-1 text-sm">Protein sequence<select value={form.sequenceId} onChange={(event) => update('sequenceId', event.target.value)} className={inputClass}><option value="">Select…</option>{byKind('protein_sequence').map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></label><label className="space-y-1 text-sm">Canonical checkpoint<select value={form.checkpointId} onChange={(event) => update('checkpointId', event.target.value)} className={inputClass}><option value="">Select…</option>{byKind('confornets_checkpoint').map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></label><label className="space-y-1 text-sm">Optional registered config<select value={form.configId} onChange={(event) => update('configId', event.target.value)} className={inputClass}><option value="">Installed defaults</option>{byKind('confornets_config').map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></label><label className="space-y-1 text-sm">Task<select value={form.task} onChange={(event) => taskChanged(event.target.value as CmTask)} className={inputClass}><option value="diversity">Diversity</option><option value="mse">Reference-guided MSE</option><option value="transfer">Transfer state</option></select></label><label className="space-y-1 text-sm">Explicit seed<input value={form.seeds} onChange={(event) => update('seeds', event.target.value)} className={inputClass} /></label><label className="space-y-1 text-sm">Samples per coordinate<input type="number" min={1} max={100} value={form.samples} onChange={(event) => update('samples', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-sm">Chain ID<input value={form.chainId} maxLength={8} onChange={(event) => update('chainId', event.target.value)} className={inputClass} /></label><label className="space-y-1 text-sm">Test-case ID<input value={form.testCaseId} onChange={(event) => update('testCaseId', event.target.value)} className={inputClass} /></label><label className="space-y-1 text-sm">Benchmark name<input value={form.benchmarkName} onChange={(event) => update('benchmarkName', event.target.value)} className={inputClass} /></label><label className="space-y-1 text-sm">Runs<input type="number" min={1} disabled={form.task === 'transfer'} value={form.task === 'transfer' ? 1 : form.runs} onChange={(event) => update('runs', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-sm">Networks<input type="number" min={form.task === 'diversity' ? 2 : 1} disabled={form.task !== 'diversity'} value={form.task === 'diversity' ? form.networks : 1} onChange={(event) => update('networks', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-sm">Saved steps<input disabled={form.task !== 'diversity'} value={form.task === 'mse' ? String(form.maxSteps) : form.task === 'transfer' ? '0' : form.savedSteps} onChange={(event) => update('savedSteps', event.target.value)} className={inputClass} /></label><label className="space-y-1 text-sm">Maximum steps<input type="number" min={1} value={form.maxSteps} onChange={(event) => update('maxSteps', Number(event.target.value))} className={inputClass} /></label></div>{form.task === 'mse' && <label className="mt-4 block space-y-1 text-sm">One or two registered references<select multiple value={form.referenceIds} onChange={(event) => update('referenceIds', Array.from(event.target.selectedOptions, (option) => option.value).slice(0, 2))} className={`${inputClass} min-h-28`}>{structureSources.map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></label>}{form.task === 'transfer' && <label className="mt-4 block space-y-1 text-sm">Registered transfer state<select value={form.transferId} onChange={(event) => update('transferId', event.target.value)} className={inputClass}><option value="">Select…</option>{byKind('confornets_state').map((source) => <option key={source.source_id} value={source.source_id}>{sourceLabel(source)}</option>)}</select></label>}<details className="mt-4 rounded-xl border border-slate-800 p-3"><summary className="cursor-pointer text-sm font-medium text-slate-300">Exact runtime controls</summary><div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><label className="space-y-1 text-xs text-slate-400">Recycles<input type="number" min={0} value={form.numRecycles} onChange={(event) => update('numRecycles', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-xs text-slate-400">Diffusion steps<input type="number" min={1} value={form.numDiffusionSteps} onChange={(event) => update('numDiffusionSteps', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-xs text-slate-400">Learning rate<input type="number" min="0.0000001" step="0.0001" value={form.learningRate} onChange={(event) => update('learningRate', Number(event.target.value))} className={inputClass} /></label><label className="space-y-1 text-xs text-slate-400">Gradient clip<input type="number" min="0.0000001" value={form.gradientClip} onChange={(event) => update('gradientClip', Number(event.target.value))} className={inputClass} /></label></div><div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{([['skipMsa', 'Skip MSA'], ['computeConfidence', 'Compute confidence'], ['saveFullConfidence', 'Save full confidence'], ['computeEvaluation', 'Compute evaluation']] as const).map(([key, label]) => <label key={key} className="flex items-center gap-2 text-xs"><input type="checkbox" checked={form[key]} onChange={(event) => update(key, event.target.checked)} className={checkClass} />{label}</label>)}</div></details></section>}

            <details className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 sm:p-5"><summary className="cursor-pointer font-semibold text-white">Exact analysis admission policy</summary><div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(form.analysis).filter(([key]) => !['clash_detector_id', 'clash_detector_version'].includes(key)).map(([key, value]) => <label key={key} className="space-y-1 text-xs text-slate-400"><span>{key.replaceAll('_', ' ')}</span><input type="number" step="any" value={value as number} onChange={(event) => update('analysis', { ...form.analysis, [key]: Number(event.target.value) })} className={inputClass} /></label>)}</div><p className="mt-3 text-xs text-slate-500">Installed detector: <span className="font-mono">{form.analysis.clash_detector_id}@{form.analysis.clash_detector_version}</span></p></details>

            <section className={`rounded-2xl border p-4 sm:p-5 ${planningWarning ? 'border-amber-500/30 bg-amber-500/5' : 'border-slate-800 bg-slate-900/70'}`} aria-labelledby="cm-estimate-heading"><h3 id="cm-estimate-heading" className="font-semibold text-white">Pre-submit planning estimate</h3><div className="mt-3 grid gap-3 sm:grid-cols-3"><div><div className="text-xs text-slate-500">Candidate coordinates</div><div className="mt-1 text-xl font-semibold text-white">{expectedCount.toLocaleString()}</div></div><div><div className="text-xs text-slate-500">Planning storage</div><div className="mt-1 text-xl font-semibold text-white">~{estimatedStorageGiB.toFixed(2)} GiB</div></div><div><div className="text-xs text-slate-500">Execution shape</div><div className="mt-1 text-sm text-white">{form.backend === 'external_import' ? 'CPU staging + analysis' : `${expectedCount} backend candidate runs + analysis`}</div></div></div><p className="mt-3 text-xs leading-5 text-slate-500">Storage is an operator planning estimate using {planningMiBPerCandidate} MiB per candidate plus selected input bytes. Native structures, full sidecars, normalized maps, exact-20 landscapes, analysis, and provenance are retained; actual usage is runtime-dependent.</p>{planningWarning && <p className="mt-2 text-xs font-medium text-amber-200">Large request: confirm GPU queue capacity and durable result storage before submission.</p>}</section>

            <section className="rounded-2xl border border-slate-800 bg-slate-900/70 p-4 sm:p-5" aria-labelledby="cm-validation-heading"><div className="flex flex-wrap items-center justify-between gap-2"><h3 id="cm-validation-heading" className="font-semibold text-white">Validation receipt</h3><span className={`rounded-full px-3 py-1 text-xs font-medium ${validationErrors.length ? 'bg-red-500/10 text-red-200' : 'bg-emerald-500/10 text-emerald-200'}`}>{validationErrors.length ? `${validationErrors.length} blocking issue${validationErrors.length === 1 ? '' : 's'}` : 'Ready for typed admission'}</span></div>{validationErrors.length ? <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-red-200">{validationErrors.map((message) => <li key={message}>{message}</li>)}</ul> : <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2"><div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Backend</span><div className="mt-1 font-mono text-slate-200">{form.backend}</div></div><div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Expected cardinality</span><div className="mt-1 text-slate-200">{expectedCount.toLocaleString()}</div></div><div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Input authority</span><div className="mt-1 truncate font-mono text-slate-200">{form.backend === 'confornets' ? form.sequenceId : form.backend === 'external_import' ? form.importIds.join(' → ') : form.snapshotId}</div></div><div className="rounded-lg border border-slate-800 p-3"><span className="text-slate-500">Inactive backend fields</span><div className="mt-1 text-slate-200">Excluded from payload</div></div></div>}</section>

            {error && <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">{error}</div>}
            {sources.isError && <div role="alert" className="rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">{cmApiError(sources.error, 'Unable to load the authenticated source registry.')}</div>}
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-700 bg-slate-900 p-4"><p className="text-xs text-slate-500">Submission uses <code>/api/conformational-mapping/requests</code>; the generic job creation route is not used.</p><button type="button" disabled={validationErrors.length > 0 || submit.isPending || sources.isError} onClick={() => { setError(null); submit.mutate(); }} className="rounded-lg bg-orange-500 px-5 py-2.5 font-semibold text-slate-950 hover:bg-orange-400 disabled:cursor-not-allowed disabled:opacity-40">{submit.isPending ? 'Submitting authenticated request…' : 'Submit canonical request'}</button></div>
        </div>
    );
}

export default ConformationalMappingLauncher;
