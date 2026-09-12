import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { parseOntFastqQcResult } from '../src/lib/ontFastqQcResult.js';

const JOB_ID = '31f02bd5-830f-4558-aa78-3873c515de68';
const FIXTURE_URL = new URL('../../api/tests/fixtures/ont_fastq_qc_result_retry3_v1.json', import.meta.url);

function validPayload(): Record<string, unknown> {
    return JSON.parse(readFileSync(FIXTURE_URL, 'utf8')) as Record<string, unknown>;
}

function verification(payload: Record<string, unknown>): Record<string, unknown> {
    return payload.verification as Record<string, unknown>;
}

function checks(payload: Record<string, unknown>): Record<string, Record<string, unknown>> {
    return verification(payload).checks as Record<string, Record<string, unknown>>;
}

test('strict parser accepts path-opaque stage counts from the backend result contract', () => {
    const parsed = parseOntFastqQcResult(validPayload(), JOB_ID);
    assert.deepEqual(parsed.stages[0], { stage: 'fastq_align', status: 'complete', output_count: 5 });
    assert.equal(JSON.stringify(parsed.stages).includes('bms_results/'), false);
});

test('strict parser rejects the old path-bearing stage contract', () => {
    const payload = validPayload();
    (payload.stages as Array<Record<string, unknown>>)[0] = {
        stage: 'fastq_align',
        status: 'complete',
        outputs: ['bms_results/retry3/align/aligned.bam'],
    };
    assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /stage has an unsupported wire shape/u);
});

test('strict parser accepts closed decision checks and rejects synthetic interpretation fields', () => {
    const payload = validPayload();
    const parsed = parseOntFastqQcResult(payload, JOB_ID);

    assert.equal(parsed.verification.checks.expected_reference_screen.status, 'pass');
    assert.equal(
        parsed.verification.checks.expected_reference_screen.metrics.screen_basis,
        'expected_reference_mapping_only',
    );
    assert.equal(
        parsed.verification.checks.expected_reference_screen.metrics.organism_identity_claimed,
        false,
    );
    assert.equal(parsed.verification.checks.read_support.status, 'review');
    assert.equal(parsed.verification.threshold_profile.id, 'plasmid_strict_v1');

    verification(payload).interpretation = 'synthetic browser interpretation';
    assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /verification has an unsupported wire shape/u);
});

test('strict parser consumes the same retry3 fixture as the normative backend schema', () => {
    const parsed = parseOntFastqQcResult(validPayload(), JOB_ID);

    assert.equal(parsed.authority.declared_artifact_count, 36);
    assert.equal(parsed.read_length_histogram.bins.length, 50);
    assert.equal(parsed.coverage.points.length, 1858);
    assert.equal(parsed.coverage.minimum_depth, 24840);
    assert.equal(parsed.coverage.minimum_depth_position_1based, 3516);
    assert.equal(parsed.coverage.depth_basis, 'samtools_depth_aa_default_filters_excludes_deletions_v1');
    assert.equal(parsed.verification.checks.coverage.metrics.minimum_depth, 49126);
    assert.equal(parsed.verification.variants[0]?.record_start_1based, 3515);
    assert.equal(parsed.verification.variants[0]?.record_end_1based, 3516);
    assert.equal(parsed.verification.variants[0]?.affected_start_1based, 3516);
    assert.equal(parsed.verification.variants[0]?.affected_end_1based, 3516);
});

test('parser preserves reduced FAIL identity evidence without inventing measurements', () => {
    const payload = validPayload();
    verification(payload).verdict = 'FAIL';
    verification(payload).reason_codes = ['CONSENSUS_UNAVAILABLE'];
    (verification(payload).summary as Record<string, unknown>).sequence_identity_fraction = null;
    const identity = checks(payload).sequence_identity;
    identity.status = 'fail';
    identity.reason_codes = ['CONSENSUS_UNAVAILABLE'];
    identity.purpose = 'Identity evidence when consensus is unavailable.';
    identity.metrics = { identity_fraction: null, observed_length: 0, reference_length: 5570 };
    identity.units = { identity_fraction: 'fraction', observed_length: 'base_pairs', reference_length: 'base_pairs' };
    const parsed = parseOntFastqQcResult(payload, JOB_ID);
    assert.equal(parsed.verification.verdict, 'FAIL');
    assert.equal(parsed.verification.summary.sequence_identity_fraction, null);
    assert.deepEqual(parsed.verification.checks.sequence_identity, identity);
    assert.equal('edit_cost' in parsed.verification.checks.sequence_identity.metrics, false);
});

test('parser accepts sparse checks, additional metrics and reduced topology provenance', () => {
    const payload = validPayload();
    for (const check of Object.values(checks(payload))) {
        check.status = 'not_evaluated';
        check.metrics = {};
        check.units = {};
    }
    checks(payload).topology.metrics = { state: 'unavailable', evidence_sha256: {}, new_evidence: [null, { available: false }] };
    checks(payload).topology.units = { state: 'categorical' };
    const summary = payload.summary as Record<string, unknown>;
    delete summary.mean_read_length_bp;
    summary.new_measurement = null;
    const parsed = parseOntFastqQcResult(payload, JOB_ID);
    assert.deepEqual(parsed.verification.checks.coverage.metrics, {});
    assert.deepEqual(parsed.verification.checks.topology.metrics, checks(payload).topology.metrics);
    assert.equal(parsed.summary.new_measurement, null);
    assert.equal('mean_read_length_bp' in parsed.summary, false);
});

test('parser retains check status, reason and safe JSON type boundaries', () => {
    const cases: Array<[string, unknown]> = [
        ['status', 'PASS'], ['reason_codes', [42]], ['purpose', {}],
        ['metrics', { identity_fraction: Infinity }], ['metrics', { evidence: undefined }],
        ['units', { identity_fraction: 1 }],
    ];
    for (const [key, value] of cases) {
        const payload = validPayload();
        checks(payload).sequence_identity[key] = value;
        assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /verification checks/u);
    }
    for (const value of [[], {}, Infinity, undefined]) {
        const payload = validPayload();
        (payload.summary as Record<string, unknown>).measurement = value;
        assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /summary.measurement/u);
    }
});

test('parser does not compare filtered histogram counts or independent support sources to input reads', () => {
    const payload = validPayload();
    Object.assign(payload.alignment as object, { total_reads: 1000, reads_passing_length_filter: 921 });
    const histogram = payload.read_length_histogram as Record<string, unknown>;
    histogram.source_row_count = 921;
    (histogram.bins as Array<Record<string, number>>).forEach((bin, index) => { bin.read_count = index === 0 ? 921 : 0; });
    (checks(payload).coverage.metrics as Record<string, unknown>).row_count = 5500;
    (checks(payload).read_support.metrics as Record<string, unknown>).minimum_depth = 12;
    const parsed = parseOntFastqQcResult(payload, JOB_ID);
    assert.equal(parsed.read_length_histogram.source_row_count, 921);
    assert.equal(parsed.alignment.total_reads, 1000);
    assert.equal(parsed.alignment.reads_passing_length_filter, 921);
    assert.equal(parsed.verification.checks.read_support.metrics.minimum_depth, 12);
});

test('zero filtered reads retain an empty histogram and error-only checks', () => {
    const payload = validPayload();
    Object.assign(payload.alignment as object, { total_reads: 1000, reads_passing_length_filter: 0 });
    const histogram = payload.read_length_histogram as Record<string, unknown>;
    histogram.source_row_count = 0;
    (histogram.bins as Array<Record<string, number>>).forEach((bin) => { bin.read_count = 0; });
    verification(payload).verdict = 'FAIL';
    Object.assign(verification(payload).summary as object, {
        observed_length: null, sequence_identity_fraction: null, coverage_fraction: null, unmapped_fraction: null,
    });
    for (const check of Object.values(checks(payload))) {
        Object.assign(check, { status: 'not_evaluated', metrics: { error: 'No filtered reads available' }, units: {} });
    }
    const parsed = parseOntFastqQcResult(payload, JOB_ID);
    assert.equal(parsed.verification.verdict, 'FAIL');
    assert.equal(parsed.read_length_histogram.source_row_count, 0);
    assert.ok(parsed.read_length_histogram.bins.every((bin) => bin.read_count === 0));
    assert.equal(parsed.alignment.total_reads, 1000);
    assert.equal(parsed.alignment.reads_passing_length_filter, 0);
    assert.equal(parsed.verification.summary.observed_length, null);
    assert.deepEqual(parsed.verification.checks.sequence_identity.metrics, { error: 'No filtered reads available' });
});

test('completed jobs retain partial artifacts and missing stage receipts without faking readiness', () => {
    const payload = validPayload();
    const artifact = (payload.artifacts as Array<Record<string, unknown>>).find((item) => item.state !== 'present');
    assert.ok(artifact);
    artifact.state = 'missing_required';
    (payload.stages as Array<Record<string, unknown>>)[0].status = 'missing';
    (payload.stages as Array<Record<string, unknown>>)[0].output_count = 0;
    (payload.authority as Record<string, unknown>).alignment_readiness = 'unavailable';
    for (const session of payload.alignment_sessions as Array<Record<string, unknown>>) {
        Object.assign(session, { ready: false, reference_contig: null, unavailable_reason: 'Alignment unavailable' });
    }
    const parsed = parseOntFastqQcResult(payload, JOB_ID);
    assert.equal(parsed.job.status, 'completed');
    assert.ok(parsed.artifacts.some((item) => item.state === 'present' && item.url));
    assert.ok(parsed.artifacts.some((item) => item.state === 'missing_required' && item.url === null));
    assert.equal(parsed.stages[0].status, 'missing');
    assert.equal(parsed.authority.alignment_readiness, 'unavailable');
});

test('strict parser rejects coverage envelope metadata drift', () => {
    for (const field of ['minimum_depth', 'bucket_width_rows'] as const) {
        const payload = validPayload();
        const coverage = payload.coverage as Record<string, unknown>;
        coverage[field] = Number(coverage[field]) + 1;
        assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /coverage/u);
    }
});

test('strict parser rejects normalized variant interval drift', () => {
    const payload = validPayload();
    const variant = (verification(payload).variants as Array<Record<string, unknown>>)[0];
    variant.affected_start_1based = 3515;
    assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /variant interval/u);
});

test('strict parser rejects open threshold-profile values', () => {
    const payload = validPayload();
    const profile = verification(payload).threshold_profile as Record<string, unknown>;
    (profile.values as Record<string, unknown>).browser_threshold = 1;
    assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /threshold profile values/u);
});

test('strict parser rejects relational and payload-bound drift', () => {
    const cases: Array<[string, (payload: Record<string, unknown>) => void, RegExp]> = [
        ['job identity', (payload) => { (payload.job as Record<string, unknown>).id = 'other-job'; }, /job/u],
        ['schema identity', (payload) => { payload.schema = 'other-schema'; }, /schema/u],
        ['foreign artifact URL', (payload) => {
            const artifact = (payload.artifacts as Array<Record<string, unknown>>).find((item) => item.state === 'present');
            if (artifact) artifact.url = `https://foreign.example/api/jobs/${JOB_ID}/ngs-artifacts/${artifact.artifact_id}`;
        }, /artifact/u],
        ['cross-job artifact URL', (payload) => {
            const artifact = (payload.artifacts as Array<Record<string, unknown>>).find((item) => item.state === 'present');
            if (artifact) artifact.url = `/api/jobs/other-job/ngs-artifacts/${artifact.artifact_id}`;
        }, /artifact/u],
        ['artifact URL', (payload) => {
            const artifact = (payload.artifacts as Array<Record<string, unknown>>)
                .find((item) => item.state === 'present');
            if (artifact) artifact.url = `/api/jobs/${JOB_ID}/ngs-artifacts/${'0'.repeat(64)}`;
        }, /artifact/u],
        ['histogram count', (payload) => {
            const histogram = payload.read_length_histogram as Record<string, unknown>;
            (histogram.bins as Array<Record<string, number>>)[0].read_count += 1;
        }, /histogram count/u],
        ['reference identity', (payload) => {
            const coverage = payload.coverage as Record<string, unknown>;
            for (const point of coverage.points as Array<Record<string, unknown>>) {
                point.reference = 'foreign_contig';
            }
        }, /reference identity/u],
        ['stage order', (payload) => {
            const stages = payload.stages as Array<unknown>;
            [stages[0], stages[1]] = [stages[1], stages[0]];
        }, /canonical stage order/u],
        ['variant count', (payload) => {
            const summary = verification(payload).summary as Record<string, number>;
            summary.variant_count += 1;
        }, /variant count/u],
        ['alignment readiness', (payload) => {
            (payload.authority as Record<string, unknown>).alignment_readiness = 'unavailable';
        }, /alignment readiness/u],
        ['payload size', (payload) => {
            (payload.job as Record<string, unknown>).error_message = 'x'.repeat(300_000);
        }, /response-size bound/u],
    ];
    for (const [name, mutate, message] of cases) {
        const payload = validPayload();
        mutate(payload);
        assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), message, name);
    }
});

test('strict parser rejects artifact enum values outside the backend contract', () => {
    for (const [field, replacement] of [
        ['kind', 'future_kind'],
        ['scientific_role', 'future_role'],
        ['content_disposition', 'future_disposition'],
        ['filename_extension', 'future_extension'],
    ] as const) {
        const payload = validPayload();
        const artifact = (payload.artifacts as Array<Record<string, unknown>>)
            .find((item) => item.state === 'present');
        assert.ok(artifact);
        artifact[field] = replacement;
        assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /artifact/u, field);
    }
});

test('strict parser rejects impossible alignment-session branches', () => {
    const cases: Array<(payload: Record<string, unknown>) => void> = [
        (payload) => {
            const session = (payload.alignment_sessions as Array<Record<string, unknown>>)[0];
            (payload.authority as Record<string, unknown>).alignment_readiness = 'unavailable';
            session.ready = false;
            session.reference_contig = 'eGFP_plasmid';
            session.unavailable_reason = null;
        },
        (payload) => {
            const session = (payload.alignment_sessions as Array<Record<string, unknown>>)[0];
            session.ready = true;
            session.reference_contig = null;
            session.unavailable_reason = 'unexpected';
        },
    ];
    for (const mutate of cases) {
        const payload = validPayload();
        mutate(payload);
        assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /alignment session branch/u);
    }
});
