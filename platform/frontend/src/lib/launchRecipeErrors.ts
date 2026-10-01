export interface BinderShellError { message: string; section: 'sources' | 'binder' | 'campaign' | 'objectives' | 'expert'; field?: string }
/** Presentation mapping only; the model-owned failure remains authoritative. */
export function binderShellError(error: unknown): BinderShellError {
    const failure = error as { response?: { data?: { detail?: unknown } }; message?: string };
    const detail = failure?.response?.data?.detail;
    const value = detail && typeof detail === 'object' ? detail as Record<string, unknown> : undefined;
    const message = typeof detail === 'string' ? detail : typeof value?.message === 'string' ? value.message : detail ? JSON.stringify(detail) : failure?.message ?? 'Campaign preview failed';
    const field = typeof value?.field === 'string' ? value.field : ['max_trajectories', 'targets', 'binder_scaffold', 'binder_lengths', 'filters', 'losses'].find(key => message.includes(key));
    const section = field === 'targets' || field === 'binder_scaffold' ? 'sources' : field === 'binder_lengths' ? 'binder' : field === 'filters' || field === 'losses' ? 'objectives' : 'campaign';
    return { message, section, ...(field ? { field } : {}) };
}
