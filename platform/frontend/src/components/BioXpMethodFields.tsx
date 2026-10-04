import { useState } from 'react';
import { isMethodNumber, methodNumber } from '../lib/bioxpMethodNumber';
import { BioXpMethodExpression } from './BioXpMethodExpression';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import type { MethodValue, MethodCatalog, MethodFinding } from '../lib/bioxpMethods';
export const object = (value: unknown): MethodValue => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as MethodValue : {};
const fieldClass = 'rounded border border-slate-600 bg-slate-900 p-2';
/** Typed extension editor preserves unknown fields and their types, never a JSON-only escape hatch. */
export function MethodValueField({ label, value, onChange }: { label: string; value: unknown; onChange: (value: unknown) => void }) {
    const [key, setKey] = useState('');
    const displayLabel = label.replace(/^(Inputs|Node|Structure|Repeat|Condition|Arguments) [^.\[]+/, '$1').replaceAll('_', ' ');
    const kind = isMethodNumber(value) ? 'number' : value === undefined ? 'omitted' : value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
    return <fieldset className="space-y-2 border border-slate-700 p-2"><legend>{displayLabel}</legend>
        <select aria-label={`${label} type`} value={kind} onChange={e => onChange(({ omitted: undefined, null: null, object: {}, array: [], string: '', number: methodNumber(''), boolean: false } as Record<string, unknown>)[e.target.value])}>
            {['omitted', 'null', 'string', 'number', 'boolean', 'object', 'array'].map(type => <option key={type}>{type}</option>)}
        </select>
        {(kind === 'string' || kind === 'number') && <input className={fieldClass} aria-label={label} value={isMethodNumber(value) ? value.expr.value : String(value)} onChange={e => onChange(kind === 'number' ? methodNumber(e.target.value) : e.target.value)} />}
        {kind === 'boolean' && <input aria-label={label} type="checkbox" checked={value === true} onChange={e => onChange(e.target.checked)} />}
        {kind === 'object' && <>{Object.entries(object(value)).map(([name, child]) => <div key={name}><MethodValueField label={`${label}.${name}`} value={child} onChange={next => { const result = { ...object(value) }; if (next === undefined) delete result[name]; else result[name] = next; onChange(result); }} /></div>)}
            <input aria-label={`${label} new field`} value={key} onChange={e => setKey(e.target.value)} /><button type="button" disabled={!key || Object.hasOwn(object(value), key)} onClick={() => { onChange({ ...object(value), [key]: '' }); setKey(''); }}>Add {label} field</button></>}
        {Array.isArray(value) && <>{value.map((child, index) => <div key={index}><MethodValueField label={`${label}[${index}]`} value={child} onChange={next => onChange(value.map((old, i) => i === index ? next : old))} /><button type="button" onClick={() => onChange(value.filter((_, i) => i !== index))}>Remove {label}[{index}]</button></div>)}<button type="button" onClick={() => onChange([...value, ''])}>Add {label} item</button></>}
    </fieldset>;
}
export function MethodFields({ label, schema, value, onChange, rootSchema }: { label: string; schema?: Schema; rootSchema?: Schema; value: unknown; onChange: (v: unknown) => void }) {
    if (isMethodNumber(value)) return <MethodValueField label={label} value={value} onChange={onChange} />;
    if (object(value).expr) return <BioXpMethodExpression label={label} value={value} onChange={onChange} literal={(v, change) => <MethodValueField label={`${label} literal`} value={v} onChange={change} />} />;
    return schema ? <BioXpSchemaInput rawDraft label={label} schema={schema} rootSchema={rootSchema ?? schema} value={value} onChange={onChange} fallback={(name, v, change) => <MethodValueField label={name} value={v} onChange={change} />} /> : <MethodValueField label={label} value={value} onChange={onChange} />;
}
function actionInputsSchema(node: MethodValue, catalog: MethodCatalog): Schema | undefined {
    const action = node.action === 'native_intent' ? object(node.inputs).operation : node.action;
    const entry = catalog.actions?.find(a => (a.action ?? a.id) === action);
    const source = entry?.input_schema ?? entry?.inputs;
    if (!source) return undefined;
    const result: Schema = { ...source, $defs: { ...object(catalog.native_definitions) as Record<string, Schema>, ...source.$defs } };
    if (node.action !== 'native_intent' && result.properties?.operation && !Object.hasOwn(object(node.inputs), 'operation')) {
        const { operation: _operation, ...properties } = result.properties;
        result.properties = properties; result.required = result.required?.filter(name => name !== 'operation');
    }
    return result;
}
export function methodBindingsSchema(method: MethodValue, catalog: MethodCatalog, reference: 'param' | 'arg' = 'param'): Schema {
    const properties: Record<string, Schema> = {};
    const definitions: Record<string, Schema> = { ...object(catalog.native_definitions) as Record<string, Schema> };
    const nodes: MethodValue[] = [];
    const collect = (rows: unknown) => { if (!Array.isArray(rows)) return; for (const raw of rows) { const row = object(raw); nodes.push(row); for (const key of ['steps', 'then', 'else']) collect(row[key]); } };
    collect(method.steps); for (const p of Array.isArray(method.procedures) ? method.procedures : []) collect(object(p).steps);
    for (const raw of Array.isArray(method.parameters) ? method.parameters : []) {
        const parameter = object(raw), id = String(parameter.id ?? '');
        const node = nodes.find(n => object(object(n.inputs).expr).op === reference && object(object(n.inputs).expr).id === id);
        const inferred = node ? actionInputsSchema(node, catalog) : undefined;
        Object.assign(definitions, inferred?.$defs);
        properties[id] = { ...(inferred ?? { type: String(parameter.type ?? 'string') }),
            ...(Array.isArray(parameter.choices) ? { enum: parameter.choices } : {}),
            description: `${String(parameter.label ?? id)}${parameter.unit ? ` (${String(parameter.unit)})` : ''}. Explicit binding; omitted remains omitted.`,
        };
    }
    return { type: 'object', properties, $defs: definitions, additionalProperties: true };
}
const id = () => crypto.randomUUID();
export function duplicateNode(node: MethodValue): MethodValue {
    const copy = structuredClone(node); copy.step_id = id();
    for (const key of ['steps', 'then', 'else']) if (Array.isArray(copy[key])) copy[key] = (copy[key] as MethodValue[]).map(duplicateNode);
    return copy;
}
export function findMethodNode(nodes: MethodValue[], id: string): MethodValue | undefined {
    for (const node of nodes) {
        if (node.step_id === id) return node;
        for (const key of ['steps', 'then', 'else']) {
            const found = findMethodNode(Array.isArray(node[key]) ? node[key] as MethodValue[] : [], id);
            if (found) return found;
        }
    }
}
export function editMethodNode(nodes: MethodValue[], id: string, change: (node: MethodValue) => MethodValue): MethodValue[] {
    return nodes.map(node => node.step_id === id ? change(node) : Object.fromEntries(Object.entries(node).map(([key, value]) => [key, ['steps', 'then', 'else'].includes(key) && Array.isArray(value) ? editMethodNode(value, id, change) : value])));
}
export function MethodOutline({ nodes, onChange, catalog, nodeSchema, rootSchema, procedures = [], findings = [], path = '/steps', collapsed, onCollapse, flat = false, selection, actionProperties }: { nodes: MethodValue[]; onChange: (nodes: MethodValue[]) => void; catalog: MethodCatalog; nodeSchema?: Schema; rootSchema?: Schema; procedures?: MethodValue[]; findings?: MethodFinding[]; path?: string; collapsed?: Record<string, boolean>; onCollapse?: (id: string, collapsed: boolean) => void; flat?: boolean; selection?: { id: string; onSelect: (id: string) => void }; actionProperties?: (node: MethodValue, onChange: (node: MethodValue) => void) => React.ReactNode }) {
    const [type, setType] = useState('action');
    const [action, setAction] = useState('');
    const [drag, setDrag] = useState<string | null>(null);
    const actions = catalog.actions ?? [];
    const update = (index: number, next: MethodValue) => onChange(nodes.map((node, i) => i === index ? next : node));
    const move = (from: number, to: number) => { if (to < 0 || to >= nodes.length || from < 0) return; const next = [...nodes]; next.splice(to, 0, next.splice(from, 1)[0]); onChange(next); };
    return <section aria-label="Method outline" className="space-y-3">
        {selection && <><h3>Sequence</h3><ol className="bioxp-method-sequence">{nodes.map((node, index) => <li key={String(node.step_id)} draggable onDragStart={() => setDrag(String(node.step_id))} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); move(nodes.findIndex(n => n.step_id === drag), index); setDrag(null); }}><button type="button" aria-label={`Select step ${index + 1}`} aria-pressed={selection.id === node.step_id} onClick={() => selection.onSelect(String(node.step_id))} onKeyDown={e => { if (e.altKey && ['ArrowUp', 'ArrowDown'].includes(e.key)) { e.preventDefault(); move(index, index + (e.key === 'ArrowUp' ? -1 : 1)); } }}>{index + 1}. {String(node.label || actions.find(a => (a.action ?? a.id) === node.action)?.label || node.action || node.type).replaceAll('_', ' ')}{node.enabled === false ? ' · disabled' : ''}</button></li>)}</ol>{!nodes.length && <p>Select a well to add a Move, or start a Transfer.</p>}</>}
        {nodes.map((node, index) => selection && selection.id !== node.step_id ? (findMethodNode([node], selection.id) ? <fieldset key={String(node.step_id)}><legend>{String(node.label || node.type)}</legend>{['steps', 'then', 'else'].filter(key => findMethodNode(Array.isArray(node[key]) ? node[key] as MethodValue[] : [], selection.id)).map(key => <MethodOutline key={key} actionProperties={actionProperties} selection={selection} nodes={node[key] as MethodValue[]} onChange={v => update(index, { ...node, [key]: v })} catalog={catalog} nodeSchema={nodeSchema} rootSchema={rootSchema} procedures={procedures} findings={findings} path={`${path}/${index}/${key}`} />)}</fieldset> : null) : <details key={String(node.step_id)} draggable={!selection} onDragStart={() => setDrag(String(node.step_id))} onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); move(nodes.findIndex(n => n.step_id === drag), index); setDrag(null); }} open={selection ? true : collapsed ? collapsed[String(node.step_id)] === false : undefined} onToggle={e => { const isCollapsed = !e.currentTarget.open; if (!selection && collapsed && isCollapsed !== (collapsed[String(node.step_id)] !== false)) onCollapse?.(String(node.step_id), isCollapsed); }}>
            <summary tabIndex={0} onKeyDown={e => { if (e.altKey && ['ArrowUp', 'ArrowDown'].includes(e.key)) { e.preventDefault(); move(index, index + (e.key === 'ArrowUp' ? -1 : 1)); } }}>{selection ? 'Properties' : String(node.label || node.action || node.type).replaceAll('_', ' ')}{node.enabled === false ? ' · disabled' : ''}</summary>
            <div className="flex gap-2"><button type="button" disabled={index === 0} onClick={() => move(index, index - 1)}>Move step up</button><button type="button" disabled={index === nodes.length - 1} onClick={() => move(index, index + 1)}>Move step down</button><button type="button" onClick={() => onChange([...nodes.slice(0, index + 1), duplicateNode(node), ...nodes.slice(index + 1)])}>Duplicate step</button><button type="button" onClick={() => onChange(nodes.filter((_, i) => i !== index))}>Remove step</button></div>
            <label>Step label<input aria-label={`Label ${node.step_id}`} value={String(node.label ?? '')} onChange={e => update(index, { ...node, label: e.target.value })} /></label>
            <label><input type="checkbox" checked={node.enabled !== false} onChange={e => update(index, { ...node, enabled: e.target.checked })} />Enabled</label>
            <label>On error<select value={String(node.on_error ?? '')} onChange={e => { const next = { ...node }; if (e.target.value) next.on_error = e.target.value; else delete next.on_error; update(index, next); }}><option value="">Inherited</option><option value="stop">Stop</option><option value="pause_for_operator">Pause for operator</option></select></label>
            {node.type === 'action' ? <><p>Action: {String(node.action).replaceAll('_', ' ')}</p>{actionProperties?.(node, next => update(index, next)) ?? <MethodFields label={`Inputs ${node.step_id}`} schema={actionInputsSchema(node, catalog)} value={node.inputs ?? {}} onChange={v => update(index, { ...node, inputs: v })} />}
                <details><summary>Advanced action fields / expressions</summary><MethodFields label={`Node ${node.step_id}`} schema={nodeSchema} rootSchema={rootSchema} value={node} onChange={v => update(index, object(v))} /></details></> : <>
                {node.type === 'repeat' && <><label>Repeat mode<select aria-label={`Repeat mode ${node.step_id}`} value={Object.hasOwn(node, 'items') ? 'items' : 'count'} onChange={e => { const next = { ...node }; delete next.count; delete next.items; next[e.target.value] = e.target.value === 'items' ? [] : ''; update(index, next); }}><option value="count">Finite count</option><option value="items">Ordered for-each items</option></select></label><MethodFields label={`Repeat ${node.step_id}`} schema={Object.hasOwn(node, 'items') ? { type: 'array', items: {} } : { type: 'integer', minimum: 0 }} value={node.items ?? node.count} onChange={v => update(index, { ...node, [Object.hasOwn(node, 'items') ? 'items' : 'count']: v })} /></>}
                {node.type === 'if' && <BioXpMethodExpression label={`Condition ${node.step_id}`} value={node.condition} onChange={v => update(index, { ...node, condition: v })} literal={(v, change) => <MethodValueField label="Boolean condition" value={v} onChange={change} />} />}
                {node.type === 'call' && <><label>Pinned procedure<select aria-label={`Procedure ${node.step_id}`} value={String(node.procedure_id ?? '')} onChange={e => update(index, { ...node, procedure_id: e.target.value })}><option value="">Select procedure…</option>{!procedures.some(p => p.id === node.procedure_id) && node.procedure_id ? <option value={String(node.procedure_id)}>{String(node.procedure_id)} (external dependency)</option> : null}{procedures.map(p => <option key={String(p.id)} value={String(p.id)}>{String(p.label ?? p.id)}</option>)}</select></label><MethodFields label={`Arguments ${node.step_id}`} schema={methodBindingsSchema(procedures.find(p => p.id === node.procedure_id) ?? {}, catalog, 'arg')} value={node.arguments ?? {}} onChange={v => update(index, { ...node, arguments: v })} /></>}
                <MethodFields label={`Structure ${node.step_id}`} schema={nodeSchema} rootSchema={rootSchema} value={Object.fromEntries(Object.entries(node).filter(([key]) => !['steps', 'then', 'else'].includes(key)))} onChange={v => update(index, { ...node, ...object(v) })} />
                {(node.type === 'if' ? ['then', 'else'] : node.type === 'call' ? [] : ['steps']).map(key => <fieldset key={key}><legend>{key}</legend><MethodOutline actionProperties={actionProperties} selection={selection} nodes={Array.isArray(node[key]) ? node[key] as MethodValue[] : []} onChange={v => update(index, { ...node, [key]: v })} catalog={catalog} nodeSchema={nodeSchema} rootSchema={rootSchema} procedures={procedures} findings={findings} path={`${path}/${index}/${key}`} collapsed={collapsed} onCollapse={onCollapse} /></fieldset>)}
            </>}
            {findings.filter(f => f.step_id === node.step_id || f.path?.startsWith(`${path}/${index}/`)).map((f, n) => <p key={n} role="status">{f.path}: {f.message}</p>)}
            <p className="text-xs">{['station', 'location_id', 'well', 'reference_well', 'height', 'height_steps', 'volume', 'volume_ul', 'channels'].filter(key => Object.hasOwn(object(node.inputs), key)).map(key => `${key === 'location_id' || key === 'station' ? 'Station' : key}: ${JSON.stringify(object(node.inputs)[key])}`).join(' · ')}</p>
        </details>)}
        <div className="flex flex-wrap gap-2"><label>Step type<select value={type} onChange={e => setType(e.target.value)}>{(flat ? ['action'] : ['action', 'group', 'repeat', 'call', 'if']).map(t => <option key={t}>{t}</option>)}</select></label>
            {type === 'action' && <label>Action palette<select aria-label="Action palette" value={action} onChange={e => setAction(e.target.value)}><option value="">Select action…</option>{actions.map(a => <option key={String(a.action ?? a.id)} value={String(a.action ?? a.id)}>{String(a.label ?? a.action ?? a.id).replaceAll('_', ' ')}</option>)}</select></label>}
            <button type="button" disabled={type === 'action' && !action} onClick={() => { const step_id = id(); onChange([...nodes, { step_id, type, ...(type === 'action' ? { action, inputs: {} } : type === 'group' ? { steps: [] } : type === 'repeat' ? { count: '', steps: [] } : type === 'if' ? { condition: {}, then: [], else: [] } : { procedure_id: '', arguments: {} }) }]); selection?.onSelect(step_id); }}>Add step</button>
        </div><small>Drag or Alt+↑/↓ to reorder.</small>
    </section>;
}
