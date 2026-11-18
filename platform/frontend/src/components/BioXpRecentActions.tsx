import { useState, type ReactNode } from 'react';
import { BioXpHistoryReceiptCard, type useBioXpHistoryPagination } from './BioXpHistoryReceiptCard';
import { bioXpErrorText, useCreateBioXpOperatorReportExport, type BioXpOperatorHistoryReceipt } from '../lib/bioxpClient';
import { bioXpReceiptFailureText, bioXpReceiptIsMoveTimeoutReport, bioXpReceiptStatusText } from '../lib/bioxpEvidencePresentation';
import { bioXpReceiptTimestampText } from '../lib/bioxpReceiptTimestamp';

/** One line per robot action; the full receipt opens on demand. `current` is the command in flight, shown first. */
export function BioXpRecentActions({ rows, generation, connected, message, pagination, nextCursor, pagerDisabled, current }: {
    rows: BioXpOperatorHistoryReceipt[]; generation: number; connected: boolean; message: string | null;
    pagination: ReturnType<typeof useBioXpHistoryPagination>; nextCursor: string | null; pagerDisabled: boolean; current?: ReactNode;
}) {
    const [open, setOpen] = useState<string | null>(null);
    const exporter = useCreateBioXpOperatorReportExport();
    const [download, setDownload] = useState<{ format: string; href: string } | null>(null);
    const exportAs = (format: 'json' | 'csv') => {
        setDownload(null);
        exporter.mutate({ format, limit: 1000 }, { onSuccess: result => setDownload({ format, href: result.download }) });
    };
    return <section className="bx-card bx-recent" aria-label="Recent robot actions">
        <header className="bx-card-head"><h3>Recent actions</h3><div className="bx-card-actions">
            <button type="button" disabled={pagerDisabled || !pagination.hasNewer} onClick={pagination.newer}>Newer</button>
            <button type="button" disabled={pagerDisabled || nextCursor === null} onClick={() => { if (nextCursor !== null) pagination.older(nextCursor); }}>Older</button>
            <button type="button" disabled={!connected || exporter.isPending} onClick={() => exportAs('json')}>Export JSON</button>
            <button type="button" disabled={!connected || exporter.isPending} onClick={() => exportAs('csv')}>Export CSV</button>
        </div></header>
        {current}
        {exporter.isError && <p role="alert">Export failed: {bioXpErrorText(exporter.error)}</p>}
        {download && <p role="status">{download.format.toUpperCase()} export is ready. <a href={download.href}>Download</a></p>}
        {message && <p className="bx-recent-empty">{message}</p>}
        {rows.length > 0 && <table><tbody>{rows.map(row => {
            const failure = bioXpReceiptFailureText(row);
            const report = bioXpReceiptIsMoveTimeoutReport(row);
            const tone = report || row.status === 'ambiguous' ? 'warn' : row.status === 'failed' ? 'bad' : row.status === 'completed' ? 'ok' : 'warn';
            const expanded = open === row.command_id;
            return [<tr key={row.command_id} data-command={row.command_id}>
                <td className="bx-recent-time">{bioXpReceiptTimestampText(row.finished_at ?? row.accepted_at)}</td>
                <td><b>{row.action_id}</b>
                    {row.status === 'ambiguous' && <div className="bx-recent-warn">Outcome ambiguous; do not resubmit.</div>}
                    {failure && <div className={report ? 'bx-recent-warn' : 'bx-recent-bad'}>{row.error && failure === row.error.message ? `${row.error.code}: ${failure}` : failure}</div>}</td>
                <td className={`bx-recent-${tone}`}>{bioXpReceiptStatusText(row, row.status.replaceAll('_', ' '))}</td>
                <td><button type="button" aria-label={`Receipt for ${row.action_id}`} aria-expanded={expanded} onClick={() => setOpen(expanded ? null : row.command_id)}>⋯</button></td>
            </tr>, expanded && <tr key={`${row.command_id}:receipt`}><td colSpan={4}><BioXpHistoryReceiptCard receipt={row} generation={generation} connected={connected} /></td></tr>];
        })}</tbody></table>}
    </section>;
}
