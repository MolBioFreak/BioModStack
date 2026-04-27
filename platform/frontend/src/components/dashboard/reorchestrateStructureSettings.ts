import type { Job } from '../../lib/api.js';
import {
    BOLTZ_CP_DEFAULT_SHARD_PLAN_ID,
    buildBoltzCpSubmitParams,
    deriveBoltzCpGpuLaunchSettings,
    getBoltzQualityPresetValues,
    getBoltzCpLogicalSizeCp,
    getPredictorFamiliesForSelection,
    inferBoltzCpShardPlanId,
    normalizeBoltzCpShardPlanId,
    normalizeMsaTargetShardMinSizeGb,
    normalizeMsaTargetShardMode,
    normalizeMsaTargetShards,
    parseBoltzCpGpuIds,
    type BoltzCpShardPlanId,
    type StructureMsaTargetShardMode,
    type StructurePredictionMode,
    type StructurePredictorFamily,
} from '../structurePredictionUiState.js';

export type StructurePredictor = StructurePredictorFamily;
export type StructureMsaProvider = 'local' | 'colabfold_api';
export type StructureMsaPreset = 'maximum' | 'balanced' | 'fast';
export type StructureBoltzCpOutputFormat = 'mmcif' | 'pdb';

type StructureRetryJob = Pick<Job, 'model_id' | 'mode' | 'params'>;

export interface StructureReorchestrateSettings {
    predictors: StructurePredictor[];
    msaProvider: StructureMsaProvider;
    msaPreset: StructureMsaPreset;
    msaTargetShardMode: StructureMsaTargetShardMode;
    msaTargetShards: number;
    msaTargetShardMinSizeGb: number;
    skipMsa: boolean;
    msaAllowEmptyFallback: boolean;
    boltz: {
        useMsa: boolean;
        recyclingSteps: number;
        samplingSteps: number;
        numSamples: number;
        maxParallelSamples: number;
        usePotentials: boolean;
    };
    boltzCp: {
        enabled: boolean;
        pinnedGpus: number[];
        lockGpus: boolean;
        shardPlanId: BoltzCpShardPlanId;
        outputFormat: StructureBoltzCpOutputFormat;
        writeFullPae: boolean;
        seed: string;
    };
    rf3: {
        useMsa: boolean;
        numRecycles: number;
        numSamples: number;
    };
    protenix: {
        useMsa: boolean;
        modelWeights: string;
        seeds: string;
        nSample: number;
        nStep: number;
        nCycle: number;
    };
}

const DEFAULTS: StructureReorchestrateSettings = {
    predictors: ['boltz'],
    msaProvider: 'local',
    msaPreset: 'fast',
    msaTargetShardMode: 'auto',
    msaTargetShards: 4,
    msaTargetShardMinSizeGb: 1,
    skipMsa: false,
    msaAllowEmptyFallback: false,
    boltz: {
        useMsa: true,
        recyclingSteps: 3,
        samplingSteps: getBoltzQualityPresetValues('max').samplingSteps,
        numSamples: 1,
        maxParallelSamples: 1,
        usePotentials: false,
    },
    boltzCp: {
        enabled: false,
        pinnedGpus: [],
        lockGpus: false,
        shardPlanId: BOLTZ_CP_DEFAULT_SHARD_PLAN_ID,
        outputFormat: 'mmcif',
        writeFullPae: false,
        seed: '',
    },
    rf3: {
        useMsa: true,
        numRecycles: 10,
        numSamples: 1,
    },
    protenix: {
        useMsa: true,
        modelWeights: 'protenix_base_20250630_v1.0.0',
        seeds: '42',
        nSample: 5,
        nStep: 200,
        nCycle: 10,
    },
};

const PREDICTOR_ORDER: StructurePredictor[] = ['boltz', 'rf3', 'protenix'];

const toBoolean = (value: unknown, fallback: boolean): boolean => {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number') return value !== 0;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
        if (['false', '0', 'no', 'off'].includes(normalized)) return false;
    }
    return fallback;
};

const toInteger = (value: unknown, fallback: number, min = 1): number => {
    const parsed = Number.parseInt(String(value), 10);
    return Number.isFinite(parsed) ? Math.max(min, parsed) : fallback;
};

const normalizeMsaProvider = (value: unknown): StructureMsaProvider => (
    value === 'colabfold_api' ? 'colabfold_api' : 'local'
);

const normalizeMsaPreset = (value: unknown): StructureMsaPreset => {
    if (value === 'maximum' || value === 'balanced' || value === 'fast') return value;
    return 'fast';
};

const normalizeBoltzCpOutputFormat = (value: unknown): StructureBoltzCpOutputFormat => (
    String(value || '').trim().toLowerCase() === 'pdb' ? 'pdb' : 'mmcif'
);

const normalizeBoltzCpSeed = (value: unknown): string => {
    if (value === null || value === undefined) return '';
    const normalized = String(value).trim();
    return normalized;
};

const normalizeProtenixModel = (model?: string): string => {
    if (!model) return DEFAULTS.protenix.modelWeights;
    if (model === 'protenix_base_20241211_v0.2.1') return 'protenix_base_default_v1.0.0';
    if (model === 'protenix_esm_20241211_v0.2.1') return 'protenix_mini_esm_v0.5.0';
    return model;
};

const hasPredictorHints = (params: Record<string, unknown>, predictor: StructurePredictor): boolean => {
    if (predictor === 'boltz') {
        return [
            'boltz_use_msa',
            'boltz_recycling_steps',
            'boltz_sampling_steps',
            'boltz_num_samples',
            'boltz_max_parallel_samples',
            'boltz_use_potentials',
        ].some((key) => key in params);
    }
    if (predictor === 'rf3') {
        return ['rf3_use_msa', 'rf3_num_recycles', 'rf3_num_samples'].some((key) => key in params);
    }
    return [
        'protenix_use_msa',
        'protenix_model_weights',
        'protenix_seeds',
        'protenix_n_sample',
        'protenix_n_step',
        'protenix_n_cycle',
    ].some((key) => key in params);
};

const isBoltzCpLaunch = (job: StructureRetryJob): boolean => {
    const params = job.params || {};
    const modelId = String(job.model_id || '').trim().toLowerCase();
    const launchVariant = String(params.structure_launch_variant || '').trim().toLowerCase();
    return modelId === 'boltz_cp_experimental' || launchVariant === 'boltz_cp_experimental';
};

const resolveBoltzCpAutoFallbackGpuIds = (job: StructureRetryJob): string | null => {
    const params = job.params || {};
    const explicitPinned = parseBoltzCpGpuIds(params.pinned_gpus);
    if (explicitPinned.length > 0) {
        return null;
    }
    const rawFallback = String(params.gpu_ids ?? params.bcp_gpu_ids ?? '').trim();
    return rawFallback || null;
};

const sameValue = (left: unknown, right: unknown): boolean => {
    if (Array.isArray(left) && Array.isArray(right)) {
        return left.length === right.length && left.every((value, index) => value === right[index]);
    }
    return left === right;
};

const resolvePredictors = (job: StructureRetryJob): StructurePredictor[] => {
    const params = job.params || {};
    const explicit = String(params.pred_method || '').trim().toLowerCase();
    const predictionMode: StructurePredictionMode = String(job.mode || '').trim().toLowerCase() === 'complex' ? 'complex' : 'predict';
    if (explicit) {
        const explicitPredictors = getPredictorFamiliesForSelection(predictionMode, explicit);
        if (explicitPredictors.length > 0) {
            return PREDICTOR_ORDER.filter((predictor) => explicitPredictors.includes(predictor));
        }
        return [];
    }

    const validator = String(params.structure_validator || '').trim().toLowerCase();
    const modelId = String(job.model_id || '').trim().toLowerCase();
    const hinted = new Set<StructurePredictor>();

    if (validator.includes('boltz') || modelId.includes('boltz') || hasPredictorHints(params, 'boltz')) {
        hinted.add('boltz');
    }
    if (validator.includes('rf3') || modelId.includes('rf3') || hasPredictorHints(params, 'rf3')) {
        hinted.add('rf3');
    }
    if (validator.includes('protenix') || modelId.includes('protenix') || hasPredictorHints(params, 'protenix')) {
        hinted.add('protenix');
    }

    const hintedPredictors = PREDICTOR_ORDER.filter((predictor) => hinted.has(predictor));
    if (predictionMode === 'complex') {
        return hintedPredictors.filter((predictor) => predictor !== 'rf3');
    }
    return hintedPredictors;
};

export const isStructureReorchestrateJob = (job: StructureRetryJob): boolean => resolvePredictors(job).length > 0;

export const deriveStructureReorchestrateSettings = (job: StructureRetryJob): StructureReorchestrateSettings => {
    const params = job.params || {};
    const predictors = resolvePredictors(job);
    const boltzCpEnabled = isBoltzCpLaunch(job);
    const boltzCpPinnedGpus = boltzCpEnabled ? parseBoltzCpGpuIds(params.pinned_gpus) : [];

    const settings: StructureReorchestrateSettings = {
        predictors: predictors.length > 0 ? predictors : DEFAULTS.predictors,
        msaProvider: normalizeMsaProvider(params.msa_provider),
        msaPreset: normalizeMsaPreset(params.msa_preset),
        msaTargetShardMode: normalizeMsaTargetShardMode(params.msa_target_shard_mode),
        msaTargetShards: normalizeMsaTargetShards(params.msa_target_shards),
        msaTargetShardMinSizeGb: normalizeMsaTargetShardMinSizeGb(params.msa_target_shard_min_size_gb),
        skipMsa: false,
        msaAllowEmptyFallback: toBoolean(params.msa_allow_empty_fallback, DEFAULTS.msaAllowEmptyFallback),
        boltz: {
            useMsa: toBoolean(params.boltz_use_msa, DEFAULTS.boltz.useMsa),
            recyclingSteps: toInteger(params.boltz_recycling_steps, DEFAULTS.boltz.recyclingSteps),
            samplingSteps: toInteger(params.boltz_sampling_steps, DEFAULTS.boltz.samplingSteps),
            numSamples: toInteger(params.boltz_num_samples, DEFAULTS.boltz.numSamples),
            maxParallelSamples: toInteger(params.boltz_max_parallel_samples, DEFAULTS.boltz.maxParallelSamples),
            usePotentials: toBoolean(params.boltz_use_potentials, DEFAULTS.boltz.usePotentials),
        },
        boltzCp: {
            enabled: boltzCpEnabled,
            pinnedGpus: boltzCpPinnedGpus,
            lockGpus: boltzCpPinnedGpus.length > 0 && toBoolean(params.lock_gpus, DEFAULTS.boltzCp.lockGpus),
            shardPlanId: normalizeBoltzCpShardPlanId(params.bcp_shard_plan_id ?? params.shard_plan_id ?? inferBoltzCpShardPlanId(params.bcp_size_cp ?? params.size_cp)),
            outputFormat: normalizeBoltzCpOutputFormat(params.bcp_output_format ?? params.output_format),
            writeFullPae: toBoolean(params.bcp_write_full_pae ?? params.write_full_pae, DEFAULTS.boltzCp.writeFullPae),
            seed: normalizeBoltzCpSeed(params.bcp_seed ?? params.seed),
        },
        rf3: {
            useMsa: toBoolean(params.rf3_use_msa, DEFAULTS.rf3.useMsa),
            numRecycles: toInteger(params.rf3_num_recycles, DEFAULTS.rf3.numRecycles),
            numSamples: toInteger(params.rf3_num_samples, DEFAULTS.rf3.numSamples),
        },
        protenix: {
            useMsa: toBoolean(params.protenix_use_msa, DEFAULTS.protenix.useMsa),
            modelWeights: normalizeProtenixModel(typeof params.protenix_model_weights === 'string' ? params.protenix_model_weights : undefined),
            seeds: typeof params.protenix_seeds === 'string' && params.protenix_seeds.trim() ? params.protenix_seeds : DEFAULTS.protenix.seeds,
            nSample: toInteger(params.protenix_n_sample, DEFAULTS.protenix.nSample),
            nStep: toInteger(params.protenix_n_step, DEFAULTS.protenix.nStep),
            nCycle: toInteger(params.protenix_n_cycle, DEFAULTS.protenix.nCycle),
        },
    };

    const activeUseMsa = settings.predictors.map((predictor) => {
        if (predictor === 'boltz') return settings.boltz.useMsa;
        if (predictor === 'rf3') return settings.rf3.useMsa;
        return settings.protenix.useMsa;
    });
    settings.skipMsa = activeUseMsa.length > 0 && activeUseMsa.every((value) => value === false);

    return settings;
};

export const buildStructureReorchestrateOverrides = (
    job: StructureRetryJob,
    next: StructureReorchestrateSettings,
): Record<string, unknown> => {
    const previous = deriveStructureReorchestrateSettings(job);
    const overrides: Record<string, unknown> = {};

    const maybeSet = (key: string, value: unknown, prior: unknown) => {
        if (!sameValue(value, prior)) {
            overrides[key] = value;
        }
    };

    maybeSet('msa_provider', next.msaProvider, previous.msaProvider);
    maybeSet('msa_preset', next.msaPreset, previous.msaPreset);
    maybeSet('msa_target_shard_mode', next.msaTargetShardMode, previous.msaTargetShardMode);
    maybeSet('msa_target_shards', next.msaTargetShards, previous.msaTargetShards);
    maybeSet('msa_target_shard_min_size_gb', next.msaTargetShardMinSizeGb, previous.msaTargetShardMinSizeGb);
    maybeSet('msa_allow_empty_fallback', next.msaAllowEmptyFallback, previous.msaAllowEmptyFallback);

    if (next.predictors.includes('boltz')) {
        maybeSet('boltz_use_msa', next.skipMsa ? false : previous.boltz.useMsa, previous.boltz.useMsa);
        maybeSet('boltz_recycling_steps', next.boltz.recyclingSteps, previous.boltz.recyclingSteps);
        maybeSet('boltz_sampling_steps', next.boltz.samplingSteps, previous.boltz.samplingSteps);
        maybeSet('boltz_num_samples', next.boltz.numSamples, previous.boltz.numSamples);
        maybeSet('boltz_max_parallel_samples', next.boltz.maxParallelSamples, previous.boltz.maxParallelSamples);
        maybeSet('boltz_use_potentials', next.boltz.usePotentials, previous.boltz.usePotentials);
    }

    if (next.boltzCp.enabled) {
        const fallbackGpuIds = resolveBoltzCpAutoFallbackGpuIds(job);
        const previousLaunch = deriveBoltzCpGpuLaunchSettings({
            pinnedGpus: previous.boltzCp.pinnedGpus,
            requestedSizeCp: getBoltzCpLogicalSizeCp(previous.boltzCp.shardPlanId),
            fallbackGpuIds,
        });
        const nextLaunch = deriveBoltzCpGpuLaunchSettings({
            pinnedGpus: next.boltzCp.pinnedGpus,
            requestedSizeCp: getBoltzCpLogicalSizeCp(next.boltzCp.shardPlanId),
            fallbackGpuIds,
        });
        const previousParams = buildBoltzCpSubmitParams({
            shardPlanId: previous.boltzCp.shardPlanId,
            outputFormat: previous.boltzCp.outputFormat,
            writeFullPae: previous.boltzCp.writeFullPae,
            seed: previous.boltzCp.seed,
            gpuIds: previousLaunch.gpuIds,
        });
        const nextParams = buildBoltzCpSubmitParams({
            shardPlanId: next.boltzCp.shardPlanId,
            outputFormat: next.boltzCp.outputFormat,
            writeFullPae: next.boltzCp.writeFullPae,
            seed: next.boltzCp.seed,
            gpuIds: nextLaunch.gpuIds,
        });

        maybeSet(
            'pinned_gpus',
            next.boltzCp.pinnedGpus.length > 0 ? next.boltzCp.pinnedGpus : null,
            previous.boltzCp.pinnedGpus.length > 0 ? previous.boltzCp.pinnedGpus : null,
        );
        maybeSet(
            'lock_gpus',
            next.boltzCp.pinnedGpus.length > 0 ? next.boltzCp.lockGpus : false,
            previous.boltzCp.pinnedGpus.length > 0 ? previous.boltzCp.lockGpus : false,
        );
        maybeSet('bcp_shard_plan_id', nextParams.bcp_shard_plan_id, previousParams.bcp_shard_plan_id);
        maybeSet('bcp_gpu_ids', nextParams.bcp_gpu_ids ?? null, previousParams.bcp_gpu_ids ?? null);
        maybeSet('bcp_output_format', nextParams.bcp_output_format, previousParams.bcp_output_format);
        maybeSet('bcp_write_full_pae', nextParams.bcp_write_full_pae, previousParams.bcp_write_full_pae);
        maybeSet('bcp_seed', nextParams.bcp_seed ?? null, previousParams.bcp_seed ?? null);
    }

    if (next.predictors.includes('rf3')) {
        maybeSet('rf3_use_msa', next.skipMsa ? false : previous.rf3.useMsa, previous.rf3.useMsa);
        maybeSet('rf3_num_recycles', next.rf3.numRecycles, previous.rf3.numRecycles);
        maybeSet('rf3_num_samples', next.rf3.numSamples, previous.rf3.numSamples);
    }

    if (next.predictors.includes('protenix')) {
        maybeSet('protenix_use_msa', next.skipMsa ? false : previous.protenix.useMsa, previous.protenix.useMsa);
        maybeSet('protenix_model_weights', next.protenix.modelWeights, previous.protenix.modelWeights);
        maybeSet('protenix_seeds', next.protenix.seeds, previous.protenix.seeds);
        maybeSet('protenix_n_sample', next.protenix.nSample, previous.protenix.nSample);
        maybeSet('protenix_n_step', next.protenix.nStep, previous.protenix.nStep);
        maybeSet('protenix_n_cycle', next.protenix.nCycle, previous.protenix.nCycle);
    }

    return overrides;
};
