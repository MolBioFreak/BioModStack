import {
    buildFrustraMpnnLaunchParams,
    type FrustraMpnnRequestedSettings,
} from './frustrampnn/frustraMpnnSettingsState.js';

export type StructurePredictionMode = 'predict' | 'complex';
export type StructurePredictorFamily = 'boltz' | 'rf3' | 'protenix' | 'esmfold2';
export type StructurePredictorSelection = StructurePredictorFamily | 'boltz_api' | 'both' | 'all' | 'boltz_protenix';
export type BoltzQualityPresetId = 'quick' | 'balanced' | 'max' | 'custom';
export type StructureLaunchVariant = 'default' | 'boltz_cp_experimental';
export type StructureMsaProvider = 'local' | 'colabfold_api';
export type StructureMsaPreset = 'maximum' | 'balanced' | 'fast';
export type StructureMsaTargetShardMode = 'auto' | 'required' | 'off';

export const BOLTZ_NUM_SAMPLES_HELP_TEXT = 'Generates this many final ranked candidate structures from the same input. model_0 is the top-ranked candidate; additional samples are alternative candidates.';
export const BOLTZ_MAX_PARALLEL_SAMPLES_HELP_TEXT = 'Caps the denoiser-forward chunk size across already-requested Boltz diffusion/FK particles. Lower can reduce neural-network VRAM spikes, but this is not a full one-at-a-time job guarantee.';

export interface StructurePredictorOption {
    id: StructurePredictorSelection;
    name: string;
    desc: string;
    color: 'blue' | 'green' | 'violet' | 'purple' | 'amber';
    disabled?: boolean;
    disabledReason?: string;
}

export interface ResolvedStructurePredictorSelection {
    requestedSelection: StructurePredictorSelection;
    canonicalSelection: StructurePredictorSelection;
    families: StructurePredictorFamily[];
    valid: boolean;
    error?: string;
}

export interface BoltzQualitySliderState {
    presetId: BoltzQualityPresetId;
    sliderValue: number;
    sliderMax: number;
    label: string;
    samplingSteps: number;
}

export interface TargetPreviewSelection {
    chain_id: string;
    color: { r: number; g: number; b: number };
    focus: boolean;
}

export interface TargetPreviewSourceInput {
    previewUrl?: string | null;
    stagedPath?: string | null;
    targetSource?: {
        name?: string | null;
        url?: string | null;
        path?: string | null;
    } | null;
}

export interface StructureLaunchConfig {
    variant: StructureLaunchVariant;
    submitModelId: 'boltz2' | 'boltz_cp_experimental';
    submitMode: 'predict' | 'design';
    allowPredictorSelection: boolean;
    showParallelJobs: boolean;
    showSequenceBatch: boolean;
    showMsaControls: boolean;
    forcedPredictor: StructurePredictorFamily | null;
}

export interface BoltzCpGpuLaunchInput {
    pinnedGpus: number[];
    requestedSizeCp?: number | null;
    fallbackGpuIds?: string | null;
}

export interface StructureSubmitTarget {
    modelId: 'boltz2' | 'boltz_api' | 'rf3' | 'protenix' | 'esmfold2' | 'boltz_cp_experimental';
    mode: 'predict' | 'complex' | 'design';
}

export interface ResolveStructureSubmitTargetInput {
    launchConfig: StructureLaunchConfig;
    predictionMode: StructurePredictionMode;
    predictorSelection: StructurePredictorSelection | string | null | undefined;
}

export interface BoltzCpSubmitParamsInput {
    outputFormat: 'mmcif' | 'pdb';
    writeFullPae: boolean;
    seed?: string | null;
    gpuIds?: string | null;
    sizeCp: number;
}

export interface StructureMsaSubmitParamsInput {
    provider: StructureMsaProvider;
    preset: StructureMsaPreset;
    targetShardMode?: StructureMsaTargetShardMode | string | null;
    targetShards?: number | string | null;
    targetShardMinSizeGb?: number | string | null;
}

export interface BoltzApiStructureRequestInput {
    name: string;
    clientRequestId: string;
    sequence: string;
    primaryChainId: string;
    complexComponents: Array<Record<string, unknown>>;
    numSamples: number;
    useMsa: boolean;
    // This field is deliberately not serialized. It keeps the payload boundary
    // explicit when a caller has local-only controls in scope.
    localControls?: {
        pinnedGpus?: number[];
        diffusionSamplingSteps?: number;
        recyclingSteps?: number;
        usePotentials?: boolean;
        denoiserChunkLimit?: number;
    };
}

const COMPLEX_RF3_DISABLED_REASON = 'RF3 is predict-only and cannot be launched in complex mode.';
const TARGET_PREVIEW_HIGHLIGHT = { r: 59, g: 130, b: 246 };
type StructureInitialValues = Record<string, unknown>;
type BoltzCpSubmitParams = Record<string, string | number | boolean>;
type StructureMsaSubmitParams = Record<string, string | number | boolean>;

export const DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MODE: StructureMsaTargetShardMode = 'auto';
export const DEFAULT_STRUCTURE_MSA_TARGET_SHARDS = 4;
export const DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MIN_SIZE_GB = 1;
export const DEFAULT_STRUCTURE_MSA_PROVIDER: StructureMsaProvider = 'colabfold_api';

const toBoltzApiNativeComponents = (components: Array<Record<string, unknown>>) => components.map((component) => {
    const type = String(component.type || '').toLowerCase();
    const providerType = type === 'peptide' ? 'protein'
        : type === 'ion' || (type === 'ligand' && component.ccd) ? 'ligand_ccd'
        : type === 'ligand' ? 'ligand_smiles'
        : type;
    const value = providerType === 'ligand_ccd'
        ? component.ccd
        : providerType === 'ligand_smiles'
            ? component.smiles
            : component.sequence;
    const rawChainIds = component.chain_ids ?? component.id;
    const chainIds = Array.isArray(rawChainIds) ? rawChainIds : [rawChainIds];
    return { type: providerType, value, chain_ids: chainIds };
});

/** Build the narrow provider request; local runtime controls never cross this boundary. */
export const buildBoltzApiStructureRequest = ({
    name,
    clientRequestId,
    sequence,
    primaryChainId,
    complexComponents,
    numSamples,
    useMsa,
}: BoltzApiStructureRequestInput) => ({
    name,
    client_request_id: clientRequestId,
    model: 'boltz-2.1' as const,
    sequence,
    primary_chain_id: primaryChainId,
    complex_components: toBoltzApiNativeComponents(complexComponents),
    num_samples: Math.max(1, Math.min(10, Math.floor(Number(numSamples) || 1))),
    use_msa: useMsa,
});

export const normalizeMsaTargetShardMode = (value: unknown): StructureMsaTargetShardMode => {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'required' || normalized === 'off') {
        return normalized;
    }
    return DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MODE;
};

export const normalizeMsaTargetShards = (value: unknown): number => {
    const parsed = Number.parseInt(String(value), 10);
    return Number.isFinite(parsed) ? Math.max(1, parsed) : DEFAULT_STRUCTURE_MSA_TARGET_SHARDS;
};

export const normalizeMsaTargetShardMinSizeGb = (value: unknown): number => {
    const parsed = Number.parseFloat(String(value));
    return Number.isFinite(parsed) ? Math.max(0, parsed) : DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MIN_SIZE_GB;
};

const toStructureLaunchVariant = (initialValues?: StructureInitialValues | null): StructureLaunchVariant => {
    const normalized = String(
        initialValues?.structure_launch_variant
        || initialValues?.template_model_id
        || initialValues?.model_id
        || ''
    ).trim().toLowerCase();
    if (normalized === 'boltz_cp_experimental') return 'boltz_cp_experimental';
    return 'default';
};

export const parseBoltzCpGpuIds = (value: unknown): number[] => {
    const rawValues = Array.isArray(value)
        ? value
        : String(value || '')
            .split(',')
            .map((token) => token.trim())
            .filter(Boolean);

    const seen = new Set<number>();
    const parsed: number[] = [];
    for (const rawValue of rawValues) {
        const gpuId = Number.parseInt(String(rawValue), 10);
        if (!Number.isFinite(gpuId) || gpuId < 0 || seen.has(gpuId)) {
            continue;
        }
        seen.add(gpuId);
        parsed.push(gpuId);
    }
    return parsed;
};

const getLargestSquareDivisor = (gpuCount: number, requestedSizeCp?: number | null): number => {
    if (!Number.isFinite(gpuCount) || gpuCount < 1) {
        return 1;
    }

    const preferred = Number.parseInt(String(requestedSizeCp ?? gpuCount), 10);
    const requested = Number.isFinite(preferred) && preferred > 0 ? preferred : gpuCount;

    let best = 1;
    for (let candidate = 1; candidate <= gpuCount; candidate += 1) {
        if (gpuCount % candidate !== 0) {
            continue;
        }
        const root = Math.sqrt(candidate);
        if (!Number.isInteger(root) || candidate > requested) {
            continue;
        }
        best = candidate;
    }
    return best;
};

export const resolveStructureLaunchConfig = (initialValues?: StructureInitialValues | null): StructureLaunchConfig => {
    const variant = toStructureLaunchVariant(initialValues);
    if (variant === 'boltz_cp_experimental') {
        return {
            variant,
            submitModelId: 'boltz_cp_experimental',
            submitMode: 'design',
            allowPredictorSelection: false,
            showParallelJobs: false,
            showSequenceBatch: false,
            showMsaControls: true,
            forcedPredictor: 'boltz',
        };
    }


    return {
        variant: 'default',
        submitModelId: 'boltz2',
        submitMode: 'predict',
        allowPredictorSelection: true,
        showParallelJobs: true,
        showSequenceBatch: true,
        showMsaControls: true,
        forcedPredictor: null,
    };
};

export const deriveBoltzCpGpuLaunchSettings = ({
    pinnedGpus,
    requestedSizeCp,
    fallbackGpuIds,
}: BoltzCpGpuLaunchInput): { gpuIds: string; sizeCp: number } => {
    const resolvedGpuIds = parseBoltzCpGpuIds(
        Array.isArray(pinnedGpus) && pinnedGpus.length > 0 ? pinnedGpus : fallbackGpuIds
    );
    return {
        gpuIds: resolvedGpuIds.join(','),
        sizeCp: getLargestSquareDivisor(resolvedGpuIds.length, requestedSizeCp),
    };
};

export const resolveStructureSubmitTarget = ({
    launchConfig,
    predictionMode,
    predictorSelection,
}: ResolveStructureSubmitTargetInput): StructureSubmitTarget => {
    if (launchConfig.variant !== 'default') {
        return {
            modelId: launchConfig.submitModelId,
            mode: launchConfig.submitMode,
        };
    }

    const resolvedSelection = resolveStructurePredictorSelection(predictionMode, predictorSelection);
    return {
        modelId: resolvedSelection.canonicalSelection === 'boltz_api'
            ? 'boltz_api'
            : resolvedSelection.canonicalSelection === 'rf3'
                ? 'rf3'
                : resolvedSelection.canonicalSelection === 'protenix'
                    ? 'protenix'
                    : resolvedSelection.canonicalSelection === 'esmfold2'
                        ? 'esmfold2'
                        : 'boltz2',
        mode: predictionMode,
    };
};

export const buildBoltzCpSubmitParams = ({
    outputFormat,
    writeFullPae,
    seed,
    gpuIds,
    sizeCp,
}: BoltzCpSubmitParamsInput): BoltzCpSubmitParams => {
    const params: BoltzCpSubmitParams = {
        structure_launch_variant: 'boltz_cp_experimental',
        num_parallel_jobs: 1,
        bcp_input_format: 'config_files',
        bcp_output_format: outputFormat,
        bcp_write_full_pae: writeFullPae,
        bcp_size_cp: sizeCp,
    };
    if (gpuIds && gpuIds.trim()) {
        params.bcp_gpu_ids = gpuIds.trim();
    }
    const normalizedSeed = String(seed || '').trim();
    if (normalizedSeed) {
        const parsedSeed = Number.parseInt(normalizedSeed, 10);
        params.bcp_seed = Number.isFinite(parsedSeed) ? parsedSeed : normalizedSeed;
    }
    return params;
};

export type StructureFrustraMpnnSubmitParams =
    | {
        run_frustrampnn: true;
        frustrampnn_requiredness: 'required';
        frustrampnn_settings: FrustraMpnnRequestedSettings;
    }
    | {
        run_frustrampnn: false;
        frustrampnn_requiredness: 'required';
    };

export const buildStructureFrustraMpnnSubmitParams = (
    enabled: boolean | undefined,
    settings: FrustraMpnnRequestedSettings,
): StructureFrustraMpnnSubmitParams => ({
    ...buildFrustraMpnnLaunchParams(enabled !== false, settings),
    frustrampnn_requiredness: 'required',
});

export const buildStructureMsaSubmitParams = ({
    provider,
    preset,
    targetShardMode,
    targetShards,
    targetShardMinSizeGb,
}: StructureMsaSubmitParamsInput): StructureMsaSubmitParams => {
    const normalizedProvider = provider === 'colabfold_api' ? 'colabfold_api' : 'local';
    const params: StructureMsaSubmitParams = {
        msa_provider: normalizedProvider,
        msa_preset: preset === 'maximum' || preset === 'balanced' ? preset : 'fast',
    };
    if (normalizedProvider === 'local') {
        params.msa_target_shard_mode = normalizeMsaTargetShardMode(targetShardMode);
        params.msa_target_shards = normalizeMsaTargetShards(targetShards);
        params.msa_target_shard_min_size_gb = normalizeMsaTargetShardMinSizeGb(targetShardMinSizeGb);
    }
    return params;
};

export const BOLTZ_QUALITY_PRESETS = [
    { id: 'quick' as const, label: 'Quick', samplingSteps: 50 },
    { id: 'balanced' as const, label: 'Balanced', samplingSteps: 100 },
    { id: 'max' as const, label: 'High', samplingSteps: 200 },
];

const PREDICT_MODE_OPTIONS: StructurePredictorOption[] = [
    { id: 'boltz', name: 'Boltz-2', desc: 'Fast, SOTA accuracy', color: 'blue' },
    { id: 'boltz_api', name: 'Boltz API', desc: 'Remote Boltz-2.1 queue', color: 'blue' },
    { id: 'rf3', name: 'RoseTTAFold3', desc: 'Open-source AF3 alt.', color: 'green' },
    { id: 'protenix', name: 'Protenix', desc: 'AF3-level, multi-modal', color: 'violet' },
    { id: 'esmfold2', name: 'ESMFold2', desc: 'Fast local all-atom folding', color: 'blue' },
    { id: 'both', name: 'Boltz + RF3', desc: 'Ensemble (2)', color: 'purple' },
    { id: 'all', name: 'All Three', desc: 'Full ensemble', color: 'amber' },
];

const COMPLEX_MODE_OPTIONS: StructurePredictorOption[] = [
    { id: 'boltz', name: 'Boltz-2', desc: 'Complex prediction with target conditioning', color: 'blue' },
    { id: 'boltz_api', name: 'Boltz API', desc: 'Remote Boltz-2.1 complex prediction', color: 'blue' },
    { id: 'rf3', name: 'RoseTTAFold3', desc: 'Predict-only; unavailable for complexes', color: 'green', disabled: true, disabledReason: COMPLEX_RF3_DISABLED_REASON },
    { id: 'protenix', name: 'Protenix', desc: 'Template-guided complex prediction', color: 'violet' },
    { id: 'esmfold2', name: 'ESMFold2', desc: 'Fast MSA-free complex co-folding', color: 'blue' },
    { id: 'boltz_protenix', name: 'Boltz + Protenix', desc: 'Truthful complex ensemble', color: 'amber' },
];

const toPredictorSelection = (value: string | null | undefined): StructurePredictorSelection => {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'boltz_api' || normalized === 'rf3' || normalized === 'protenix' || normalized === 'esmfold2' || normalized === 'both' || normalized === 'all' || normalized === 'boltz_protenix') {
        return normalized;
    }
    return 'boltz';
};

export const getStructurePredictorOptions = (mode: StructurePredictionMode): StructurePredictorOption[] => (
    mode === 'complex' ? COMPLEX_MODE_OPTIONS : PREDICT_MODE_OPTIONS
);

export const resolveStructurePredictorSelection = (
    mode: StructurePredictionMode,
    selection: StructurePredictorSelection | string | null | undefined,
): ResolvedStructurePredictorSelection => {
    const requestedSelection = toPredictorSelection(selection);

    if (mode === 'complex') {
        if (requestedSelection === 'boltz_api') {
            return {
                requestedSelection,
                canonicalSelection: 'boltz_api',
                families: ['boltz'],
                valid: true,
            };
        }
        if (requestedSelection === 'rf3') {
            return {
                requestedSelection,
                canonicalSelection: requestedSelection,
                families: [],
                valid: false,
                error: COMPLEX_RF3_DISABLED_REASON,
            };
        }
        if (requestedSelection === 'esmfold2') {
            return {
                requestedSelection,
                canonicalSelection: 'esmfold2',
                families: ['esmfold2'],
                valid: true,
            };
        }
        if (requestedSelection === 'both' || requestedSelection === 'all' || requestedSelection === 'boltz_protenix') {
            return {
                requestedSelection,
                canonicalSelection: 'boltz_protenix',
                families: ['boltz', 'protenix'],
                valid: true,
            };
        }
        if (requestedSelection === 'protenix') {
            return {
                requestedSelection,
                canonicalSelection: 'protenix',
                families: ['protenix'],
                valid: true,
            };
        }
        return {
            requestedSelection,
            canonicalSelection: 'boltz',
            families: ['boltz'],
            valid: true,
        };
    }

    if (requestedSelection === 'boltz_api') {
        return {
            requestedSelection,
            canonicalSelection: 'boltz_api',
            families: ['boltz'],
            valid: true,
        };
    }
    if (requestedSelection === 'both') {
        return {
            requestedSelection,
            canonicalSelection: 'both',
            families: ['boltz', 'rf3'],
            valid: true,
        };
    }
    if (requestedSelection === 'all') {
        return {
            requestedSelection,
            canonicalSelection: 'all',
            families: ['boltz', 'rf3', 'protenix'],
            valid: true,
        };
    }
    if (requestedSelection === 'protenix') {
        return {
            requestedSelection,
            canonicalSelection: 'protenix',
            families: ['protenix'],
            valid: true,
        };
    }
    if (requestedSelection === 'rf3') {
        return {
            requestedSelection,
            canonicalSelection: 'rf3',
            families: ['rf3'],
            valid: true,
        };
    }
    if (requestedSelection === 'esmfold2') {
        return {
            requestedSelection,
            canonicalSelection: 'esmfold2',
            families: ['esmfold2'],
            valid: true,
        };
    }
    return {
        requestedSelection,
        canonicalSelection: 'boltz',
        families: ['boltz'],
        valid: true,
    };
};

export const getPredictorFamiliesForSelection = (
    mode: StructurePredictionMode,
    selection: StructurePredictorSelection | string | null | undefined,
): StructurePredictorFamily[] => {
    const resolved = resolveStructurePredictorSelection(mode, selection);
    return resolved.families;
};

export const getBoltzQualityPresetValues = (presetId: Exclude<BoltzQualityPresetId, 'custom'>): { samplingSteps: number } => {
    const preset = BOLTZ_QUALITY_PRESETS.find((entry) => entry.id === presetId) || BOLTZ_QUALITY_PRESETS[0];
    return { samplingSteps: preset.samplingSteps };
};

export const getBoltzQualitySliderState = ({ samplingSteps }: { samplingSteps: number; recyclingSteps?: number }): BoltzQualitySliderState => {
    const matchedPreset = BOLTZ_QUALITY_PRESETS.find((preset) => preset.samplingSteps === samplingSteps);
    if (matchedPreset) {
        const sliderValue = BOLTZ_QUALITY_PRESETS.findIndex((preset) => preset.id === matchedPreset.id);
        return {
            presetId: matchedPreset.id,
            sliderValue,
            sliderMax: BOLTZ_QUALITY_PRESETS.length - 1,
            label: matchedPreset.label,
            samplingSteps: matchedPreset.samplingSteps,
        };
    }

    return {
        presetId: 'custom',
        sliderValue: BOLTZ_QUALITY_PRESETS.length,
        sliderMax: BOLTZ_QUALITY_PRESETS.length,
        label: 'Custom legacy',
        samplingSteps,
    };
};

export const resolveBoltzSamplingStepsFromSlider = ({
    currentSamplingSteps,
    sliderValue,
}: {
    currentSamplingSteps: number;
    sliderValue: number;
}): number => {
    const currentState = getBoltzQualitySliderState({ samplingSteps: currentSamplingSteps });
    if (currentState.presetId === 'custom' && sliderValue === currentState.sliderValue) {
        return currentSamplingSteps;
    }
    const clampedSliderValue = Math.max(0, Math.min(BOLTZ_QUALITY_PRESETS.length - 1, sliderValue));
    return BOLTZ_QUALITY_PRESETS[clampedSliderValue]?.samplingSteps ?? BOLTZ_QUALITY_PRESETS[BOLTZ_QUALITY_PRESETS.length - 1].samplingSteps;
};

export const inferTargetStructureFormat = (value: string | null | undefined): 'pdb' | 'cif' => {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized.endsWith('.cif') || normalized.endsWith('.mmcif')) {
        return 'cif';
    }
    return 'pdb';
};

export const resolveTargetPreviewSource = ({ previewUrl, stagedPath, targetSource }: TargetPreviewSourceInput): { structureUrl: string | null; format: 'pdb' | 'cif' } => {
    const structureUrl = previewUrl || targetSource?.url || stagedPath || targetSource?.path || null;
    const formatHint = stagedPath || targetSource?.name || targetSource?.url || targetSource?.path || previewUrl || null;
    return {
        structureUrl,
        format: inferTargetStructureFormat(formatHint),
    };
};

export const buildTargetPreviewSelections = (
    chainIds: Array<string | null | undefined>,
): TargetPreviewSelection[] => {
    const seen = new Set<string>();
    const normalizedChainIds = chainIds
        .map((value) => String(value || '').trim())
        .filter((value) => {
            if (!value || seen.has(value)) {
                return false;
            }
            seen.add(value);
            return true;
        });

    return normalizedChainIds.map((chainId, index) => ({
        chain_id: chainId,
        color: TARGET_PREVIEW_HIGHLIGHT,
        focus: index === 0,
    }));
};

export const buildTargetPreviewSelection = (primaryChainId: string | null | undefined): TargetPreviewSelection[] => (
    buildTargetPreviewSelections([primaryChainId])
);
