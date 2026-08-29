import { useState } from 'react';

import {
    useBioXpOperatorReportCommands,
    useBioXpOperatorReportCommandDetail,
    useBioXpOperatorReportCommandEvidence,
    useBioXpOperatorReportCommandTransitions,
    useBioXpOperatorReportEventDetail,
    useBioXpOperatorReportEvents,
    useBioXpOperatorReportPipette,
    useBioXpOperatorReportPipetteChannels,
    useBioXpOperatorReportPipetteDetail,
    useBioXpOperatorReportPipetteExchanges,
    useBioXpOperatorReportPressureDetail,
    useBioXpOperatorReportPressureSamples,
    useBioXpOperatorReportPressureStreams,
    useBioXpOperatorReportSummary,
    useBioXpOperatorReportExports,
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
    const [outcome, setOutcome] = useState('');
    const [evidenceState, setEvidenceState] = useState('');
    const [eventKind, setEventKind] = useState('');
    const [eventSource, setEventSource] = useState('');
    const [deliveryVerified, setDeliveryVerified] = useState('');
    const [controllerAcknowledged, setControllerAcknowledged] = useState('');
    const [completionVerified, setCompletionVerified] = useState('');
    const [postconditionVerified, setPostconditionVerified] = useState('');
    const [physicalEffectVerified, setPhysicalEffectVerified] = useState('');
    const [channel, setChannel] = useState('');
    const [start, setStart] = useState('');
    const [end, setEnd] = useState('');
    const [cursor, setCursor] = useState<string | null>(null);
    const [cursorStack, setCursorStack] = useState<Array<string | null>>([]);
    const [selectedCommand, setSelectedCommand] = useState<string | null>(null);
    const [selectedEvent, setSelectedEvent] = useState<number | null>(null);
    const [selectedPressureStream, setSelectedPressureStream] = useState<string | null>(null);
    const [transitionCursor, setTransitionCursor] = useState<string | null>(null);
    const [evidenceCursor, setEvidenceCursor] = useState<string | null>(null);
    const [channelCursor, setChannelCursor] = useState<string | null>(null);
    const [exchangeCursor, setExchangeCursor] = useState<string | null>(null);
    const [sampleCursor, setSampleCursor] = useState<string | null>(null);
    const [exportMessage, setExportMessage] = useState<string | null>(null);
    const [exportDownload, setExportDownload] = useState<string | null>(null);

    const filters: BioXpOperatorReportFilters = {
        ...(status ? { status } : {}),
        ...(operation ? { operation } : {}),
        ...(action ? { action } : {}),
        ...(outcome ? { outcome } : {}),
        ...(evidenceState ? { evidence_state: evidenceState } : {}),
        ...(eventKind ? { event_kind: eventKind } : {}),
        ...(eventSource ? { event_source: eventSource } : {}),
        ...(deliveryVerified ? { delivery_verified: deliveryVerified === 'true' } : {}),
        ...(controllerAcknowledged ? { controller_acknowledged: controllerAcknowledged === 'true' } : {}),
        ...(completionVerified ? { completion_verified: completionVerified === 'true' } : {}),
        ...(postconditionVerified ? { hardware_postcondition_verified: postconditionVerified === 'true' } : {}),
        ...(physicalEffectVerified ? { physical_effect_verified: physicalEffectVerified === 'true' } : {}),
        ...(channel ? { channel: Number(channel) } : {}),
        ...(start ? { start: numberOrUndefined(start) } : {}),
        ...(end ? { end: numberOrUndefined(end) } : {}),
    };

    const summaryQuery = useBioXpOperatorReportSummary(generation, connected, filters);
    const commandsQuery = useBioXpOperatorReportCommands(generation, connected, 25, cursor, filters);
    const detailQuery = useBioXpOperatorReportCommandDetail(selectedCommand, connected);
    const transitionQuery = useBioXpOperatorReportCommandTransitions(selectedCommand, transitionCursor, connected);
    const evidenceQuery = useBioXpOperatorReportCommandEvidence(selectedCommand, evidenceCursor, connected);
    const eventsQuery = useBioXpOperatorReportEvents(generation, connected, filters);
    const pipetteQuery = useBioXpOperatorReportPipette(generation, connected, filters);
    const pressureQuery = useBioXpOperatorReportPressureStreams(generation, connected, filters);
    const exportsQuery = useBioXpOperatorReportExports(generation, connected);
    const selectedPipette = detailQuery.data?.pipette?.pipette_operation_id ?? null;
    const pipetteDetailQuery = useBioXpOperatorReportPipetteDetail(selectedPipette, connected);
    const pipetteChannelsQuery = useBioXpOperatorReportPipetteChannels(selectedPipette, channelCursor, connected);
    const pipetteExchangesQuery = useBioXpOperatorReportPipetteExchanges(selectedPipette, exchangeCursor, connected);
    const eventDetailQuery = useBioXpOperatorReportEventDetail(selectedEvent, connected);
    const pressureDetailQuery = useBioXpOperatorReportPressureDetail(selectedPressureStream, connected);
    const pressureSamplesQuery = useBioXpOperatorReportPressureSamples(selectedPressureStream, sampleCursor, connected);
    const exportMutation = useCreateBioXpOperatorReportExport();
    const summary = connected && !summaryQuery.isError ? summaryQuery.data : undefined;
    const rows = connected && !commandsQuery.isError ? (commandsQuery.data?.commands ?? []) : [];
    const statusCounts = summary?.commands?.by_status ?? {};
    const detail = detailQuery.data;

    const clearFilters = () => {
        setStatus('');
        setOperation('');
        setAction('');
        setOutcome('');
        setEvidenceState('');
        setEventKind('');
        setEventSource('');
        setDeliveryVerified('');
        setControllerAcknowledged('');
        setCompletionVerified('');
        setPostconditionVerified('');
        setPhysicalEffectVerified('');
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

    const selectCommand = (commandId: string | null) => {
        setSelectedCommand(commandId);
        setTransitionCursor(null);
        setEvidenceCursor(null);
        setChannelCursor(null);
        setExchangeCursor(null);
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
                    <button type="button" className="rounded border border-slate-700 px-2 py-1 hover:border-cyan-500" onClick={() => void Promise.all([summaryQuery.refetch(), commandsQuery.refetch(), eventsQuery.refetch(), pipetteQuery.refetch(), pressureQuery.refetch(), exportsQuery.refetch()])} disabled={!connected || summaryQuery.isFetching}>
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
                    <label className="text-xs text-slate-400">Outcome<input className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={outcome} onChange={(event) => setOutcome(event.target.value)} placeholder="all" /></label>
                    <label className="text-xs text-slate-400">Evidence state<input className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={evidenceState} onChange={(event) => setEvidenceState(event.target.value)} placeholder="all" /></label>
                    <label className="text-xs text-slate-400">Event kind<input className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={eventKind} onChange={(event) => setEventKind(event.target.value)} placeholder="all" /></label>
                    <label className="text-xs text-slate-400">Event source<input className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={eventSource} onChange={(event) => setEventSource(event.target.value)} placeholder="all" /></label>
                    <label className="text-xs text-slate-400">Delivered<select className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={deliveryVerified} onChange={(event) => setDeliveryVerified(event.target.value)}><option value="">all</option><option value="true">verified</option><option value="false">not verified</option></select></label>
                    <label className="text-xs text-slate-400">Controller ACK<select className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={controllerAcknowledged} onChange={(event) => setControllerAcknowledged(event.target.value)}><option value="">all</option><option value="true">verified</option><option value="false">not verified</option></select></label>
                    <label className="text-xs text-slate-400">Completion<select className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={completionVerified} onChange={(event) => setCompletionVerified(event.target.value)}><option value="">all</option><option value="true">verified</option><option value="false">not verified</option></select></label>
                    <label className="text-xs text-slate-400">Postcondition<select className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={postconditionVerified} onChange={(event) => setPostconditionVerified(event.target.value)}><option value="">all</option><option value="true">verified</option><option value="false">not verified</option></select></label>
                    <label className="text-xs text-slate-400">Physical effect<select className="mt-1 w-full rounded bg-slate-950 px-2 py-1 text-sm text-slate-100" value={physicalEffectVerified} onChange={(event) => setPhysicalEffectVerified(event.target.value)}><option value="">all</option><option value="true">verified</option><option value="false">not verified</option></select></label>
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
                        <tbody>{rows.map((row) => <CommandRow key={String(row.command_id ?? row.sequence)} row={row} selected={selectedCommand === row.command_id} onSelect={() => selectCommand(row.command_id ?? null)} />)}</tbody>
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
                        <ul className="mt-1 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(transitionQuery.data?.transitions ?? []).map((transition) => <li key={String(transition.transition_id)}>{display(transition.observed_at)} · {display(transition.state)}</li>)}</ul>
                        <button type="button" className="mt-2 rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-40" disabled={!transitionQuery.data?.next_cursor} onClick={() => setTransitionCursor(transitionQuery.data?.next_cursor ?? null)}>Next transitions</button>
                        <h4 className="mt-3 text-xs font-semibold text-slate-300">Evidence artifacts</h4>
                        <ul className="mt-1 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(evidenceQuery.data?.evidence ?? []).map((item, index) => <li key={String(item.evidence_artifact_id ?? index)}>{display(item.evidence_artifact_id)} · {display(item.sha256)} · {display(item.expiry_state)}</li>)}</ul>
                        <button type="button" className="mt-2 rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-40" disabled={!evidenceQuery.data?.next_cursor} onClick={() => setEvidenceCursor(evidenceQuery.data?.next_cursor ?? null)}>Next evidence</button>
                    </div>
                    <div className="rounded border border-slate-800 bg-slate-900/50 p-3">
                        <h3 className="font-semibold">Pipette correlation</h3>
                        {!selectedPipette && <p className="mt-2 text-xs text-slate-400">No pipette operation is attached to this command.</p>}
                        {selectedPipette && <dl className="mt-2 grid grid-cols-2 gap-2 text-xs"><dt className="text-slate-400">Operation</dt><dd>{display(pipetteDetailQuery.data?.operation)}</dd><dt className="text-slate-400">Status</dt><dd className={statusClass(pipetteDetailQuery.data?.status)}>{display(pipetteDetailQuery.data?.status)}</dd><dt className="text-slate-400">Channels</dt><dd>{pipetteChannelsQuery.data?.filtered_total ?? 0}</dd><dt className="text-slate-400">CAN exchanges</dt><dd>{pipetteExchangesQuery.data?.filtered_total ?? 0}</dd></dl>}
                        <h4 className="mt-3 text-xs font-semibold text-slate-300">Channel evidence</h4>
                        <ul className="mt-1 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(pipetteChannelsQuery.data?.channels ?? []).map((item, index) => <li key={String(item.observation_id ?? index)}>channel {display(item.channel)} · {display(item.semantic_validity)} · {display(item.pressure)} {display(item.pressure_units)}</li>)}</ul>
                        <button type="button" className="mt-2 rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-40" disabled={!pipetteChannelsQuery.data?.next_cursor} onClick={() => setChannelCursor(pipetteChannelsQuery.data?.next_cursor ?? null)}>Next channels</button>
                        <h4 className="mt-3 text-xs font-semibold text-slate-300">Transport exchanges</h4>
                        <ul className="mt-1 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(pipetteExchangesQuery.data?.exchanges ?? []).map((item, index) => <li key={String(item.exchange_id ?? index)}>{display(item.transaction_phase)} · TX {display(item.tx_id)} · RX {display(item.observed_rx_id)} · complete {display(item.completion_verified)}</li>)}</ul>
                        <button type="button" className="mt-2 rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-40" disabled={!pipetteExchangesQuery.data?.next_cursor} onClick={() => setExchangeCursor(pipetteExchangesQuery.data?.next_cursor ?? null)}>Next exchanges</button>
                    </div>
                </div>
            )}

            {connected && !summaryQuery.isError && (
                <div className="mt-4 grid gap-3 lg:grid-cols-2">
                    <div className="rounded border border-slate-800 bg-slate-900/40 p-3"><h3 className="font-semibold">Recent runtime events</h3><p className="mt-1 text-xs text-slate-400">{eventsQuery.data?.returned_count ?? 0} events in the current report view.</p><ul className="mt-2 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(eventsQuery.data?.events ?? []).map((event, index) => <li key={String(event.event_id ?? index)}><button type="button" className="text-left hover:text-cyan-200" onClick={() => { if (typeof event.event_id === 'number') setSelectedEvent(event.event_id); }}>{display(event.observed_at)} · {display(event.event_kind)} · {display(event.command_id)}</button></li>)}</ul></div>
                    <div className="rounded border border-slate-800 bg-slate-900/40 p-3"><h3 className="font-semibold">Pressure evidence</h3><p className="mt-1 text-xs text-slate-400">{pressureQuery.data?.returned_count ?? 0} pressure streams in the current report view.</p><ul className="mt-2 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(pressureQuery.data?.pressure_streams ?? []).map((stream, index) => <li key={String(stream.stream_session_id ?? index)}><button type="button" className="text-left hover:text-cyan-200" onClick={() => { if (typeof stream.stream_session_id === 'string') { setSelectedPressureStream(stream.stream_session_id); setSampleCursor(null); } }}>{display(stream.stream_session_id)} · channels {display(stream.channels)} · {display(stream.terminal_state)}</button></li>)}</ul></div>
                    <div className="rounded border border-slate-800 bg-slate-900/40 p-3"><h3 className="font-semibold">Pipette operations</h3><p className="mt-1 text-xs text-slate-400">{pipetteQuery.data?.returned_count ?? 0} pipette operations in the current report view.</p><ul className="mt-2 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(pipetteQuery.data?.pipette ?? []).map((item, index) => <li key={String(item.pipette_operation_id ?? index)}>{display(item.operation)} · {display(item.status)} · {display(item.pipette_operation_id)}</li>)}</ul></div>
                    <div className="rounded border border-slate-800 bg-slate-900/40 p-3"><h3 className="font-semibold">Report exports</h3><p className="mt-1 text-xs text-slate-400">{exportsQuery.data?.returned_count ?? 0} retained exports.</p><ul className="mt-2 max-h-32 space-y-1 overflow-auto text-xs text-slate-400">{(exportsQuery.data?.items ?? []).map((item) => <li key={item.export_id}>{display(item.format)} · {display(item.publication_state)} · {item.download ? <a className="text-cyan-200 underline" href={item.download}>download</a> : 'unavailable'}</li>)}</ul></div>
                </div>
            )}

            {(eventDetailQuery.data || pressureDetailQuery.data) && (
                <div className="mt-4 grid gap-3 lg:grid-cols-2" aria-label="Report evidence drill-down">
                    {eventDetailQuery.data && <div className="rounded border border-slate-800 bg-slate-900/50 p-3"><h3 className="font-semibold">Runtime event detail</h3><dl className="mt-2 grid grid-cols-2 gap-2 text-xs"><dt className="text-slate-400">Event</dt><dd>{display(eventDetailQuery.data.event_id)}</dd><dt className="text-slate-400">Source</dt><dd>{display(eventDetailQuery.data.event_source)}</dd><dt className="text-slate-400">Kind</dt><dd>{display(eventDetailQuery.data.event_kind)}</dd><dt className="text-slate-400">Observed</dt><dd>{display(eventDetailQuery.data.observed_at)}</dd></dl><pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-2 text-xs text-slate-400">{JSON.stringify(eventDetailQuery.data, null, 2)}</pre></div>}
                    {pressureDetailQuery.data && <div className="rounded border border-slate-800 bg-slate-900/50 p-3"><h3 className="font-semibold">Pressure stream detail and trend evidence</h3><dl className="mt-2 grid grid-cols-2 gap-2 text-xs"><dt className="text-slate-400">Stream</dt><dd className="font-mono">{display(pressureDetailQuery.data.stream_session_id)}</dd><dt className="text-slate-400">Channels</dt><dd>{display(pressureDetailQuery.data.channels)}</dd><dt className="text-slate-400">Terminal state</dt><dd>{display(pressureDetailQuery.data.terminal_state)}</dd><dt className="text-slate-400">Chunks</dt><dd>{pressureSamplesQuery.data?.filtered_total ?? pressureDetailQuery.data.chunks?.length ?? 0}</dd></dl><ol className="mt-3 max-h-40 space-y-1 overflow-auto text-xs text-slate-400">{(pressureSamplesQuery.data?.samples ?? []).map((sample, index) => <li key={String(sample.chunk_id ?? index)}>#{display(sample.chunk_sequence)} · channel {display(sample.channel)} · {display(sample.sample_count)} samples · {display(sample.units)} · {display(sample.summary)}</li>)}</ol><button type="button" className="mt-2 rounded border border-slate-700 px-2 py-1 text-xs disabled:opacity-40" disabled={!pressureSamplesQuery.data?.next_cursor} onClick={() => setSampleCursor(pressureSamplesQuery.data?.next_cursor ?? null)}>Next pressure samples</button></div>}
                </div>
            )}

            {connected && !summaryQuery.isLoading && !commandsQuery.isLoading && rows.length === 0 && !summaryQuery.isError && !commandsQuery.isError && <p className="mt-4 text-sm text-slate-400">No robot command records match the current report scope.</p>}
        </section>
    );
}
