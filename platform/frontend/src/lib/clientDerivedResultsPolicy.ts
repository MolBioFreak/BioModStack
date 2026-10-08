export type ClientDerivedResultsPolicyInput = {
    total: number;
    loaded: number;
    requiresClientDerivation: boolean;
};

export type ClientDerivedResultsPolicy = {
    allowed: boolean;
    message: string | null;
};

export const CLIENT_DERIVED_RESULTS_LIMIT = 500;
const CLIENT_DERIVED_RESULTS_MESSAGE =
    'Client-side sorting and source/result-set filters require a result set of 500 designs or fewer. Narrow the server-side filters first.';

/**
 * Client-only filters and sort keys have no server equivalent.  They must never
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
