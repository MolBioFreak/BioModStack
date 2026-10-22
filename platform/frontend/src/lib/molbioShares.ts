/**
 * Typed client for the tailnet-only MolBio guest share API (/api/molbio/shares).
 * The link and passcode returned by createMolBioShare are shown exactly once.
 */

type Transport = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

const BASE = '/api/molbio/shares';

export type MolBioShareScope =
    | { kind: 'project'; project_id: string }
    | {
        kind: 'project_domain';
        project_id: string;
        global_experiment_id: string;
        domain_experiment_id: string;
        state_revision_id?: string | null;
    }
    | { kind: 'items'; sequence_ids: string[]; operation_ids: string[] };

export interface MolBioShareConfig {
    configured: boolean;
    max_expiry_seconds: number;
    min_expiry_seconds: number;
    public_port: number | null;
}

export interface MolBioShareAccessEntry {
    [key: string]: unknown;
}

/** Scope summary frozen by the server at creation time. */
export interface MolBioShareScopeSummary {
    kind?: string;
    project_id?: string;
    project_name?: string;
    global_experiment_id?: string;
    domains?: Array<Record<string, unknown>>;
    sequence_ids?: string[];
    operation_ids?: string[];
    counts?: {
        scoped_sequences?: number;
        ancestor_sequences?: number;
        scoped_operations?: number;
        ancestor_operations?: number;
    };
    [key: string]: unknown;
}

export interface MolBioShareGrant {
    grant_id: string;
    label: string;
    creator: string;
    scope: MolBioShareScopeSummary;
    created_at: string | null;
    expires_at: string | null;
    revoked_at: string | null;
    revoked_by: string | null;
    state: string;
    active: boolean;
    bundle_bytes: number | null;
    access_log: MolBioShareAccessEntry[];
}

export interface MolBioShareCreated {
    grant: MolBioShareGrant;
    link: string;
    passcode: string;
    notice: string;
}

export interface MolBioShareList {
    schema: 'bms.molbio.share-list.v1';
    active_count: number;
    items: MolBioShareGrant[];
}

export interface CreateMolBioShareRequest {
    scope: MolBioShareScope;
    expires_in_seconds: number;
    label?: string;
}

export class MolBioShareError extends Error {
    readonly status: number;
    readonly code: string | null;
    constructor(message: string, status: number, code: string | null) {
        super(message);
        this.name = 'MolBioShareError';
        this.status = status;
        this.code = code;
    }
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

function fail(path: string): never {
    throw new MolBioShareError(`Unexpected share API response (${path}).`, 0, 'invalid_response');
}

function errorMessage(status: number, body: unknown): { message: string; code: string | null } {
    const detail = isObject(body) ? body.detail : undefined;
    if (isObject(detail) && typeof detail.message === 'string') {
        return { message: detail.message, code: str(detail.code) };
    }
    if (typeof detail === 'string') return { message: detail, code: null };
    if (Array.isArray(detail)) {
        const first = detail.find(isObject);
        if (first && typeof first.msg === 'string') return { message: first.msg, code: 'validation_error' };
    }
    return { message: `Share request failed (HTTP ${status}).`, code: null };
}

async function request(transport: Transport, path: string, init: RequestInit = {}): Promise<unknown> {
    let response: Response;
    try {
        response = await transport(`${BASE}${path}`, {
            credentials: 'same-origin',
            ...init,
            headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
        });
    } catch {
        throw new MolBioShareError('Share service is unreachable.', 0, 'network_error');
    }
    let body: unknown = null;
    try {
        body = await response.json();
    } catch {
        body = null;
    }
    if (!response.ok) {
        const { message, code } = errorMessage(response.status, body);
        throw new MolBioShareError(message, response.status, code);
    }
    return body;
}

export function parseMolBioShareGrant(value: unknown, path = 'grant'): MolBioShareGrant {
    if (!isObject(value)) fail(path);
    const grantId = str(value.grant_id);
    if (!grantId) fail(`${path}.grant_id`);
    if (typeof value.active !== 'boolean') fail(`${path}.active`);
    return {
        grant_id: grantId,
        label: str(value.label) ?? '',
        creator: str(value.creator) ?? 'operator',
        scope: isObject(value.scope) ? (value.scope as MolBioShareScopeSummary) : {},
        created_at: str(value.created_at),
        expires_at: str(value.expires_at),
        revoked_at: str(value.revoked_at),
        revoked_by: str(value.revoked_by),
        state: str(value.state) ?? (value.active ? 'active' : 'ended'),
        active: value.active,
        bundle_bytes: typeof value.bundle_bytes === 'number' ? value.bundle_bytes : null,
        access_log: Array.isArray(value.access_log) ? value.access_log.filter(isObject) : [],
    };
}

export async function fetchMolBioShareConfig(transport: Transport = fetch): Promise<MolBioShareConfig> {
    const body = await request(transport, '/config');
    if (!isObject(body) || typeof body.configured !== 'boolean'
        || typeof body.max_expiry_seconds !== 'number' || typeof body.min_expiry_seconds !== 'number') {
        fail('config');
    }
    return {
        configured: body.configured,
        max_expiry_seconds: body.max_expiry_seconds,
        min_expiry_seconds: body.min_expiry_seconds,
        public_port: typeof body.public_port === 'number' ? body.public_port : null,
    };
}

export async function listMolBioShares(transport: Transport = fetch): Promise<MolBioShareList> {
    const body = await request(transport, '');
    if (!isObject(body) || !Array.isArray(body.items)) fail('list');
    const items = body.items.map((item, index) => parseMolBioShareGrant(item, `items[${index}]`));
    return {
        schema: 'bms.molbio.share-list.v1',
        active_count: typeof body.active_count === 'number' ? body.active_count : items.filter((g) => g.active).length,
        items,
    };
}

export async function createMolBioShare(payload: CreateMolBioShareRequest, transport: Transport = fetch): Promise<MolBioShareCreated> {
    const body = await request(transport, '', {
        method: 'POST',
        body: JSON.stringify({ scope: payload.scope, expires_in_seconds: payload.expires_in_seconds, label: payload.label ?? '' }),
    });
    if (!isObject(body)) fail('create');
    const link = str(body.link);
    const passcode = str(body.passcode);
    if (!link) fail('create.link');
    if (!passcode) fail('create.passcode');
    return {
        grant: parseMolBioShareGrant(body.grant),
        link,
        passcode,
        notice: str(body.notice) ?? '',
    };
}

export async function revokeMolBioShare(grantId: string, transport: Transport = fetch): Promise<MolBioShareGrant> {
    const body = await request(transport, `/${encodeURIComponent(grantId)}/revoke`, { method: 'POST' });
    if (!isObject(body)) fail('revoke');
    return parseMolBioShareGrant(body.grant);
}

/** Plain-language scope description for lists. */
export function describeShareScope(scope: MolBioShareScopeSummary): string {
    const counts = scope.counts ?? {};
    const seqs = counts.scoped_sequences ?? scope.sequence_ids?.length ?? 0;
    const ops = counts.scoped_operations ?? scope.operation_ids?.length ?? 0;
    const tally = [
        seqs ? `${seqs} sequence${seqs === 1 ? '' : 's'}` : null,
        ops ? `${ops} experiment${ops === 1 ? '' : 's'}` : null,
    ].filter(Boolean).join(', ');
    if (scope.kind === 'project' || scope.kind === 'project_domain') {
        const name = scope.project_name || scope.project_id || 'Project';
        const domain = scope.kind === 'project_domain' && scope.domains?.length === 1
            ? str((scope.domains[0] as Record<string, unknown>).name) ?? str((scope.domains[0] as Record<string, unknown>).display_name)
            : null;
        return `${name}${domain ? ` — ${domain}` : ''}${tally ? ` (${tally})` : ''}`;
    }
    return tally || 'Selected items';
}

/** "3h 12m left" style countdown; returns null once expired. */
export function formatShareRemaining(expiresAt: string | null, nowMs: number): string | null {
    if (!expiresAt) return null;
    const ms = Date.parse(expiresAt) - nowMs;
    if (!Number.isFinite(ms) || ms <= 0) return null;
    const totalMinutes = Math.ceil(ms / 60000);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    if (hours === 0) return `${minutes}m left`;
    return `${hours}h ${minutes}m left`;
}
