import { useState } from 'react';
import { BioXpPipetteResults, PipetteValue } from './BioXpPipetteResults';
import type { MethodCompile, MethodValue } from '../lib/bioxpMethods';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import { previewActionDestination } from '../lib/bioxpWorkflowPlan';
import { occurrenceSteps } from './BioXpMethodPlanSummary';
import { object } from './BioXpMethodFields';
import { methodStateAfter, methodVessels } from '../lib/bioxpMethodSimulation';
import { deckResources } from '../lib/bioxpWorkflowDeck';
export function nativeMethodActions(document: unknown): MethodValue[] {
    return (Array.isArray(object(document).stages) ? object(document).stages as unknown[] : []).flatMap(stage => Array.isArray(object(stage).actions) ? object(stage).actions as MethodValue[] : []);
}
// Presentation-only selection from the existing emitted action, never a second
// Recipe/Application state owner or a numerical geometry/accounting projection.
function nativeGeometryContext(value: unknown, path = 'params'): { path: string; value: unknown }[] {
    if (!value || typeof value !== 'object') return [];
    return Object.entries(value).flatMap(([key, child]) => {
        const childPath = `${path}.${key}`;
        if (['accounting_context', 'accounting_intent', 'aspiration_intent', 'conditioning_intent', 'rack_transform', 'footprint', 'target_liquid_ul', 'conditioning_target_liquid_ul'].includes(key)
            || key === 'reference' && ['vessel', 'rack'].includes(String(object(child).kind))) return [{ path: childPath, value: child }];
        return nativeGeometryContext(child, childPath);
    });
}
export function BioXpMethodPreview({ result, initialState, custodyDestinations = [] }: { result: MethodCompile; initialState?: unknown; custodyDestinations?: MethodValue[] }) {
    const [selected, setSelected] = useState('');
    const provenance = result.provenance ?? [];
    const row = provenance.find(p => p.occurrence_id === selected) ?? provenance[0];
    const ids = new Set(Array.isArray(row?.native_action_ids) ? row.native_action_ids : []);
    const actions = nativeMethodActions(result.document).filter(a => ids.has(a.action_id));
    const [child, setChild] = useState(0);
    const action = actions[Math.min(child, Math.max(0, actions.length - 1))];
    const context = nativeGeometryContext(action?.params);
    const [contextOpen, setContextOpen] = useState(false);
    const destination = previewActionDestination({ kind: String(action?.kind ?? ''), params: object(action?.params), station: null, well: null }, custodyDestinations);
    const after = methodStateAfter(result.simulation, initialState, row?.occurrence_id);
    const vessels = methodVessels(after);
    const display = (value: unknown) => value == null ? 'unknown' : typeof value === 'object' ? JSON.stringify(value) : String(value);
    return <section aria-label="Compiled occurrence preview"><h4>Compiled occurrences and generated actions</h4>
        <select aria-label="Preview occurrence" value={String(row?.occurrence_id ?? '')} onChange={e => { setSelected(e.target.value); setChild(0); }}>{provenance.map(p => <option key={String(p.occurrence_id)} value={String(p.occurrence_id)}>{String(p.step_id)} · {String(p.occurrence_id)} · {JSON.stringify(p.loop_path)}</option>)}</select>
        <ol>{actions.map((a, i) => <li key={String(a.action_id)}><button type="button" onClick={() => setChild(i)}>{String(a.action_id)} · {String(a.kind)}</button></li>)}</ol>
        <p>Station: {destination.station ?? 'No emitted station / non-XY action'} · head-reference well: {destination.well ?? 'unspecified'}</p>
        <BioXpWorkflowDeck readOnly selection={{ station: destination.station ?? '', wells: destination.well ? [destination.well] : [] }} onChange={() => {}} overlay={<g aria-label="Simulated contents after occurrence">{vessels.map(vessel => {
            const resource = deckResources.find(r => r.id === vessel.station || r.locationId != null && String(r.locationId) === String(vessel.station));
            const point = resource?.points.find(p => p.well === vessel.well);
            if (!point) return null;
            return <g key={vessel.key} data-planned-vessel={vessel.key}><circle cx={point.x} cy={point.y} r="8" fill={vessel.volume_ul == null ? '#94a3b8' : '#14b8a6'} fillOpacity="0.65" /><title>{vessel.labwareId} {vessel.well}: {display(vessel.volume_ul)} µL; materials {display(vessel.materials)}. Planned, not measured.</title></g>;
        })}</g>} />
        <dl>{Object.entries(object(action?.params)).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{JSON.stringify(value)}</dd></div>)}</dl>
        {context.length > 0 && <details aria-label="Authored native geometry and accounting context" open={contextOpen} onToggle={e => setContextOpen(e.currentTarget.open)}><summary>Authored native context — not a calculated result</summary>{contextOpen && <><p>Selected action only. Native geometry, compartment inventories and per-channel endpoints are retained for native execution. No preflight surface, footprint, occupancy or measured delivery is inferred here. Legacy recipe totals describe labels and explicit strokes, not the native completed inventory.</p>{context.map(entry => <section key={entry.path}><h5>{entry.path}</h5><PipetteValue value={entry.value} /></section>)}</>}</details>}
        <section aria-label="Planned state after occurrence"><h4>Simulated after the complete occurrence</h4><p>Not observed pose or a physical measurement. Selecting a native child changes the destination highlight, not this occurrence-level simulation.</p>
            {!after ? <p>Unknown / not reported.</p> : <><table><thead><tr><th>Labware</th><th>Station</th><th>Well</th><th>Liquid µL</th><th>Mixture / materials</th></tr></thead><tbody>{vessels.map(v => <tr key={v.key}><td>{v.labwareId}</td><td>{display(v.station)}</td><td>{v.well}</td><td>{display(v.volume_ul)}</td><td>{display(v.materials)}</td></tr>)}</tbody></table>
            <dl>{['channels', 'custody', 'thermal_tasks', 'head_reference'].map(key => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{display(after[key])}</dd></div>)}</dl></>}
        </section>
    </section>;
}
export function BioXpMethodApplicationFields({ value }: { value: unknown }) {
    const fields: { path: string; data: MethodValue }[] = [];
    const walk = (v: unknown, path: string) => {
        if (!v || typeof v !== 'object') return;
        const row = object(v);
        if (Object.hasOwn(row, 'requested') && Object.hasOwn(row, 'resolved') && !Object.hasOwn(row, 'fields') && (Object.hasOwn(row, 'emitted') || Object.hasOwn(row, 'applied'))) fields.push({ path, data: row });
        else for (const [key, child] of Object.entries(v)) walk(child, `${path}/${key}`);
    };
    walk(value, '');
    if (!fields.length) return null;
    const display = (v: unknown) => v === undefined ? 'not reported / unspecified' : v === null ? 'explicit null' : typeof v === 'object' ? JSON.stringify(v) : String(v);
    return <section aria-label="Field application records"><h4>Per-field application records</h4><table><thead><tr><th>Field / context</th><th>Requested (including absence)</th><th>Resolved / source revision</th><th>Applicability / adapter / options</th><th>Emitted</th><th>Reported applied</th></tr></thead><tbody>{fields.map(({ path, data }) => <tr key={path}><td>{path}</td><td>{display(data.requested)}</td><td>{display(data.resolved)} · {display(data.source ?? data.ref ?? object(data.resolved).reference)}</td><td>{display(data.applicability ?? data.applicable)} · {display(data.adapter)} · {display(data.options)}</td><td>{display(data.emitted)}</td><td>{display(data.applied ?? data.reported_applied)}</td></tr>)}</tbody></table><p>Unknown, unsupported and not-emitted are not applied. Applicability and adapter/options evidence are not inferred from a number.</p></section>;
}
export function methodOccurrence(workflow: MethodValue, context?: MethodValue) {
    const occurrence = workflow.source_occurrence_id;
    const provenance = Array.isArray(context?.provenance) ? context.provenance as MethodValue[] : [];
    const exact = provenance.filter(row => row.occurrence_id === occurrence);
    const matches = exact.length ? exact : provenance.filter(row => occurrence != null && Array.isArray(row.native_action_ids) && row.native_action_ids.includes(occurrence));
    const current = matches.length === 1 ? matches[0] : undefined;
    const label = current && occurrenceSteps({ provenance } as MethodCompile, object(context?.method)).get(String(current.occurrence_id))?.label;

    return { occurrence, current, label };
}
function AttemptEvidence({ actions }: { actions: unknown }) {
    if (!Array.isArray(actions)) return null;
    return <>{actions.map((value, index) => {
        const child = object(value);
        if (!Array.isArray(child.attempts)) return null;
        return <ul key={index} aria-label={`Attempts for ${child.action_id}`}>{child.attempts.map((value, i) => {
            const attempt = object(value);
            return <li key={i}>{String(child.action_id)} · Attempt {String(attempt.attempt)}: {String(attempt.status ?? 'not reported')}
                {attempt.resolution != null && ` · Operator decision: ${String(attempt.resolution).replaceAll('_', ' ')} (not machine verification)`}</li>;
        })}</ul>;
    })}</>;
}
export function BioXpMethodProgress({ report, context }: { report: MethodValue; context?: MethodValue }) {
    const workflow = object(report.workflow);
    const { occurrence, current, label } = methodOccurrence(workflow, context);
    const provenance = Array.isArray(context?.provenance) ? context.provenance as MethodValue[] : [];
    const index = current ? provenance.indexOf(current) : -1;
    const id = workflow.native_action_id ?? (current && Array.isArray(current.native_action_ids) && current.native_action_ids.length === 1 ? current.native_action_ids[0] : undefined);
    const raw = object(context?.action_targets)[String(id)];
    const targets = (Array.isArray(raw) ? raw : []).map(p => previewActionDestination({ kind: '', params: object(p), station: null, well: null })).filter(t => t.station);
    const [focus, setFocus] = useState(0);
    const target = targets[focus] ?? targets[0];
    const rows = Array.isArray(report.occurrences) && report.occurrences.length ? report.occurrences : Array.isArray(report.occurrence_outcomes) && report.occurrence_outcomes.length ? report.occurrence_outcomes : Array.isArray(report.action_results) ? report.action_results : [];
    return <section aria-label="Occurrence outcomes"><h4>Exact occurrence progress and partial effects</h4>{context && <div className="bxw-preview">
        <p>{index >= 0 ? `Current step ${index + 1} of ${provenance.length}: ${label ?? current?.step_id}` : 'Current authored step not reported'}</p>
        {index >= 0 && <progress aria-label="Current step in original plan" max={provenance.length} value={index + 1} />}
        <details open><summary>Run deck · planned targets, not measured position</summary>
            {targets.length > 1 && <label>Show target <select value={targets[focus] ? focus : 0} onChange={e => setFocus(Number(e.target.value))}>{targets.map((t,i) => <option key={i} value={i}>{t.station} {t.well}</option>)}</select></label>}
            {!target && <p>No deck target reported for this action.</p>}
            <BioXpWorkflowDeck readOnly selection={{ station: target?.station ?? '', wells: target?.well ? [target.well] : [] }} onChange={() => {}} />
        </details></div>}{occurrence != null && <p role="status">Current occurrence: {label ?? String(occurrence)} · {String(current?.occurrence_id ?? occurrence)} · {String(workflow.phase ?? 'unknown')}{current && ` · ${JSON.stringify({ call_path: current.call_path, loop_path: current.loop_path })}`}</p>}{(Array.isArray(report.events) ? report.events : []).filter(e => object(e).event === 'recovery_decision').map((event, i) => {
        const detail = object(object(event).detail);
        return <p key={i}>Operator decision: {String(detail.decision ?? 'not reported').replaceAll('_', ' ')} · {String(detail.source_occurrence_id ?? '')} · {String(detail.native_action_id ?? '')} · attempt {String(detail.expected_attempt ?? 'not reported')}. Decision recorded, not physical verification.</p>;
    })}<table><thead><tr><th>Authored occurrence</th><th>Call / loop path</th><th>Reported status</th><th>Generated native actions / partial effects</th></tr></thead><tbody>{rows.map((value, i) => { const row = object(value); const source = object(object(row.metadata).bms_method); return <tr key={String(row.occurrence_id ?? i)}><td>{String(row.occurrence_id ?? row.source_occurrence_id ?? object(row.metadata).occurrence_id ?? row.step_id ?? 'unknown')}</td><td>{JSON.stringify({ path: row.path ?? source.path, call_path: row.call_path ?? source.call_path, loop_path: row.loop_path ?? source.loop_path })}</td><td>{String(row.status ?? 'unknown')}</td><td><AttemptEvidence actions={row.children} />{JSON.stringify(row.children ?? row.native_actions ?? row.effects ?? row.child_outcomes ?? row.native_action_ids ?? null)}</td></tr>; })}</tbody></table>{(Array.isArray(report.action_results) ? report.action_results : []).map((action, index) => <BioXpPipetteResults key={index} value={action} />)}{rows.length === 0 && <p>No occurrence outcomes reported. An absent ACK or HTTP error does not establish that nothing started.</p>}</section>;
}
