import { useState } from 'react';
import type { MethodCompile, MethodValue } from '../lib/bioxpMethods';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import { previewActionDestination } from '../lib/bioxpWorkflowPlan';
import { object } from './BioXpMethodFields';
export function nativeMethodActions(document: unknown): MethodValue[] {
    return (Array.isArray(object(document).stages) ? object(document).stages as unknown[] : []).flatMap(stage => Array.isArray(object(stage).actions) ? object(stage).actions as MethodValue[] : []);
}
export function BioXpMethodPreview({ result }: { result: MethodCompile }) {
    const [selected, setSelected] = useState('');
    const provenance = result.provenance ?? [];
    const row = provenance.find(p => p.occurrence_id === selected) ?? provenance[0];
    const ids = new Set(Array.isArray(row?.native_action_ids) ? row.native_action_ids : []);
    const actions = nativeMethodActions(result.document).filter(a => ids.has(a.action_id));
    const [child, setChild] = useState(0);
    const action = actions[Math.min(child, Math.max(0, actions.length - 1))];
    const destination = previewActionDestination({ kind: String(action?.kind ?? ''), params: object(action?.params), station: null, well: null });
    const after = (Array.isArray(object(result.simulation).occurrences) ? object(result.simulation).occurrences as MethodValue[] : []).find(s => s.occurrence_id === row?.occurrence_id);
    return <section aria-label="Compiled occurrence preview"><h4>Compiled occurrences and generated actions</h4>
        <select aria-label="Preview occurrence" value={String(row?.occurrence_id ?? '')} onChange={e => { setSelected(e.target.value); setChild(0); }}>{provenance.map(p => <option key={String(p.occurrence_id)} value={String(p.occurrence_id)}>{String(p.step_id)} · {String(p.occurrence_id)} · {JSON.stringify(p.loop_path)}</option>)}</select>
        <ol>{actions.map((a, i) => <li key={String(a.action_id)}><button type="button" onClick={() => setChild(i)}>{String(a.action_id)} · {String(a.kind)}</button></li>)}</ol>
        <p>Station: {destination.station ?? 'No emitted station / non-XY action'} · head-reference well: {destination.well ?? 'unspecified'}</p>
        <BioXpWorkflowDeck readOnly selection={{ station: destination.station ?? '', wells: destination.well ? [destination.well] : [] }} onChange={() => {}} />
        <dl>{Object.entries(object(action?.params)).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{JSON.stringify(value)}</dd></div>)}</dl>
        <p>Planned after this occurrence: {after ? JSON.stringify(after) : 'unknown / not reported'}. This is not observed pose or a physical measurement.</p>
    </section>;
}
export function BioXpMethodApplicationFields({ value }: { value: unknown }) {
    const fields: { path: string; data: MethodValue }[] = [];
    const walk = (v: unknown, path: string) => {
        if (!v || typeof v !== 'object') return;
        const row = object(v);
        if (Object.hasOwn(row, 'requested') && Object.hasOwn(row, 'resolved')) fields.push({ path, data: row });
        else for (const [key, child] of Object.entries(v)) walk(child, `${path}/${key}`);
    };
    walk(value, '');
    if (!fields.length) return null;
    const display = (v: unknown) => v === undefined ? 'not reported / unspecified' : v === null ? 'explicit null' : typeof v === 'object' ? JSON.stringify(v) : String(v);
    return <section aria-label="Field application records"><h4>Per-field application records</h4><table><thead><tr><th>Field / context</th><th>Requested (including absence)</th><th>Resolved / source revision</th><th>Emitted</th><th>Reported applied</th></tr></thead><tbody>{fields.map(({ path, data }) => <tr key={path}><td>{path}</td><td>{display(data.requested)}</td><td>{display(data.resolved)} · {display(data.source ?? data.ref)}</td><td>{display(data.emitted)}</td><td>{display(data.applied ?? data.reported_applied)}</td></tr>)}</tbody></table><p>Unknown, unsupported and not-emitted are not applied. Applicability and adapter/options evidence are not inferred from a number.</p></section>;
}
export function BioXpMethodProgress({ report }: { report: MethodValue }) {
    const rows = Array.isArray(report.occurrences) ? report.occurrences : Array.isArray(report.occurrence_outcomes) ? report.occurrence_outcomes : Array.isArray(report.action_results) ? report.action_results : [];
    return <section aria-label="Occurrence outcomes"><h4>Exact occurrence progress and partial effects</h4><table><thead><tr><th>Authored occurrence</th><th>Call / loop path</th><th>Reported status</th><th>Generated native actions / partial effects</th></tr></thead><tbody>{rows.map((value, i) => { const row = object(value); return <tr key={String(row.occurrence_id ?? i)}><td>{String(row.occurrence_id ?? row.source_occurrence_id ?? object(row.metadata).occurrence_id ?? row.step_id ?? 'unknown')}</td><td>{JSON.stringify({ path: row.path, call_path: row.call_path, loop_path: row.loop_path })}</td><td>{String(row.status ?? 'unknown')}</td><td>{JSON.stringify(row.children ?? row.native_actions ?? row.effects ?? row.native_action_ids ?? null)}</td></tr>; })}</tbody></table>{rows.length === 0 && <p>No occurrence outcomes reported. An absent ACK or HTTP error does not establish that nothing started.</p>}</section>;
}
