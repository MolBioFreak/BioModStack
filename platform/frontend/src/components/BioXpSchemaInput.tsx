import { useState } from 'react';
import { BioXpNumericInput } from './BioXpNumericInput';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';

const classes = 'mt-1 w-full rounded border border-slate-700 bg-slate-900 p-2';
const objectValue = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const typeOf = (value: unknown) => value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value;
// Resolve only schema references, lazily. Keep literal data and recursive
// definitions intact, and carry the same root through every nested control.
function resolve(schema: Schema, root: Schema): Schema {
    let result = schema;
    const seen = new Set<string>();
    while (result.$ref?.startsWith('#/') && !seen.has(result.$ref)) {
        seen.add(result.$ref);
        let target: unknown = root;
        for (const part of result.$ref.slice(2).split('/')) target = objectValue(target) ? target[part.replace(/~1/g, '/').replace(/~0/g, '~')] : undefined;
        if (!objectValue(target)) break;
        const { $ref: _reference, ...siblings } = result;
        result = { ...target, ...siblings };
    }
    return result;
}
const variants = (schema: Schema, root = schema): Schema[] => {
    const resolved = resolve(schema, root);
    return (resolved.anyOf ?? resolved.oneOf ?? (Array.isArray(resolved.type) ? resolved.type.map(type => ({ ...resolved, type })) : [resolved])).map(choice => resolve(choice, root));
};
export function schemaInitialValue(schema: Schema, root = schema): unknown {
    schema = resolve(schema, root);
    if (Object.hasOwn(schema, 'default')) return structuredClone(schema.default);
    if (Object.hasOwn(schema, 'const')) return structuredClone(schema.const);
    if (schema.enum?.length) return structuredClone(schema.enum[0]);
    if (schema.anyOf || schema.oneOf || Array.isArray(schema.type)) return schemaInitialValue(variants(schema, root)[0], root);
    if (schema.type === 'object' || schema.properties) return Object.fromEntries(Object.entries(schema.properties ?? {})
        .filter(([, child]) => Object.hasOwn(resolve(child, root), 'const'))
        .map(([name, child]) => [name, schemaInitialValue(resolve(child, root), root)]));
    if (schema.type === 'array' || schema.items) return [];
    if (schema.type === 'null') return null;
    if (schema.type === 'boolean') return false;
    if (schema.type === 'number' || schema.type === 'integer') return 0;
    return '';
}
export function schemaEditableValue(schema: Schema): unknown {
    const initial = schemaInitialValue(schema);
    if (initial !== null) return initial;
    const candidate = variants(schema).find(choice => choice.type !== 'null') ?? schema;
    const withoutDefault = { ...candidate };
    delete withoutDefault.default;
    return schemaInitialValue(withoutDefault, schema);
}
function matches(schema: Schema, value: unknown, root: Schema): boolean {
    if (schema.enum) return schema.enum.some(item => JSON.stringify(item) === JSON.stringify(value));
    if (Object.hasOwn(schema, 'const')) return JSON.stringify(schema.const) === JSON.stringify(value);
    if (objectValue(value) && schema.properties && Object.entries(schema.properties).some(([name, child]) => {
        const field = resolve(child, root);
        return Object.hasOwn(field, 'const') && JSON.stringify(field.const) !== JSON.stringify(value[name]);
    })) return false;
    return schema.type === typeOf(value) || (schema.type === 'integer' && typeof value === 'number') || (!schema.type && !!schema.properties && objectValue(value));
}

/** Schema-driven presentation, not an admission/validation authority. */
export function BioXpSchemaInput({ label, schema, value, onChange, fallback, rootSchema = schema }: {
    label: string; schema: Schema; rootSchema?: Schema; value: unknown; onChange: (value: unknown) => void;
    fallback: (label: string, value: unknown, onChange: (value: unknown) => void) => React.ReactNode;
}) {
    const [newKey, setNewKey] = useState('');
    const resolved = resolve(schema, rootSchema);
    const choices = variants(resolved, rootSchema);
    const discriminator = resolved.discriminator;
    const mapped = discriminator && objectValue(value) ? discriminator.mapping?.[String(value[discriminator.propertyName])] : undefined;
    const rawChoices = resolved.oneOf ?? resolved.anyOf;
    const mappedIndex = mapped ? rawChoices?.findIndex(choice => choice.$ref === mapped) ?? -1 : -1;
    const selected = Math.max(0, mappedIndex >= 0 ? mappedIndex : choices.findIndex(choice => matches(choice, value, rootSchema)));
    const variantValue = (index: number) => {
        const initial = schemaInitialValue(choices[index], rootSchema);
        const tag = discriminator && Object.entries(discriminator.mapping ?? {}).find(([, ref]) => ref === rawChoices?.[index].$ref)?.[0];
        return discriminator && tag !== undefined && objectValue(initial)
            ? { ...initial, [discriminator.propertyName]: tag } : initial;
    };
    const active = choices[selected];
    const kind = active.type ?? (active.properties ? 'object' : active.items ? 'array' : undefined);
    const enums = active.enum ?? (Object.hasOwn(active, 'const') ? [active.const] : undefined);
    const fields = active.properties ?? {};
    const update = (name: string, next: unknown) => {
        const result = { ...(objectValue(value) ? value : {}) };
        if (next === undefined) delete result[name]; else result[name] = next;
        onChange(result);
    };
    return <fieldset className="min-w-0 space-y-2 rounded border border-slate-700 p-2">
        <legend>{label}</legend>
        {schema.description && <p className="text-xs text-slate-400">{schema.description}</p>}
        {choices.length > 1 && <label>Variant<select aria-label={`${label} variant`} className={classes} value={selected} onChange={event => onChange(variantValue(Number(event.target.value)))}>
            {choices.map((choice, index) => <option key={index} value={index}>{choice.title ?? String(choice.type ?? `Variant ${index + 1}`)}</option>)}
        </select></label>}
        {enums ? <label>{label}<select aria-label={label} className={classes} value={enums.findIndex(item => JSON.stringify(item) === JSON.stringify(value))} onChange={event => onChange(structuredClone(enums[Number(event.target.value)]))}>
            <option value={-1}>Select…</option>{enums.map((item, index) => <option key={index} value={index}>{typeof item === 'string' ? item : JSON.stringify(item)}</option>)}
        </select></label> : kind === 'object' && objectValue(value) ? <>
            {[...new Set([...Object.keys(fields), ...Object.keys(value)])].map(name => {
                const child = resolve(fields[name] ?? (objectValue(active.additionalProperties) ? active.additionalProperties as Schema : {}), rootSchema);
                return <div key={name}>
                    <label>{label}.{name}{active.required?.includes(name) ? ' *' : ''}<select aria-label={`${label}.${name} presence`} className={classes} value={value[name] === undefined ? 'omitted' : 'value'} onChange={event => update(name, event.target.value === 'omitted' ? undefined : schemaInitialValue(child, rootSchema))}>
                        <option value="omitted">Omit (service default{Object.hasOwn(child, 'default') ? `: ${JSON.stringify(child.default)}` : ''})</option><option value="value">Explicit value</option>
                    </select></label>
                    {value[name] !== undefined && <BioXpSchemaInput label={`${label}.${name}`} schema={child} rootSchema={rootSchema} value={value[name]} onChange={next => update(name, next)} fallback={fallback} />}
                </div>;
            })}
            {active.additionalProperties !== false && <>
                <label>Property name<input aria-label={`${label} property name`} className={classes} value={newKey} onChange={event => setNewKey(event.target.value)} /></label>
                <button type="button" disabled={Object.hasOwn(value, newKey) || Object.hasOwn(fields, newKey)} onClick={() => { update(newKey, schemaInitialValue(objectValue(active.additionalProperties) ? active.additionalProperties as Schema : {}, rootSchema)); setNewKey(''); }}>Add {label} property</button>
            </>}
        </> : kind === 'array' && Array.isArray(value) ? <>
            <p className="text-xs text-slate-400">Items: {active.minItems ?? 'unbounded'} to {active.maxItems ?? 'unbounded'}</p>
            {value.map((item, index) => <div key={index}><BioXpSchemaInput label={`${label}[${index}]`} schema={active.items ?? {}} rootSchema={rootSchema} value={item} onChange={next => onChange(value.map((old, i) => i === index ? next : old))} fallback={fallback} />
                <button type="button" onClick={() => onChange(value.filter((_, i) => i !== index))}>Remove {label}[{index}]</button></div>)}
            <button type="button" onClick={() => onChange([...value, schemaInitialValue(active.items ?? {}, rootSchema)])}>Add {label} item</button>
        </> : kind === 'boolean' ? <label>{label}<input aria-label={label} type="checkbox" checked={value === true} onChange={event => onChange(event.target.checked)} /></label>
            : kind === 'null' ? <p>Explicit null</p>
                : kind === 'string' || kind === 'number' || kind === 'integer' ? <>
                    <label>{label}{kind === 'string' ? <input aria-label={label} className={classes} type="text" minLength={active.minLength} maxLength={active.maxLength} value={String(value ?? '')} onChange={event => onChange(event.target.value)} />
                        : <BioXpNumericInput aria-label={label} className={classes} step={kind === 'integer' ? 1 : 'any'} min={active.minimum ?? active.exclusiveMinimum} max={active.maximum ?? active.exclusiveMaximum} value={value} onValueChange={onChange} />}</label>
                    {(active.minimum != null || active.maximum != null || active.exclusiveMinimum != null || active.exclusiveMaximum != null) && <p className="text-xs text-slate-400">Allowed: {active.minimum != null ? `≥ ${active.minimum}` : active.exclusiveMinimum != null ? `> ${active.exclusiveMinimum}` : 'unbounded'} to {active.maximum != null ? `≤ ${active.maximum}` : active.exclusiveMaximum != null ? `< ${active.exclusiveMaximum}` : 'unbounded'}</p>}
                </> : fallback(label, value, onChange)}
    </fieldset>;
}
