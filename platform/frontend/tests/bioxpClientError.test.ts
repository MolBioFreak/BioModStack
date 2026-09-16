import assert from 'node:assert/strict';
import test from 'node:test';

import { bioXpErrorText } from '../src/lib/bioxpClient.js';
import { bioXpReceiptFailureText } from '../src/lib/bioxpEvidencePresentation.js';

test('BioXP errors expose backend refusal detail without losing generic fallback', () => {
    assert.equal(
        bioXpErrorText({ response: { data: { detail: 'target is outside trusted networks' } }, message: 'Request failed' }),
        'target is outside trusted networks',
    );
    assert.equal(bioXpErrorText({ message: 'transport unavailable' }), 'transport unavailable');
});

test('BioXP errors safely normalize FastAPI validation detail arrays', () => {
    const error = {
        response: {
            data: {
                detail: [{ loc: ['body', 'idempotency_key'], msg: 'Field required', type: 'missing' }],
            },
        },
        message: 'Request failed with status code 422',
    };
    assert.equal(bioXpErrorText(error), 'body.idempotency_key: Field required');
});

test('BioXP errors and command receipts expose nested robot refusal detail', () => {
    const upstream = 'USB transport is intentionally unbound; use an explicit ownership POST';
    assert.equal(
        bioXpErrorText({
            response: { data: { detail: { http_status: 503, detail: { detail: upstream } } } },
            message: 'Request failed with status code 503',
        }),
        upstream,
    );
    assert.equal(
        // REPAIR-PLAN R1: retain nested failure evidence, not the retired
        // /commands DTO. The mounted history renderer now uses this helper;
        // operator_action_receipt.v1 stores error + response.body.failure.
        bioXpReceiptFailureText({
            command_id: 'cmd-1',
            action_id: 'oem.y.manual_panel_home',
            idempotency_key: 'key-1',
            ownership_generation: 7,
            status: 'failed',
            started_at: '2026-07-30T00:00:00Z',
            finished_at: '2026-07-30T00:00:01Z',
            remote_acknowledged: false,
            physical_effect_verified: false,
            error: 'Robot rejected command with HTTP 503',
            response: { http_status: 503, body: { failure: upstream, ok: false } },
        }),
        `${upstream} — Robot rejected command with HTTP 503`,
    );
});

test('BioXP command receipt text is character bounded for hostile upstream detail', () => {
    const receipt = {
        command_id: 'huge',
        action_id: 'oem.y.manual_panel_home',
        idempotency_key: 'huge',
        ownership_generation: 1,
        status: 'failed',
        started_at: '2026-07-30T00:00:00Z',
        finished_at: '2026-07-30T00:00:01Z',
        remote_acknowledged: false,
        physical_effect_verified: false,
        error: 'Robot rejected command',
        response: { body: { failure: 'X'.repeat(1_000_000) } },
    };
    const text = bioXpReceiptFailureText(receipt);

    assert.ok(text, 'a missing receipt preview must not pass the size bound');
    assert.match(text, /^X/);
    assert.match(text, /\[truncated\]$/);
    assert.ok(text.length <= 4_096);
    assert.equal(receipt.response.body.failure.length, 1_000_000, 'presentation must preserve source evidence');
});
