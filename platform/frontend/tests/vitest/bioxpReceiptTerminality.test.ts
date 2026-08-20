import { describe, expect, it } from 'vitest';

import { bioXpReceiptIsNonTerminal } from '../../src/lib/bioxpClient';

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
});
