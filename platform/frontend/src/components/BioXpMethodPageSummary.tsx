import type { MethodCatalog, MethodCompile, MethodValue } from '../lib/bioxpMethods';
import { pageProvenance, type MethodPage } from '../lib/bioxpMethodPages';
import { methodCanvasEntries } from '../lib/bioxpMethodCanvas';
import { methodInputBinding } from '../lib/bioxpMethodInputBinding';
import { deckStations } from '../lib/bioxpWorkflowDeck';
import { isMethodNumber } from '../lib/bioxpMethodNumber';
import { custodyMetadata } from './BioXpMethodCustodyEditor';

const object = (v: unknown): MethodValue => v && typeof v === 'object' && !Array.isArray(v) ? v as MethodValue : {};
const raw = (v: unknown): string => isMethodNumber(v) ? v.expr.value : v == null ? 'not set' : typeof v === 'object' ? JSON.stringify(v) : String(v);
const station = (v: unknown): string => deckStations.find(s => s.id === v)?.label ?? String(v ?? 'Unspecified station');
const duration = (v: unknown): string => {
    const n = Number(raw(v));
    return Number.isFinite(n) && n > 0 && n % 60 === 0 ? `${n / 60} min` : `${raw(v)} s`;
};

export function BioXpMethodPageSummary({ page, method, bindings, actions, preview, catalog, onSelect }: {
    page: MethodPage; method: MethodValue; bindings: MethodValue; actions: MethodValue[];
    preview?: MethodCompile; catalog: MethodCatalog; onSelect: (path: string) => void;
}) {
    const entries = methodCanvasEntries(Array.isArray(method.steps) ? method.steps as MethodValue[] : [])
        .filter(e => page.paths.some(p => e.path === p || e.path.startsWith(p + '/')));
    const transfers = new Map<string, { count: number | null; text: string }>();
    const provenance = pageProvenance(page, preview);
    const occurrences = (path: string) => preview?.document ? provenance.filter(p => p.path === `/method${path}` && Array.isArray(p.native_action_ids) && p.native_action_ids.length).length : 1;
    for (const { node, path } of entries.filter(e => e.node.action === 'transfer' && e.node.enabled !== false)) {
        if (!occurrences(path)) continue;
        const i = object(methodInputBinding(method, node, bindings).inputs), s = object(i.source), d = object(i.destination);
        const text = `${station(s.station)} → ${station(d.station)} · ${raw(i.volume_ul)} µL per channel`;
        const count = Array.isArray(i.channels) && Array.isArray(s.wells) && Array.isArray(d.wells) && s.wells.length === d.wells.length
            ? i.channels.length * s.wells.length * occurrences(path) : null;
        const prior = transfers.get(text);
        transfers.set(text, { text, count: prior ? prior.count === null || count === null ? null : prior.count + count : count });
    }
    const custody = custodyMetadata(catalog);
    const objectName = (token: unknown) => String(custody.objects.find(o => o.token === token)?.label ?? token);
    const destinationName = (token: unknown) => String(custody.destinations.find(d => d.token === token)?.label ?? station(token));
    const actionCount = entries.filter(e => !Array.isArray(e.node.steps)).length;
    const settings = [...new Set(entries.filter(e => e.node.action === 'mix' && e.node.enabled !== false && occurrences(e.path)).map(e => { const i = object(methodInputBinding(method, e.node, bindings).inputs); return `Mix ${raw(i.volume_ul)} µL × ${raw(i.cycles)} cycles`; }))];
    for (const a of actions) {
        const p = object(a.params);
        if (a.kind === 'plate_move' || a.kind === 'move_cover') {
            settings.push(`${objectName(p.plate_id ?? p.cover_id)} → ${destinationName(p.target_location)}${p.move_mode ? ` · ${raw(p.move_mode)}` : ''}`);
        } else if (a.kind === 'thermal_door') settings.push(`Cycler door ${p.door_command === 'DO' ? 'open' : p.door_command === 'DC' ? 'closed' : raw(p.door_command)}`);
        else if (a.kind === 'thermal_profile') {
            const segments = Array.isArray(p.segments) ? p.segments : [];
            settings.push(`Cycler: ${segments.map(v => { const s = object(v); return `${raw(s.target_temp_c)} °C for ${duration(s.duration_s)} (${raw(s.bank)}, ${s.start === 'attainment' ? 'after reaching temperature' : 'from dispatch'})`; }).join(' → ')} · ${raw(p.repeat)} pass(es)`);
        } else if (['thermal_hold', 'incubate', 'thermal_setpoint'].includes(String(a.kind))) settings.push(`${raw(p.bank)}: ${raw(p.target_temp_c)} °C${p.duration_s !== undefined ? ` for ${duration(p.duration_s)}` : ''}`);
        else if (a.kind === 'chiller_setpoint') settings.push(`${String(p.bank).toUpperCase()} chiller: ${raw(p.target_temp_c)} °C`);
        else if (a.kind === 'wait') settings.push(`Wait ${duration(p.seconds)}`);
        else if (a.kind === 'inspect') settings.push('Detect chiller covers and move them to their storage positions');
        else if (a.kind === 'pause_review') settings.push(`Operator step: ${raw(p.message ?? a.pause_message)}`);
        else if (a.kind === 'park') settings.push('Park gantry');
    }
    return <section className="bioxp-page-summary" aria-label="Stage summary">
        {page.id === 'starting-deck' ? <p>Planned starting contents and labware. Next shows the first operation; browsing does not move the robot.</p> : <>
            <ul>{[...transfers.values()].map(t => <li key={t.text}>{t.count === null ? '' : `${t.count} transfers · `}{t.text}</li>)}
                {settings.map((text, i) => <li key={i}>{text}</li>)}</ul>
            {!preview && <p>Showing authored steps; compiled settings and resulting placement are not yet available.</p>}
            <details><summary>{actionCount} authored action{actionCount === 1 ? '' : 's'}{preview ? ` · ${actions.length} robot action${actions.length === 1 ? '' : 's'}` : ''} in this stage</summary>
                <ol>{entries.filter(e => !Array.isArray(e.node.steps)).map(e => <li key={e.path}><button type="button" onClick={() => onSelect(e.path)}>{String(e.node.label || e.node.action || e.node.type || 'Step')}</button></li>)}</ol>
            </details>
        </>}
    </section>;
}
