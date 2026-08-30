import assert from 'node:assert/strict';
import test from 'node:test';

import { api } from '../src/lib/api.js';
import {
    AlignmentReadScanTruncatedError,
    bindAlignmentSessionsToResultAuthority,
    describeNgsError,
    disposeAlignmentAccess,
    fetchAlignmentRead,
    filterAlignmentReads,
    formatAlignmentReadSummary,
    isAlignmentAccessDenied,
    isAlignmentReadScanTruncatedError,
    normalizeAlignmentSessions,
    normalizeAlignmentAccessRotation,
    withAlignmentAccessRecovery,
    type AlignmentRead,
    type AlignmentSessionArtifact,
    type AlignmentSessionResponse,
} from '../src/lib/ngsAlignmentSession.js';

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function ngsError(code: string, jobId = 'job-recovery-race', status = 403) {
    return {
        response: {
            status,
            data: {
                schema: 'bms.ngs.error.v1', code, message: 'Denied.', job_id: jobId,
                resource: 'rotation', retryable: true,
            },
        },
    };
}

const filteredReads: AlignmentRead[] = [
    { read_id: 'clean', length: 1000, mean_quality: 18, contig: 'ref', start_1based: 1, strand: '+', mapq: 60, cigar: '1000M', flags: 0, unmapped: false, aligned_query_bases: 1000, inserted_bases: 0, deleted_bases: 0, skipped_reference_bases: 0, clipped_bases: 0, edit_distance: 4, reference_substitution_count: 4, reference_substitution_rate: 0.004, aligned_fraction: 1, clipped_fraction: 0, reference_disagreement_rate: 0.004 },
    { read_id: 'substitution-rich', length: 1000, mean_quality: 12, contig: 'ref', start_1based: 1, strand: '+', mapq: 50, cigar: '1000M', flags: 0, unmapped: false, aligned_query_bases: 1000, inserted_bases: 0, deleted_bases: 0, skipped_reference_bases: 0, clipped_bases: 0, edit_distance: 80, reference_substitution_count: 80, reference_substitution_rate: 0.08, aligned_fraction: 1, clipped_fraction: 0, reference_disagreement_rate: 0.08 },
    { read_id: 'large-gap', length: 1000, mean_quality: 16, contig: 'ref', start_1based: 1, strand: '+', mapq: 40, cigar: '400M120N600M', flags: 0, unmapped: false, aligned_query_bases: 1000, inserted_bases: 0, deleted_bases: 0, skipped_reference_bases: 120, clipped_bases: 0, edit_distance: 2, reference_substitution_count: 2, reference_substitution_rate: 0.002, aligned_fraction: 1, clipped_fraction: 0, reference_disagreement_rate: 0.002 },
    { read_id: 'clipped', length: 1000, mean_quality: 17, contig: 'ref', start_1based: 1, strand: '+', mapq: 35, cigar: '150S850M', flags: 0, unmapped: false, aligned_query_bases: 850, inserted_bases: 0, deleted_bases: 0, skipped_reference_bases: 0, clipped_bases: 150, edit_distance: 3, reference_substitution_count: 3, reference_substitution_rate: 3 / 850, aligned_fraction: 0.85, clipped_fraction: 0.15, reference_disagreement_rate: 3 / 850 },
    { read_id: 'indel-only', length: 1000, mean_quality: 17, contig: 'ref', start_1based: 1, strand: '+', mapq: 45, cigar: '900M100I', flags: 0, unmapped: false, aligned_query_bases: 1000, inserted_bases: 100, deleted_bases: 0, skipped_reference_bases: 0, clipped_bases: 0, edit_distance: 100, reference_substitution_count: 0, reference_substitution_rate: 0, aligned_fraction: 1, clipped_fraction: 0, reference_disagreement_rate: 0.1 },
];

test('read presets filter bounded alignment evidence and format operator summaries', () => {
    assert.deepEqual(filterAlignmentReads(filteredReads, 'clean', '').map((read) => read.read_id), ['clean']);
    assert.deepEqual(filterAlignmentReads(filteredReads, 'substitution_rich', '').map((read) => read.read_id), ['substitution-rich']);
    assert.deepEqual(filterAlignmentReads(filteredReads, 'indels_gaps', '').map((read) => read.read_id), ['large-gap', 'indel-only']);
    assert.deepEqual(filterAlignmentReads(filteredReads, 'clipped', '').map((read) => read.read_id), ['clipped']);
    assert.deepEqual(filterAlignmentReads(filteredReads, 'all', 'GAP').map((read) => read.read_id), ['large-gap']);
    assert.equal(formatAlignmentReadSummary(filteredReads[2]), 'Q16.0 · MAPQ 40 · 2 reference substitutions · 120 bp gap · 100% aligned');
});

test('alignment access recovery is offered only for the exact capability-denial code', () => {
    assert.equal(isAlignmentAccessDenied(ngsError('NGS_CAPABILITY_DENIED'), 'job-recovery-race'), true);
    assert.equal(isAlignmentAccessDenied(ngsError('NGS_HIERARCHY_DENIED')), false);
    assert.equal(isAlignmentAccessDenied({ response: { status: 403, data: { ...ngsError('NGS_CAPABILITY_DENIED').response.data, extra: true } } }), false);
    assert.equal(isAlignmentAccessDenied(ngsError('NGS_CAPABILITY_DENIED'), 'other-job'), false);
    assert.equal(isAlignmentAccessDenied({ response: { status: 403 } }), false);
    assert.equal(isAlignmentAccessDenied({ response: { status: 404, data: { detail: 'alignment access denied' } } }), false);
    assert.equal(isAlignmentAccessDenied(new Error('alignment access denied')), false);
});

test('governed NGS failures retain a specific operator-visible category', () => {
    assert.equal(
        describeNgsError(ngsError('NGS_ARTIFACT_INTEGRITY_CONFLICT'), 'fallback'),
        'Integrity error (NGS_ARTIFACT_INTEGRITY_CONFLICT): Denied.',
    );
    assert.equal(
        describeNgsError(ngsError('NGS_ROTATION_CONFLICT', 'job-recovery-race', 409), 'fallback'),
        'Access rotation error (NGS_ROTATION_CONFLICT): Denied.',
    );
    assert.equal(
        describeNgsError(new Error('Result parser error: result.artifacts is invalid'), 'fallback'),
        'Result parser error: result.artifacts is invalid',
    );
    assert.equal(
        describeNgsError({ response: { status: 502, data: { detail: 'bad gateway' } } }, 'Unable to load result'),
        'Network error (HTTP 502): Unable to load result',
    );
});

test('rotation response is a closed exact authority contract', () => {
    const valid = {
        schema: 'bms.ngs.rotation-success.v1', job_id: 'job-a', rotated: true,
        scheme: 'opaque_job_capability_v1', rotation_count: 1, expires_at: '2026-08-21T20:00:00Z',
    };
    assert.deepEqual(normalizeAlignmentAccessRotation(valid, 'job-a'), valid);
    assert.throws(() => normalizeAlignmentAccessRotation({ ...valid, token: 'secret' }, 'job-a'), /unknown/i);
    assert.throws(() => normalizeAlignmentAccessRotation({ ...valid, schema: 'old' }, 'job-a'), /invalid/i);
});


test('late pre-rotation denial retries with the current capability without a second rotation', async () => {
    const rotationGate = deferred<void>();
    const staleRequestGate = deferred<string>();
    const originalPost = api.post;
    let rotations = 0;
    let firstCalls = 0;
    let delayedCalls = 0;
    api.post = (async () => {
        rotations += 1;
        await rotationGate.promise;
        return {
            data: {
                schema: 'bms.ngs.rotation-success.v1',
                job_id: 'job-recovery-race',
                rotated: true,
                scheme: 'opaque_job_capability_v1',
                rotation_count: 1,
                expires_at: '2026-08-21T20:00:00Z',
            },
        };
    }) as typeof api.post;
    const denied = ngsError('NGS_CAPABILITY_DENIED');
    try {
        const first = withAlignmentAccessRecovery('job-recovery-race', async () => {
            firstCalls += 1;
            if (firstCalls === 1) throw denied;
            return 'sessions';
        });
        const delayed = withAlignmentAccessRecovery('job-recovery-race', async () => {
            delayedCalls += 1;
            if (delayedCalls === 1) return staleRequestGate.promise;
            return 'result';
        });
        await Promise.resolve();
        assert.equal(rotations, 1);
        rotationGate.resolve();
        assert.equal(await first, 'sessions');
        staleRequestGate.reject(denied);
        assert.equal(await delayed, 'result');
        assert.equal(rotations, 1);
        assert.equal(delayedCalls, 2);
    } finally {
        api.post = originalPost;
    }
});

test('page disposal revokes the cookie and restores a fresh recovery budget', async () => {
    const originalPost = api.post;
    const originalDelete = api.delete;
    let rotations = 0;
    let revocations = 0;
    api.post = (async () => ({
        data: {
            schema: 'bms.ngs.rotation-success.v1', job_id: 'job-dispose', rotated: true,
            scheme: 'opaque_job_capability_v1', rotation_count: ++rotations,
            expires_at: '2026-08-21T20:00:00Z',
        },
    })) as typeof api.post;
    api.delete = (async () => { revocations += 1; return { data: {} }; }) as typeof api.delete;
    const run = async () => {
        let calls = 0;
        return withAlignmentAccessRecovery('job-dispose', async () => {
            calls += 1;
            if (calls === 1) throw ngsError('NGS_CAPABILITY_DENIED', 'job-dispose');
            return 'ready';
        });
    };
    try {
        assert.equal(await run(), 'ready');
        disposeAlignmentAccess('job-dispose');
        await Promise.resolve();
        assert.equal(await run(), 'ready');
        assert.equal(rotations, 2);
        assert.equal(revocations, 1);
    } finally {
        api.post = originalPost;
        api.delete = originalDelete;
    }
});

function artifact(artifactId: string, sha256: string, sizeBytes: number, mimeType = 'application/octet-stream'): AlignmentSessionArtifact {
    return {
        artifact_id: artifactId,
        url: `/api/jobs/job-a/alignment-artifacts/${artifactId}`,
        sha256,
        size_bytes: sizeBytes,
        mime_type: mimeType,
        range_capable: true,
        source_manifest_sha256: '1'.repeat(64),
    };
}

test('detail scan exhaustion remains a distinct visible state', () => {
    const error = new AlignmentReadScanTruncatedError('scan budget exhausted; absence is not proven');
    assert.equal(isAlignmentReadScanTruncatedError(error), true);
    assert.equal(error.scanTruncated, true);
    assert.match(error.message, /absence is not proven/u);
});

test('typed backend scan truncation becomes the scientist-facing integrity state and rejects open envelopes', async () => {
    const originalGet = api.get;
    const canonical = {
        schema: 'bms.ngs.error.v1', code: 'NGS_READ_SCAN_TRUNCATED',
        message: 'The bounded scan ended before absence could be proved.',
        job_id: 'job-a', resource: 'read', retryable: false,
    };
    let payload: unknown = canonical;
    api.get = (async () => {
        throw { response: { status: 409, data: payload } };
    }) as typeof api.get;
    try {
        await assert.rejects(
            fetchAlignmentRead('job-a', 'session-a', 'read-a'),
            (error: unknown) => isAlignmentReadScanTruncatedError(error),
        );
        for (const malformed of [
            { ...canonical, unexpected: true },
            { ...canonical, message: null },
            { ...canonical, message: '' },
            { ...canonical, retryable: true },
            { ...canonical, resource: 'artifact' },
        ]) {
            payload = malformed;
            await assert.rejects(
                fetchAlignmentRead('job-a', 'session-a', 'read-a'),
                (error: unknown) => !isAlignmentReadScanTruncatedError(error),
            );
        }
    } finally {
        api.get = originalGet;
    }
});

const payload: AlignmentSessionResponse = {
    schema: 'bms.ngs.alignment-session-list.v1',
    job_id: 'job-a',
    sessions: [
        {
            schema: 'bms.ngs.alignment-session.v1',
            session_id: 'dff31f503c17efd74ff97a0f',
            job_id: 'job-a',
            mode: 'primary',
            ready: true,
            unavailable_reason: null,
            reads_url: '/api/jobs/job-a/reads?session_id=dff31f503c17efd74ff97a0f',
            sequence_qc_manifest_sha256: '1'.repeat(64),
            verification_manifest_sha256: '2'.repeat(64),
            artifact_set_sha256: '3'.repeat(64),
            reference: {
                contig: 'plasmid',
                length_bp: 8,
                topology: 'circular',
                normalized_sequence_sha256: '4'.repeat(64),
                fasta_sha256: 'c'.repeat(64),
                fai_sha256: 'd'.repeat(64),
            },
            artifacts: {
                alignment: artifact('a'.repeat(64), 'a'.repeat(64), 4),
                alignment_index: artifact('b'.repeat(64), 'b'.repeat(64), 4),
                reference: artifact('c'.repeat(64), 'c'.repeat(64), 8, 'text/plain'),
                reference_index: artifact('d'.repeat(64), 'd'.repeat(64), 8, 'text/plain'),
            },
            alignment_pair_sha256: '5e3ded98e45517815b150158f6e2e4570fbf6f10108e812fc6b9d949944780a2',
        },
        {
            schema: 'bms.ngs.alignment-session.v1',
            session_id: '2'.repeat(24),
            job_id: 'job-a',
            mode: 'dimer_candidates',
            ready: false,
            unavailable_reason: 'missing alignment index',
            reads_url: null,
            sequence_qc_manifest_sha256: null,
            verification_manifest_sha256: null,
            artifact_set_sha256: null,
            reference: null,
            artifacts: {},
            alignment_pair_sha256: null,
        },
    ],
};

test('normalizes only job-bound opaque session URLs without path inference', async () => {
    const sessions = await normalizeAlignmentSessions(payload, 'job-a');
    assert.equal(sessions.length, 2);
    assert.equal(sessions[0].artifacts.alignment?.url, `/api/jobs/job-a/alignment-artifacts/${'a'.repeat(64)}`);
    assert.equal(sessions[0].artifacts.reference_index?.sha256, 'd'.repeat(64));
    assert.equal(sessions[1].unavailable_reason, 'missing alignment index');
});

test('rejects unknown fields in the closed session and artifact wire contract', async () => {
    const extraSession = structuredClone(payload) as AlignmentSessionResponse & { sessions: Array<Record<string, unknown>> };
    extraSession.sessions[0].legacy_reference_contig = 'plasmid';
    await assert.rejects(normalizeAlignmentSessions(extraSession as AlignmentSessionResponse, 'job-a'), /unknown/i);

    const extraArtifact = structuredClone(payload) as unknown as { sessions: Array<{ artifacts: Record<string, Record<string, unknown>> }> };
    extraArtifact.sessions[0]!.artifacts.alignment!.manifest = 'fastq_qc/qc_manifest.json';
    await assert.rejects(normalizeAlignmentSessions(extraArtifact as unknown as AlignmentSessionResponse, 'job-a'), /unknown/i);
});


test('rejects cross-job session payloads and non-job-scoped artifact URLs', async () => {
    await assert.rejects(normalizeAlignmentSessions({ ...payload, job_id: 'job-b' }, 'job-a'), /job mismatch/i);
    const tampered = structuredClone(payload);
    tampered.sessions[0].artifacts.alignment!.url = '/api/files/stream?path=/tmp/job-b/aligned.bam';
    await assert.rejects(normalizeAlignmentSessions(tampered, 'job-a'), /unsafe artifact URL/i);
});

test('normalizes every authoritative auxiliary artifact role through opaque job URLs', async () => {
    const complete = structuredClone(payload);
    const roles = [
        'coverage_depth',
        'gc_content',
        'position_gradient',
        'gc_zscore',
        'split_read_density',
        'soft_clip_density',
        'junction_hotspots',
        'report',
        'track_config',
    ] as const;
    for (const [index, role] of roles.entries()) {
        const digest = (index + 6).toString(16).repeat(64);
        complete.sessions[0].artifacts[role] = artifact(digest, digest, index + 1);
    }
    const sessionSeed = `job-a\0primary\0${Object.keys(complete.sessions[0].artifacts).sort()
        .map((role) => complete.sessions[0].artifacts[role as keyof typeof complete.sessions[0]['artifacts']]!.artifact_id).join('\0')}`;
    complete.sessions[0].session_id = [...new Uint8Array(await crypto.subtle.digest(
        'SHA-256', new TextEncoder().encode(sessionSeed),
    ))].map((byte) => byte.toString(16).padStart(2, '0')).join('').slice(0, 24);
    complete.sessions[0].reads_url = `/api/jobs/job-a/reads?session_id=${complete.sessions[0].session_id}`;

    const session = (await normalizeAlignmentSessions(complete, 'job-a'))[0];
    assert.deepEqual(
        Object.keys(session.artifacts).filter((role) => roles.includes(role as typeof roles[number])),
        roles,
    );
});

test('rejects cross-bound alignment pair, reference, and manifest authority', async () => {
    for (const mutate of [
        (value: AlignmentSessionResponse) => { value.sessions[0].alignment_pair_sha256 = '0'.repeat(64); },
        (value: AlignmentSessionResponse) => { value.sessions[0].reference!.fasta_sha256 = '0'.repeat(64); },
        (value: AlignmentSessionResponse) => { value.sessions[0].artifacts.alignment!.source_manifest_sha256 = '0'.repeat(64); },
    ]) {
        const tampered = structuredClone(payload);
        mutate(tampered);
        await assert.rejects(normalizeAlignmentSessions(tampered, 'job-a'), /authority/i);
    }
});

test('cross-binds ready sessions to canonical result authority', async () => {
    const sessions = await normalizeAlignmentSessions(payload, 'job-a');
    const authority = {
        sequence_qc_manifest_sha256: '1'.repeat(64),
        construct_verification_manifest_sha256: '2'.repeat(64),
        artifact_set_sha256: '3'.repeat(64),
        reference_sequence_sha256: '4'.repeat(64),
    };
    const expectedSessions = [
        { session_id: 'dff31f503c17efd74ff97a0f', mode: 'primary' as const, ready: true, unavailable_reason: null, reference_contig: 'plasmid' },
        { session_id: '2'.repeat(24), mode: 'dimer_candidates' as const, ready: false, unavailable_reason: 'missing alignment index', reference_contig: null },
    ];
    assert.equal(bindAlignmentSessionsToResultAuthority(sessions, authority, expectedSessions), sessions);
    assert.throws(
        () => bindAlignmentSessionsToResultAuthority(
            sessions, { ...authority, artifact_set_sha256: '0'.repeat(64) }, expectedSessions,
        ),
        /Scientific integrity error/u,
    );
});

test('read request params carry locus filters and expose scan truncation', async () => {
    const module = await import('../src/lib/ngsAlignmentSession.js') as Record<string, unknown>;
    const buildParams = module.buildAlignmentReadRequestParams as ((options: Record<string, unknown>) => Record<string, unknown>) | undefined;
    const normalizePage = module.normalizeAlignmentReadPage as ((page: Record<string, unknown>) => Record<string, unknown>) | undefined;
    assert.equal(typeof buildParams, 'function');
    assert.equal(typeof normalizePage, 'function');
    assert.deepEqual(buildParams!({
        sessionId: 'session-a',
        q: 'target',
        contig: 'plasmid',
        start: 10,
        end: 30,
        limit: 25,
    }), {
        session_id: 'session-a',
        q: 'target',
        cursor: undefined,
        limit: 25,
        include_sequence: false,
        contig: 'plasmid',
        start: 10,
        end: 30,
    });
    const page = normalizePage!({ reads: [], next_cursor: null, limit: 25, sequence_included: false, scan_truncated: true });
    assert.equal(page.scan_truncated, true);
});

test('latest request guard invalidates stale list and detail completions on reset', async () => {
    const module = await import('../src/lib/ngsAlignmentSession.js') as Record<string, unknown>;
    const createGuard = module.createLatestRequestGuard as (() => {
        begin(): number;
        reset(): void;
        isCurrent(token: number): boolean;
    }) | undefined;
    assert.equal(typeof createGuard, 'function');
    const guard = createGuard!();
    const stale = guard.begin();
    guard.reset();
    const current = guard.begin();
    assert.equal(guard.isCurrent(stale), false);
    assert.equal(guard.isCurrent(current), true);
});

test('FASTQ export refuses missing quality instead of fabricating bases', async () => {
    const module = await import('../src/lib/ngsAlignmentSession.js') as Record<string, unknown>;
    const buildFastq = module.buildFastqDownload as ((read: Record<string, unknown>) => string | null) | undefined;
    assert.equal(typeof buildFastq, 'function');
    assert.equal(buildFastq!({ read_id: 'r1', sequence: 'ACGT', quality: null }), null);
    assert.equal(buildFastq!({ read_id: 'r1', sequence: 'ACGT', quality: '##II' }), '@r1\nACGT\n+\n##II\n');
});
