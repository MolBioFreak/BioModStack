import { useState } from 'react';

import {
    useBioXpOperatorReportCommands,
    useBioXpOperatorReportCommandDetail,
    useBioXpOperatorReportEvents,
    useBioXpOperatorReportPressureStreams,
    useBioXpOperatorReportSummary,
    useCreateBioXpOperatorReportExport,
} from '../lib/bioxpClient';
import type { BioXpOperatorReportCommandRow, BioXpOperatorReportFilters } from '../lib/bioxpClient';

function display(value: unknown): string {
    if (value === null || value === undefined || value === '') return '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

function numberOrUndefined(value: string): number | undefined {
    if (!value.trim()) return undefined;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
}

function statusClass(status: string | undefined): string {
    if (status === 'completed' || status === 'acknowledged') return 'text-emerald-300';
    if (status === 'failed' || status === 'rejected' || status === 'blocked') return 'text-red-300';
    return 'text-amber-200';
}

function CommandRow({ row, selected, onSelect }: {
    row: BioXpOperatorReportCommandRow;
    selected: boolean;
    onSelect: () => void;
}) {
    return (
        <tr className={`border-t border-slate-800 ${selected ? 'bg-cyan-950/40' : ''}`}>
            <td className="px-2 py-2">
                <button type="button" className="font-mono text-left text-cyan-200 underline-offset-2 hover:underline" onClick={onSelect}>
                    {display(row.command_id)}
                </button>
            </td>
            <td className="px-2 py-2">{display(row.operation)}</td>
            <td className={`px-2 py-2 ${statusClass(row.status)}`}>{display(row.status)}</td>
            <td className="px-2 py-2">{display(row.outcome ?? row.failure_code)}</td>
            <td className="px-2 py-2">{row.controller_acknowledged ? 'ACK' : '—'}</td>
            <td className="px-2 py-2">{row.completion_verified ? 'complete' : 'pending'}</td>
            <td className="px-2 py-2">{display(row.evidence_state)}</td>
        </tr>
    );
}

export function BioXpOperatorReports({ generation, connected }: { generation: number; connected: boolean }) {
    const [status, setStatus] = useState('');
    const [operation, setOperation] = useState('');
    const [action, setAction] = useState('');
    const [channel, setChannel] = useState('');
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');
    const [cursor, setCursor] = useState<string | null>(null);
    const [cursorStack, setCursorStack] = useState<Array<string | null>>([]);
    const [selectedCommand, setSelectedCommand] = useState<string | null>(null);
    const [exportMessage, setExportMessage] = useState<string | null>(null);
    const [exportDownload, setExportDownload] = useState<string | null>(null);

    const filters: BioXpOperatorReportFilters = {
        ...(status ? { status } : {}),
        ...(operation ? { operation } : {}),
        ...(action ? { action } : {}),
        ...(channel ? { channel: Number(channel) } : {}),
        ...(start ? { start: numberOrUndefined(start) } : {}),
        ...(end ? { end: numberOrUndefined(end) } : {}),
    };

    const summaryQuery = useBioXpOperatorReportSummary(generation, connected, filters);
    const commandsQuery = useBioXpOperatorReportCommands(generation, connected, 25, cursor, filters);
    const detailQuery = useBioXpOperatorReportCommandDetail(selectedCommand, connected);
    const eventsQuery = useBioXpOperatorReportEvents(generation, connected, filters);
    const pressureQuery = useBioXpOperatorReportPressureStreams(generation, connected, filters);
    const exportMutation = useCreateBioXpOperatorReportExport();
    const summary = connected && !summaryQuery.isError ? summaryQuery.data : undefined;
    const rows = connected && !commandsQuery.isError ? (commandsQuery.data?.commands ?? []) : [];
    const statusCounts = summary?.commands?.by_status ?? {};
    const detail = detailQuery.data;

    const clearFilters = () => {
        setStatus('');
        setOperation('');
        setAction('');
        setChannel('');
        setStart('');
        setEnd('');
        setCursor(null);
        setCursorStack([]);
    };

    const goNext = () => {
        const next = commandsQuery.data?.next_cursor;
        if (!next) return;
        setCursorStack((items) => [...items, cursor]);
        setCursor(next);
        setSelectedCommand(null);
    };

    const goPrevious = () => {
        const previous = cursorStack.at(-1);
        if (previous === undefined) return;
        setCursorStack((items) => items.slice(0, -1));
        setCursor(previous);
        setSelectedCommand(null);
    };

    const exportReport = (format: 'json' | 'csv') => {
        setExportMessage(null);
        setExportDownload(null);
        exportMutation.mutate({ format, filters, limit: 1000 }, {
            onSuccess: (result) => {
                setExportDownload(result.download);
                setExportMessage(`${format.toUpperCase()} export ${result.export_id} is ready. SHA-256 ${result.sha256}.`);
            },
            onError: (error) => setExportMessage(`Export failed: ${display(error)}`),
        });
    };

    return (
        <section className="rounded-xl border border-slate-800 bg-slate-950/70 p-4" aria-labelledby="bioxp-operator-reports-title">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 id="bioxp-operator-reports-title" className="text-lg font-semibold">Operator reports</h2>
                    <p className="mt-1 text-sm text-slate-400">Robot-owned audit data. BMS stores no duplicate command history.</p>
                </div>
                <div className="flex items-center gap-2 text-xs text-slate-300">
                    <span className="rounded bg-slate-900 px-2 py-1 font-mono">generation {generation || '—'}</span>
                    <button type="button" className="rounded border border-slate-700 px-2 py-1 hover:border-cyan-500" onClick={() => void Promise.all([summaryQuery.refetch(), commandsQuery.refetch(), eventsQuery.refetch(), pressureQuery.refetch()])} disabled={!connected || summaryQuery.isFetching}>
                        Refresh
                    </button>
                </div>
            </div>

            {!connected && <p className="mt-3 text-sm text-slate-400">Connect to BioXP to read reports.</p>}
            {connected && (summaryQuery.isLoading || commandsQuery.isLoading) && <p role="status" className="mt-3 text-sm text-cyan-200">Loading robot reports…</p>}
            {connected && (summaryQuery.isError || commandsQuery.isError) && <p role="alert" className="mt-3 text-sm text-amber-200">Robot report data is unavailable. No local history is shown.</p>}

            {connected && (
                <div className="mt-4 grid gap-2 rounded border border-slate-800 bg-slate-900/40 p-3 md:grid-cols-3 lg:grid-cols-6">
                    <label className="text-xs text-slate-400">Status<input className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={status} onChange={(event) => { setStatus(event.target.value); setCursor(null); setCursorStack([]); }} placeholder="all" /></label>
                    <label className="text-xs text-slate-400">Operation<input className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={operation} onChange={(event) => { setOperation(event.target.value); setCursor(null); setCursorStack([]); }} placeholder="all" /></label>
                    <label className="text-xs text-slate-400">Action<input className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={action} onChange={(event) => { setAction(event.target.value); setCursor(null); setCursorStack([]); }} placeholder="all" /></label>
                    <label className="text-xs text-slate-400">Channel<select className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={channel} onChange={(event) => { setChannel(event.target.value); setCursor(null); setCursorStack([]); }}><option value="">all</option><option value="0">0</option><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></label>
                    <label className="text-xs text-slate-400">Start timestamp<input className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={start} onChange={(event) => setStart(event.target.value)} inputMode="decimal" placeholder="optional" /></label>
                    <label className="text-xs text-slate-400">End timestamp<input className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={end} onChange={(event) => setEnd(event.target.value)} inputMode="decimal" placeholder="optional" /></label>
                    <div className="flex items-end gap-2 md:col-span-3 lg:col-span-6">
                        <button type="button" className="rounded border border-slate-700 px-3 py-1 text-xs hover:border-cyan-500" onClick={clearFilters}>Clear filters</button>
                        <button type="button" className="rounded border border-slate-700 px-3 py-1 text-xs hover:border-cyan-500" onClick={() => exportReport('json')} disabled={exportMutation.isPending}>Export JSON</button>
                        <button type="button" className="rounded border border-slate-700 px-3 py-1 text-xs hover:border-cyan-500" onClick={() => exportReport('csv')} disabled={exportMutation.isPending}>Export CSV</button>
                        {exportMessage && <span role="status" className="text-xs text-slate-300">{exportMessage}</span>}
                        {exportDownload && <a className="text-xs text-cyan-200 underline" href={exportDownload}>Download export</a>}
                    </div>
                </div>
            )}

            {summary && (
                <dl className="mt-4 grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Commands</dt><dd className="mt-1 text-xl font-semibold">{summary.commands?.total ?? 0}</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Pipette operations</dt><dd className="mt-1 text-xl font-semibold">{summary.pipette_operations?.total ?? 0}</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Completed</dt><dd className="mt-1 text-xl font-semibold text-emerald-300">{statusCounts.completed ?? 0}</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Failed</dt><dd className="mt-1 text-xl font-semibold text-red-300">{statusCounts.failed ?? 0}</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Events</dt><dd className="mt-1 text-xl font-semibold">{summary.runtime_events?.total ?? 0}</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Pressure chunks</dt><dd className="mt-1 text-xl font-semibold">{summary.pressure?.chunks ?? 0}</dd></div>
                    </dl>
                    )}
                    {summary && (
                    <dl className="mt-2 grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">ACK rate</dt><dd className="mt-1 text-lg font-semibold">{Math.round((summary.rates?.ack_rate ?? 0) * 100)}%</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Completion rate</dt><dd className="mt-1 text-lg font-semibold">{Math.round((summary.rates?.completion_rate ?? 0) * 100)}%</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Postcondition rate</dt><dd className="mt-1 text-lg font-semibold">{Math.round((summary.rates?.postcondition_rate ?? 0) * 100)}%</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Failure rate</dt><dd className="mt-1 text-lg font-semibold text-amber-200">{Math.round((summary.rates?.failure_rate ?? 0) * 100)}%</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Average latency</dt><dd className="mt-1 text-lg font-semibold">{(summary.latency?.average_ms ?? 0).toFixed(1)} ms</dd></div>
                    <div className="rounded bg-slate-900/70 p-3"><dt className="text-xs text-slate-400">Max latency</dt><dd className="mt-1 text-lg font-semibold">{(summary.latency?.maximum_ms ?? 0).toFixed(1)} ms</dd></div>
                    </dl>
                    )}

            {rows.length > 0 && (
                <div className="mt-4 overflow-x-auto">
                    <table className="min-w-full text-left text-xs">
                        <thead className="text-slate-400"><tr><th className="px-2 py-2">Command</th><th className="px-2 py-2">Operation</th><th className="px-2 py-2">Status</th><th className="px-2 py-2">Outcome</th><th className="px-2 py-2">Controller</th><th className="px-2 py-2">Completion</th><th className="px-2 py-2">Evidence</th></tr></thead>
                        <tbody>{rows.map((row) => <CommandRow key={String(row.command_id ?? row.sequence)} row={row} selected={selectedCommand === row.command_id} onSelect={() => setSelectedCommand(row.command_id ?? null)} />)}</tbody>
                    </table>
                    <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
                        <span>{commandsQuery.data?.filtered_total ?? rows.length} matching rows</span>
                        <div className="flex gap-2"><button type="button" className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40" onClick={goPrevious} disabled={cursorStack.length === 0}>Previous</button><button type="button" className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40" onClick={goNext} disabled={!commandsQuery.data?.next_cursor}>Next</button></div>
                    </div>
                </div>
            )}

            {detail && (
                <div className="mt-4 grid gap-3 lg:grid-cols-2" aria-label="Selected command detail">
                    <div className="rounded border border-slate-800 bg-slate-900/50 p-3">
                        <h3 className="font-semibold">Command detail</h3>
                        <dl className="mt-2 grid grid-cols-2 gap-2 text-xs"><dt className="text-slate-400">Command</dt><dd className="font-mono">{display(detail.command_id)}</dd><dt className="text-slate-400">Status</dt><dd className={statusClass(detail.status)}>{display(detail.status)}</dd><dt className="text-slate-400">ACK</dt><dd>{detail.controller_acknowledged ? 'verified' : 'not verified'}</dd><dt className="text-slate-400">Completion</dt><dd>{detail.completion_verified ? 'verified' : 'not verified'}</dd><dt className="text-slate-400">Evidence</dt><dd>{display(detail.evidence_state)}</dd></dl>
                        <h4 className="mt-3 text-xs font-semibold text-slate-300">State transitions</h4>
                        <ul className="mt-1 space-y-1 text-xs text-slate-400">{(detail.transitions ?? []).map((transition) => <li key={String(transition.transition_id)}>{display(transition.observed_at)} · {display(transition.state)}</li>)}</ul>
                    </div>
                    <div className="rounded border border-slate-800 bg-slate-900/50 p-3">
                        <h3 className="font-semibold">Pipette correlation</h3>
                        {!detail.pipette && <p className="mt-2 text-xs text-slate-400">No pipette operation is attached to this command.</p>}
                        {detail.pipette && <dl className="mt-2 grid grid-cols-2 gap-2 text-xs"><dt className="text-slate-400">Operation</dt><dd>{display(detail.pipette.operation)}</dd><dt className="text-slate-400">Status</dt><dd className={statusClass(detail.pipette.status)}>{display(detail.pipette.status)}</dd><dt className="text-slate-400">Channels</dt><dd>{detail.pipette.channels?.length ?? 0}</dd><dt className="text-slate-400">CAN exchanges</dt><dd>{detail.pipette.exchanges?.length ?? 0}</dd><dt className="text-slate-400">Runtime events</dt><dd>{detail.pipette.events?.length ?? 0}</dd><dt className="text-slate-400">Pressure streams</dt><dd>{detail.pipette.pressure_streams?.length ?? 0}</dd></dl>}
                    </div>
                </div>
            )}

            {connected && !summaryQuery.isError && (
                <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    <div className="rounded border border-slate-800 bg-slate-900/40 p-3"><h3 className="font-semibold">Recent runtime events</h3><p className="mt-1 text-xs text-slate-400">{eventsQuery.data?.returned_count ?? 0} events in the current report view.</p><ul className="mt-2 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(eventsQuery.data?.events ?? []).map((event, index) => <li key={String(event.event_id ?? index)}>{display(event.observed_at)} · {display(event.event_kind)} · {display(event.command_id)}</li>)}</ul></div>
                    <div className="rounded border border-slate-800 bg-slate-900/40 p-3"><h3 className="font-semibold">Pressure evidence</h3><p className="mt-1 text-xs text-slate-400">{pressureQuery.data?.returned_count ?? 0} pressure streams in the current report view.</p><ul className="mt-2 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(pressureQuery.data?.pressure_streams ?? []).map((stream, index) => <li key={String(stream.stream_session_id ?? index)}>{display(stream.stream_session_id)} · channels {display(stream.channels)} · {display(stream.terminal_state)}</li>)}</ul></div>
                </div>
            )}

            {connected && !summaryQuery.isLoading && !commandsQuery.isLoading && rows.length === 0 && !summaryQuery.isError && !commandsQuery.isError && <p className="mt-4 text-sm text-slate-400">No robot command records match the current report scope.</p>}
        </section>
    );
}
