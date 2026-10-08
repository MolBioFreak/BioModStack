import type { Job } from '../../lib/api.js';

export type StructurePredictor = 'boltz' | 'rf3' | 'protenix';
export type StructureMsaProvider = 'local' | 'colabfold_api';
export type StructureMsaPreset = 'maximum' | 'balanced' | 'fast';

type StructureRetryJob = Pick<Job, 'model_id' | 'mode' | 'params'>;

export interface StructureReorchestrateSettings {
    predictors: StructurePredictor[];
    msaProvider: StructureMsaProvider;
    msaPreset: StructureMsaPreset;
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
    skipMsa: false,
    msaAllowEmptyFallback: false,
    boltz: {
        useMsa: true,
        recyclingSteps: 3,
        samplingSteps: 50,
        numSamples: 1,
        maxParallelSamples: 1,
        usePotentials: false,
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

const normalizeProtenixModel = (model?: string): string => {
    if (!model) return DEFAULTS.protenix.modelWeights;
    if (model === 'protenix_base_20241211_v0.2.1') return 'protenix_base_default_v1.0.0';
    if (model === 'protenix_esm_20241211_v0.2.1') return 'protenix_mini_esm_v0.5.0';
    return model;
};

const hasPredictorHints = (params: Record<string, any>, predictor: StructurePredictor): boolean => {
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

const resolvePredictors = (job: StructureRetryJob): StructurePredictor[] => {
    const params = job.params || {};
    const explicit = String(params.pred_method || '').trim().toLowerCase();
    switch (explicit) {
        case 'boltz':
            return ['boltz'];
        case 'rf3':
            return ['rf3'];
        case 'protenix':
            return ['protenix'];
        case 'both':
            return ['boltz', 'rf3'];
        case 'all':
            return ['boltz', 'rf3', 'protenix'];
        default:
            break;
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

    return PREDICTOR_ORDER.filter((predictor) => hinted.has(predictor));
};

export const isStructureReorchestrateJob = (job: StructureRetryJob): boolean => resolvePredictors(job).length > 0;

export const deriveStructureReorchestrateSettings = (job: StructureRetryJob): StructureReorchestrateSettings => {
    const params = job.params || {};
    const predictors = resolvePredictors(job);

    const settings: StructureReorchestrateSettings = {
        predictors: predictors.length > 0 ? predictors : DEFAULTS.predictors,
        msaProvider: normalizeMsaProvider(params.msa_provider),
        msaPreset: normalizeMsaPreset(params.msa_preset),
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
        if (value !== prior) {
            overrides[key] = value;
        }
    };

    maybeSet('msa_provider', next.msaProvider, previous.msaProvider);
    maybeSet('msa_preset', next.msaPreset, previous.msaPreset);
    maybeSet('msa_allow_empty_fallback', next.msaAllowEmptyFallback, previous.msaAllowEmptyFallback);

    if (next.predictors.includes('boltz')) {
        maybeSet('boltz_use_msa', next.skipMsa ? false : previous.boltz.useMsa, previous.boltz.useMsa);
        maybeSet('boltz_recycling_steps', next.boltz.recyclingSteps, previous.boltz.recyclingSteps);
        maybeSet('boltz_sampling_steps', next.boltz.samplingSteps, previous.boltz.samplingSteps);
        maybeSet('boltz_num_samples', next.boltz.numSamples, previous.boltz.numSamples);
        maybeSet('boltz_max_parallel_samples', next.boltz.maxParallelSamples, previous.boltz.maxParallelSamples);
        maybeSet('boltz_use_potentials', next.boltz.usePotentials, previous.boltz.usePotentials);
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
