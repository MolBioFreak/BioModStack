export type StructurePredictionMode = 'predict' | 'complex';
export type StructurePredictorFamily = 'boltz' | 'rf3' | 'protenix';
export type StructurePredictorSelection = StructurePredictorFamily | 'both' | 'all' | 'boltz_protenix';
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

export type BoltzCpShardPlanId = '1x1' | '2x2' | '4x4';

export interface BoltzCpShardPlanDefinition {
    id: BoltzCpShardPlanId;
    label: string;
    logicalSizeCp: number;
    description: string;
}

export interface StructureSubmitTarget {
    modelId: 'boltz2' | 'rf3' | 'protenix' | 'boltz_cp_experimental';
    mode: 'predict' | 'complex' | 'design';
}

export interface ResolveStructureSubmitTargetInput {
    launchConfig: StructureLaunchConfig;
    predictionMode: StructurePredictionMode;
    predictorSelection: StructurePredictorSelection | string | null | undefined;
}

export interface BoltzCpSubmitParamsInput {
    shardPlanId: BoltzCpShardPlanId;
    outputFormat: 'mmcif' | 'pdb';
    writeFullPae: boolean;
    seed?: string | null;
    gpuIds?: string | null;
}

export interface StructureMsaSubmitParamsInput {
    provider: StructureMsaProvider;
    preset: StructureMsaPreset;
    targetShardMode?: StructureMsaTargetShardMode | string | null;
    targetShards?: number | string | null;
    targetShardMinSizeGb?: number | string | null;
}

const COMPLEX_RF3_DISABLED_REASON = 'RF3 is predict-only and cannot be launched in complex mode.';
const TARGET_PREVIEW_HIGHLIGHT = { r: 59, g: 130, b: 246 };
type StructureInitialValues = Record<string, unknown>;
type BoltzCpSubmitParams = Record<string, string | number | boolean>;
type StructureMsaSubmitParams = Record<string, string | number | boolean>;

export const DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MODE: StructureMsaTargetShardMode = 'auto';
export const DEFAULT_STRUCTURE_MSA_TARGET_SHARDS = 4;
export const DEFAULT_STRUCTURE_MSA_TARGET_SHARD_MIN_SIZE_GB = 1;

export const BOLTZ_CP_DEFAULT_SHARD_PLAN_ID: BoltzCpShardPlanId = '2x2';
const BOLTZ_CP_LOGICAL_SIZE_CP_BY_ID: Record<BoltzCpShardPlanId, number> = {
    '1x1': 1,
    '2x2': 4,
    '4x4': 16,
};

export const BOLTZ_CP_SHARD_PLAN_DEFINITIONS: BoltzCpShardPlanDefinition[] = [
    {
        id: '1x1',
        label: '1×1 (single logical shard)',
        logicalSizeCp: 1,
        description: 'No logical sharding; useful for fallback/debug runs.',
    },
    {
        id: '2x2',
        label: '2×2 (4 logical shards)',
        logicalSizeCp: 4,
        description: 'Defines a 2×2 logical tile mesh. The selected logical plan does not change with GPU count.',
    },
    {
        id: '4x4',
        label: '4×4 (16 logical shards)',
        logicalSizeCp: 16,
        description: 'Defines a 4×4 logical tile mesh. The selected logical plan does not change with GPU count.',
    },
];

export const normalizeBoltzCpShardPlanId = (value: unknown): BoltzCpShardPlanId => {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === '1x1' || normalized === '2x2' || normalized === '4x4') {
        return normalized;
    }
    return BOLTZ_CP_DEFAULT_SHARD_PLAN_ID;
};

export const getBoltzCpLogicalSizeCp = (shardPlanId: unknown): number => (
    BOLTZ_CP_LOGICAL_SIZE_CP_BY_ID[normalizeBoltzCpShardPlanId(shardPlanId)]
);

export const inferBoltzCpShardPlanId = (sizeCp: unknown): BoltzCpShardPlanId => {
    const parsed = Number.parseInt(String(sizeCp), 10);
    if (parsed === 1) return '1x1';
    if (parsed === 16) return '4x4';
    if (parsed === 4) return '2x2';
    return BOLTZ_CP_DEFAULT_SHARD_PLAN_ID;
};

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
    return normalized === 'boltz_cp_experimental' ? 'boltz_cp_experimental' : 'default';
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

export const getBoltzCpRuntimeBridgeSummary = ({
    shardPlanId,
    gpuIds,
    sizeCp,
    autoFallbackLabel = 'auto-selected GPU pool',
}: {
    shardPlanId: unknown;
    gpuIds?: string | null;
    sizeCp: number;
    autoFallbackLabel?: string;
}): string => {
    const normalizedPlanId = normalizeBoltzCpShardPlanId(shardPlanId);
    const logicalSizeCp = getBoltzCpLogicalSizeCp(normalizedPlanId);
    const resolvedGpuLabel = String(gpuIds || '').trim() || autoFallbackLabel;
    const logicalShardLabel = logicalSizeCp === 1 ? 'logical shard' : 'logical shards';
    const physicalRankLabel = sizeCp === 1 ? 'CP rank' : 'CP ranks';
    return `The selected logical plan stays ${normalizedPlanId} (${logicalSizeCp} ${logicalShardLabel}); GPU count only affects the current runtime bridge. ${resolvedGpuLabel} → current physical launch = ${sizeCp} ${physicalRankLabel}.`;
};

export const resolveStructureSubmitTarget = ({
    launchConfig,
    predictionMode,
    predictorSelection,
}: ResolveStructureSubmitTargetInput): StructureSubmitTarget => {
    if (launchConfig.variant === 'boltz_cp_experimental') {
        return {
            modelId: launchConfig.submitModelId,
            mode: launchConfig.submitMode,
        };
    }

    const resolvedSelection = resolveStructurePredictorSelection(predictionMode, predictorSelection);
    return {
        modelId: resolvedSelection.canonicalSelection === 'rf3'
            ? 'rf3'
            : resolvedSelection.canonicalSelection === 'protenix'
                ? 'protenix'
                : 'boltz2',
        mode: predictionMode,
    };
};

export const buildBoltzCpSubmitParams = ({
    shardPlanId,
    outputFormat,
    writeFullPae,
    seed,
    gpuIds,
}: BoltzCpSubmitParamsInput): BoltzCpSubmitParams => {
    const params: BoltzCpSubmitParams = {
        structure_launch_variant: 'boltz_cp_experimental',
        num_parallel_jobs: 1,
        bcp_input_format: 'config_files',
        bcp_shard_plan_id: normalizeBoltzCpShardPlanId(shardPlanId),
        bcp_output_format: outputFormat,
        bcp_write_full_pae: writeFullPae,
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
    { id: 'rf3', name: 'RoseTTAFold3', desc: 'Open-source AF3 alt.', color: 'green' },
    { id: 'protenix', name: 'Protenix', desc: 'AF3-level, multi-modal', color: 'violet' },
    { id: 'both', name: 'Boltz + RF3', desc: 'Ensemble (2)', color: 'purple' },
    { id: 'all', name: 'All Three', desc: 'Full ensemble', color: 'amber' },
];

const COMPLEX_MODE_OPTIONS: StructurePredictorOption[] = [
    { id: 'boltz', name: 'Boltz-2', desc: 'Complex prediction with target conditioning', color: 'blue' },
    { id: 'rf3', name: 'RoseTTAFold3', desc: 'Predict-only; unavailable for complexes', color: 'green', disabled: true, disabledReason: COMPLEX_RF3_DISABLED_REASON },
    { id: 'protenix', name: 'Protenix', desc: 'Template-guided complex prediction', color: 'violet' },
    { id: 'boltz_protenix', name: 'Boltz + Protenix', desc: 'Truthful complex ensemble', color: 'amber' },
];

const toPredictorSelection = (value: string | null | undefined): StructurePredictorSelection => {
    const normalized = String(value || '').trim().toLowerCase();
    if (normalized === 'rf3' || normalized === 'protenix' || normalized === 'both' || normalized === 'all' || normalized === 'boltz_protenix') {
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
        if (requestedSelection === 'rf3') {
            return {
                requestedSelection,
                canonicalSelection: requestedSelection,
                families: [],
                valid: false,
                error: COMPLEX_RF3_DISABLED_REASON,
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
