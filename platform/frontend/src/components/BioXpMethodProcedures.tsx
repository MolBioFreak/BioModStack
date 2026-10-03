import type { MethodCatalog, MethodValue } from '../lib/bioxpMethods';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { methodParameterSchema } from '../lib/bioxpMethodEditorSchema';
import { MethodFields, MethodOutline, object, duplicateNode } from './BioXpMethodFields';
export function BioXpMethodProcedures({ value, onChange, catalog, rootSchema }: { value: unknown; onChange: (v: MethodValue[]) => void; catalog: MethodCatalog; rootSchema: Schema }) {
    const procedures = Array.isArray(value) ? value as MethodValue[] : [];
    const update = (i: number, p: MethodValue) => onChange(procedures.map((old, n) => n === i ? p : old));
    return <section aria-label="Method procedures">{procedures.map((p, i) => <details key={String(p.id ?? i)}><summary>{String(p.label ?? p.id ?? 'Unnamed procedure')}</summary>
        <label>Procedure ID<input aria-label={`Procedure ${i + 1} ID`} value={String(p.id ?? '')} onChange={e => update(i, { ...p, id: e.target.value })} /></label>
        <label>Procedure label<input aria-label={`Procedure ${i + 1} label`} value={String(p.label ?? '')} onChange={e => update(i, { ...p, label: e.target.value })} /></label>
        <MethodFields label={`Procedure ${i + 1} parameters`} schema={methodParameterSchema} value={p.parameters ?? []} onChange={v => update(i, { ...p, parameters: v })} />
        <MethodOutline nodes={Array.isArray(p.steps) ? p.steps as MethodValue[] : []} onChange={steps => update(i, { ...p, steps })} procedures={procedures} catalog={catalog} rootSchema={rootSchema} nodeSchema={rootSchema.properties?.steps?.items} />
        <details><summary>Procedure extension fields</summary><MethodFields label={`Procedure ${i + 1}`} value={p} onChange={v => update(i, object(v))} /></details>
        <button type="button" onClick={() => onChange([...procedures, { ...structuredClone(p), id: crypto.randomUUID(), steps: (Array.isArray(p.steps) ? p.steps as MethodValue[] : []).map(duplicateNode) }])}>Duplicate procedure</button>
        <button type="button" onClick={() => onChange(procedures.filter((_, n) => n !== i))}>Remove procedure</button>
    </details>)}<button type="button" onClick={() => onChange([...procedures, { id: crypto.randomUUID(), parameters: [], steps: [] }])}>Add procedure</button><p>Embedded procedures are frozen with the method revision. External definitions belong to pinned dependencies. Recursive calls are representation findings, never silently truncated.</p></section>;
}
