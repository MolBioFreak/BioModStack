export type ClientDerivedResultsPolicyInput = {
    total: number;
    loaded: number;
    requiresClientDerivation: boolean;
};

export type ClientDerivedResultsPolicy = {
    allowed: boolean;
    message: string | null;
};

export type AuthoritativeDesignSummary = {
    total: number;
    favorites: number;
    avg_plddt: number | null;
    avg_pae: number | null;
    avg_ptm: number | null;
    avg_iptm: number | null;
    avg_ipsae: number | null;
    avg_affinity: number | null;
    avg_binder_probability: number | null;
    avg_epitope_contacts: number | null;
    avg_target_contacts: number | null;
    avg_epitope_distance: number | null;
    avg_target_distance: number | null;
    avg_hotspot_coverage: number | null;
    avg_psce: number | null;
    high_confidence: number;
    low_error: number;
    high_contacts: number;
    screen_passed: number;
    screen_failed: number;
};

export const CLIENT_DERIVED_RESULTS_LIMIT = 500;
const CLIENT_DERIVED_RESULTS_MESSAGE =
    'Client-side sorting and source/result-set filters require a result set of 500 designs or fewer. Narrow the server-side filters first.';

/**
 * Client-only filters and sort keys have no server equivalent. They must never
 * present a capped first page as a complete result set.
 */
export function getClientDerivedResultsPolicy({
    total,
    loaded,
    requiresClientDerivation,
}: ClientDerivedResultsPolicyInput): ClientDerivedResultsPolicy {
    if (requiresClientDerivation && (total > CLIENT_DERIVED_RESULTS_LIMIT || loaded < total)) {
        return { allowed: false, message: CLIENT_DERIVED_RESULTS_MESSAGE };
    }
    return { allowed: true, message: null };
}

export function applyAuthoritativeDesignSummary<T extends Record<string, unknown>>(
    sample: T,
    summary: AuthoritativeDesignSummary,
    loaded: number,
): T {
    const merged: Record<string, unknown> = {
        ...sample,
        total: summary.total,
        favorites: summary.favorites,
        avgPlddt: summary.avg_plddt,
        avgPae: summary.avg_pae,
        avgPtm: summary.avg_ptm,
        avgIptm: summary.avg_iptm,
        avgIpsae: summary.avg_ipsae,
        avgAffinity: summary.avg_affinity,
        avgBinderProb: summary.avg_binder_probability,
        avgEpitopeContacts: summary.avg_epitope_contacts,
        avgTargetContacts: summary.avg_target_contacts,
        avgEpitopeDistance: summary.avg_epitope_distance,
        avgTargetDistance: summary.avg_target_distance,
        avgHotspotCoverage: summary.avg_hotspot_coverage,
        avgPsce: summary.avg_psce,
        highConfidence: summary.high_confidence,
        lowError: summary.low_error,
        highContacts: summary.high_contacts,
        screenPassed: summary.screen_passed,
        screenFailed: summary.screen_failed,
    };

    if (summary.total > loaded) {
        Object.assign(merged, {
            avgMaxResiduePsce: null,
            avgPpiflowDeltaInterface: null,
            avgPpiflowInterfaceScore: null,
            avgPpiflowRmsd: null,
            avgPpiflowSeqIdentity: null,
            avgPpiflowAnchors: null,
            ppiflowUniqueSources: 0,
            ppiflowImproved: 0,
            ppiflowStable: 0,
            ppiflowDegraded: 0,
            ppiflowZeroClash: 0,
            ppiflowClashy: 0,
            ppiflowLowDrift: 0,
            avgFrustrationHigh: null,
            avgFrustrationPctHigh: null,
            annotatedWithFrustration: 0,
            tierA: 0,
            tierB: 0,
            tierC: 0,
            tierD: 0,
            psceExcellent: 0,
            psceGood: 0,
            psceModerate: 0,
            psceReview: 0,
            worstPsceClean: 0,
            worstPsceWatch: 0,
            worstPsceOutlier: 0,
            worstPsceSevere: 0,
            topScreeningReasons: [],
        });
    }

    return merged as T;
}
