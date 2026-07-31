import { describe, expect, it } from 'vitest';

import { bioXpOperatorGenerationPayload } from '../../src/lib/bioxpClient';

describe('BioXP operator generation payload', () => {
    it('keeps the BMS connection epoch distinct from the robot ownership epoch', () => {
        expect(bioXpOperatorGenerationPayload(2637337272774657, 2)).toEqual({
            expected_connection_generation: 2637337272774657,
            expected_ownership_generation: 2,
        });
    });

    it('rejects absent or non-positive generation identities', () => {
        expect(() => bioXpOperatorGenerationPayload(0, 2)).toThrow('connection generation');
        expect(() => bioXpOperatorGenerationPayload(7, 0)).toThrow('ownership generation');
    });
});
