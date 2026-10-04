import { criteriaFields, defaultCriteria, type PHDesignCriteria } from '../lib/protonPottsSettings';
import { SequenceDesignerSettings } from './SequenceDesignerSettings';
import { NativeStructureSource, OrderedRowActions, type NativeAuthoringProps } from './CalibyNativeForm';

/** Model-specific repeated native values; never parse scientific objects from JSON. */
function StringList({ label, value, onChange }: { label: string; value: readonly string[]; onChange: (value: string[]) => void }) {
    return <fieldset><legend>{label}</legend>{value.map((item, index) => <div key={index}>
        <input aria-label={`${label}.${index}`} value={item} onChange={event => onChange(value.map((v, i) => i === index ? event.target.value : v))} />
        <OrderedRowActions label={`${label}.${index}`} rows={[...value]} index={index} onChange={onChange} />
    </div>)}<button type="button" onClick={() => onChange([...value, ''])}>Add {label}</button></fieldset>;
}
type Field = typeof criteriaFields[number];
function Scalar({ label, field, value, onChange }: { label: string; field: Field; value: unknown; onChange: (value: string | number | boolean) => void }) {
    if ('choices' in field) return <label>{label}<select aria-label={label} value={String(value)} onChange={event => onChange(event.target.value)}>{field.choices.map(choice => <option key={choice} value={choice}>{choice}</option>)}</select></label>;
    if (field.type === 'bool') return <label><input aria-label={label} type="checkbox" checked={value === true} onChange={event => onChange(event.target.checked)} />{label}</label>;
    return <label>{label}<input aria-label={label} type={field.type === 'str' ? 'text' : 'number'} step={field.type === 'int' ? 1 : 'any'} min={'minimum' in field ? field.minimum : undefined} value={String(value ?? '')} onChange={event => { if (field.type === 'str') onChange(event.target.value); else if (event.target.value !== '') onChange(Number(event.target.value)); }} /></label>;
}
function CriterionField({ field, criterion, index, onChange }: { field: Field; criterion: PHDesignCriteria; index: number; onChange: (key: keyof PHDesignCriteria, value: unknown) => void }) {
    const value = criterion[field.name];
    const label = `criteria.${index}.${field.name}`;
    if (field.name === 'dep_map') return <fieldset><legend>{label}</legend>{Object.entries(criterion.dep_map).map(([key, partners]) => <div key={key}>
        <label>Protonated token<input aria-label={`${label}.${key}.key`} value={key} onChange={event => onChange('dep_map', Object.fromEntries(Object.entries(criterion.dep_map).map(([k, v]) => [k === key ? event.target.value : k, v])))} /></label>
        <StringList label={`${label}.${key}`} value={partners} onChange={v => onChange('dep_map', { ...criterion.dep_map, [key]: v })} />
        <button type="button" onClick={() => onChange('dep_map', Object.fromEntries(Object.entries(criterion.dep_map).filter(([k]) => k !== key)))}>Remove contrast {key}</button>
    </div>)}<button type="button" onClick={() => onChange('dep_map', { ...criterion.dep_map, '': [] })}>Add contrast</button></fieldset>;
    if (field.name === 'explicit_centers') return <fieldset><legend>{label}</legend>{criterion.explicit_centers.map((center, i, rows) => <div key={i}>
        <label>res_id<input aria-label={`${label}.${i}.res_id`} type="number" step={1} value={center.res_id} onChange={event => { if (event.target.value !== '') onChange('explicit_centers', rows.map((row, j) => i === j ? { ...row, res_id: Number(event.target.value) } : row)); }} /></label>
        <label>protonation_type<input aria-label={`${label}.${i}.protonation_type`} value={center.protonation_type} onChange={event => onChange('explicit_centers', rows.map((row, j) => i === j ? { ...row, protonation_type: event.target.value } : row))} /></label>
        <OrderedRowActions label={`${label}.${i}`} rows={rows} index={i} onChange={rows => onChange('explicit_centers', rows)} />
    </div>)}<button type="button" onClick={() => onChange('explicit_centers', [...criterion.explicit_centers, { res_id: 0, protonation_type: 'HIS-P' }])}>Add explicit center</button></fieldset>;
    if (field.type === 'List[str]') return <><StringList label={label} value={value as string[]} onChange={v => onChange(field.name, v)} /><p>{field.name === 'center_types' ? 'Exact ordered composition, not a sweep axis. Repeated tokens are retained.' : field.name === 'placement_region' ? 'Union of native regions: interface, core, surface, all. Not a sweep.' : 'Native token list, not a sweep.'}</p></>;
    return <fieldset><legend>{field.name}</legend>{field.sweep && <label><input aria-label={`${label}.sweep`} type="checkbox" checked={Array.isArray(value)} onChange={event => onChange(field.name, event.target.checked ? [value] : (value as unknown[])[0] ?? field.default)} />Cartesian sweep</label>}
        {Array.isArray(value) ? <>{value.map((v, i) => <div key={i}><Scalar label={`${label}.${i}`} field={field} value={v} onChange={next => onChange(field.name, value.map((item, j) => j === i ? next : item))} /><OrderedRowActions label={`${label}.${i}`} rows={value} index={i} onChange={rows => onChange(field.name, rows)} /></div>)}<button type="button" onClick={() => onChange(field.name, [...value, field.default])}>Add {label} sweep value</button></> : <Scalar label={label} field={field} value={value} onChange={v => onChange(field.name, v)} />}
    </fieldset>;
}
export function ProtonPottsSettings({ parameters, values, onChange, renderScalar, selected = false }: NativeAuthoringProps & { selected?: boolean }) {
    const criteria: PHDesignCriteria[] = values.criteria ?? [];
    const engine = values.engine_options ?? {};
    return <section aria-label="ProtonPottsMPNN native settings" className="space-y-3">
        {!selected && <NativeStructureSource label="target_pdb" value={values.target_pdb} onChange={value => onChange('target_pdb', value)} />}
        <label>Explicit binder chain<input aria-label="binder_chain" value={values.binder_chain ?? ''} onChange={event => onChange('binder_chain', event.target.value)} /></label>
        <p>Supplied complex; fixed target context. Chain identity is explicit. Redesign does not automatically start prediction.</p>
        {criteria.map((criterion, index) => <fieldset key={index}><legend>Native criteria {index + 1}</legend>
            <SequenceDesignerSettings fields={[...criteriaFields]} initiallyOpenGroups={['Sequence sampling']} renderField={field => <div><CriterionField field={field as Field} criterion={criterion} index={index} onChange={(key, value) => onChange('criteria', criteria.map((row, i) => index === i ? { ...row, [key]: value } : row))} /><details><summary>Native meaning and defaults</summary><p>{field.description}</p><p>Native default: {JSON.stringify(field.default)}. {field.type === 'int' ? 'Integer, unit count.' : field.type === 'float' ? 'Native dimensionless value, full numeric precision.' : 'Native identities retained verbatim.'}</p></details></div>} />
            <OrderedRowActions label={`criteria.${index}`} rows={criteria} index={index} onChange={rows => onChange('criteria', rows)} />
        </fieldset>)}<button type="button" onClick={() => onChange('criteria', [...criteria, structuredClone(parameters.find(p => p.name === 'criteria')?.default?.[0] ?? defaultCriteria())])}>Add native criteria</button>
        <label><input aria-label="initial_sequences.enabled" type="checkbox" checked={Array.isArray(values.initial_sequences)} onChange={event => onChange('initial_sequences', event.target.checked ? [] : null)} />Supply native initial sequences</label>
        {Array.isArray(values.initial_sequences) && <StringList label="initial_sequences" value={values.initial_sequences} onChange={v => onChange('initial_sequences', v)} />}
        <fieldset><legend>Checkpoint profile engine options</legend><p>Vocabulary is fixed by the v6 profile. Head options must match the checkpoint. Scheduler owns device and CPU workers.</p>
            <label>extended_vocab<input aria-label="engine_options.extended_vocab" readOnly value={engine.extended_vocab ?? ''} /></label>
            <label>field_source<select aria-label="engine_options.field_source" value={engine.field_source ?? 'self_edge'} onChange={event => onChange('engine_options', { ...engine, field_source: event.target.value })}><option value="self_edge">self_edge</option><option value="node">node</option></select></label>
            <label>etab_source<select aria-label="engine_options.etab_source" value={engine.etab_source ?? ''} onChange={event => onChange('engine_options', { ...engine, etab_source: event.target.value || null })}><option value="">Native checkpoint auto-detect (null)</option><option value="edge">edge</option><option value="node_edge_node">node_edge_node</option></select></label>
            {(['etab_hidden', 'field_hidden'] as const).map(key => <fieldset key={key}><legend>{key}</legend><label><input aria-label={`engine_options.${key}.enabled`} type="checkbox" checked={Array.isArray(engine[key])} onChange={event => onChange('engine_options', { ...engine, [key]: event.target.checked ? [] : null })} />Explicit head widths (otherwise null)</label>
                {Array.isArray(engine[key]) && <>{engine[key].map((width: number, index: number, rows: number[]) => <div key={index}><input aria-label={`engine_options.${key}.${index}`} type="number" step={1} value={width} onChange={event => { if (event.target.value !== '') onChange('engine_options', { ...engine, [key]: rows.map((v, i) => i === index ? Number(event.target.value) : v) }); }} /><OrderedRowActions label={`engine_options.${key}.${index}`} rows={rows} index={index} onChange={rows => onChange('engine_options', { ...engine, [key]: rows })} /></div>)}<button type="button" onClick={() => onChange('engine_options', { ...engine, [key]: [...engine[key], 128] })}>Add {key} width</button></>}
            </fieldset>)}
        </fieldset>
        {parameters.filter(p => !['target_pdb', 'binder_chain', 'criteria', 'initial_sequences', 'engine_options'].includes(p.name)).map(renderScalar)}
    </section>;
}
