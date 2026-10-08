import { api } from './api';
import type { BioXpOperatorJsonSchema, BioXpWorkflowJob } from './bioxpClient';
export type MethodValue = Record<string, unknown>;
export type MethodRecord = { id: string; revision: number; name: string; description?: string; method: MethodValue };
export type MethodCollection = 'library' | 'liquid-classes' | 'presets';
export type MethodFinding = { code: string; message: string; path?: string; category?: string; step_id?: string };
export type MethodCompile = { document: MethodValue | null; digest: string | null; issues: MethodFinding[]; resolved?: unknown; dependencies?: unknown; water_substitutions?: unknown[]; provenance?: MethodValue[]; simulation?: unknown };
export type MethodCatalog = { actions?: Array<MethodValue & { action?: string; id?: string; label?: string; inputs?: BioXpOperatorJsonSchema; input_schema?: BioXpOperatorJsonSchema }>; [key: string]: unknown };
const base = '/api/bioxp/methods';
export const methodsGet = async <T,>(path: string, params?: Record<string, unknown>): Promise<T> => (await api.get<T>(`${base}/${path}`, { params })).data;
export const methodsPost = async <T,>(path: string, body: unknown, params?: Record<string, unknown>): Promise<T> => (await api.post<T>(`${base}/${path}`, body, params ? { params } : undefined)).data;
export const methodsSave = async (collection: MethodCollection, method: MethodValue, saved: MethodRecord | null): Promise<MethodRecord> => {
    const body = { method, name: method.name, ...(saved ? { expected_base_revision: saved.revision } : {}) };
    return saved ? (await api.put<MethodRecord>(`${base}/${collection}/${encodeURIComponent(saved.id)}`, body)).data
        : methodsPost<MethodRecord>(collection, body);
};
export const methodRows = <T,>(value: unknown): T[] => Array.isArray(value) ? value : value && typeof value === 'object' ? ((value as { rows?: T[] }).rows ?? (value as { items?: T[] }).items ?? (value as { examples?: T[] }).examples ?? []) : [];
export const newMethod = (): MethodValue => ({ schema: 'bms.bioxp-method.v1', name: '', parameters: [], procedures: [], steps: [] });
export type MethodRun = BioXpWorkflowJob & { method_snapshot?: MethodValue };
/** Only explicit admission evidence settles refusal. HTTP failure/404 alone never does. */
export function definiteMethodRefusal(error: unknown): boolean {
    const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return !!detail && typeof detail === 'object' && ((detail as MethodValue).delivery === 'not_submitted' || (detail as MethodValue).dispatch_state === 'not_dispatched');
}
