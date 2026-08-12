import { api } from './api.js';

export type AlignmentSessionMode = 'primary' | 'dimer_candidates';

export interface AlignmentSessionArtifact {
    artifact_id: string;
    url: string;
    sha256: string;
    size_bytes: number;
    mime_type: string;
    range_capable: boolean;
    declared_sha256: string | null;
    declared_size_bytes: number | null;
    observed_sha256: string;
    observed_size_bytes: number;
    integrity_valid: boolean;
    manifest: string;
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

export interface AlignmentSession {
    session_id: string;
    job_id: string;
    mode: AlignmentSessionMode;
    reference_contig: string | null;
    ready: boolean;
    unavailable_reason: string | null;
    reads_url: string;
    artifacts: Partial<Record<AlignmentSessionArtifactRole, AlignmentSessionArtifact>>;
}

export interface AlignmentSessionResponse {
    job_id: string;
    sessions: AlignmentSession[];
}

export interface AlignmentAccessRotationResponse {
    job_id: string;
    rotated: true;
    scheme: 'opaque_job_capability_v1';
    rotation_count: number;
}

export function isAlignmentAccessDenied(reason: unknown): boolean {
    return (reason as { response?: { status?: unknown } } | null)?.response?.status === 403;
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

export function normalizeAlignmentSessions(payload: AlignmentSessionResponse, expectedJobId: string): AlignmentSession[] {
    if (!payload || payload.job_id !== expectedJobId || !Array.isArray(payload.sessions)) {
        throw new Error('Alignment session job mismatch.');
    }
    const artifactPrefix = `/api/jobs/${encodeURIComponent(expectedJobId)}/alignment-artifacts/`;
    const readsPrefix = `/api/jobs/${encodeURIComponent(expectedJobId)}/reads`;
    return payload.sessions.map((session) => {
        if (session.job_id !== expectedJobId || !['primary', 'dimer_candidates'].includes(session.mode)) {
            throw new Error('Alignment session job mismatch.');
        }
        if (!session.reads_url.startsWith(readsPrefix)) {
            throw new Error('Unsafe reads URL in alignment session.');
        }
        for (const role of artifactRoles) {
            const artifact = session.artifacts[role];
            if (!artifact) continue;
            if (!artifact.url.startsWith(artifactPrefix) || !artifact.range_capable) {
                throw new Error('Unsafe artifact URL in alignment session.');
            }
            if (!/^[0-9a-f]+$/i.test(artifact.sha256) || artifact.size_bytes < 0) {
                throw new Error('Invalid artifact integrity metadata in alignment session.');
            }
        }
        return session;
    });
}

export async function fetchAlignmentSessions(jobId: string): Promise<AlignmentSession[]> {
    const response = await api.get<AlignmentSessionResponse>(
        `/api/jobs/${encodeURIComponent(jobId)}/alignment-sessions`,
    );
    return normalizeAlignmentSessions(response.data, jobId);
}

export async function rotateAlignmentAccess(jobId: string): Promise<AlignmentAccessRotationResponse> {
    const response = await api.post<AlignmentAccessRotationResponse>(
        `/api/jobs/${encodeURIComponent(jobId)}/alignment-access/rotate`,
    );
    return response.data;
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
        const response = (reason as { response?: { status?: number; data?: { detail?: { scan_truncated?: unknown; message?: unknown } } } }).response;
        const detail = response?.data?.detail;
        if (response?.status === 409 && detail?.scan_truncated === true) {
            throw new AlignmentReadScanTruncatedError(
                typeof detail.message === 'string' ? detail.message : undefined,
            );
        }
        throw reason;
    }
}
