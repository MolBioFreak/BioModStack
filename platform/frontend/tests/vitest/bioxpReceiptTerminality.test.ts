import { describe, expect, it } from 'vitest';

import { bioXpErrorPresentation, bioXpReceiptIsNonTerminal } from '../../src/lib/bioxpClient';

describe('BioXP receipt terminality (WP-B)', () => {
    it('treats completed, failed, rejected, blocked, and cleared receipts as terminal', () => {
        for (const status of ['completed', 'failed', 'rejected', 'blocked', 'cleared']) {
            expect(bioXpReceiptIsNonTerminal({ status })).toBe(false);
        }
    });

    it('treats acknowledged, queued, and dispatched receipts as live', () => {
        for (const status of ['acknowledged', 'queued', 'dispatched', 'admission_pending']) {
            expect(bioXpReceiptIsNonTerminal({ status })).toBe(true);
        }
    });

    it('treats absent or untyped statuses as terminal so polling stops', () => {
        expect(bioXpReceiptIsNonTerminal(null)).toBe(false);
        expect(bioXpReceiptIsNonTerminal(undefined)).toBe(false);
        expect(bioXpReceiptIsNonTerminal({})).toBe(false);
        expect(bioXpReceiptIsNonTerminal({ status: 7 })).toBe(false);
    });

    it('preserves bounded structured error status, summary, and raw evidence', () => {
        const presentation = bioXpErrorPresentation({
            response: {
                status: 409,
                data: {
                    detail: {
                        error: 'board_epoch_conflict',
                        expected: { '4': 2 },
                        actual: { '4': 3 },
                        evidence: 'x'.repeat(20_000),
                    },
                },
            },
        });
        expect(presentation.status).toBe(409);
        expect(presentation.summary).toBe('board_epoch_conflict');
        expect(presentation.rawJson).toContain('"expected"');
        expect(presentation.rawJson).toContain('"actual"');
        expect(presentation.rawJson.length).toBeLessThanOrEqual(8_192);
    });
});
