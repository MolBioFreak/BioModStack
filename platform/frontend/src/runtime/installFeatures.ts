import { useQuery } from '@tanstack/react-query';

export const DEFAULT_BMS_FEATURES = {
    bioxp: false,
    ngs_ont: true,
    stats: true,
    protein_compute: true,
} as const;

export const DEFAULT_BMS_DEV_FEATURES = {
    bioxp: true,
    ngs_ont: false,
    stats: false,
    protein_compute: false,
} as const;

export type BmsFeatureKey = keyof typeof DEFAULT_BMS_FEATURES;
export type BmsFeatures = Record<BmsFeatureKey, boolean>;

export interface BmsFeatureState {
    features: BmsFeatures;
    configuredFeatures: BmsFeatures;
    devFeatures: BmsFeatures;
}

interface BmsFeaturesEnvelope {
    features?: Partial<Record<BmsFeatureKey, unknown>> | null;
    configured_features?: Partial<Record<BmsFeatureKey, unknown>> | null;
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

function normalizeFeatureMap(
    raw: Partial<Record<BmsFeatureKey, unknown>> | null | undefined,
    defaults: BmsFeatures,
): BmsFeatures {
    if (!raw || typeof raw !== 'object') {
        return { ...defaults };
    }
    return {
        bioxp: coerceFeatureBool(raw.bioxp, defaults.bioxp),
        ngs_ont: coerceFeatureBool(raw.ngs_ont, defaults.ngs_ont),
        stats: coerceFeatureBool(raw.stats, defaults.stats),
        protein_compute: coerceFeatureBool(raw.protein_compute, defaults.protein_compute),
    };
}

export function normalizeBmsFeatures(payload: unknown): BmsFeatures {
    const envelope = payload && typeof payload === 'object' ? payload as BmsFeaturesEnvelope : null;
    return normalizeFeatureMap(envelope?.features, { ...DEFAULT_BMS_FEATURES });
}

function normalizeBmsDevFeatures(payload: unknown): BmsFeatures {
    const envelope = payload && typeof payload === 'object' ? payload as BmsFeaturesEnvelope : null;
    return normalizeFeatureMap(envelope?.dev_features, { ...DEFAULT_BMS_DEV_FEATURES });
}

export function normalizeBmsFeatureState(payload: unknown): BmsFeatureState {
    const envelope = payload && typeof payload === 'object' ? payload as BmsFeaturesEnvelope : null;
    const features = normalizeBmsFeatures(payload);
    const configuredFeatures = envelope?.configured_features
        ? normalizeFeatureMap(envelope.configured_features, features)
        : { ...features };
    return {
        features,
        configuredFeatures,
        devFeatures: normalizeBmsDevFeatures(payload),
    };
}

export function isBmsFeatureEnabled(features: BmsFeatures, feature: BmsFeatureKey): boolean {
    return features[feature];
}

export function isBmsFeatureVisible(state: BmsFeatureState, feature: BmsFeatureKey, showDevFeatures: boolean, known = true): boolean {
    // A checked dev-tools control owns visibility while an install-status read is unavailable.
    // A validated disable still hides the add-on.
    return (state.features[feature] || (!known && showDevFeatures))
        && (showDevFeatures || !state.devFeatures[feature]);
}

export function resolveShowDevFeaturesDefault(viteDev: boolean, storedValue: string | null): boolean {
    if (storedValue === 'true') return true;
    if (storedValue === 'false') return false;
    return viteDev;
}

async function fetchBmsFeatureState(): Promise<BmsFeatureState> {
    const response = await fetch('/api/system/features', { cache: 'no-store' });
    if (!response.ok) {
        throw new Error(`install features unavailable (${response.status})`);
    }
    const payload = await response.json() as BmsFeaturesEnvelope | null;
    // Only validated configuration may replace the query's last successful value.
    // Missing/invalid replies are transport failures, not a server disable decision.
    if (typeof payload?.features?.bioxp !== 'boolean'
        || (payload.dev_features !== undefined && typeof payload.dev_features?.bioxp !== 'boolean')) {
        throw new Error('invalid install features response');
    }
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

export function useResolvedBmsFeatures(): { features: BmsFeatures; resolved: boolean; known: boolean } {
    const query = useQuery({
        queryKey: ['bms-install-features'],
        queryFn: fetchBmsFeatureState,
        staleTime: 60_000,
    });
    return {
        features: resolveBmsFeatureQueryState(query.data, query.isError).features,
        resolved: query.isSuccess || query.isError,
        known: query.data !== undefined,
    };
}

export function resolveBmsFeatureQueryState(
    data: BmsFeatureState | undefined,
    _failed: boolean,
): BmsFeatureState {
    // Refetch errors retain validated query data; a cold query remains disabled for opt-in dev features.
    if (!data) {
        return {
            features: { ...DEFAULT_BMS_FEATURES },
            configuredFeatures: { ...DEFAULT_BMS_FEATURES },
            devFeatures: { ...DEFAULT_BMS_DEV_FEATURES },
        };
    }
    return data;
}

export function useBmsFeatureState(): BmsFeatureState & { known: boolean } {
    const query = useQuery({
        queryKey: ['bms-install-features'],
        queryFn: fetchBmsFeatureState,
        staleTime: 60_000,
    });
    return { ...resolveBmsFeatureQueryState(query.data, query.isError), known: query.data !== undefined };
}
