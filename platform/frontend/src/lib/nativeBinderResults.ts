import { api } from './api';

export const isNativeBinderGeneration = (job: { model_id: string; mode: string; params?: Record<string, unknown>; provenance?: Record<string, unknown> | null } | undefined) => Boolean(job && (
    Boolean(job.provenance?.native_generation_publication)
    || (job.model_id === 'laproteina' && job.mode === 'evaluate')
    || (job.model_id === 'protein_modification_experimental' && job.mode === 'de_novo_design'
        && ['rfd4_proteina', 'complexa'].includes(String(job.params?.generator_model_id ?? job.params?.generator)))
    || (job.model_id === 'boltzgen' && ['protein_binder', 'nanobody_binder', 'peptide_binder', 'antibody_binder', 'ligand_binder', 'ntp_binder', 'scaffold_around_ligand', 'backbone_docking'].includes(job.mode))
    || (['rfd4_proteina', 'complexa'].includes(job.model_id) && ['generate', 'evaluate'].includes(job.mode))
    || (job.model_id === 'ppiflow' && ['protein_binder', 'antibody_binder', 'nanobody_binder'].includes(job.mode))));
export interface NativeGenerationDocument {
    artifact_id: string; target_state?: string | null; model?: number; logical_path?: string; download_url?: string; primary?: boolean; sha256?: string;
    relation_role?: string; producer_field?: string;
    binder_chains?: string | string[] | null; target_chains?: string | string[] | null;
}
const hasChains = (value: NativeGenerationDocument['binder_chains']) => Array.isArray(value) ? value.length > 0 : typeof value === 'string' && value.trim().length > 0;
export function nativeDocumentLabel(document: NativeGenerationDocument): string {
    if (document.relation_role) return `${document.relation_role} · ${document.producer_field ?? 'native relation'}`;
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
    record_counts?: { total: number; observations: number; molecular_records: number; populations: Record<string, number> };
    native_request?: Record<string, unknown> | null;
    receipt: Record<string, unknown>; records: NativeGenerationRecord[];
    publication: Record<string, unknown>; total: number; offset: number; limit: number;
    model_id?: string; owner_model_id?: string; native_execution_status?: string;
    execution_status?: string; population_completeness?: string; observation_errors?: unknown[]; missingness?: unknown;
    artifacts?: Array<{ path: string; download_url?: string; bytes?: number; artifact_id?: string; sha256?: string; source_path?: string | null; role?: string | null }>;
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
    const metrics = row.metrics && typeof row.metrics === 'object' && !Array.isArray(row.metrics) ? row.metrics as Record<string, unknown> : {};
    const typed = Object.fromEntries(Object.entries(metrics).map(([key, value]) => [key,
        metricDescriptor(row, key)?.value_type === 'number' && typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value)) ? Number(value) : value]));
    return Object.hasOwn(row, 'reward') ? { ...typed, reward: row.reward } : typed;
}

function metricDescriptor(row: NativeGenerationRecord | undefined, key: string): Record<string, unknown> | undefined {
    const descriptors = row?.metric_descriptors;
    if (!descriptors || typeof descriptors !== 'object' || Array.isArray(descriptors)) return undefined;
    const value = (descriptors as Record<string, unknown>)[key];
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}
export function nativeGenerationMetricUnit(row: NativeGenerationRecord | undefined, key: string): string | undefined {
    const declared = metricDescriptor(row, key)?.unit;
    if (typeof declared === 'string') return declared;
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
    const declared = metricDescriptor(row, key);
    if (typeof declared?.description === 'string') return [declared.description, declared.unit, declared.source].filter(Boolean).join(' · ');
    if (key === 'reward' && row?.event) return 'Native search reward; not independent prediction confidence';
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
        if (document.model != null) query.set('model_number', String(document.model));
    }
    return `/designs/${encodeURIComponent(jobId)}?${query}`;
}

/** Exact selector wire; MODEL is not a confidence source or an acceptance rule. */
export function fetchNativeComparison<T>(source: import('./binderContinuation').NativeCandidateSource, leaf: 'document' | 'residue-metrics') {
    return api.get<T>(`/api/designs/native/${encodeURIComponent(source.job_id)}/${encodeURIComponent(source.artifact_id)}/${leaf}`, {
        params: { target_state: source.document?.target_state, model_number: source.document?.model },
    });
}

export interface NativeExportContext {
    jobId: string; adapter: string; page?: NativeGenerationPage; loaded: number;
    scope: 'all' | 'selected' | 'filtered'; filter: unknown; rank: unknown;
    readError: string | null; loading: boolean; selected: unknown[]; unresolved: unknown[];
}
/** Portable, informational scope. Raw native objects are not rewritten or flattened. */
export function nativeGenerationExport(rows: NativeGenerationRecord[], context: NativeExportContext) {
    const page = context.page;
    return {
        schema: 'bms.native-generation-export.v1',
        job_id: context.jobId, model_id: page?.model_id ?? page?.publication.model_id ?? null,
        owner_model_id: page?.owner_model_id ?? null, adapter: context.adapter,
        publication: page?.publication ?? null, receipt: page?.receipt ?? null,
        scope: context.scope, filter: context.filter, rank: context.rank,
        counts: { total_known: page?.total ?? null, loaded: context.loaded, exported: rows.length, native_records: page?.record_counts ?? null },
        completeness: { loaded_complete: !!page && context.loaded === page.total && !context.readError && !context.loading,
            loading: context.loading, read_error: context.readError,
            population: page?.population_completeness ?? null, execution_status: page?.execution_status ?? null,
            native_execution_status: page?.native_execution_status ?? null,
            observation_errors: page?.observation_errors ?? [], missingness: page?.missingness ?? null },
        selected_refs: context.selected, unresolved_selected_refs: context.unresolved,
        records: rows.map(row => ({ candidate_key: row.candidate_key ?? null, candidate_ref: row.candidate_ref ?? null,
            design_id: row.design_id ?? null, documents: row.structures ?? [],
            native_record: Object.prototype.hasOwnProperty.call(row, 'native_record') ? row.native_record : row,
            // Keep reader additions (metric descriptors, bindings, membership) independently of raw native payload.
            record: Object.prototype.hasOwnProperty.call(row, 'native_record')
                ? Object.fromEntries(Object.entries(row).filter(([key]) => key !== 'native_record')) : null })),
    };
}
