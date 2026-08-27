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

test('strict parser rejects expected-reference screen overclaims', () => {
    for (const [field, replacement] of [
        ['screen_basis', 'taxonomic_contamination_screen'],
        ['organism_identity_claimed', true],
    ] as const) {
        const payload = validPayload();
        const screen = checks(payload).expected_reference_screen;
        (screen.metrics as Record<string, unknown>)[field] = replacement;
        assert.throws(() => parseOntFastqQcResult(payload, JOB_ID), /expected-reference screen/u);
    }
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
        ['completed stage missing', (payload) => {
            (payload.stages as Array<Record<string, unknown>>)[0].status = 'missing';
        }, /completed result stage state/u],
        ['completed stage output count', (payload) => {
            (payload.stages as Array<Record<string, unknown>>)[0].output_count = 4;
        }, /completed result stage state/u],
        ['completed required artifact missing', (payload) => {
            const artifact = (payload.artifacts as Array<Record<string, unknown>>)
                .find((item) => item.state !== 'present');
            if (artifact) artifact.state = 'missing_required';
        }, /missing required artifact/u],
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
