import { useCallback, useState, useRef, useEffect, useMemo } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { completeCurrentLaunchContext, submitJob, estimateBoltzApiJob, fetchBoltzApiProviderStatus, submitBoltzApiJob, fetchMsaCacheInfo, fetchUserSequence, uploadFile, type BoltzApiEstimateResponse, type BoltzApiProviderStatus, type MsaCacheInfo } from '../lib/api';
import { useNavigate } from 'react-router-dom';
import { parseMolecularDynamicsHandoffUserSequence } from './gen2StartingStructureState';
import { SequenceManager } from './SequenceManager';
import { LigandSelector, type LigandEntry } from './LigandSelector';
import { componentIdFromIndex } from './ligandSelectorData';
import { TargetAntigenSelector, type SelectedTarget } from './TargetAntigenSelector';
import MolstarViewer from './MolstarViewer';
import {
    BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT,
    BOLTZ_NUM_SAMPLES_HELP_TEXT,
    BOLTZ_QUALITY_PRESETS,
    DEFAULT_STRUCTURE_MSA_PROVIDER,
    DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MIN_SIZE_GB,
    DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MODE,
    DEFAULT_STRUCTURE_MSA_TARGET_SHARDS,
    buildBoltzCpSubmitParams,
    buildBoltzApiStructureRequest,
    buildStructureFrustraMpnnSubmitParams,
    buildStructureMsaSubmitParams,
    buildTargetPreviewSelection,
    buildTargetPreviewSelections,
    deriveBoltzCpGpuLaunchSettings,
    getBoltzQualityPresetValues,
    getBoltzQualitySliderState,
    getPredictorFamiliesForSelection,
    getStructurePredictorOptions,
    normalizeMsaTargetShardMinSizeGb,
    normalizeMsaTargetShardMode,
    normalizeMsaTargetShards,
    resolveBoltzSamplingStepsFromSlider,
    resolveStructureLaunchConfig,
    resolveStructurePredictorSelection,
    resolveStructureSubmitTarget,
    resolveTargetPreviewSource,
    type StructurePredictionMode,
    type StructureMsaTargetShardMode,
    type StructurePredictorSelection,
} from './structurePredictionUiState.js';
import { BoltzApiNativeSettingsPanel } from './BoltzApiNativeSettings';
import { parsePDBFile, getModelByNumber, type Chain, type ParsedPDB } from '../utils/pdbUtils';
import { useLiveGpuCatalog } from './useLiveGpuCatalog';
import { ModelDocumentationLinks, type ModelDocumentationTopic } from './ModelDocumentationLinks';
import { resolveInitialGpuPinningState } from './gpuToggleState.js';
import { ModelIntegrationControl, useModelIntegrationConfig } from './ModelIntegrationControl';
import { FrustraMpnnSettingsPanel } from './frustrampnn/FrustraMpnnSettingsPanel.js';
import { hydrateFrustraMpnnSettings } from './frustrampnn/frustraMpnnSettingsState.js';
import { fetchFrustraMpnnIntegration } from '../lib/frustraMpnnApi.js';
import {
    applyModelIntegrationChoice,
    applyModelIntegrationDefault,
    createModelIntegrationSelection,
} from './modelIntegrationControlState';
import { buildMolecularDynamicsPredictionReturnRoute } from './gen2StartingStructureState.js';


interface StructurePredictionTemplateProps {
    onBack: () => void;
    initialValues?: Record<string, UntypedApiValue>;
    onDraftChange?: (draft: Record<string, UntypedApiValue>) => void;
    onOpenTemplateManager?: (context: {
        currentParams?: Record<string, UntypedApiValue>;
        currentModelId?: string;
        currentMode?: string;
        baseTemplateId?: string;
    }) => void;
    sourceSequenceId?: string | null;
    mdDraftId?: string | null;
    returnTemplate?: 'molecular_dynamics' | null;
}

const parseChainIdList = (value: unknown): string[] => {
    if (typeof value !== 'string') return [];
    return value
        .split(/[|,]/)
        .map((token) => token.trim())
        .filter(Boolean);
};

const normalizeProteinSequence = (value: unknown): string => (
    typeof value === 'string'
        ? value.toUpperCase().replace(/[^A-Z]/g, '')
        : ''
);

const isProteinComponent = (component: UntypedApiValue): boolean => {
    const type = String(component?.type || '').trim().toLowerCase();
    return type === 'protein' || type === 'peptide';
};

const looksLikeAntibodyVariableDomain = (sequence: string): boolean => {
    const normalized = normalizeProteinSequence(sequence);
    if (normalized.length < 90 || normalized.length > 180) return false;
    return ['QVQL', 'EVQL', 'QLQL', 'QVQ', 'EVQ', 'VQLA', 'DIQM', 'EIVL'].some((prefix) => normalized.startsWith(prefix));
};

const resolveInitialPrimaryProteinComponent = (initialValues?: Record<string, UntypedApiValue>) => {
    const components = Array.isArray(initialValues?.complex_components) ? initialValues.complex_components : [];
    const proteinComponents = components.filter(isProteinComponent);
    if (proteinComponents.length === 0) return null;

    const preferredIds = [
        ...parseChainIdList(initialValues?.sequence_batch_component_id),
        ...parseChainIdList(initialValues?.binder_chains),
        ...parseChainIdList(initialValues?.antibody_chains),
        ...parseChainIdList(initialValues?.primary_chain_id),
        ...parseChainIdList(initialValues?.target_chains),
    ];
    for (const chainId of preferredIds) {
        const matched = proteinComponents.find((component: UntypedApiValue) => String(component?.id || '').trim() === chainId);
        if (matched) return matched;
    }

    const preferredSequence = normalizeProteinSequence(initialValues?.sequence || initialValues?.sequence_input);
    if (preferredSequence) {
        const matched = proteinComponents.find((component: UntypedApiValue) => normalizeProteinSequence(component?.sequence) === preferredSequence);
        if (matched) return matched;
    }

    const antibodyLike = proteinComponents.find((component: UntypedApiValue) => looksLikeAntibodyVariableDomain(String(component?.sequence || '')));
    if (antibodyLike) return antibodyLike;

    return proteinComponents[0];
};

const MIN_BOLTZ_NO_MSA_RECYCLING_STEPS = 3;
const MIN_BOLTZ_NO_MSA_SAMPLING_STEPS = 50;

const clampBoltzRecyclingSteps = (value: unknown, useMsa: boolean): number => {
    const parsed = Number.parseInt(String(value), 10);
    const min = useMsa ? 1 : MIN_BOLTZ_NO_MSA_RECYCLING_STEPS;
    if (!Number.isFinite(parsed)) return MIN_BOLTZ_NO_MSA_RECYCLING_STEPS;
    return Math.max(min, Math.min(10, parsed));
};

const clampBoltzSamplingSteps = (value: unknown, useMsa: boolean): number => {
    const parsed = Number.parseInt(String(value), 10);
    const min = useMsa ? 10 : MIN_BOLTZ_NO_MSA_SAMPLING_STEPS;
    if (!Number.isFinite(parsed)) return MIN_BOLTZ_NO_MSA_SAMPLING_STEPS;
    return Math.max(min, Math.min(1000, parsed));
};

export function StructurePredictionTemplate({ onBack, initialValues, onDraftChange, onOpenTemplateManager, sourceSequenceId = null, mdDraftId = null, returnTemplate = null }: StructurePredictionTemplateProps) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const { gpuOptions } = useLiveGpuCatalog();
    const frustrampnnIntegrationQuery = useModelIntegrationConfig('frustrampnn', fetchFrustraMpnnIntegration);
    const normalizeProtenixModel = (_model?: string) => 'protenix-v2';
    const initialPrimaryProteinComponent = resolveInitialPrimaryProteinComponent(initialValues);
    const initialPrimarySequence = initialPrimaryProteinComponent?.sequence || initialValues?.sequence || '';
    const initialPrimaryName = initialPrimaryProteinComponent?.name || initialValues?.sequence_name || 'predicted';
    const initialPrimaryChain = String(
        initialPrimaryProteinComponent?.id
        || initialValues?.primary_chain_id
        || initialValues?.target_chains
        || 'A'
    ).split(',')
        .map((token: string) => token.trim())
        .find(Boolean) || 'A';

    const launchConfig = resolveStructureLaunchConfig(initialValues);
    const initialBoltzCpSizeCp = Number.parseInt(String(initialValues?.size_cp ?? initialValues?.bcp_size_cp ?? 4), 10);
    const initialBoltzCpSeed = initialValues?.seed ?? initialValues?.bcp_seed;

    // Core state
    const initialGpuPinningState = resolveInitialGpuPinningState(initialValues);
    const [jobName, setJobName] = useState(initialValues?.name || initialValues?.job_name || 'structure_prediction');
    const initialFrustrampnnSelection = createModelIntegrationSelection(initialValues?.run_frustrampnn, true);
    const frustrampnnSelectionRef = useRef(initialFrustrampnnSelection);
    const [runFrustrampnn, setRunFrustrampnn] = useState(initialFrustrampnnSelection.value);
    const [frustrampnnSettings, setFrustrampnnSettings] = useState(() => (
        hydrateFrustraMpnnSettings(initialValues?.frustrampnn_settings)
    ));
    const configuredFrustrampnnDefault = frustrampnnIntegrationQuery.data?.workflows?.structure_prediction?.default_enabled;
    useEffect(() => {
        const previous = frustrampnnSelectionRef.current;
        const next = applyModelIntegrationDefault(previous, configuredFrustrampnnDefault);
        frustrampnnSelectionRef.current = next;
        if (next !== previous) {
            setRunFrustrampnn(next.value);
        }
    }, [configuredFrustrampnnDefault]);
    const updateRunFrustrampnn = (checked: boolean) => {
        const next = applyModelIntegrationChoice(frustrampnnSelectionRef.current, checked);
        frustrampnnSelectionRef.current = next;
        setRunFrustrampnn(next.value);
    };
    const [pinnedGpus, setPinnedGpus] = useState(initialGpuPinningState.pinnedGpus);
    const [lockGpus, setLockGpus] = useState(initialGpuPinningState.lockGpus);
    const clearGpuPinning = () => {
        setPinnedGpus([]);
        setLockGpus(false);
    };
    const [sequence, setSequence] = useState(initialPrimarySequence);
    const [sequenceName, setSequenceName] = useState(initialPrimaryName);
    const [sequenceHandoffError, setSequenceHandoffError] = useState('');
    const [sequenceHandoffLoading, setSequenceHandoffLoading] = useState(Boolean(sourceSequenceId));
    useEffect(() => {
        if (!sourceSequenceId) return;
        let cancelled = false;
        setSequenceHandoffLoading(true);
        setSequenceHandoffError('');
        void fetchUserSequence(sourceSequenceId).then(({ data }) => {
            if (cancelled) return;
            const sequenceRecord = parseMolecularDynamicsHandoffUserSequence(data, sourceSequenceId);
            setSequence(sequenceRecord.sequence);
            setSequenceName(sequenceRecord.name);
        }).catch((error: unknown) => {
            if (!cancelled) setSequenceHandoffError(error instanceof Error ? error.message : 'The selected saved sequence is unavailable.');
        }).finally(() => {
            if (!cancelled) setSequenceHandoffLoading(false);
        });
        return () => { cancelled = true; };
    }, [sourceSequenceId]);
    const [primaryChainId, setPrimaryChainId] = useState<string>(initialPrimaryChain);
    const [targetSource, setTargetSource] = useState<SelectedTarget | null>((initialValues?.target_source as SelectedTarget | null) || null);
    const [targetSourcePath, setTargetSourcePath] = useState<string | null>(
        initialValues?.fixed_target_source_path || initialValues?.target_source?.path || null
    );
    const [targetPreviewUrl, setTargetPreviewUrl] = useState<string | null>(null);
    const targetPreviewBlobRef = useRef<string | null>(null);
    const [targetSourceChainId, setTargetSourceChainId] = useState<string | null>(
        String(initialValues?.fixed_target_source_chains || initialValues?.primary_chain_id || initialValues?.target_chains || '')
            .split(',')
            .map((token: string) => token.trim())
            .find(Boolean) || null
    );
    const [targetSourceSequence, setTargetSourceSequence] = useState<string>(
        String(initialValues?.fixed_target_source_sequence || '')
    );

    const [targetStructure, setTargetStructure] = useState<ParsedPDB | null>(null);
    const [selectedTargetModel, setSelectedTargetModel] = useState<number | null>(() => {
        const raw = initialValues?.fixed_target_model_number ?? initialValues?.target_model_number;
        const parsed = Number(raw);
        return Number.isFinite(parsed) && parsed >= 1 ? parsed : 1;
    });

    // Predictor selection
    const [predictor, setPredictor] = useState<StructurePredictorSelection>(
        (launchConfig.forcedPredictor || initialValues?.pred_method as StructurePredictorSelection | undefined) || 'boltz'
    );

    // Boltz-2 parameters
    const initialBoltzUseMsa = launchConfig.showMsaControls ? (initialValues?.boltz_use_msa ?? true) : false;
    const [boltzUseMsa, setBoltzUseMsa] = useState(initialBoltzUseMsa);
    const [boltzRecyclingSteps, setBoltzRecyclingSteps] = useState(
        clampBoltzRecyclingSteps(initialValues?.boltz_recycling_steps ?? 3, initialBoltzUseMsa)
    );
    const [boltzSamplingSteps, setBoltzSamplingSteps] = useState(
        clampBoltzSamplingSteps(initialValues?.boltz_sampling_steps ?? getBoltzQualityPresetValues('max').samplingSteps, initialBoltzUseMsa)
    );
    const [boltzNumSamples, setBoltzNumSamples] = useState(initialValues?.boltz_num_samples ?? 1);
    const [boltzUsePotentials, setBoltzUsePotentials] = useState(initialValues?.boltz_use_potentials ?? false);
    const [boltzMethod, setBoltzMethod] = useState(initialValues?.boltz_method || '');
    const [boltzMaxParallelSamples, setBoltzMaxParallelSamples] = useState(initialValues?.boltz_max_parallel_samples ?? 1);
    const [boltzTargetGeometryMode, setBoltzTargetGeometryMode] = useState<'flexible' | 'conditioned' | 'frozen'>(
        initialValues?.boltz_target_geometry_mode || (initialValues?.boltz_anchor_target ? 'conditioned' : 'flexible')
    );

    // Protenix parameters
    const [protenixModelWeights, setProtenixModelWeights] = useState(normalizeProtenixModel(initialValues?.protenix_model_weights));
    const [protenixSeeds, setProtenixSeeds] = useState(initialValues?.protenix_seeds || '42');
    const [protenixNSample, setProtenixNSample] = useState(initialValues?.protenix_n_sample ?? 5);
    const [protenixNStep, setProtenixNStep] = useState(initialValues?.protenix_n_step ?? 200);
    const [protenixNCycle, setProtenixNCycle] = useState(initialValues?.protenix_n_cycle ?? 10);
    const [protenixUseMsa, setProtenixUseMsa] = useState(initialValues?.protenix_use_msa ?? true);
    const [protenixTargetGeometryMode, setProtenixTargetGeometryMode] = useState<'flexible' | 'conditioned' | 'frozen'>(
        initialValues?.protenix_target_geometry_mode || ((initialValues?.protenix_anchor_target || initialValues?.protenix_use_template) ? 'conditioned' : 'flexible')
    );

    // Parallel jobs
    const [numParallelJobs, setNumParallelJobs] = useState(initialValues?.num_parallel_jobs ?? 1);

    // Boltz-CP-specific settings
    const [bcpRequestedSizeCp, setBcpRequestedSizeCp] = useState(
        Number.isFinite(initialBoltzCpSizeCp) && initialBoltzCpSizeCp > 0 ? initialBoltzCpSizeCp : 4
    );
    const [bcpOutputFormat, setBcpOutputFormat] = useState<'mmcif' | 'pdb'>(
        String(initialValues?.output_format ?? initialValues?.bcp_output_format ?? 'mmcif').toLowerCase() === 'pdb' ? 'pdb' : 'mmcif'
    );
    const [bcpWriteFullPae, setBcpWriteFullPae] = useState(Boolean(initialValues?.write_full_pae ?? initialValues?.bcp_write_full_pae ?? false));
    const [bcpSeed, setBcpSeed] = useState(initialBoltzCpSeed != null ? String(initialBoltzCpSeed) : '');

    // ESMFold2-specific settings intentionally stay compact; inputs reuse the standard structure surface.
    const [esmfold2Variant, setEsmfold2Variant] = useState<'fast' | 'full'>(
        initialValues?.model_variant === 'full' || initialValues?.esmf_model_variant === 'full' ? 'full' : 'fast'
    );

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
        initialValues?.msa_provider === 'local' ? 'local' : DEFAULT_STRUCTURE_MSA_PROVIDER
    );
    const [msaTargetShardMode, setMsaTargetShardMode] = useState<StructureMsaTargetShardMode>(
        normalizeMsaTargetShardMode(initialValues?.msa_target_shard_mode ?? DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MODE)
    );
    const [msaTargetShards, setMsaTargetShards] = useState<number>(
        normalizeMsaTargetShards(initialValues?.msa_target_shards ?? DEFAULT_STRUCTURE_MSA_TARGET_SHARDS)
    );
    const [msaTargetShardMinSizeGb, setMsaTargetShardMinSizeGb] = useState<number>(
        normalizeMsaTargetShardMinSizeGb(initialValues?.msa_target_shard_min_size_gb ?? DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MIN_SIZE_GB)
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
        const primaryId = String(initialPrimaryProteinComponent?.id || '');
        const nonPrimaryComponents = components.filter((component: UntypedApiValue) => String(component?.id || '') !== primaryId);
        // Everything except the resolved primary component becomes additional context.
        // Expand counted components into distinct entries so
        // retries and cloned jobs preserve repeated ions/cofactors in the UI.
        const expanded: LigandEntry[] = [];
        nonPrimaryComponents.forEach((component: UntypedApiValue) => {
            const countRaw = component?.count ?? 1;
            const count = Number.isFinite(Number(countRaw))
                ? Math.max(1, Math.min(12, Math.floor(Number(countRaw))))
                : 1;
            for (let idx = 0; idx < count; idx += 1) {
                const baseName = component?.name || `Chain ${component?.id || componentIdFromIndex(1 + expanded.length)}`;
                const displayName = count > 1 ? `${baseName} #${idx + 1}` : baseName;
                expanded.push({
                    id: count > 1 ? componentIdFromIndex(1 + expanded.length) : (component?.id || componentIdFromIndex(1 + expanded.length)),
                    type: component?.type || 'protein',
                    sequence: component?.sequence,
                    ccd: component?.ccd,
                    smiles: component?.smiles,
                    name: displayName,
                });
            }
        });
        return expanded;
    });
    const [sequenceBatchInput, setSequenceBatchInput] = useState(initialValues?.sequence_batch_input || '');
    const [sequenceBatchPrefix, setSequenceBatchPrefix] = useState(
        initialValues?.sequence_batch_prefix || initialValues?.sequence_name || initialValues?.name || 'variant'
    );
    const [sequenceBatchComponentId, setSequenceBatchComponentId] = useState(initialValues?.sequence_batch_component_id || '');

    const [showInputModal, setShowInputModal] = useState(false);
    const [inputModalTab, setInputModalTab] = useState<'library' | 'pdb'>('library');
    const importTargetRef = useRef<'primary' | 'additional'>('primary');  // Use ref to avoid stale closure
    const [modalTargetSource, setModalTargetSource] = useState<SelectedTarget | null>(null);
    const [modalPreviewUrl, setModalPreviewUrl] = useState<string | null>(null);
    const modalPreviewBlobRef = useRef<string | null>(null);
    const [modalParsedStructure, setModalParsedStructure] = useState<ParsedPDB | null>(null);
    const [modalSelectedModel, setModalSelectedModel] = useState<number>(1);
    const [parsedChains, setParsedChains] = useState<Chain[]>([]);
    const [selectedChainIndices, setSelectedChainIndices] = useState<Set<number>>(new Set());
    const [sequenceToSave, setSequenceToSave] = useState<{ sequence: string; name: string } | null>(null);
    const [boltzApiStatus, setBoltzApiStatus] = useState<BoltzApiProviderStatus | null>(null);

    const projectDraftJson = JSON.stringify((() => {
        const predMethod = predictor === 'boltz' ? 'boltz2' : predictor === 'protenix' ? 'protenix' : 'esmfold2';
        const common: Record<string, UntypedApiValue> = {
            sequence: sequence.trim(), sequence_name: sequenceName.trim(), pred_method: predMethod,
            num_parallel_jobs: numParallelJobs,
        };
        if (predMethod === 'esmfold2') return {
            ...common, run_frustrampnn: false, frustrampnn_requiredness: 'required',
            model_variant: esmfold2Variant, local_files_only: true,
        };
        if (predMethod === 'boltz2') return {
            ...common, boltz_recycling_steps: boltzRecyclingSteps,
            boltz_diffusion_samples: boltzNumSamples,
            boltz_max_parallel_samples: boltzMaxParallelSamples,
            boltz_sampling_steps: boltzSamplingSteps, boltz_use_msa: boltzUseMsa,
            boltz_method: boltzMethod,
        };
        return {
            ...common, protenix_model_weights: protenixModelWeights,
            protenix_use_msa: protenixUseMsa,
            protenix_msa_backend: initialValues?.protenix_msa_backend ?? 'auto',
            protenix_use_template: initialValues?.protenix_use_template ?? false,
            protenix_seeds: protenixSeeds, protenix_n_sample: protenixNSample,
            protenix_n_step: protenixNStep, protenix_n_cycle: protenixNCycle,
        };
    })());
    useEffect(() => {
        onDraftChange?.(JSON.parse(projectDraftJson) as Record<string, UntypedApiValue>);
    }, [onDraftChange, projectDraftJson]);
    const [boltzApiEstimate, setBoltzApiEstimate] = useState<BoltzApiEstimateResponse | null>(null);
    const [boltzApiEstimateApproved, setBoltzApiEstimateApproved] = useState(false);
    const [boltzApiEstimateError, setBoltzApiEstimateError] = useState<string | null>(null);
    const [boltzApiEstimating, setBoltzApiEstimating] = useState(false);
    const [boltzApiClientRequestId, setBoltzApiClientRequestId] = useState(() => crypto.randomUUID());

    const navigateAfterSubmission = async (jobResponse: unknown) => {
        const launchContextActive = Boolean(new URLSearchParams(window.location.search).get('launch_context_id'));
        if (!launchContextActive && returnTemplate === 'molecular_dynamics' && mdDraftId) {
            navigate(buildMolecularDynamicsPredictionReturnRoute(mdDraftId, jobResponse));
            return;
        }
        navigate(await completeCurrentLaunchContext(jobResponse as UntypedApiValue) ?? '/');
    };

    const submitMutation = useMutation({
        mutationFn: async (data: UntypedApiValue) => submitJob(data),
        onSuccess: async (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            await navigateAfterSubmission(response.data);
        }
    });

    const boltzApiSubmitMutation = useMutation({
        mutationFn: submitBoltzApiJob,
        onSuccess: async (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            await navigateAfterSubmission(response.data);
        },
        onError: (error: UntypedApiValue) => {
            const detail = error?.response?.data?.detail;
            setBoltzApiEstimateError(detail?.message || error?.message || 'Boltz API job could not be queued.');
        },
    });

    const applyMsaPreset = (preset: 'maximum' | 'balanced' | 'fast') => {
        setMsaPreset(preset);
        // Preset selection should clear advanced overrides so behavior matches the preset label.
        setMsaUseExpand(undefined);
        setMsaUseEnv(undefined);
        setMsaNumIterations(undefined);
    };

    const fixedTargetAvailable = !!targetSourceChainId && !!targetSource;

    const replacePreviewBlobUrl = (
        ref: React.MutableRefObject<string | null>,
        setter: (value: string | null) => void,
        nextUrl: string | null,
    ) => {
        if (ref.current && ref.current !== nextUrl) {
            URL.revokeObjectURL(ref.current);
        }
        ref.current = nextUrl;
        setter(nextUrl);
    };

    const clearTargetPreview = () => replacePreviewBlobUrl(targetPreviewBlobRef, setTargetPreviewUrl, null);
    const clearModalPreview = () => replacePreviewBlobUrl(modalPreviewBlobRef, setModalPreviewUrl, null);
    const adoptModalPreviewForTarget = () => {
        const nextUrl = modalPreviewBlobRef.current;
        modalPreviewBlobRef.current = null;
        setModalPreviewUrl(null);
        replacePreviewBlobUrl(targetPreviewBlobRef, setTargetPreviewUrl, nextUrl);
    };

    useEffect(() => () => {
        if (targetPreviewBlobRef.current) {
            URL.revokeObjectURL(targetPreviewBlobRef.current);
        }
        if (modalPreviewBlobRef.current) {
            URL.revokeObjectURL(modalPreviewBlobRef.current);
        }
    }, []);

    const closePdbModalState = () => {
        clearModalPreview();
        setModalTargetSource(null);
        setModalParsedStructure(null);
        setModalSelectedModel(1);
        setParsedChains([]);
        setSelectedChainIndices(new Set());
    };

    const sanitizeSequenceInput = (value: string) => value.toUpperCase().replace(/[^A-Z]/g, '');

    const parseSequenceBatchInput = (raw: string, prefix: string) => {
        const trimmed = raw.trim();
        if (!trimmed) return [];

        const entries: Array<{ name: string; sequence: string }> = [];
        const safePrefix = (prefix.trim() || 'variant').replace(/[^A-Za-z0-9._-]+/g, '_');
        if (trimmed.includes('>')) {
            const blocks = trimmed.split(/^>/m).map((block) => block.trim()).filter(Boolean);
            blocks.forEach((block, index) => {
                const [header, ...rest] = block.split(/\n/);
                const sequenceValue = sanitizeSequenceInput(rest.join(''));
                if (!sequenceValue) return;
                const headerName = (header || '').trim().split(/\s+/)[0];
                const name = (headerName || `${safePrefix}_${String(index + 1).padStart(3, '0')}`).replace(/[^A-Za-z0-9._-]+/g, '_');
                entries.push({ name, sequence: sequenceValue });
            });
            return entries;
        }

        trimmed
            .split(/\n+/)
            .map((line) => line.trim())
            .filter(Boolean)
            .forEach((line, index) => {
                let name = '';
                let sequenceValue = '';
                const colonIndex = line.indexOf(':');
                if (colonIndex > 0) {
                    name = line.slice(0, colonIndex).trim();
                    sequenceValue = sanitizeSequenceInput(line.slice(colonIndex + 1));
                } else {
                    const tokens = line.split(/\s+/);
                    if (tokens.length > 1) {
                        name = tokens[0];
                        sequenceValue = sanitizeSequenceInput(tokens.slice(1).join(''));
                    } else {
                        sequenceValue = sanitizeSequenceInput(line);
                    }
                }
                if (!sequenceValue) return;
                const resolvedName = (name || `${safePrefix}_${String(index + 1).padStart(3, '0')}`).replace(/[^A-Za-z0-9._-]+/g, '_');
                entries.push({ name: resolvedName, sequence: sequenceValue });
            });
        return entries;
    };

    const canonicalTargetSourceName = (target: SelectedTarget | null) => {
        const raw = (target?.name?.trim() || 'target').replace(/[^A-Za-z0-9._-]+/g, '_');
        return raw.toLowerCase().endsWith('.pdb') || raw.toLowerCase().endsWith('.cif') || raw.toLowerCase().endsWith('.mmcif')
            ? raw
            : `${raw}.pdb`;
    };

    const nextAvailableComponentId = (usedIds: Set<string>) => {
        let index = 0;
        let candidate = componentIdFromIndex(index);
        while (usedIds.has(candidate)) {
            index += 1;
            candidate = componentIdFromIndex(index);
        }
        return candidate;
    };

    const batchEntriesPreview = parseSequenceBatchInput(sequenceBatchInput, sequenceBatchPrefix);
    const hasBatchEntries = batchEntriesPreview.length > 0;
    const hasProteinBinderComponents = ligands.some((ligand) => ligand.type === 'protein' || ligand.type === 'peptide');
    const autoBatchBinderMode = hasBatchEntries && fixedTargetAvailable && !hasProteinBinderComponents;
    const complexMode = ligands.length > 0 || autoBatchBinderMode;
    const implicitBatchBinderId = (() => {
        const usedIds = new Set<string>();
        const primaryId = (primaryChainId || 'A').trim() || 'A';
        usedIds.add(primaryId);
        ligands.forEach((ligand) => {
            const ligandId = (ligand.id || '').trim();
            if (ligandId) {
                usedIds.add(ligandId);
            }
        });
        return nextAvailableComponentId(usedIds);
    })();

    const buildComplexComponents = useCallback((batchEntries: Array<{ name: string; sequence: string }> = []) => {
        const components: Array<Record<string, UntypedApiValue>> = [];
        const usedIds = new Set<string>();
        const reserveId = (preferred?: string) => {
            const normalized = (preferred || '').trim();
            if (normalized && !usedIds.has(normalized)) {
                usedIds.add(normalized);
                return normalized;
            }
            const fallback = nextAvailableComponentId(usedIds);
            usedIds.add(fallback);
            return fallback;
        };

        const resolvedPrimaryId = reserveId(primaryChainId || 'A');
        components.push({
            type: 'protein',
            id: resolvedPrimaryId,
            sequence: sequence.trim(),
            name: sequenceName,
        });

        const binderIds: string[] = [];
        ligands.forEach((ligand) => {
            const resolvedId = reserveId(ligand.id);
            const component: Record<string, UntypedApiValue> = {
                type: ligand.type,
                id: resolvedId,
                name: ligand.name,
            };
            if (ligand.sequence) component.sequence = ligand.sequence;
            if (ligand.ccd) component.ccd = ligand.ccd;
            if (ligand.smiles) component.smiles = ligand.smiles;
            components.push(component);
            if (ligand.type === 'protein' || ligand.type === 'peptide') {
                binderIds.push(resolvedId);
            }
        });

        if (autoBatchBinderMode && binderIds.length === 0) {
            const autoBinderId = reserveId(implicitBatchBinderId);
            components.push({
                type: 'protein',
                id: autoBinderId,
                sequence: batchEntries[0]?.sequence || 'G',
                name: `${sequenceBatchPrefix || 'batch'}_binder_panel`,
            });
            binderIds.push(autoBinderId);
        }

        return {
            components,
            resolvedPrimaryId,
            binderIds,
        };
    }, [autoBatchBinderMode, implicitBatchBinderId, ligands, primaryChainId, sequence, sequenceBatchPrefix, sequenceName]);

    const proteinBatchTargets = [
        { id: primaryChainId || 'A', name: `${sequenceName || 'Primary'} (${primaryChainId || 'A'})`, role: 'Primary' },
        ...ligands
            .filter((ligand) => ligand.type === 'protein' || ligand.type === 'peptide')
            .map((ligand) => ({
                id: ligand.id,
                name: `${ligand.name || ligand.id} (${ligand.id})`,
                role: 'Additional',
            })),
        ...(autoBatchBinderMode ? [{
            id: implicitBatchBinderId,
            name: `Batch Binder (${implicitBatchBinderId})`,
            role: 'Batch',
        }] : []),
    ];
    const resolvedSequenceBatchComponentId =
        sequenceBatchComponentId ||
        proteinBatchTargets.find((entry) => entry.role === 'Batch')?.id ||
        proteinBatchTargets.find((entry) => entry.role === 'Additional')?.id ||
        proteinBatchTargets[0]?.id ||
        '';
    const predictionMode: StructurePredictionMode = complexMode ? 'complex' : 'predict';
    const activePredictorSelection = predictor;
    const resolvedPredictorSelection = resolveStructurePredictorSelection(predictionMode, activePredictorSelection);
    const predictorFamilies = getPredictorFamiliesForSelection(predictionMode, activePredictorSelection);
    const predictorOptions = getStructurePredictorOptions(predictionMode);
    const selectedPredictorId = resolvedPredictorSelection.canonicalSelection;
    const isBoltzApi = selectedPredictorId === 'boltz_api';
    const isBoltzCpLaunch = selectedPredictorId === 'fold_cp';
    const submitTarget = resolveStructureSubmitTarget({
        launchConfig,
        predictionMode,
        predictorSelection: activePredictorSelection,
    });
    const usesBoltz = predictorFamilies.includes('boltz');
    const usesFoldCp = predictorFamilies.includes('fold_cp');
    const usesProtenix = predictorFamilies.includes('protenix');
    const usesEsmFold2 = predictorFamilies.includes('esmfold2');
    const msaNeeded =
        (usesBoltz && !isBoltzApi && boltzUseMsa) ||
        (usesFoldCp && boltzUseMsa) ||
        (usesProtenix && protenixUseMsa);

    useEffect(() => {
        if (!isBoltzApi) return;
        let cancelled = false;
        fetchBoltzApiProviderStatus()
            .then(({ data }) => {
                if (!cancelled) setBoltzApiStatus(data);
            })
            .catch(() => {
                if (!cancelled) {
                    setBoltzApiStatus(null);
                    setBoltzApiEstimateError('Boltz API provider status could not be loaded.');
                }
            });
        return () => { cancelled = true; };
    }, [isBoltzApi]);

    useEffect(() => {
        setBoltzApiEstimate(null);
        setBoltzApiEstimateApproved(false);
        setBoltzApiEstimateError(null);
        setBoltzApiClientRequestId(crypto.randomUUID());
    }, [sequence, primaryChainId, ligands, sequenceBatchInput, boltzNumSamples, boltzUseMsa, predictionMode, isBoltzApi]);
    const boltzCpFallbackGpuIds = useMemo(() => {
        const explicitGpuIds = initialValues?.gpu_ids ?? initialValues?.bcp_gpu_ids;
        const explicitGpuIdsText = String(explicitGpuIds ?? '').trim();
        return explicitGpuIdsText || gpuOptions.map((gpu) => gpu.index).join(',');
    }, [gpuOptions, initialValues?.gpu_ids, initialValues?.bcp_gpu_ids]);
    const boltzCpGpuSettings = deriveBoltzCpGpuLaunchSettings({
        pinnedGpus,
        requestedSizeCp: bcpRequestedSizeCp,
        fallbackGpuIds: boltzCpFallbackGpuIds,
    });
    const boltzQualityState = getBoltzQualitySliderState({
        samplingSteps: boltzSamplingSteps,
        recyclingSteps: boltzRecyclingSteps,
    });
    const currentTemplateParams = useMemo(() => {
        const params: Record<string, UntypedApiValue> = {
            name: jobName,
            job_name: jobName,
            sequence: sequence.trim(),
            sequence_name: sequenceName,
            pred_method: resolvedPredictorSelection.canonicalSelection,
            num_parallel_jobs: launchConfig.showParallelJobs && !isBoltzCpLaunch ? numParallelJobs : 1,
            pinned_gpus: pinnedGpus,
            lock_gpus: lockGpus && pinnedGpus.length > 0,
            allow_retries: allowRetries,
            ...buildStructureFrustraMpnnSubmitParams(runFrustrampnn, frustrampnnSettings),
        };

        if (isBoltzCpLaunch) {
            params.structure_launch_variant = 'boltz_cp_experimental';
            Object.assign(params, buildBoltzCpSubmitParams({
                outputFormat: bcpOutputFormat,
                writeFullPae: bcpWriteFullPae,
                seed: bcpSeed,
                gpuIds: boltzCpGpuSettings.gpuIds,
                sizeCp: boltzCpGpuSettings.sizeCp,
            }));
        }

        if (usesEsmFold2) {
            params.model_variant = esmfold2Variant;
            params.local_files_only = true;
        }

        if (typeof initialValues?.workflow_adapter === 'string' && initialValues.workflow_adapter.trim()) {
            params.workflow_adapter = initialValues.workflow_adapter.trim();
        }

        if (usesBoltz) {
            params.boltz_use_msa = boltzUseMsa;
            params.boltz_recycling_steps = boltzRecyclingSteps;
            params.boltz_sampling_steps = boltzSamplingSteps;
            params.boltz_num_samples = boltzNumSamples;
            params.boltz_use_potentials = boltzUsePotentials;
            params.boltz_max_parallel_samples = boltzMaxParallelSamples;
            params.boltz_target_geometry_mode = boltzTargetGeometryMode;
            if (boltzMethod) params.boltz_method = boltzMethod;
        }

        if (usesFoldCp) {
            params.boltz_use_msa = boltzUseMsa;
            params.boltz_recycling_steps = boltzRecyclingSteps;
            params.boltz_sampling_steps = boltzSamplingSteps;
            params.boltz_num_samples = boltzNumSamples;
        }

        if (usesProtenix) {
            params.protenix_model_weights = protenixModelWeights;
            params.protenix_seeds = protenixSeeds;
            params.protenix_n_sample = protenixNSample;
            params.protenix_n_step = protenixNStep;
            params.protenix_n_cycle = protenixNCycle;
            params.protenix_use_msa = protenixUseMsa;
            params.protenix_target_geometry_mode = protenixTargetGeometryMode;
        }

        if (msaNeeded) {
            Object.assign(params, buildStructureMsaSubmitParams({
                provider: msaProvider,
                preset: msaPreset,
                targetShardMode: msaTargetShardMode,
                targetShards: msaTargetShards,
                targetShardMinSizeGb: msaTargetShardMinSizeGb,
            }));
            if (msaTaxonomy) params.msa_taxon_list = msaTaxonomy;
            if (msaEvalue) params.msa_evalue = parseFloat(msaEvalue);
            if (msaMinSeqId) params.msa_min_seq_id = parseFloat(msaMinSeqId);
            if (msaMinCoverage) params.msa_min_coverage = parseFloat(msaMinCoverage);
            params.msa_min_depth_warning = msaMinDepthWarning;
            params.msa_min_depth_fail = msaMinDepthFail;
            if (msaCacheOnly) params.msa_cache_only = true;
            if (msaAllowEmptyFallback) params.msa_allow_empty_fallback = true;
            if (msaProvider === 'colabfold_api') {
                params.colabfold_api_host = colabfoldApiHost.trim() || 'https://api.colabfold.com';
                params.colabfold_api_min_interval = Math.max(0, Number(colabfoldApiMinInterval) || 0);
                params.colabfold_api_poll_interval = Math.max(1, Number(colabfoldApiPollInterval) || 6);
            }
            if (msaUseExpand !== undefined) params.msa_use_expand = msaUseExpand;
            if (msaUseEnv !== undefined) params.msa_use_env = msaUseEnv;
            if (msaNumIterations !== undefined) params.msa_num_iterations = msaNumIterations;
        }

        if (targetSource) {
            params.target_source = {
                type: targetSource.type,
                url: targetSource.url,
                path: targetSource.path,
                designId: targetSource.designId,
                pdbId: targetSource.pdbId,
                name: targetSource.name,
            };
        }
        if (targetSourcePath) params.fixed_target_source_path = targetSourcePath;
        if (targetSourceChainId) params.fixed_target_source_chains = targetSourceChainId;
        if (selectedTargetModel) params.fixed_target_model_number = selectedTargetModel;
        if (targetSourceSequence) params.fixed_target_source_sequence = targetSourceSequence;

        if (complexMode) {
            const { components, resolvedPrimaryId, binderIds } = buildComplexComponents(batchEntriesPreview);
            params.complex_components = components;
            params.primary_chain_id = resolvedPrimaryId;
            params.target_chains = resolvedPrimaryId;
            if (binderIds.length > 0) {
                params.binder_chains = binderIds.join(',');
            }
        }

        if (batchEntriesPreview.length > 0) {
            params.sequence_batch_entries = batchEntriesPreview;
            params.sequence_batch_input = sequenceBatchInput;
            params.sequence_batch_prefix = sequenceBatchPrefix;
            if (complexMode) {
                params.sequence_batch_component_id = resolvedSequenceBatchComponentId;
            }
        }

        return Object.fromEntries(
            Object.entries(params).filter(([, value]) => value !== undefined)
        );
    }, [jobName, sequence, sequenceName, resolvedPredictorSelection.canonicalSelection, launchConfig.showParallelJobs, numParallelJobs, pinnedGpus, lockGpus, allowRetries, runFrustrampnn, frustrampnnSettings, isBoltzCpLaunch, esmfold2Variant, usesEsmFold2, usesBoltz, usesFoldCp, usesProtenix, msaNeeded, targetSource, targetSourcePath, targetSourceChainId, selectedTargetModel, targetSourceSequence, complexMode, batchEntriesPreview, bcpRequestedSizeCp, bcpOutputFormat, bcpWriteFullPae, bcpSeed, boltzCpGpuSettings.gpuIds, boltzCpGpuSettings.sizeCp, boltzUseMsa, boltzRecyclingSteps, boltzSamplingSteps, boltzNumSamples, boltzUsePotentials, boltzMaxParallelSamples, boltzTargetGeometryMode, boltzMethod, protenixModelWeights, protenixSeeds, protenixNSample, protenixNStep, protenixNCycle, protenixUseMsa, protenixTargetGeometryMode, msaProvider, msaPreset, msaTargetShardMode, msaTargetShards, msaTargetShardMinSizeGb, msaTaxonomy, msaEvalue, msaMinSeqId, msaMinCoverage, msaMinDepthWarning, msaMinDepthFail, msaCacheOnly, msaAllowEmptyFallback, msaUseExpand, msaUseEnv, msaNumIterations, colabfoldApiHost, colabfoldApiMinInterval, colabfoldApiPollInterval, buildComplexComponents, sequenceBatchInput, sequenceBatchPrefix, resolvedSequenceBatchComponentId]);
    const targetPreview = targetSource
        ? resolveTargetPreviewSource({
            previewUrl: targetPreviewUrl,
            stagedPath: targetSourcePath,
            targetSource,
        })
        : { structureUrl: null, format: 'pdb' as const };
    const targetPreviewSelections = buildTargetPreviewSelection(targetSourceChainId || primaryChainId);
    const activeModalModel = modalParsedStructure ? getModelByNumber(modalParsedStructure, modalSelectedModel) : null;
    const modalPreview = modalTargetSource
        ? resolveTargetPreviewSource({
            previewUrl: modalPreviewUrl,
            stagedPath: modalTargetSource.path || null,
            targetSource: modalTargetSource,
        })
        : { structureUrl: null, format: 'pdb' as const };
    const modalPreviewSelections = buildTargetPreviewSelections(
        Array.from(selectedChainIndices)
            .map((index) => (activeModalModel?.chains ?? parsedChains)[index]?.id)
    );

    useEffect(() => {
        if (predictor === resolvedPredictorSelection.canonicalSelection) {
            return;
        }
        if (!resolvedPredictorSelection.valid) {
            return;
        }
        setPredictor(resolvedPredictorSelection.canonicalSelection);
    }, [
        predictionMode,
        predictor,
        resolvedPredictorSelection.canonicalSelection,
        resolvedPredictorSelection.valid,
    ]);

    const resolveTargetStructurePath = async () => {
        if (targetSourcePath) {
            return targetSourcePath;
        }
        if (!targetSource) {
            return null;
        }
        if (targetSource.path) {
            setTargetSourcePath(targetSource.path);
            return targetSource.path;
        }

        let sourceFile = targetSource.file ?? null;
        if (!sourceFile && targetSource.url) {
            const response = await fetch(targetSource.url);
            if (!response.ok) {
                throw new Error(`Failed to fetch target structure (${response.status})`);
            }
            const blob = await response.blob();
            sourceFile = new File([blob], canonicalTargetSourceName(targetSource), { type: blob.type || 'chemical/x-pdb' });
        }

        if (!sourceFile) {
            return null;
        }

        const response = await uploadFile('inputs/structure_prediction', sourceFile);
        const uploadedPath = response.data?.path || `inputs/structure_prediction/${sourceFile.name}`;
        setTargetSourcePath(uploadedPath);
        return uploadedPath;
    };

    useEffect(() => {
        if (!modalParsedStructure) {
            setParsedChains([]);
            return;
        }
        const activeModel = getModelByNumber(modalParsedStructure, modalSelectedModel);
        setParsedChains(activeModel?.chains ?? modalParsedStructure.chains ?? []);
    }, [modalParsedStructure, modalSelectedModel]);

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
                .catch((err: UntypedApiValue) => {
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

    const handleSubmit = async () => {
        if (sequenceHandoffLoading || sequenceHandoffError) {
            alert(sequenceHandoffError || 'The selected saved sequence is still loading.');
            return;
        }
        const batchEntries = batchEntriesPreview;

        if (!sequence.trim() && batchEntries.length === 0) {
            alert('Please enter an amino acid sequence');
            return;
        }

        if (batchEntries.length > 0 && complexMode && !resolvedSequenceBatchComponentId) {
            alert('Sequence matrix mode requires a target protein component to replace.');
            return;
        }

        if (!resolvedPredictorSelection.valid) {
            alert(resolvedPredictorSelection.error || 'The selected structure predictor cannot be launched in this mode.');
            return;
        }

        const params: Record<string, UntypedApiValue> = {
            sequence: sequence.trim(),
            sequence_name: sequenceName,
            pred_method: resolvedPredictorSelection.canonicalSelection,
            num_parallel_jobs: launchConfig.showParallelJobs && !isBoltzCpLaunch ? numParallelJobs : 1,
            ...buildStructureFrustraMpnnSubmitParams(runFrustrampnn, frustrampnnSettings),
        };

        if (isBoltzCpLaunch) {
            Object.assign(params, buildBoltzCpSubmitParams({
                outputFormat: bcpOutputFormat,
                writeFullPae: bcpWriteFullPae,
                seed: bcpSeed,
                gpuIds: boltzCpGpuSettings.gpuIds,
                sizeCp: boltzCpGpuSettings.sizeCp,
            }));
        }

        if (usesEsmFold2) {
            params.model_variant = esmfold2Variant;
            params.local_files_only = true;
            if (typeof initialValues?.workflow_adapter === 'string' && initialValues.workflow_adapter.trim()) {
                params.workflow_adapter = initialValues.workflow_adapter.trim();
            }
        }

        // Boltz-2 parameters
        if (usesBoltz) {
            params.boltz_use_msa = boltzUseMsa;
            params.boltz_recycling_steps = boltzRecyclingSteps;
            params.boltz_sampling_steps = boltzSamplingSteps;
            params.boltz_num_samples = boltzNumSamples;
            params.boltz_use_potentials = boltzUsePotentials;
            params.boltz_max_parallel_samples = boltzMaxParallelSamples;
            params.boltz_target_geometry_mode = boltzTargetGeometryMode;
            if (boltzMethod) params.boltz_method = boltzMethod;
        }

        if (usesFoldCp) {
            params.boltz_use_msa = boltzUseMsa;
            params.boltz_recycling_steps = boltzRecyclingSteps;
            params.boltz_sampling_steps = boltzSamplingSteps;
            params.boltz_num_samples = boltzNumSamples;
        }

        // Protenix parameters
        if (usesProtenix) {
            params.protenix_model_weights = protenixModelWeights;
            params.protenix_seeds = protenixSeeds;
            params.protenix_n_sample = protenixNSample;
            params.protenix_n_step = protenixNStep;
            params.protenix_n_cycle = protenixNCycle;
            params.protenix_use_msa = protenixUseMsa;
            params.protenix_target_geometry_mode = protenixTargetGeometryMode;
        }

        if (msaNeeded && msaProvider === 'colabfold_api' && numParallelJobs > 1) {
            alert('ColabFold API MSA provider currently supports only single-job submissions (num_parallel_jobs=1).');
            return;
        }

        if (msaNeeded && msaCacheOnly && (!msaCacheInfo || msaCacheInfo.cache_entries < 1)) {
            alert('Use Cache Only is enabled, but no cached MSA exists for this sequence.');
            return;
        }

        // MSA Quality parameters (when MSA is enabled for unknown predictor)
        if (msaNeeded) {
            Object.assign(params, buildStructureMsaSubmitParams({
                provider: msaProvider,
                preset: msaPreset,
                targetShardMode: msaTargetShardMode,
                targetShards: msaTargetShards,
                targetShardMinSizeGb: msaTargetShardMinSizeGb,
            }));
            if (msaTaxonomy) params.msa_taxon_list = msaTaxonomy;
            if (msaEvalue) params.msa_evalue = parseFloat(msaEvalue);
            if (msaMinSeqId) params.msa_min_seq_id = parseFloat(msaMinSeqId);
            if (msaMinCoverage) params.msa_min_coverage = parseFloat(msaMinCoverage);
            params.msa_min_depth_warning = msaMinDepthWarning;
            params.msa_min_depth_fail = msaMinDepthFail;
            if (msaForceRefresh && !msaCacheOnly) params.msa_force_refresh = true;
            if (msaCacheOnly) params.msa_cache_only = true;
            if (msaAllowEmptyFallback) params.msa_allow_empty_fallback = true;
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

        const targetConditioningRequested = (!isBoltzApi && usesBoltz && boltzTargetGeometryMode !== 'flexible') || (usesProtenix && protenixTargetGeometryMode !== 'flexible');
        if (targetConditioningRequested && !complexMode) {
            alert('Target conditioning needs a shared target or complex component.');
            return;
        }
        if (targetConditioningRequested && !fixedTargetAvailable) {
            alert('Fixed-target anchoring requires importing the primary target from a PDB source first.');
            return;
        }
        if (targetConditioningRequested) {
            const normalizedCurrentSequence = sanitizeSequenceInput(sequence);
            const normalizedSourceSequence = sanitizeSequenceInput(targetSourceSequence);
            if (!normalizedSourceSequence || normalizedCurrentSequence !== normalizedSourceSequence) {
                alert('Fixed-target anchoring requires the primary sequence to exactly match the imported target source chain.');
                return;
            }
        }

        const modelId = submitTarget.modelId;
        const mode = submitTarget.mode;
        let remoteComponents: Array<Record<string, unknown>> = [];
        let remotePrimaryChainId = primaryChainId || 'A';

        if (complexMode) {
            const { components, resolvedPrimaryId, binderIds } = buildComplexComponents(batchEntries);
            remoteComponents = components as Array<Record<string, unknown>>;
            remotePrimaryChainId = resolvedPrimaryId;
            params.complex_components = components;
            params.primary_chain_id = resolvedPrimaryId;
            params.target_chains = resolvedPrimaryId;
            if (binderIds.length > 0) {
                params.binder_chains = binderIds.join(',');
            }

            if (targetConditioningRequested) {
                try {
                    const resolvedSourcePath = await resolveTargetStructurePath();
                    if (!resolvedSourcePath) {
                        alert('Failed to stage the fixed target structure for anchored prediction.');
                        return;
                    }
                    params.fixed_target_source_path = resolvedSourcePath;
                    params.fixed_target_source_chains = targetSourceChainId;
                    params.fixed_target_model_number = selectedTargetModel || undefined;
                    params.fixed_target_source_sequence = targetSourceSequence || undefined;
                } catch (error: UntypedApiValue) {
                    alert(error?.message || 'Failed to stage the fixed target structure.');
                    return;
                }
            }
        }

        if (isBoltzApi) {
            if (batchEntries.length > 0) {
                alert('Boltz API submission currently queues one remote prediction per launch. Remove the sequence batch first.');
                return;
            }
            const request = buildBoltzApiStructureRequest({
                name: jobName,
                clientRequestId: boltzApiClientRequestId,
                sequence: sequence.trim(),
                primaryChainId: remotePrimaryChainId,
                complexComponents: remoteComponents,
                numSamples: boltzNumSamples,
                useMsa: boltzUseMsa,
            });
            if (!boltzApiEstimate) {
                setBoltzApiEstimating(true);
                setBoltzApiEstimateError(null);
                try {
                    const { data } = await estimateBoltzApiJob(request);
                    setBoltzApiEstimate(data);
                    setBoltzApiEstimateApproved(false);
                } catch (error: UntypedApiValue) {
                    const detail = error?.response?.data?.detail;
                    setBoltzApiEstimateError(detail?.message || error?.message || 'Boltz API cost estimate failed.');
                } finally {
                    setBoltzApiEstimating(false);
                }
                return;
            }
            if (!boltzApiEstimateApproved) {
                alert('Review and approve the current Boltz API cost estimate before queueing the remote job.');
                return;
            }
            boltzApiSubmitMutation.mutate({
                ...request,
                approved_estimate_fingerprint: boltzApiEstimate.estimate_fingerprint,
            });
            return;
        }

        if (batchEntries.length > 0) {
            params.sequence_batch_entries = batchEntries;
            params.sequence_batch_input = sequenceBatchInput;
            params.sequence_batch_prefix = sequenceBatchPrefix;
            if (complexMode) {
                params.sequence_batch_component_id = resolvedSequenceBatchComponentId;
            }
        }

        const launchContextActive = Boolean(new URLSearchParams(window.location.search).get('launch_context_id'));
        const pinnedIncludesLockGpus = Object.prototype.hasOwnProperty.call(initialValues || {}, 'lock_gpus');
        const pinnedIncludesAllowRetries = Object.prototype.hasOwnProperty.call(initialValues || {}, 'allow_retries');
        submitMutation.mutate({
            name: jobName,
            model_id: modelId,
            mode: mode,
            params: {
                ...params,
                target_source: targetSource || undefined,
                pinned_gpus: pinnedGpus.length > 0 ? pinnedGpus : undefined,
                lock_gpus: launchContextActive && !pinnedIncludesLockGpus ? undefined : lockGpus && pinnedGpus.length > 0,
                allow_retries: launchContextActive && !pinnedIncludesAllowRetries ? undefined : allowRetries
            },
            pinned_gpu: pinnedGpus.length === 1 ? pinnedGpus[0] : null
        });

        // Treat force-refresh as a one-shot action to avoid accidental cache-bypass on reruns.
        if (msaForceRefresh) {
            setMsaForceRefresh(false);
        }
    };

    const handlePdbSelect = async (target: SelectedTarget | null) => {
        if (!target) return;

        try {
            let file: File;
            let nextModalPreviewUrl: string | null = null;
            if (target.type === 'upload' && target.file) {
                file = target.file;
                nextModalPreviewUrl = URL.createObjectURL(file);
            } else if (target.url) {
                const response = await fetch(target.url);
                const blob = await response.blob();
                file = new File([blob], canonicalTargetSourceName(target), { type: blob.type || 'chemical/x-pdb' });
            } else {
                return;
            }

            const parsed = await parsePDBFile(file);
            const defaultModelNumber = parsed.models[0]?.modelNumber ?? 1;
            if (nextModalPreviewUrl) {
                replacePreviewBlobUrl(modalPreviewBlobRef, setModalPreviewUrl, nextModalPreviewUrl);
            } else {
                clearModalPreview();
            }
            setModalTargetSource(target);
            setModalParsedStructure(parsed);
            setModalSelectedModel(defaultModelNumber);
            setSequenceName(target.name.replace(/\.pdb$/i, ''));

            if (parsed.chains.length === 1) {
                const onlyChain = parsed.chains[0];
                if (importTargetRef.current === 'additional') {
                    setLigands((prev) => [...prev, {
                        id: onlyChain.id,
                        type: 'protein',
                        sequence: onlyChain.sequence,
                        name: `Chain ${onlyChain.id}`,
                    }]);
                } else {
                    setTargetSource(target);
                    setTargetSourcePath(target.path || null);
                    adoptModalPreviewForTarget();
                    setSequence(onlyChain.sequence);
                    setPrimaryChainId(onlyChain.id || 'A');
                    setTargetSourceChainId(onlyChain.id || 'A');
                    setTargetSourceSequence(onlyChain.sequence);
                    setTargetStructure(parsed);
                    setSelectedTargetModel(defaultModelNumber);
                }
                setShowInputModal(false);
                closePdbModalState();
            } else if (parsed.chains.length > 1) {
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
        const sourceChains = activeModalModel?.chains ?? parsedChains;
        const selectedChains = sourceChains.filter((_, i) => selectedChainIndices.has(i));
        if (selectedChains.length === 0) return;

        // Sort by ID to keep order deterministic (A, B, C...)
        selectedChains.sort((a, b) => a.id.localeCompare(b.id));

        if (importTargetRef.current === 'additional') {
            const newLigands: LigandEntry[] = selectedChains.map((c) => ({
                id: c.id,
                type: 'protein',
                sequence: c.sequence,
                name: `Chain ${c.id}`
            }));
            setLigands((prev) => [...prev, ...newLigands]);
            setShowInputModal(false);
            closePdbModalState();
            return;
        }

        if (modalTargetSource) {
            setTargetSource(modalTargetSource);
            setTargetSourcePath(modalTargetSource.path || null);
        }
        adoptModalPreviewForTarget();

        // First chain is primary target
        const primary = selectedChains[0];
        setSequence(primary.sequence);
        setPrimaryChainId(primary.id || 'A');
        setTargetSourceChainId(primary.id || 'A');
        setTargetSourceSequence(primary.sequence);
        setTargetStructure(modalParsedStructure);
        setSelectedTargetModel(modalSelectedModel);

        // Others are ligands/complex components
        const others = selectedChains.slice(1);
        if (others.length > 0) {
            const newLigands: LigandEntry[] = others.map(c => ({
                id: c.id,
                type: 'protein',
                sequence: c.sequence,
                name: `Chain ${c.id}`
            }));
            setLigands(prev => [...prev, ...newLigands]);
        }

        setShowInputModal(false);
        closePdbModalState();
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

    const showBoltzParams = (usesBoltz || usesFoldCp) && !isBoltzApi;
    const showProtenixParams = usesProtenix;
    const showEsmFold2Params = usesEsmFold2;
    const structureTemplateBaseId = 'structure_prediction';
    const structureDocumentationTopics = useMemo<ModelDocumentationTopic[]>(() => {
        if (isBoltzCpLaunch) return ['fold_cp', 'boltz2'];
        const topics: ModelDocumentationTopic[] = [];
        if (usesBoltz) topics.push('boltz2');
        if (usesProtenix) topics.push('protenix');
        if (usesEsmFold2) topics.push('esmfold2');
        return topics.length > 0 ? topics : ['boltz2'];
    }, [isBoltzCpLaunch, usesBoltz, usesProtenix, usesEsmFold2]);

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
                {onOpenTemplateManager && resolvedPredictorSelection.valid && (
                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            onClick={() => onOpenTemplateManager({
                                currentModelId: submitTarget.modelId,
                                currentMode: submitTarget.mode,
                                baseTemplateId: structureTemplateBaseId,
                            })}
                            className="rounded-lg border border-slate-600 bg-slate-900/60 px-3 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-800"
                        >
                            Load Template
                        </button>
                        <button
                            type="button"
                            onClick={() => onOpenTemplateManager({
                                currentParams: currentTemplateParams,
                                currentModelId: submitTarget.modelId,
                                currentMode: submitTarget.mode,
                                baseTemplateId: structureTemplateBaseId,
                            })}
                            className="rounded-lg border border-slate-600 bg-slate-900/60 px-3 py-2 text-sm font-medium text-slate-200 transition-colors hover:bg-slate-800"
                        >
                            Save Template
                        </button>
                    </div>
                )}
            </div>

            {sequenceHandoffLoading && <div role="status" className="mb-4 rounded-lg border border-cyan-500/30 bg-cyan-500/10 px-4 py-3 text-sm text-cyan-200">Loading the durable saved sequence…</div>}
            {sequenceHandoffError && <div role="alert" className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-200">{sequenceHandoffError}</div>}

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
                    {!isBoltzApi && (
                    <div>
                        <label className="block text-sm font-medium text-slate-400 mb-2">
                            GPU Pinning {pinnedGpus.length > 0 && <span className="text-blue-400">({pinnedGpus.length} selected)</span>}
                        </label>
                        <div className="flex gap-2">
                            <button
                                onClick={clearGpuPinning}
                                className={`px-3 py-2 rounded-lg font-medium text-sm transition-all ${pinnedGpus.length === 0
                                    ? 'bg-slate-600 text-white ring-2 ring-slate-400'
                                    : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                    }`}
                            >
                                Auto
                            </button>
                            {gpuOptions.map(gpu => (
                                <button
                                    key={gpu.index}
                                    onClick={() => {
                                        setPinnedGpus(prev =>
                                            prev.includes(gpu.index)
                                                ? prev.filter(g => g !== gpu.index)
                                                : [...prev, gpu.index].sort((a, b) => a - b)
                                        );
                                    }}
                                    className={`px-3 py-2 rounded-lg font-medium text-sm transition-all ${pinnedGpus.includes(gpu.index)
                                        ? 'bg-blue-600 text-white ring-2 ring-blue-400'
                                        : 'bg-slate-800 text-slate-400 hover:bg-slate-700'
                                        }`}
                                >
                                    {gpu.label}
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
                    )}
                </div>

                <div>
                    <label className="block text-sm font-medium text-slate-400 mb-3">Structure Predictor</label>
                    {launchConfig.allowPredictorSelection ? (
                        <div className={`grid grid-cols-2 ${predictionMode === 'complex' ? 'sm:grid-cols-4' : 'sm:grid-cols-5'} gap-3`}>
                            {predictorOptions.map((pred) => {
                                const isSelected = selectedPredictorId === pred.id || predictor === pred.id;
                                const selectedClass = pred.color === 'blue'
                                    ? 'bg-blue-600/20 border-blue-500 text-blue-300'
                                    : pred.color === 'green'
                                        ? 'bg-green-600/20 border-green-500 text-green-300'
                                        : pred.color === 'violet'
                                            ? 'bg-violet-600/20 border-violet-500 text-violet-300'
                                            : pred.color === 'amber'
                                                ? 'bg-amber-600/20 border-amber-500 text-amber-300'
                                                : 'bg-accent/20 border-accent text-accent';
                                return (
                                    <button
                                        key={pred.id}
                                        type="button"
                                        onClick={() => {
                                            if (!pred.disabled) {
                                                setPredictor(pred.id);
                                            }
                                        }}
                                        disabled={pred.disabled}
                                        title={pred.disabledReason}
                                        className={`p-3 rounded-lg border text-left transition-all ${isSelected
                                            ? selectedClass
                                            : pred.disabled
                                                ? 'bg-slate-900/60 border-slate-800 text-slate-600 cursor-not-allowed'
                                                : 'bg-slate-900 border-slate-700 text-slate-400 hover:border-slate-600'
                                            }`}
                                    >
                                        <div className="font-medium mb-1 flex items-center justify-between gap-2">
                                            <span>{pred.name}</span>
                                            {pred.disabled && <span className="text-[10px] uppercase tracking-wide text-slate-500">Unavailable</span>}
                                        </div>
                                        <div className="text-xs opacity-70">{pred.desc}</div>
                                        {pred.disabledReason && (
                                            <div className="text-[11px] text-amber-300/80 mt-2 leading-snug">{pred.disabledReason}</div>
                                        )}
                                    </button>
                                );
                            })}
                        </div>
                    ) : (
                        <div className="rounded-lg border border-orange-500/30 bg-orange-500/10 px-4 py-3 text-sm text-orange-100">
                            <div className="font-medium">Fixed predictor</div>
                            <div className="mt-1 text-xs text-orange-100/80">
                                Fixed predictor variant.
                            </div>
                        </div>
                    )}
                    {!resolvedPredictorSelection.valid && (
                        <div role="alert" className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
                            <div className="font-semibold">Retired historical predictor</div>
                            <div className="mt-1 text-xs text-amber-100/80">{resolvedPredictorSelection.error}</div>
                        </div>
                    )}
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
                <div className={`grid ${launchConfig.showParallelJobs && !isBoltzApi && !isBoltzCpLaunch ? 'grid-cols-2' : 'grid-cols-1'} gap-4`}>
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
                    {launchConfig.showParallelJobs && !isBoltzApi && !isBoltzCpLaunch && (
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">Parallel Jobs</label>
                            <input
                                type="number"
                                value={numParallelJobs}
                                onChange={(e) => setNumParallelJobs(Math.max(1, Math.min(500, parseInt(e.target.value) || 1)))}
                                min={1}
                                max={500}
                                disabled={hasBatchEntries}
                                className={`w-full border border-slate-700 rounded-lg px-3 py-2 text-white text-sm ${hasBatchEntries ? 'bg-slate-800/70 cursor-not-allowed text-slate-500' : 'bg-slate-900'}`}
                            />
                            {hasBatchEntries && (
                                <p className="mt-1 text-xs text-slate-500">Batch launches derive one runtime per pasted sequence and ignore this single-sequence fanout.</p>
                            )}
                        </div>
                    )}
                </div>

                {launchConfig.showSequenceBatch && !isBoltzApi && !isBoltzCpLaunch && (
                    <div className="border border-slate-700/50 rounded-lg p-4 space-y-4">
                        <div>
                            <h3 className="text-sm font-semibold text-slate-200">Sequence Matrix Batch</h3>
                            <p className="text-xs text-slate-500">
                                Paste FASTA or one sequence per line; target imports become shared binder screens.
                            </p>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Default Name Prefix</label>
                                <input
                                    type="text"
                                    value={sequenceBatchPrefix}
                                    onChange={(e) => setSequenceBatchPrefix(e.target.value)}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-white text-sm"
                                    placeholder="variant"
                                />
                            </div>
                            {complexMode && proteinBatchTargets.length > 1 && (
                                <div>
                                    <label className="text-xs text-slate-400 block mb-1">Replace Protein Component</label>
                                    <select
                                        value={resolvedSequenceBatchComponentId}
                                        onChange={(e) => setSequenceBatchComponentId(e.target.value)}
                                        className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-white text-sm"
                                    >
                                        {proteinBatchTargets.map((entry) => (
                                            <option key={entry.id} value={entry.id}>
                                                {entry.role}: {entry.name}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                            )}
                            <div className="bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-2">
                                <div className="text-xs text-slate-500 mb-1">Parsed Variants</div>
                                <div className="text-white text-sm">{batchEntriesPreview.length}</div>
                                <div className="text-xs text-slate-500 mt-1">
                                    {batchEntriesPreview[0]?.name ? `First: ${batchEntriesPreview[0].name}` : 'Paste FASTA or lines to enable'}
                                </div>
                            </div>
                        </div>
                        <textarea
                            value={sequenceBatchInput}
                            onChange={(e) => setSequenceBatchInput(e.target.value)}
                            placeholder={'>sample_001\nQVQLVESGGGLVQ...\n>sample_002\nQVQLVESGGGLVQ...\n\nor:\nseq_003: QVQLVESGGGLVQ...'}
                            rows={7}
                            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2.5 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-blue-500 outline-none"
                        />
                        {batchEntriesPreview.length > 0 && (
                            <div className="text-xs text-slate-400 bg-slate-900/50 border border-slate-800 rounded-lg px-3 py-2">
                                {autoBatchBinderMode
                                    ? `Shared target: ${batchEntriesPreview.length} candidates vs chain ${targetSourceChainId || primaryChainId}.`
                                    : 'Batch mode will use deterministic per-sequence names and ignore the single-sequence parallel fanout.'}
                            </div>
                        )}
                    </div>
                )}

                {targetSource && (
                    <div className="border border-slate-700/50 rounded-lg p-4 space-y-4">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <h3 className="text-sm font-semibold text-slate-200">Target Geometry Source</h3>
                                <p className="text-xs text-slate-500">
                                    Imported target is staged for conditioned/frozen complex prediction; keep the primary chain sequence unchanged.
                                </p>
                            </div>
                            <button
                                type="button"
                                onClick={() => {
                                    setTargetSource(null);
                                    setTargetSourcePath(null);
                                    clearTargetPreview();
                                    setTargetSourceChainId(null);
                                    setTargetSourceSequence('');
                                    setTargetStructure(null);
                                    setPrimaryChainId('A');
                                    setBoltzTargetGeometryMode('flexible');
                                    setProtenixTargetGeometryMode('flexible');
                                }}
                                className="text-xs text-slate-400 hover:text-white"
                            >
                                Clear Source
                            </button>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
                            <div className="bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-2">
                                <div className="text-xs text-slate-500 mb-1">Source</div>
                                <div className="text-slate-200 break-all">{targetSource.name}</div>
                            </div>
                            <div className="bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-2">
                                <div className="text-xs text-slate-500 mb-1">Primary Chain</div>
                                <div className="text-slate-200">{targetSourceChainId || primaryChainId}</div>
                            </div>
                            <div className="bg-slate-900/60 border border-slate-700/60 rounded-lg px-3 py-2">
                                <div className="text-xs text-slate-500 mb-1">Staged Path</div>
                                <div className="text-slate-200 break-all">{targetSourcePath || 'Will stage at submit'}</div>
                            </div>
                        </div>

                        {targetStructure && targetStructure.models.length > 1 && (
                            <div className="max-w-xs">
                                <label className="text-xs text-slate-400 block mb-1">Target Model</label>
                                <select
                                    value={selectedTargetModel ?? targetStructure.models[0]?.modelNumber ?? 1}
                                    onChange={(e) => setSelectedTargetModel(Number(e.target.value) || 1)}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                >
                                    {targetStructure.models.map((model) => (
                                        <option key={model.modelNumber} value={model.modelNumber}>
                                            {model.label}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        )}

                        {targetPreview.structureUrl && (
                            <div className="rounded-xl border border-slate-700/60 bg-slate-950/50 overflow-hidden">
                                <div className="flex items-center justify-between px-3 py-2 border-b border-slate-800 text-xs text-slate-400">
                                    <span>Source mini-viewer</span>
                                    <span>{targetPreview.format.toUpperCase()}</span>
                                </div>
                                <MolstarViewer
                                    structureUrl={targetPreview.structureUrl}
                                    format={targetPreview.format}
                                    height={240}
                                    hideControls={true}
                                    alphafoldView={false}
                                    selections={targetPreviewSelections}
                                    label={targetSourceChainId || primaryChainId || undefined}
                                />
                                <div className="px-3 py-2 text-xs text-slate-500 border-t border-slate-800">
                                    Imported primary chain {targetSourceChainId || primaryChainId || 'A'} is highlighted in blue.
                                </div>
                            </div>
                        )}

                        {complexMode ? (
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                {usesBoltz && !isBoltzApi && (
                                    <div className="p-3 bg-blue-500/10 border border-blue-500/30 rounded-lg">
                                        <label className="text-xs text-blue-100/80 block mb-1">Boltz target geometry</label>
                                        <select
                                            value={boltzTargetGeometryMode}
                                            onChange={(e) => setBoltzTargetGeometryMode(e.target.value as 'flexible' | 'conditioned' | 'frozen')}
                                            className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                        >
                                            <option value="flexible">Flexible</option>
                                            <option value="conditioned">Conditioned</option>
                                            <option value="frozen">Hard Frozen</option>
                                        </select>
                                        <p className="mt-2 text-xs text-blue-100/70">Conditioned applies model-native target steering. Hard Frozen also restores exact target coordinates after prediction.</p>
                                    </div>
                                )}
                                {usesProtenix && (
                                    <div className="p-3 bg-violet-500/10 border border-violet-500/30 rounded-lg">
                                        <label className="text-xs text-violet-100/80 block mb-1">Protenix target geometry</label>
                                        <select
                                            value={protenixTargetGeometryMode}
                                            onChange={(e) => setProtenixTargetGeometryMode(e.target.value as 'flexible' | 'conditioned' | 'frozen')}
                                            className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                        >
                                            <option value="flexible">Flexible</option>
                                            <option value="conditioned">Conditioned</option>
                                            <option value="frozen">Hard Frozen</option>
                                        </select>
                                        <p className="mt-2 text-xs text-violet-100/70">Conditioned applies template guidance. Hard Frozen also restores exact target coordinates after prediction.</p>
                                    </div>
                                )}
                            </div>
                        ) : (
                            <p className="text-xs text-amber-300/80">
                                Import a shared target source or add an additional component to enable target conditioning.
                            </p>
                        )}
                    </div>
                )}

                {/* Remote Boltz API queue */}
                {isBoltzApi && (
                    <div className="border border-blue-500/40 bg-blue-500/5 rounded-lg p-4 space-y-4" data-bms-boltz-api-submit="true">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <h3 className="text-sm font-semibold text-blue-300">Boltz API submission</h3>
                                <p className="mt-1 text-xs text-slate-400">
                                    Estimate the provider charge, approve it, then queue a remote Boltz-2.1 structure prediction. Completion is downloaded and ingested automatically.
                                </p>
                            </div>
                            <span className={`rounded-full border px-2.5 py-1 text-[11px] font-medium ${boltzApiStatus?.available
                                ? 'border-emerald-400/30 bg-emerald-500/10 text-emerald-200'
                                : 'border-amber-400/30 bg-amber-500/10 text-amber-200'
                                }`}>
                                {boltzApiStatus?.available ? 'Provider configured' : 'Provider unavailable'}
                            </span>
                        </div>
                        {boltzApiStatus && !boltzApiStatus.available && (
                            <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                                {boltzApiStatus.message}
                            </div>
                        )}
                        <BoltzApiNativeSettingsPanel status={boltzApiStatus} />
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Provider model</label>
                                <div className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white">Boltz-2.1</div>
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Use provider MSA</label>
                                <select
                                    value={boltzUseMsa ? 'true' : 'false'}
                                    onChange={(event) => setBoltzUseMsa(event.target.value === 'true')}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-2 text-white text-sm"
                                >
                                    <option value="true">Yes</option>
                                    <option value="false">No</option>
                                </select>
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Samples</label>
                                <input
                                    type="number"
                                    value={Math.min(10, boltzNumSamples)}
                                    onChange={(event) => setBoltzNumSamples(Math.max(1, Math.min(10, Number.parseInt(event.target.value, 10) || 1)))}
                                    min={1}
                                    max={10}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-2 text-white text-sm"
                                />
                            </div>
                        </div>
                        {boltzApiEstimate && (
                            <div className="rounded-lg border border-emerald-500/25 bg-emerald-500/5 p-3 space-y-3">
                                <div className="text-xs font-medium text-emerald-200">Current provider estimate</div>
                                <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs text-slate-300">{JSON.stringify(boltzApiEstimate.estimate, null, 2)}</pre>
                                <label className="flex items-start gap-3 text-sm text-slate-200">
                                    <input
                                        type="checkbox"
                                        checked={boltzApiEstimateApproved}
                                        onChange={(event) => setBoltzApiEstimateApproved(event.target.checked)}
                                        className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-900 text-blue-500"
                                    />
                                    <span>I approve this provider estimate and authorize queueing the remote job.</span>
                                </label>
                            </div>
                        )}
                        {boltzApiEstimateError && (
                            <div className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-200">{boltzApiEstimateError}</div>
                        )}
                    </div>
                )}

                {/* Boltz-2 Parameters */}
                {showBoltzParams && (
                    <div className="border border-slate-700/50 rounded-lg p-4 space-y-4">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <h3 className="text-sm font-semibold text-blue-400">{isBoltzCpLaunch ? 'NVIDIA Fold-CP Settings' : 'Boltz-2 Settings'}</h3>
                            <span className="rounded-full border border-blue-500/30 bg-blue-500/10 px-2.5 py-1 text-[11px] font-medium text-blue-200">
                                {isBoltzCpLaunch ? 'OEM context parallelism' : 'Model controls'}
                            </span>
                        </div>
                        <ModelDocumentationLinks
                            topics={structureDocumentationTopics}
                            summary={isBoltzCpLaunch ? 'NVIDIA Fold-CP runs the selected structure input with OEM DTensor context parallelism.' : 'Method background is linked out; this panel only exposes runtime knobs.'}
                            compact
                        />

                        {/* Physics potentials are a Boltz-2 control, not an OEM Fold-CP option. */}
                        {!isBoltzCpLaunch && (
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
                                <p className="text-xs text-slate-500">physics/FK steering potentials; use batching for high sample counts.</p>
                            </label>
                        </div>
                        )}

                        <div className={`grid grid-cols-1 ${launchConfig.showMsaControls ? 'md:grid-cols-2' : ''} gap-4`}>
                            {launchConfig.showMsaControls && (
                                <div>
                                    <label className="text-xs text-slate-400 block mb-1">Use MSA</label>
                                    <select
                                        value={boltzUseMsa ? 'true' : 'false'}
                                        onChange={(e) => {
                                            const nextUseMsa = e.target.value === 'true';
                                            setBoltzUseMsa(nextUseMsa);
                                            setBoltzRecyclingSteps((prev) => clampBoltzRecyclingSteps(prev, nextUseMsa));
                                            setBoltzSamplingSteps((prev) => clampBoltzSamplingSteps(prev, nextUseMsa));
                                        }}
                                        className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                    >
                                        <option value="true">Yes</option>
                                        <option value="false">No</option>
                                    </select>
                                </div>
                            )}
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Recycling Steps</label>
                                <input
                                    type="number"
                                    value={boltzRecyclingSteps}
                                    onChange={(e) => setBoltzRecyclingSteps(clampBoltzRecyclingSteps(e.target.value, boltzUseMsa))}
                                    min={boltzUseMsa ? 1 : MIN_BOLTZ_NO_MSA_RECYCLING_STEPS}
                                    max={10}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                            </div>
                        </div>

                        <div className="rounded-lg border border-blue-500/20 bg-blue-500/5 p-4 space-y-4">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                                <div>
                                    <label className="text-xs text-blue-100/80 block mb-1">Sampling Quality</label>
                                    <div className="text-sm text-slate-200">
                                        {boltzQualityState.label} · {boltzSamplingSteps} sampling steps
                                    </div>
                                    <p className="text-xs text-slate-400 mt-1">
                                        200-step High is the default top preset. Older 1000-step runs remain editable as custom legacy settings.
                                    </p>
                                </div>
                                <div className="text-right text-xs text-slate-400">
                                    <div>{boltzUseMsa ? 'MSA-enabled' : 'No-MSA clamp active'}</div>
                                    {!boltzUseMsa && <div>Minimum 50 steps enforced</div>}
                                </div>
                            </div>

                            <div className="grid grid-cols-3 gap-2">
                                {BOLTZ_QUALITY_PRESETS.map((preset) => {
                                    const isActive = boltzQualityState.presetId === preset.id;
                                    return (
                                        <button
                                            key={preset.id}
                                            type="button"
                                            onClick={() => setBoltzSamplingSteps(clampBoltzSamplingSteps(preset.samplingSteps, boltzUseMsa))}
                                            className={`rounded-lg border px-3 py-2 text-left transition-colors ${isActive
                                                ? 'border-blue-400 bg-blue-500/15 text-blue-100'
                                                : 'border-slate-700 bg-slate-900/70 text-slate-300 hover:border-slate-500'
                                                }`}
                                        >
                                            <div className="text-sm font-medium">{preset.label}</div>
                                            <div className="text-xs text-slate-400 mt-1">{preset.samplingSteps} steps</div>
                                        </button>
                                    );
                                })}
                            </div>

                            <div>
                                <input
                                    type="range"
                                    min={0}
                                    max={boltzQualityState.sliderMax}
                                    value={boltzQualityState.sliderValue}
                                    onChange={(e) => setBoltzSamplingSteps(clampBoltzSamplingSteps(
                                        resolveBoltzSamplingStepsFromSlider({
                                            currentSamplingSteps: boltzSamplingSteps,
                                            sliderValue: Number(e.target.value),
                                        }),
                                        boltzUseMsa,
                                    ))}
                                    className="w-full accent-blue-500"
                                />
                                <div className={`mt-2 grid text-[11px] text-slate-500 ${boltzQualityState.presetId === 'custom' ? 'grid-cols-4' : 'grid-cols-3'}`}>
                                    {BOLTZ_QUALITY_PRESETS.map((preset) => (
                                        <div key={preset.id} className="text-center">{preset.label}</div>
                                    ))}
                                    {boltzQualityState.presetId === 'custom' && <div className="text-center">Custom</div>}
                                </div>
                            </div>

                            {boltzQualityState.presetId === 'custom' && (
                                <div className="max-w-xs">
                                    <label className="text-xs text-slate-400 block mb-1">Legacy custom sampling steps</label>
                                    <input
                                        type="number"
                                        value={boltzSamplingSteps}
                                        onChange={(e) => setBoltzSamplingSteps(clampBoltzSamplingSteps(e.target.value, boltzUseMsa))}
                                        min={boltzUseMsa ? 10 : MIN_BOLTZ_NO_MSA_SAMPLING_STEPS}
                                        max={1000}
                                        className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                    />
                                </div>
                            )}
                        </div>

                        {isBoltzCpLaunch && (
                            <div className="rounded-lg border border-orange-500/20 bg-orange-500/5 p-4 space-y-4">
                                <div>
                                    <label className="text-xs text-orange-100/80 block mb-1">Context Parallel Size Request</label>
                                    <input
                                        type="number"
                                        value={bcpRequestedSizeCp}
                                        onChange={(e) => setBcpRequestedSizeCp(Math.max(1, Math.min(16, Number.parseInt(e.target.value || '1', 10) || 1)))}
                                        min={1}
                                        max={16}
                                        className="w-full max-w-xs bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                    />
                                    <p className="mt-2 text-xs text-slate-400">
                                        OEM Fold-CP uses a square context-parallel mesh. Current GPU resolution: {boltzCpGpuSettings.gpuIds || 'auto fallback'} → size_cp {boltzCpGpuSettings.sizeCp}.
                                    </p>
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                    <div>
                                        <label className="text-xs text-slate-400 block mb-1">Output Format</label>
                                        <select
                                            value={bcpOutputFormat}
                                            onChange={(e) => setBcpOutputFormat(e.target.value === 'pdb' ? 'pdb' : 'mmcif')}
                                            className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                        >
                                            <option value="mmcif">mmCIF</option>
                                            <option value="pdb">PDB</option>
                                        </select>
                                    </div>
                                    <div>
                                        <label className="text-xs text-slate-400 block mb-1">Seed</label>
                                        <input
                                            type="text"
                                            value={bcpSeed}
                                            onChange={(e) => setBcpSeed(e.target.value.replace(/[^0-9-]/g, ''))}
                                            placeholder="optional"
                                            className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                        />
                                    </div>
                                    <label className="flex items-center gap-3 rounded-lg border border-slate-700/60 bg-slate-900/40 px-3 py-2 text-sm text-slate-200">
                                        <input
                                            type="checkbox"
                                            checked={bcpWriteFullPae}
                                            onChange={(e) => setBcpWriteFullPae(e.target.checked)}
                                            className="w-4 h-4 rounded bg-slate-900 border-slate-700 text-orange-500"
                                        />
                                        <span>Write full PAE matrix</span>
                                    </label>
                                </div>
                            </div>
                        )}

                        <div className={`grid ${isBoltzCpLaunch ? 'grid-cols-1' : 'grid-cols-2'} gap-4`}>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Num Samples</label>
                                <input
                                    type="number"
                                    value={boltzNumSamples}
                                    onChange={(e) => setBoltzNumSamples(Math.max(1, Math.min(32, parseInt(e.target.value) || 1)))}
                                    min={1}
                                    max={32}
                                    title={BOLTZ_NUM_SAMPLES_HELP_TEXT}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                />
                                <p className="mt-1 text-[11px] leading-snug text-slate-500">{BOLTZ_NUM_SAMPLES_HELP_TEXT}</p>
                            </div>
                            {!isBoltzCpLaunch && (
                                <div>
                                    <label className="text-xs text-slate-400 block mb-1">Denoiser Chunk Limit</label>
                                    <input
                                        type="number"
                                        value={boltzMaxParallelSamples}
                                        onChange={(e) => setBoltzMaxParallelSamples(Math.max(1, Math.min(boltzNumSamples, parseInt(e.target.value) || 1)))}
                                        min={1}
                                        max={boltzNumSamples}
                                        title={BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT}
                                        className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                    />
                                    <p className="mt-1 text-[11px] leading-snug text-slate-500">{BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT}</p>
                                </div>
                            )}
                        </div>

                        {!boltzUseMsa && (
                            <p className="text-xs text-amber-300/90 mt-3">
                                {isBoltzCpLaunch
                                    ? 'No-MSA Fold-CP runs use the displayed OEM sampling and recycling settings.'
                                    : 'No-MSA Boltz-2 runs are held to at least 50 sampling steps and 3 recycling steps to avoid malformed geometry.'}
                            </p>
                        )}

                        {!isBoltzCpLaunch && (
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
                        )}
                    </div>
                )}

                {/* ESMFold2 Parameters */}
                {showEsmFold2Params && (
                    <div className="border border-cyan-500/30 bg-cyan-500/5 rounded-lg p-4 space-y-4" data-bms-esmfold2-structure-variant="true">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <h3 className="text-sm font-semibold text-cyan-300">ESMFold2 Settings</h3>
                            <span className="rounded-full border border-cyan-400/25 bg-cyan-500/10 px-2.5 py-1 text-[11px] font-medium text-cyan-200">
                                Local-only
                            </span>
                        </div>
                        <ModelDocumentationLinks
                            topics={structureDocumentationTopics}
                            summary="Docs linked; structure inputs stay in this launcher."
                            compact
                        />
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Model Variant</label>
                                <select
                                    value={esmfold2Variant}
                                    onChange={(e) => setEsmfold2Variant(e.target.value === 'full' ? 'full' : 'fast')}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                >
                                    <option value="fast">Fast</option>
                                    <option value="full">Full</option>
                                </select>
                            </div>
                            <div className="rounded-lg border border-slate-700/60 bg-slate-900/40 px-3 py-2 text-xs text-slate-400">
                                PDB import supplies sequence; DNA/RNA/ligands use Complex Components below. PDB coordinates are not structural templates.
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
                                    <option value="protenix-v2">Protenix v2 (required)</option>
                                </select>
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Use MSA</label>
                                <select
                                    value={protenixUseMsa ? 'true' : 'false'}
                                    onChange={(e) => {
                                        setProtenixUseMsa(e.target.value === 'true');
                                    }}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                >
                                    <option value="true">Yes</option>
                                    <option value="false">No (V2 sequence-only mode)</option>
                                </select>
                            </div>
                            <div>
                                <label className="text-xs text-slate-400 block mb-1">Geometry Mode</label>
                                <select
                                    value={protenixTargetGeometryMode}
                                    onChange={(e) => setProtenixTargetGeometryMode(e.target.value as 'flexible' | 'conditioned' | 'frozen')}
                                    className="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-white text-sm"
                                >
                                    <option value="flexible">Flexible</option>
                                    <option value="conditioned">Conditioned</option>
                                    <option value="frozen">Hard Frozen</option>
                                </select>
                            </div>
                        </div>
                        {protenixTargetGeometryMode !== 'flexible' && (
                            <p className="text-xs text-amber-300/90">
                                Conditioned and frozen Protenix runs use the imported target structure as a template source.
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
                {((showBoltzParams && boltzUseMsa) || (showProtenixParams && protenixUseMsa)) && (
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
                                            <option value="local">Local MMseqs2 (manual override)</option>
                                            <option value="colabfold_api" disabled={numParallelJobs > 1}>
                                                ColabFold API (default; single-job only)
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

                                {msaProvider === 'local' && (
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 p-3 rounded-lg border border-emerald-500/30 bg-emerald-500/5">
                                        <div>
                                            <label className="text-xs text-[var(--text-secondary)] block mb-1">EnvDB Target Sharding</label>
                                            <select
                                                value={msaTargetShardMode}
                                                onChange={(e) => setMsaTargetShardMode(normalizeMsaTargetShardMode(e.target.value))}
                                                className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                            >
                                                <option value="auto">Auto for balanced/maximum</option>
                                                <option value="required">Required (fail if unavailable)</option>
                                                <option value="off">Off / unsharded fallback</option>
                                            </select>
                                        </div>
                                        <div>
                                            <label className="text-xs text-[var(--text-secondary)] block mb-1">Target Shards</label>
                                            <input
                                                type="number"
                                                min={1}
                                                step={1}
                                                value={msaTargetShards}
                                                onChange={(e) => setMsaTargetShards(normalizeMsaTargetShards(e.target.value))}
                                                className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                            />
                                        </div>
                                        <div>
                                            <label className="text-xs text-[var(--text-secondary)] block mb-1">Min DB Size (GB)</label>
                                            <input
                                                type="number"
                                                min={0}
                                                step={0.1}
                                                value={msaTargetShardMinSizeGb}
                                                onChange={(e) => setMsaTargetShardMinSizeGb(normalizeMsaTargetShardMinSizeGb(e.target.value))}
                                                className="w-full bg-[var(--bg-primary)] border border-[var(--border-primary)] rounded px-2 py-1.5 text-[var(--text-primary)] text-sm"
                                            />
                                        </div>
                                        <p className="md:col-span-3 text-xs text-emerald-200/80">
                                            Shards EnvDB for balanced/maximum runs. Fast screens quickly; Off is rollback/debug.
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
                <div className={`flex ${isBoltzApi ? 'justify-end' : 'justify-between'} items-center pt-6 border-t border-slate-800`}>
                    {/* Left side: managed post-prediction analysis and retry policy */}
                    {!isBoltzApi && <div className="flex flex-wrap items-start gap-5">
                        <ModelIntegrationControl
                            modelId="frustrampnn"
                            workflowId="structure_prediction"
                            checked={runFrustrampnn}
                            onChange={updateRunFrustrampnn}
                            fallbackLabel="Frustration analysis"
                            integration={frustrampnnIntegrationQuery.data}
                            settingsControl={(
                                <FrustraMpnnSettingsPanel
                                    value={frustrampnnSettings}
                                    onChange={setFrustrampnnSettings}
                                />
                            )}
                        />
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
                    </div>}

                    {/* Right side: Submit button */}
                    <button
                        onClick={handleSubmit}
                        disabled={!resolvedPredictorSelection.valid || !sequence.trim() || submitMutation.isPending || boltzApiSubmitMutation.isPending || boltzApiEstimating || (isBoltzApi && (!boltzApiStatus || boltzApiStatus.available === false))}
                        className="px-6 py-3 bg-gradient-to-r from-blue-600 to-accent-secondary hover:from-blue-500 hover:to-accent disabled:opacity-50 disabled:grayscale text-white font-bold rounded-lg shadow-lg shadow-accent/20 transition-all transform active:scale-95"
                    >
                        {isBoltzApi
                            ? (boltzApiSubmitMutation.isPending
                                ? 'Queueing Boltz API job...'
                                : boltzApiEstimating
                                    ? 'Estimating API cost...'
                                    : boltzApiEstimate
                                        ? 'Queue Boltz API job'
                                        : 'Estimate API cost')
                            : (submitMutation.isPending ? 'Submitting...' : 'Launch Prediction')}
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
                                                setPrimaryChainId('A');
                                                setTargetSource(null);
                                                setTargetSourcePath(null);
                                                clearTargetPreview();
                                                setTargetSourceChainId(null);
                                                setTargetSourceSequence('');
                                                setTargetStructure(null);
                                                setBoltzTargetGeometryMode('flexible');
                                                setProtenixTargetGeometryMode('flexible');
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
                                                    onClick={closePdbModalState}
                                                    className="text-sm text-slate-400 hover:text-white"
                                                >
                                                    ← Back to search
                                                </button>
                                            </div>
                                            {modalParsedStructure && modalParsedStructure.models.length > 1 && (
                                                <div className="max-w-xs">
                                                    <label className="block text-sm font-medium text-slate-400 mb-2">Source Model</label>
                                                    <select
                                                        value={modalSelectedModel}
                                                        onChange={(e) => setModalSelectedModel(Number(e.target.value) || 1)}
                                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm"
                                                    >
                                                        {modalParsedStructure.models.map((model) => (
                                                            <option key={model.modelNumber} value={model.modelNumber}>
                                                                {model.label}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>
                                            )}
                                            <p className="text-sm text-slate-500">
                                                Multiple chains found in the selected structure. Choose the model and chain set you want to import.
                                            </p>
                                            {modalPreview.structureUrl && (
                                                <div className="rounded-xl border border-slate-700/60 bg-slate-950/50 overflow-hidden">
                                                    <div className="flex items-center justify-between px-3 py-2 border-b border-slate-800 text-xs text-slate-400">
                                                        <span>Source mini-viewer</span>
                                                        <span>{modalPreview.format.toUpperCase()}</span>
                                                    </div>
                                                    <MolstarViewer
                                                        structureUrl={modalPreview.structureUrl}
                                                        format={modalPreview.format}
                                                        height={260}
                                                        hideControls={true}
                                                        alphafoldView={false}
                                                        selections={modalPreviewSelections}
                                                        label={modalPreviewSelections[0]?.chain_id}
                                                    />
                                                    <div className="px-3 py-2 text-xs text-slate-500 border-t border-slate-800">
                                                        Selected chains are highlighted in blue; the first selected chain becomes the imported primary target.
                                                    </div>
                                                </div>
                                            )}
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
                                            selectedTarget={targetSource}
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
