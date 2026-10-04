import { expect, it } from 'vitest';
import { bioXpReceiptFailureText } from '../../src/lib/bioxpEvidencePresentation';
import deckFailure from '../fixtures/bioxp_retained_deck_failure_followup.json';
import wasteFailure from '../fixtures/bioxp_retained_waste_failure_followup.json';

const legacyMessage = 'Action outcome unknown; reconciliation required and retry forbidden';
const currentMessage = 'Action outcome is uncertain; inspect the exact receipt and controller state. No automatic retry was performed.';

it.each([deckFailure, wasteFailure])('presents retained $command_id uncertainty without obsolete recovery policy or changing its native record', receipt => {
    const before = JSON.stringify(receipt);
    const text = bioXpReceiptFailureText(receipt);
    expect(text).toContain(currentMessage);
    expect(text).not.toContain('reconciliation required');
    expect(text).not.toContain('retry forbidden');
    expect(JSON.stringify(receipt)).toBe(before);
    expect(receipt.status).toBe('ambiguous');
    expect(receipt.error.message).toBe(legacyMessage);
    expect(receipt.error.retryable).toBe(false);
});

it('does not reinterpret a different native error code, message or current publication', () => {
    expect(bioXpReceiptFailureText({ error: { code: 'motion_blocked', message: legacyMessage } })).toBe(legacyMessage);
    expect(bioXpReceiptFailureText({ error: { code: 'action_outcome_unknown', message: 'Controller outcome unavailable' } })).toBe('Controller outcome unavailable');
    expect(bioXpReceiptFailureText({ error: { code: 'action_outcome_unknown', message: currentMessage } })).toBe(currentMessage);
    expect(bioXpReceiptFailureText({ error: 'Native controller refusal' })).toBe('Native controller refusal');
    expect(bioXpReceiptFailureText(undefined)).toBeNull();
});
