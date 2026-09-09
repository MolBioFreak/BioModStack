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
    const failure = bioXpProviderFailure(receipt);
    const error = (receipt as Record<string, unknown>).error;
    const record = error != null && typeof error === 'object' ? error as Record<string, unknown> : null;
    const generic = typeof error === 'string' ? error
        : typeof record?.message === 'string' ? record.message
            : typeof record?.code === 'string' ? record.code : null;
    if (failure) return bounded(generic && generic !== failure ? `${failure} — ${bounded(generic)}` : failure);
    return generic ? bounded(generic) : null;
}
