import assert from 'node:assert/strict';
import test from 'node:test';
import { bioXpProviderFailure, bioXpReceiptFailureText } from '../src/lib/bioxpEvidencePresentation.js';
import { bioXpErrorText } from '../src/lib/bioxpClient.js';

test('provider failures preserve transport context without inferring completion', () => {
    const receipt = { status: 'failed', error: 'robot route returned HTTP 200', response: { body: { failure: 'motor timed out', ok: false } } };
    assert.equal(bioXpReceiptFailureText(receipt), 'motor timed out — robot route returned HTTP 200');
    assert.equal(bioXpReceiptFailureText({ status: 'completed', physical_effect_verified: false }), null);
    assert.equal(bioXpReceiptFailureText({ error: { code: 'unavailable', message: 'Provider unavailable' } }), 'Provider unavailable');
    assert.equal(bioXpProviderFailure({ stage_receipts: [{ response: { body: { failure: 'stage failed' } } }] }), 'stage failed');
    assert.equal(bioXpProviderFailure({ telemetry: { failure: 'unrelated diagnostic' } }), null);
});

test('failure summaries bound work, text, cycles and missing data', () => {
    assert.ok(bioXpReceiptFailureText({ response: { body: { failure: 'X'.repeat(1_000_000) } } })!.length <= 2048);
    const cycle: Record<string, unknown> = {}; cycle.response = cycle;
    assert.equal(bioXpProviderFailure(cycle), null);
    assert.equal(bioXpProviderFailure({ child_receipts: Array(10_000).fill(cycle) }), null);
    for (const value of [null, undefined, [], 200, true]) assert.equal(bioXpReceiptFailureText(value), null);
    assert.equal(bioXpErrorText({ message: 'transport unavailable' }), 'transport unavailable');
    assert.equal(bioXpErrorText({ response: { data: { detail: [{ loc: ['body', 'key'], msg: 'Field required' }] } } }), 'body.key: Field required');
});

test('BioXP HTTP 200 wrappers expose the provider failure rather than transport success', () => {
    assert.match(bioXpErrorText({ response: { status: 502, data: { detail: {
        error: 'robot route returned HTTP 200',
        response: { http_status: 200, body: { ok: false, failure: 'RuntimeError: Reach GZ position time out! board=4; axis=0; position=10000' } },
    } } } }), /RuntimeError: Reach GZ position time out!/);
});
