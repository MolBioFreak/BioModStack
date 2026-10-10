import type { BioXpMeasurementForm } from '../lib/bioxpClient';
import { PipetteValue } from './BioXpPipetteResults';

/** Exact external observations; no fit, conversion, sensor inference or targets seeded. */
export function BioXpMeasurementReview({ form, values, onChange }: {
    form: BioXpMeasurementForm; values: Record<string, string[]>;
    onChange: (values: Record<string, string[]>) => void;
}) {
    return <fieldset className="space-y-3 min-w-0"><legend>External measurements</legend>
        <p>Enter readings from the external instrument. Targets, commanded values and controller reports are not external measurements.</p>
        {form.fields.map(field => <fieldset key={field.field_id} className="rounded border border-slate-700 p-3 min-w-0">
            <legend>{field.label} · {field.module}{field.zone ? ` / ${field.zone}` : ''} · {field.unit}</legend>
            <p>Location: {field.location ?? 'not specified'}</p>
            <p>Target: {field.target ?? 'not supplied'} · Commanded: {field.commanded ?? 'not supplied'} · Controller reported: {field.controller_reported ?? 'not supplied'}</p>
            {Array.from({ length: field.repetitions ?? 1 }, (_, index) => <label className="block" key={index}>
                {field.label} reading {index + 1} ({field.unit})
                <input aria-label={`${field.label} reading ${index + 1} (${field.unit})`} inputMode="decimal" className="max-w-full rounded bg-slate-950 p-2" value={values[field.field_id]?.[index] ?? ''}
                    onChange={event => { const readings = Array.from({ length: field.repetitions ?? 1 }, (_, i) => values[field.field_id]?.[i] ?? ''); readings[index] = event.target.value; onChange({ ...values, [field.field_id]: readings }); }} />
            </label>)}
            {field.context && <details><summary>Authored measurement context</summary><PipetteValue value={field.context} /></details>}
        </fieldset>)}
    </fieldset>;
}
