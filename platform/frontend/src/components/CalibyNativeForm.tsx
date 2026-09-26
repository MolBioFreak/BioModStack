import type { ReactNode } from 'react';
import { StructuralSourceFiles } from './StructuralSourceFiles';
import { SequenceDesignerSettings } from './SequenceDesignerSettings';

export interface NativeAuthoringProps {
    parameters: UntypedApiValue[];
    values: Record<string, UntypedApiValue>;
    onChange: (key: string, value: UntypedApiValue) => void;
    renderScalar: (parameter: UntypedApiValue) => ReactNode;
}
const inputClass = 'rounded border border-slate-600 bg-slate-900 p-2 text-sm';

/** Paths remain governed source references; no browser conversion or chain inference. */
export function NativeStructureSource({ label, value, onChange }: { label: string; value?: string; onChange: (value: string) => void }) {
    return <div role="group" aria-label={label} className="space-y-2">
        <label className="block">{label}<input className={`${inputClass} block w-full`} aria-label={label} value={value ?? ''} onChange={event => onChange(event.target.value)} /></label>
        <StructuralSourceFiles onSelect={source => onChange(source.path)} />
    </div>;
}

export function OrderedRowActions({ label, index, rows, onChange }: { label: string; index: number; rows: UntypedApiValue[]; onChange: (rows: UntypedApiValue[]) => void }) {
    const move = (delta: number) => { const next = [...rows]; [next[index], next[index + delta]] = [next[index + delta], next[index]]; onChange(next); };
    return <div className="flex gap-3 text-xs">
        <button type="button" aria-label={`Move ${label} up`} disabled={index === 0} onClick={() => move(-1)}>Move up</button>
        <button type="button" aria-label={`Move ${label} down`} disabled={index === rows.length - 1} onClick={() => move(1)}>Move down</button>
        <button type="button" aria-label={`Remove ${label}`} onClick={() => onChange(rows.filter((_, i) => i !== index))}>Remove</button>
    </div>;
}

const constraintNames = ['fixed_pos_seq', 'fixed_pos_scn', 'fixed_pos_override_seq', 'pos_restrict_aatype', 'symmetry_pos'] as const;
function States({ label, rows, design, onChange }: { label: string; rows: UntypedApiValue[]; design: boolean; onChange: (rows: UntypedApiValue[]) => void }) {
    return <div className="space-y-3">{rows.map((state, index) => {
        const prefix = `${label}.${index}`;
        const patch = (key: string, value: string) => onChange(rows.map((row, i) => i === index ? { ...row, [key]: value } : row));
        return <fieldset key={index} className="space-y-2 rounded border border-slate-700 p-3"><legend>{design && index === 0 ? 'Primary state' : 'State'} {index + 1}</legend>
            <label>State ID <input className={inputClass} aria-label={`${prefix}.state_id`} value={state.state_id ?? ''} onChange={event => patch('state_id', event.target.value)} /></label>
            <NativeStructureSource label={`${prefix}.path`} value={state.path} onChange={value => patch('path', value)} />
            {design && <details><summary>Native positional constraints</summary><p className="text-xs">Native strings are retained verbatim; interpretation belongs to Caliby.</p>{constraintNames.map(key => <label className="block" key={key}>{key}<input className={`${inputClass} block w-full`} aria-label={`${prefix}.${key}`} value={state[key] ?? ''} onChange={event => patch(key, event.target.value)} /></label>)}</details>}
            <OrderedRowActions label={prefix} rows={rows} index={index} onChange={onChange} />
        </fieldset>;
    })}<button type="button" onClick={() => onChange([...rows, { state_id: '', path: '' }])}>Add {label} state</button></div>;
}

export function CalibyNativeForm({ mode, parameters, values, onChange, renderScalar }: NativeAuthoringProps & { mode: string }) {
    const ensembles: UntypedApiValue[] = values.ensembles ?? [];
    return <section aria-label="Caliby native authoring" className="space-y-4">
        {mode === 'ensemble_design' ? <>
            <p>Ensembles and states are ordered. The first state is the native primary conformer.</p>
            {ensembles.map((ensemble, index) => <fieldset key={index} className="space-y-3 rounded border border-slate-700 p-3"><legend>Ensemble {index + 1}</legend>
                <label>Ensemble ID <input className={inputClass} aria-label={`ensembles.${index}.ensemble_id`} value={ensemble.ensemble_id ?? ''} onChange={event => onChange('ensembles', ensembles.map((row, i) => i === index ? { ...row, ensemble_id: event.target.value } : row))} /></label>
                <States label={`ensembles.${index}.states`} rows={ensemble.states ?? []} design onChange={states => onChange('ensembles', ensembles.map((row, i) => i === index ? { ...row, states } : row))} />
                <OrderedRowActions label={`ensembles.${index}`} rows={ensembles} index={index} onChange={rows => onChange('ensembles', rows)} />
            </fieldset>)}
            <button type="button" onClick={() => onChange('ensembles', [...ensembles, { ensemble_id: '', states: [] }])}>Add ensemble</button>
            <fieldset><legend>omit_aas</legend><p className="text-xs">Amino acids omitted from sequence design; clearing all retains an explicit empty list.</p>
                {'ACDEFGHIKLMNPQRSTVWY'.split('').map(aa => <label className="mr-3 inline-flex gap-1" key={aa}><input type="checkbox" aria-label={`omit_aas.${aa}`} checked={(values.omit_aas ?? []).includes(aa)} onChange={event => onChange('omit_aas', event.target.checked ? [...(values.omit_aas ?? []), aa] : (values.omit_aas ?? []).filter((item: string) => item !== aa))} />{aa}</label>)}
            </fieldset>
        </> : <States label="structures" rows={values.structures ?? []} design={false} onChange={rows => onChange('structures', rows)} />}
        <SequenceDesignerSettings fields={parameters.filter(p => !['ensembles', 'structures', 'omit_aas'].includes(p.name))} renderField={renderScalar} />
    </section>;
}
