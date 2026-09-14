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

/** Finite native prerequisite grammar; never display arbitrary exception prose. */
export function bioXpDeckReadinessText(reason: string): string {
    const prefix = 'canonical_deck_authority_unavailable';
    if (!reason.startsWith(prefix)) return reason;
    const code = reason.slice(prefix.length + 1);
    const messages: Record<string, string> = {
        deck_bootstrap_semantic_location_unavailable: 'This source branch requires an established previous location and well. Refresh does not initialize them.',
        deck_bootstrap_board_epochs_unavailable: 'Controller board ownership is not established. Refresh deck readiness to query it.',
        deck_bootstrap_branch_state_unavailable: 'Required source branch state is unknown. Refresh does not reconstruct it.',
        deck_bootstrap_latch_or_tip_state_unavailable: 'Required latch or tip state is unknown. Refresh deck readiness to query available observations.',
        deck_gripper_observation_not_authoritative: 'Current gripper confirmation is unavailable. Refresh deck readiness to query it.',
        deck_authority_cache_unavailable: 'Deck readiness has not been observed. Use Refresh deck readiness; it does not move or initialize the robot.',
        deck_authority_cache_stale: 'Deck readiness observation expired. Use Refresh deck readiness; no motion is performed.',
        'source_authority_missing:deck_authority_cached_snapshot': 'A current deck readiness observation is unavailable.',
        deck_controller_positions_not_authoritative: 'Current controller positions are unavailable. Refresh deck readiness to query them.',
        deck_reference_snapshot_not_authoritative: 'Required axis references are unavailable. Refresh does not home the robot.',
        deck_board_epochs_not_authoritative: 'Controller board ownership is not established.',
        deck_board4_not_active: 'Controller board 4 is not active.',
        ownership_generation_changed: 'Robot ownership changed. Obtain current readiness before moving.',
        deck_semantic_generation_epochs_stale: 'Source state belongs to an earlier controller generation.',
        deck_authority_changed_during_collection: 'Robot authority changed during collection. Obtain current readiness before moving.',
        deck_authority_changed_during_observation: 'Robot authority changed during observation. Obtain current readiness before moving.',
        oem_host_latch_status_reader_not_bound: 'The source latch observation owner is unavailable.',
        oem_host_latch_status_observation_failed: 'The source latch observation failed. Refresh deck readiness to query it.',
        deck_latch_observation_failed: 'Latch observation failed. Refresh deck readiness to query it.',
        deck_latch_observation_malformed: 'Latch observation is not valid.',
    };
    const semantic: Record<string, string> = {
        tip_loaded: 'Whether a tip is loaded is unknown.', tip_dirty: 'Source tip cleanliness state is unknown.',
        tip_location: 'Source tip location is unknown.', clean_path: 'This source branch requires known tray clean-path state.',
        location: 'This source branch requires an established previous location.',
        location_revision: 'This source branch requires an established previous location and well.',
        plate_on_gantry: 'Held-plate state required by this source branch is unknown.',
        latch_status: 'Source latch state is unknown.', machine_latch_closed: 'Machine latch-closed state is unknown.',
        ambiguity: 'A previous deck outcome requires reconciliation; do not resubmit.',
        pseudo_z_home: 'Source pseudo-home state is unavailable.',
    };
    for (const [key, message] of Object.entries(semantic)) messages[`deck_semantic_state_not_authoritative:${key}`] = message;
    for (const axis of ['x', 'y', 'z', 'g']) messages[`deck_reference_not_authoritative:${axis}`] = `Required ${axis.toUpperCase()} reference is unavailable. Refresh does not home the robot.`;
    return Object.hasOwn(messages, code) ? messages[code] : 'Source-owned deck readiness is unavailable. Refresh queries readiness; it does not initialize or move the robot.';
}

// Caller completion is not controller arrival. Old receipts are read-only.
export function bioXpReceiptIsMoveTimeoutReport(receipt: unknown): boolean {
    const object = (value: unknown): Record<string, unknown> =>
        value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
    const row = object(receipt);
    if (row.completion_class === 'oem_manual_timeout_report') return true;
    const xy = object(row.xy_failure);
    const wait = object(object(xy.controller_failure).wait);
    return row.status === 'failed'
        && wait.failure === 'oem_moveToAbs_target_event_timeout' && wait.no24v === false
        && object(xy.terminal_classification).classification === 'failed_with_coherent_stopped_coordinates';
}

export function bioXpReceiptStatusText(receipt: unknown, fallback: string): string {
    return bioXpReceiptIsMoveTimeoutReport(receipt) ? 'Move timeout reported' : fallback;
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
    if ((row.status === 'failed' || row.completion_class === 'oem_manual_timeout_report') && wait.failure === 'oem_moveToAbs_target_event_timeout' && wait.no24v === false) {
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
