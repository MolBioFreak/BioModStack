import type { BioXpOperatorReceiptV2, BioXpOperatorReceiptDetailV2 } from '../lib/bioxpClient';
import { bioXpReceiptFailureText } from '../lib/bioxpEvidencePresentation';

/** Selected retained outcomes, not a raw telemetry/transport dump. */
export function BioXpReceiptEvidence({ receipt }: { receipt: BioXpOperatorReceiptV2 & Partial<BioXpOperatorReceiptDetailV2> }) {
    return <div className="mt-2 text-xs text-slate-300">
        <p>{receipt.command_id} · {receipt.status} · {receipt.completion_class ?? 'completion not reported'}</p>
        <p>Physical effect verified: {String(receipt.physical_effect_verified)}</p>
        {bioXpReceiptFailureText(receipt) && <p>{bioXpReceiptFailureText(receipt)}</p>}
        {receipt.deck_movement && <p>Source branch: {receipt.deck_movement.source_branch ?? 'unknown'} · Delivery attempted: {String(receipt.deck_movement.delivery_attempted ?? 'unknown')} · Controller completion: {String(receipt.deck_movement.controller_completion_verified ?? 'unknown')} · Semantic state committed: {String(receipt.deck_movement.semantic_state_committed ?? 'unknown')}</p>}
        {(receipt.child_receipts ?? []).map(child => <p key={child.command_id}>{child.action_id} · {child.command_id} · {child.status} · {child.completion_class ?? 'completion not reported'} · {bioXpReceiptFailureText(child)}</p>)}
    </div>;
}
