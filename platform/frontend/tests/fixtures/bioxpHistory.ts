// Test-fixture projection only. Runtime consumers never convert native receipts.
import type { BioXpOperatorHistoryReceipt } from '../../src/lib/bioxpClient';

const epoch = (value: unknown): number | null => {
    if (value == null) return null;
    const n = Number(value);
    if (Number.isFinite(n)) return n;
    const date = typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? Date.parse(value) / 1000 : NaN;
    return Number.isFinite(date) ? date : null;
};
export function historyItem(row: Record<string, unknown>): BioXpOperatorHistoryReceipt {
    const recorded = String(row.status ?? 'failed');
    const status = ({ acknowledged: 'queued', admission_pending: 'queued', outcome_unknown: 'ambiguous', reconciliation_required: 'ambiguous', blocked: 'rejected', stopped: 'interrupted', aborted: 'interrupted', cancelled: 'cleared' } as Record<string, string>)[recorded] ?? recorded;
    const commandId = String(row.command_id ?? row.receipt_id ?? 'fixture-unindexed');
    const accepted = epoch(row.accepted_at ?? row.queued_at ?? row.started_at) ?? 0;
    return {
        schema_version: 'bioxp.operator_action_receipt.v2', command_id: commandId,
        action_id: String(row.action_id ?? row.operation ?? 'fixture-recorded'),
        status: status as BioXpOperatorHistoryReceipt['status'],
        terminal: !['queued', 'dispatched', 'issued_pending', 'interrupting'].includes(status),
        sequence: Number(row.sequence ?? 1), method_id: null,
        ownership_generation: Number(row.ownership_generation ?? 0), expected_board_epoch_by_board: {}, state_version: 1,
        status_path: `/operator/v2/actions/receipts/${commandId}`,
        accepted_at: accepted, queued_at: epoch(row.queued_at) ?? accepted,
        dispatched_at: epoch(row.dispatched_at), finished_at: epoch(row.finished_at),
        terminal_receipt_id: null, completion_class: null, physical_effect_verified: row.physical_effect_verified === true,
        error: ['ambiguous', 'failed', 'rejected'].includes(status) ? { code: status === 'ambiguous' ? 'action_outcome_unknown' : 'route_application_failed', message: typeof row.error === 'string' ? row.error : 'Recorded action did not complete.', retryable: false } : null,
        history: { source: row.source === 'legacy_operator_plane' ? 'retained' : 'direct',
            source_schema: typeof row.schema_version === 'string' ? row.schema_version : null,
            recorded_status: recorded, remote_acknowledged: typeof row.remote_acknowledged === 'boolean' ? row.remote_acknowledged : null,
            controller_acknowledged: typeof row.controller_acknowledged === 'boolean' ? row.controller_acknowledged : null,
            controller_terminal_state_verified: typeof row.controller_terminal_state_verified === 'boolean' ? row.controller_terminal_state_verified : null,
            machine_assessment: (row.machine_assessment ?? null) as BioXpOperatorHistoryReceipt['history']['machine_assessment'],
            operator_assessment: (row.operator_assessment ?? null) as 'pass' | 'fail' | null,
            operator_note: typeof row.operator_note === 'string' ? row.operator_note : null,
        },
    };
}
