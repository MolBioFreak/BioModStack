import { useQuery } from '@tanstack/react-query';
import { methodsGet, methodRows, type MethodRecord } from '../lib/bioxpMethods';
import { createContext, useContext, useState, useRef, useEffect } from 'react';
import type { MethodValue } from '../lib/bioxpMethods';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { MethodFields, object } from './BioXpMethodFields';
import { liquidClassEditorSchema } from '../lib/bioxpMethodEditorSchema';

export const RepairWorkspaceContext = createContext<{ method: MethodValue; dependencies: MethodValue; entries: MethodValue[]; pin: (entry: MethodValue) => void }>({ method: {}, dependencies: {}, entries: [], pin: () => {} });
function LazyFields({ label, children }: { label: string; children: () => React.ReactNode }) {
    const [open, setOpen] = useState(false);
    return <details onToggle={e => setOpen(e.currentTarget.open)}><summary>{label}</summary>{open && children()}</details>;
}
/** Original source stays immutable; record only fields actually changed by the editor. */
export function editClassSettings(entry: MethodValue, next: unknown, touched?: string): MethodValue {
    const before = object(entry.settings), settings = object(next), authored = { ...object(entry.authored_settings) };
    for (const key of new Set([...Object.keys(before), ...Object.keys(settings)])) {
        if (key === touched || JSON.stringify(before[key]) !== JSON.stringify(settings[key])) {
            if (Object.hasOwn(settings, key)) authored[key] = settings[key]; else delete authored[key];
        }
    }
    return { ...entry, settings: next, authored_settings: authored };
}
export function RepairWorkspaceClassEditor({ value, schema = liquidClassEditorSchema, onChange }: { value: MethodValue; schema?: Schema; onChange: (value: MethodValue) => void }) {
    const { source: _source, ...editable } = value;
    const fields = schema.properties ?? {}, settingsSchema = fields.settings ?? liquidClassEditorSchema.properties!.settings;
    const keys = Object.keys(settingsSchema.properties ?? {});
    const groups: [string, (key: string) => boolean][] = [['Aspiration', k => /aspirat|leading_air|trailing_air|air_gap/.test(k)], ['Dispense', k => /dispens|blowout|carry/.test(k)], ['Segments / multi', k => /segment|conditioning|excess|reaspiration|number_|sample_count/.test(k)], ['Application controls', () => true]];
    const assigned = new Set<string>();
    return <section aria-label="Liquid class tasks">
        <MethodFields label="Class context" schema={fields.context ?? liquidClassEditorSchema.properties!.context} value={value.context} onChange={context => onChange({ ...value, context })} />
        {groups.map(([title, accepts]) => { const selected = keys.filter(k => !assigned.has(k) && accepts(k)); selected.forEach(k => assigned.add(k)); return <LazyFields key={title} label={title}>{() => <>{selected.map(key => <MethodFields key={key} label={key.replaceAll('_', ' ')} schema={settingsSchema.properties![key]} value={object(value.settings)[key]} onChange={v => { const settings = { ...object(value.settings) }; if (v === undefined) delete settings[key]; else settings[key] = v; onChange(editClassSettings(value, settings, key)); }} />)}</>}</LazyFields>; })}
        <LazyFields label="Source, exact identity & provenance">{() => <pre>{JSON.stringify({ id: value.id, revision: value.revision, source: value.source, authored_settings: value.authored_settings }, null, 2)}</pre>}</LazyFields>
        <LazyFields label="Advanced class fields">{() => <MethodFields label="Liquid class" schema={{ ...schema, properties: Object.fromEntries(Object.entries(fields).filter(([key]) => key !== 'source')) }} value={editable} onChange={v => { const next: MethodValue = { ...object(v), ...(Object.hasOwn(value, 'source') ? { source: value.source } : {}) }; onChange(JSON.stringify(value.settings) === JSON.stringify(next.settings) ? next : { ...next, authored_settings: editClassSettings(value, next.settings).authored_settings }); }} />}</LazyFields>
        <p>Requested, resolved, emitted and reported-applied are separate. Source blanks are unreported, not zero. Unsupported fields remain in Advanced and compiler evidence.</p>
    </section>;
}
export function RepairWorkspaceClassSelection({ value, onChange, schema, owner = value }: { owner?: unknown; value: unknown; onChange: (value: MethodValue) => void; schema?: Schema }) {
    const context = useContext(RepairWorkspaceContext), liquid = object(value);
    const currentValue = useRef(owner); currentValue.current = owner;
    const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
    const pinned = object(liquid.liquid_class);
    const [selected, setSelected] = useState(''), [revision, setRevision] = useState(''), [error, setError] = useState('');
    const [search, setSearch] = useState(''), [offset, setOffset] = useState(0);
    const library = useQuery({ queryKey: ['bioxp', 'methods', 'liquid-classes', search, offset], queryFn: () => methodsGet<unknown>('liquid-classes', { search, offset, limit: 25 }), retry: false });
    const revisions = useQuery({ queryKey: ['bioxp', 'methods', 'liquid-classes', selected, 'revisions'], queryFn: () => methodsGet<unknown>(`liquid-classes/${encodeURIComponent(selected)}/revisions`), enabled: !!selected, retry: false });
    return <section aria-label="Transfer liquid class">
        <label>Find saved class<input aria-label="Find saved class" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></label>
        <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous classes</button><button type="button" disabled={methodRows(library.data).length < 25} onClick={() => setOffset(offset + 25)}>More classes</button>
        {(library.isError || revisions.isError) && <p role="status">Class library read unavailable; retained selection is unchanged.</p>}
        <label>Saved liquid class<select aria-label="Saved liquid class" value={selected} onChange={e => { setSelected(e.target.value); setRevision(''); }}><option value="">Choose saved class…</option>{methodRows<MethodRecord>(library.data).map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
        <label>Exact class revision<select aria-label="Exact class revision" value={revision} onChange={e => setRevision(e.target.value)}><option value="">Choose revision…</option>{methodRows<MethodRecord>(revisions.data).map(row => <option key={row.revision} value={row.revision}>{row.revision}</option>)}</select></label>
        <button type="button" disabled={!selected || !revision} onClick={() => { const id = selected, rev = revision, original = owner; void methodsGet<MethodRecord>(`liquid-classes/${encodeURIComponent(id)}/revisions/${encodeURIComponent(rev)}`).then(record => { if (!alive.current || currentValue.current !== original) return; const entry: MethodValue = { ...record.method, id: record.id, revision: record.revision }; context.pin(entry); onChange({ ...liquid, liquid_class: entry, context: structuredClone(entry.context) }); }).catch(e => setError(String(e))); }}>Pin exact class revision</button>{error && <p role="status">{error}</p>}
        <label>Class and exact revision<select aria-label="Class and exact revision" value="" onChange={e => { if (!e.target.value) return; const entry = context.entries[Number(e.target.value)]; if (!entry) return; context.pin(entry); onChange({ ...liquid, liquid_class: structuredClone(entry), context: structuredClone(entry.context) }); }}><option value="">Choose a pinned revision…</option>{context.entries.map((entry, index) => <option key={index} value={index}>{String(entry.name ?? entry.label ?? entry.id)} · {String(entry.id)} · r{String(entry.revision)}</option>)}</select></label>
        <p>Selected: {String(pinned.name ?? pinned.label ?? pinned.id ?? liquid.liquid_class ?? 'Not authored')} · revision {String(pinned.revision ?? 'Not reported')}</p>
        <LazyFields label="Source context & explicit overrides">{() => <MethodFields label="Transfer liquid settings" schema={schema} value={value} onChange={v => onChange(object(v))} />}</LazyFields>
    </section>;
}
