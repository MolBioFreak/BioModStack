/**
 * Guest-side client for the public read-only share server.
 *
 * The link key lives only in the URL fragment (never sent by the browser as
 * part of a request line) and the passcode only in memory. Both are sent as
 * headers on every API call; the server never sets or reads cookies.
 */

export type Transport = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export const SHARE_BUNDLE_SCHEMA = 'bms.molbio.share-bundle.v1';
const KEY_PATTERN = /^[A-Za-z0-9_-]{43}$/u;
const PASSCODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';

export interface ShareCredentials {
    key: string;
    passcode: string;
}

export interface GuestSequenceRecord {
    name: string | null;
    description: string | null;
    sequence: string | null;
    sequence_type: string | null;
    molecule_strandedness: string | null;
    molecule_orientation: string | null;
    is_circular: boolean | null;
    length: number | null;
    features: unknown[] | null;
    primers: unknown[] | null;
    analysis_tracks: unknown[] | null;
    organism: string | null;
    accession: string | null;
    operation: string | null;
    operation_params: Record<string, unknown> | null;
    gc_content: number | null;
    source_file: string | null;
}

export interface GuestSequence {
    sequence_id: string;
    revision_id: string;
    revision_number: number | null;
    role: 'scoped' | 'ancestor';
    change_kind: string | null;
    content_sha256: string | null;
    content_length: number | null;
    created_at: string | null;
    derived_by_operation_id: string | null;
    parent_sequence_id: string | null;
    project_tags: string[];
    project_notes: string;
    record: GuestSequenceRecord;
}

export interface GuestOperationEdge {
    revision_id: string;
    role: string;
    position: number;
}

export interface GuestOperation {
    operation_id: string;
    role: 'scoped' | 'ancestor';
    operation_kind: string;
    title: string;
    status: string | null;
    implementation: string | null;
    implementation_version: string | null;
    parameters: Record<string, unknown>;
    warnings: unknown[];
    created_at: string | null;
    inputs: GuestOperationEdge[];
    outputs: GuestOperationEdge[];
}

export interface GuestBundle {
    schema: typeof SHARE_BUNDLE_SCHEMA;
    title: string;
    scope: Record<string, unknown>;
    sequences: GuestSequence[];
    operations: GuestOperation[];
}

export interface GuestShare {
    grant: { grant_id: string; label: string; expires_at: string | null; created_at: string | null };
    bundle: GuestBundle;
}

export type GuestErrorKind = 'invalid_link' | 'wrong_passcode' | 'blocked' | 'locked' | 'ended' | 'rate_limited' | 'unavailable';

export class GuestShareError extends Error {
    readonly kind: GuestErrorKind;
    readonly status: number;
    constructor(kind: GuestErrorKind, message: string, status = 0) {
        super(message);
        this.name = 'GuestShareError';
        this.kind = kind;
        this.status = status;
    }
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** Read the link key from `#<key>`; null when absent or malformed. */
export function linkKeyFromHash(hash: string): string | null {
    const key = hash.replace(/^#/u, '').trim();
    return KEY_PATTERN.test(key) ? key : null;
}

/** Uppercase, drop separators/spaces; null unless it is a well-formed 8-character passcode. */
export function normalizeGuestPasscode(value: string): string | null {
    const cleaned = value.toUpperCase().replace(/[\s-]/gu, '');
    if (cleaned.length !== 8 || [...cleaned].some((ch) => !PASSCODE_ALPHABET.includes(ch))) return null;
    return cleaned;
}

/** Transport that attaches the share credentials and never sends cookies. */
export function shareTransport(credentials: ShareCredentials, base: Transport = fetch): Transport {
    return (input, init = {}) => {
        const headers = new Headers(init.headers);
        headers.set('X-BMS-Share-Key', credentials.key);
        headers.set('X-BMS-Share-Passcode', credentials.passcode);
        return base(input, { ...init, headers, credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer' });
    };
}

function errorFor(status: number, message: string | null): GuestShareError {
    if (status === 401) return new GuestShareError('wrong_passcode', message || 'That passcode is not correct.', status);
    if (status === 403) return new GuestShareError('blocked', 'Access from your network is blocked after too many failed attempts.', status);
    if (status === 404) return new GuestShareError('invalid_link', 'This share link is not valid.', status);
    if (status === 410) return new GuestShareError('ended', 'This share has expired or was revoked.', status);
    if (status === 423) return new GuestShareError('locked', 'This share is locked after too many incorrect passcodes. Ask the sender for a new link.', status);
    if (status === 429) return new GuestShareError('rate_limited', 'Too many requests. Wait a minute and try again.', status);
    return new GuestShareError('unavailable', message || 'The share is unavailable right now.', status);
}

function parseSequence(value: unknown, index: number): GuestSequence {
    if (!isObject(value) || !isObject(value.record)) throw new GuestShareError('unavailable', `Shared sequence ${index} is malformed.`);
    const sequenceId = str(value.sequence_id);
    const revisionId = str(value.revision_id);
    if (!sequenceId || !revisionId) throw new GuestShareError('unavailable', `Shared sequence ${index} has no identity.`);
    const r = value.record;
    return {
        sequence_id: sequenceId,
        revision_id: revisionId,
        revision_number: num(value.revision_number),
        role: value.role === 'ancestor' ? 'ancestor' : 'scoped',
        change_kind: str(value.change_kind),
        content_sha256: str(value.content_sha256),
        content_length: num(value.content_length),
        created_at: str(value.created_at),
        derived_by_operation_id: str(value.derived_by_operation_id),
        parent_sequence_id: str(value.parent_sequence_id),
        project_tags: Array.isArray(value.project_tags) ? value.project_tags.filter((t): t is string => typeof t === 'string') : [],
        project_notes: str(value.project_notes) ?? '',
        record: {
            name: str(r.name),
            description: str(r.description),
            sequence: str(r.sequence),
            sequence_type: str(r.sequence_type),
            molecule_strandedness: str(r.molecule_strandedness),
            molecule_orientation: str(r.molecule_orientation),
            is_circular: typeof r.is_circular === 'boolean' ? r.is_circular : null,
            length: num(r.length),
            features: Array.isArray(r.features) ? r.features : null,
            primers: Array.isArray(r.primers) ? r.primers : null,
            analysis_tracks: Array.isArray(r.analysis_tracks) ? r.analysis_tracks : null,
            organism: str(r.organism),
            accession: str(r.accession),
            operation: str(r.operation),
            operation_params: isObject(r.operation_params) ? r.operation_params : null,
            gc_content: num(r.gc_content),
            source_file: str(r.source_file),
        },
    };
}

function parseEdges(value: unknown): GuestOperationEdge[] {
    if (!Array.isArray(value)) return [];
    return value.filter(isObject).flatMap((edge) => {
        const revisionId = str(edge.revision_id);
        return revisionId ? [{ revision_id: revisionId, role: str(edge.role) ?? '', position: num(edge.position) ?? 0 }] : [];
    });
}

function parseOperation(value: unknown, index: number): GuestOperation {
    if (!isObject(value)) throw new GuestShareError('unavailable', `Shared experiment ${index} is malformed.`);
    const operationId = str(value.operation_id);
    if (!operationId) throw new GuestShareError('unavailable', `Shared experiment ${index} has no identity.`);
    const kind = str(value.operation_kind) ?? 'operation';
    return {
        operation_id: operationId,
        role: value.role === 'ancestor' ? 'ancestor' : 'scoped',
        operation_kind: kind,
        title: str(value.title) ?? kind,
        status: str(value.status),
        implementation: str(value.implementation),
        implementation_version: str(value.implementation_version),
        parameters: isObject(value.parameters) ? value.parameters : {},
        warnings: Array.isArray(value.warnings) ? value.warnings : [],
        created_at: str(value.created_at),
        inputs: parseEdges(value.inputs),
        outputs: parseEdges(value.outputs),
    };
}

export function parseGuestShare(value: unknown): GuestShare {
    if (!isObject(value) || !isObject(value.grant) || !isObject(value.bundle)) {
        throw new GuestShareError('unavailable', 'The share response is malformed.');
    }
    const b = value.bundle;
    if (b.schema !== SHARE_BUNDLE_SCHEMA) throw new GuestShareError('unavailable', 'This share uses an unsupported format.');
    if (!Array.isArray(b.sequences) || !Array.isArray(b.operations)) throw new GuestShareError('unavailable', 'The share response is malformed.');
    const g = value.grant;
    return {
        grant: {
            grant_id: str(g.grant_id) ?? '',
            label: str(g.label) ?? '',
            expires_at: str(g.expires_at),
            created_at: str(g.created_at),
        },
        bundle: {
            schema: SHARE_BUNDLE_SCHEMA,
            title: str(b.title) ?? 'Shared MolBio items',
            scope: isObject(b.scope) ? b.scope : {},
            sequences: b.sequences.map(parseSequence),
            operations: b.operations.map(parseOperation),
        },
    };
}

export async function fetchGuestShare(credentials: ShareCredentials, base: Transport = fetch, signal?: AbortSignal): Promise<GuestShare> {
    let response: Response;
    try {
        response = await shareTransport(credentials, base)('/api/share/bundle', { signal, headers: { Accept: 'application/json' } });
    } catch (error) {
        if (signal?.aborted) throw error;
        throw new GuestShareError('unavailable', 'Could not reach the share server.');
    }
    let body: unknown = null;
    try {
        body = await response.json();
    } catch {
        body = null;
    }
    if (!response.ok) {
        const detail = isObject(body) && isObject(body.detail) ? str(body.detail.message) : null;
        throw errorFor(response.status, detail);
    }
    return parseGuestShare(body);
}

/** "3h 12m left" / "5m left"; null once expired. */
export function formatGuestRemaining(expiresAt: string | null, nowMs: number): string | null {
    if (!expiresAt) return null;
    const ms = Date.parse(expiresAt) - nowMs;
    if (!Number.isFinite(ms) || ms <= 0) return null;
    const minutes = Math.ceil(ms / 60000);
    const hours = Math.floor(minutes / 60);
    return hours === 0 ? `${minutes % 60}m left` : `${hours}h ${minutes % 60}m left`;
}
