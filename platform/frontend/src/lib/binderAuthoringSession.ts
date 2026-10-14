import { EXECUTION_TARGET_STORAGE_KEY } from './api';

const prefix = 'bms.binder-authoring.v1:';
/** Tab-local drafts: settings and destination are one envelope, never ancestry. */
export function binderAuthoringKey(search: string): string | null {
    const query = new URLSearchParams(search);
    if (['launch_context_id', 'setup_context_id', 'source_structure', 'bc2_source_job_id'].some(key => query.has(key))) return null;
    const model = query.get('model');
    if (model && ['boltzgen', 'ppiflow'].includes(model)) return `${prefix}${model}:${query.get('mode') ?? ''}`;
    if (query.get('template') === 'antibody_denovo' || model === 'bindcraft2') return `${prefix}antibody_denovo`;
    return null;
}
export function readBinderAuthoringSession(storage: Storage, key: string | null): Record<string, UntypedApiValue> | undefined {
    if (!key) return undefined;
    try {
        const envelope = JSON.parse(storage.getItem(key) ?? 'null');
        return envelope?.schema_version === 1 && envelope.values && typeof envelope.values === 'object' && !Array.isArray(envelope.values) ? envelope.values : undefined;
    } catch { return undefined; }
}
export function writeBinderAuthoringSession(storage: Storage, key: string | null, values: Record<string, UntypedApiValue>) {
    if (!key) return;
    try { storage.setItem(key, JSON.stringify({ schema_version: 1, values: { ...values, execution_target_id: storage.getItem(EXECUTION_TARGET_STORAGE_KEY) || null } })); }
    catch { /* Storage unavailable/quota does not alter the editable request. */ }
}
