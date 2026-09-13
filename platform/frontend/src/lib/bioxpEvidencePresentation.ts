// Presentation only: never use these summaries as admission or completion proof.
import { boundedBioXpText as bounded } from './bioxpErrorPreview';

/** Read only known failure envelopes, not arbitrary telemetry or transport logs.
 * Depth, node and array budgets also bound cyclic/pathological provider data.
 */
export function bioXpProviderFailure(value: unknown): string | null {
    let remaining = 128;
    const visit = (value: unknown, depth: number): string | null => {
        if (--remaining < 0 || depth > 8 || value == null || typeof value !== 'object') return null;
        if (Array.isArray(value)) {
            for (const entry of value.slice(0, 16)) {
                const failure = visit(entry, depth + 1);
                if (failure) return failure;
            }
            return null;
        }
        const record = value as Record<string, unknown>;
        if (typeof record.failure === 'string' && bounded(record.failure)) return bounded(record.failure);
        for (const key of ['body', 'response', 'raw_return_layers', 'detail', 'error', 'stage_receipts', 'child_receipts']) {
            const failure = visit(record[key], depth + 1);
            if (failure) return failure;
        }
        return null;
    };
    return visit(value, 0);
}

export function bioXpReceiptFailureText(receipt: unknown): string | null {
    if (receipt == null || typeof receipt !== 'object' || Array.isArray(receipt)) return null;
    const object = (value: unknown): Record<string, unknown> =>
        value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
    const row = object(receipt);
    const xy = object(row.xy_failure);
    const wait = object(object(xy.controller_failure).wait);
    // Use the retained source wait, not a transport error or an endpoint tolerance.
    // Old saved receipts have a generic HTTP conflict but retain this evidence.
    if (row.status === 'failed' && wait.failure === 'oem_moveToAbs_target_event_timeout' && wait.no24v === false) {
        const terminal = object(xy.terminal_classification);
        const readbacks = object(terminal.readbacks);
        const x = object(object(readbacks.x).position);
        const y = object(object(readbacks.y).position);
        const requested = object(xy.requested);
        const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
        const recorded = terminal.classification === 'failed_with_coherent_stopped_coordinates'
            && x.position_reply_valid === true && y.position_reply_valid === true
            && finite(x.position) && finite(y.position)
            ? `Recorded stopped position: X${x.position}, Y${y.position}${finite(requested.x) && finite(requested.y) ? ` (requested X${requested.x}, Y${requested.y})` : ''}.`
            : 'Stopped position is not established in this receipt.';
        return `Move timeout reported. ${recorded} Past receipt only; not current position or readiness. Source result remains failed.`;
    }
    const failure = bioXpProviderFailure(receipt);
    const error = row.error;
    const record = error != null && typeof error === 'object' ? error as Record<string, unknown> : null;
    const generic = typeof error === 'string' ? error
        : typeof record?.message === 'string' ? record.message
            : typeof record?.code === 'string' ? record.code : null;
    if (failure) return bounded(generic && generic !== failure ? `${failure} — ${bounded(generic)}` : failure);
    return generic ? bounded(generic) : null;
}
