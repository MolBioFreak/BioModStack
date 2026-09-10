import { useEffect, useState } from 'react';
import {
    type BioXpOperatorHistoryReceipt,
    bioXpErrorText,
    useBioXpOperatorReceiptV2,
} from '../lib/bioxpClient';
import { bioXpReceiptTimestampText } from '../lib/bioxpReceiptTimestamp';

/** Shared history rendering. Evidence is fetched only when this row is opened. */
export function BioXpHistoryReceiptCard({ receipt, generation, connected }: {
    receipt: BioXpOperatorHistoryReceipt; generation: number; connected: boolean;
}) {
    const [expanded, setExpanded] = useState(false);
    const detail = useBioXpOperatorReceiptV2(receipt.command_id, generation, connected && expanded);
    const evidence = receipt.history;
    return <article className="rounded border border-slate-800 bg-slate-900/60 p-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
            <strong className="font-mono text-slate-100">{receipt.action_id}</strong>
            <span className={receipt.status === 'ambiguous' ? 'text-amber-300' : receipt.status === 'failed' ? 'text-red-300' : 'text-slate-300'}>
                {receipt.status.replaceAll('_', ' ')} · {bioXpReceiptTimestampText(receipt.finished_at ?? receipt.accepted_at)}
            </span>
        </div>
        <p className="mt-1 break-all font-mono text-xs text-slate-400">{receipt.command_id} · generation {receipt.ownership_generation}</p>
        {evidence.source === 'retained' && <p className="mt-1 text-xs text-amber-300">Retained legacy record — not current control authority.</p>}
        {receipt.status === 'ambiguous' && <p className="mt-1 text-amber-300">Outcome ambiguous; do not resubmit; reconciliation required.</p>}
        {receipt.error && <p className="mt-1 whitespace-pre-wrap text-red-300">{receipt.error.code}: {receipt.error.message}</p>}
        <p className="mt-1 text-xs text-slate-400">
            {evidence.remote_acknowledged ? 'Robot HTTP acknowledged' : 'Robot HTTP unverified'} · {evidence.controller_acknowledged ? 'Controller ACK' : 'Controller ACK unverified'} · {evidence.controller_terminal_state_verified ? 'Terminal proof verified' : 'Terminal proof unverified'} · {receipt.physical_effect_verified ? 'Physical effect verified' : 'Physical effect unverified'}
        </p>
        <p className="mt-1 text-xs text-slate-400">Recorded status: {evidence.recorded_status} · machine={evidence.machine_assessment ?? 'unverified'} · operator={evidence.operator_assessment ?? 'unreviewed'}</p>
        {evidence.operator_note && <p className="mt-1 text-slate-300">Operator: {evidence.operator_note}</p>}
        <details className="mt-2" onToggle={(event) => setExpanded(event.currentTarget.open)}>
            <summary className="cursor-pointer text-xs text-slate-400">Full robot receipt and retained evidence</summary>
            {expanded && connected && <>
                {detail.isLoading && <p role="status">Loading receipt evidence…</p>}
                {detail.isError ? <p role="alert">Receipt evidence unavailable: {bioXpErrorText(detail.error)}</p>
                    : detail.data && <pre className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap text-[11px] text-slate-400">{JSON.stringify(detail.data, null, 2)}</pre>}
            </>}
        </details>
    </article>;
}

/** Cursor ownership includes both connection generation and selected page size. */
export function useBioXpHistoryPagination(generation: number, limit: number) {
    const [state, setState] = useState<{ generation: number; limit: number; cursors: Array<string | null> }>({ generation, limit, cursors: [null] });
    const cursors = state.generation === generation && state.limit === limit ? state.cursors : [null];
    useEffect(() => setState({ generation, limit, cursors: [null] }), [generation, limit]);
    return {
        cursor: cursors[cursors.length - 1],
        hasNewer: cursors.length > 1,
        newer: () => setState({ generation, limit, cursors: cursors.slice(0, -1).length ? cursors.slice(0, -1) : [null] }),
        older: (cursor: string) => setState({ generation, limit, cursors: [...cursors, cursor] }),
    };
}

export function BioXpHistoryPager({ pagination, nextCursor, disabled }: {
    pagination: ReturnType<typeof useBioXpHistoryPagination>; nextCursor: string | null; disabled: boolean;
}) {
    return <nav aria-label="Robot history pages" className="mt-3 flex gap-2 text-xs">
        <button type="button" className="rounded border border-slate-700 px-3 py-2 disabled:opacity-35" disabled={disabled || !pagination.hasNewer} onClick={pagination.newer}>Newer actions</button>
        <button type="button" className="rounded border border-slate-700 px-3 py-2 disabled:opacity-35" disabled={disabled || nextCursor === null} onClick={() => { if (nextCursor !== null) pagination.older(nextCursor); }}>Older actions</button>
    </nav>;
}
