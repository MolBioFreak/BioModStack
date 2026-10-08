import { api } from './api.js';

export type AlignmentSessionMode = 'primary' | 'dimer_candidates';

export interface AlignmentSessionArtifact {
    artifact_id: string;
    url: string;
    sha256: string;
    size_bytes: number;
    mime_type: string;
    range_capable: true;
    source_manifest_sha256: string;
}

export type AlignmentSessionArtifactRole =
    | 'alignment'
    | 'alignment_index'
    | 'reference'
    | 'reference_index'
    | 'coverage_depth'
    | 'gc_content'
    | 'position_gradient'
    | 'gc_zscore'
    | 'split_read_density'
    | 'soft_clip_density'
    | 'junction_hotspots'
    | 'report'
    | 'track_config';

export interface AlignmentSessionReference {
    contig: string;
    length_bp: number;
    topology: 'linear' | 'circular';
    normalized_sequence_sha256: string;
    fasta_sha256: string;
    fai_sha256: string;
}

interface AlignmentSessionBase {
    schema: 'bms.ngs.alignment-session.v1';
    session_id: string;
    job_id: string;
    mode: AlignmentSessionMode;
}

export interface ReadyAlignmentSession extends AlignmentSessionBase {
    ready: true;
    unavailable_reason: null;
    reads_url: string;
    sequence_qc_manifest_sha256: string;
    verification_manifest_sha256: string;
    artifact_set_sha256: string;
    reference: AlignmentSessionReference;
    artifacts: Partial<Record<AlignmentSessionArtifactRole, AlignmentSessionArtifact>>;
    alignment_pair_sha256: string;
}

export interface UnavailableAlignmentSession extends AlignmentSessionBase {
    mode: 'dimer_candidates';
    ready: false;
    unavailable_reason: string;
    reads_url: null;
    sequence_qc_manifest_sha256: null;
    verification_manifest_sha256: null;
    artifact_set_sha256: null;
    reference: null;
    artifacts: Record<string, never>;
    alignment_pair_sha256: null;
}

export type AlignmentSession = ReadyAlignmentSession | UnavailableAlignmentSession;

export interface AlignmentSessionResponse {
    schema: 'bms.ngs.alignment-session-list.v1';
    job_id: string;
    sessions: AlignmentSession[];
}

export interface AlignmentPresentationArtifact {
    kind: string;
    url: string;
    sha256: string;
    size_bytes: number;
    mime_type: string;
    range_capable: true;
}

export interface AlignmentPresentation {
    schema: 'bms.ngs.alignment-presentation.v1';
    job_id: string;
    session_id: string;
    mode: AlignmentSessionMode;
    state: 'ready';
    source: {
        package_manifest_sha256: string;
        alignment_sha256: string;
        alignment_size_bytes: number;
        alignment_index_sha256: string;
        alignment_index_size_bytes: number;
        primary_read_count: number;
        alignment_record_count: number;
    };
    policy: { id: string; version: string; target_reads: number; max_preview_bytes: number; max_coverage_bins: number };
    preview: {
        kind: 'primary_read_preview';
        selected_read_count: number;
        selected_record_count: number;
        selected_read_set_sha256: string;
        forward_count: number;
        reverse_count: number;
        bam: AlignmentPresentationArtifact;
        index: AlignmentPresentationArtifact;
    };
    coverage: {
        kind: 'full_source_primary_coverage';
        bin_width_bp: number;
        primary_read_count: number;
        artifact: AlignmentPresentationArtifact;
    };
    manifest: AlignmentPresentationArtifact;
}

export interface AlignmentLocusSliceRequest {
    contig: string;
    start_1based: number;
    end_1based: number;
    max_reads: number;
}

export interface AlignmentLocusSlice {
    schema: 'bms.ngs.alignment-locus-slice.v1';
    job_id: string;
    session_id: string;
    slice_id: string;
    state: 'ready';
    contig: string;
    start_1based: number;
    end_1based: number;
    overlapping_read_count: number;
    selected_read_count: number;
    selected_record_count: number;
    capped: boolean;
    policy: { id: string; version: string; max_reads: number };
    bam: AlignmentPresentationArtifact;
    index: AlignmentPresentationArtifact;
    manifest: AlignmentPresentationArtifact;
}

export interface AlignmentAccessRotationResponse {
    schema: 'bms.ngs.rotation-success.v1';
    job_id: string;
    rotated: true;
    scheme: 'opaque_job_capability_v1';
    rotation_count: number;
    expires_at: string;
}

function isExactNgsError(
    reason: unknown,
    status: number,
    code: string,
    expectedJobId?: string,
    expectedResource?: string,
    expectedRetryable = true,
): boolean {
    const response = (reason as { response?: { status?: unknown; data?: unknown } } | null)?.response;
    const data = response?.data;
    if (response?.status !== status || !data || typeof data !== 'object' || Array.isArray(data)) return false;
    const record = data as Record<string, unknown>;
    const keys = Object.keys(record).sort();
    const expectedKeys = ['code', 'job_id', 'message', 'resource', 'retryable', 'schema'];
    return keys.length === expectedKeys.length
        && keys.every((key, index) => key === expectedKeys[index])
        && record.schema === 'bms.ngs.error.v1'
        && record.code === code
        && (expectedJobId === undefined || record.job_id === expectedJobId)
        && typeof record.job_id === 'string'
        && typeof record.message === 'string'
        && record.message.length > 0
        && record.message.length <= 512
        && typeof record.resource === 'string'
        && (expectedResource === undefined || record.resource === expectedResource)
        && record.retryable === expectedRetryable;
}

export function isAlignmentAccessDenied(reason: unknown, expectedJobId?: string): boolean {
    return isExactNgsError(reason, 403, 'NGS_CAPABILITY_DENIED', expectedJobId);
}

export function describeNgsError(reason: unknown, fallback: string): string {
    const response = (reason as { response?: { status?: unknown; data?: unknown } } | null)?.response;
    const data = response?.data;
    if (data && typeof data === 'object' && !Array.isArray(data)) {
        const record = data as Record<string, unknown>;
        const keys = Object.keys(record).sort();
        const expectedKeys = ['code', 'job_id', 'message', 'resource', 'retryable', 'schema'];
        if (
            keys.length === expectedKeys.length
            && keys.every((key, index) => key === expectedKeys[index])
            && record.schema === 'bms.ngs.error.v1'
            && typeof record.code === 'string'
            && typeof record.message === 'string'
            && record.message.length > 0
            && record.message.length <= 512
        ) {
            const category = record.code.includes('INTEGRITY')
                ? 'Integrity error'
                : record.code.includes('CAPABILITY') || record.code.includes('AUTH') || record.code.includes('HIERARCHY')
                    ? 'Authorization error'
                    : record.code.includes('ROTATION')
                        ? 'Access rotation error'
                        : 'Governed NGS error';
            return `${category} (${record.code}): ${record.message}`;
        }
    }
    if (reason instanceof Error && reason.message.startsWith('Result parser error:')) return reason.message;
    if (typeof response?.status === 'number') return `Network error (HTTP ${response.status}): ${fallback}`;
    if (reason instanceof Error && reason.message) return `Network or transport error: ${reason.message}`;
    return fallback;
}

export interface AlignmentRead {
    read_id: string;
    length: number | null;
    mean_quality: number | null;
    contig: string | null;
    start_1based: number | null;
    strand: '+' | '-';
    mapq: number | null;
    cigar: string | null;
    flags: number;
    unmapped: boolean;
    sequence?: string | null;
    quality?: string | null;
}

export interface AlignmentReadPage {
    reads: AlignmentRead[];
    next_cursor: string | null;
    limit: number;
    sequence_included: boolean;
    scan_truncated: boolean;
}

export interface AlignmentReadRequestOptions {
    q?: string;
    cursor?: string;
    limit?: number;
    includeSequence?: boolean;
    contig?: string;
    start?: number;
    end?: number;
    signal?: AbortSignal;
}

export const DEFAULT_READ_QUERY_DEBOUNCE_MS = 300;

export class AlignmentReadScanTruncatedError extends Error {
    readonly scanTruncated = true;

    constructor(message = 'Exact read lookup scan budget exhausted; absence is not proven.') {
        super(message);
        this.name = 'AlignmentReadScanTruncatedError';
    }
}

export function isAlignmentReadScanTruncatedError(reason: unknown): reason is AlignmentReadScanTruncatedError {
    return reason instanceof AlignmentReadScanTruncatedError
        || (reason as { scanTruncated?: unknown } | null)?.scanTruncated === true;
}

export function createLatestRequestGuard() {
    let generation = 0;
    return {
        begin: () => ++generation,
        reset: () => { generation += 1; },
        isCurrent: (token: number) => token === generation,
    };
}

export function buildAlignmentReadRequestParams(options: AlignmentReadRequestOptions & { sessionId: string }) {
    return {
        session_id: options.sessionId,
        q: options.q?.trim() || undefined,
        cursor: options.cursor || undefined,
        limit: options.limit ?? 50,
        include_sequence: options.includeSequence === true,
        contig: options.contig?.trim() || undefined,
        start: options.start,
        end: options.end,
    };
}

export function normalizeAlignmentReadPage(page: AlignmentReadPage): AlignmentReadPage {
    return { ...page, scan_truncated: page.scan_truncated === true };
}

export function buildFastqDownload(read: Pick<AlignmentRead, 'read_id' | 'sequence' | 'quality'>): string | null {
    if (!read.sequence || !read.quality || read.quality.length !== read.sequence.length) return null;
    return `@${read.read_id}\n${read.sequence}\n+\n${read.quality}\n`;
}

const artifactRoles = [
    'alignment',
    'alignment_index',
    'reference',
    'reference_index',
    'coverage_depth',
    'gc_content',
    'position_gradient',
    'gc_zscore',
    'split_read_density',
    'soft_clip_density',
    'junction_hotspots',
    'report',
    'track_config',
] as const satisfies readonly AlignmentSessionArtifactRole[];

function requireExactKeys(value: unknown, allowed: readonly string[], label: string): asserts value is Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error(`Invalid ${label}.`);
    }
    const allowedKeys = new Set(allowed);
    if (Object.keys(value).some((key) => !allowedKeys.has(key))) {
        throw new Error(`Unknown field in ${label}.`);
    }
}

function requireClosedKeys(value: unknown, expected: readonly string[], label: string): asserts value is Record<string, unknown> {
    requireExactKeys(value, expected, label);
    if (Object.keys(value).length !== expected.length || expected.some((key) => !Object.prototype.hasOwnProperty.call(value, key))) {
        throw new Error(`Missing field in ${label}.`);
    }
}

const isSha256 = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const isNonNegativeInteger = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const isPositiveInteger = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) > 0;

function validatePresentationArtifact(value: unknown, label: string): asserts value is AlignmentPresentationArtifact {
    requireClosedKeys(value, ['kind', 'url', 'sha256', 'size_bytes', 'mime_type', 'range_capable'], label);
    if (typeof value.kind !== 'string' || !value.kind || typeof value.url !== 'string' || !value.url.startsWith('/')
        || !isSha256(value.sha256) || !isNonNegativeInteger(value.size_bytes)
        || typeof value.mime_type !== 'string' || !value.mime_type || value.range_capable !== true) {
        throw new Error(`Invalid ${label}.`);
    }
}

export function normalizeAlignmentPresentation(value: unknown, expectedJobId: string, expectedSessionId: string): AlignmentPresentation {
    requireClosedKeys(value, ['schema', 'job_id', 'session_id', 'mode', 'state', 'source', 'policy', 'preview', 'coverage', 'manifest'], 'alignment presentation');
    const receipt = value as unknown as AlignmentPresentation;
    if (receipt.schema !== 'bms.ngs.alignment-presentation.v1' || receipt.job_id !== expectedJobId
        || receipt.session_id !== expectedSessionId || !['primary', 'dimer_candidates'].includes(receipt.mode)
        || receipt.state !== 'ready') throw new Error('Invalid alignment presentation identity.');
    requireClosedKeys(receipt.source, ['package_manifest_sha256', 'alignment_sha256', 'alignment_size_bytes', 'alignment_index_sha256', 'alignment_index_size_bytes', 'primary_read_count', 'alignment_record_count'], 'alignment presentation source');
    if (!isSha256(receipt.source.package_manifest_sha256) || !isSha256(receipt.source.alignment_sha256)
        || !isPositiveInteger(receipt.source.alignment_size_bytes) || !isSha256(receipt.source.alignment_index_sha256)
        || !isPositiveInteger(receipt.source.alignment_index_size_bytes) || !isNonNegativeInteger(receipt.source.primary_read_count)
        || !isNonNegativeInteger(receipt.source.alignment_record_count) || receipt.source.alignment_record_count < receipt.source.primary_read_count) {
        throw new Error('Invalid alignment presentation source.');
    }
    requireClosedKeys(receipt.policy, ['id', 'version', 'target_reads', 'max_preview_bytes', 'max_coverage_bins'], 'alignment presentation policy');
    if (typeof receipt.policy.id !== 'string' || !receipt.policy.id || typeof receipt.policy.version !== 'string' || !receipt.policy.version
        || !isPositiveInteger(receipt.policy.target_reads) || !isPositiveInteger(receipt.policy.max_preview_bytes)
        || !isPositiveInteger(receipt.policy.max_coverage_bins)) throw new Error('Invalid alignment presentation policy.');
    requireClosedKeys(receipt.preview, ['kind', 'selected_read_count', 'selected_record_count', 'selected_read_set_sha256', 'forward_count', 'reverse_count', 'bam', 'index'], 'alignment preview');
    if (receipt.preview.kind !== 'primary_read_preview' || !isNonNegativeInteger(receipt.preview.selected_read_count)
        || !isNonNegativeInteger(receipt.preview.selected_record_count) || !isSha256(receipt.preview.selected_read_set_sha256)
        || !isNonNegativeInteger(receipt.preview.forward_count) || !isNonNegativeInteger(receipt.preview.reverse_count)
        || receipt.preview.selected_read_count > receipt.source.primary_read_count
        || receipt.preview.selected_record_count > receipt.source.alignment_record_count
        || receipt.preview.forward_count + receipt.preview.reverse_count !== receipt.preview.selected_read_count) throw new Error('Invalid alignment preview.');
    validatePresentationArtifact(receipt.preview.bam, 'alignment preview BAM');
    validatePresentationArtifact(receipt.preview.index, 'alignment preview index');
    requireClosedKeys(receipt.coverage, ['kind', 'bin_width_bp', 'primary_read_count', 'artifact'], 'alignment coverage');
    if (receipt.coverage.kind !== 'full_source_primary_coverage' || !isPositiveInteger(receipt.coverage.bin_width_bp)
        || receipt.coverage.primary_read_count !== receipt.source.primary_read_count) throw new Error('Invalid alignment coverage.');
    validatePresentationArtifact(receipt.coverage.artifact, 'alignment coverage artifact');
    validatePresentationArtifact(receipt.manifest, 'alignment presentation manifest');
    return receipt;
}

export function buildAlignmentLocusSliceRequest(
    locus: { contig: string; start: number; end: number },
    maxReads = 5000,
): AlignmentLocusSliceRequest {
    const contig = locus.contig.trim();
    if (!contig || !isPositiveInteger(locus.start) || !isPositiveInteger(locus.end) || locus.end < locus.start
        || !isPositiveInteger(maxReads) || maxReads > 5000) throw new Error('Invalid alignment locus slice request.');
    return { contig, start_1based: locus.start, end_1based: locus.end, max_reads: maxReads };
}

export function normalizeAlignmentLocusSlice(value: unknown, expectedJobId: string, expectedSessionId: string): AlignmentLocusSlice {
    requireClosedKeys(value, ['schema', 'job_id', 'session_id', 'slice_id', 'state', 'contig', 'start_1based', 'end_1based', 'overlapping_read_count', 'selected_read_count', 'selected_record_count', 'capped', 'policy', 'bam', 'index', 'manifest'], 'alignment locus slice');
    const slice = value as unknown as AlignmentLocusSlice;
    if (slice.schema !== 'bms.ngs.alignment-locus-slice.v1' || slice.job_id !== expectedJobId
        || slice.session_id !== expectedSessionId || !isSha256(slice.slice_id) || slice.state !== 'ready'
        || typeof slice.contig !== 'string' || !slice.contig || !isPositiveInteger(slice.start_1based)
        || !isPositiveInteger(slice.end_1based) || slice.end_1based < slice.start_1based
        || !isNonNegativeInteger(slice.overlapping_read_count) || !isNonNegativeInteger(slice.selected_read_count)
        || !isNonNegativeInteger(slice.selected_record_count) || typeof slice.capped !== 'boolean'
        || slice.selected_read_count > slice.overlapping_read_count) throw new Error('Invalid alignment locus slice.');
    requireClosedKeys(slice.policy, ['id', 'version', 'max_reads'], 'alignment locus policy');
    if (typeof slice.policy.id !== 'string' || !slice.policy.id || typeof slice.policy.version !== 'string' || !slice.policy.version
        || !isPositiveInteger(slice.policy.max_reads) || slice.selected_read_count > slice.policy.max_reads
        || slice.capped !== (slice.overlapping_read_count > slice.selected_read_count)) throw new Error('Invalid alignment locus policy.');
    validatePresentationArtifact(slice.bam, 'alignment locus BAM');
    validatePresentationArtifact(slice.index, 'alignment locus index');
    validatePresentationArtifact(slice.manifest, 'alignment locus manifest');
    return slice;
}

export async function normalizeAlignmentSessions(payload: AlignmentSessionResponse, expectedJobId: string): Promise<AlignmentSession[]> {
    requireExactKeys(payload, ['schema', 'job_id', 'sessions'], 'alignment session envelope');
    if (!payload || payload.schema !== 'bms.ngs.alignment-session-list.v1'
        || payload.job_id !== expectedJobId || !Array.isArray(payload.sessions)) {
        throw new Error('Alignment session job mismatch.');
    }
    if (payload.sessions.length < 1 || payload.sessions.length > 2
        || payload.sessions[0]?.mode !== 'primary' || payload.sessions[0]?.ready !== true
        || (payload.sessions.length === 2 && payload.sessions[1]?.mode !== 'dimer_candidates')) {
        throw new Error('Invalid alignment session list authority.');
    }
    const artifactPrefix = `/api/jobs/${encodeURIComponent(expectedJobId)}/alignment-artifacts/`;
    const readsPrefix = `/api/jobs/${encodeURIComponent(expectedJobId)}/reads`;
    const sessions = await Promise.all(payload.sessions.map(async (session) => {
        requireExactKeys(session, [
            'schema', 'session_id', 'job_id', 'mode', 'ready', 'unavailable_reason', 'reads_url',
            'sequence_qc_manifest_sha256', 'verification_manifest_sha256', 'artifact_set_sha256',
            'reference', 'artifacts', 'alignment_pair_sha256',
        ], 'alignment session');
        if (session.schema !== 'bms.ngs.alignment-session.v1'
            || session.job_id !== expectedJobId || !/^[0-9a-f]{24}$/.test(session.session_id)
            || !['primary', 'dimer_candidates'].includes(session.mode)) {
            throw new Error('Alignment session job mismatch.');
        }
        if (!session.ready) {
            if (session.mode !== 'dimer_candidates' || !session.unavailable_reason
                || session.reads_url !== null || session.reference !== null
                || session.sequence_qc_manifest_sha256 !== null
                || session.verification_manifest_sha256 !== null
                || session.artifact_set_sha256 !== null
                || session.alignment_pair_sha256 !== null
                || Object.keys(session.artifacts).length !== 0) {
                throw new Error('Invalid unavailable alignment session authority.');
            }
            return session;
        }
        const expectedReadsUrl = `${readsPrefix}?session_id=${session.session_id}`;
        requireExactKeys(session.reference, [
            'contig', 'length_bp', 'topology', 'normalized_sequence_sha256', 'fasta_sha256', 'fai_sha256',
        ], 'alignment reference');
        requireExactKeys(session.artifacts, artifactRoles, 'alignment artifacts');
        if (session.reads_url !== expectedReadsUrl
            || !/^[0-9a-f]{64}$/.test(session.sequence_qc_manifest_sha256)
            || !/^[0-9a-f]{64}$/.test(session.verification_manifest_sha256)
            || !/^[0-9a-f]{64}$/.test(session.artifact_set_sha256)
            || !/^[0-9a-f]{64}$/.test(session.alignment_pair_sha256)
            || !session.reference.contig
            || !Number.isInteger(session.reference.length_bp) || session.reference.length_bp < 1
            || !['linear', 'circular'].includes(session.reference.topology)
            || !/^[0-9a-f]{64}$/.test(session.reference.normalized_sequence_sha256)
            || !/^[0-9a-f]{64}$/.test(session.reference.fasta_sha256)
            || !/^[0-9a-f]{64}$/.test(session.reference.fai_sha256)) {
            throw new Error('Invalid ready alignment session authority.');
        }
        for (const role of artifactRoles) {
            const artifact = session.artifacts[role];
            if (!artifact) continue;
            requireExactKeys(artifact, [
                'artifact_id', 'url', 'sha256', 'size_bytes', 'mime_type', 'range_capable', 'source_manifest_sha256',
            ], 'alignment artifact');
            if (!/^[0-9a-f]{64}$/.test(artifact.artifact_id)
                || artifact.url !== `${artifactPrefix}${artifact.artifact_id}` || artifact.range_capable !== true) {
                throw new Error('Unsafe artifact URL in alignment session.');
            }
            if (!/^[0-9a-f]{64}$/.test(artifact.sha256)
                || !/^[0-9a-f]{64}$/.test(artifact.source_manifest_sha256)
                || artifact.size_bytes < 0) {
                throw new Error('Invalid artifact integrity metadata in alignment session.');
            }
        }
        for (const required of ['alignment', 'alignment_index', 'reference', 'reference_index'] as const) {
            if (!session.artifacts[required]) throw new Error('Incomplete ready alignment session authority.');
        }
        const alignment = session.artifacts.alignment!;
        const alignmentIndex = session.artifacts.alignment_index!;
        const reference = session.artifacts.reference!;
        const referenceIndex = session.artifacts.reference_index!;
        const sourceAuthorities = new Set(
            Object.values(session.artifacts)
                .filter((artifact): artifact is AlignmentSessionArtifact => artifact !== null)
                .map((artifact) => artifact.source_manifest_sha256),
        );
        if (sourceAuthorities.size !== 1
            || (session.mode === 'primary' && !sourceAuthorities.has(session.sequence_qc_manifest_sha256))
            || reference.sha256 !== session.reference.fasta_sha256
            || referenceIndex.sha256 !== session.reference.fai_sha256) {
            throw new Error('Alignment session artifact authority is cross-bound.');
        }
        const pairBytes = new TextEncoder().encode(
            `bms.ngs.alignment-pair.v1\0${JSON.stringify({
                alignment_index_sha256: alignmentIndex.sha256,
                alignment_sha256: alignment.sha256,
            })}`,
        );
        const pairDigest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', pairBytes))]
            .map((byte) => byte.toString(16).padStart(2, '0')).join('');
        if (pairDigest !== session.alignment_pair_sha256) {
            throw new Error('Alignment pair authority is invalid.');
        }
        const sessionArtifactIds = Object.entries(session.artifacts)
            .filter((entry): entry is [string, AlignmentSessionArtifact] => entry[1] !== null)
            .sort(([left], [right]) => left.localeCompare(right))
            .map(([, artifact]) => artifact.artifact_id);
        const sessionSeed = `${expectedJobId}\0${session.mode}\0${sessionArtifactIds.join('\0')}`;
        const sessionDigest = [...new Uint8Array(await crypto.subtle.digest(
            'SHA-256', new TextEncoder().encode(sessionSeed),
        ))].map((byte) => byte.toString(16).padStart(2, '0')).join('');
        if (session.session_id !== sessionDigest.slice(0, 24)) {
            throw new Error('Alignment session identity is invalid.');
        }
        return session;
    }));
    const primary = sessions[0];
    for (const session of sessions.filter((candidate) => candidate.ready)) {
        if (!primary.ready || !session.reference || !primary.reference
            || session.sequence_qc_manifest_sha256 !== primary.sequence_qc_manifest_sha256
            || session.verification_manifest_sha256 !== primary.verification_manifest_sha256
            || session.artifact_set_sha256 !== primary.artifact_set_sha256) {
            throw new Error('Alignment session list package authority is inconsistent.');
        }
    }
    return sessions;
}

export function bindAlignmentSessionsToResultAuthority(
    sessions: AlignmentSession[],
    authority: {
        sequence_qc_manifest_sha256: string;
        construct_verification_manifest_sha256: string;
        artifact_set_sha256: string;
        reference_sequence_sha256: string;
    },
    expectedSessions: Array<{
        session_id: string;
        mode: string;
        ready: boolean;
        unavailable_reason: string | null;
        reference_contig: string | null;
    }>,
): AlignmentSession[] {
    if (sessions.length !== expectedSessions.length) {
        throw new Error('Scientific integrity error: alignment session list differs from the canonical result.');
    }
    for (const [index, session] of sessions.entries()) {
        const expected = expectedSessions[index];
        if (session.session_id !== expected.session_id || session.mode !== expected.mode
            || session.ready !== expected.ready || session.unavailable_reason !== expected.unavailable_reason
            || (session.reference?.contig ?? null) !== expected.reference_contig) {
            throw new Error('Scientific integrity error: alignment session identity differs from the canonical result.');
        }
    }
    for (const session of sessions.filter((candidate) => candidate.ready)) {
        if (!session.reference
            || session.sequence_qc_manifest_sha256 !== authority.sequence_qc_manifest_sha256
            || session.verification_manifest_sha256 !== authority.construct_verification_manifest_sha256
            || session.artifact_set_sha256 !== authority.artifact_set_sha256
            || (session.mode === 'primary' && session.reference.normalized_sequence_sha256 !== authority.reference_sequence_sha256)) {
            throw new Error('Scientific integrity error: alignment session authority differs from the canonical result.');
        }
    }
    return sessions;
}

interface AlignmentAccessRecoveryState {
    epoch: number;
    automaticRotationUsed: boolean;
    rotation: Promise<void> | null;
}

const alignmentAccessRecoveryStateByJob = new Map<string, AlignmentAccessRecoveryState>();

function alignmentAccessRecoveryState(jobId: string): AlignmentAccessRecoveryState {
    let state = alignmentAccessRecoveryStateByJob.get(jobId);
    if (!state) {
        state = { epoch: 0, automaticRotationUsed: false, rotation: null };
        alignmentAccessRecoveryStateByJob.set(jobId, state);
    }
    return state;
}

export function normalizeAlignmentAccessRotation(
    value: unknown,
    expectedJobId: string,
): AlignmentAccessRotationResponse {
    requireExactKeys(value, ['schema', 'job_id', 'rotated', 'scheme', 'rotation_count', 'expires_at'], 'alignment rotation response');
    const response = value as unknown as AlignmentAccessRotationResponse;
    if (response.schema !== 'bms.ngs.rotation-success.v1'
        || response.job_id !== expectedJobId || response.rotated !== true
        || response.scheme !== 'opaque_job_capability_v1'
        || !Number.isInteger(response.rotation_count) || response.rotation_count < 1
        || typeof response.expires_at !== 'string' || Number.isNaN(Date.parse(response.expires_at))) {
        throw new Error('Invalid alignment rotation response.');
    }
    return response;
}

export async function rotateAlignmentAccess(jobId: string): Promise<AlignmentAccessRotationResponse> {
    const response = await api.post<unknown>(
        `/api/jobs/${encodeURIComponent(jobId)}/alignment-access/rotate`,
    );
    const normalized = normalizeAlignmentAccessRotation(response.data, jobId);
    alignmentAccessRecoveryState(jobId).epoch += 1;
    return normalized;
}

export async function revokeAlignmentAccess(jobId: string): Promise<void> {
    alignmentAccessRecoveryStateByJob.delete(jobId);
    await api.delete(`/api/jobs/${encodeURIComponent(jobId)}/alignment-access`);
}

export function disposeAlignmentAccess(jobId: string): void {
    alignmentAccessRecoveryStateByJob.delete(jobId);
    void api.delete(`/api/jobs/${encodeURIComponent(jobId)}/alignment-access`).catch(() => undefined);
}

function isAlignmentAccessRotationConflict(reason: unknown, expectedJobId: string): boolean {
    return isExactNgsError(reason, 409, 'NGS_CAPABILITY_ROTATION_CONFLICT', expectedJobId);
}

export async function withAlignmentAccessRecovery<T>(
    jobId: string,
    operation: () => Promise<T>,
): Promise<T> {
    const state = alignmentAccessRecoveryState(jobId);
    const operationEpoch = state.epoch;
    try {
        return await operation();
    } catch (reason) {
        if (!isAlignmentAccessDenied(reason, jobId)) throw reason;
        if (state.epoch > operationEpoch) return operation();

        let recovery = state.rotation;
        if (!recovery) {
            if (state.automaticRotationUsed) throw reason;
            state.automaticRotationUsed = true;
            recovery = rotateAlignmentAccess(jobId)
                .then(() => undefined)
                .catch((rotationReason) => {
                    if (!isAlignmentAccessRotationConflict(rotationReason, jobId)) throw rotationReason;
                    state.epoch += 1;
                });
            state.rotation = recovery;
            void recovery.finally(() => {
                if (state.rotation === recovery) state.rotation = null;
            }).catch(() => undefined);
        }
        await recovery;
        return operation();
    }
}

export async function fetchAlignmentSessions(jobId: string): Promise<AlignmentSession[]> {
    return withAlignmentAccessRecovery(jobId, async () => {
        const response = await api.get<AlignmentSessionResponse>(
            `/api/jobs/${encodeURIComponent(jobId)}/alignment-sessions`,
        );
        return await normalizeAlignmentSessions(response.data, jobId);
    });
}

export async function fetchAlignmentPresentation(jobId: string, sessionId: string): Promise<AlignmentPresentation> {
    return withAlignmentAccessRecovery(jobId, async () => {
        const response = await api.get<unknown>(
            `/api/jobs/${encodeURIComponent(jobId)}/alignment-sessions/${encodeURIComponent(sessionId)}/presentation`,
        );
        return normalizeAlignmentPresentation(response.data, jobId, sessionId);
    });
}

export async function createAlignmentLocusSlice(
    jobId: string,
    sessionId: string,
    locus: { contig: string; start: number; end: number },
    signal?: AbortSignal,
): Promise<AlignmentLocusSlice> {
    const request = buildAlignmentLocusSliceRequest(locus, 5000);
    const response = await api.post<unknown>(
        `/api/jobs/${encodeURIComponent(jobId)}/alignment-sessions/${encodeURIComponent(sessionId)}/locus-slices`,
        request,
        { signal },
    );
    return normalizeAlignmentLocusSlice(response.data, jobId, sessionId);
}

export async function fetchAlignmentReads(
    jobId: string,
    sessionId: string,
    options: AlignmentReadRequestOptions = {},
): Promise<AlignmentReadPage> {
    const response = await api.get<AlignmentReadPage>(`/api/jobs/${encodeURIComponent(jobId)}/reads`, {
        params: buildAlignmentReadRequestParams({ ...options, sessionId }),
        signal: options.signal,
    });
    return normalizeAlignmentReadPage(response.data);
}

export async function fetchAlignmentRead(
    jobId: string,
    sessionId: string,
    readId: string,
    options: Pick<AlignmentReadRequestOptions, 'contig' | 'start' | 'end' | 'signal'> = {},
): Promise<AlignmentRead> {
    try {
        const response = await api.get<AlignmentRead>(
            `/api/jobs/${encodeURIComponent(jobId)}/reads/${encodeURIComponent(readId)}`,
            {
                params: {
                    session_id: sessionId,
                    contig: options.contig?.trim() || undefined,
                    start: options.start,
                    end: options.end,
                },
                signal: options.signal,
            },
        );
        return response.data;
    } catch (reason) {
        if (isExactNgsError(reason, 409, 'NGS_READ_SCAN_TRUNCATED', jobId, 'read', false)) {
            const data = (reason as { response: { data: { message: string } } }).response.data;
            throw new AlignmentReadScanTruncatedError(data.message);
        }
        throw reason;
    }
}
