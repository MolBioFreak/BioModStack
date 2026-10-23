import { api } from './api';

export const isNativeBinderGeneration = (job: { model_id: string; mode: string; params?: Record<string, unknown>; provenance?: Record<string, unknown> | null } | undefined) => Boolean(job && (
    Boolean(job.provenance?.native_generation_publication)
    || (job.model_id === 'protein_modification_experimental' && job.mode === 'de_novo_design'
        && ['rfd4_proteina', 'complexa'].includes(String(job.params?.generator_model_id ?? job.params?.generator)))
    || (job.model_id === 'boltzgen' && ['protein_binder', 'nanobody_binder', 'peptide_binder', 'antibody_binder', 'ligand_binder', 'ntp_binder', 'scaffold_around_ligand', 'backbone_docking'].includes(job.mode))
    || (['rfd4_proteina', 'complexa'].includes(job.model_id) && ['generate', 'evaluate'].includes(job.mode))
    || (job.model_id === 'ppiflow' && ['protein_binder', 'antibody_binder', 'nanobody_binder'].includes(job.mode))));
export interface NativeGenerationDocument {
    artifact_id: string; target_state?: string | null; logical_path?: string; download_url?: string; primary?: boolean; sha256?: string;
    binder_chains?: string | string[] | null; target_chains?: string | string[] | null;
}
const hasChains = (value: NativeGenerationDocument['binder_chains']) => Array.isArray(value) ? value.length > 0 : typeof value === 'string' && value.trim().length > 0;
export function nativeDocumentLabel(document: NativeGenerationDocument): string {
    if (hasChains(document.binder_chains) && hasChains(document.target_chains)) return 'Binder–target complex';
    if (hasChains(document.binder_chains) && Object.prototype.hasOwnProperty.call(document, 'target_chains') && !hasChains(document.target_chains)) return 'Binder only';
    return document.target_state || 'Saved structure';
}
/** Display preference only: an explicitly requested document is still authoritative. */
export function uniqueBinderComplex(documents: NativeGenerationDocument[]): NativeGenerationDocument | undefined {
    const complexes = documents.filter(doc => hasChains(doc.binder_chains) && hasChains(doc.target_chains));
    return complexes.length === 1 ? complexes[0] : undefined;
}

export interface NativeGenerationRecord extends Record<string, unknown> {
    candidate_ref?: import('./binderSelectedRound').CandidateRef; candidate_key?: string; design_id?: string; structures?: NativeGenerationDocument[];
}
export interface NativeGenerationPage {
    receipt: Record<string, unknown>; records: NativeGenerationRecord[];
    publication: Record<string, unknown>; total: number; offset: number; limit: number;
    execution_status?: string; population_completeness?: string; observation_errors?: unknown[]; missingness?: unknown;
    artifacts?: Array<{ path: string; download_url?: string; bytes?: number }>;
}
/** Display-only projection: BoltzGen publishes verified scalar records, PPIFlow publishes flat metrics. */
export function nativeGenerationMetrics(row: NativeGenerationRecord): Record<string, unknown> {
    const block = row.native_metrics;
    if (block && typeof block === 'object' && !Array.isArray(block)
        && (block as Record<string, unknown>).producer === 'boltzgen') {
        const metrics = (block as Record<string, unknown>).metrics;
        if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) return {};
        return Object.fromEntries(Object.entries(metrics).map(([key, entry]) => {
            const record = entry && typeof entry === 'object' && !Array.isArray(entry) ? entry as Record<string, unknown> : {};
            const value = record.value;
            return [key, record.state === 'ok' && typeof value === 'number' && Number.isFinite(value) ? value : undefined];
        }));
    }
    return row.metrics && typeof row.metrics === 'object' && !Array.isArray(row.metrics) ? row.metrics as Record<string, unknown> : {};
}

export function nativeGenerationMetricUnit(row: NativeGenerationRecord | undefined, key: string): string | undefined {
    const block = row?.native_metrics;
    if (!block || typeof block !== 'object' || (block as Record<string, unknown>).producer !== 'boltzgen') return undefined;
    const metrics = (block as Record<string, unknown>).metrics;
    if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) return undefined;
    const entry = (metrics as Record<string, unknown>)[key];
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return undefined;
    const unit = (entry as Record<string, unknown>).unit;
    return typeof unit === 'string' && unit ? unit : undefined;
}

export function nativeGenerationMetricDetail(row: NativeGenerationRecord | undefined, key: string): string | undefined {
    const block = row?.native_metrics;
    if (!block || typeof block !== 'object' || (block as Record<string, unknown>).producer !== 'boltzgen') return undefined;
    const metrics = (block as Record<string, unknown>).metrics;
    if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) return undefined;
    const entry = (metrics as Record<string, unknown>)[key];
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return undefined;
    const record = entry as Record<string, unknown>;
    return [record.unit, record.state === 'ok' ? undefined : record.state, record.reason_code].filter(value => typeof value === 'string' && value).join(' · ') || undefined;
}

export async function fetchNativeBinderGeneration(jobId: string, offset = 0, limit = 100, signal?: AbortSignal): Promise<NativeGenerationPage> {
    return (await api.get<NativeGenerationPage>(`/api/jobs/${encodeURIComponent(jobId)}/generation-results`, { params: { offset, limit }, signal })).data;
}
export function nativeCandidateRoute(jobId: string, designId: string, document?: NativeGenerationDocument, launchContextId?: string | null) {
    const query = new URLSearchParams({ design_id: designId });
    if (launchContextId) query.set('launch_context_id', launchContextId);
    if (document) {
        query.set('artifact_id', document.artifact_id);
        if (document.target_state != null) query.set('target_state', document.target_state);
    }
    return `/designs/${encodeURIComponent(jobId)}?${query}`;
}
