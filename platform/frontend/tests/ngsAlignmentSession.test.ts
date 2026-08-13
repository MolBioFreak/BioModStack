import assert from 'node:assert/strict';
import test from 'node:test';

import {
    AlignmentReadScanTruncatedError,
    isAlignmentAccessDenied,
    isAlignmentReadScanTruncatedError,
    normalizeAlignmentSessions,
    type AlignmentSessionArtifact,
    type AlignmentSessionResponse,
} from '../src/lib/ngsAlignmentSession.js';

test('alignment access recovery is offered only for the exact capability-denial response', () => {
    assert.equal(isAlignmentAccessDenied({ response: { status: 403, data: { detail: 'alignment access denied' } } }), true);
    assert.equal(isAlignmentAccessDenied({ response: { status: 403, data: { detail: 'alignment resource unavailable' } } }), false);
    assert.equal(isAlignmentAccessDenied({ response: { status: 403 } }), false);
    assert.equal(isAlignmentAccessDenied({ response: { status: 404, data: { detail: 'alignment access denied' } } }), false);
    assert.equal(isAlignmentAccessDenied(new Error('alignment access denied')), false);
});

function artifact(artifactId: string, sha256: string, sizeBytes: number, mimeType = 'application/octet-stream'): AlignmentSessionArtifact {
    return {
        artifact_id: artifactId,
        url: `/api/jobs/job-a/alignment-artifacts/${artifactId}`,
        sha256,
        size_bytes: sizeBytes,
        mime_type: mimeType,
        range_capable: true,
        declared_sha256: sha256,
        declared_size_bytes: sizeBytes,
        observed_sha256: sha256,
        observed_size_bytes: sizeBytes,
        integrity_valid: true,
        manifest: 'fastq_qc/qc_manifest.json',
    };
}

test('detail scan exhaustion remains a distinct visible state', () => {
    const error = new AlignmentReadScanTruncatedError('scan budget exhausted; absence is not proven');
    assert.equal(isAlignmentReadScanTruncatedError(error), true);
    assert.equal(error.scanTruncated, true);
    assert.match(error.message, /absence is not proven/u);
});

const payload: AlignmentSessionResponse = {
    job_id: 'job-a',
    sessions: [
        {
            session_id: 'primary-session',
            job_id: 'job-a',
            mode: 'primary',
            reference_contig: 'plasmid',
            ready: true,
            unavailable_reason: null,
            reads_url: '/api/jobs/job-a/reads?session_id=primary-session',
            artifacts: {
                alignment: artifact('bam', 'a', 4),
                alignment_index: artifact('bai', 'b', 4),
                reference: artifact('ref', 'c', 8, 'text/plain'),
            },
        },
        {
            session_id: 'dimer-session',
            job_id: 'job-a',
            mode: 'dimer_candidates',
            reference_contig: null,
            ready: false,
            unavailable_reason: 'missing alignment index',
            reads_url: '/api/jobs/job-a/reads?session_id=dimer-session',
            artifacts: {},
        },
    ],
};

test('normalizes only job-bound opaque session URLs without path inference', () => {
    const sessions = normalizeAlignmentSessions(payload, 'job-a');
    assert.equal(sessions.length, 2);
    assert.equal(sessions[0].artifacts.alignment?.url, '/api/jobs/job-a/alignment-artifacts/bam');
    assert.equal(sessions[0].artifacts.reference_index, undefined);
    assert.equal(sessions[1].unavailable_reason, 'missing alignment index');
});

test('rejects cross-job session payloads and non-job-scoped artifact URLs', () => {
    assert.throws(() => normalizeAlignmentSessions({ ...payload, job_id: 'job-b' }, 'job-a'), /job mismatch/i);
    const tampered = structuredClone(payload);
    tampered.sessions[0].artifacts.alignment!.url = '/api/files/stream?path=/tmp/job-b/aligned.bam';
    assert.throws(() => normalizeAlignmentSessions(tampered, 'job-a'), /unsafe artifact URL/i);
});

test('normalizes every authoritative auxiliary artifact role through opaque job URLs', () => {
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
        complete.sessions[0].artifacts[role] = artifact(role, (index + 1).toString(16), index + 1);
    }

    const session = normalizeAlignmentSessions(complete, 'job-a')[0];
    assert.deepEqual(
        Object.keys(session.artifacts).filter((role) => roles.includes(role as typeof roles[number])),
        roles,
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
