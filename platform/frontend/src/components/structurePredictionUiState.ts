export type StructurePredictionMode = 'predict' | 'complex';
export type StructurePredictorFamily = 'boltz' | 'rf3' | 'protenix';
export type StructurePredictorSelection = StructurePredictorFamily | 'both' | 'all' | 'boltz_protenix';
export type BoltzQualityPresetId = 'quick' | 'balanced' | 'max' | 'custom';

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

const COMPLEX_RF3_DISABLED_REASON = 'RF3 is predict-only and cannot be launched in complex mode.';
const TARGET_PREVIEW_HIGHLIGHT = { r: 59, g: 130, b: 246 };

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
