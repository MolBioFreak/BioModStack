import { useState } from 'react';
import { BC2Number } from './BindCraft2NativeControls';

export function BindCraft2ListEditor({ label, value, onChange, numericOnly = false }: {
    label: string; value: unknown[]; onChange: (value: unknown[]) => void; numericOnly?: boolean;
}) {
    const [newNumber, setNewNumber] = useState<number | undefined>();
    return <div className="space-y-2" role="group" aria-label={label}>
        <div className="max-h-72 space-y-2 overflow-auto">
        {value.map((item, index) => {
            const update = (next: unknown) => onChange(value.map((old, position) => position === index ? next : old));
            return <div key={index} className="flex min-w-0 flex-wrap items-center gap-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] p-2">
                <span className="text-xs text-[var(--text-secondary)]">{index + 1}</span>
                {!numericOnly && <select className="!w-auto" aria-label={`${label}.${index}.type`} value={Array.isArray(item) ? 'list' : typeof item} onChange={event => update(event.currentTarget.value === 'number' ? 0 : event.currentTarget.value === 'list' ? [] : '')}><option value="number">Number</option><option value="string">Text / native identifier</option><option value="list">Nested list</option></select>}
                <div className="min-w-0 flex-1">{Array.isArray(item) ? <BindCraft2ListEditor label={`${label}.${index}`} value={item} onChange={update} /> :
                    typeof item === 'number' || numericOnly ? <BC2Number label={`${label}.${index}`} value={item} onChange={next => { if (next !== undefined) update(next); }} /> :
                    <input aria-label={`${label}.${index}`} type="text" value={String(item ?? '')} onChange={event => update(event.currentTarget.value)} />}</div>
                <button type="button" aria-label={`Remove ${label}.${index}`} onClick={() => onChange(value.filter((_, position) => position !== index))}>Remove</button>
            </div>;
        })}
        </div>
        {!value.length && <small>No explicit entries.</small>}
        {numericOnly ? <div className="flex items-center gap-2"><BC2Number label={`${label}.new`} value={newNumber} onChange={setNewNumber} /><button type="button" disabled={newNumber === undefined} onClick={() => { if (newNumber !== undefined) { onChange([...value, newNumber]); setNewNumber(undefined); } }}>Add value</button></div> : <button type="button" onClick={() => onChange([...value, ''])}>Add entry</button>}
    </div>;
}
