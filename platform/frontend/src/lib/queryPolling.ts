type PollingQueryState = { state?: { fetchFailureCount?: number } };

const MAX_POLL_BACKOFF_MULTIPLIER = 16;

/**
 * Shared adaptive polling policy. TanStack suppresses background work through
 * `refetchIntervalInBackground: false`; retaining the interval here lets its
 * focus manager resume the same query when the renderer becomes visible again.
 */
export function jobPollingInterval(intervalMs: number, query?: PollingQueryState): number {
    const failures = Math.max(0, query?.state?.fetchFailureCount ?? 0);
    return intervalMs * Math.min(2 ** failures, MAX_POLL_BACKOFF_MULTIPLIER);
}
