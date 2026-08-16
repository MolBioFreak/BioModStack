export interface AntibodyRefinementLaunchState {
    refinementMode: boolean;
    sourceJobId?: string;
    selectedDesignIds?: string[];
    sourceSavedFilterSetId?: string | null;
    sourceSavedFilterSetName?: string | null;
    sourceSavedFilterSetCreatedAt?: string | null;
    sourceSavedFilterSetDesignCount?: number | null;
    reviewFilterSetId?: string | null;
    reviewFilterSetName?: string | null;
    reviewFilterSetCreatedAt?: string | null;
    reviewFilterSetDesignCount?: number | null;
    sourceArtifactGroup?: string | null;
    sourceOutputSourceFilter?: string | null;
    sourceSortField?: string | null;
    sourceSortDir?: 'asc' | 'desc' | null;
    sourceVisibleCount?: number | null;
    sourceTotalCount?: number | null;
}

const STORAGE_ENTRY = 'bms_antibody_refinement_launch_v1';

const canUseSessionStorage = () =>
    typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined';

export const saveAntibodyRefinementLaunchState = (state: AntibodyRefinementLaunchState) => {
    if (!canUseSessionStorage()) return;
    try {
        window.sessionStorage.setItem(STORAGE_ENTRY, JSON.stringify(state));
    } catch (error) {
        console.warn('[REFINEMENT] Failed to persist launch state', error);
    }
};

export const loadAntibodyRefinementLaunchState = (): AntibodyRefinementLaunchState | null => {
    if (!canUseSessionStorage()) return null;
    try {
        const raw = window.sessionStorage.getItem(STORAGE_ENTRY);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== 'object') return null;
        return parsed as AntibodyRefinementLaunchState;
    } catch (error) {
        console.warn('[REFINEMENT] Failed to hydrate launch state', error);
        return null;
    }
};

export const clearAntibodyRefinementLaunchState = () => {
    if (!canUseSessionStorage()) return;
    try {
        window.sessionStorage.removeItem(STORAGE_ENTRY);
    } catch (error) {
        console.warn('[REFINEMENT] Failed to clear launch state', error);
    }
};
