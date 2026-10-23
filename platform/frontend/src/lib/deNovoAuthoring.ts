import { prepareExecutionPlacement, type Job } from './api';

/** Editor history is not scientific input. Snapshot placement at the parent. */
export function deNovoAuthoringEnvelope(draft: Record<string, unknown>) {
    const { native_job_request, ...state } = draft;
    const placement = prepareExecutionPlacement({});
    const editor_state = { ...state, ...placement };
    return Object.hasOwn(draft, 'native_job_request')
        ? { editor_state, native_job_request: native_job_request ? { ...(native_job_request as Partial<Job>), ...placement } : null }
        : editor_state;
}

export function deNovoEditorState(values: Record<string, unknown>): Record<string, unknown> {
    return values.editor_state && typeof values.editor_state === 'object' && !Array.isArray(values.editor_state)
        ? values.editor_state as Record<string, unknown> : values;
}
