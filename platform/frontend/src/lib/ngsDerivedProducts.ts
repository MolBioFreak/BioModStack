import { api } from './api.js';
import { withAlignmentAccessRecovery } from './ngsAlignmentSession';

export type DerivedProductKind = 'catalog' | 'preview';
export type DerivedFailureCode = 'source_invalid' | 'resource_limit' | 'cancelled'
    | 'infrastructure_failed' | 'publication_failed' | 'integrity_mismatch';
interface RequestedProduct {
    request_id: string;
    request_sha256: string | null;
    attempt_count: number;
    manual_retry_count: number;
}
export type DerivedProductState =
    | { state: 'unavailable'; reason: 'request_missing' | 'unsupported_source' }
    | (RequestedProduct & { state: 'requested'; blocked_on?: 'catalog' | null })
    | (RequestedProduct & { state: 'running'; request_sha256: string })
    | (RequestedProduct & { state: 'ready'; request_sha256: string; authority_sha256: string; manifest_sha256: string })
    | (RequestedProduct & { state: 'failed'; code: DerivedFailureCode; retryable: boolean });
export interface AlignmentDerivedStatus {
    schema: 'bms.ngs.alignment-presentation.v3';
    job_id: string;
    session_id: string;
    catalog: DerivedProductState;
    preview: DerivedProductState;
}

const digest = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const failureCodes = new Set(['source_invalid', 'resource_limit', 'cancelled', 'infrastructure_failed', 'publication_failed', 'integrity_mismatch']);
function object(value: unknown): Record<string, unknown> {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid derived product response.');
    return value as Record<string, unknown>;
}
function closed(value: Record<string, unknown>, keys: string[]) {
    if (Object.keys(value).some((key) => !keys.includes(key))) throw new Error('Unexpected derived product response fields.');
}
function parseProduct(value: unknown, kind: DerivedProductKind): DerivedProductState {
    const row = object(value);
    if (row.state === 'unavailable') {
        closed(row, ['state', 'reason']);
        if (row.reason !== 'request_missing' && row.reason !== 'unsupported_source') throw new Error('Invalid derived unavailable reason.');
        return row as Extract<DerivedProductState, { state: 'unavailable' }>;
    }
    const common = ['state', 'request_id', 'request_sha256', 'attempt_count', 'manual_retry_count'];
    if (typeof row.request_id !== 'string' || !row.request_id.startsWith(`ngs-${kind}-`)
        || (!digest(row.request_sha256) && row.request_sha256 !== null)
        || !Number.isSafeInteger(row.attempt_count) || Number(row.attempt_count) < 0
        || !Number.isSafeInteger(row.manual_retry_count) || Number(row.manual_retry_count) < 0) {
        throw new Error('Invalid derived request identity.');
    }
    if (row.state === 'requested') {
        closed(row, [...common, 'blocked_on']);
        if (row.blocked_on != null && (kind !== 'preview' || row.blocked_on !== 'catalog')) throw new Error('Invalid derived dependency.');
        if (kind === 'catalog' && !digest(row.request_sha256)) throw new Error('Catalog request identity is missing.');
    } else if (row.state === 'running' || row.state === 'ready') {
        closed(row, row.state === 'ready' ? [...common, 'authority_sha256', 'manifest_sha256'] : common);
        if (!digest(row.request_sha256) || Number(row.attempt_count) < 1) throw new Error('Derived claim identity is missing.');
        if (row.state === 'ready' && (!digest(row.authority_sha256) || !digest(row.manifest_sha256))) throw new Error('Derived publication identity is missing.');
    } else if (row.state === 'failed') {
        closed(row, [...common, 'code', 'retryable']);
        if (!failureCodes.has(String(row.code)) || typeof row.retryable !== 'boolean') throw new Error('Invalid derived failure.');
    } else {
        throw new Error('Unsupported derived state.');
    }
    return row as unknown as DerivedProductState;
}
export function parseAlignmentDerivedStatus(value: unknown, jobId: string, sessionId: string): AlignmentDerivedStatus {
    const row = object(value);
    closed(row, ['schema', 'job_id', 'session_id', 'catalog', 'preview']);
    if (row.schema !== 'bms.ngs.alignment-presentation.v3' || row.job_id !== jobId || row.session_id !== sessionId) {
        throw new Error('Derived response does not match this native result.');
    }
    return { schema: row.schema, job_id: jobId, session_id: sessionId,
        catalog: parseProduct(row.catalog, 'catalog'), preview: parseProduct(row.preview, 'preview') };
}
const base = (jobId: string, sessionId: string) => `/api/jobs/${encodeURIComponent(jobId)}/alignment-sessions/${encodeURIComponent(sessionId)}/presentation`;
export async function fetchAlignmentDerivedStatus(jobId: string, sessionId: string, signal?: AbortSignal): Promise<AlignmentDerivedStatus> {
    const response = await withAlignmentAccessRecovery(jobId, () => api.get(`${base(jobId, sessionId)}/status`, { signal }));
    return parseAlignmentDerivedStatus(response.data, jobId, sessionId);
}
export async function retryAlignmentDeliveryCache(jobId: string, sessionId: string, kind: DerivedProductKind, requestId: string): Promise<AlignmentDerivedStatus> {
    // Mutations are not automatically replayed, including after access recovery.
    const response = await api.post(`${base(jobId, sessionId)}/products/${kind}/cache/retry`, { request_id: requestId });
    return parseAlignmentDerivedStatus(response.data, jobId, sessionId);
}

export async function retryAlignmentDerivedProduct(jobId: string, sessionId: string, kind: DerivedProductKind, requestId: string): Promise<AlignmentDerivedStatus> {
    const response = await withAlignmentAccessRecovery(jobId, () => api.post(`${base(jobId, sessionId)}/products/${kind}/retry`, { request_id: requestId }));
    return parseAlignmentDerivedStatus(response.data, jobId, sessionId);
}


export interface ReadyPreview {
    schema: "bms.ngs.alignment-preview.v6";
    job_id: string; session_id: string; catalog_authority_sha256: string;
    preview_request_id: string; preview_authority_sha256: string;
    selected_read_count: number; selected_record_count: number;
    bam: { url: string; sha256: string; size_bytes: number; mime_type: string; range_capable: true };
    index: ReadyPreview["bam"];
}
function exact(value: unknown, keys: string[]) {
    const row = object(value); closed(row, keys);
    if (keys.some((key) => !Object.prototype.hasOwnProperty.call(row, key))) throw new Error("Missing preview authority.");
    return row;
}
function canonicalIdentity(value: unknown): string {
    if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
    if (typeof value === "number" && Number.isSafeInteger(value)) return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(canonicalIdentity).join(",")}]`;
    if (value && typeof value === "object") return `{${Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
        .map(([key, item]) => `${JSON.stringify(key)}:${canonicalIdentity(item)}`).join(",")}}`;
    throw new Error("Invalid identity value.");
}
async function identityDigest(value: unknown): Promise<string> {
    return [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonicalIdentity(value))))]
        .map((byte) => byte.toString(16).padStart(2, "0")).join("");
}
export async function fetchReadyPreview(session: import("./ngsAlignmentSession").AlignmentSession, status: AlignmentDerivedStatus, signal?: AbortSignal): Promise<ReadyPreview> {
    if (!session.ready || status.job_id !== session.job_id || status.session_id !== session.session_id
        || status.catalog.state !== "ready" || status.preview.state !== "ready") throw new Error("Preview is not ready for this session.");
    const response = await withAlignmentAccessRecovery(session.job_id, () => api.get(
        `/api/jobs/${encodeURIComponent(session.job_id)}/alignment-sessions/${session.session_id}/preview-product`,
        { signal, params: { preview_request_id: status.preview.state === "ready" ? status.preview.request_id : null } }));
    const row = exact(response.data, ["schema", "job_id", "session_id", "source", "catalog_authority_sha256", "preview_request_id",
        "preview_authority_sha256", "policy", "selected_read_count", "selected_record_count", "bam", "index"]);
    if (row.schema !== "bms.ngs.alignment-preview.v6" || row.job_id !== session.job_id || row.session_id !== session.session_id
        || row.catalog_authority_sha256 !== status.catalog.authority_sha256 || row.preview_request_id !== status.preview.request_id
        || row.preview_authority_sha256 !== status.preview.authority_sha256) throw new Error("Preview publication changed.");
    const source = exact(row.source, ["schema", "job_id", "session_id", "mode", "reference", "source_manifest_sha256", "source_artifact_set_sha256",
        "package_artifact_set_sha256", "alignment_pair_sha256", "alignment_sha256", "alignment_size_bytes", "alignment_index_sha256", "alignment_index_size_bytes"]);
    if (source.schema !== "bms.ngs.alignment-presentation-source.v2" || source.job_id !== session.job_id
        || source.session_id !== session.session_id || source.mode !== session.mode
        || source.source_manifest_sha256 !== session.artifacts.alignment?.source_manifest_sha256
        || source.package_artifact_set_sha256 !== session.artifact_set_sha256
        || source.alignment_pair_sha256 !== session.alignment_pair_sha256
        || source.alignment_sha256 !== session.artifacts.alignment?.sha256 || source.alignment_size_bytes !== session.artifacts.alignment?.size_bytes
        || source.alignment_index_sha256 !== session.artifacts.alignment_index?.sha256 || source.alignment_index_size_bytes !== session.artifacts.alignment_index?.size_bytes
        || !digest(source.source_artifact_set_sha256)) throw new Error("Preview scientific source changed.");
    const reference = exact(source.reference, ["contig", "length_bp", "topology", "normalized_sequence_sha256", "fasta_sha256", "fai_sha256"]);
    if (Object.entries(reference).some(([key, value]) => value !== session.reference[key as keyof typeof session.reference])) throw new Error("Preview reference changed.");
    if (session.schema === "bms.ngs.native-alignment-session.v2" && await identityDigest(source) !== session.source_authority_sha256) throw new Error("Native preview source authority changed.");
    const policy = exact(row.policy, ["schema", "target_reads", "max_records", "max_bytes", "projection", "header_policy", "bgzf_admission_version", "writer_contract"]);
    if (policy.schema !== "bms.ngs.alignment-preview-policy.v6" || policy.target_reads !== 5000 || policy.max_records !== 20000
        || policy.max_bytes !== 67108864 || policy.projection !== "alignment_core_projection_v1" || policy.header_policy !== "sq_coordinate_v1"
        || policy.bgzf_admission_version !== 2) throw new Error("Unsupported preview policy.");
    const writer = exact(policy.writer_contract, ["pysam", "htslib", "mode", "threads", "record_order", "selection"]);
    if (typeof writer.pysam !== "string" || !writer.pysam || typeof writer.htslib !== "string" || !writer.htslib
        || writer.mode !== "wb6" || writer.threads !== 1 || writer.record_order !== "reference_start_source_ordinal"
        || writer.selection !== "stratified_largest_remainder_sha256_v1") throw new Error("Unsupported preview writer.");
    if ("ngs-preview-" + await identityDigest({ schema: "bms.ngs.alignment-preview-intent.v6", catalog_request_id: status.catalog.request_id, policy }) !== status.preview.request_id) throw new Error("Preview policy is not bound to this request.");
    for (const key of ["selected_read_count", "selected_record_count"]) {
        if (!Number.isSafeInteger(row[key]) || Number(row[key]) < 0) throw new Error("Invalid preview counts.");
    }
    if (Number(row.selected_read_count) > 5000 || Number(row.selected_record_count) > 20000
        || Number(row.selected_read_count) > Number(row.selected_record_count)) throw new Error("Preview limits changed.");
    for (const key of ["bam", "index"]) {
        const artifact = exact(row[key], ["url", "sha256", "size_bytes", "mime_type", "range_capable"]);
        const url = `/api/jobs/${encodeURIComponent(session.job_id)}/alignment-sessions/${session.session_id}/presentation/products/preview/${status.preview.request_id}/${status.preview.authority_sha256}/${key}`;
        if (artifact.url !== url || !digest(artifact.sha256) || !Number.isSafeInteger(artifact.size_bytes) || Number(artifact.size_bytes) < 1
            || artifact.mime_type !== "application/octet-stream" || artifact.range_capable !== true
            || key === "bam" && Number(artifact.size_bytes) > 67108864) throw new Error("Invalid preview artifact.");
    }
    return row as unknown as ReadyPreview;
}


export interface ReadyReadOverlay {
    schema: 'bms.ngs.read-overlay.v2'; job_id: string; session_id: string; read_id: string;
    overlay_id: string; catalog_authority_sha256: string; state: 'ready';
    primary_record_count: 1; mapped_supplementary_record_count: number;
    bam: ReadyPreview['bam']; index: ReadyPreview['bam']; manifest: ReadyPreview['bam'];
}
/** One explicit attempt. Access renewal does not retry a failed overlay build. */
export async function createReadOverlay(session: import('./ngsAlignmentSession').AlignmentSession,
    read: import('./ngsAlignmentSession').AlignmentRead, signal?: AbortSignal): Promise<ReadyReadOverlay> {
    if (!session.ready || !digest(read.population_id) || !digest(read.catalog_authority_sha256)) throw new Error('Exact catalog selection is unavailable.');
    const base = `/api/jobs/${encodeURIComponent(session.job_id)}/alignment-sessions/${encodeURIComponent(session.session_id)}/read-overlays`;
    const response = await api.post(base,
        { schema: 'bms.ngs.read-overlay-request.v2', read_id: read.read_id, population_id: read.population_id }, { signal });
    const row = exact(response.data, ['schema', 'job_id', 'session_id', 'read_id', 'overlay_id', 'state',
        'catalog_authority_sha256', 'identity', 'primary_record_count', 'mapped_supplementary_record_count', 'bam', 'index', 'manifest']);
    if (row.schema !== 'bms.ngs.read-overlay.v2' || row.job_id !== session.job_id || row.session_id !== session.session_id
        || row.read_id !== read.read_id || row.state !== 'ready' || !digest(row.overlay_id)
        || row.catalog_authority_sha256 !== read.catalog_authority_sha256 || row.primary_record_count !== 1
        || !Number.isSafeInteger(row.mapped_supplementary_record_count)
        || Number(row.mapped_supplementary_record_count) < 0 || Number(row.mapped_supplementary_record_count) > 255) throw new Error('Selected-read response changed authority.');
    const identity = exact(row.identity, ['schema', 'source', 'catalog_authority_sha256', 'catalog_artifacts', 'source_header', 'read_id', 'policy']);
    if (identity.schema !== 'bms.ngs.read-overlay-identity.v2' || identity.read_id !== read.read_id
        || identity.catalog_authority_sha256 !== read.catalog_authority_sha256 || await identityDigest(identity) !== row.overlay_id) throw new Error('Selected-read identity changed.');
    const source = exact(identity.source, ['schema', 'job_id', 'session_id', 'mode', 'reference', 'source_manifest_sha256', 'source_artifact_set_sha256',
        'package_artifact_set_sha256', 'alignment_pair_sha256', 'alignment_sha256', 'alignment_size_bytes', 'alignment_index_sha256', 'alignment_index_size_bytes']);
    if (source.schema !== 'bms.ngs.alignment-presentation-source.v2' || source.job_id !== session.job_id || source.session_id !== session.session_id
        || source.mode !== session.mode || source.source_manifest_sha256 !== session.artifacts.alignment?.source_manifest_sha256
        || source.package_artifact_set_sha256 !== session.artifact_set_sha256 || source.alignment_pair_sha256 !== session.alignment_pair_sha256
        || source.alignment_sha256 !== session.artifacts.alignment?.sha256 || source.alignment_size_bytes !== session.artifacts.alignment?.size_bytes
        || source.alignment_index_sha256 !== session.artifacts.alignment_index?.sha256 || source.alignment_index_size_bytes !== session.artifacts.alignment_index?.size_bytes
        || !digest(source.source_artifact_set_sha256)) throw new Error('Selected-read scientific source changed.');
    const reference = exact(source.reference, ['contig', 'length_bp', 'topology', 'normalized_sequence_sha256', 'fasta_sha256', 'fai_sha256']);
    if (Object.entries(reference).some(([key, value]) => value !== session.reference[key as keyof typeof session.reference])) throw new Error('Selected-read reference changed.');
    if (session.schema === 'bms.ngs.native-alignment-session.v2' && await identityDigest(source) !== session.source_authority_sha256) throw new Error('Native selected-read source changed.');
    const catalog = exact(identity.catalog_artifacts, ['catalog', 'locators']);
    for (const role of ['catalog', 'locators']) {
        const item = exact(catalog[role], ['filename', 'sha256', 'size_bytes']);
        if (item.filename !== (role === 'catalog' ? 'read-catalog.parquet' : 'read-record-locators.parquet') || !digest(item.sha256)
            || !Number.isSafeInteger(item.size_bytes) || Number(item.size_bytes) < 1) throw new Error('Invalid overlay catalog identity.');
    }
    const header = exact(identity.source_header, ['sha256', 'raw_bytes']);
    if (!digest(header.sha256) || !Number.isSafeInteger(header.raw_bytes) || Number(header.raw_bytes) < 12) throw new Error('Invalid exact source header.');
    const policy = exact(identity.policy, ['schema', 'max_records', 'max_bam_bytes', 'max_index_bytes', 'max_seconds', 'tags', 'header', 'bgzf_admission_version', 'writer']);
    if (policy.schema !== 'bms.ngs.read-overlay-policy.v2' || policy.max_records !== 256 || policy.max_bam_bytes !== 16777216
        || policy.max_index_bytes !== 1048576 || policy.max_seconds !== 10 || policy.tags !== 'all_exact_source_tags_v1'
        || policy.header !== 'exact_source_header_v1' || policy.bgzf_admission_version !== 2) throw new Error('Unsupported selected-read policy.');
    const writer = exact(policy.writer, ['pysam', 'htslib', 'mode', 'threads', 'order']);
    if (typeof writer.pysam !== 'string' || !writer.pysam || typeof writer.htslib !== 'string' || !writer.htslib
        || writer.mode !== 'wb6' || writer.threads !== 1 || writer.order !== 'reference_start_source_ordinal') throw new Error('Unsupported selected-read writer.');
    for (const role of ['bam', 'index', 'manifest']) {
        const item = exact(row[role], ['url', 'sha256', 'size_bytes', 'mime_type', 'range_capable']);
        if (!digest(item.sha256) || item.url !== `${base}/${row.overlay_id}/${item.sha256}/${role === 'index' ? 'bai' : role}`
            || !Number.isSafeInteger(item.size_bytes) || Number(item.size_bytes) < 1 || Number(item.size_bytes) > (role === 'bam' ? 16777216 : 1048576)
            || item.mime_type !== 'application/octet-stream' || item.range_capable !== true) throw new Error('Invalid selected-read artifact.');
    }
    return row as unknown as ReadyReadOverlay;
}

export async function retrySelectedReadDeliveryCache(session: import('./ngsAlignmentSession').AlignmentSession,
    read: import('./ngsAlignmentSession').AlignmentRead, signal?: AbortSignal): Promise<void> {
    if (!session.ready || !digest(read.population_id) || !digest(read.catalog_authority_sha256)) throw new Error('Exact catalog selection is unavailable.');
    await api.post(`/api/jobs/${encodeURIComponent(session.job_id)}/alignment-sessions/${encodeURIComponent(session.session_id)}/read-overlays/cache/retry`,
        { schema: 'bms.ngs.read-overlay-request.v2', read_id: read.read_id, population_id: read.population_id }, { signal });
}
