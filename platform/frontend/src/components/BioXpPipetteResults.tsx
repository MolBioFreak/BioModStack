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

export function PipetteOutcome({ result }: { result: PipetteResult }) {
    const body = result.body_completed ?? result.completed;
    return <dl className="text-sm space-y-1">
        {result.action && <><dt>OEM diagnostic action</dt><dd>{result.action.replaceAll('_', ' ')}</dd></>}
        {([['requested_pipette', 'Requested source pipette'], ['tip_location', 'Robot source TipLocation'], ['alignment_published', 'New alignment published'], ['already_matching_tip_type', 'Matching-tip early return'], ['selected_channels', 'Selected source channels'], ['lost_tip_channels', 'Lost-tip channels'], ['cached_tip_channels', 'Cached tipped channels'], ['ejected_channels', 'Ejected channels'], ['controller_outcome_ok', 'Controller outcome'], ['source_return_completed', 'Source caller returned'], ['physical_effect_verified', 'Physical effect verified']] as const).map(([key, label]) => result[key] !== undefined && <Fragment key={key}><dt>{label}</dt><dd><PipetteValue value={result[key]} /></dd></Fragment>)}
        {result.already_matching_tip_type === true && <><dt>Alignment note</dt><dd>Existing alignment retained; requesting another pipette did not establish new alignment.</dd></>}
        {result.channels && <><dt>Diagnostic channel results</dt><dd><PipetteValue value={result.channels.map(row => Object.fromEntries(Object.entries(row).filter(([key]) => ['channel', 'part_number', 'revision', 'firmware', 'data', 'error', 'display', 'diagnosis'].includes(key))))} /></dd></>}
        {result.tests && <><dt>Ordered diagnosis tests</dt><dd><PipetteValue value={result.tests.map(row => ({ number: row.number, label: row.label, channels: Array.isArray(row.channels) ? row.channels.map(channel => { const r = resultRecord(channel); return r ? { channel: r.channel, diagnosis: r.diagnosis, display: r.display } : channel; }) : row.channels }))} /></dd></>}
        {result.attempts && <><dt>Initialize attempts</dt><dd>{result.attempts.length}</dd></>}
        {result.action_id && <><dt>Action ID</dt><dd>{result.action_id}</dd></>}
        {result.detail != null && <><dt>Action detail</dt><dd><PipetteValue value={result.detail} /></dd></>}
        {result.kind && <><dt>Result kind</dt><dd>{result.kind}</dd></>}
        {result.run_id && <><dt>Calibration run</dt><dd className="break-all">{result.run_id}</dd></>}
        {body !== undefined && <><dt>Source body</dt><dd>{body ? 'Completed' : 'Incomplete / partial result'}</dd></>}
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
        {result.error != null && <><dt>Source / action error</dt><dd role="alert"><PipetteValue value={result.error} /></dd></>}
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
