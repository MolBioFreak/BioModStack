import assert from 'node:assert/strict';
import test from 'node:test';

import { bioXpErrorText } from '../src/lib/bioxpClient.js';

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
