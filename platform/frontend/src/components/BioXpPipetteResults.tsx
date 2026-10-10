import { Fragment } from 'react';
import { pipetteResults, resultRecord, type PipetteResult } from '../lib/bioxpPipetteResults';

/** Read-only critical outcome data, not a raw diagnostic/JSON console. */
export function PipetteValue({ value }: { value: unknown }) {
    if (value === undefined) return <>Not reported</>;
    if (value === null) return <>None</>;
    if (Array.isArray(value)) return <ol>{value.map((row, index) => <li key={index}><PipetteValue value={row} /></li>)}</ol>;
    const record = resultRecord(value);
    if (record) return <dl className="ml-3">{Object.entries(record).map(([key, field]) =>
        <Fragment key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd><PipetteValue value={field} /></dd></Fragment>)}</dl>;
    return <>{String(value)}</>;
}

const recordRows = (value: unknown) => Array.isArray(value) ? value.map(resultRecord).filter((row): row is Record<string, unknown> => row !== null) : [];
const proof = (value: unknown) => value === true ? 'Yes' : value === false ? 'No' : 'Unknown';
/** Exact native report attribution; an answer or ACK is not completion. */
function DeviceOperationResult({ event }: { event: Record<string, unknown> }) {
    const partial = resultRecord(resultRecord(event.partial)?.pipette_partial_result);
    const outcome = resultRecord(event.result) ?? partial ?? {};
    const rows = recordRows(outcome.channels);
    const failed = resultRecord(resultRecord(outcome.failure_details)?.driver_result);
    return <section aria-label="Device command reports" className="min-w-0 break-words">
        <p>Device operation status: {String(event.status ?? 'Unknown')}. Answers, controller completion and physical verification are distinct.</p>
        {outcome.failed_channel !== undefined && <p role="alert">Failed channel: {String(outcome.failed_channel)} · {String(failed?.error ?? 'Failure detail not reported')}</p>}
        {rows.map((channel, index) => { const result = resultRecord(channel.result) ?? {}; const reports = recordRows(result.reports); return <section key={index} aria-label={`Device channel ${String(channel.channel)}`}>
            <h5>Channel {String(channel.channel)}</h5>
            <dl><dt>Controller acknowledged</dt><dd>{proof(result.controller_acknowledged)}</dd><dt>Completion verified</dt><dd>{proof(result.completion_verified)}</dd><dt>Physical effect verified</dt><dd>{proof(result.physical_effect_verified)}</dd><dt>Completion attribution unknown</dt><dd>{proof(result.completion_attribution_unknown)}</dd></dl>
            {reports.length ? <div className="max-w-full overflow-x-auto"><table><thead><tr>{['Command index', 'Answer index', 'Report', 'Value', 'Units', 'Parsed', 'Attribution unknown', 'Receive sequence', 'Robot receive time'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{reports.map((report, i) => { const ingress = resultRecord(report.response) ?? {}; return <tr key={i}>{[report.command_index, report.answer_index, report.report_kind, report.value, report.units, report.parsed, report.attribution_unknown, ingress.receive_sequence, ingress.received_at].map((value, j) => <td key={j}>{String(value ?? 'Unknown')}</td>)}</tr>; })}</tbody></table></div> : <p>No decoded report retained for this channel.</p>}
            <details><summary>Exact command intent and report ingress</summary><PipetteValue value={{ requested: result.requested, buffer_action: result.buffer_action, command_effects: result.command_effects, reports: result.reports }} /></details>
        </section>; })}
        {rows.length === 0 && <p>No per-channel outcome retained; completion is unknown.</p>}
    </section>;
}

export function PipetteOutcome({ result }: { result: PipetteResult }) {
    const body = result.body_completed ?? result.completed;
    return <dl className="min-w-0 break-words text-sm space-y-1">
        {result.action && <><dt>OEM diagnostic action</dt><dd>{result.action.replaceAll('_', ' ')}</dd></>}
        {([['requested_pipette', 'Requested pipette'], ['tip_location', 'Reported tip alignment'], ['alignment_published', 'New alignment reported'], ['already_matching_tip_type', 'Matching tips already loaded'], ['selected_channels', 'Selected channels'], ['lost_tip_channels', 'Lost-tip channels'], ['cached_tip_channels', 'Cached tipped channels'], ['ejected_channels', 'Ejected channels'], ['controller_outcome_ok', 'Controller outcome'], ['source_return_completed', 'Procedure returned'], ['physical_effect_verified', 'Physical effect verified']] as const).map(([key, label]) => result[key] !== undefined && <Fragment key={key}><dt>{label}</dt><dd><PipetteValue value={result[key]} /></dd></Fragment>)}
        {result.already_matching_tip_type === true && <><dt>Alignment note</dt><dd>Existing alignment retained; no new alignment was reported.</dd></>}
        {result.channels && <><dt>Diagnostic channel results</dt><dd><PipetteValue value={result.channels.map(row => Object.fromEntries(Object.entries(row).filter(([key]) => ['channel', 'part_number', 'revision', 'firmware', 'data', 'error', 'display', 'diagnosis'].includes(key))))} /></dd></>}
        {result.tests && <><dt>Ordered diagnosis tests</dt><dd><PipetteValue value={result.tests.map(row => ({ number: row.number, label: row.label, channels: Array.isArray(row.channels) ? row.channels.map(channel => { const r = resultRecord(channel); return r ? { channel: r.channel, diagnosis: r.diagnosis, display: r.display } : channel; }) : row.channels }))} /></dd></>}
        {result.action === 'diagnoses' && result.tests?.every(row => Array.isArray(row.channels) && row.channels.length === 0) && <><dt>Selection outcome</dt><dd>No channels tested. This is not an all-channel pass.</dd></>}
        {result.events && <><dt>Native operations — commanded / reported</dt><dd><ol>{result.events.map((event, index) => <li key={index} className="border-b border-slate-700 py-2"><strong>{String(event.operation ?? 'Operation')} · {String(event.status ?? 'not reported')}</strong><p>Source identity: {String(event.source_identity ?? 'not reported')}</p>{['device_command', 'device_program'].includes(String(event.operation)) ? <><DeviceOperationResult event={event} /><details><summary>Authored device inputs and reported application</summary><PipetteValue value={{ inputs: event.inputs, reported_applied: event.reported_applied }} /></details></> : <PipetteValue value={{ inputs: event.inputs, reported_applied: event.reported_applied }} />}<details><summary>Returned operation evidence</summary><PipetteValue value={event.result ?? event.partial} /></details></li>)}</ol></dd></>}
        {(['plld', 'shared_head', 'liquid_settings', 'requested', 'correction', 'analysis'] as const).map(key => result[key] !== undefined && <Fragment key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd><PipetteValue value={result[key]} /></dd></Fragment>)}
        {result.attempts && <><dt>Initialize attempts</dt><dd>{result.attempts.length}</dd></>}
        {result.action_id && <><dt>Action ID</dt><dd>{result.action_id}</dd></>}
        {result.detail != null && <><dt>Action detail</dt><dd><PipetteValue value={result.detail} /></dd></>}
        {result.kind && <><dt>Result kind</dt><dd>{result.kind}</dd></>}
        {result.run_id && <><dt>Calibration run</dt><dd className="break-all">{result.run_id}</dd></>}
        {body !== undefined && <><dt>Procedure</dt><dd>{body ? 'Completed' : 'Incomplete / partial result'}</dd></>}
        {result.position_steps !== undefined && <><dt>Measured fluid height (Z steps)</dt><dd>{result.position_steps}</dd></>}
        {result.lost_steps !== undefined && <><dt>Pickup lost steps</dt><dd>{result.lost_steps}{result.lost_steps_warning ? ' · source warning' : ''}</dd></>}
        {result.source_return !== undefined && <><dt>{result.samples ? 'OEM fluid offset (Z steps)' : 'Source return'}</dt><dd><PipetteValue value={result.source_return} /></dd></>}
        {result.samples && <><dt>Sampled wells</dt><dd>{result.samples.map(row => row.well).filter(value => typeof value === 'string').join(', ')}</dd><dt>Sample measurements</dt><dd><PipetteValue value={result.samples} /></dd></>}
        {result.scans && <><dt>OEM Detect Fluid (raw Z steps)</dt><dd><StationResults rows={result.scans} /></dd></>}
        {result.measurements && <><dt>OEM fluid calibration (raw Z steps)</dt><dd><StationResults rows={result.measurements} /></dd></>}
        <dt>Calibration saved</dt><dd>{typeof result.saved_revision_id === 'string' ? `Yes · revision ${result.saved_revision_id}${result.pending_restart === true ? ' · pending restart' : ''}` : result.saved_revision_id === null ? 'No saved revision' : result.calibration_persisted === true ? 'Yes · revision not reported' : result.calibration_persisted === false ? 'No (robot reported)' : 'Not reported'}</dd>
        {'active_revision_id' in result && <><dt>Active revision</dt><dd>{result.active_revision_id ?? 'Baseline / no revision'}</dd></>}
        {result.pending_restart !== undefined && <><dt>Pending restart (robot reported)</dt><dd>{String(result.pending_restart)}</dd></>}
        {'comparison_choice' in result && <><dt>Comparison choice</dt><dd>{result.comparison_choice == null ? 'Not decided' : String(result.comparison_choice)}</dd></>}
        {result.comparison_source && <><dt>Comparison source</dt><dd>{result.comparison_source}</dd></>}
        {result.source && <><dt>OEM source</dt><dd>{result.source}</dd></>}
        {result.error != null && <><dt>Action error</dt><dd role="alert"><PipetteValue value={result.error} /></dd></>}
        {result.finalization_error != null && <><dt>Finalization error</dt><dd role="alert"><PipetteValue value={result.finalization_error} /></dd></>}
    </dl>;
}
function StationResults({ rows }: { rows: Record<string, unknown>[] }) {
    return <ol>{rows.map((row, index) => <li key={index}>
        <span>{String(row.plate ?? `Station ${index + 1}`)}: {String(row.measured_raw_z ?? 'measurement not reported')}</span>
        <PipetteValue value={Object.fromEntries(Object.entries(row).filter(([key]) =>
            !['plate', 'measured_raw_z', 'scan', 'children', 'response', 'result', 'raw_debug'].includes(key)))} />
        {resultRecord(row.scan) && <BioXpPipetteResults value={row.scan} />}
    </li>)}</ol>;
}
export function BioXpPipetteResults({ value }: { value: unknown }) {
    return <>{pipetteResults(value).map((result, index) => <section key={index} aria-label={`Measurement ${index + 1}`}><PipetteOutcome result={result} /></section>)}</>;
}
