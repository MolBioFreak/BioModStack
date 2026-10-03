import { useState } from 'react';
import type { MethodCompile, MethodValue } from '../lib/bioxpMethods';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import { previewActionDestination } from '../lib/bioxpWorkflowPlan';
import { object } from './BioXpMethodFields';
import { methodStateAfter, methodVessels } from '../lib/bioxpMethodSimulation';
import { deckResources } from '../lib/bioxpWorkflowDeck';
export function nativeMethodActions(document: unknown): MethodValue[] {
    return (Array.isArray(object(document).stages) ? object(document).stages as unknown[] : []).flatMap(stage => Array.isArray(object(stage).actions) ? object(stage).actions as MethodValue[] : []);
}
export function BioXpMethodPreview({ result, initialState }: { result: MethodCompile; initialState?: unknown }) {
    const [selected, setSelected] = useState('');
    const provenance = result.provenance ?? [];
    const row = provenance.find(p => p.occurrence_id === selected) ?? provenance[0];
    const ids = new Set(Array.isArray(row?.native_action_ids) ? row.native_action_ids : []);
    const actions = nativeMethodActions(result.document).filter(a => ids.has(a.action_id));
    const [child, setChild] = useState(0);
    const action = actions[Math.min(child, Math.max(0, actions.length - 1))];
    const destination = previewActionDestination({ kind: String(action?.kind ?? ''), params: object(action?.params), station: null, well: null });
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
export function BioXpMethodProgress({ report }: { report: MethodValue }) {
    const rows = Array.isArray(report.occurrences) ? report.occurrences : Array.isArray(report.occurrence_outcomes) ? report.occurrence_outcomes : Array.isArray(report.action_results) ? report.action_results : [];
    return <section aria-label="Occurrence outcomes"><h4>Exact occurrence progress and partial effects</h4><table><thead><tr><th>Authored occurrence</th><th>Call / loop path</th><th>Reported status</th><th>Generated native actions / partial effects</th></tr></thead><tbody>{rows.map((value, i) => { const row = object(value); return <tr key={String(row.occurrence_id ?? i)}><td>{String(row.occurrence_id ?? row.source_occurrence_id ?? object(row.metadata).occurrence_id ?? row.step_id ?? 'unknown')}</td><td>{JSON.stringify({ path: row.path, call_path: row.call_path, loop_path: row.loop_path })}</td><td>{String(row.status ?? 'unknown')}</td><td>{JSON.stringify(row.children ?? row.native_actions ?? row.effects ?? row.native_action_ids ?? null)}</td></tr>; })}</tbody></table>{rows.length === 0 && <p>No occurrence outcomes reported. An absent ACK or HTTP error does not establish that nothing started.</p>}</section>;
}
