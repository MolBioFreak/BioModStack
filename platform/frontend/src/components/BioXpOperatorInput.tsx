import { useState } from 'react';
import { BioXpNumericInput } from './BioXpNumericInput';
import { BioXpSchemaInput, schemaEditableValue } from './BioXpSchemaInput';
import type { BioXpOperatorActionSpec, BioXpOperatorInputSpec } from '../lib/bioxpClient';

const controlClass = 'mt-1 w-full rounded border border-slate-700 bg-slate-900 p-2';
const isObject = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);

/** Presentation only: the catalog still owns action inputs, bounds and admission.
 * Legacy catalog flattens these source types to json. See the liquid input audit.
 */
export function liquidInputKind(action: BioXpOperatorActionSpec, input: BioXpOperatorInputSpec) {
    if (!action.informational_path?.startsWith('/liquid/') || input.value_type !== 'json') return null;
    if (input.name === 'channels') return 'channels';
    if (input.label === 'LiquidLocationRequest' && ['source', 'destination', 'dest', 'location'].includes(input.name)) return 'location';
    return null;
}

export function initialOperatorInputs(action: BioXpOperatorActionSpec | undefined): Record<string, unknown> {
    // Legacy null default also means "no default" for required inputs. Do not
    // manufacture null requests on hydration; explicit operator null is retained.
    return Object.fromEntries((action?.inputs ?? []).flatMap(input => {
        if (input.json_schema && Object.hasOwn(input.json_schema, 'default')) return [[input.name, structuredClone(input.json_schema.default)]];
        return input.default == null ? [] : [[input.name, input.default]];
    }));
}

export function normalizeOperatorInputs(action: BioXpOperatorActionSpec, values: Record<string, unknown>) {
    const result: Record<string, unknown> = {};
    for (const input of action.inputs) {
        const value = values[input.name];
        if (input.required && (value === undefined || value === null || value === '')) throw new Error(`${input.label} is required.`);
        if (value === undefined) {
            continue;
        }
        // Null and empty are explicit values, not omissions. Controller validation
        // remains authoritative, including empty channel selection semantics.
        if (value === null || value === '') { result[input.name] = value; continue; }
        if (input.value_type === 'integer' || input.value_type === 'number') {
            const parsed = Number(value);
            if (!Number.isFinite(parsed) || (input.value_type === 'integer' && !Number.isSafeInteger(parsed))) {
                throw new Error(`${input.label} must be ${input.value_type === 'integer' ? 'an integer' : 'a finite number'}.`);
            }
            result[input.name] = parsed;
        } else result[input.name] = value;
    }
    return result;
}

const valueKind = (value: unknown) => value === null ? 'null' : Array.isArray(value) ? 'list' : isObject(value) ? 'object' : typeof value;
const newValue = (kind: string): unknown => {
    const defaults: Record<string, unknown> = { string: '', number: 0, boolean: false, null: null, list: [], object: {} };
    return defaults[kind];
};

/** Typed recursive metadata/list editor; no serialization while editing. */
export function BioXpJsonInput({ label, value, onChange }: { label: string; value: unknown; onChange: (value: unknown) => void }) {
    const [key, setKey] = useState('');
    return <fieldset className="space-y-2 rounded border border-slate-700 p-2">
        <legend>{label}</legend>
        <label>Value type<select aria-label={`${label} value type`} className={controlClass} value={valueKind(value)} onChange={event => onChange(newValue(event.target.value))}>
            {['string', 'number', 'boolean', 'null', 'list', 'object'].map(kind => <option key={kind}>{kind}</option>)}
        </select></label>
        {Array.isArray(value) ? <>
            {value.map((item, index) => <div key={index}>
                <BioXpJsonInput label={`${label}[${index}]`} value={item} onChange={next => onChange(value.map((old, i) => i === index ? next : old))} />
                <button type="button" onClick={() => onChange(value.filter((_, i) => i !== index))}>Remove {label}[{index}]</button>
            </div>)}
            <button type="button" onClick={() => onChange([...value, ''])}>Add {label} item</button>
        </> : isObject(value) ? <>
            {Object.entries(value).map(([name, item]) => <div key={name}>
                <BioXpJsonInput label={`${label}.${name}`} value={item} onChange={next => onChange({ ...value, [name]: next })} />
                <button type="button" onClick={() => { const next = { ...value }; delete next[name]; onChange(next); }}>Remove {label}.{name}</button>
            </div>)}
            <label>Property name<input aria-label={`${label} property name`} className={controlClass} value={key} onChange={event => setKey(event.target.value)} /></label>
            <button type="button" disabled={Object.hasOwn(value, key)} onClick={() => { onChange({ ...value, [key]: '' }); setKey(''); }}>Add {label} property</button>
        </> : typeof value === 'boolean' ? <label>{label} value<input aria-label={`${label} value`} type="checkbox" checked={value} onChange={event => onChange(event.target.checked)} /></label>
            : value !== null && <label>{label} value<input aria-label={`${label} value`} className={controlClass} type={typeof value === 'number' ? 'number' : 'text'} step="any" value={String(value ?? '')} onChange={event => onChange(typeof value === 'number' ? (event.target.value === '' ? '' : Number(event.target.value)) : event.target.value)} /></label>}
    </fieldset>;
}

export function BioXpOperatorInput({ action, input, value, onChange }: {
    action: BioXpOperatorActionSpec; input: BioXpOperatorInputSpec; value: unknown; onChange: (value: unknown) => void;
}) {
    const kind = liquidInputKind(action, input);
    const label = kind === 'location' ? input.name : input.label;
    const status = value === undefined ? 'omitted' : value === null ? 'null' : 'value';
    const start = () => input.json_schema ? schemaEditableValue(input.json_schema) : kind === 'channels' ? [] : kind === 'location' || input.value_type === 'json' ? {} : input.value_type === 'boolean' ? false : input.value_type === 'number' || input.value_type === 'integer' ? 0 : '';
    const location = isObject(value) ? value : {};
    return <fieldset className="min-w-0 space-y-2 rounded border border-slate-700 p-3">
        <legend>{label}{input.required ? ' *' : ''}{input.unit ? ` (${input.unit})` : ''}</legend>
        <label>Send value<select aria-label={`${label} presence`} className={controlClass} value={status} onChange={event => onChange(event.target.value === 'omitted' ? undefined : event.target.value === 'null' ? null : start())}>
            <option value="omitted">Omit (service default)</option><option value="value">Explicit value</option><option value="null">Explicit null</option>
        </select></label>
        {kind === 'location' && <p className="text-xs text-amber-200">Liquid context only. These fields do not position the robot or move to a well. Z offset is context, not a movement command.</p>}
        {status === 'value' && (kind === 'channels' && Array.isArray(value) ? <div>
            <p className="text-xs">OEM channel IDs (zero-based). Omit uses the service default; an empty selection remains an explicit empty list.</p>
            {[0, 1, 2, 3].map(channel => <label key={channel} className="mr-3">Channel {channel}<input aria-label={`${label} channel ${channel}`} type="checkbox" checked={value.includes(channel)} onChange={event => onChange(event.target.checked ? [...value, channel] : value.filter(item => item !== channel))} /></label>)}
        </div> : kind === 'location' && !input.json_schema && isObject(value) ? <>
            {['location_id', 'well_id', 'plate_name', 'z_offset_steps'].map(name => <BioXpOperatorInput key={name} action={action}
                input={{ ...input, name, label: `${label}.${name}`, value_type: name === 'z_offset_steps' ? 'integer' : 'string', required: name === 'location_id', description: '', default: null }}
                value={location[name]} onChange={next => { const updated = { ...location }; if (next === undefined) delete updated[name]; else updated[name] = next; onChange(updated); }} />)}
        </> : input.json_schema ? <BioXpSchemaInput label={label} schema={input.json_schema} value={value} onChange={onChange} fallback={(name, item, update) => <BioXpJsonInput label={name} value={item} onChange={update} />} />
            : input.value_type === 'json' ? <BioXpJsonInput label={label} value={value} onChange={onChange} />
            : input.value_type === 'boolean' ? <label>{label}<input aria-label={label} type="checkbox" checked={value === true} onChange={event => onChange(event.target.checked)} /></label>
                : input.value_type === 'enum' ? <label>{label}<select aria-label={label} className={controlClass} value={String(value)} onChange={event => onChange(event.target.value)}><option value="">Select…</option>{input.enum_values.map(item => <option key={item} value={item}>{item}</option>)}</select></label>
                    : <label>{label}{input.value_type === 'string' ? <input aria-label={label} className={controlClass} type="text" value={String(value)} onChange={event => onChange(event.target.value)} />
                        : <BioXpNumericInput aria-label={label} className={controlClass} value={value} min={input.minimum ?? input.exclusive_minimum ?? undefined} max={input.maximum ?? input.exclusive_maximum ?? undefined} step={input.value_type === 'integer' ? 1 : 'any'} onValueChange={onChange} />}</label>)}
        {(input.minimum != null || input.maximum != null || input.exclusive_minimum != null || input.exclusive_maximum != null) && <p className="text-xs text-slate-400">Allowed: {input.minimum != null ? `≥ ${input.minimum}` : input.exclusive_minimum != null ? `> ${input.exclusive_minimum}` : 'unbounded'} to {input.maximum != null ? `≤ ${input.maximum}` : input.exclusive_maximum != null ? `< ${input.exclusive_maximum}` : 'unbounded'}</p>}
        {input.description && <p className="text-xs text-slate-400">{input.description}</p>}
    </fieldset>;
}
