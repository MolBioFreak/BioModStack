export type RemoteResultPolicy = 'manual' | 'automatic';
export interface ExecutionPolicy { remote_result_policy: RemoteResultPolicy }
export const RESULT_POLICY_DEFAULT_KEY = 'bms.remoteResultPolicy.default.v1';
let draft: ExecutionPolicy | undefined;
export const normalizeResultPolicy = (value: unknown): RemoteResultPolicy => value === 'automatic' ? 'automatic' : 'manual';
export function newJobExecutionPolicy(): ExecutionPolicy {
    return { remote_result_policy: normalizeResultPolicy(typeof window === 'undefined' ? null : window.localStorage.getItem(RESULT_POLICY_DEFAULT_KEY)) };
}
export function setDraftExecutionPolicy(value: ExecutionPolicy | undefined) { draft = value; }
export function submissionExecutionPolicy(): ExecutionPolicy { return draft ?? newJobExecutionPolicy(); }
export function saveNewJobExecutionPolicy(value: ExecutionPolicy) {
    window.localStorage.setItem(RESULT_POLICY_DEFAULT_KEY, value.remote_result_policy);
}
export function initialExecutionPolicy(): ExecutionPolicy {
    // A clone is saved job authority, never the current new-job preference.
    try {
        const source = JSON.parse(window.localStorage.getItem('clonedJobData') || 'null');
        if (source) return { remote_result_policy: normalizeResultPolicy(source.execution_policy?.remote_result_policy ?? source.params?.remote_result_policy) };
    } catch { /* malformed clone data cannot authorize automatic return */ }
    return newJobExecutionPolicy();
}
