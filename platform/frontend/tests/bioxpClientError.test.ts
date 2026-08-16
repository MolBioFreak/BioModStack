import assert from 'node:assert/strict';
import test from 'node:test';

import { bioXpCommandRecordText, bioXpErrorText } from '../src/lib/bioxpClient.js';

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
        bioXpCommandRecordText({
            command_id: 'cmd-1',
            command: 'recover_motion_non_homing',
            idempotency_key: 'key-1',
            generation: 7,
            status: 'delivery_failed',
            started_at: '2026-07-30T00:00:00Z',
            finished_at: '2026-07-30T00:00:01Z',
            remote_acknowledged: false,
            physical_effect_verified: false,
            detail: 'Robot rejected command with HTTP 503',
            handler_response: { http_status: 503, detail: { detail: upstream } },
        }),
        `Robot rejected command with HTTP 503 — ${upstream}`,
    );
});

test('BioXP command receipt text is character bounded for hostile upstream detail', () => {
    const text = bioXpCommandRecordText({
        command_id: 'huge',
        command: 'recover_motion_non_homing',
        idempotency_key: 'huge',
        generation: 1,
        status: 'delivery_failed',
        started_at: '2026-07-30T00:00:00Z',
        finished_at: '2026-07-30T00:00:01Z',
        remote_acknowledged: false,
        physical_effect_verified: false,
        detail: 'Robot rejected command',
        handler_response: { detail: { detail: 'X'.repeat(1_000_000) } },
    });

    assert.ok(text.length <= 4_096);
});
