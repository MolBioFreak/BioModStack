import { useQuery } from '@tanstack/react-query';

export const DEFAULT_BMS_FEATURES = {
    bioxp: false,
    stats_tools: true,
    assay_db: true,
} as const;

export const DEFAULT_BMS_DEV_FEATURES = {
    bioxp: true,
    stats_tools: true,
    assay_db: true,
} as const;

export type BmsFeatureKey = keyof typeof DEFAULT_BMS_FEATURES;
export type BmsFeatures = Record<BmsFeatureKey, boolean>;

export interface BmsFeatureState {
    features: BmsFeatures;
    devFeatures: BmsFeatures;
}

interface BmsFeaturesEnvelope {
    features?: Partial<Record<BmsFeatureKey, unknown>> | null;
    dev_features?: Partial<Record<BmsFeatureKey, unknown>> | null;
}

function coerceFeatureBool(value: unknown, fallback: boolean): boolean {
    if (typeof value === 'boolean') {
        return value;
    }
    if (typeof value === 'number') {
        return value !== 0;
    }
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['1', 'true', 'yes', 'on'].includes(normalized)) {
            return true;
        }
        if (['0', 'false', 'no', 'off'].includes(normalized)) {
            return false;
        }
    }
    return fallback;
}

export function normalizeBmsFeatures(payload: unknown): BmsFeatures {
    const defaults: BmsFeatures = { ...DEFAULT_BMS_FEATURES };
    const envelope = payload && typeof payload === 'object' ? payload as BmsFeaturesEnvelope : null;
    const rawFeatures = envelope?.features;
    if (!rawFeatures || typeof rawFeatures !== 'object') {
        return defaults;
    }

    return {
        bioxp: coerceFeatureBool(rawFeatures.bioxp, defaults.bioxp),
        stats_tools: coerceFeatureBool(rawFeatures.stats_tools, defaults.stats_tools),
        assay_db: coerceFeatureBool(rawFeatures.assay_db, defaults.assay_db),
    };
}

function normalizeBmsDevFeatures(payload: unknown): BmsFeatures {
    const defaults: BmsFeatures = { ...DEFAULT_BMS_DEV_FEATURES };
    const envelope = payload && typeof payload === 'object' ? payload as BmsFeaturesEnvelope : null;
    const rawFeatures = envelope?.dev_features;
    if (!rawFeatures || typeof rawFeatures !== 'object') {
        return defaults;
    }

    return {
        bioxp: coerceFeatureBool(rawFeatures.bioxp, defaults.bioxp),
        stats_tools: coerceFeatureBool(rawFeatures.stats_tools, defaults.stats_tools),
        assay_db: coerceFeatureBool(rawFeatures.assay_db, defaults.assay_db),
    };
}

export function normalizeBmsFeatureState(payload: unknown): BmsFeatureState {
    return {
        features: normalizeBmsFeatures(payload),
        devFeatures: normalizeBmsDevFeatures(payload),
    };
}

export function isBmsFeatureEnabled(features: BmsFeatures, feature: BmsFeatureKey): boolean {
    return features[feature];
}

export function isBmsFeatureVisible(state: BmsFeatureState, feature: BmsFeatureKey, showDevFeatures: boolean): boolean {
    return state.features[feature] && (showDevFeatures || !state.devFeatures[feature]);
}

async function fetchBmsFeatureState(): Promise<BmsFeatureState> {
    const response = await fetch('/api/system/features', { cache: 'no-store' });
    if (!response.ok) {
        throw new Error(`install features unavailable (${response.status})`);
    }
    const payload = await response.json().catch(() => null) as unknown;
    return normalizeBmsFeatureState(payload);
}

export async function setBmsFeature(feature: BmsFeatureKey, enabled: boolean): Promise<BmsFeatures> {
    const response = await fetch('/api/system/features', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ features: { [feature]: enabled } }),
    });
    if (!response.ok) {
        const body = await response.json().catch(() => null) as { detail?: unknown } | null;
        throw new Error(String(body?.detail || `feature update failed (${response.status})`));
    }
    const payload = await response.json().catch(() => null) as unknown;
    return normalizeBmsFeatures(payload);
}

export function useBmsFeatures(): BmsFeatures {
    return useBmsFeatureState().features;
}

export function resolveBmsFeatureQueryState(
    data: BmsFeatureState | undefined,
    failed: boolean,
): BmsFeatureState {
    if (failed || !data) {
        return {
            features: { ...DEFAULT_BMS_FEATURES },
            devFeatures: { ...DEFAULT_BMS_DEV_FEATURES },
        };
    }
    return data;
}

export function useBmsFeatureState(): BmsFeatureState {
    const query = useQuery({
        queryKey: ['bms-install-features'],
        queryFn: fetchBmsFeatureState,
        staleTime: 60_000,
    });
    return resolveBmsFeatureQueryState(query.data, query.isError);
}
