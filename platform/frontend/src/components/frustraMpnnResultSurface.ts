export interface FrustraMpnnSurfaceJob {
    readonly model_id?: string | null;
    readonly params?: unknown;
    readonly frustrampnn_result_count?: number | null;
}

export interface FrustraMpnnResultContext {
    readonly kind: 'scheduler-child' | 'integrated-parent';
    readonly usesChildReceipt: boolean;
    readonly canReanalyzePersistedInputs: boolean;
    readonly canRetryStatisticsAnalysis: boolean;
    readonly executionLabel: string;
}

const asRecord = (value: unknown): Record<string, unknown> | null => (
    value !== null && typeof value === 'object' && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null
);

/** FrustraMPNN owns a result surface only when applicability is explicit. */
export const hasFrustraMpnnResultSurface = (job: FrustraMpnnSurfaceJob | null | undefined): boolean => {
    if (!job) return false;
    if ((job.model_id ?? '').trim().toLowerCase() === 'frustrampnn') return true;
    if (Number.isInteger(job.frustrampnn_result_count) && Number(job.frustrampnn_result_count) > 0) return true;
    return asRecord(job.params)?.run_frustrampnn === true;
};

/** Child-only receipt/reanalysis authority must never be applied to integrated parents. */
export const getFrustraMpnnResultContext = (
    job: FrustraMpnnSurfaceJob | null | undefined,
): FrustraMpnnResultContext | null => {
    if (!hasFrustraMpnnResultSurface(job)) return null;
    if ((job?.model_id ?? '').trim().toLowerCase() === 'frustrampnn') {
        return {
            kind: 'scheduler-child',
            usesChildReceipt: true,
            canReanalyzePersistedInputs: true,
            canRetryStatisticsAnalysis: true,
            executionLabel: 'Persisted execution child',
        };
    }
    return {
        kind: 'integrated-parent',
        usesChildReceipt: false,
        canReanalyzePersistedInputs: false,
        canRetryStatisticsAnalysis: true,
        executionLabel: 'Persisted workflow analysis',
    };
};
